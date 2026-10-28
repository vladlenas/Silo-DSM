#!/usr/bin/env python3
"""The "What's new" line for Package Center, from CHANGELOG.md.

    tools/changelog_info.py CHANGELOG.md <version>

Prints the entry of <version> as one line in the style of the SynoCommunity
packages: "1. Silo is updated to ... 2. Plex is built in again 3. ...".
The opening paragraph of the entry and every bullet give one item each: the
bold part of a bullet, otherwise its first sentence. Nothing is printed when
the version has no entry.
"""
import re
import sys

MAX_ITEM = 90
MAX_TOTAL = 600


def clean(text):
    text = re.sub(r"\*\*|`", "", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text.replace('"', "'")


def first_sentence(text):
    text = clean(text)
    m = re.match(r"(.+?)(?:[.:;](?:\s|$)| \(|$)", text)
    item = m.group(1) if m else text
    if len(item) > MAX_ITEM:
        item = item[:MAX_ITEM - 1].rsplit(" ", 1)[0] + "…"
    return item.rstrip(" .:")


def entry(lines, version):
    out, inside = [], False
    for line in lines:
        if line.startswith("## "):
            if inside:
                break
            inside = line[3:].split()[0] == version
            continue
        if inside:
            out.append(line.rstrip("\n"))
    return out


def items(lines):
    result, para, bullet = [], [], None

    def flush_bullet():
        if bullet is not None:
            # "**Helper → Logs → Clear:** empties ..." -> title and what it does
            m = re.match(r"\*\*(.+?):?\*\*:?\s*(.*)", bullet)
            if m and (bullet[:m.end(1) + 3].rstrip("*").endswith(":") or m.group(2).startswith(":")):
                rest = first_sentence(m.group(2).lstrip(": "))
                result.append(clean(m.group(1)).rstrip(" :") + (": " + rest if rest else ""))
            else:
                result.append(first_sentence(bullet))

    for line in lines:
        if line.startswith("- "):
            flush_bullet()
            bullet = line[2:]
        elif bullet is not None and line.startswith("  ") and line.strip():
            bullet += " " + line.strip()
        elif line.startswith("#"):
            flush_bullet()
            bullet = None
        elif line.strip() and bullet is None and not result:
            para.append(line.strip())
        elif not line.strip():
            flush_bullet()
            bullet = None
            if para and not result:
                result.append(first_sentence(" ".join(para)))
                para = []
    flush_bullet()
    if para and not result:
        result.append(first_sentence(" ".join(para)))
    return [i for i in result if i]


def summary(text, version):
    found = items(entry(text.splitlines(), version))
    line = " ".join("{}. {}".format(n, item) for n, item in enumerate(found, 1))
    if len(line) > MAX_TOTAL:
        line = line[:MAX_TOTAL - 1].rsplit(" ", 1)[0] + "…"
    return line


def main(argv):
    if len(argv) != 3:
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
        return 2
    try:
        with open(argv[1], encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return 0
    line = summary(text, argv[2])
    if line:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
