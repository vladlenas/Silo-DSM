#!/bin/sh
# Print the CHANGELOG.md entry for a version (without its heading).
# Usage: release-notes.sh <version> [CHANGELOG.md]
# Prints nothing (exit 1) when there is no entry for that version.
VERSION="$1"
FILE="${2:-CHANGELOG.md}"

[ -n "$VERSION" ] && [ -f "$FILE" ] || exit 1

awk -v v="$VERSION" '
    /^## / {
        if (found) exit
        if ($2 == v) { found = 1 }
        next
    }
    found { print }
' "$FILE" | sed -e '/./,$!d' | sed -e ':a' -e '/^[[:space:]]*$/{$d;N;ba' -e '}' > /tmp/release-notes.$$

if [ -s /tmp/release-notes.$$ ]; then
    cat /tmp/release-notes.$$
    rm -f /tmp/release-notes.$$
else
    rm -f /tmp/release-notes.$$
    exit 1
fi
