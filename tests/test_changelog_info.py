"""tools/changelog_info.py: the Package Center "What's new" line."""
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import changelog_info as ci  # noqa: E402

SAMPLE = """# Changelog

## 2.0.1 (2026-11-01)

Silo is updated to commit `abc1234`: more fixes.

### Added

- **Helper → Logs → Clear:** empties `Silo.log` and deletes its older
  copies; the log opens at its end.
- Plex is built in again (library menu → Plex).

### Fixed

- The "page not found" window after a restart. DSM reloads nginx now.

## 2.0.0 (2026-10-01)

- Older entry.
"""


class ChangelogInfoTests(unittest.TestCase):
    def test_items_of_the_version(self):
        self.assertEqual(
            ci.summary(SAMPLE, "2.0.1"),
            "1. Silo is updated to commit abc1234 "
            "2. Helper → Logs → Clear: empties Silo.log and deletes its older copies "
            "3. Plex is built in again "
            "4. The 'page not found' window after a restart")

    def test_other_and_missing_versions(self):
        self.assertEqual(ci.summary(SAMPLE, "2.0.0"), "1. Older entry")
        self.assertEqual(ci.summary(SAMPLE, "9.9.9"), "")

    def test_no_double_quotes_for_info(self):
        self.assertNotIn('"', ci.summary(SAMPLE, "2.0.1"))

    def test_info_has_the_line_of_the_current_version(self):
        with open(os.path.join(ROOT, "Makefile")) as f:
            version = [l.split(":=")[1].strip() for l in f if l.startswith("PKG_VERSION")][0]
        info = subprocess.check_output(
            ["bash", os.path.join(ROOT, "src", "INFO.sh"), version, "amd64", "1", "Silo.1-x"], text=True)
        lines = [l for l in info.splitlines() if l.startswith("changelog=")]
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith('changelog="1. '))


if __name__ == "__main__":
    unittest.main()
