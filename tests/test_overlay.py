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
        self.upstream_default_ui = self.snapshot("internal/plugin/default_ui")

    def snapshot(self, rel):
        files = {}
        base = self.path(rel)
        for root, _, names in os.walk(base):
            for name in names:
                p = os.path.join(root, name)
                files[os.path.relpath(p, base)] = read(p)
        return files

    def git_commit(self):
        def run(*args):
            subprocess.check_call(["git", "-C", self.src] + list(args), stdout=subprocess.DEVNULL)
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

    def test_default_ui_stays_untouched(self):
        self.assertEqual(self.apply(), (0, ""))
        self.assertEqual(self.snapshot("internal/plugin/default_ui"), self.upstream_default_ui)

    def test_dsm_ui_is_a_builtin_theme_built_on_the_default_ui_scripts(self):
        self.assertEqual(self.apply(), (0, ""))
        ui = "internal/plugin/dsm_ui/"
        self.assertIn('id: "dsm-ui"', read(self.path(ui + "info.yaml")))
        self.assertIn("theme_ui: true", read(self.path(ui + "info.yaml")))
        self.assertIn('src="js/dsm.js"', read(self.path(ui + "index.html")))
        self.assertTrue(os.path.exists(self.path(ui + "service.js")))
        self.assertTrue(os.path.exists(self.path(ui + "css/dsm.css")))
        self.assertTrue(os.path.exists(self.path(ui + "img/ico-movie.svg")))
        # upstream scripts copied unchanged, test files left out
        self.assertEqual(read(self.path(ui + "js/app.js")), "// upstream app.js\n")
        self.assertFalse(os.path.exists(self.path(ui + "js/ui_test.js")))
        # Silo's own logo stays
        self.assertEqual(read(self.path(ui + "img/logo.png")), "upstream logo\n")
        ru = json.loads(read(self.path(ui + "i18n/ru.json")))
        self.assertEqual(ru["library"], "Библиотека")
        self.assertEqual(ru["dsm_search"], "Поиск")

    def test_builtins_registered_with_dsm_ui_first(self):
        self.assertEqual(self.apply(), (0, ""))
        go = read(self.path("internal/plugin/builtin.go"))
        self.assertIn("//go:embed all:dsm_ui", go)
        self.assertIn("//go:embed all:torrserver_import", go)
        self.assertIn("//go:embed all:profile_editor", go)
        order = [go.index('"dsm_ui"}'), go.index('"default_ui"}'),
                 go.index('"admin_ui"}'), go.index('"torrserver_import"}'),
                 go.index('"profile_editor"}'), go.index('"plex_scan"}')]
        self.assertIn("//go:embed all:plex_scan", go)
        self.assertEqual(order, sorted(order))

    def test_profile_editor_is_built_in(self):
        self.assertEqual(self.apply(), (0, ""))
        plugin = "internal/plugin/profile_editor/"
        self.assertIn('id: "profile-editor"', read(self.path(plugin + "info.yaml")))
        self.assertTrue(os.path.exists(self.path(plugin + "js/app.js")))

    def test_torrserver_import_is_built_in(self):
        self.assertEqual(self.apply(), (0, ""))
        plugin = "internal/plugin/torrserver_import/"
        self.assertIn('id: "torrserver-import"', read(self.path(plugin + "info.yaml")))
        self.assertTrue(os.path.exists(self.path(plugin + "service.js")))
        self.assertTrue(os.path.exists(self.path(plugin + "i18n/ru.json")))

    def test_light_style_only_under_the_dsm_class(self):
        self.assertEqual(self.apply(), (0, ""))
        css = read(self.path("internal/web/static/css/style.css"))
        self.assertTrue(css.startswith(":root { --silo-bg: #14171a; }"))  # upstream kept first
        added = css[css.index("Silo DSM: light DSM look"):]
        self.assertNotIn(":root", added.split("*/", 1)[1])
        self.assertIn("html.silo-dsm {", added)
        for line in added.splitlines():
            if line.rstrip().endswith("{") and not line.startswith("@media"):
                self.assertIn("html.silo-dsm", line)

        # The white box must not hide the "checked" state (it did in 0.8.1).
        self.assertNotIn('html.silo-dsm input[type="checkbox"] {', added)
        self.assertIn('html.silo-dsm input[type="checkbox"]:checked {', added)

        script = '<script src="/js/silo-look.js"></script>'
        self.assertTrue(os.path.exists(self.path("internal/web/static/js/silo-look.js")))
        for page in ("internal/web/static/login.html", "internal/plugin/admin_ui/index.html"):
            html = read(self.path(page))
            self.assertIn(script, html)
            self.assertLess(html.index(script), html.index("</head>"))

    def test_silo_branding_kept_for_its_own_theme(self):
        self.assertEqual(self.apply(), (0, ""))
        self.assertIn("<h1>SILO</h1>", read(self.path("internal/web/static/login.html")))
        self.assertIn(">SILO</span>", read(self.path("internal/plugin/admin_ui/index.html")))
        self.assertEqual(read(self.path("internal/web/static/img/favicon.svg")), "upstream image\n")
        # Silo's logo everywhere on the web, the package icon only in DSM
        self.assertFalse(os.path.exists(self.path("internal/web/static/img/dsm-logo.png")))
        self.assertNotIn("dsm-logo", read(self.path("internal/web/static/css/style.css")))
        self.assertNotIn("dsm-logo", read(self.path("internal/web/static/js/silo-look.js")))
        css = read(self.path("internal/web/static/css/style.css"))
        self.assertIn('content: "TorrServer Silo"', css)

    def test_core_fixes(self):
        self.assertEqual(self.apply(), (0, ""))
        server = read(self.path("internal/web/server.go"))
        self.assertIn("AuthMiddleware(s.userSvc), siloDSMValidHash())", server)
        self.assertIn("func siloDSMValidHash()", read(self.path("internal/web/silo_dsm_fixes.go")))
        # HLS fixes are upstream since 29497f8: nothing of ours in these files
        self.assertNotIn("siloDSM", read(self.path("internal/web/api_hls.go")))
        self.assertNotIn("siloDSM", read(self.path("internal/ffmpeg/job/throttle.go")))
        self.assertFalse(os.path.exists(self.path("internal/ffmpeg/job/silo_dsm_throttle.go")))

    def test_changed_server_code_stops_the_build(self):
        with open(self.path("internal/web/server.go"), "w") as f:
            f.write("package web\n")
        code, err = self.apply()
        self.assertEqual(code, 1)
        self.assertIn("server.go", err)

    def test_twice_is_once(self):
        self.git_commit()
        self.assertEqual(self.apply()[0], 0)
        first = {rel: read(self.path(rel)) for rel in (
            "internal/web/static/css/style.css", "internal/plugin/builtin.go",
            "internal/plugin/admin_ui/index.html", "internal/web/static/login.html")}
        self.assertEqual(self.apply()[0], 0)
        for rel, text in first.items():
            self.assertEqual(read(self.path(rel)), text, rel)
        self.assertEqual(first["internal/plugin/builtin.go"].count("all:dsm_ui"), 1)

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

    def test_changed_builtin_list_stops_the_build(self):
        with open(self.path("internal/plugin/builtin.go"), "w") as f:
            f.write("package plugin\n")
        code, err = self.apply()
        self.assertEqual(code, 1)
        self.assertIn("builtin.go", err)

    def test_changed_page_head_stops_the_build(self):
        with open(self.path("internal/web/static/login.html"), "w") as f:
            f.write("<html><head></head><body><h1>SILO</h1></body></html>\n")
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
