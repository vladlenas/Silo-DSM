#!/usr/bin/env python3
"""Package settings of TorrServer Silo DSM and the config.yaml made from them.

Silo reads all of its settings from one YAML file (``silo -c config.yaml``)
and only writes that file itself when it does not exist yet. The package keeps
its own settings in ``settings.json`` (written by the install wizard and the
Helper) and generates ``config.yaml`` from them:

* in postinst (from the install wizard answers),
* right before every start of Silo (start-stop-status, restart-silo),
* when the Helper saves settings.

So config.yaml is never edited by hand: it is overwritten from settings.json.

Command line (used by the package scripts):

    siloconf.py init    create settings.json from the wizard_* variables
                        (only when it does not exist yet), then write config.yaml
    siloconf.py write   write config.yaml from settings.json
    siloconf.py reset   drop settings.json (back to defaults), then write

Only the Python standard library is used: DSM has no PyYAML. The YAML is
written by hand; every string is emitted as a JSON string, which is a valid
YAML double-quoted scalar.
"""

import json
import os
import re
import sys

PACKAGE_NAME = "TorrServerSilo"

# SILO_DSM_VAR exists for the test suite; DSM never sets it.
PACKAGE_VAR = (
    os.environ.get("SILO_DSM_VAR")
    or os.environ.get("SYNOPKG_PKGVAR")
    or "/var/packages/{}/var".format(PACKAGE_NAME)
)

SETTINGS_FILE = "settings.json"
CONFIG_FILE = "config.yaml"
DEFAULT_DATA_SUBDIR = "data"
PLUGINS_SUBDIR = "plugins"
DB_NAME = "torrserver.db"

# 8090 is TorrServer DSM's port: Silo gets its own so both can run.
DEFAULT_PORT = 8095
# The Helper listens on 127.0.0.1:42778 (TorrServer DSM's Helper uses 42777).
PROBE_TIMEOUT_SEC = 90
HELPER_PORT = 42778
RESERVED_PORTS = {HELPER_PORT: "Web port is reserved for the Helper"}

LOG_LEVELS = ("debug", "info", "warn", "error")
HWACCEL_METHODS = ("none", "vaapi", "auto")

DEFAULTS = {
    "port": DEFAULT_PORT,
    "data_dir": "",
    "log_level": "info",
    "owner_password": "",
    "ffmpeg_enabled": True,
    "ffmpeg_hwaccel": "none",
    "ffmpeg_hw_device": "",
    "ffmpeg_threads": 0,
    "ffmpeg_max_sessions": 3,
    "ffmpeg_cache_dir": "",
}

# name -> (minimum, maximum) for the integer settings.
INT_RANGES = {
    "port": (1024, 65535),
    "ffmpeg_threads": (0, 64),
    "ffmpeg_max_sessions": (0, 32),
}

PASSWORD_MAX_LENGTH = 128
VOLUME_PATH = re.compile(r"^/volume[0-9]+(?:/[^/\x00-\x1f\x7f]+)+$")
DEVICE_PATH = re.compile(r"^/dev/dri/[A-Za-z0-9_.-]+$")


class SettingsError(ValueError):
    """A setting has an invalid value. The message is shown to the user."""


def settings_path(var=None):
    return os.path.join(var or PACKAGE_VAR, SETTINGS_FILE)


def config_path(var=None):
    return os.path.join(var or PACKAGE_VAR, CONFIG_FILE)


def write_file(path, value, mode=0o600):
    """Atomically replace *path*; *mode* is applied at creation."""
    tmp = path + ".tmp"
    try:
        os.unlink(tmp)
    except OSError:
        pass

    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(value)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _as_int(name, value):
    if isinstance(value, bool):
        raise SettingsError("Invalid value: {}".format(name))
    if isinstance(value, int):
        number = value
    else:
        text = str(value).strip()
        if not re.match(r"^[0-9]{1,9}$", text):
            raise SettingsError("Invalid value: {}".format(name))
        number = int(text)

    low, high = INT_RANGES[name]
    if not low <= number <= high:
        raise SettingsError("Value out of range: {} ({}-{})".format(name, low, high))
    return number


def _as_bool(value):
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _as_dir(name, value):
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) > 1:
        text = text.rstrip("/")
    if not VOLUME_PATH.match(text) or "/../" in text + "/" or "/./" in text + "/":
        raise SettingsError("Folder must be inside a volume, for example /volume1/silo: {}".format(name))
    return text


def normalize(raw):
    """Return a complete, validated copy of the settings.

    Unknown keys are dropped, missing keys get their defaults. Raises
    SettingsError with a message for the user when a value is invalid.
    """
    raw = raw if isinstance(raw, dict) else {}
    result = dict(DEFAULTS)

    for name in INT_RANGES:
        if name in raw:
            result[name] = _as_int(name, raw[name])

    if "ffmpeg_enabled" in raw:
        result["ffmpeg_enabled"] = _as_bool(raw["ffmpeg_enabled"])

    if "log_level" in raw:
        level = str(raw["log_level"]).strip().lower()
        if level not in LOG_LEVELS:
            raise SettingsError("Invalid value: log_level")
        result["log_level"] = level

    if "ffmpeg_hwaccel" in raw:
        method = str(raw["ffmpeg_hwaccel"]).strip().lower()
        if method not in HWACCEL_METHODS:
            raise SettingsError("Invalid value: ffmpeg_hwaccel")
        result["ffmpeg_hwaccel"] = method

    if "ffmpeg_hw_device" in raw:
        device = str(raw["ffmpeg_hw_device"] or "").strip()
        if device and not DEVICE_PATH.match(device):
            raise SettingsError("Device must be a node in /dev/dri, for example /dev/dri/renderD128")
        result["ffmpeg_hw_device"] = device

    if "owner_password" in raw:
        password = str(raw["owner_password"] or "")
        if len(password) > PASSWORD_MAX_LENGTH or re.search(r"[\x00-\x1f\x7f]", password):
            raise SettingsError("Password must not contain control characters and must be at most 128 characters long")
        result["owner_password"] = password

    for name in ("data_dir", "ffmpeg_cache_dir"):
        if name in raw:
            result[name] = _as_dir(name, raw[name])

    if result["port"] in RESERVED_PORTS:
        raise SettingsError(RESERVED_PORTS[result["port"]])

    return result


# ---------------------------------------------------------------------------
# settings.json
# ---------------------------------------------------------------------------

def load(var=None):
    """The saved settings, completed with defaults.

    A missing or broken file gives the defaults; a value that became invalid
    (edited by hand) falls back to its default instead of failing the start.
    """
    try:
        with open(settings_path(var), "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        raw = {}

    if not isinstance(raw, dict):
        raw = {}

    try:
        return normalize(raw)
    except SettingsError:
        good = {}
        for key, value in raw.items():
            try:
                normalize({key: value})
                good[key] = value
            except SettingsError:
                continue
        return normalize(good)


def save(settings, var=None):
    clean = normalize(settings)
    write_file(settings_path(var), json.dumps(clean, indent=2, sort_keys=True) + "\n")
    return clean


def exists(var=None):
    return os.path.isfile(settings_path(var))


# ---------------------------------------------------------------------------
# config.yaml
# ---------------------------------------------------------------------------

def resolved_data_dir(settings, var=None):
    return settings["data_dir"] or os.path.join(var or PACKAGE_VAR, DEFAULT_DATA_SUBDIR)


def _yaml_value(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return json.dumps(str(value), ensure_ascii=False)


def render_yaml(settings, var=None):
    """config.yaml for Silo (only the settings the package manages; Silo
    fills in its own defaults for everything else)."""
    var = var or PACKAGE_VAR
    sections = [
        ("server", [
            ("host", "0.0.0.0"),
            ("port", settings["port"]),
        ]),
        ("storage", [
            ("data_dir", resolved_data_dir(settings, var)),
            ("db_name", DB_NAME),
        ]),
        ("plugins", [
            ("dir", os.path.join(var, PLUGINS_SUBDIR)),
        ]),
        # No "torrent" section: Silo.1 does not read it. The torrent engine
        # (RAM/disk cache, its size, peers, speed limits) is configured in
        # Silo's own web interface and stored in its database.
        # No log file: Silo writes every line to stdout as well, and the
        # package sends stdout to Silo.log. A file here would double each line.
        ("logging", [
            ("level", settings["log_level"]),
            ("file", ""),
        ]),
        ("auth", [
            ("owner_password", settings["owner_password"]),
        ]),
        ("ffmpeg", [
            ("enabled", settings["ffmpeg_enabled"]),
            ("cache_dir", settings["ffmpeg_cache_dir"]),
            ("max_sessions", settings["ffmpeg_max_sessions"]),
            ("threads", settings["ffmpeg_threads"]),
            ("hwaccel", settings["ffmpeg_hwaccel"]),
            ("hw_device", settings["ffmpeg_hw_device"]),
            # Silo reads a file with ffprobe before transcoding it. A torrent
            # that has just woken up may need longer than Silo's 30 s to
            # deliver the first megabytes ("ffprobe failed: signal: killed").
            ("probe_timeout_sec", PROBE_TIMEOUT_SEC),
        ]),
    ]

    lines = [
        "# Generated by TorrServer Silo DSM from settings.json.",
        "# Do not edit: change the settings in the package window (Helper);",
        "# this file is written again before every start.",
    ]
    for section, items in sections:
        lines.append("{}:".format(section))
        for key, value in items:
            lines.append("  {}: {}".format(key, _yaml_value(value)))
    return "\n".join(lines) + "\n"


def prepare_dirs(settings, var=None):
    """Create the data and plugin folders. Returns a list of problems (the
    folders that cannot be created or written by the package user)."""
    var = var or PACKAGE_VAR
    problems = []
    dirs = [resolved_data_dir(settings, var), os.path.join(var, PLUGINS_SUBDIR)]
    if settings["ffmpeg_cache_dir"]:
        dirs.append(settings["ffmpeg_cache_dir"])

    for path in dirs:
        try:
            os.makedirs(path, 0o755, exist_ok=True)
        except OSError:
            problems.append(path)
            continue
        if not os.access(path, os.W_OK | os.X_OK):
            problems.append(path)
    return problems


def write_config(var=None, settings=None):
    """Write config.yaml from *settings* (default: the saved settings).

    Returns the list of folders that could not be prepared; config.yaml is
    written anyway, so Silo reports the problem in its own log too.
    """
    var = var or PACKAGE_VAR
    settings = load(var) if settings is None else normalize(settings)
    problems = prepare_dirs(settings, var)
    write_file(config_path(var), render_yaml(settings, var))
    return problems


# ---------------------------------------------------------------------------
# Install wizard
# ---------------------------------------------------------------------------

def from_wizard(env):
    """Settings from the install wizard answers (WIZARD_UIFILES/install_uifile).

    DSM exports every wizard key as an environment variable; a singleselect
    group gives one variable per choice with "true" for the chosen one.
    """
    raw = {}

    port = (env.get("wizard_port") or "").strip()
    if port:
        raw["port"] = port

    password = env.get("wizard_owner_password")
    if password:
        raw["owner_password"] = password

    if "wizard_ffmpeg_enabled" in env:
        raw["ffmpeg_enabled"] = env.get("wizard_ffmpeg_enabled") == "true"

    for method in HWACCEL_METHODS:
        if env.get("wizard_hwaccel_" + method) == "true":
            raw["ffmpeg_hwaccel"] = method

    return normalize(raw)


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------

def _report(problems):
    for path in problems:
        print("Warning: the package user cannot write to {}".format(path))


def main(argv):
    command = argv[1] if len(argv) > 1 else "write"

    try:
        if command == "init":
            if not exists():
                save(from_wizard(os.environ))
            _report(write_config())
        elif command == "write":
            _report(write_config())
        elif command == "reset":
            try:
                os.unlink(settings_path())
            except OSError:
                pass
            _report(write_config())
        else:
            print("Usage: siloconf.py init|write|reset", file=sys.stderr)
            return 2
    except SettingsError as e:
        print("Error: {}".format(e))
        return 1
    except OSError as e:
        print("Error: cannot write {}: {}".format(config_path(), e))
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
