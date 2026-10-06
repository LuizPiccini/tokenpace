"""Where readings come from.

Each provider is a function (subscription config, now) -> reading, where a reading is
{"windows": [...], "plan_label": str | None, "message": str | None}. Providers read
credentials that already exist on the machine (the CLI logins), make one HTTPS
request, and return numbers only. They never print or store tokens, except the
opt-in Claude refresh, which writes the rotated token pair back to the file it read.

Providers:
  codex            ChatGPT plan limits from the Codex CLI login (~/.codex/auth.json)
  claude_code      Claude plan limits from the Claude Code login (file or macOS Keychain)
  openrouter_free  OpenRouter's daily allowance of requests to :free models
  manual           numbers typed on the page (for consoles without an API)
  push             readings sent by "tokenpace push" from another machine
  demo             synthetic data, to try the page and the widget
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
import platform
import re
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from .locks import file_lock
from .pace import kind_for_seconds, make_window, num, sort_windows

HTTP_TIMEOUT = 15
USER_AGENT = "tokenpace"
PLAN_NAMES = {"prolite": "Pro Lite", "pro": "Pro", "plus": "Plus", "team": "Team", "business": "Business",
              "enterprise": "Enterprise", "free": "Free", "go": "Go"}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow redirects: urllib would resend the Authorization header to the new host."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


_OPENER = urllib.request.build_opener(_NoRedirect())


class ProviderError(Exception):
    """status is "unavailable" (no login, nothing to read) or "error" (reading failed)."""

    def __init__(self, status: str, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def expand(path: str | os.PathLike) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(str(path))))


def read_json_file(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def http_json(url: str, headers: dict[str, str], data: bytes | None = None) -> tuple[int, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json", **headers},
                                 data=data, method="POST" if data is not None else "GET")
    try:
        with _OPENER.open(req, timeout=HTTP_TIMEOUT) as resp:
            status, body = resp.status, resp.read(262144)
    except urllib.error.HTTPError as exc:
        with exc:
            status, body = exc.code, exc.read(65536)
    except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
        raise ProviderError("error", f"No connection ({type(exc).__name__}).") from None
    except ValueError:
        # e.g. a credential with a newline in it: never echo the value
        raise ProviderError("error", "The stored credential has an invalid format.") from None
    try:
        return status, json.loads(body.decode("utf-8"))
    except ValueError:
        return status, None


def http_failure(who: str, status: int) -> ProviderError:
    if status in (401, 403):
        return ProviderError("error", f"{who} rejected the login (HTTP {status}).")
    if status == 429:
        return ProviderError("error", f"{who} rate-limited the request (HTTP 429); retrying next cycle.")
    return ProviderError("error", f"{who} answered HTTP {status}.")


def jwt_exp(token: str) -> float | None:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return float(json.loads(base64.urlsafe_b64decode(payload)).get("exp"))
    except Exception:  # noqa: BLE001 - not a JWT
        return None


# ---------------------------------------------------------------- Codex / ChatGPT

WHAM_URL = "https://chatgpt.com/backend-api/wham/usage"


def codex(cfg: dict[str, Any], now: float) -> dict[str, Any]:
    """Read-only: the Codex CLI refreshes its own login; refreshing here would race it."""
    path = expand(cfg.get("auth_file") or Path(os.environ.get("CODEX_HOME", "~/.codex")) / "auth.json")
    tokens = ((read_json_file(path) or {}).get("tokens")) or {}
    token = tokens.get("access_token")
    if not token:
        raise ProviderError("unavailable", "No ChatGPT login found (sign in with the Codex CLI, or set auth_file).")
    exp = jwt_exp(token)
    if exp is not None and exp < now + 60:
        raise ProviderError("unavailable", "The Codex login expired; the next Codex run renews it.")
    headers = {"Authorization": f"Bearer {token}", "User-Agent": "codex-cli"}
    if tokens.get("account_id"):
        headers["ChatGPT-Account-Id"] = str(tokens["account_id"])
    status, body = http_json(WHAM_URL, headers)
    if status != 200 or not isinstance(body, dict):
        raise http_failure("ChatGPT", status)
    rate = body.get("rate_limit") or {}
    windows = []
    for key in ("primary_window", "secondary_window"):
        win = rate.get(key)
        if not isinstance(win, dict):
            continue
        used = num(win.get("used_percent"))
        if used is None:
            continue
        seconds = num(win.get("limit_window_seconds"))
        reset = win.get("reset_at")
        if reset is None and num(win.get("reset_after_seconds")) is not None:
            reset = now + float(win["reset_after_seconds"])
        windows.append(make_window(kind_for_seconds(seconds), used, reset, seconds))
    if not windows:
        raise ProviderError("error", "ChatGPT returned no usage windows.")
    plan = str(body.get("plan_type") or "").lower()
    return {"windows": sort_windows(windows), "plan_label": PLAN_NAMES.get(plan),
            "message": "Limit reached." if rate.get("limit_reached") else None}


# ---------------------------------------------------------------- Claude Code

CLAUDE_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
CLAUDE_TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
CLAUDE_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"   # Claude Code's public OAuth client
CLAUDE_BETA = "oauth-2025-04-20"
CLAUDE_BUCKETS = {
    "five_hour": "five_hour",
    "seven_day": "weekly",
    "seven_day_opus": "weekly_opus",
    "seven_day_sonnet": "weekly_sonnet",
}
_claude_lock = threading.Lock()


def _claude_keychain() -> dict[str, Any] | None:
    if platform.system() != "Darwin":
        return None
    try:
        out = subprocess.run(["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
                             capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    try:
        return json.loads(out.stdout) if out.returncode == 0 else None
    except ValueError:
        return None


def claude_plan_label(plan: Any, tier: Any) -> str | None:
    """'max' + 'default_claude_max_5x' -> 'Max 5x'; 'pro' -> 'Pro'."""
    plan, tier = str(plan or "").lower(), str(tier or "").lower()
    match = re.search(r"max_(\d+)x", tier)
    if plan == "max" or match:
        return f"Max {match.group(1)}x" if match else "Max"
    return {"pro": "Pro", "free": "Free", "team": "Team", "enterprise": "Enterprise"}.get(plan)


def _claude_refresh(path: Path, data: dict[str, Any]) -> dict[str, Any]:
    oauth = data["claudeAiOauth"]
    payload = {"grant_type": "refresh_token", "refresh_token": oauth["refreshToken"], "client_id": CLAUDE_CLIENT_ID}
    if isinstance(oauth.get("scopes"), list) and oauth["scopes"]:
        payload["scope"] = " ".join(str(s) for s in oauth["scopes"])
    status, body = http_json(CLAUDE_TOKEN_URL, {"Content-Type": "application/json"}, json.dumps(payload).encode())
    if status != 200 or not isinstance(body, dict) or not isinstance(body.get("access_token"), str):
        raise ProviderError("error", f"Anthropic refused to renew the login (HTTP {status}); run "
                                     "'claude auth login' again for this credentials file.")
    oauth = dict(oauth)
    oauth["accessToken"] = body["access_token"]
    if isinstance(body.get("refresh_token"), str) and body["refresh_token"]:
        oauth["refreshToken"] = body["refresh_token"]
    oauth["expiresAt"] = int((time.time() + (num(body.get("expires_in")) or 3600)) * 1000)
    data = {**data, "claudeAiOauth": oauth}
    # The old refresh token is dead now: persist before anything else.
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    return data


def claude_code(cfg: dict[str, Any], now: float) -> dict[str, Any]:
    """Read-only by default. With refresh = true the provider renews the login itself; use it
    only with a login made for tokenpace (CLAUDE_CONFIG_DIR=... claude auth login), never
    with the one Claude Code uses, because refresh tokens rotate and a race logs one side out."""
    default = Path(os.environ.get("CLAUDE_CONFIG_DIR", "~/.claude")) / ".credentials.json"
    path = expand(cfg.get("credentials_file") or default)
    # With refresh on, a lock shared across processes: the second one rereads the renewed file
    # instead of sending the same (now dead) refresh token.
    with _claude_lock, (file_lock(path) if cfg.get("refresh") else contextlib.nullcontext()):
        data = read_json_file(path)
        source = str(path)
        if not data and not cfg.get("credentials_file"):
            data, source = _claude_keychain(), "the macOS Keychain"
        oauth = (data or {}).get("claudeAiOauth") or {}
        if not oauth.get("accessToken"):
            raise ProviderError("unavailable", "No Claude Code login found (sign in with Claude Code, or set "
                                               "credentials_file).")
        expires = (num(oauth.get("expiresAt")) or 0) / 1000.0
        if expires < now + 600 and cfg.get("refresh") and source == str(path) and oauth.get("refreshToken"):
            data = _claude_refresh(path, data)
            oauth = data["claudeAiOauth"]
        elif expires < now + 60:
            raise ProviderError("unavailable", "The Claude Code login expired; the next Claude Code run renews it "
                                               "(or set refresh = true with a dedicated login).")
    headers = {"Authorization": f"Bearer {oauth['accessToken']}", "anthropic-beta": CLAUDE_BETA,
               "Content-Type": "application/json", "User-Agent": "claude-cli (tokenpace)"}
    status, body = http_json(CLAUDE_USAGE_URL, headers)
    if status != 200 or not isinstance(body, dict):
        raise http_failure("Anthropic", status)
    windows = []
    for key, kind in CLAUDE_BUCKETS.items():
        bucket = body.get(key)
        if isinstance(bucket, dict) and num(bucket.get("utilization")) is not None:
            windows.append(make_window(kind, num(bucket["utilization"]), bucket.get("resets_at")))
    if not windows:
        raise ProviderError("error", "Anthropic returned no usage windows.")
    return {"windows": sort_windows(windows),
            "plan_label": claude_plan_label(oauth.get("subscriptionType"), oauth.get("rateLimitTier")),
            "message": None}


# ---------------------------------------------------------------- OpenRouter free models

OPENROUTER_KEY_URL = "https://openrouter.ai/api/v1/key"


def openrouter_free(cfg: dict[str, Any], now: float) -> dict[str, Any]:
    key = None
    if cfg.get("api_key_file"):
        try:
            key = expand(cfg["api_key_file"]).read_text(encoding="utf-8").strip()
        except OSError:
            key = None
    key = key or os.environ.get(cfg.get("api_key_env") or "OPENROUTER_API_KEY")
    if not key:
        raise ProviderError("unavailable", "No OpenRouter key (set api_key_env or api_key_file).")
    status, body = http_json(OPENROUTER_KEY_URL, {"Authorization": f"Bearer {key}"})
    if status != 200 or not isinstance(body, dict):
        raise http_failure("OpenRouter", status)
    data = body.get("data") or {}
    quota = data.get("free_model_daily_requests") if isinstance(data, dict) else None
    if not isinstance(quota, dict) or not num(quota.get("limit")):
        raise ProviderError("error", "OpenRouter did not report free_model_daily_requests.")
    used, limit = num(quota.get("used")) or 0.0, num(quota["limit"])
    reset = (int(now // 86400) + 1) * 86400   # 00:00 UTC
    window = make_window("daily", used / limit * 100.0, reset, label="Day (UTC)")
    window.update({"used_amount": int(used), "limit_amount": int(limit), "unit": "requests"})
    message = None
    if used >= limit:
        message = "Daily free-model allowance used up."
    elif limit <= 50:
        message = "No credits bought: 50 free requests a day (1,000 after buying credits)."
    return {"windows": [window], "plan_label": f"{int(limit):,} requests/day", "message": message}


# ---------------------------------------------------------------- demo

def demo(cfg: dict[str, Any], now: float) -> dict[str, Any]:
    """Synthetic, stable-looking windows so the page and the widget can be tried."""
    seed = int(hashlib.sha256(str(cfg.get("id")).encode()).hexdigest()[:8], 16)
    windows = []
    for i, kind in enumerate(cfg.get("demo_windows") or ["weekly"]):
        seconds = make_window(kind, 0, None)["window_seconds"] or 7 * 86400
        offset = (seed >> (i * 5)) % seconds
        reset = now - (now + offset) % seconds + seconds
        elapsed = 1.0 - (reset - now) / seconds
        rate = float(cfg.get("demo_rate") or (0.35 + ((seed >> (i * 7)) % 100) / 100.0))
        windows.append(make_window(kind, min(100.0, elapsed * 100.0 * rate), reset))
    return {"windows": sort_windows(windows), "plan_label": cfg.get("plan"), "message": None}


PROVIDERS: dict[str, Callable[[dict[str, Any], float], dict[str, Any]]] = {
    "codex": codex,
    "claude_code": claude_code,
    "openrouter_free": openrouter_free,
    "demo": demo,
}
PASSIVE = ("manual", "push")   # stored readings only; nothing to collect locally
