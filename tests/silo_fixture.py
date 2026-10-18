#!/usr/bin/env python3
"""A tiny stand-in for the Silo source tree: just the files overlay/apply.py
touches, shaped like the real ones.

    python3 tests/silo_fixture.py <dir>
"""
import json
import os
import sys

DEFAULT_UI_SCRIPTS = ("utils.js", "categories.js", "cards.js", "add.js", "modals.js",
                      "cache.js", "info.js", "menu.js", "app.js")
STATIC_IMAGES = ("favicon-96x96.png", "apple-touch-icon.png", "web-app-manifest-192x192.png",
                 "web-app-manifest-512x512.png", "favicon.ico", "favicon.svg")


def put(root, rel, text):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def make(root):
    put(root, "internal/web/static/css/style.css", ":root { --silo-bg: #14171a; }\n")
    for name in STATIC_IMAGES:
        put(root, "internal/web/static/img/" + name, "upstream image\n")
    put(root, "internal/web/static/img/site.webmanifest",
        json.dumps({"name": "Silo", "theme_color": "#14171a"}) + "\n")
    put(root, "internal/web/static/login.html",
        "<html><head><title>TS Silo - Sign in</title></head>"
        "<body><div class=\"login-brand\"><h1>SILO</h1></div></body></html>\n")
    put(root, "internal/plugin/admin_ui/index.html",
        "<html><head><title>TS Silo Admin</title></head>"
        "<body><span class=\"brand-name\">SILO</span></body></html>\n")

    ui = "internal/plugin/default_ui/"
    put(root, ui + "index.html", "<html>upstream library page</html>\n")
    put(root, ui + "info.yaml", "id: \"default-ui\"\ntheme_ui: true\n")
    put(root, ui + "i18n/en.json", json.dumps({"library": "Library"}) + "\n")
    put(root, ui + "i18n/ru.json", json.dumps({"library": "Библиотека"}) + "\n")
    for name in DEFAULT_UI_SCRIPTS:
        put(root, ui + "js/" + name, "// upstream {}\n".format(name))
    # Element lookups like the real scripts do.
    put(root, ui + "js/cards.js", "document.getElementById('grid');\n")
    put(root, ui + "js/menu.js",
        "document.getElementById('plugins-menu');\ndocument.querySelector('#sidebar');\n")


if __name__ == "__main__":
    make(sys.argv[1])
