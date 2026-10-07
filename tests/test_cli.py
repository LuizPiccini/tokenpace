import os
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from tokenpace.__main__ import main
from tokenpace.config import load


class SetTokenTest(unittest.TestCase):
    def run_cli(self, path):
        out = StringIO()
        with redirect_stdout(out):
            self.assertEqual(main(["set-token", "--config", str(path)]), 0)
        return out.getvalue()

    def test_writes_token_once_without_printing_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text('[server]\nport = 8787\n\n[[subscriptions]]\nid = "d"\nprovider = "demo"\n', encoding="utf-8")
            out = self.run_cli(path)
            token = load(str(path)).token
            self.assertTrue(token and len(token) >= 30)
            self.assertNotIn(token, out)
            if os.name == "posix":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertIn("already has a token", self.run_cli(path))
            self.assertEqual(load(str(path)).token, token)

    def test_adds_server_section_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text('[[subscriptions]]\nid = "d"\nprovider = "demo"\n', encoding="utf-8")
            self.run_cli(path)
            self.assertTrue(load(str(path)).token)
