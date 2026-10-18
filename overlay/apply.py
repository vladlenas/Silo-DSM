#!/usr/bin/env python3
"""Give Silo the DSM look before it is compiled.

    overlay/apply.py <silo checkout> [<overlay dir>]

Silo embeds its web pages in the binary, so the look is changed in the source
tree, right before `go build`:

* internal/web/static/css/style.css (shared by the admin panel, the login page
  and plugin pages) gets overlay/static/dsm-core.css appended: light DSM
  colours instead of the dark ones.
* internal/web/static/img: the package icon instead of Silo's logo.
* internal/plugin/default_ui (the library page, Silo's built-in theme) gets
  the page, style, icons and js/dsm.js of overlay/default_ui. Its own scripts
  stay, so the library keeps working exactly as upstream wrote it.
* "SILO" in the admin panel and on the login page becomes "TorrServer Silo".

The tree is reset to the commit first, so running it twice is the same as
running it once. Anything that does not match what this script expects (a
file moved upstream, an element id the scripts need but the new page does not
have) stops the build with a message instead of producing a broken page.
"""
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


class OverlayError(Exception):
    pass


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def need(path):
    if not os.path.exists(path):
        raise OverlayError("missing in the Silo source: " + path)
    return path


def replace_once(path, old, new):
    text = read(need(path))
    if old not in text:
        raise OverlayError("{}: expected text not found: {!r}".format(path, old))
    write(path, text.replace(old, new))


def reset(src):
    if os.path.isdir(os.path.join(src, ".git")):
        subprocess.check_call(["git", "-C", src, "checkout", "-q", "--", "internal"])
        subprocess.check_call(["git", "-C", src, "clean", "-qfd", "--", "internal"])


def copy_tree(src_dir, dest_dir):
    for root, _, files in os.walk(src_dir):
        rel = os.path.relpath(root, src_dir)
        target = os.path.normpath(os.path.join(dest_dir, rel))
        os.makedirs(target, exist_ok=True)
        for name in files:
            shutil.copyfile(os.path.join(root, name), os.path.join(target, name))


def core_style(src, overlay):
    css = need(os.path.join(src, "internal/web/static/css/style.css"))
    write(css, read(css).rstrip("\n") + "\n\n" + read(os.path.join(overlay, "static/dsm-core.css")))

    img = need(os.path.join(src, "internal/web/static/img"))
    for name in sorted(os.listdir(os.path.join(overlay, "static/img"))):
        need(os.path.join(img, name))
        shutil.copyfile(os.path.join(overlay, "static/img", name), os.path.join(img, name))

    manifest = os.path.join(img, "site.webmanifest")
    data = json.loads(read(need(manifest)))
    data.update(name="TorrServer Silo", short_name="Silo",
                theme_color="#f1f5f9", background_color="#f1f5f9")
    write(manifest, json.dumps(data, indent=4) + "\n")


def branding(src):
    login = os.path.join(src, "internal/web/static/login.html")
    replace_once(login, "<h1>SILO</h1>", "<h1>TorrServer Silo</h1>")
    replace_once(login, "<title>TS Silo - Sign in</title>", "<title>TorrServer Silo</title>")

    admin = os.path.join(src, "internal/plugin/admin_ui/index.html")
    replace_once(admin, '<span class="brand-name">SILO</span>',
                 '<span class="brand-name">TorrServer Silo</span>')
    replace_once(admin, "<title>TS Silo Admin</title>", "<title>TorrServer Silo · Admin</title>")


def element_ids_used(js_dir):
    """Ids the Default Web UI scripts look up."""
    ids = set()
    for name in sorted(os.listdir(js_dir)):
        if not name.endswith(".js") or name.endswith("_test.js"):
            continue
        text = read(os.path.join(js_dir, name))
        ids.update(re.findall(r"getElementById\(\s*['\"]([\w-]+)['\"]\s*\)", text))
        ids.update(re.findall(r"querySelector(?:All)?\(\s*['\"]#([\w-]+)['\"]\s*\)", text))
    return ids


def default_ui(src, overlay):
    ui = need(os.path.join(src, "internal/plugin/default_ui"))
    js_dir = need(os.path.join(ui, "js"))
    theme = os.path.join(overlay, "default_ui")

    for name in ("index.html", "info.yaml", "i18n"):
        need(os.path.join(ui, name))

    page = read(os.path.join(theme, "index.html"))

    # Every script of the Default Web UI must be on the new page, and every
    # script on the page must exist after the copy.
    upstream_js = sorted(n for n in os.listdir(js_dir) if n.endswith(".js") and not n.endswith("_test.js"))
    on_page = re.findall(r'<script src="js/([\w.-]+)"', page)
    own_js = sorted(os.listdir(os.path.join(theme, "js")))
    missing_js = [n for n in upstream_js if n not in on_page]
    if missing_js:
        raise OverlayError("Default Web UI scripts not loaded by overlay/default_ui/index.html: "
                           + ", ".join(missing_js))
    unknown_js = [n for n in on_page if n not in upstream_js and n not in own_js]
    if unknown_js:
        raise OverlayError("overlay/default_ui/index.html loads scripts Silo does not have: "
                           + ", ".join(unknown_js))

    page_ids = set(re.findall(r'\bid="([\w-]+)"', page))
    missing_ids = sorted(element_ids_used(js_dir) - page_ids)
    if missing_ids:
        raise OverlayError("elements the Default Web UI scripts need are missing in "
                           "overlay/default_ui/index.html: " + ", ".join(missing_ids))

    for sub in ("css", "js", "img"):
        copy_tree(os.path.join(theme, sub), os.path.join(ui, sub))
    shutil.copyfile(os.path.join(theme, "index.html"), os.path.join(ui, "index.html"))

    # Own words go into the dictionaries of the Default Web UI.
    for name in sorted(os.listdir(os.path.join(theme, "i18n"))):
        target = os.path.join(ui, "i18n", name)
        data = json.loads(read(target)) if os.path.exists(target) else {}
        data.update(json.loads(read(os.path.join(theme, "i18n", name))))
        write(target, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def main(argv):
    if len(argv) not in (2, 3):
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
        return 2
    src = argv[1]
    overlay = argv[2] if len(argv) == 3 else HERE
    try:
        reset(src)
        core_style(src, overlay)
        branding(src)
        default_ui(src, overlay)
    except (OverlayError, OSError, ValueError, subprocess.CalledProcessError) as e:
        print("ERROR: DSM look: {}".format(e), file=sys.stderr)
        return 1
    print(">>> DSM look applied to " + src)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
