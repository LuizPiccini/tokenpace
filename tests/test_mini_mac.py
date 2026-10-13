import json
import math
import os
import plistlib
import struct
import sys
import tempfile
import time
import unittest
from unittest import mock

from tokenpace import mini
from tests.test_mini import row


def demo_view(expanded: bool = True) -> mini.View:
    v = mini.View()
    v.configured, v.server, v.expanded, v.at = True, "http://h:8787", expanded, time.time()
    v.groups = mini.parse(json.dumps({"groups": [
        {"label": "Personal", "items": [row(), row(rank=2, name="A plan with a very long name " * 3, level="save",
                                                   need=0.6, used_percent=63, elapsed_percent=49)]},
        {"label": "Work", "items": [row(level="blocked", released_epoch=time.time() + 600, elapsed_percent=None)]}]}))
    return v


@unittest.skipUnless(sys.platform == "darwin", "macOS drawing, settings and login item")
class MacMiniTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tokenpace import mini_mac
        cls.m = mini_mac

    def test_snapshot_is_a_png_of_the_view(self):
        for expanded in (False, True):
            view = demo_view(expanded)
            with tempfile.TemporaryDirectory() as d:
                path = os.path.join(d, "mini.png")
                self.m.render_png(view, path, backdrop="#2B3A55")
                with open(path, "rb") as f:
                    head = f.read(24)
            self.assertEqual(head[:8], b"\x89PNG\r\n\x1a\n")
            w, h = view.size()
            self.assertEqual(struct.unpack(">II", head[16:24]), (math.ceil(w * 2), math.ceil(h * 2)))
            self.assertIn("toggle", [hit[4] for hit in view.hits])

    def test_text_and_clips(self):
        seen = {}

        def paint(c):
            long = "A very long plan name " * 6
            seen["used"] = c.runs([(long, mini.Canvas.SEMIBOLD, 15, mini.C["ink"])], 0, 0, 20, 120)
            seen["right"] = c.runs([("1.5×", mini.Canvas.BOLD, 15, mini.C["ink"])], 0, 20, 20, 120, right=True)
            seen["bold"] = c.measure("Claude", mini.Canvas.BOLD, 15)
            seen["regular"] = c.measure("Claude", mini.Canvas.REGULAR, 15)
            c.runs([(json.loads('"Claude \\ud83d"'), mini.Canvas.REGULAR, 13, mini.C["soft"])], 0, 40, 18, 120)
            c.clip_rect(0, 0, 200, 30)
            c.pace_bar(0, 10, 200, 6, 30, 70)   # rounded clip on top of the base clip, then back
            seen["depth_in_list"] = c._depth
            c.unclip_all()
            seen["depth_after"] = c._depth

        self.m.CGImageRelease(self.m.draw_image(200, 60, 2, paint))
        self.assertLessEqual(seen["used"], 120)
        self.assertGreater(seen["used"], 100)
        self.assertGreater(seen["right"], 10)
        self.assertGreater(seen["bold"], seen["regular"])
        self.assertEqual((seen["depth_in_list"], seen["depth_after"]), (1, 0))

    def test_settings_keep_the_token_out_of_the_file(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(self.m, "support_dir", return_value=d), \
                mock.patch.object(self.m, "keychain_get", return_value="secret") as get, \
                mock.patch.object(self.m, "keychain_set") as put:
            s = self.m.MacSettings()
            s.server, s.token, s.left, s.top, s.expanded = "http://h:8787", "secret", 10.4, 20.6, True
            s.focus = "Tela"
            s.save()
            put.assert_called_once_with("secret")
            with open(os.path.join(d, "mini.json"), encoding="utf-8") as f:
                self.assertEqual(json.load(f), {"server": "http://h:8787", "expanded": True, "focus": "Tela",
                                                "left": 10, "top": 21})
            t = self.m.MacSettings.load()
            get.assert_called_once()
            self.assertEqual((t.server, t.token, t.left, t.top, t.expanded, t.focus),
                             ("http://h:8787", "secret", 10, 21, True, "Tela"))
            t.save()
            put.assert_called_once()   # an unchanged token is not written again

    def test_login_item(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(self.m, "agent_path", return_value=os.path.join(d, "LaunchAgents", "a.plist")), \
                mock.patch.object(self.m, "log_path", return_value=os.path.join(d, "Logs", "mini.log")):
            self.assertFalse(self.m.starts_at_login())
            self.m.set_starts_at_login(True)
            self.assertTrue(self.m.starts_at_login())
            with open(self.m.agent_path(), "rb") as f:
                agent = plistlib.load(f)
            self.assertEqual(agent["ProgramArguments"], self.m.start_args())
            self.assertEqual((agent["RunAtLoad"], agent["KeepAlive"]), (True, False))
            self.m.set_starts_at_login(False)
            self.assertFalse(os.path.exists(self.m.agent_path()))

    def test_start_args(self):
        clone = "/tmp/a clone with spaces"
        self.assertEqual(self.m.start_args(clone)[0], sys.executable)
        self.assertEqual(self.m.start_args(clone)[-1], clone)

    @unittest.skipUnless(os.environ.get("TOKENPACE_KEYCHAIN_TEST") == "1", "writes to the login Keychain")
    def test_keychain_round_trip(self):
        service = "Token Pace Mini test"
        try:
            self.m.keychain_set("first", service)
            self.m.keychain_set("sécond", service)
            self.assertEqual(self.m.keychain_get(service), "sécond")
        finally:
            self.m.keychain_set("", service)
        self.assertEqual(self.m.keychain_get(service), "")


if __name__ == "__main__":
    unittest.main()
