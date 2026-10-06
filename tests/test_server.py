import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from tokenpace import providers
from tokenpace.config import ConfigError, from_dict
from tokenpace.pace import iso
from tokenpace.server import App, Handler


def config(tmp, **server):
    return from_dict({
        "server": {"state_dir": tmp, **server},
        "groups": [{"id": "personal", "label": "Personal", "logo": "logo.png"}, {"id": "work", "label": "Work"}],
        "subscriptions": [
            {"id": "console", "name": "Console", "provider": "manual", "period_days": 30},
            {"id": "laptop-claude", "name": "Claude", "group": "work", "provider": "push"},
            {"id": "demo", "name": "Demo", "provider": "demo", "demo_windows": ["weekly"]},
        ],
    }, Path(tmp) / "tokenpace.toml")


class AppTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_config_errors(self):
        with self.assertRaises(ConfigError):
            from_dict({"subscriptions": [{"id": "x", "provider": "nope"}]})
        with self.assertRaises(ConfigError):
            from_dict({"subscriptions": [{"id": "x", "provider": "demo", "group": "missing"}]})

    def test_manual_report_and_staleness(self):
        app = App(config(self.tmp))
        with self.assertRaises(ValueError):
            app.report_manual({"id": "console", "used_percent": 10})          # no renewal date
        with self.assertRaises(ValueError):
            app.report_manual({"id": "demo", "used_percent": 10, "resets_at": iso(time.time() + 86400)})
        app.report_manual({"id": "console", "used_amount": 25, "limit_amount": 100, "unit": "credits",
                           "resets_at": iso(time.time() + 10 * 86400)})
        s = next(x for x in app.view()["subscriptions"] if x["id"] == "console")
        self.assertEqual(s["status"], "ok")
        self.assertEqual(s["windows"][0]["used_percent"], 25)
        self.assertEqual(s["windows"][0]["label"], "Month")
        self.assertTrue(s["caveat"].startswith("Entered by hand"))
        app.state["console"]["observed_at"] = iso(time.time() - 4 * 86400)
        s = next(x for x in app.view()["subscriptions"] if x["id"] == "console")
        self.assertEqual(s["status"], "stale")

    def test_push(self):
        app = App(config(self.tmp))
        with self.assertRaises(ValueError):
            app.ingest_push({"readings": {"console": {"windows": []}}})       # not a push subscription
        app.ingest_push({"source": "laptop", "readings": {"laptop-claude": {
            "windows": [{"kind": "weekly", "used_percent": 30, "resets_at": iso(time.time() + 86400)}]}}})
        view = app.view()
        s = next(x for x in view["subscriptions"] if x["id"] == "laptop-claude")
        self.assertEqual((s["status"], s["source"]), ("ok", "laptop"))
        self.assertEqual(view["advice"]["groups"]["work"][0]["id"], "laptop-claude")
        # An error push keeps the last numbers, shown as old.
        app.ingest_push({"readings": {"laptop-claude": {"error": "login expired"}}})
        s = next(x for x in app.view()["subscriptions"] if x["id"] == "laptop-claude")
        self.assertEqual(s["status"], "stale")
        self.assertEqual(len(s["windows"]), 1)

    def test_collect_keeps_last_numbers_on_failure(self):
        app = App(config(self.tmp))
        app.collect()
        self.assertEqual(app.state["demo"]["status"], "ok")
        with mock.patch.dict(providers.PROVIDERS, {"demo": mock.Mock(side_effect=providers.ProviderError("error", "down"))}):
            app.collect()
        s = next(x for x in app.view()["subscriptions"] if x["id"] == "demo")
        self.assertEqual(s["status"], "stale")
        self.assertTrue(s["windows"])
        widget = app.widget_view()
        self.assertEqual([g["id"] for g in widget["groups"]], ["personal", "work"])


class HttpTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        Path(self.tmp, "logo.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
        app = App(config(self.tmp, token="s3cret"))
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), type("H", (Handler,), {"app": app}))
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def call(self, path, data=None, token="s3cret", ctype="application/json"):
        headers = {"Content-Type": ctype} if data is not None else {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method="POST" if data is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            with e:
                return e.code, e.read()

    def test_page_is_public_and_api_needs_token(self):
        self.assertEqual(self.call("/", token=None)[0], 200)
        self.assertEqual(self.call("/api/usage", token=None)[0], 401)
        self.assertEqual(self.call("/api/usage", token="wrong")[0], 401)
        code, body = self.call("/api/widget")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["groups"][0]["logo_url"], "logo/personal")

    def test_posts_must_be_json(self):
        body = json.dumps({"readings": {}}).encode()
        self.assertEqual(self.call("/api/push", body, ctype="text/plain")[0], 400)
        self.assertEqual(self.call("/api/push", body)[0], 200)

    def test_logo(self):
        code, body = self.call("/logo/personal", token=None)
        self.assertEqual((code, body[:4]), (200, b"\x89PNG"))
        self.assertEqual(self.call("/logo/work", token=None)[0], 404)


if __name__ == "__main__":
    unittest.main()
