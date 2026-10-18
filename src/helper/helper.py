#!/usr/bin/env python3

import base64
import grp
import hashlib
import html
import json
import os
import platform
import pwd
import re
import shutil
import subprocess
import sys
import time
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse, quote

HELPER_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HELPER_DIR)

import siloconf  # noqa: E402  (next to this file)


# The helper is only reachable through DSM nginx (see nginx/Silo.conf), which
# proxies to 127.0.0.1. It must never listen on a public interface: it has no
# authentication of its own.
HOST = "127.0.0.1"
HELPER_PORT = siloconf.HELPER_PORT

MAX_POST_BYTES = 64 * 1024

PACKAGE_NAME = siloconf.PACKAGE_NAME
PACKAGE_VAR = siloconf.PACKAGE_VAR
# DSM hands its own path to the package scripts (SYNOPKG_PKGDEST); the Helper
# inherits it from start-stop-status. SILO_DSM_TARGET exists for the tests.
PACKAGE_TARGET = (
    os.environ.get("SILO_DSM_TARGET")
    or os.environ.get("SYNOPKG_PKGDEST")
    or "/var/packages/{}/target".format(PACKAGE_NAME)
)
SILO_BIN = os.path.join(PACKAGE_TARGET, "bin", "silo")
# Written by build-package.sh: "Silo.1 fc5795e".
VERSION_FILE = os.path.join(HELPER_DIR, "VERSION")

# The one log of the package: Silo (stdout/stderr), the start/stop scripts and
# the Helper append to it. The Helper rotates it by size.
SILO_LOG = os.path.join(PACKAGE_VAR, "Silo.log")

LOG_FILES = {
    "Silo.log": SILO_LOG,
    "Silo.log.1": SILO_LOG + ".1",
    "Silo.log.2": SILO_LOG + ".2",
}
LOG_MAX_SIZE = 2 * 1024 * 1024
LOG_BACKUP_COUNT = 2

STATUS_LOGO_FILE = os.path.join(HELPER_DIR, "silo-status.png")

RESTART_SCRIPT = "/var/packages/{}/scripts/restart-silo".format(PACKAGE_NAME)
RESTART_LOCK = os.path.join(PACKAGE_VAR, "restart.lock")
RESTART_MIN_SECONDS = 3
RESTART_GIVE_UP_SECONDS = 90
RESTART_STATE = {"started": None}

# FFmpeg from SynoCommunity: the folder service-setup chose (ffmpeg8, else
# ffmpeg7) and passed on in FFMPEG_DIR. Same folder that
# service-setup puts first in Silo's PATH.
FFMPEG_CANDIDATES = ("/var/packages/ffmpeg8/target/bin", "/var/packages/ffmpeg7/target/bin")
FFMPEG_DIR = os.environ.get("FFMPEG_DIR") or next(
    (d for d in FFMPEG_CANDIDATES if os.access(os.path.join(d, "ffmpeg"), os.X_OK)),
    FFMPEG_CANDIDATES[0],
)
FFMPEG_BIN = os.path.join(FFMPEG_DIR, "ffmpeg")
# Silo takes the ffprobe that lies next to the ffmpeg it found.
FFPROBE_BIN = os.path.join(FFMPEG_DIR, "ffprobe")
DRI_DIR = os.environ.get("SILO_DSM_DRI") or "/dev/dri"
VIDEO_GROUP = "videodriver"
VAAPI_TEST_TIMEOUT = 20
VAAPI_RESULT = {"text": "", "ok": None, "device": ""}

LOCALE_DIR = os.path.join(HELPER_DIR, "locales")
LANGUAGE_FILE = os.path.join(PACKAGE_VAR, "helper.language")
SUPPORTED_LANGUAGES = ("en", "ru", "lt", "pl", "uk")
LANGUAGE_NAMES = {
    "en": "English",
    "ru": "Русский",
    "lt": "Lietuvių",
    "pl": "Polski",
    "uk": "Українська",
}



# ---------------------------------------------------------------------------
# Authentication
#
# nginx proxies /webman/3rdparty/TorrServerSilo/helper/ to this server WITHOUT any
# DSM login check, so the helper must verify the DSM session itself. It asks
# DSM's own authenticate.cgi (the documented way for third-party CGIs) who the
# caller is and only serves DSM administrators. Everything fails closed.
# ---------------------------------------------------------------------------
AUTH_CGI_PATHS = (
    "/usr/syno/synoman/webman/modules/authenticate.cgi",
    "/usr/syno/synoman/webman/authenticate.cgi",
)
ADMIN_GROUP = "administrators"
AUTH_CACHE_SECONDS = 15
AUTH_DENIED_CACHE_SECONDS = 2
AUTH_CACHE_MAX = 256

# The DSM desktop passes its CSRF token (SynoToken) in the iframe URL; it is
# kept in a cookie scoped to the helper so later links and form posts carry it.
TOKEN_COOKIE = "SiloSynoToken"
TOKEN_COOKIE_PATH = "/webman/3rdparty/TorrServerSilo/"
TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_\-+/=.]{1,256}$")
USER_PATTERN = re.compile(r"^[\w.@\\ -]{1,128}$")

_AUTH_CACHE = {}
_AUTH_CACHE_LOCK = threading.Lock()


def auth_log(message):
    try:
        print("helper-auth: {}".format(message), file=sys.stderr, flush=True)
    except Exception:
        pass


def parse_cgi_user(output):
    """Extract the user name printed by authenticate.cgi."""
    text = (output or "").replace("\r\n", "\n").strip()

    if not text:
        return ""

    # Some builds print CGI headers first.
    first = text.split("\n", 1)[0]
    if ":" in first and "\n\n" in text:
        text = text.split("\n\n", 1)[1].strip()

    line = text.split("\n", 1)[0].strip()
    return line if USER_PATTERN.match(line) else ""


def run_authenticate_cgi(cookie, token, remote_addr, host):
    """Return ``(user, reason)``; user is "" when the session is not valid."""
    reason = "authenticate.cgi not found"

    for path in AUTH_CGI_PATHS:
        if not os.access(path, os.X_OK):
            continue

        env = {
            "PATH": "/usr/syno/bin:/usr/syno/sbin:/usr/bin:/usr/sbin:/bin:/sbin",
            "HTTP_COOKIE": cookie,
            "REMOTE_ADDR": remote_addr,
            "HTTP_HOST": host,
            "REQUEST_METHOD": "GET",
            "SERVER_PROTOCOL": "HTTP/1.1",
        }
        if token:
            env["HTTP_X_SYNO_TOKEN"] = token

        try:
            result = subprocess.run(
                [path],
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=5,
                universal_newlines=True,
            )
        except Exception as e:
            reason = "{} failed: {}".format(os.path.basename(path), e)
            continue

        user = parse_cgi_user(result.stdout)
        if user:
            return user, ""

        reason = "{} rc={} returned no user (stdout {} bytes, stderr {} bytes)".format(
            path, result.returncode, len(result.stdout or ""), len(result.stderr or "")
        )

    return "", reason


def is_dsm_admin(username):
    """True when *username* belongs to the DSM administrators group.

    DSM user names are case-insensitive: authenticate.cgi may print "Vlad"
    while the account is stored as "vlad", so both spellings are tried.
    """
    try:
        group = grp.getgrnam(ADMIN_GROUP)
    except KeyError:
        return False

    wanted = username.lower()

    if any(member.lower() == wanted for member in group.gr_mem):
        return True

    entry = None
    system_name = username

    for candidate in dict.fromkeys((username, wanted)):
        try:
            entry = pwd.getpwnam(candidate)
            system_name = candidate
            break
        except KeyError:
            continue

    if entry is None:
        return False

    if entry.pw_gid == group.gr_gid:
        return True

    try:
        return group.gr_gid in os.getgrouplist(system_name, entry.pw_gid)
    except OSError:
        return False


def check_dsm_session(cookie, token, remote_addr, host=""):
    """Return ``(admin_user, reason)``; admin_user is "" when access is denied."""
    if not cookie:
        return "", "no session cookie"

    key = hashlib.sha256(
        "\0".join((cookie, token, remote_addr)).encode("utf-8", "replace")
    ).hexdigest()
    now = time.monotonic()

    with _AUTH_CACHE_LOCK:
        cached = _AUTH_CACHE.get(key)
        if cached and cached[0] > now:
            return cached[1], cached[2]

    user, reason = run_authenticate_cgi(cookie, token, remote_addr, host)

    if user and not is_dsm_admin(user):
        reason = "user '{}' is not a DSM administrator".format(user)
        user = ""

    lifetime = AUTH_CACHE_SECONDS if user else AUTH_DENIED_CACHE_SECONDS

    with _AUTH_CACHE_LOCK:
        if len(_AUTH_CACHE) >= AUTH_CACHE_MAX:
            for stale in [k for k, v in _AUTH_CACHE.items() if v[0] <= now]:
                _AUTH_CACHE.pop(stale, None)
            if len(_AUTH_CACHE) >= AUTH_CACHE_MAX:
                _AUTH_CACHE.clear()
        _AUTH_CACHE[key] = (now + lifetime, user, reason)

    return user, reason


def get_language():
    value = read_file(LANGUAGE_FILE, "en").strip().lower()
    return value if value in SUPPORTED_LANGUAGES else "en"


def set_language(language):
    language = str(language or "").strip().lower()
    if language not in SUPPORTED_LANGUAGES:
        return False
    write_file(LANGUAGE_FILE, language)
    return True


def load_locale(language=None):
    language = language or get_language()
    path = os.path.join(LOCALE_DIR, language + ".json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


_HTML_TOKEN_RE = re.compile(
    r"(<script\b[^>]*>)(.*?)(</script>)"      # 1-3: script open / body / close
    r"|<style\b.*?</style>"                   # style: never translated
    r"|<(pre|td|span|div)\b[^>]*\btranslate=\"no\"[^>]*>.*?</\4>"   # 4: data (log lines, paths)
    r"|<!--.*?-->"                             # comments: never translated
    r"|<[^>]+>",                               # any other tag
    re.S | re.I,
)
_TRANSLATABLE_ATTR_RE = re.compile(
    r"""(\b(?:placeholder|title|alt|aria-label)\s*=\s*)("[^"]*"|'[^']*')""",
    re.I,
)


CARD_ICONS = {
    "status": '<rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/>'
              '<rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/>',
    "settings": '<path d="M4 7h10M18 7h2M4 17h2M10 17h10"/><circle cx="16" cy="7" r="2"/><circle cx="8" cy="17" r="2"/>',
    "logs": '<path d="M6 3h9l4 4v14H6z"/><path d="M15 3v4h4M9 12h7M9 16h7"/>',
    "server": '<rect x="3" y="4" width="18" height="7" rx="2"/><rect x="3" y="13" width="18" height="7" rx="2"/>'
              '<path d="M7 7.5h.01M7 16.5h.01"/>',
    "cpu": '<rect x="6" y="6" width="12" height="12" rx="2"/><rect x="9.5" y="9.5" width="5" height="5"/>'
           '<path d="M9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M2 15h3M19 9h3M19 15h3"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
    "key": '<circle cx="8" cy="15" r="4"/><path d="M10.8 12.2L20 3M17 6l3 3M14.5 8.5l2 2"/>',
    "lock": '<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
    "globe": '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 2.4 3.8 5.4 3.8 9s-1.3 6.6-3.8 9c-2.5-2.4-3.8-5.4-3.8-9S9.5 5.4 12 3z"/>',
    "film": '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M7 5v14M17 5v14M3 9h4M3 15h4M17 9h4M17 15h4"/>',
}


def inline_icons(content):
    """Replace data-icon markers by inline SVG, drawn in the text colour."""
    def draw(match):
        name = match.group(2)
        return '{}><svg viewBox="0 0 24 24" aria-hidden="true">{}</svg>'.format(
            match.group(1), CARD_ICONS[name])
    return re.sub(r'(<span class="(?:metric|side)-icon[^"]*") data-icon="([a-z]+)">', draw, content)


def localize_html(content):
    """Translate English source strings in rendered HTML.

    Locale files use the English text as key. Only text that is meant for the
    reader is translated:

    * text nodes,
    * the placeholder / title / alt / aria-label attributes,
    * string literals inside <script> (the key must start right after a quote).

    Text from outside (log lines, folder names) is wrapped in an element with
    translate="no" and is never changed: a key such as "Start" must not turn
    "Starting" into "Запуститьing" or a folder called "Status" into "Статус".

    Everything else (``value``, ``href``, ``id``, ``onclick``, identifiers in
    scripts, CSS, ``data:`` URIs) is left untouched, so form values typed by
    the user, paths and embedded images can never be altered by a translation.
    """
    content = inline_icons(content)
    translations = {
        source: str(translated)
        for source, translated in load_locale().items()
        if source and source != str(translated)
    }
    if not translations:
        return content

    ordered = sorted(translations, key=len, reverse=True)

    # Keys that contain markup (e.g. <b>..</b>) span several tokens, so they
    # are applied to the whole document first. They are full sentences, so an
    # accidental match elsewhere is not a practical concern.
    for source in ordered:
        if "<" in source:
            content = content.replace(source, translations[source])

    plain = [source for source in ordered if "<" not in source]
    if not plain:
        return content

    text_re = re.compile("|".join(re.escape(source) for source in plain))
    script_re = re.compile(
        "(?<=['\"])(?:" + "|".join(re.escape(source) for source in plain) + ")"
    )

    def translate_text(text):
        return text_re.sub(lambda m: translations[m.group(0)], text)

    def translate_script(text):
        return script_re.sub(lambda m: translations[m.group(0)], text)

    def translate_tag(tag):
        return _TRANSLATABLE_ATTR_RE.sub(
            lambda m: m.group(1) + translate_text(m.group(2)), tag
        )

    parts = []
    position = 0

    for match in _HTML_TOKEN_RE.finditer(content):
        parts.append(translate_text(content[position:match.start()]))

        token = match.group(0)

        if match.group(1) is not None:
            parts.append(match.group(1))
            parts.append(translate_script(match.group(2)))
            parts.append(match.group(3))
        elif match.group(4) is not None:
            parts.append(token)          # marked translate="no": shown as it is
        elif token.startswith("<") and not re.match(r"<(?:style|!--)", token, re.I):
            parts.append(translate_tag(token))
        else:
            parts.append(token)

        position = match.end()

    parts.append(translate_text(content[position:]))
    return "".join(parts)


def language_selector():
    current = get_language()
    options = []
    for code in SUPPORTED_LANGUAGES:
        selected = " selected" if code == current else ""
        options.append(
            '<option value="{}"{}>{}</option>'.format(
                code, selected, html.escape(LANGUAGE_NAMES[code])
            )
        )

    return """
<div class="settings-card language-card">
    <div class="settings-card-body">
        <form method="post" action="./language">
            <div class="form-row">
                <label for="language"><span class="metric-icon" data-icon="globe"></span><span>Language</span></label>
                <select id="language" name="language" onchange="this.form.submit()">
                    {}
                </select>
            </div>
        </form>
    </div>
</div>
""".format("".join(options))



def read_file(path, default=""):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return default


def write_file(path, value, mode=None):
    """Atomically replace *path*. *mode* (e.g. 0o600) is applied at creation."""
    tmp = path + ".tmp"

    try:
        os.unlink(tmp)
    except OSError:
        pass

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(tmp, flags, 0o644 if mode is None else mode)

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


def read_dsm_version_file():
    for path in ("/etc.defaults/VERSION", "/etc/VERSION"):
        try:
            data = {}

            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()

                    if "=" in line:
                        key, value = line.split("=", 1)
                        data[key.strip()] = value.strip().strip('"')

            if data:
                return data

        except Exception:
            pass

    return {}


def get_dsm_version():
    data = read_dsm_version_file()
    version = data.get("productversion", "")
    build = data.get("buildnumber", "")

    if not version:
        return "Unknown"

    return "{}-{}".format(version, build) if build else version


def get_nas_model():
    model = read_file("/proc/sys/kernel/syno_hw_version", "").strip()

    if model:
        return model

    data = read_dsm_version_file()

    return (
        data.get("modelname", "").strip()
        or data.get("unique", "").strip()
        or "Unknown"
    )


def get_cpu_model():
    hardware = ""

    try:
        with open("/proc/cpuinfo", "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if ":" not in line:
                    continue

                key, value = line.split(":", 1)
                key = key.strip().lower()
                value = value.strip()

                if key == "model name" and value:
                    return value

                if key == "hardware" and value:
                    hardware = value

    except Exception:
        pass

    if hardware:
        return hardware

    try:
        value = platform.processor().strip()
        if value and not value.isdigit():
            return value
    except Exception:
        pass

    return "Unknown"


def get_cpu_cores():
    try:
        return os.cpu_count() or 1
    except Exception:
        return 1


def get_memory_text(path="/proc/meminfo"):
    """RAM as "used / total GB", or "-" when it cannot be read."""
    try:
        values = {}
        with open(path, encoding="utf-8") as f:
            for line in f:
                name, _, rest = line.partition(":")
                parts = rest.split()
                if parts and parts[0].isdigit():
                    values[name] = int(parts[0])
        total = values["MemTotal"]
        used = total - values.get("MemAvailable", values.get("MemFree", 0))
        gb = 1024.0 * 1024.0
        return "{:.1f} / {:.1f} GB".format(used / gb, total / gb)
    except (OSError, KeyError, ValueError):
        return "-"


def get_architecture():
    try:
        return platform.machine()
    except Exception:
        return "Unknown"




def get_silo_version():
    """Silo version and commit the package was built from (VERSION file)."""
    value = read_file(VERSION_FILE, "")
    return value or "Unknown"


def silo_pids():
    """Pids of this package's Silo (matched by the binary path, so another
    "silo" process on the NAS is never mistaken for it).

    Paths are compared after resolving links: DSM starts Silo as
    /volume1/@appstore/TorrServerSilo/bin/silo, while
    /var/packages/TorrServerSilo/target is a link to that folder.
    """
    pids = []
    try:
        names = os.listdir("/proc")
    except OSError:
        return pids

    wanted = os.path.realpath(SILO_BIN)
    for name in names:
        if not name.isdigit():
            continue
        try:
            with open("/proc/{}/cmdline".format(name), "rb") as f:
                first = f.read().split(b"\0", 1)[0]
        except OSError:
            continue
        if not first:
            continue
        if os.path.realpath(first.decode("utf-8", errors="replace")) == wanted:
            pids.append(int(name))
    return sorted(pids)


def is_silo_running():
    return bool(silo_pids())


def get_status():
    return "Running" if is_silo_running() else "Stopped"


def get_silo_uptime():
    pids = silo_pids()
    if not pids:
        return "Stopped"

    try:
        with open("/proc/{}/stat".format(pids[0]), "r", encoding="utf-8") as f:
            # The command name may contain spaces: count from its closing ")".
            stat_data = f.read().rsplit(")", 1)[1].split()

        start_ticks = int(stat_data[19])

        with open("/proc/uptime", "r", encoding="utf-8") as f:
            system_uptime = float(f.read().split()[0])

        clock_ticks = os.sysconf(os.sysconf_names["SC_CLK_TCK"])
        elapsed = max(0, int(system_uptime - (start_ticks / clock_ticks)))

        days = elapsed // 86400
        hours = (elapsed % 86400) // 3600
        minutes = (elapsed % 3600) // 60

        if days:
            return "{}d {}h {}m".format(days, hours, minutes)
        if hours:
            return "{}h {}m".format(hours, minutes)
        return "{}m".format(minutes)

    except Exception:
        return "Unknown"


# The message is built from three fixed sentences around the folder and the
# share, so that each part has its own translation and the user sees which
# shared folder needs the permission.
NOT_WRITABLE_A = "The TorrServerSilo service user cannot write to "
NOT_WRITABLE_B = ". Give the TorrServerSilo user Read/Write permission on the shared folder "
NOT_WRITABLE_C = " (in DSM: Control Panel → Shared Folder → Edit → Permissions → System internal user)."


def share_of(path):
    """/volume1/media/silo -> media (the shared folder that holds it)."""
    parts = [p for p in path.split("/") if p]
    return parts[1] if len(parts) >= 2 else (parts[0] if parts else path)


def not_writable_message(path):
    return NOT_WRITABLE_A + path + NOT_WRITABLE_B + share_of(path) + NOT_WRITABLE_C


def prepare_folder(path):
    """Create *path* as the service user and check that it can write there.

    Returns "" when the folder is usable, otherwise the message to show.
    """
    if os.path.islink(path):
        return "Folder must not be a symbolic link: {}".format(path)

    if os.path.exists(path) and not os.path.isdir(path):
        return "Not a folder: {}".format(path)

    try:
        os.makedirs(path, 0o755, exist_ok=True)
    except PermissionError:
        return not_writable_message(path)
    except OSError as e:
        return "Failed to create folder: {} ({})".format(path, e)

    if not os.access(path, os.W_OK | os.X_OK):
        return not_writable_message(path)
    return ""


def restart_silo():
    """
    Restart the Silo process; no root and no sudo are involved.

    restart-silo writes config.yaml again, stops the Silo binary and starts it
    with the new settings. The helper keeps running, so it is not touched, and
    its pid is handed over so the package pid file stays correct. The script
    gets its own session and finishes after this request is answered.
    """
    if not os.path.isfile(RESTART_SCRIPT):
        return False, "Restart script not found"

    environment = dict(os.environ)
    environment["SILO_HELPER_PID"] = str(os.getpid())

    try:
        process = subprocess.Popen(
            ["/bin/sh", RESTART_SCRIPT],
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )

        if process.pid <= 0:
            return False, "Failed to start restart script"

        RESTART_STATE["started"] = time.monotonic()
        return True, "Restarting..."

    except Exception as e:
        return False, str(e)


def restart_finished():
    """True once a restart started by this helper is over and Silo runs.

    The script takes its lock right after it starts, so a short grace period
    keeps the "no lock yet" moment from looking like "finished".
    """
    started = RESTART_STATE["started"]

    if started is None:
        return True

    if time.monotonic() - started < RESTART_MIN_SECONDS:
        return False

    if os.path.isdir(RESTART_LOCK):
        return False

    return is_silo_running()


def restart_in_progress():
    """True while a restart started by this helper is still running.

    Gives up after RESTART_GIVE_UP_SECONDS, so a Silo that never comes back
    is shown as stopped instead of restarting forever.
    """
    started = RESTART_STATE["started"]

    if started is None:
        return False

    if time.monotonic() - started > RESTART_GIVE_UP_SECONDS or restart_finished():
        RESTART_STATE["started"] = None
        return False

    return True


def last_problem_lines(count=3, width=220):
    """The last lines of the log: where Silo's own start-up errors end up
    (for example a port that is already taken)."""
    try:
        with open(SILO_LOG, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            handle.seek(max(0, handle.tell() - 16384))
            text = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return []

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return [line[:width] for line in lines[-count:]]


def stopped_info_html():
    lines = last_problem_lines()
    if not lines:
        return ""

    return (
        '<div class="stopped-info">'
        '<div class="status-subtitle">Last lines of the log:</div><pre translate="no">{}</pre>'
        '</div>'
    ).format(html.escape("\n".join(lines)))


START_ACTION = (
    '        <div class="web-ui-action">'
    '<form method="post" action="./restart"><button type="submit">Start</button></form>'
    '</div>\n'
)


# ---------------------------------------------------------------------------
# FFmpeg and VAAPI
# ---------------------------------------------------------------------------

def run_command(args, timeout=10):
    """``(returncode, output)``; returncode is None when it could not run."""
    try:
        result = subprocess.run(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            timeout=timeout,
        )
        return result.returncode, result.stdout or ""
    except subprocess.TimeoutExpired:
        return None, "timed out after {} seconds".format(timeout)
    except OSError as e:
        return None, str(e)


def get_ffmpeg_info():
    """What Silo will find: version and whether this build knows VAAPI."""
    info = {"found": False, "version": "", "vaapi": False}

    if not os.access(FFMPEG_BIN, os.X_OK):
        return info

    info["found"] = True
    code, output = run_command([FFMPEG_BIN, "-hide_banner", "-version"])
    if code == 0 and output:
        match = re.match(r"ffmpeg version (\S+)", output)
        info["version"] = match.group(1) if match else output.splitlines()[0][:80]

    code, output = run_command([FFMPEG_BIN, "-hide_banner", "-hwaccels"])
    if code == 0:
        info["vaapi"] = "vaapi" in output.split()
    return info


def get_dri_devices():
    """Render nodes in /dev/dri with their group and this user's access.

    The Helper runs as the same package user as Silo, so "access" here is
    exactly what Silo gets.
    """
    devices = []
    try:
        names = sorted(n for n in os.listdir(DRI_DIR) if n.startswith("renderD"))
    except OSError:
        return devices

    for name in names:
        path = os.path.join(DRI_DIR, name)
        try:
            st = os.stat(path)
            try:
                group = grp.getgrgid(st.st_gid).gr_name
            except KeyError:
                group = str(st.st_gid)
            mode = oct(st.st_mode & 0o777)[2:]
        except OSError:
            group, mode = "?", "?"
        devices.append({
            "path": path,
            "group": group,
            "mode": mode,
            "access": os.access(path, os.R_OK | os.W_OK),
        })
    return devices


def get_user_groups():
    names = []
    for gid in sorted(set(os.getgroups()) | {os.getgid()}):
        try:
            names.append(grp.getgrgid(gid).gr_name)
        except KeyError:
            names.append(str(gid))
    return names


def vaapi_test(device):
    """Two checks with the FFmpeg that Silo uses:

    1. the same device probe Silo runs before it chooses VAAPI,
    2. a one-second H.264 encode on the GPU (h264_vaapi).

    Returns ``(ok, report)``.
    """
    if not os.access(FFMPEG_BIN, os.X_OK):
        return False, "FFmpeg was not found: {}".format(FFMPEG_BIN)

    steps = [
        ("Device probe", [
            FFMPEG_BIN, "-hide_banner", "-v", "error",
            "-init_hw_device", "vaapi=probe:" + device,
            "-f", "lavfi", "-i", "nullsrc=s=64x64:d=1", "-f", "null", "-",
        ]),
        ("H.264 encode", [
            FFMPEG_BIN, "-hide_banner", "-v", "error",
            "-init_hw_device", "vaapi=va:" + device, "-filter_hw_device", "va",
            "-f", "lavfi", "-i", "testsrc2=s=320x240:r=25:d=1",
            "-vf", "format=nv12,hwupload", "-c:v", "h264_vaapi", "-f", "null", "-",
        ]),
    ]

    report = []
    ok = True
    for title, args in steps:
        code, output = run_command(args, timeout=VAAPI_TEST_TIMEOUT)
        passed = code == 0
        ok = ok and passed
        report.append("{}: {}".format(title, "OK" if passed else "FAILED"))
        if not passed:
            tail = [line for line in output.strip().splitlines() if line.strip()][-6:]
            report.extend("  " + line[:200] for line in tail)
            break

    return ok, "\n".join(report)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

PASSWORD_PLACEHOLDER = "••••••••"


def get_settings():
    return siloconf.load(PACKAGE_VAR)


def get_listening_tcp_ports():
    ports = set()

    for path in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(path, "r", encoding="ascii") as f:
                next(f, None)
                for line in f:
                    parts = line.split()
                    if len(parts) < 4 or parts[3] != "0A":
                        continue

                    try:
                        _, port_hex = parts[1].rsplit(":", 1)
                        ports.add(int(port_hex, 16))
                    except (ValueError, TypeError):
                        continue
        except (OSError, IOError):
            continue

    return ports


def is_port_in_use(port_number, allowed_ports=None):
    allowed_ports = set(allowed_ports or ())
    return port_number in (get_listening_tcp_ports() - allowed_ports)


# Form field -> settings key. Checkboxes are missing from a form post when they
# are off, so they are handled separately.
FORM_FIELDS = (
    "port", "data_dir", "log_level", "ffmpeg_hwaccel", "ffmpeg_hw_device",
    "ffmpeg_threads", "ffmpeg_max_sessions", "ffmpeg_cache_dir",
)


def save_settings(params):
    def field(name, default=""):
        return params.get(name, [default])[0]

    old = get_settings()
    raw = dict(old)

    for name in FORM_FIELDS:
        if name in params:
            raw[name] = field(name).strip()

    # Checkboxes are left out of a form post when they are off, so they are
    # only read from the full settings form (marked by its hidden field).
    full_form = field("form") == "settings"
    if full_form:
        raw["ffmpeg_enabled"] = field("ffmpeg_enabled", "0") == "1"

    # Password: unchanged while the placeholder is shown; off when the
    # checkbox is cleared.
    if not full_form:
        pass
    elif field("auth", "0") == "1":
        password = field("password")
        if password == PASSWORD_PLACEHOLDER or not password:
            if not old["owner_password"]:
                return False, "Password is required"
            password = old["owner_password"]
        raw["owner_password"] = password
    else:
        raw["owner_password"] = ""

    # ---- 1. Validate everything first; nothing is changed until all pass.
    try:
        new = siloconf.normalize(raw)
    except siloconf.SettingsError as e:
        return False, str(e)

    if new["port"] != old["port"] and is_port_in_use(new["port"]):
        return False, "Web port is already in use: {}".format(new["port"])

    for name in ("data_dir", "ffmpeg_cache_dir"):
        if new[name]:
            problem = prepare_folder(new[name])
            if problem:
                return False, problem

    # ---- 2. Apply.
    try:
        siloconf.save(new, PACKAGE_VAR)
        siloconf.write_config(PACKAGE_VAR, new)
    except OSError as e:
        return False, "Unable to write settings: {}".format(e)

    return True, "Settings saved"


def get_log_path(name):
    return LOG_FILES.get(name)


def get_log_by_name(name):
    log_path = get_log_path(name)
    if not log_path:
        return "Invalid log file"

    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            data = f.read()

        if len(data) > 200000:
            data = data[-200000:]

        return data

    except FileNotFoundError:
        return "Log file not found: {}".format(name)
    except Exception as e:
        return "Unable to read log: {}".format(e)


def rotate_log(path):
    try:
        if not os.path.isfile(path):
            return

        if os.path.getsize(path) < LOG_MAX_SIZE:
            return

        oldest = "{}.{}".format(path, LOG_BACKUP_COUNT)
        if os.path.exists(oldest):
            os.remove(oldest)

        for number in range(LOG_BACKUP_COUNT - 1, 0, -1):
            source = "{}.{}".format(path, number)
            target = "{}.{}".format(path, number + 1)

            if os.path.exists(source):
                os.replace(source, target)

        # copy + truncate: the writer keeps its file descriptor, so the file
        # cannot be renamed away.
        shutil.copyfile(path, "{}.1".format(path))
        os.truncate(path, 0)

    except Exception:
        pass


def rotate_log_if_needed():
    rotate_log(SILO_LOG)



def page_header(title="TorrServer Silo"):
    return """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{}</title>
<style>
* {{
    box-sizing: border-box;
}}

body {{
    font-family: Arial, Helvetica, sans-serif;
    margin: 0;
    background: #eef0f3;
    color: #222;
    font-size: 14px;
}}

.container {{
    max-width: 1100px;
    margin: 0 auto;
    padding: 14px 18px 30px;
}}

.card {{
    background: #fff;
    border: 1px solid #d6d9de;
    border-radius: 6px;
    padding: 20px;
    margin-bottom: 12px;
    box-shadow: 0 1px 2px rgba(0,0,0,.08);
}}

.card h1 {{
    margin: 0 0 18px;
    font-size: 25px;
    font-weight: 600;
}}

.card h2 {{
    margin: 0 0 12px;
    font-size: 20px;
    font-weight: 600;
}}

table {{
    width: 100%;
    border-collapse: collapse;
}}

td {{
    padding: 9px 6px;
    border-bottom: 1px solid #e5e7eb;
    vertical-align: middle;
}}

td:first-child {{
    width: 230px;
    font-weight: 600;
}}

input[type=text],
input[type=password],
input[type=number],
select {{
    height: 36px;
    width: 100%;
    max-width: 520px;
    padding: 7px 10px;
    border: 1px solid #bfc4cb;
    border-radius: 3px;
    background: #fff;
    color: #222;
    font-size: 14px;
}}

input:focus,
select:focus {{
    outline: none;
    border-color: #1677ff;
    box-shadow: 0 0 0 2px rgba(22,119,255,.12);
}}

button,
.button {{
    display: inline-block;
    min-height: 36px;
    border: 1px solid transparent;
    border-radius: 4px;
    padding: 8px 15px;
    cursor: pointer;
    text-decoration: none;
    background: #1677ff;
    color: #fff;
    font-size: 14px;
    line-height: 18px;
}}

button:hover,
.button:hover {{
    filter: brightness(.96);
}}

button.secondary,
.button.secondary {{
    background: #6b6f75;
}}

button.danger,
.button.danger {{
    background: #d32f2f;
}}

button.danger:disabled,
.button.danger:disabled {{
    background: #777;
    opacity: 0.55;
    cursor: not-allowed;
}}

.status-running {{
    color: #16803c;
    font-weight: 600;
}}

.status-stopped {{
    color: #c62828;
    font-weight: 600;
}}

.browse-denied {{
    float: right;
    padding: 1px 9px;
    border-radius: 10px;
    background: #fef3c7;
    color: #92400e;
    font-size: 12px;
    font-weight: 600;
}}

.beta-note {{
    margin: 0 0 14px;
    padding: 10px 14px;
    border: 1px solid #f5d48a;
    border-radius: 8px;
    background: #fff8e6;
    color: #6b4f00;
}}

.value-good {{
    color: #15803d;
    font-weight: 600;
}}

.value-bad {{
    color: #c62828;
    font-weight: 600;
}}

.wide-card {{
    grid-column: 1 / -1;
}}

.wide-layout {{
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: 18px;
}}

.vaapi-form {{
    display: flex;
    gap: 8px;
}}

.vaapi-form button {{
    white-space: nowrap;
}}

.vaapi-form select {{
    max-width: 260px;
}}

.vaapi-panel > div {{
    margin-top: 12px;
}}

.vaapi-result {{
    box-sizing: border-box;
    max-width: 100%;
    min-height: 0;
    margin-top: 6px;
    padding: 10px 12px;
    overflow-x: auto;
    white-space: pre-wrap;
}}

.path-picker {{
    display: flex;
    gap: 8px;
    max-width: 620px;
}}

.stopped-info {{
    flex: 0 0 calc(100% - 90px);
    min-width: 0;
    margin: 14px 0 0 90px;
}}

.status-banner:has(.stopped-info) {{
    flex-wrap: wrap;
}}

.stopped-info pre {{
    box-sizing: border-box;
    max-width: 100%;
    overflow-x: auto;
    min-height: 0;
    font-size: 12px;
    padding: 10px 12px;
    margin: 6px 0 0;
}}

.status-restarting {{
    color: #b45309;
    font-weight: 600;
}}

.notice {{
    background: #fff7d6;
    border: 1px solid #e6cf75;
    border-left: 4px solid #e0b100;
    border-radius: 4px;
    padding: 12px 14px;
    margin-bottom: 16px;
    color: #5f4b00;
}}

.notice strong {{
    color: #4d3d00;
}}

.form-row {{
    margin-bottom: 16px;
}}

.form-row label {{
    display: block;
    margin-bottom: 6px;
    font-weight: 600;
}}

.help {{
    margin-top: 6px;
    color: #6b7280;
    font-size: 13px;
}}

.actions {{
    display: flex;
    gap: 8px;
    align-items: center;
    margin-top: 24px;
    padding-top: 16px;
    border-top: 1px solid #d9dce1;
}}

pre {{
    white-space: pre-wrap;
    word-break: break-word;
    background: #111;
    color: #ddd;
    padding: 15px;
    border-radius: 4px;
    overflow-x: auto;
    min-height: 180px;
    margin: 0;
    font-family: Consolas, "Courier New", monospace;
    font-size: 13px;
    line-height: 1.35;
}}

.checkbox-row {{
    margin: 8px 0;
}}

.app-shell {{
    display: flex;
    min-height: calc(100vh - 28px);
    margin: -14px -18px -30px;
    background: #f1f5f9;
}}

.app-sidebar {{
    width: 220px;
    flex: 0 0 220px;
    background: #ffffff;
    border-right: 1px solid #d6dee8;
    padding: 18px 10px;
}}

.side-item {{
    display: flex;
    align-items: center;
    gap: 12px;
    height: 44px;
    margin: 3px 0;
    padding: 0 13px;
    border-radius: 5px;
    color: #30415e;
    text-decoration: none;
    font-size: 14px;
}}

.side-item:hover {{
    background: #edf5ff;
}}

.side-item.active {{
    background: #e5f1ff;
    color: #1167c9;
    box-shadow: inset 3px 0 0 #1677ff;
}}

.side-icon {{
    width: 22px;
    height: 22px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
}}

.side-icon svg {{
    width: 20px;
    height: 20px;
    fill: none;
    stroke: currentColor;
    stroke-width: 1.8;
    stroke-linecap: round;
    stroke-linejoin: round;
}}

.app-content {{
    flex: 1;
    min-width: 0;
    padding: 28px 28px 36px;
}}

.app-title {{
    font-size: 28px;
    font-weight: 600;
    color: #17233b;
    margin-bottom: 16px;
}}

.status-banner {{
    min-height: 104px;
    background: #fff;
    border: 1px solid #d9e1eb;
    border-radius: 7px;
    box-shadow: 0 1px 3px rgba(30,50,80,.06);
    display: flex;
    align-items: center;
    padding: 18px 22px;
    margin-bottom: 18px;
}}

.status-logo {{
    width: 72px;
    height: 72px;
    flex: 0 0 72px;
    margin-right: 18px;
    display: flex;
    align-items: center;
    justify-content: center;
}}

.status-logo img {{
    width: 72px;
    height: 72px;
    object-fit: contain;
    display: block;
}}

.status-text {{
    flex: 1;
}}

.status-text .status-running,
.status-text .status-restarting,
.status-text .status-stopped {{
    font-size: 23px;
}}

.status-subtitle {{
    color: #627089;
    margin-top: 5px;
    font-size: 14px;
}}

.light-button {{
    background: #eaf3ff !important;
    color: #1a3f70 !important;
    border-color: #d4e5f8 !important;
}}

.dashboard-grid {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 18px;
}}

.dashboard-card {{
    background: #fff;
    border: 1px solid #d9e1eb;
    border-radius: 7px;
    box-shadow: 0 1px 3px rgba(30,50,80,.06);
    padding: 20px 22px;
    min-width: 0;
}}

.dashboard-card-title {{
    display: flex;
    align-items: center;
    gap: 11px;
    font-size: 19px;
    font-weight: 600;
    color: #17233b;
    padding-bottom: 13px;
    border-bottom: 1px solid #e2e8f0;
    margin-bottom: 4px;
}}

.metric-icon {{
    width: 34px;
    height: 34px;
    border-radius: 7px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    background: #edf3fa;
    color: #354b68;
}}

.metric-icon svg {{
    width: 19px;
    height: 19px;
    fill: none;
    stroke: currentColor;
    stroke-width: 1.8;
    stroke-linecap: round;
    stroke-linejoin: round;
}}

.metric-table td {{
    padding: 9px 2px;
    border-bottom: 1px solid #e4e9ef;
}}

.metric-table td:first-child {{
    width: 46%;
    font-weight: 400;
    color: #40536f;
}}

.metric-table td.path-value {{
    word-break: break-all;
}}

.metric-table td:last-child {{
    color: #1d2e49;
}}

.download {{
    color: #20a04b;
}}

.settings-layout {{
    max-width: 980px;
}}

.settings-card {{
    background: #fff;
    border: 1px solid #d9e1eb;
    border-radius: 7px;
    box-shadow: 0 1px 3px rgba(30,50,80,.06);
    margin-bottom: 16px;
    overflow: hidden;
}}

.language-card .settings-card-body {{
    padding-top: 12px;
    padding-bottom: 12px;
}}

.language-card .form-row {{
    margin-bottom: 0;
}}

.language-card label {{
    display: flex;
    align-items: center;
    gap: 11px;
}}

.settings-card.disabled {{
    opacity: 0.55;
}}

.settings-card-title {{
    display: flex;
    align-items: center;
    gap: 11px;
    padding: 15px 18px;
    border-bottom: 1px solid #e2e8f0;
    font-size: 18px;
    font-weight: 600;
    color: #17233b;
}}

.settings-card-body {{
    padding: 18px 20px;
}}

.settings-card .form-row {{
    display: grid;
    grid-template-columns: 190px minmax(0, 1fr);
    align-items: center;
    gap: 18px;
    margin-bottom: 14px;
}}

.settings-card .form-row label {{
    margin: 0;
    color: #30415e;
}}

.settings-card input[type=text],
.settings-card input[type=password],
.settings-card input[type=number],
.settings-card select {{
    max-width: 620px;
}}

.settings-card .checkbox-row {{
    margin: 0 0 14px;
}}

.settings-card .checkbox-row label {{
    color: #30415e;
}}

.settings-card .help {{
    margin: -6px 0 14px 208px;
    color: #64748b;
}}

@media (max-width: 800px) {{
    .settings-card .help {{
        margin-left: 0;
    }}
}}

.settings-card.save-bar {{
    position: sticky;
    bottom: 0;
    z-index: 5;
    margin-bottom: 0;
    box-shadow: 0 -3px 10px rgba(30,50,80,.10);
}}

.settings-card .actions {{
    margin: 0;
    padding: 16px 20px;
    background: #f8fafc;
}}

.logs-card {{
    background: #fff;
    border: 1px solid #d9e1eb;
    border-radius: 7px;
    box-shadow: 0 1px 3px rgba(30,50,80,.06);
    padding: 18px;
}}

.logs-toolbar {{
    display: flex;
    gap: 8px;
    align-items: center;
    margin-bottom: 14px;
}}

.logs-toolbar select {{
    width: 220px;
}}

.logs-toolbar button,
.logs-toolbar .button {{
    min-height: 36px;
}}

.logs-output {{
    min-height: 520px;
    max-height: calc(100vh - 250px);
    overflow: auto;
    background: #101820;
    border: 1px solid #0b1117;
    color: #d8dee7;
    padding: 16px;
    border-radius: 5px;
    white-space: pre-wrap;
    word-break: break-word;
    font-family: Consolas, "Courier New", monospace;
    font-size: 12px;
    line-height: 1.42;
}}

@media (max-width: 800px) {{
    .container {{
        padding: 10px;
    
    
    }}

    .app-shell {{
        margin: -10px -10px -30px;
    
    
    }}

    .app-sidebar {{
        width: 170px;
        flex-basis: 170px;
    
    
    }}

    .app-content {{
        padding: 18px 14px 28px;
    
    
    }}

    .dashboard-grid {{
        grid-template-columns: 1fr;
    
    
    }}

    .settings-card .form-row {{
        grid-template-columns: 1fr;
        gap: 6px;
    
    
    }}

    .settings-card input[type=text],
.settings-card input[type=password],
.settings-card input[type=number],
.settings-card select {{
        max-width: 100%;
    
    
    }}

    .logs-toolbar {{
        align-items: stretch;
        flex-direction: column;
    
    
    }}

    .logs-toolbar select {{
        width: 100%;
    
    
    }}
}}

.web-ui-actions {{
    display: flex;
    gap: 10px;
    align-items: flex-start;
}}

.web-ui-action {{
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 5px;
}}

.web-ui-address {{
    font-size: 11px;
    color: #64748b;
    white-space: nowrap;
}}

.button.disabled {{
    opacity: 0.42;
    cursor: default;
    pointer-events: none;
}}

.web-ui-address.disabled {{
    color: #94a3b8;
}}

.info-card {{
    position: relative;
    grid-column: 1 / -1;
}}

.info-maintainer-top {{
    position: absolute;
    top: 18px;
    right: 20px;
    font-size: 11px;
    color: #64748b;
    white-space: nowrap;
}}

.info-layout {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 24px;
    padding-top: 12px;
}}

.info-text {{
    min-width: 180px;
}}

.info-title {{
    font-size: 18px;
    font-weight: 600;
    color: #1d2e49;
}}

.info-subtitle {{
    margin-top: 5px;
    color: #64748b;
    font-size: 14px;
}}

.info-links {{
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 8px;
    flex: 1 1 auto;
    max-width: 460px;
}}

.info-link {{
    display: flex;
    align-items: center;
    justify-content: center;
    box-sizing: border-box;
    min-height: 40px;
    padding: 6px 12px;
    text-align: center;
    line-height: 1.3;
    border: 1px solid #d6e0ec;
    border-radius: 6px;
    background: #f7f9fc;
    color: #294766;
    text-decoration: none;
    font-size: 13px;
    font-weight: 500;
}}

.info-link:hover {{
    background: #edf3fa;
    border-color: #c7d5e5;
}}

.info-donate {{
    background: #fff5e9;
    border-color: #f3d5b1;
    color: #a85c13;
}}

.info-donate:hover {{
    background: #ffecd6;
    border-color: #e8bf8e;
}}

@media (max-width: 800px) {{
    .info-layout {{
        flex-direction: column;
        align-items: stretch;
    
    
    }}

    .info-links {{
        grid-template-columns: 1fr;
    }}

    .info-maintainer-top {{
        position: static;
        margin-bottom: 8px;
        white-space: normal;
    
    
    }}
}}
</style>
</head>
<body>
<div class="container">
""".format(html.escape(title))

def page_footer():
    return """
</div>
</body>
</html>
"""


def app_sidebar(active):
    items = [
        ("./", "status", "Status", "status"),
        ("./settings", "settings", "Settings", "settings"),
        ("./logs", "logs", "Logs", "logs"),
    ]

    parts = ['<div class="app-sidebar">']
    for href, icon, label, key in items:
        cls = "side-item active" if key == active else "side-item"
        parts.append(
            '<a class="{}" href="{}"><span class="side-icon" data-icon="{}"></span><span>{}</span></a>'.format(
                cls, href, icon, label
            )
        )
    parts.append("</div>")
    return "".join(parts)


def get_status_logo_data_uri():
    try:
        with open(STATUS_LOGO_FILE, "rb") as f:
            encoded = base64.b64encode(f.read()).decode("ascii")
        return "data:image/png;base64,{}".format(encoded)
    except Exception:
        return ""

RESTART_WATCH_SCRIPT = """
<script>
(function () {
    var started = Date.now();

    function poll() {
        if (Date.now() - started > 100000) {
            window.location.reload();
            return;
        }

        fetch('./restart-status', {cache: 'no-store', credentials: 'same-origin'})
            .then(function (response) { return response.text(); })
            .then(function (text) {
                if (text.trim() === 'ready') {
                    window.location.reload();
                } else {
                    setTimeout(poll, 1000);
                }
            })
            .catch(function () { setTimeout(poll, 1500); });
    }

    setTimeout(poll, 1500);
})();
</script>
"""

# While nothing is restarting, the status page notices by itself that
# Silo was stopped or started somewhere else (its own web page, DSM).
STATE_WATCH_SCRIPT = """
<script>
(function () {
    var shown = '__STATE__';

    function poll() {
        if (document.hidden) {
            setTimeout(poll, 5000);
            return;
        }

        fetch('./server-state', {cache: 'no-store', credentials: 'same-origin'})
            .then(function (response) { return response.text(); })
            .then(function (text) {
                var now = text.trim();
                if ((now === 'running' || now === 'stopped') && now !== shown) {
                    window.location.reload();
                } else {
                    setTimeout(poll, 5000);
                }
            })
            .catch(function () { setTimeout(poll, 8000); });
    }

    setTimeout(poll, 5000);
})();
</script>
"""





def yes_no(value):
    return "Enabled" if value else "Disabled"


HWACCEL_LABELS = {
    "none": "None (CPU only)",
    "vaapi": "VAAPI (Intel)",
    "auto": "Automatic",
}


def transcoding_card(settings):
    info = get_ffmpeg_info()
    devices = get_dri_devices()
    groups = get_user_groups()

    if info["found"]:
        ffmpeg_value = '<span translate="no">{}</span>'.format(html.escape(info["version"] or "?"))
    else:
        ffmpeg_value = '<span class="value-bad">Not found</span>'

    if os.access(FFPROBE_BIN, os.X_OK):
        ffprobe_value = '<span translate="no">{}</span>'.format(html.escape(FFPROBE_BIN))
    else:
        ffprobe_value = '<span class="value-bad">Not found</span>'

    if devices:
        rows = []
        for device in devices:
            rows.append(
                '<span translate="no">{} ({}, {})</span> {}'.format(
                    html.escape(device["path"]),
                    html.escape(device["group"]),
                    html.escape(device["mode"]),
                    '<span class="value-good">access OK</span>' if device["access"]
                    else '<span class="value-bad">no access</span>',
                )
            )
        dri_value = "<br>".join(rows)
    else:
        dri_value = "No render device (no Intel GPU, or an ARM model)"

    group_value = '<span translate="no">{}</span>'.format(html.escape(", ".join(groups)))
    if VIDEO_GROUP in groups:
        group_value += ' <span class="value-good">videodriver</span>'

    test_form = ""
    if devices and info["found"]:
        options = "".join(
            '<option value="{0}"{1}>{0}</option>'.format(
                html.escape(device["path"], quote=True),
                " selected" if device["path"] == (VAAPI_RESULT["device"] or devices[0]["path"]) else "",
            )
            for device in devices
        )
        test_form = (
            '<form method="post" action="./vaapi-test" class="vaapi-form">'
            '<select name="device" translate="no">{}</select>'
            '<button type="submit">Test VAAPI</button>'
            '</form>'
        ).format(options)

    result_html = ""
    if VAAPI_RESULT["text"]:
        result_html = '<div class="{}">{}</div><pre translate="no" class="vaapi-result">{}</pre>'.format(
            "value-good" if VAAPI_RESULT["ok"] else "value-bad",
            "VAAPI works" if VAAPI_RESULT["ok"] else "VAAPI does not work",
            html.escape(VAAPI_RESULT["text"]),
        )

    return """
<div class="dashboard-card wide-card">
    <div class="dashboard-card-title">
        <span class="metric-icon" data-icon="film"></span>
        <span>Transcoding</span>
    </div>

    <div class="wide-layout">
        <table class="metric-table">
            <tr><td>Transcoding</td><td>{enabled}</td></tr>
            <tr><td>FFmpeg</td><td>{ffmpeg}</td></tr>
            <tr><td>FFprobe</td><td>{ffprobe}</td></tr>
            <tr><td>VAAPI in FFmpeg</td><td>{vaapi}</td></tr>
            <tr><td>Acceleration</td><td>{hwaccel}</td></tr>
            <tr><td>Render devices</td><td>{dri}</td></tr>
            <tr><td>Groups</td><td>{groups}</td></tr>
        </table>
        <div class="vaapi-panel">
            {test_form}
            {result}
        </div>
    </div>
</div>
""".format(
        enabled=yes_no(settings["ffmpeg_enabled"]),
        ffmpeg=ffmpeg_value,
        ffprobe=ffprobe_value,
        vaapi=("Supported" if info["vaapi"] else "Not supported") if info["found"] else "-",
        hwaccel=HWACCEL_LABELS.get(settings["ffmpeg_hwaccel"], settings["ffmpeg_hwaccel"]),
        dri=dri_value,
        groups=group_value,
        test_form=test_form,
        result=result_html,
    )


def main_page(host):
    settings = get_settings()
    status = get_status()
    port = settings["port"]

    restarting = restart_in_progress()

    web_ui_actions = ""
    if status == "Running":
        web_ui_actions = '''        <div class="web-ui-action">
            <a class="button light-button"
               href="http://{0}:{1}/"
               target="_blank">
                🌐 Open Silo ↗
            </a>
            <div class="web-ui-address">http://{0}:{1}</div>
        </div>
'''.format(host, port)

    if restarting:
        status = "Restarting"
        status_class = "status-restarting"
        status_text = "Silo is restarting. This page will update automatically."
    else:
        status_class = "status-running" if status == "Running" else "status-stopped"
        status_text = "Silo is running." if status == "Running" else "Silo is not running."

    if status == "Stopped" and not restarting:
        # Where the "Open" button is while Silo runs.
        web_ui_actions = START_ACTION

    body = page_header("TorrServer Silo")

    body += """
<div class="app-shell">

{sidebar}

<div class="app-content">

<div class="app-title">Status</div>

<div class="beta-note">Silo is an early rewrite of TorrServer: many things do not work yet. Report problems of the package on the Silo DSM project page.</div>

<div class="status-banner">
    <div class="status-logo">
        <img src="{logo}" alt="TorrServer Silo">
    </div>
    <div class="status-text">
        <div class="{status_class}">{status}</div>
        <div class="status-subtitle">{status_text}</div>
    </div>
    {actions}
    {stopped_info}
</div>

<div class="dashboard-grid">

<div class="dashboard-card">
    <div class="dashboard-card-title">
        <span class="metric-icon server-icon" data-icon="server"></span>
        <span>Silo</span>
    </div>

    <table class="metric-table">
        <tr><td>Version</td><td translate="no">{version}</td></tr>
        <tr><td>Web port</td><td>{port}</td></tr>
        <tr><td>Data folder</td><td class="path-value" translate="no">{data_dir}</td></tr>
        <tr><td>Password</td><td>{auth}</td></tr>
        <tr><td>Uptime</td><td>{uptime}</td></tr>
    </table>
</div>


<div class="dashboard-card">
    <div class="dashboard-card-title">
        <span class="metric-icon system-icon" data-icon="cpu"></span>
        <span>System</span>
    </div>

    <table class="metric-table">
        <tr><td>DSM</td><td>{dsm}</td></tr>
        <tr><td>NAS model</td><td>{model}</td></tr>
        <tr><td>CPU</td><td>{cpu}</td></tr>
        <tr><td>Cores</td><td>{cores}</td></tr>
        <tr><td>Memory</td><td>{memory}</td></tr>
        <tr><td>Architecture</td><td>{arch}</td></tr>
    </table>
</div>

{transcoding}

<div class="dashboard-card info-card">
    <div class="info-maintainer-top">TorrServer Silo for Synology, maintained by vladlenas</div>

    <div class="dashboard-card-title">
        <span class="metric-icon" data-icon="info"></span>
        <span>Information</span>
    </div>

    <div class="info-layout">
        <div class="info-text">
            <div class="info-title">TorrServer Silo</div>
            <div class="info-subtitle">Silo project, Silo DSM package and support</div>
        </div>

        <div class="info-links">
            <a class="info-link" href="https://github.com/YouROK/TorrServer/tree/silo" target="_blank" rel="noopener noreferrer">Silo Project ↗</a>
            <a class="info-link" href="https://github.com/vladlenas/Silo-DSM" target="_blank" rel="noopener noreferrer">Silo DSM Project ↗</a>
            <a class="info-link info-donate" href="https://github.com/YouROK/TorrServer#donate" target="_blank" rel="noopener noreferrer">♥ Support TorrServer project ↗</a>
            <a class="info-link info-donate" href="https://paypal.me/VoxelFirmware" target="_blank" rel="noopener noreferrer">♥ Support Silo DSM project ↗</a>
        </div>
    </div>
</div>

</div>
</div>
</div>
""".format(
        sidebar=app_sidebar("status"),
        logo=get_status_logo_data_uri(),
        status_class=status_class,
        status=html.escape(status),
        status_text=status_text,
        actions='<div class="web-ui-actions{}">{}</div>'.format(
            " start-actions" if web_ui_actions is START_ACTION else "", web_ui_actions
        ) if web_ui_actions else "",
        stopped_info=stopped_info_html() if (status == "Stopped" and not restarting) else "",
        version=html.escape(get_silo_version()),
        port=port,
        data_dir=html.escape(siloconf.resolved_data_dir(settings, PACKAGE_VAR)),
        auth=yes_no(settings["owner_password"]),
        uptime=html.escape(get_silo_uptime()),
        transcoding=transcoding_card(settings),
        dsm=html.escape(get_dsm_version()),
        model=html.escape(get_nas_model()),
        cpu=html.escape(get_cpu_model()),
        cores=get_cpu_cores(),
        memory=html.escape(get_memory_text()),
        arch=html.escape(get_architecture()),
    )

    if restarting:
        body += RESTART_WATCH_SCRIPT
    else:
        body += STATE_WATCH_SCRIPT.replace("__STATE__", status.lower())

    body += page_footer()
    return localize_html(body)


def option_list(values, labels, current):
    return "".join(
        '<option value="{}"{}>{}</option>'.format(
            html.escape(value, quote=True),
            " selected" if value == current else "",
            html.escape(labels.get(value, value)),
        )
        for value in values
    )


LOG_LEVEL_LABELS = {
    "debug": "Detailed (debug)",
    "info": "Normal",
    "warn": "Warnings and errors",
    "error": "Errors only",
}


def settings_page(message="", overrides=None):
    settings = get_settings()
    for key, value in (overrides or {}).items():
        if value:
            settings[key] = value

    password = settings["owner_password"]

    body = page_header("TorrServer Silo Settings")

    body += """
<div class="app-shell">

{sidebar}

<div class="app-content">

<div class="app-title">Settings</div>

{language_selector}

<div class="notice">
To apply changes, click <b>Save</b>, then <b>Restart</b>.<br>The settings are written to config.yaml, which Silo reads at start.
</div>
""".format(
        sidebar=app_sidebar("settings"),
        language_selector=language_selector(),
    )

    if message:
        body += '<div class="notice">{}</div>'.format(html.escape(message))

    body += """
<div class="settings-layout">

<form method="post" action="./settings">
<input type="hidden" name="form" value="settings">

<div class="settings-card">
    <div class="settings-card-title">
        <span class="metric-icon" data-icon="server"></span>
        <span>Silo</span>
    </div>
    <div class="settings-card-body">

        <div class="form-row">
            <label for="webPort">Web port</label>
            <input id="webPort" type="number" name="port" min="1024" max="65535" value="{port}">
        </div>
        <div class="help">TorrServer DSM uses 8090, so Silo has its own port (8095 by default).</div>

        <div class="form-row">
            <label for="dataDir">Data folder</label>
            <div class="path-picker">
                <input id="dataDir" type="text" name="data_dir" value="{data_dir}" placeholder="{default_data_dir}">
                <button type="button" class="secondary" onclick="browse('data_dir')">Browse</button>
            </div>
        </div>
        <div class="help">Optional. Silo keeps its database and the transcoding cache here. Empty: the package folder. Existing data is not moved when the folder changes.</div>

        <div class="form-row">
            <label for="logLevel">Log level</label>
            <select id="logLevel" name="log_level">{log_levels}</select>
        </div>

    </div>
</div>

<div class="settings-card">
    <div class="settings-card-title">
        <span class="metric-icon" data-icon="key"></span>
        <span>Access</span>
    </div>
    <div class="settings-card-body">

        <div class="checkbox-row">
            <label>
                <input type="checkbox" name="auth" value="1" {auth_checked} onchange="toggleAuth()">
                Require the owner password
            </label>
        </div>

        <div class="form-row">
            <label for="password">Owner password</label>
            <input id="password" type="password" name="password" value="{password}" data-password-placeholder="{password}" {auth_disabled}>
        </div>
        <div class="help">Without a password anyone on your network can use Silo. The password is stored in config.yaml, readable only by the package user.</div>

    </div>
</div>

<div class="settings-card">
    <div class="settings-card-title">
        <span class="metric-icon" data-icon="film"></span>
        <span>Transcoding</span>
    </div>
    <div class="settings-card-body">

        <div class="checkbox-row">
            <label>
                <input type="checkbox" name="ffmpeg_enabled" value="1" {ffmpeg_checked}>
                Enable transcoding (FFmpeg)
            </label>
        </div>

        <div class="form-row">
            <label for="hwaccel">Hardware acceleration</label>
            <select id="hwaccel" name="ffmpeg_hwaccel">{hwaccel_options}</select>
        </div>
        <div class="help">VAAPI needs an Intel CPU with integrated graphics. Test it on the Status page first.</div>

        <div class="form-row">
            <label for="hwDevice">VAAPI device</label>
            <input id="hwDevice" type="text" name="ffmpeg_hw_device" value="{hw_device}" placeholder="/dev/dri/renderD128">
        </div>

        <div class="form-row">
            <label for="threads">FFmpeg threads</label>
            <input id="threads" type="number" name="ffmpeg_threads" min="0" max="64" value="{threads}">
        </div>
        <div class="help">0 lets FFmpeg decide.</div>

        <div class="form-row">
            <label for="maxSessions">Simultaneous transcodes</label>
            <input id="maxSessions" type="number" name="ffmpeg_max_sessions" min="0" max="32" value="{max_sessions}">
        </div>

        <div class="form-row">
            <label for="cacheDir">Transcoding cache folder</label>
            <div class="path-picker">
                <input id="cacheDir" type="text" name="ffmpeg_cache_dir" value="{cache_dir}" placeholder="{default_cache_dir}">
                <button type="button" class="secondary" onclick="browse('ffmpeg_cache_dir')">Browse</button>
            </div>
        </div>
        <div class="help">Optional. Empty: the transcode folder inside the data folder.</div>

    </div>
</div>

<div class="settings-card save-bar">
    <div class="actions">
        <button type="submit">Save</button>
        <button type="submit" formaction="./restart" class="danger">Restart</button>
    </div>
</div>

</form>
</div>

<script>
function toggleAuth() {{
    var checkbox = document.querySelector('input[name="auth"]');
    document.getElementById('password').disabled = !checkbox.checked;
}}

function browse(name) {{
    var field = document.querySelector('input[name="' + name + '"]');
    var path = field.value.trim() || '/';
    window.location.href = './browse?path=' + encodeURIComponent(path) + '&target=' + name;
}}

var passwordField = document.getElementById('password');
var passwordPlaceholder = passwordField.getAttribute('data-password-placeholder') || '';
passwordField.addEventListener('focus', function() {{
    if (this.value === passwordPlaceholder) {{ this.value = ''; }}
}});
passwordField.addEventListener('blur', function() {{
    if (!this.value && passwordPlaceholder) {{ this.value = passwordPlaceholder; }}
}});

toggleAuth();
</script>
""".format(
        port=settings["port"],
        data_dir=html.escape(settings["data_dir"], quote=True),
        default_data_dir=html.escape(os.path.join(PACKAGE_VAR, siloconf.DEFAULT_DATA_SUBDIR), quote=True),
        log_levels=option_list(siloconf.LOG_LEVELS, LOG_LEVEL_LABELS, settings["log_level"]),
        auth_checked="checked" if password else "",
        auth_disabled="" if password else "disabled",
        password=html.escape(PASSWORD_PLACEHOLDER if password else "", quote=True),
        ffmpeg_checked="checked" if settings["ffmpeg_enabled"] else "",
        hwaccel_options=option_list(siloconf.HWACCEL_METHODS, HWACCEL_LABELS, settings["ffmpeg_hwaccel"]),
        hw_device=html.escape(settings["ffmpeg_hw_device"], quote=True),
        threads=settings["ffmpeg_threads"],
        max_sessions=settings["ffmpeg_max_sessions"],
        cache_dir=html.escape(settings["ffmpeg_cache_dir"], quote=True),
        default_cache_dir=html.escape(
            os.path.join(siloconf.resolved_data_dir(settings, PACKAGE_VAR), "transcode"), quote=True),
    )

    body += page_footer()
    return localize_html(body)

def cache_browser_path(path):
    """Return a safe cache-browser path under /volume* only."""
    if not path:
        return "/"

    path = os.path.abspath(path)

    if path == "/":
        return "/"

    if not re.match(r"^/volume[0-9]+(?:/.*)?$", path):
        return "/"

    real = os.path.realpath(path)
    if not re.match(r"^/volume[0-9]+(?:/.*)?$", real):
        return "/"

    if not os.path.isdir(real):
        return "/"

    return real


BROWSE_TARGETS = {
    "data_dir": "Select data folder",
    "ffmpeg_cache_dir": "Select transcoding cache folder",
}


def cache_browser_page(path, target="data_dir"):
    path = cache_browser_path(path)

    if path == "/":
        # At the top level show only DSM volumes.
        try:
            names = sorted(
                name for name in os.listdir("/")
                if re.match(r"^volume[0-9]+$", name)
                and os.path.isdir(os.path.join("/", name))
            )
        except OSError:
            names = []
        parent = None
    else:
        try:
            names = sorted(
                name for name in os.listdir(path)
                if os.path.isdir(os.path.join(path, name))
                and not name.startswith(".")
                and not name.startswith("@")
            )
        except OSError:
            names = []
        parent = os.path.dirname(path.rstrip("/")) or "/"

    rows = []
    for name in names:
        child = os.path.join(path, name) if path != "/" else os.path.join("/", name)
        # Folder names are the user's own text and are never translated.
        label = '<span translate="no">{}/</span>'.format(html.escape(name))
        if path != "/" and not os.access(child, os.W_OK | os.X_OK):
            label += ' <span class="browse-denied">no write access</span>'
        rows.append(
            '<div style="margin:6px 0;">'
            '<a class="button secondary" style="width:100%;box-sizing:border-box;text-align:left;" '
            'href="./browse?path={}&target={}">{}</a>'
            '</div>'.format(
                quote(child, safe=""),
                quote(target, safe=""),
                label
            )
        )

    if not rows:
        rows.append('<p>No accessible directories.</p>')

    parent_html = ""
    if parent is not None:
        parent_html = '<a class="button secondary" href="./browse?path={}&target={}">..</a>'.format(
            quote(parent, safe=""),
            quote(target, safe="")
        )

    if target not in BROWSE_TARGETS:
        target = "data_dir"
    select_href = "./settings?{}={}".format(target, quote(path, safe=""))
    page_title = BROWSE_TARGETS[target]

    # A folder the package user cannot write to is chosen anyway only by
    # mistake: say so here, before Save, and name the shared folder to fix.
    warning = ""
    if path != "/" and not os.access(path, os.W_OK | os.X_OK):
        warning = '<div class="notice">{}{}{}{}{}</div>'.format(
            NOT_WRITABLE_A,
            '<span translate="no">{}</span>'.format(html.escape(path)),
            NOT_WRITABLE_B,
            '<span translate="no">{}</span>'.format(html.escape(share_of(path))),
            NOT_WRITABLE_C,
        )

    body = page_header(page_title)
    body += """
<div class="card">
<h1>{}</h1>
<p><b>Current:</b> <code>{}</code></p>
{warning}
<div style="margin-bottom:15px;">
{}
</div>
<div style="margin-bottom:15px;">
<a class="button" href="{}">Select this directory</a>
</div>
<div>
{}
</div>
</div>
""".format(
        page_title,
        html.escape(path),
        parent_html,
        select_href,
        "".join(rows),
        warning=warning,
    )

    body += page_footer()
    return localize_html(body)


def log_options():
    """The log and its rotated copies that exist (the log itself always)."""
    names = [
        name for name, path in LOG_FILES.items()
        if name == "Silo.log" or os.path.isfile(path)
    ]
    return "\n        ".join('<option value="{0}">{0}</option>'.format(name) for name in names)


def logs_page():
    body = page_header("TorrServer Silo Logs")

    body += """
<div class="app-shell">

{sidebar}

<div class="app-content">

<div class="app-title">Logs</div>

<div class="logs-card">

<div class="logs-toolbar">
    <select id="logSelect">
        {log_options}
    </select>

    <button type="button" onclick="openLog()">↻ Refresh</button>

    <a class="button secondary"
       id="downloadButton"
       href="./download-log?name=Silo.log">
        ↓ Download
    </a>
</div>

<pre id="logContent" class="logs-output">Loading Silo.log...</pre>

</div>
</div>
</div>

<script>
function openLog() {{
    var name = document.getElementById("logSelect").value;
    document.getElementById("logContent").textContent = "Loading " + name + "...";

    var basePath = window.location.pathname.substring(
        0,
        window.location.pathname.lastIndexOf("/") + 1
    );

    fetch(basePath + "read-log?name=" + encodeURIComponent(name))
        .then(function(response) {{
            if (!response.ok) {{
                throw new Error("HTTP " + response.status);
            }}
            return response.text();
        }})
        .then(function(data) {{
            document.getElementById("logContent").textContent = data;
            document.getElementById("downloadButton").href =
                basePath + "download-log?name=" + encodeURIComponent(name);
        }})
        .catch(function(error) {{
            document.getElementById("logContent").textContent =
                "Unable to read log: " + error;
        }});
}}

document.getElementById("logSelect").addEventListener("change", openLog);
window.addEventListener("load", openLog);
</script>
""".format(sidebar=app_sidebar("logs"), log_options=log_options())

    body += page_footer()
    return localize_html(body)

class Handler(BaseHTTPRequestHandler):

    def send_security_headers(self):
        # Pages hold settings: never cache them, and only DSM itself (same
        # origin) may frame them.
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "frame-ancestors 'self'")

    def send_html(self, content, status=200):
        data = content.encode("utf-8")

        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_security_headers()
        self.end_headers()

        self.wfile.write(data)

    def send_text(self, content, status=200):
        data = content.encode("utf-8")

        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_security_headers()
        self.end_headers()

        self.wfile.write(data)

    def redirect(self, location, extra_headers=()):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        for name, value in extra_headers:
            self.send_header(name, value)
        self.send_security_headers()
        self.end_headers()

    def client_ip(self):
        # nginx overwrites X-Real-IP with the real peer address.
        value = (self.headers.get("X-Real-IP", "") or "").strip()
        return value if re.match(r"^[0-9A-Fa-f:.]{3,45}$", value) else self.client_address[0]

    def cookie_value(self, name):
        for part in (self.headers.get("Cookie", "") or "").split(";"):
            key, _, value = part.strip().partition("=")
            if key == name:
                return value
        return ""

    def send_message_page(self, title, text, status):
        """A tiny page with a title and one sentence, translated like the rest."""
        page = (
            "<!DOCTYPE html><html><head><meta charset=\"utf-8\"><title>{title}</title></head>"
            "<body style=\"font-family:Arial,sans-serif;margin:40px\">"
            "<h2>{title}</h2><p>{text}</p></body></html>"
        ).format(title=html.escape(title), text=html.escape(text))
        self.send_html(localize_html(page), status)

    def send_denied(self):
        self.send_message_page(
            "Access denied",
            "Sign in to DSM as an administrator and open the TorrServer Silo application.",
            403,
        )

    def authorize(self):
        """Only DSM administrators may use the helper. Returns True if allowed;
        otherwise the response has already been sent."""
        parsed = urlparse(self.path)
        query_token = parse_qs(parsed.query).get("SynoToken", [""])[0].strip()
        cookie_token = self.cookie_value(TOKEN_COOKIE)
        header_token = (self.headers.get("X-SYNO-TOKEN", "") or "").strip()

        token = header_token or query_token or cookie_token
        if token and not TOKEN_PATTERN.match(token):
            token = ""

        user, reason = check_dsm_session(
            self.headers.get("Cookie", "") or "",
            token,
            self.client_ip(),
            self.request_host(),
        )

        if not user:
            auth_log("denied {} {} from {}: {}".format(
                self.command, parsed.path, self.client_ip(), reason))
            self.send_denied()
            return False

        # The DSM desktop hands the token over in the URL once; keep it in a
        # cookie and redirect to the clean URL so it does not stay in the
        # address bar or in later links.
        if query_token and query_token == token and query_token != cookie_token \
                and self.command == "GET":
            flags = "; Path={}; HttpOnly; SameSite=Strict".format(TOKEN_COOKIE_PATH)
            if (self.headers.get("X-Forwarded-Proto", "") or "").lower() == "https":
                flags += "; Secure"
            self.redirect(
                "./" + parsed.path.lstrip("/"),
                extra_headers=(("Set-Cookie", "{}={}{}".format(TOKEN_COOKIE, query_token, flags)),),
            )
            return False

        return True

    def request_host(self):
        """Host name from the Host header, restricted to hostname characters
        so it is safe to embed in HTML."""
        raw = (self.headers.get("Host", "") or "").strip()

        if raw.startswith("["):
            name = raw.split("]", 1)[0] + "]"
        else:
            name = raw.split(":", 1)[0]

        if re.match(r"^[A-Za-z0-9._\-\[\]:]{1,255}$", name):
            return name

        return "localhost"

    def is_same_origin(self):
        """CSRF guard: a POST must come from a page served by this host.

        Browsers always send Origin (or at least Referer) on cross-site form
        posts, so a missing or foreign value is rejected.
        """
        expected = self.request_host().strip("[]").lower()

        for header in ("Origin", "Referer"):
            value = self.headers.get(header)
            if not value:
                continue

            try:
                actual = urlparse(value).hostname
            except ValueError:
                return False

            return bool(actual) and actual.lower() == expected

        return False

    def read_post_params(self):
        """Return parsed form parameters, or None after sending an error."""
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            self.send_text("Invalid Content-Length", 400)
            return None

        if length < 0:
            self.send_text("Invalid Content-Length", 400)
            return None

        if length > MAX_POST_BYTES:
            self.send_text("Request too large", 413)
            return None

        body = self.rfile.read(length).decode("utf-8", errors="replace")
        return parse_qs(body)



    def do_GET(self):
        # Probe of check_nginx_route(): answered without a DSM session and
        # without a log line, it only shows that nginx still reaches us.
        if urlparse(self.path).path == NGINX_PROBE:
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        if not self.authorize():
            return

        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path == "/":
            self.send_html(main_page(self.request_host()))
            return

        if path == "/server-state":
            self.send_text(get_status().lower())
            return

        if path == "/restart-status":
            self.send_text("wait" if restart_in_progress() else "ready")
            return

        if path == "/settings":
            overrides = {
                name: cache_browser_path(query.get(name, [""])[0])
                for name in BROWSE_TARGETS
                if query.get(name, [""])[0]
            }
            # The folder browser returns "/" for anything outside a volume.
            overrides = {k: v for k, v in overrides.items() if v != "/"}
            self.send_html(settings_page(overrides=overrides))
            return

        if path == "/browse":
            selected_path = query.get("path", ["/"])[0]
            target = query.get("target", ["data_dir"])[0]
            self.send_html(cache_browser_page(selected_path, target))
            return

        if path == "/logs":
            self.send_html(logs_page())
            return

        if path == "/read-log":
            name = query.get("name", [""])[0]
            if not get_log_path(name):
                self.send_text("Invalid log file", 400)
                return

            self.send_text(get_log_by_name(name))
            return

        if path == "/download-log":
            name = query.get("name", [""])[0]
            log_path = get_log_path(name)

            if not log_path:
                self.send_text("Invalid log file", 400)
                return

            try:
                with open(log_path, "rb") as f:
                    data = f.read()

                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Disposition", 'attachment; filename="{}"'.format(name))
                self.send_header("Content-Length", str(len(data)))
                self.send_security_headers()
                self.end_headers()
                self.wfile.write(data)

            except FileNotFoundError:
                self.send_text("Log file not found: {}".format(name), 404)
            except Exception as e:
                self.send_text("Unable to download log: {}".format(str(e)), 500)

            return

        self.send_html("Not Found", 404)

    def do_POST(self):
        if not self.authorize():
            return

        parsed = urlparse(self.path)
        path = parsed.path

        if not self.is_same_origin():
            self.send_message_page(
                "Forbidden",
                "Cross-site request rejected. Reload the page and try again.",
                403,
            )
            return

        params = self.read_post_params()
        if params is None:
            return

        if path == "/language":
            language = params.get("language", ["en"])[0]
            if set_language(language):
                self.redirect("./settings")
            else:
                self.send_html(settings_page("Invalid language"), 400)
            return

        if path == "/settings":
            ok, message = save_settings(params)

            if ok:
                self.redirect("./settings")
            else:
                self.send_html(settings_page(message), 400)

            return

        if path == "/vaapi-test":
            device = params.get("device", [""])[0]
            if device not in [d["path"] for d in get_dri_devices()]:
                ok, report = False, "Invalid device"
            else:
                ok, report = vaapi_test(device)
            VAAPI_RESULT.update({"text": report, "ok": ok, "device": device})
            print("helper: VAAPI test on {}: {}".format(device, "OK" if ok else "FAILED"),
                  file=sys.stderr, flush=True)
            self.redirect("./")
            return

        if path == "/restart":
            ok, message = restart_silo()

            if ok:
                self.redirect("./")
            else:
                self.send_html(
                    localize_html(
                        page_header("Restart Error")
                        + """
<div class="card">
<h1>Restart failed</h1>
<p>{}</p>
</div>
""".format(html.escape(message))
                        + page_footer()
                    ),
                    500,
                )

            return

        self.send_html("Not Found", 404)

    def log_message(self, format_string, *args):
        return


def log_rotation_loop():
    while True:
        rotate_log_if_needed()
        time.sleep(10)


# Silo plugins have no timers. The "TorrServer Import" plugin
# (plugins/torrserver-import) runs its automatic scan when this endpoint is
# called and decides itself whether its interval is over, so one call a
# minute is enough. A 404 (plugin not installed) or a stopped Silo is ignored.
PLUGIN_TICK_PATH = "/plugins/torrserver-import/api/tick"
PLUGIN_TICK_SECONDS = 60


def plugin_tick():
    """One wake-up call; returns the HTTP status, or None when Silo is
    stopped or could not be reached."""
    if not is_silo_running():
        return None
    url = "http://127.0.0.1:{}{}".format(get_settings()["port"], PLUGIN_TICK_PATH)
    request = urllib.request.Request(url, data=b"", method="POST")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            response.read()
            return response.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return None


# DSM's nginx reaches this Helper through a rule that DSM itself registers
# from conf/resource. It has been seen to disappear while the package kept
# running (the DSM window then shows Synology's "page not found"); the package
# runs without root and cannot restore it. So the Helper checks its own address
# through nginx and logs when the rule goes missing and when it is back, to
# find out what removes it. Without a DSM session the Helper answers 403; a
# 404 comes from nginx itself.
NGINX_ROUTE_PATH = "/webman/3rdparty/{}/helper/".format(PACKAGE_NAME)
NGINX_PROBE = "/nginx-probe"
SYNOINFO_FILES = ("/etc/synoinfo.conf", "/etc.defaults/synoinfo.conf")
NGINX_ROUTE_STATE = {"missing": None}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Report a redirect (DSM forcing HTTPS) instead of following it."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def dsm_http_port():
    for path in SYNOINFO_FILES:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    key, _, value = line.partition("=")
                    if key.strip() == "external_port_dsm_http":
                        value = value.strip().strip('"')
                        if value.isdigit():
                            return int(value)
        except OSError:
            continue
    return 5000


def nginx_route_status():
    """HTTP status of the Helper address through DSM nginx, or None."""
    url = "http://127.0.0.1:{}{}{}".format(dsm_http_port(), NGINX_ROUTE_PATH, NGINX_PROBE.lstrip("/"))
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(url, timeout=10) as response:
            return response.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return None


def check_nginx_route():
    status = nginx_route_status()
    if status is None or status in (301, 302, 307, 308):
        return  # DSM not reachable over plain HTTP here: nothing to tell
    missing = status == 404
    if missing != NGINX_ROUTE_STATE["missing"]:
        if missing:
            helper_log("Warning: DSM nginx no longer knows the Helper address {} "
                       "(HTTP 404), the package window shows \"page not found\". "
                       "Reinstall the package over itself or run "
                       "\"sudo systemctl restart nginx\".".format(NGINX_ROUTE_PATH))
        elif NGINX_ROUTE_STATE["missing"]:
            helper_log("DSM nginx knows the Helper address again (HTTP {})".format(status))
        NGINX_ROUTE_STATE["missing"] = missing


def helper_log(message):
    try:
        with open(SILO_LOG, "a", encoding="utf-8") as f:
            f.write("{} helper: {}\n".format(time.strftime("%Y/%m/%d %H:%M:%S UTC0", time.gmtime()), message))
    except OSError:
        pass


def plugin_tick_loop():
    while True:
        time.sleep(PLUGIN_TICK_SECONDS)
        plugin_tick()
        check_nginx_route()


def run():
    # Plain HTTP on loopback only. DSM nginx terminates TLS and proxies
    # /webman/3rdparty/TorrServerSilo/helper/ here, so the browser never talks
    # to this port directly (no mixed content, no unauthenticated LAN exposure).
    server = ThreadingHTTPServer((HOST, HELPER_PORT), Handler)
    server.daemon_threads = True

    threading.Thread(target=log_rotation_loop, daemon=True).start()
    threading.Thread(target=plugin_tick_loop, daemon=True).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    run()

