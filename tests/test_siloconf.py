"""settings.json -> config.yaml.

Run:  python3 -m unittest discover -s tests -v
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
def slurp(path):
    with open(path, encoding="utf-8") as f:
        return f.read()

SILOCONF = os.path.join(ROOT, "src", "helper", "siloconf.py")

spec = importlib.util.spec_from_file_location("siloconf_under_test", SILOCONF)
sc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sc)


def parse_config(text):
    """Reads the YAML this generator writes: two levels, every value is a
    JSON scalar. A line that does not fit fails the test."""
    result, section = {}, None
    for line in text.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        if not line.startswith(" "):
            assert line.endswith(":"), line
            section = result.setdefault(line[:-1], {})
            continue
        key, _, value = line.strip().partition(": ")
        section[key] = json.loads(value)
    return result


class Base(unittest.TestCase):
    def setUp(self):
        self.var = tempfile.mkdtemp(prefix="siloconf-")
        self.addCleanup(shutil.rmtree, self.var, True)


class NormalizeTests(Base):
    def test_defaults_are_complete_and_valid(self):
        self.assertEqual(sc.normalize({}), sc.DEFAULTS)
        self.assertEqual(sc.DEFAULTS["port"], 8095)

    def test_numbers_from_a_form_are_accepted(self):
        result = sc.normalize({"port": "8100", "ffmpeg_threads": " 4 "})
        self.assertEqual((result["port"], result["ffmpeg_threads"]), (8100, 4))

    def test_invalid_values_are_rejected(self):
        for raw in (
            {"port": "80"}, {"port": "70000"}, {"port": "8x"}, {"port": True},
            {"ffmpeg_max_sessions": "-1"}, {"log_level": "trace"}, {"ffmpeg_hwaccel": "cuda"},
            {"ffmpeg_hw_device": "/etc/passwd"}, {"data_dir": "/etc"},
            {"data_dir": "/volume1/../etc"}, {"owner_password": "a\nb"},
            {"owner_password": "x" * 129},
        ):
            with self.subTest(raw=raw), self.assertRaises(sc.SettingsError):
                sc.normalize(raw)

    def test_helper_port_is_reserved(self):
        with self.assertRaises(sc.SettingsError):
            sc.normalize({"port": sc.HELPER_PORT})

    def test_folders_lose_a_trailing_slash(self):
        self.assertEqual(sc.normalize({"data_dir": "/volume1/silo/"})["data_dir"], "/volume1/silo")

    def test_unknown_keys_are_dropped(self):
        self.assertNotIn("evil", sc.normalize({"evil": 1}))


class StorageTests(Base):
    def test_missing_or_broken_file_gives_defaults(self):
        self.assertEqual(sc.load(self.var), sc.DEFAULTS)
        with open(sc.settings_path(self.var), "w") as f:
            f.write("{broken")
        self.assertEqual(sc.load(self.var), sc.DEFAULTS)

    def test_a_bad_value_edited_by_hand_falls_back_alone(self):
        with open(sc.settings_path(self.var), "w") as f:
            json.dump({"port": 1, "ffmpeg_threads": 3}, f)
        loaded = sc.load(self.var)
        self.assertEqual(loaded["port"], 8095)
        self.assertEqual(loaded["ffmpeg_threads"], 3)

    def test_save_is_private(self):
        sc.save({"owner_password": "secret"}, self.var)
        self.assertEqual(os.stat(sc.settings_path(self.var)).st_mode & 0o777, 0o600)


class YamlTests(Base):
    def test_config_has_every_section_silo_reads(self):
        config = parse_config(sc.render_yaml(sc.DEFAULTS, self.var))
        self.assertEqual(
            sorted(config), ["auth", "ffmpeg", "logging", "plugins", "server", "storage"])
        self.assertEqual(config["server"], {"host": "0.0.0.0", "port": 8095})
        self.assertEqual(config["storage"]["data_dir"], os.path.join(self.var, "data"))
        self.assertEqual(config["plugins"]["dir"], os.path.join(self.var, "plugins"))

    def test_no_torrent_section(self):
        # Silo.1 does not read it; the engine is set up in Silo itself.
        self.assertNotIn("torrent:", sc.render_yaml(sc.DEFAULTS, self.var))

    def test_old_settings_with_torrent_keys_still_load(self):
        with open(sc.settings_path(self.var), "w") as f:
            json.dump({"port": 8100, "cache_size_mb": 512, "peers_limit": 300}, f)
        loaded = sc.load(self.var)
        self.assertEqual(loaded["port"], 8100)
        self.assertNotIn("cache_size_mb", loaded)

    def test_ffprobe_gets_time_for_a_waking_torrent(self):
        config = parse_config(sc.render_yaml(sc.DEFAULTS, self.var))
        self.assertEqual(int(config["ffmpeg"]["probe_timeout_sec"]), 90)

    def test_log_goes_to_stdout_only(self):
        # Silo prints every line to stdout too; a file would double the log.
        config = parse_config(sc.render_yaml(sc.DEFAULTS, self.var))
        self.assertEqual(config["logging"]["file"], "")

    def test_strings_are_quoted_safely(self):
        tricky = 'a"b: #c \\ d\'e ü'
        config = parse_config(sc.render_yaml(dict(sc.DEFAULTS, owner_password=tricky), self.var))
        self.assertEqual(config["auth"]["owner_password"], tricky)

    def test_booleans_are_yaml_booleans(self):
        text = sc.render_yaml(dict(sc.DEFAULTS, ffmpeg_enabled=False), self.var)
        self.assertIn("  enabled: false\n", text)

    def test_real_yaml_parser_agrees(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML not installed")
        settings = dict(sc.DEFAULTS, owner_password='p: "#x', data_dir="/volume1/silo data")
        text = sc.render_yaml(settings, self.var)
        self.assertEqual(yaml.safe_load(text), parse_config(text))

    def test_write_config_creates_folders_and_a_private_file(self):
        problems = sc.write_config(self.var)
        self.assertEqual(problems, [])
        self.assertTrue(os.path.isdir(os.path.join(self.var, "data")))
        self.assertTrue(os.path.isdir(os.path.join(self.var, "plugins")))
        self.assertEqual(os.stat(sc.config_path(self.var)).st_mode & 0o777, 0o600)


class WizardTests(Base):
    def test_wizard_answers(self):
        result = sc.from_wizard({
            "wizard_port": "8099",
            "wizard_owner_password": "pw",
            "wizard_ffmpeg_enabled": "false",
            "wizard_hwaccel_none": "false",
            "wizard_hwaccel_vaapi": "true",
            "wizard_hwaccel_auto": "false",
        })
        self.assertEqual(result["port"], 8099)
        self.assertEqual(result["owner_password"], "pw")
        self.assertFalse(result["ffmpeg_enabled"])
        self.assertEqual(result["ffmpeg_hwaccel"], "vaapi")

    def test_no_wizard_gives_defaults(self):
        self.assertEqual(sc.from_wizard({}), sc.DEFAULTS)

    def run_cli(self, *args, **env):
        environment = dict(os.environ, SILO_DSM_VAR=self.var, **env)
        return subprocess.run(
            [sys.executable, SILOCONF] + list(args), env=environment,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)

    def test_init_uses_the_wizard_only_once(self):
        self.assertEqual(self.run_cli("init", wizard_port="8101").returncode, 0)
        self.assertEqual(sc.load(self.var)["port"], 8101)
        # An upgrade runs postinst again, without wizard answers.
        self.assertEqual(self.run_cli("init").returncode, 0)
        self.assertEqual(sc.load(self.var)["port"], 8101)
        self.assertIn("port: 8101", slurp(sc.config_path(self.var)))

    def test_invalid_wizard_port_fails_with_a_message(self):
        result = self.run_cli("init", wizard_port="42778")
        self.assertEqual(result.returncode, 1)
        self.assertIn("reserved", result.stdout)

    def test_reset(self):
        self.run_cli("init", wizard_port="8101")
        self.assertEqual(self.run_cli("reset").returncode, 0)
        self.assertEqual(sc.load(self.var)["port"], 8095)


class WizardFileTests(unittest.TestCase):
    def test_every_wizard_key_is_read(self):
        with open(os.path.join(ROOT, "src", "WIZARD_UIFILES", "install_uifile")) as f:
            steps = json.load(f)
        keys = {sub["key"] for step in steps for item in step["items"] for sub in item["subitems"]}
        source = slurp(SILOCONF)
        for key in keys:
            if key.startswith("wizard_hwaccel_"):
                self.assertIn(key[len("wizard_hwaccel_"):], sc.HWACCEL_METHODS)
            else:
                self.assertIn('"{}"'.format(key), source)


if __name__ == "__main__":
    unittest.main()
