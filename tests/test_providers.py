import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from tokenpace import providers


class ProvidersTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_codex(self):
        auth = self.tmp / "auth.json"
        auth.write_text(json.dumps({"tokens": {"access_token": "not-a-jwt", "account_id": "acct"}}))
        body = {"plan_type": "plus", "rate_limit": {
            "primary_window": {"used_percent": 12, "limit_window_seconds": 18000, "reset_after_seconds": 3600},
            "secondary_window": {"used_percent": 40, "limit_window_seconds": 604800, "reset_at": time.time() + 86400}}}
        with mock.patch.object(providers, "http_json", return_value=(200, body)) as call:
            r = providers.codex({"auth_file": str(auth)}, time.time())
        self.assertEqual(call.call_args[0][1]["ChatGPT-Account-Id"], "acct")
        self.assertEqual([w["label"] for w in r["windows"]], ["5 hours", "Week"])
        self.assertEqual(r["plan_label"], "Plus")

    def test_codex_without_login(self):
        with self.assertRaises(providers.ProviderError) as ctx:
            providers.codex({"auth_file": str(self.tmp / "missing.json")}, time.time())
        self.assertEqual(ctx.exception.status, "unavailable")

    def creds(self, expires_in):
        path = self.tmp / ".credentials.json"
        path.write_text(json.dumps({"claudeAiOauth": {
            "accessToken": "old-access", "refreshToken": "old-refresh", "expiresAt": int((time.time() + expires_in) * 1000),
            "scopes": ["user:inference", "user:profile"], "subscriptionType": "max", "rateLimitTier": "default_claude_max_5x"}}))
        return path

    def test_claude_read_only(self):
        path = self.creds(3600)
        usage = {"five_hour": {"utilization": 20, "resets_at": "2030-01-01T00:00:00Z"},
                 "seven_day": {"utilization": 44, "resets_at": "2030-01-05T00:00:00Z"}}
        with mock.patch.object(providers, "http_json", return_value=(200, usage)):
            r = providers.claude_code({"credentials_file": str(path)}, time.time())
        self.assertEqual(r["plan_label"], "Max 5x")
        self.assertEqual([w["used_percent"] for w in r["windows"]], [20, 44])
        expired = self.creds(-10)
        with self.assertRaises(providers.ProviderError):
            providers.claude_code({"credentials_file": str(expired)}, time.time())

    def test_claude_refresh_persists_rotated_pair(self):
        path = self.creds(-10)
        usage = {"seven_day": {"utilization": 10, "resets_at": "2030-01-05T00:00:00Z"}}
        token = {"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 28800}
        with mock.patch.object(providers, "http_json", side_effect=[(200, token), (200, usage)]) as call:
            providers.claude_code({"credentials_file": str(path), "refresh": True}, time.time())
        sent = json.loads(call.call_args_list[0][0][2])
        self.assertEqual((sent["refresh_token"], sent["scope"]), ("old-refresh", "user:inference user:profile"))
        self.assertEqual(call.call_args_list[1][0][1]["Authorization"], "Bearer new-access")
        saved = json.loads(path.read_text())["claudeAiOauth"]
        self.assertEqual((saved["accessToken"], saved["refreshToken"]), ("new-access", "new-refresh"))

    def test_openrouter_free(self):
        body = {"data": {"free_model_daily_requests": {"used": 250, "limit": 1000}}}
        with mock.patch.dict("os.environ", {"OPENROUTER_API_KEY": "k"}), \
                mock.patch.object(providers, "http_json", return_value=(200, body)):
            r = providers.openrouter_free({}, time.time())
        w = r["windows"][0]
        self.assertEqual((w["used_percent"], w["limit_amount"], w["label"]), (25, 1000, "Day (UTC)"))
        self.assertTrue(w["resets_at"].endswith("T00:00:00Z"))


if __name__ == "__main__":
    unittest.main()
