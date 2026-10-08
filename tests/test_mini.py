import json
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from tokenpace import mini


def row(**kw):
    base = {"rank": 1, "name": "Claude", "window": "Week", "level": "use", "verdict": "Use first",
            "used_percent": 30, "elapsed_percent": 70, "need": 2.3, "free": False,
            "resets_epoch": 2_000_000_000, "released_epoch": None, "note": None}
    base.update(kw)
    return base


class Hostile(BaseHTTPRequestHandler):
    seen: list = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        Hostile.seen.append((self.path, self.headers.get("Authorization")))
        case = self.path.strip("/").split("/")[0]
        if case == "big":
            self.send_response(200)
            self.end_headers()
            try:
                for _ in range(200):
                    self.wfile.write(b" " * 65536)
            except OSError:
                pass
            return
        if case == "slow":
            self.send_response(200)
            self.end_headers()
            try:
                for _ in range(20):
                    self.wfile.write(b" ")
                    self.wfile.flush()
                    time.sleep(0.2)
            except OSError:
                pass
            return
        if case == "redirect":
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:%d/steal/api/widget" % self.server.server_port)
            self.end_headers()
            return
        if case == "locked":
            self.send_response(401)
            self.end_headers()
            return
        if case == "dripheaders":
            try:
                self.wfile.write(b"HTTP/1.1 200 OK\r\n")
                for _ in range(40):
                    self.wfile.write(b"X-Slow: 1\r\n")
                    self.wfile.flush()
                    time.sleep(0.2)
            except OSError:
                pass
            self.close_connection = True
            return
        if case == "many":
            body = json.dumps({"groups": [{"label": "P", "items": [{}] * 5000}]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        body = json.dumps({"groups": [{"label": "Personal", "items": [row()]}]}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class FetchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Hostile)
        cls.base = "http://127.0.0.1:%d" % cls.httpd.server_port
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def test_reads_and_sends_token(self):
        groups = mini.fetch(self.base + "/ok", "tok")
        self.assertEqual(groups[0]["items"][0]["name"], "Claude")
        self.assertIn(("/ok/api/widget", "Bearer tok"), Hostile.seen)

    def test_refuses_what_a_hostile_server_sends(self):
        with self.assertRaisesRegex(mini.MiniError, "too large"):
            mini.fetch(self.base + "/big")
        start = time.monotonic()
        with mock.patch.object(mini, "TOTAL_TIMEOUT", 1.0), self.assertRaisesRegex(mini.MiniError, "too slow"):
            mini.fetch(self.base + "/slow")   # the body drips for 4 s
        self.assertLess(time.monotonic() - start, 2.5)
        with self.assertRaisesRegex(mini.MiniError, "HTTP 302"):
            mini.fetch(self.base + "/redirect", "secret")
        self.assertFalse(any(p.startswith("/steal") for p, _ in Hostile.seen))   # redirect not followed
        with self.assertRaisesRegex(mini.MiniError, "token"):
            mini.fetch(self.base + "/locked")
        with self.assertRaisesRegex(mini.MiniError, "token"):
            mini.fetch(self.base + "/ok", "has space")
        with self.assertRaisesRegex(mini.MiniError, "reach"):
            mini.fetch("http://127.0.0.1:9")

    def test_total_timeout_covers_headers(self):
        start = time.monotonic()
        with mock.patch.object(mini, "TOTAL_TIMEOUT", 1.0), self.assertRaisesRegex(mini.MiniError, "too slow"):
            mini.fetch(self.base + "/dripheaders")
        self.assertLess(time.monotonic() - start, 4)

    def test_rows_are_capped(self):
        self.assertEqual(len(mini.fetch(self.base + "/many")[0]["items"]), mini.MAX_ROWS)
        groups = mini.parse(json.dumps({"groups": {"g%d" % i: [{}] for i in range(500)}}))
        self.assertEqual(len(groups), mini.MAX_ROWS)

    def test_http_in_capitals_bypasses_the_proxy(self):
        hits = []

        class Proxy(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                hits.append(self.headers.get("Authorization"))
                self.send_response(502)
                self.end_headers()

        proxy = ThreadingHTTPServer(("127.0.0.1", 0), Proxy)
        threading.Thread(target=proxy.serve_forever, daemon=True).start()
        try:
            env = {"http_proxy": "http://127.0.0.1:%d" % proxy.server_port, "no_proxy": ""}
            with mock.patch.dict("os.environ", env):
                server = mini.normalize(self.base.replace("http://", "HTTP://") + "/ok")
                self.assertTrue(server.startswith("http://"))
                mini.fetch(server, "secret")
        finally:
            proxy.shutdown()
            proxy.server_close()
        self.assertEqual(hits, [])


class ParseTest(unittest.TestCase):
    def test_both_feed_shapes(self):
        listed = mini.parse(json.dumps({"groups": [{"id": "p", "label": "Personal", "items": [row()]}]}))
        old = mini.parse(json.dumps({"groups": {"Personal": [row()], "Tela": []}}))
        self.assertEqual(listed[0]["label"], "Personal")
        self.assertEqual([g["label"] for g in old], ["Personal", "Tela"])
        self.assertEqual(mini.top_plan(old)["name"], "Claude")

    def test_bad_values(self):
        p = mini.parse(json.dumps({"groups": [{"label": "P", "items": [
            row(used_percent="NaN", need="Infinity", elapsed_percent=None), row(used_percent=250, elapsed_percent=-5), "x"]}]}))[0]["items"]
        self.assertEqual((p[0]["used"], p[0]["need"], p[0]["elapsed"]), (0.0, 0.0, None))
        self.assertEqual((p[1]["used"], p[1]["elapsed"]), (100.0, 0.0))
        self.assertEqual(len(p), 2)
        for bad in ("not json", "[]", '{"x": 1}'):
            with self.assertRaises(mini.MiniError):
                mini.parse(bad)

    def test_text(self):
        p = mini.parse(json.dumps({"groups": [{"label": "P", "items": [row(need=120), row(level="blocked", released_epoch=1000 + 7200)]}]}))[0]["items"]
        self.assertEqual(mini.pace_text(p[0]), "99×+")
        self.assertEqual(mini.pace_text(p[1]), "used up")
        self.assertEqual(mini.when_text(p[1], now=1000), "frees in 2h 00m")
        self.assertIsNone(mini.top_plan([{"label": "P", "items": []}]))
        self.assertEqual(mini.clip16("ab\U0001F680c", 3), "ab")
        self.assertEqual(mini._u16("\U0001F680"), 2)
        lone = json.loads('"Claude \\ud83d"')   # half an emoji, as a server might cut it
        self.assertEqual(mini._u16(lone), 8)
        self.assertEqual(mini.clip16(lone, 7), "Claude ")

    @unittest.skipUnless(sys.platform == "win32", "Windows command-line parsing")
    def test_start_command_survives_odd_paths(self):
        import ctypes
        from ctypes import wintypes
        parse = ctypes.windll.shell32.CommandLineToArgvW
        parse.restype = ctypes.POINTER(wintypes.LPWSTR)
        for path in (r"C:\Users\D'Angelo\src", "D:\\", r"C:\with space\x"):
            n = ctypes.c_int()
            argv = parse(mini.start_command(path), ctypes.byref(n))
            self.assertEqual(argv[n.value - 1], path)

    def test_normalize(self):
        self.assertEqual(mini.normalize("host:8787/api/widget/"), "http://host:8787")
        self.assertEqual(mini.normalize("https://h.example"), "https://h.example")
        for bad in ("", "file:///c:/x", "javascript:alert(1)", "http://"):
            self.assertEqual(mini.normalize(bad), "")
