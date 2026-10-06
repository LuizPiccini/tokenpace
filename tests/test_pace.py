import time
import unittest

from tokenpace.pace import build_advice, iso, make_window, window_pace


def sub(sid, used, elapsed_frac, seconds=7 * 86400, kind="weekly", group="personal", **extra):
    now = time.time()
    reset = now + (1 - elapsed_frac) * seconds
    return {"id": sid, "name": sid, "group": group, "status": "ok",
            "windows": [make_window(kind, used, iso(reset), seconds)], **extra}


class PaceTest(unittest.TestCase):
    def test_needed_pace(self):
        now = time.time()
        w = make_window("weekly", 20, iso(now + 3.5 * 86400))
        p = window_pace(w, now)
        self.assertAlmostEqual(p["elapsed_percent"], 50, delta=0.2)
        self.assertAlmostEqual(p["need"], 1.6, delta=0.01)   # 80% left / 50% of the week left
        self.assertEqual(p["projected_left_percent"], 60)

    def test_renewed_window_counts_as_fresh(self):
        now = time.time()
        w = make_window("five_hour", 90, iso(now - 60))
        p = window_pace(w, now)
        self.assertTrue(p["renewed_since_reading"])
        self.assertEqual(p["used_percent"], 0)

    def test_window_without_reset_is_left_out(self):
        s = {"id": "x", "name": "X", "group": "personal", "status": "ok",
             "windows": [make_window("weekly", 10, None)]}
        adv = build_advice([s], ["personal"], time.time())
        self.assertEqual(adv["groups"]["personal"], [])
        self.assertEqual(adv["left_out"][0]["id"], "x")

    def test_ranking(self):
        now = time.time()
        subs = [
            sub("on-pace", 50, 0.5),
            sub("most-slack", 10, 0.8),
            sub("some-slack", 20, 0.5),
            sub("saving", 90, 0.5),
            sub("free", 1, 0.5, 86400, "daily", free=True),
            sub("work", 5, 0.5, group="work"),
        ]
        blocked = sub("blocked", 10, 0.5)
        blocked["windows"].append(make_window("five_hour", 99, iso(now + 3600)))
        subs.append(blocked)
        adv = build_advice(subs, ["personal", "work"], now)
        rows = adv["groups"]["personal"]
        self.assertEqual([r["id"] for r in rows], ["most-slack", "some-slack", "on-pace", "saving", "free", "blocked"])
        self.assertEqual([r["verdict"] for r in rows],
                         ["Use first", "Use too", "On pace", "Save", "Free reserve", "Used up now"])
        self.assertEqual(adv["groups"]["work"][0]["verdict"], "Use first")   # each group ranks on its own
        self.assertIsNotNone(rows[-1]["released_at"])


if __name__ == "__main__":
    unittest.main()
