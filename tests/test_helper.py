"""Helper: settings form, VAAPI check and translations.

Run:  python3 -m unittest discover -s tests -v
"""
import html
import importlib.util
import json
import os
import re
import shutil
import stat
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
def slurp(path):
    with open(path, encoding="utf-8") as f:
        return f.read()

HELPER_DIR = os.path.join(ROOT, "src", "helper")

VAR = tempfile.mkdtemp(prefix="helper-var-")
FFMPEG_DIR = tempfile.mkdtemp(prefix="helper-ffmpeg-")
DRI_DIR = tempfile.mkdtemp(prefix="helper-dri-")
os.environ.update(SILO_DSM_VAR=VAR, FFMPEG_DIR=FFMPEG_DIR, SILO_DSM_DRI=DRI_DIR)

spec = importlib.util.spec_from_file_location("helper_under_test", os.path.join(HELPER_DIR, "helper.py"))
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)

FAKE_FFMPEG = """#!/bin/sh
case "$*" in
  *-version*) echo "ffmpeg version 7.1.5 Copyright";;
  *-hwaccels*) printf 'Hardware acceleration methods:\\nvaapi\\n';;
  *h264_vaapi*) [ -n "$FAIL_ENCODE" ] && { echo "Error while opening encoder"; exit 1; }; exit 0;;
  *) exit 0;;
esac
"""

LANGS = ("ru", "uk", "lt", "pl")
# Names and values that stay as they are in every language.
NOT_TRANSLATED = {
    "CPU", "DSM", "FFmpeg 7", "Silo", "TorrServer Silo", "VAAPI (Intel)", "Silo.log",
    "English", "Русский", "Lietuvių", "Polski", "Українська",
}


def form(**values):
    """A full settings form post with the current values, changed by *values*."""
    current = h.get_settings()
    params = {name: [str(current[name])] for name in h.FORM_FIELDS}
    params["form"] = ["settings"]
    if current["ffmpeg_enabled"]:
        params["ffmpeg_enabled"] = ["1"]
    if current["owner_password"]:
        params["auth"] = ["1"]
        params["password"] = [h.PASSWORD_PLACEHOLDER]
    for key, value in values.items():
        if value is None:
            params.pop(key, None)
        else:
            params[key] = [str(value)]
    return params


class Base(unittest.TestCase):
    def setUp(self):
        for name in os.listdir(VAR):
            path = os.path.join(VAR, name)
            shutil.rmtree(path) if os.path.isdir(path) else os.unlink(path)
        h.load_locale = lambda language=None: {}
        h.is_port_in_use = lambda port, allowed_ports=None: False


class SaveSettingsTests(Base):
    def test_saving_writes_settings_and_config(self):
        ok, message = h.save_settings(form(port=8111, log_level="debug"))
        self.assertTrue(ok, message)
        self.assertEqual(h.get_settings()["port"], 8111)
        config = slurp(os.path.join(VAR, "config.yaml"))
        self.assertIn("port: 8111", config)
        self.assertIn('level: "debug"', config)

    def test_invalid_input_changes_nothing(self):
        h.save_settings(form(port=8111))
        ok, message = h.save_settings(form(port=h.HELPER_PORT))
        self.assertFalse(ok)
        self.assertIn("reserved", message)
        self.assertEqual(h.get_settings()["port"], 8111)

    def test_port_in_use_is_refused(self):
        h.is_port_in_use = lambda port, allowed_ports=None: port == 9000
        ok, message = h.save_settings(form(port=9000))
        self.assertFalse(ok)
        self.assertIn("already in use", message)

    def test_password_is_kept_while_the_placeholder_is_shown(self):
        h.save_settings(form(auth=1, password="secret"))
        ok, _ = h.save_settings(form())
        self.assertTrue(ok)
        self.assertEqual(h.get_settings()["owner_password"], "secret")

    def test_password_is_required_to_turn_auth_on(self):
        ok, message = h.save_settings(form(auth=1, password=h.PASSWORD_PLACEHOLDER))
        self.assertFalse(ok)
        self.assertEqual(message, "Password is required")

    def test_clearing_the_checkbox_removes_the_password(self):
        h.save_settings(form(auth=1, password="secret"))
        h.save_settings(form(auth=None, password=None))
        self.assertEqual(h.get_settings()["owner_password"], "")

    def test_checkboxes_off(self):
        ok, _ = h.save_settings(form(ffmpeg_enabled=None))
        self.assertTrue(ok)
        self.assertFalse(h.get_settings()["ffmpeg_enabled"])

    def test_a_partial_post_does_not_clear_checkboxes(self):
        h.save_settings(form(auth=1, password="secret"))
        ok, _ = h.save_settings({"port": ["8112"]})
        self.assertTrue(ok)
        settings = h.get_settings()
        self.assertEqual(settings["owner_password"], "secret")
        self.assertTrue(settings["ffmpeg_enabled"])

    def test_folder_outside_a_volume_is_refused(self):
        ok, message = h.save_settings(form(data_dir="/etc"))
        self.assertFalse(ok)
        self.assertIn("inside a volume", message)


class FolderTests(Base):
    def test_unwritable_folder_names_the_share(self):
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, True)
        os.chmod(root, 0o500)
        self.addCleanup(os.chmod, root, 0o700)
        message = h.prepare_folder(os.path.join(root, "silo"))
        if os.geteuid() == 0:
            self.assertEqual(message, "")    # root ignores permissions
        else:
            self.assertIn("TorrServerSilo", message)

    def test_share_of(self):
        self.assertEqual(h.share_of("/volume1/media/silo"), "media")


class VaapiTests(Base):
    def setUp(self):
        super().setUp()
        path = os.path.join(FFMPEG_DIR, "ffmpeg")
        with open(path, "w") as f:
            f.write(FAKE_FFMPEG)
        os.chmod(path, 0o755)
        self.device = os.path.join(DRI_DIR, "renderD128")
        open(self.device, "w").close()
        os.chmod(self.device, 0o660)
        os.environ.pop("FAIL_ENCODE", None)

    def test_ffmpeg_info(self):
        info = h.get_ffmpeg_info()
        self.assertEqual(info, {"found": True, "version": "7.1.5", "vaapi": True})

    def test_devices_are_listed_with_access(self):
        devices = h.get_dri_devices()
        self.assertEqual([d["path"] for d in devices], [self.device])
        self.assertTrue(devices[0]["access"])
        self.assertEqual(devices[0]["mode"], "660")

    def test_vaapi_ok(self):
        ok, report = h.vaapi_test(self.device)
        self.assertTrue(ok)
        self.assertIn("Device probe: OK", report)
        self.assertIn("H.264 encode: OK", report)

    def test_vaapi_encode_failure_is_reported(self):
        os.environ["FAIL_ENCODE"] = "1"
        ok, report = h.vaapi_test(self.device)
        self.assertFalse(ok)
        self.assertIn("H.264 encode: FAILED", report)
        self.assertIn("Error while opening encoder", report)

    def test_no_ffmpeg(self):
        os.unlink(os.path.join(FFMPEG_DIR, "ffmpeg"))
        self.assertFalse(h.get_ffmpeg_info()["found"])
        ok, _ = h.vaapi_test(self.device)
        self.assertFalse(ok)

    def test_status_page_shows_the_card(self):
        page = h.main_page("nas")
        self.assertIn("Test VAAPI", page)
        self.assertIn("renderD128", page)


def locale(lang):
    with open(os.path.join(HELPER_DIR, "locales", lang + ".json"), encoding="utf-8") as f:
        return json.load(f)


def visible_texts(page):
    body = re.sub(
        r'<style.*?</style>|<script.*?</script>'
        r'|<(pre|td|span|div|select)\b[^>]*translate="no"[^>]*>.*?</\1>',
        " ", page, flags=re.S)
    texts = (html.unescape(t).strip() for t in re.split(r"<[^>]+>", body))
    return {" ".join(t.split()) for t in texts if t}


class TranslationTests(Base):
    def test_every_language_has_the_same_keys(self):
        keys = set(locale("en"))
        for lang in LANGS:
            self.assertEqual(set(locale(lang)), keys, lang)

    def test_every_key_is_used(self):
        source = slurp(os.path.join(HELPER_DIR, "helper.py"))
        source += slurp(os.path.join(HELPER_DIR, "siloconf.py"))
        for key in locale("en"):
            self.assertIn(key.strip(), source, key)

    def test_pages_are_fully_translated(self):
        h.VAAPI_RESULT.update({"text": "x", "ok": True, "device": ""})
        self.addCleanup(h.VAAPI_RESULT.update, {"text": "", "ok": None, "device": ""})
        for lang in LANGS:
            translations = locale(lang)
            h.load_locale = lambda language=None, t=translations: t
            pages = [h.main_page("nas"), h.settings_page(), h.logs_page(),
                     h.cache_browser_page("/", "data_dir")]
            for page in pages:
                for text in visible_texts(page):
                    if text in NOT_TRANSLATED or not re.search(r"[A-Za-z]{3}", text):
                        continue
                    # System values read from the NAS (CPU name, memory).
                    if re.search(r"^[0-9.]+ / [0-9.]+ GB$|Intel|AMD|ARM|Processor|x86_64|aarch64", text):
                        continue
                    # An English key still on the page: its translation was not applied.
                    self.assertFalse(
                        text in translations and translations[text] != text,
                        "{}: '{}' is shown in English".format(lang, text))
                    # Cyrillic languages: any English text is a missing key.
                    if lang in ("ru", "uk"):
                        self.assertRegex(
                            text, r"[А-Яа-яЁёІіЇїЄєҐґ]",
                            "{}: '{}' has no translation".format(lang, text))


class HelperFileTests(unittest.TestCase):
    def test_helper_and_package_agree_on_names(self):
        nginx = slurp(os.path.join(ROOT, "src", "nginx", "Silo.conf"))
        self.assertIn("127.0.0.1:{}/".format(h.HELPER_PORT), nginx)
        self.assertIn(h.TOKEN_COOKIE_PATH, nginx)
        js = slurp(os.path.join(ROOT, "src", "ui", "Silo.js"))
        self.assertIn(h.TOKEN_COOKIE_PATH + "helper/", js)
        privilege = json.loads(slurp(os.path.join(ROOT, "src", "conf", "privilege")))
        self.assertEqual(privilege["username"], h.PACKAGE_NAME)

    def test_gpu_access_is_requested(self):
        resource = json.loads(slurp(os.path.join(ROOT, "src", "conf", "resource")))
        self.assertIn("video-driver", resource)

    def test_files_are_executable_in_the_repository(self):
        for name in ("helper.py", "siloconf.py"):
            mode = os.stat(os.path.join(HELPER_DIR, name)).st_mode
            self.assertTrue(mode & stat.S_IXUSR, name)


if __name__ == "__main__":
    unittest.main()
