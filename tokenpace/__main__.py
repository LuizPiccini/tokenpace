"""Command line: tokenpace serve | collect | push | check."""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request

from . import __version__
from .config import ConfigError, demo_config, load
from .pace import iso
from .providers import PROVIDERS, ProviderError


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


def cmd_push(args: argparse.Namespace) -> int:
    """Read this machine's subscriptions and send the numbers to a tokenpace server."""
    cfg = load(args.config)
    token = args.token or os.environ.get("TOKENPACE_TOKEN") or cfg.token
    if not token:
        print("push needs the server's token: --token or TOKENPACE_TOKEN", file=sys.stderr)
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
    body = json.dumps({"source": args.source or socket.gethostname(), "readings": readings}).encode()
    req = urllib.request.Request(args.to.rstrip("/") + "/api/push", data=body, method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            print(resp.read().decode())
        return 0
    except urllib.error.HTTPError as exc:
        print(f"push failed: HTTP {exc.code}: {exc.read(300).decode(errors='replace')}", file=sys.stderr)
    except urllib.error.URLError as exc:
        print(f"push failed: {exc.reason}", file=sys.stderr)
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
    u = sub.add_parser("push", help="send this machine's readings to a tokenpace server")
    u.add_argument("--config")
    u.add_argument("--to", required=True, help="server URL, e.g. http://my-server:8787")
    u.add_argument("--token")
    u.add_argument("--source", help="name shown on the server (default: hostname)")
    u.set_defaults(fn=cmd_push)
    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except ConfigError as exc:
        print(f"tokenpace: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
