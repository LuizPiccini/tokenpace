import os
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from tokenpace.__main__ import main
from tokenpace.__main__ import set_token
from tokenpace.config import ConfigError, load


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

    def check(self, text, replaced=True):
        """set-token on text must leave a config that loads with a real, new token."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_bytes(text.encode("utf-8"))   # exact bytes: write_text would double \r on Windows
            self.assertEqual(set_token(path), replaced)
            cfg = load(str(path))
            self.assertTrue(cfg.token and cfg.token != "long-random-string" and len(cfg.token) >= 30)
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()), ["config.toml"])
            return path.read_text(encoding="utf-8")

    def test_tricky_configs(self):
        sub = '\n[[subscriptions]]\nid = "d"\nprovider = "demo"\n'
        self.check(sub + "[server]")                                   # header last, no newline
        self.check("[ server ]\nport = 8787\n" + sub)                 # spaces inside the brackets
        self.check('[server]\ntoken = ""\nport = 8787\n' + sub)        # empty token: replaced
        self.check('[server] # main\r\ntoken = "long-random-string"\r\n' + sub.replace("\n", "\r\n"))   # placeholder, CRLF
        self.check('[[groups]]\nid = "g"\nlabel = "G"\ntoken = "x"\n[server]\nport = 1\n' + sub.replace('provider = "demo"', 'provider = "demo"\ngroup = "g"'))
        text = self.check('[server]\ntoken = "' + "k" * 32 + '"\n' + sub, replaced=False)
        self.assertIn("k" * 32, text)

    def test_refuses_what_it_cannot_edit_safely(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            original = 'server = { port = 8787 }\n[[subscriptions]]\nid = "d"\nprovider = "demo"\n'
            path.write_text(original, encoding="utf-8")
            with self.assertRaises(ConfigError):
                set_token(path)
            self.assertEqual(path.read_text(encoding="utf-8"), original)
            # A token-looking line inside a multiline string must not be touched.
            original = '[server]\ntitle = """Subs\ntoken = here\n"""\n[[subscriptions]]\nid = "d"\nprovider = "demo"\n'
            path.write_text(original, encoding="utf-8")
            with self.assertRaises(ConfigError):
                set_token(path)
            self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_keeps_top_level_keys_and_line_endings(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_bytes(b'port = 9999\r\nhost = "0.0.0.0"\r\n[[subscriptions]]\r\nid = "d"\r\nprovider = "demo"\r\n')
            self.assertTrue(set_token(path))
            cfg = load(str(path))
            self.assertEqual((cfg.host, cfg.port), ("127.0.0.1", 8787))   # stray keys stay outside [server]
            self.assertTrue(cfg.token)
            self.assertIn(b"\r\n", path.read_bytes())
