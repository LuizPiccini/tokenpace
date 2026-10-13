import unittest
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from argparse import Namespace
from unittest import mock
from tokenpace.tray import Client, POPOVER_CSS, POPOVER_SCRIPT, trouble


class TrayTest(unittest.TestCase):
    def test_icon_warns_only_on_a_failing_subscription(self):
        self.assertFalse(trouble({}))
        self.assertFalse(trouble({"subscriptions": []}))
        self.assertFalse(trouble({"subscriptions": [{"status": "ok"}, {"status": "ok"}]}))
        for status in ("error", "stale", "unavailable"):
            with self.subTest(status=status):
                self.assertTrue(trouble({"subscriptions": [{"status": "ok"}, {"status": status}]}))

    def test_popover_keeps_the_page_and_drops_only_the_chrome(self):
        hidden = set()
        for line in POPOVER_CSS.splitlines():
            if "display: none" in line:
                hidden.update(s.strip() for s in line.split("{")[0].split(","))
        self.assertEqual(hidden, {"header.top", ".topgrid", ".legend", "footer", ".gate", ".err"})
        for kept in (".groups", ".group", ".rec", ".head", ".chip", ".lane"):
            self.assertNotIn(kept, hidden)

    def test_popover_script_is_valid_javascript_with_the_css_escaped(self):
        self.assertIn(json.dumps(POPOVER_CSS), POPOVER_SCRIPT)
        self.assertIn("appendChild", POPOVER_SCRIPT)
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        with tempfile.TemporaryDirectory() as temp:
            script = Path(temp) / "popover.js"
            script.write_text(POPOVER_SCRIPT)
            checked = subprocess.run([node, "--check", str(script)], capture_output=True)
        self.assertEqual(checked.returncode, 0, checked.stderr.decode())

    def test_request_uses_authorization_header_and_refresh_post(self):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'{"queued":true}'
        with mock.patch("tokenpace.tray._OPENER.open", return_value=response) as send:
            self.assertTrue(Client("http://localhost:8787", "secret").request(True)["queued"])
        req = send.call_args.args[0]
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(req.full_url, "http://localhost:8787/api/refresh")
        self.assertEqual(req.get_header("Authorization"), "Bearer secret")
        self.assertNotIn("secret", req.full_url)

    def test_url_validation(self):
        for url in ("file:///tmp/a", "http://u:p@host", "http://host?token=secret", "http://host/#token=secret"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                Client(url)
        self.assertEqual(Client("http://localhost:8787", "a+b").dashboard(),
                         "http://localhost:8787/#token=a%2Bb")

    def test_config_with_a_provider_unknown_to_the_client(self):
        from tokenpace.__main__ import cmd_tray
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / "config.toml"
            config.write_text('[server]\nhost="127.0.0.1"\nport=9876\ntoken="server-token"\n[[subscriptions]]\nid="new"\nprovider="future-provider"\n')
            with mock.patch("tokenpace.tray.run", return_value=0) as run, mock.patch.dict("os.environ", {}, clear=True):
                self.assertEqual(cmd_tray(Namespace(config=str(config), url=None, show=False)), 0)
            run.assert_called_once_with("http://127.0.0.1:9876", "server-token", show=False)

    def test_tray_show_flag_reaches_run(self):
        from tokenpace.__main__ import cmd_tray
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / "config.toml"
            config.write_text('[server]\nhost="127.0.0.1"\nport=9876\n[[subscriptions]]\nid="new"\nprovider="future-provider"\n')
            with mock.patch("tokenpace.tray.run", return_value=0) as run, mock.patch.dict("os.environ", {}, clear=True):
                self.assertEqual(cmd_tray(Namespace(config=str(config), url=None, show=True)), 0)
            run.assert_called_once_with("http://127.0.0.1:9876", None, show=True)
