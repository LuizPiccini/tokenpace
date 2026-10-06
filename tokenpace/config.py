"""Configuration: one TOML file (see config.example.toml)."""
from __future__ import annotations

import os
import re
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .providers import PASSIVE, PROVIDERS, expand

ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")


class ConfigError(ValueError):
    pass


def default_state_dir() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", "~")).expanduser() / "tokenpace"
    return Path(os.environ.get("XDG_STATE_HOME", "~/.local/state")).expanduser() / "tokenpace"


def find_config(explicit: str | None) -> Path:
    candidates = [explicit, os.environ.get("TOKENPACE_CONFIG"), "tokenpace.toml",
                  "~/.config/tokenpace/config.toml"]
    for c in candidates:
        if c and expand(c).is_file():
            return expand(c)
    raise ConfigError("No config found. Copy config.example.toml to tokenpace.toml (or "
                      "~/.config/tokenpace/config.toml), or pass --config. Try --demo to see it working.")


@dataclass
class Config:
    path: Path | None
    title: str = "AI subscriptions"
    host: str = "127.0.0.1"
    port: int = 8787
    refresh_minutes: float = 10
    stale_minutes: float = 30
    manual_stale_days: float = 3
    token: str | None = None
    state_dir: Path = field(default_factory=default_state_dir)
    groups: list[dict[str, Any]] = field(default_factory=list)
    subscriptions: list[dict[str, Any]] = field(default_factory=list)
    apk: dict[str, Any] = field(default_factory=dict)

    @property
    def base_dir(self) -> Path:
        return self.path.parent if self.path else Path.cwd()

    def resolve(self, value: str) -> Path:
        p = expand(value)
        return p if p.is_absolute() else self.base_dir / p

    def group_ids(self) -> list[str]:
        return [g["id"] for g in self.groups]


def from_dict(raw: dict[str, Any], path: Path | None = None) -> Config:
    server = raw.get("server") or {}
    cfg = Config(path=path)
    cfg.title = str(server.get("title") or cfg.title)
    cfg.host = str(server.get("host") or cfg.host)
    cfg.port = int(server.get("port") or cfg.port)
    cfg.refresh_minutes = max(1.0, float(server.get("refresh_minutes") or cfg.refresh_minutes))
    cfg.stale_minutes = float(server.get("stale_minutes") or max(cfg.stale_minutes, 3 * cfg.refresh_minutes))
    cfg.manual_stale_days = float(server.get("manual_stale_days") or cfg.manual_stale_days)
    cfg.token = os.environ.get("TOKENPACE_TOKEN") or server.get("token") or None
    if server.get("state_dir"):
        cfg.state_dir = expand(server["state_dir"])
    cfg.apk = dict(raw.get("android") or {})

    groups = raw.get("groups") or [{"id": "personal", "label": "Personal"}]
    seen: set[str] = set()
    for g in groups:
        gid = str(g.get("id") or "")
        if not ID.match(gid) or gid in seen:
            raise ConfigError(f"group id {gid!r} must be unique, lowercase letters, digits, - or _")
        seen.add(gid)
        cfg.groups.append({"id": gid, "label": str(g.get("label") or gid), "logo": g.get("logo")})

    ids: set[str] = set()
    for s in raw.get("subscriptions") or []:
        sid = str(s.get("id") or "")
        if not ID.match(sid) or sid in ids:
            raise ConfigError(f"subscription id {sid!r} must be unique, lowercase letters, digits, - or _")
        ids.add(sid)
        provider = s.get("provider")
        if provider not in PROVIDERS and provider not in PASSIVE:
            raise ConfigError(f"{sid}: provider must be one of {sorted([*PROVIDERS, *PASSIVE])}")
        group = s.get("group") or cfg.groups[0]["id"]
        if group not in seen:
            raise ConfigError(f"{sid}: group {group!r} is not defined under [[groups]]")
        cfg.subscriptions.append({**s, "id": sid, "group": group, "name": str(s.get("name") or sid)})
    if not cfg.subscriptions:
        raise ConfigError("add at least one [[subscriptions]] entry")
    return cfg


def load(path: str | None) -> Config:
    p = find_config(path)
    try:
        raw = tomllib.loads(p.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{p}: {exc}") from None
    return from_dict(raw, p)


def demo_config() -> Config:
    return from_dict({
        "server": {"title": "AI subscriptions (demo)", "refresh_minutes": 1},
        "groups": [{"id": "personal", "label": "Personal"}, {"id": "work", "label": "Work"}],
        "subscriptions": [
            {"id": "claude", "name": "Claude", "provider": "demo", "plan": "Max 5x",
             "demo_windows": ["five_hour", "weekly"], "demo_rate": 0.45, "use_via": "Claude Code"},
            {"id": "chatgpt", "name": "ChatGPT", "provider": "demo", "plan": "Plus",
             "demo_windows": ["five_hour", "weekly"], "demo_rate": 0.7, "use_via": "Codex CLI"},
            {"id": "mimo", "name": "Xiaomi MiMo", "provider": "demo", "plan": "Lite",
             "demo_windows": ["monthly"], "demo_rate": 1.0},
            {"id": "openrouter-free", "name": "OpenRouter free", "provider": "demo", "free": True,
             "demo_windows": ["daily"], "demo_rate": 0.3},
            {"id": "work-claude", "name": "Claude", "group": "work", "provider": "demo", "plan": "Team",
             "demo_windows": ["five_hour", "weekly"], "demo_rate": 1.3},
        ],
    })
