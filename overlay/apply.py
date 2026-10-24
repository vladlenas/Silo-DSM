#!/usr/bin/env python3
"""Give Silo the DSM look before it is compiled.

    overlay/apply.py <silo checkout> [<overlay dir>]

Silo embeds its web pages and built-in plugins in the binary, so they are
added to the source tree right before `go build`:

* DSM Style UI (overlay/dsm_ui) becomes a built-in theme next to Silo's own
  Default Web UI, listed first so it is the main page. Its js/ is a copy of
  the Default Web UI scripts plus js/dsm.js, so the library works exactly as
  upstream wrote it. Default Web UI itself is not touched: "Make theme" on it
  in the admin panel brings Silo's own look back.
* TorrServer Import, Transcoding Profiles and Plex (plugins/) become
  built-in plugins.
* internal/web/static/css/style.css (admin panel, login page, plugin pages)
  gets overlay/static/dsm-core.css appended. It only applies under
  html.silo-dsm, which /js/silo-look.js sets while DSM Style UI is the theme.
  Silo's own logo stays on every page (the package icon is for DSM only).
  With Silo's own theme every page looks exactly as upstream made it.
* Fixes for Silo bugs that upstream has not fixed yet (core_fixes below):
  each one is a small, exact change; when upstream changes those lines the
  build stops, and the fix is checked against the new code or dropped.

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
REPO = os.path.dirname(HERE)
LOOK_SCRIPT = '<script src="/js/silo-look.js"></script>'


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

    # New file next to Silo's own: the theme check.
    for sub, name in (("js", "silo-look.js"),):
        target = os.path.join(need(os.path.join(src, "internal/web/static", sub)), name)
        if os.path.exists(target):
            raise OverlayError("Silo already has internal/web/static/{}/{}".format(sub, name))
        shutil.copyfile(os.path.join(overlay, "static", sub, name), target)


def look_script(src):
    """/js/silo-look.js in <head> of the admin panel and the login page."""
    for page in ("internal/web/static/login.html", "internal/plugin/admin_ui/index.html"):
        replace_once(os.path.join(src, page), '<script src="/i18n.js"></script>',
                     LOOK_SCRIPT + '\n    <script src="/i18n.js"></script>')


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


def dsm_ui(src, overlay):
    plugins = need(os.path.join(src, "internal/plugin"))
    ui = need(os.path.join(plugins, "default_ui"))
    js_dir = need(os.path.join(ui, "js"))
    theme = os.path.join(overlay, "dsm_ui")
    need(os.path.join(ui, "i18n"))

    page = read(os.path.join(theme, "index.html"))

    # Every script of the Default Web UI must be on the new page, and every
    # script on the page must exist after the copy.
    upstream_js = sorted(n for n in os.listdir(js_dir) if n.endswith(".js") and not n.endswith("_test.js"))
    on_page = re.findall(r'<script src="js/([\w.-]+)"', page)
    own_js = sorted(os.listdir(os.path.join(theme, "js")))
    missing_js = [n for n in upstream_js if n not in on_page]
    if missing_js:
        raise OverlayError("Default Web UI scripts not loaded by overlay/dsm_ui/index.html: "
                           + ", ".join(missing_js))
    unknown_js = [n for n in on_page if n not in upstream_js and n not in own_js]
    if unknown_js:
        raise OverlayError("overlay/dsm_ui/index.html loads scripts Silo does not have: "
                           + ", ".join(unknown_js))

    page_ids = set(re.findall(r'\bid="([\w-]+)"', page))
    missing_ids = sorted(element_ids_used(js_dir) - page_ids)
    if missing_ids:
        raise OverlayError("elements the Default Web UI scripts need are missing in "
                           "overlay/dsm_ui/index.html: " + ", ".join(missing_ids))

    target = os.path.join(plugins, "dsm_ui")
    if os.path.exists(target):
        raise OverlayError("Silo already has internal/plugin/dsm_ui")
    os.makedirs(target)
    for sub in ("js", "img", "i18n"):
        if os.path.isdir(os.path.join(ui, sub)):
            copy_tree(os.path.join(ui, sub), os.path.join(target, sub))
    for name in os.listdir(os.path.join(target, "js")):
        if name.endswith("_test.js"):
            os.remove(os.path.join(target, "js", name))

    for sub in ("css", "js", "img"):
        copy_tree(os.path.join(theme, sub), os.path.join(target, sub))
    for name in ("index.html", "info.yaml", "service.js"):
        shutil.copyfile(os.path.join(theme, name), os.path.join(target, name))

    # Own words next to the ones of the Default Web UI (also when that one is
    # switched off, its dictionary is not loaded).
    for name in sorted(os.listdir(os.path.join(theme, "i18n"))):
        dest = os.path.join(target, "i18n", name)
        data = json.loads(read(dest)) if os.path.exists(dest) else {}
        data.update(json.loads(read(os.path.join(theme, "i18n", name))))
        write(dest, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


# Plugins of this repository that are compiled into Silo:
# (folder in plugins/, folder in internal/plugin/, Go variable)
BUILTIN_PLUGINS = (
    ("torrserver-import", "torrserver_import", "torrserverImportFiles"),
    ("profile-editor", "profile_editor", "profileEditorFiles"),
    ("plex-scan", "plex_scan", "plexScanFiles"),
)


def builtin_plugins(src, repo):
    plugins = need(os.path.join(src, "internal/plugin"))
    for name, folder, _ in BUILTIN_PLUGINS:
        target = os.path.join(plugins, folder)
        if os.path.exists(target):
            raise OverlayError("Silo already has internal/plugin/" + folder)
        copy_tree(os.path.join(repo, "plugins", name), target)


BUILTIN_EMBED = """//go:embed all:admin_ui
var adminUIFiles embed.FS
"""
BUILTIN_SOURCES = """		{defaultUIFiles, "default_ui"},
		{adminUIFiles, "admin_ui"},
"""


def register_builtins(src):
    """DSM Style UI first (the main page on a new install), Silo's own plugins
    after it, then the plugins of this repository."""
    go = os.path.join(src, "internal/plugin/builtin.go")
    embeds = """
// Added by Silo DSM.
//go:embed all:dsm_ui
var dsmUIFiles embed.FS
"""
    sources = ""
    for _, folder, var in BUILTIN_PLUGINS:
        embeds += """
// Added by Silo DSM.
//go:embed all:{}
var {} embed.FS
""".format(folder, var)
        sources += '\t\t{{{}, "{}"}},\n'.format(var, folder)
    replace_once(go, BUILTIN_EMBED, BUILTIN_EMBED + embeds)
    replace_once(go, BUILTIN_SOURCES, '\t\t{dsmUIFiles, "dsm_ui"},\n' + BUILTIN_SOURCES + sources)


# (file, exact upstream text, replacement, why)
CORE_FIXES = (
    (
        "internal/web/server.go",
        'api := s.router.Group("/api", AuthMiddleware(s.userSvc))\n',
        'api := s.router.Group("/api", AuthMiddleware(s.userSvc), siloDSMValidHash())\n',
        "a short :hash made the torrent handlers panic",
    ),
)


def core_fixes(src, overlay):
    web = need(os.path.join(src, "internal/web"))
    target = os.path.join(web, "silo_dsm_fixes.go")
    if os.path.exists(target):
        raise OverlayError("Silo already has internal/web/silo_dsm_fixes.go")
    shutil.copyfile(os.path.join(overlay, "core", "silo_dsm_fixes.go"), target)
    for rel, old, new, _why in CORE_FIXES:
        replace_once(os.path.join(src, rel), old, new)


def main(argv):
    if len(argv) not in (2, 3):
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
        return 2
    src = argv[1]
    overlay = argv[2] if len(argv) == 3 else HERE
    try:
        reset(src)
        core_style(src, overlay)
        look_script(src)
        dsm_ui(src, overlay)
        builtin_plugins(src, REPO)
        register_builtins(src)
        core_fixes(src, overlay)
    except (OverlayError, OSError, ValueError, subprocess.CalledProcessError) as e:
        print("ERROR: DSM look: {}".format(e), file=sys.stderr)
        return 1
    print(">>> DSM look applied to " + src)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
