"""overlay/apply.py against a small stand-in for the Silo source tree."""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "overlay"))
sys.path.insert(0, os.path.join(ROOT, "tests"))

import apply  # noqa: E402
import silo_fixture  # noqa: E402


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class OverlayTests(unittest.TestCase):
    def setUp(self):
        self.src = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.src)
        silo_fixture.make(self.src)

    def git_commit(self):
        run = lambda *a: subprocess.check_call(["git", "-C", self.src] + list(a),
                                               stdout=subprocess.DEVNULL)
        run("init", "-q")
        run("add", "-A")
        run("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "silo")

    def apply(self):
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = apply.main(["apply.py", self.src])
        return code, err.getvalue()

    def path(self, rel):
        return os.path.join(self.src, rel)

    def test_applies_the_look(self):
        self.assertEqual(self.apply(), (0, ""))
        css = read(self.path("internal/web/static/css/style.css"))
        self.assertIn("--silo-bg: #14171a", css)            # upstream kept
        self.assertIn("--silo-bg: #f1f5f9", css)            # light tokens after it
        self.assertLess(css.index("#14171a"), css.index("#f1f5f9"))

        ui = "internal/plugin/default_ui/"
        self.assertIn('src="js/dsm.js"', read(self.path(ui + "index.html")))
        self.assertTrue(os.path.exists(self.path(ui + "css/dsm.css")))
        self.assertTrue(os.path.exists(self.path(ui + "img/ico-movie.svg")))
        self.assertEqual(read(self.path(ui + "js/app.js")), "// upstream app.js\n")  # logic untouched

        ru = json.loads(read(self.path(ui + "i18n/ru.json")))
        self.assertEqual(ru["library"], "Библиотека")
        self.assertEqual(ru["dsm_search"], "Поиск")

        self.assertIn("<h1>TorrServer Silo</h1>", read(self.path("internal/web/static/login.html")))
        self.assertIn("TorrServer Silo</span>", read(self.path("internal/plugin/admin_ui/index.html")))
        self.assertNotEqual(read(self.path("internal/web/static/img/favicon.svg")), "upstream image\n")
        manifest = json.loads(read(self.path("internal/web/static/img/site.webmanifest")))
        self.assertEqual(manifest["theme_color"], "#f1f5f9")

    def test_twice_is_once(self):
        self.git_commit()
        self.assertEqual(self.apply()[0], 0)
        first = read(self.path("internal/web/static/css/style.css"))
        self.assertEqual(self.apply()[0], 0)
        self.assertEqual(read(self.path("internal/web/static/css/style.css")), first)
        self.assertEqual(first.count("Silo DSM: light DSM look"), 1)

    def test_new_element_id_upstream_stops_the_build(self):
        with open(self.path("internal/plugin/default_ui/js/info.js"), "a") as f:
            f.write("document.getElementById('brand-new-panel');\n")
        code, err = self.apply()
        self.assertEqual(code, 1)
        self.assertIn("brand-new-panel", err)

    def test_new_script_upstream_stops_the_build(self):
        with open(self.path("internal/plugin/default_ui/js/player.js"), "w") as f:
            f.write("// new upstream script\n")
        code, err = self.apply()
        self.assertEqual(code, 1)
        self.assertIn("player.js", err)

    def test_changed_branding_stops_the_build(self):
        with open(self.path("internal/web/static/login.html"), "w") as f:
            f.write("<h1>Silo</h1>\n")
        code, err = self.apply()
        self.assertEqual(code, 1)
        self.assertIn("login.html", err)

    def test_moved_file_stops_the_build(self):
        os.remove(self.path("internal/web/static/css/style.css"))
        code, err = self.apply()
        self.assertEqual(code, 1)
        self.assertIn("style.css", err)


if __name__ == "__main__":
    unittest.main()
