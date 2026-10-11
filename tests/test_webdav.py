"""WebDAV plugin (plugins/silo-webdav), run outside Silo by tests/webdav_harness.js.

Run:  python3 -m unittest discover -s tests -v   (needs node; skipped without)
"""
import base64
import http.client
import os
import shutil
import socket
import subprocess
import time
import unittest
import xml.etree.ElementTree as ET
from urllib.parse import quote, unquote

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HARNESS = os.path.join(ROOT, "tests", "webdav_harness.js")
BASE = "/plugins/silo-webdav/"
DAV = "{DAV:}"
MOVIE = BASE + quote("Фильмы/Movie (2026)/movie.mkv")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Harness:
    def __init__(self, password=""):
        self.port = free_port()
        args = ["node", HARNESS, str(self.port)] + ([password] if password else [])
        self.proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", self.port), 0.2).close()
                return
            except OSError:
                time.sleep(0.1)
        raise RuntimeError("harness did not start")

    def request(self, method, path, headers=None, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request(method, path, body=body, headers=dict({"Host": "127.0.0.1:%d" % self.port}, **(headers or {})))
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp, data

    def stop(self):
        self.proc.kill()
        self.proc.wait()
        self.proc.stdout.close()


def responses(data):
    """{decoded href: {prop: value}} from a multistatus body."""
    out = {}
    for r in ET.fromstring(data).findall(DAV + "response"):
        href = unquote(r.find(DAV + "href").text)
        prop = r.find(DAV + "propstat/" + DAV + "prop")
        values = {child.tag.replace(DAV, ""): (child.text or "") for child in prop}
        values["collection"] = prop.find(DAV + "resourcetype/" + DAV + "collection") is not None
        out[href] = values
    return out


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class OpenLibraryTests(unittest.TestCase):
    """Silo without an owner password: everyone is the owner."""

    @classmethod
    def setUpClass(cls):
        cls.h = Harness()

    @classmethod
    def tearDownClass(cls):
        cls.h.stop()

    def test_options_announces_dav(self):
        resp, _ = self.h.request("OPTIONS", BASE)
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.getheader("DAV"), "1")
        self.assertIn("PROPFIND", resp.getheader("Allow"))

    def test_root_lists_categories(self):
        resp, data = self.h.request("PROPFIND", BASE, {"Depth": "1"})
        self.assertEqual(resp.status, 207)
        self.assertTrue(resp.getheader("Content-Type").startswith("application/xml"))
        items = responses(data)
        self.assertEqual(sorted(items), [BASE, BASE + "Сериалы/", BASE + "Фильмы/"])
        self.assertTrue(all(v["collection"] for v in items.values()))

    def test_depth_zero_is_the_folder_only(self):
        _, data = self.h.request("PROPFIND", BASE + quote("Фильмы/"), {"Depth": "0"})
        self.assertEqual(list(responses(data)), [BASE + "Фильмы/"])

    def test_files_have_size_type_and_date(self):
        _, data = self.h.request("PROPFIND", BASE + quote("Фильмы/Movie (2026)/"), {"Depth": "1"})
        items = responses(data)
        movie = items[unquote(MOVIE)]
        self.assertFalse(movie["collection"])
        self.assertEqual(movie["getcontentlength"], "10000")
        self.assertEqual(movie["getcontenttype"], "video/x-matroska")
        self.assertEqual(movie["getlastmodified"], "Sat, 11 Oct 2025 10:53:20 GMT")
        self.assertIn(BASE + "Фильмы/Movie (2026)/Subs/", items)

    def test_special_characters_are_escaped(self):
        _, data = self.h.request("PROPFIND", BASE + quote("Фильмы/Movie (2026)/Subs/"), {"Depth": "1"})
        self.assertIn(b"en &amp; ru.srt", data)                  # valid XML
        self.assertIn(BASE + "Фильмы/Movie (2026)/Subs/en & ru.srt", responses(data))

    def test_full_get(self):
        resp, data = self.h.request("GET", MOVIE)
        self.assertEqual(resp.status, 200)
        self.assertEqual(len(data), 10000)
        self.assertEqual(resp.getheader("Accept-Ranges"), "bytes")

    def test_range_for_seeking(self):
        resp, data = self.h.request("GET", MOVIE, {"Range": "bytes=10-19"})
        self.assertEqual(resp.status, 206)
        self.assertEqual(data, b"0123456789")
        self.assertEqual(resp.getheader("Content-Range"), "bytes 10-19/10000")

    def test_head(self):
        resp, data = self.h.request("HEAD", MOVIE)
        self.assertEqual((resp.status, data), (200, b""))
        self.assertEqual(resp.getheader("Content-Length"), "10000")

    def test_browser_gets_a_folder_list(self):
        resp, data = self.h.request("GET", BASE)
        self.assertEqual(resp.status, 200)
        self.assertIn("Фильмы/".encode(), data)

    def test_missing_path(self):
        resp, _ = self.h.request("PROPFIND", BASE + "nope/", {"Depth": "1"})
        self.assertEqual(resp.status, 404)

    def test_dot_dot_stays_inside_the_library(self):
        resp, data = self.h.request("PROPFIND", BASE + "../../etc/", {"Depth": "0"})
        self.assertIn(resp.status, (207, 404))
        if resp.status == 207:
            self.assertEqual(list(responses(data)), [BASE])

    def test_writing_is_not_possible(self):
        resp, _ = self.h.request("PUT", BASE + "x.txt", body=b"x")
        self.assertEqual(resp.status, 404)


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class PasswordTests(unittest.TestCase):
    """Silo with an owner password: HTTP Basic, checked by Silo's login."""

    @classmethod
    def setUpClass(cls):
        cls.h = Harness(password="s3cret")

    @classmethod
    def tearDownClass(cls):
        cls.h.stop()

    def basic(self, user, password):
        token = base64.b64encode("{}:{}".format(user, password).encode()).decode()
        return {"Authorization": "Basic " + token, "Depth": "1"}

    def test_no_credentials_asks_for_basic(self):
        resp, _ = self.h.request("PROPFIND", BASE, {"Depth": "1"})
        self.assertEqual(resp.status, 401)
        self.assertIn("Basic", resp.getheader("WWW-Authenticate"))

    def test_wrong_password(self):
        resp, _ = self.h.request("PROPFIND", BASE, self.basic("owner", "nope"))
        self.assertEqual(resp.status, 401)

    def test_owner_password_works_and_is_checked_once(self):
        for _ in range(3):
            resp, _ = self.h.request("PROPFIND", BASE, self.basic("owner", "s3cret"))
            self.assertEqual(resp.status, 207)
        resp, data = self.h.request("GET", MOVIE, dict(self.basic("owner", "s3cret"), Range="bytes=0-3"))
        self.assertEqual((resp.status, data), (206, b"0123"))
        _, log = self.h.request("GET", "/__log")
        self.assertEqual(log.decode().count("login owner ok"), 1)


if __name__ == "__main__":
    unittest.main()
