"""Command line: tokenpace serve | collect | push | check."""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import sys
import time
import tomllib
import urllib.error
import urllib.request

from . import __version__
from .config import ConfigError, demo_config, find_config, load
from .pace import iso
from .providers import _OPENER, PROVIDERS, ProviderError


def cmd_serve(args: argparse.Namespace) -> int:
    from .server import serve
    cfg = demo_config() if args.demo else load(args.config)
    if args.host:
        cfg.host = args.host
    if args.port:
        cfg.port = args.port
    serve(cfg)
    return 0


def cmd_collect(args: argparse.Namespace) -> int:
    from .server import App
    cfg = demo_config() if args.demo else load(args.config)
    app = App(cfg)
    app.collect()
    json.dump(app.view(), sys.stdout, indent=2, ensure_ascii=False)
    print()
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    cfg = load(args.config)
    print(f"config: {cfg.path}")
    print(f"groups: {', '.join(g['label'] for g in cfg.groups)}")
    for s in cfg.subscriptions:
        print(f"  {s['id']:<20} {s['provider']:<16} group={s['group']}")
    print(f"state: {cfg.state_dir}   listen: {cfg.host}:{cfg.port}   token: {'set' if cfg.token else 'none'}")
    return 0


PLACEHOLDER_TOKENS = {"long-random-string"}   # the value in config.example.toml
SERVER_HEADER = re.compile(r"^\s*\[\s*server\s*\]\s*(#.*)?$")
TABLE_HEADER = re.compile(r"^\s*\[")
TOKEN_LINE = re.compile(r"^\s*token\s*=")


def set_token(path) -> bool:
    """Write a random token into [server] unless a real one is there. Never prints it.

    Returns False when [server] already has a usable token. Raises ConfigError, writing
    nothing, when the result would not load with the new token."""
    with open(path, encoding="utf-8", newline="") as f:   # keep the file's own line endings
        text = f.read()
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: {exc}") from None
    server = data.get("server", {})
    if not isinstance(server, dict):
        raise ConfigError(f"{path}: 'server' is not a table")
    current = server.get("token")
    if isinstance(current, str) and current.strip() and current not in PLACEHOLDER_TOKENS:
        return False
    token = secrets.token_urlsafe(24)
    line = f'token = "{token}"\n'
    lines = text.splitlines(keepends=True)
    head = next((i for i, ln in enumerate(lines) if SERVER_HEADER.match(ln.rstrip("\r\n"))), None)
    if head is None:
        if "server" in data:
            raise ConfigError(f"{path}: [server] is written in a form set-token can't edit. "
                              "Ask the person to add a token line under [server] themselves.")
        # At the end: keys above the first table must not move into [server].
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines += ["\n", "[server]\n", line]
    else:
        if not lines[head].endswith("\n"):
            lines[head] += "\n"
        end = next((i for i in range(head + 1, len(lines)) if TABLE_HEADER.match(lines[i])), len(lines))
        body = [ln for ln in lines[head + 1:end] if not TOKEN_LINE.match(ln)]   # drops an empty or placeholder token
        lines = lines[:head + 1] + [line] + body + lines[end:]
    new = "".join(lines)
    expected = {**data, "server": {**{k: v for k, v in server.items() if k != "token"}, "token": token}}
    try:
        ok = tomllib.loads(new) == expected   # only the token may change
    except tomllib.TOMLDecodeError:
        ok = False
    if not ok:
        raise ConfigError(f"{path}: could not add the token without changing anything else. "
                          "Ask the person to add a token line under [server] themselves.")
    tmp = path.with_name(path.name + ".tokenpace-tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)   # never readable by others, even briefly
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(new)
        if os.name == "posix":
            os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return True


def cmd_set_token(args: argparse.Namespace) -> int:
    path = find_config(args.config)
    if set_token(path):
        print(f"token written to [server] in {path} (not shown; read it from the file)")
    else:
        print(f"{path} already has a token; nothing changed")
    return 0


def cmd_push(args: argparse.Namespace) -> int:
    """Read this machine's subscriptions and send the numbers to a tokenpace server."""
    cfg = load(args.config)
    token = args.token or os.environ.get("TOKENPACE_TOKEN") or cfg.token
    if not token:
        print("push needs the server's token: --token or TOKENPACE_TOKEN", file=sys.stderr)
        return 2
    if not all(33 <= ord(ch) <= 126 for ch in token):
        print("the token contains spaces or control characters", file=sys.stderr)
        return 2
    now = time.time()
    readings = {}
    for s in cfg.subscriptions:
        fn = PROVIDERS.get(s["provider"])
        if fn is None:
            continue
        target = s.get("push_as") or s["id"]
        try:
            r = fn(s, now)
            readings[target] = {"windows": r["windows"], "plan_label": r.get("plan_label"),
                                "message": r.get("message"), "observed_at": iso(now)}
        except ProviderError as exc:
            readings[target] = {"error": exc.message}
    body = json.dumps({"source": args.source or "push", "readings": readings}).encode()
    req = urllib.request.Request(args.to.rstrip("/") + "/api/push", data=body, method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
    try:
        with _OPENER.open(req, timeout=20) as resp:
            print(resp.read(4096).decode(errors="replace"))
        return 0
    except urllib.error.HTTPError as exc:
        with exc:
            print(f"push failed: HTTP {exc.code}: {exc.read(300).decode(errors='replace')}", file=sys.stderr)
    except urllib.error.URLError as exc:
        print(f"push failed: {exc.reason}", file=sys.stderr)
    except (ValueError, OSError) as exc:
        print(f"push failed: {type(exc).__name__}", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="tokenpace", description="Which AI subscription to use first.")
    p.add_argument("--version", action="version", version=f"tokenpace {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="serve the page, the API and the widget feed")
    s.add_argument("--config")
    s.add_argument("--demo", action="store_true", help="synthetic data, no config or logins needed")
    s.add_argument("--host")
    s.add_argument("--port", type=int)
    s.set_defaults(fn=cmd_serve)
    c = sub.add_parser("collect", help="read every subscription once and print the view as JSON")
    c.add_argument("--config")
    c.add_argument("--demo", action="store_true")
    c.set_defaults(fn=cmd_collect)
    k = sub.add_parser("check", help="validate the config")
    k.add_argument("--config")
    k.set_defaults(fn=cmd_check)
    t = sub.add_parser("set-token", help="write a random server token into the config without printing it")
    t.add_argument("--config")
    t.set_defaults(fn=cmd_set_token)
    u = sub.add_parser("push", help="send this machine's readings to a tokenpace server")
    u.add_argument("--config")
    u.add_argument("--to", required=True, help="server URL, e.g. http://my-server:8787")
    u.add_argument("--token")
    u.add_argument("--source", help="name shown on the server for these readings (default: push)")
    u.set_defaults(fn=cmd_push)
    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except ConfigError as exc:
        print(f"tokenpace: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())


