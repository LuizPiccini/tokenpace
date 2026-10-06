"""The HTTP server: the page, the JSON API, manual entries, pushes and the widget feed."""
from __future__ import annotations

import contextlib
import hmac
import ipaddress
import json
import math
import mimetypes
import os
import socket
import threading
import time
import zipfile
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from . import __version__
from .config import Config
from .locks import file_lock
from .pace import build_advice, iso, make_window, num, parse_time, relabel, short_duration, sort_windows
from .providers import PASSIVE, PROVIDERS, ProviderError

MAX_BODY = 64 * 1024
MAX_LOGO = 512 * 1024
MAX_CONNECTIONS = 32
REQUEST_DEADLINE = 15.0   # seconds for a whole request; a dripping client is cut off
PAGE_CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'; form-action 'self'")


def now_ts() -> float:
    return time.time()


def clean_text(value: Any, limit: int = 160) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = "".join(ch for ch in value if ch.isprintable()).strip()
    return cleaned[:limit] or None


def clean_windows(raw: Any) -> list[dict[str, Any]]:
    """Validate windows coming from a push or a stored file."""
    if not isinstance(raw, list):
        raise ValueError("windows must be a list")
    out = []
    for w in raw[:8]:
        if not isinstance(w, dict):
            raise ValueError("each window must be an object")
        used = num(w.get("used_percent"))
        if used is None and num(w.get("remaining_percent")) is not None:
            used = 100.0 - num(w.get("remaining_percent"))
        if used is None:
            raise ValueError("each window needs used_percent")
        win = make_window(clean_text(w.get("kind"), 20), used, w.get("resets_at"), num(w.get("window_seconds")),
                          clean_text(w.get("label"), 40))
        for k in ("used_amount", "limit_amount"):
            if num(w.get(k)) is not None:
                win[k] = num(w[k])
        if clean_text(w.get("unit"), 20):
            win["unit"] = clean_text(w["unit"], 20)
        out.append(win)
    return sort_windows(out)


class App:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.lock = threading.Lock()
        self.state_file = cfg.state_dir / "state.json"
        self.state: dict[str, dict[str, Any]] = self._load()
        self.last_collect = 0.0
        self.wake = threading.Event()

    # ------------------------------------------------------------ state

    def _load(self) -> dict[str, dict[str, Any]]:
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_file.with_name(f".state.{os.getpid()}.{threading.get_ident()}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(self.state, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, self.state_file)

    @contextlib.contextmanager
    def _locked(self):
        """Read-modify-write under a lock shared with other tokenpace processes on this state."""
        with self.lock, file_lock(self.state_file):
            self.state = self._load()
            yield

    def apk_file(self) -> Path | None:
        """The configured widget APK, only if it is a ZIP with an AndroidManifest.xml (as APKs are)."""
        if not self.cfg.apk.get("path"):
            return None
        path = self.cfg.resolve(self.cfg.apk["path"])
        try:
            with zipfile.ZipFile(path) as apk:
                return path if "AndroidManifest.xml" in apk.namelist() else None
        except (OSError, zipfile.BadZipFile):
            return None

    def sub(self, sid: str) -> dict[str, Any] | None:
        return next((s for s in self.cfg.subscriptions if s["id"] == sid), None)

    # ------------------------------------------------------------ collection

    def collect(self) -> None:
        now = now_ts()
        results: dict[str, dict[str, Any]] = {}
        for s in self.cfg.subscriptions:
            fn = PROVIDERS.get(s["provider"])
            if fn is None:
                continue
            try:
                reading = fn(s, now)
                results[s["id"]] = {"status": "ok", "windows": reading["windows"],
                                    "plan_label": reading.get("plan_label"), "message": reading.get("message"),
                                    "observed_at": iso(now)}
            except ProviderError as exc:
                results[s["id"]] = {"status": exc.status, "message": exc.message}
            except Exception as exc:  # noqa: BLE001 - shown on the page by type only
                results[s["id"]] = {"status": "error", "message": f"Unexpected failure: {type(exc).__name__}."}
        with self._locked():
            for sid, r in results.items():
                old = self.state.get(sid) or {}
                if r["status"] != "ok":
                    # keep the last good numbers, marked old by the view
                    r = {**{k: old[k] for k in ("windows", "observed_at", "plan_label") if k in old}, **r}
                self.state[sid] = {**r, "attempted_at": iso(now)}
            self.last_collect = now
            self._save()

    def request_refresh(self) -> bool:
        if now_ts() - self.last_collect < 30:
            return False
        self.wake.set()
        return True

    def run_refresher(self) -> None:
        while True:
            try:
                self.collect()
            except Exception as exc:  # noqa: BLE001 - keep serving
                print(f"tokenpace: collection failed: {type(exc).__name__}: {exc}", flush=True)
            self.wake.wait(self.cfg.refresh_minutes * 60)
            self.wake.clear()

    # ------------------------------------------------------------ writes

    def ingest_push(self, body: Any) -> dict[str, Any]:
        if not isinstance(body, dict) or not isinstance(body.get("readings"), dict):
            raise ValueError("expected {\"source\": ..., \"readings\": {id: reading}}")
        source = clean_text(body.get("source"), 40) or "push"
        accepted, now = [], now_ts()
        updates = {}
        for sid, r in body["readings"].items():
            s = self.sub(str(sid))
            if not s or s["provider"] != "push":
                raise ValueError(f"{sid}: not a push subscription on this server")
            if not isinstance(r, dict):
                raise ValueError(f"{sid}: reading must be an object")
            entry: dict[str, Any] = {"status": "ok" if not r.get("error") else "error", "source": source,
                                     "received_at": iso(now), "attempted_at": iso(now),
                                     "message": clean_text(r.get("message") or r.get("error"), 200),
                                     "plan_label": clean_text(r.get("plan_label"), 40)}
            if r.get("windows"):
                entry["windows"] = clean_windows(r["windows"])
                observed = parse_time(r.get("observed_at")) or now
                entry["observed_at"] = iso(min(observed, now))
            updates[s["id"]] = entry
            accepted.append(s["id"])
        with self._locked():
            for sid, entry in updates.items():
                old = self.state.get(sid) or {}
                if "windows" not in entry:
                    entry = {**{k: old[k] for k in ("windows", "observed_at") if k in old}, **entry}
                self.state[sid] = entry
            self._save()
        return {"accepted": accepted}

    def report_manual(self, body: Any) -> dict[str, Any]:
        if not isinstance(body, dict):
            raise ValueError("expected a JSON object")
        s = self.sub(str(body.get("id")))
        if not s or s["provider"] != "manual":
            raise ValueError("id must be a manual subscription")
        reset = parse_time(body.get("resets_at"))
        if reset is None:
            raise ValueError("enter the renewal date")
        days = num(body.get("period_days")) or num(s.get("period_days")) or 30
        if not 1 <= days <= 366:
            raise ValueError("period_days must be between 1 and 366")
        used_amount, limit_amount = num(body.get("used_amount")), num(body.get("limit_amount"))
        if used_amount is not None or limit_amount is not None:
            if (used_amount is None or not limit_amount or limit_amount <= 0 or used_amount < 0
                    or max(used_amount, limit_amount) > 1e15):
                raise ValueError("used and total must be numbers from 0 to 10^15, with a total above zero")
            used = used_amount / limit_amount * 100.0
            if not math.isfinite(used) or used > 1000:
                raise ValueError("used is far above the total; check the numbers")
            used = min(used, 100.0)
        else:
            used = num(body.get("used_percent"))
            if used is None or not 0 <= used <= 100:
                raise ValueError("enter the % used (0-100), or the amount used and the total")
        seconds = days * 86400
        kind = "monthly" if 27 <= days <= 32 else "weekly" if days == 7 else "daily" if days == 1 else None
        window = make_window(kind, used, reset, seconds)
        if used_amount is not None:
            window.update({"used_amount": used_amount, "limit_amount": limit_amount,
                           "unit": clean_text(body.get("unit"), 20) or s.get("unit") or "credits"})
        entry = {"status": "ok", "windows": [window], "observed_at": iso(now_ts()), "manual": True,
                 "plan_label": clean_text(body.get("plan"), 40)}
        with self._locked():
            self.state[s["id"]] = entry
            self._save()
        return {"ok": True}

    # ------------------------------------------------------------ views

    def view(self) -> dict[str, Any]:
        now = now_ts()
        with self._locked():
            state = json.loads(json.dumps(self.state))
        subs = []
        for s in self.cfg.subscriptions:
            e = state.get(s["id"]) or {}
            provider = s["provider"]
            status = e.get("status")
            message = e.get("message")
            caveat = None
            observed = parse_time(e.get("observed_at"))
            age = now - observed if observed else None
            if not e:
                status = "unavailable"
                message = {"manual": "Not entered yet: use “Update” on its card.",
                           "push": "Waiting for the first push from the other machine."}.get(provider, "No reading yet.")
            elif status in ("error", "unavailable") and e.get("windows"):
                status = "stale"
            limit = self.cfg.manual_stale_days * 86400 if provider == "manual" else self.cfg.stale_minutes * 60
            if status == "ok" and age is not None and age > limit:
                status = "stale"
                message = message or ("Entered more than %g days ago; update it." % self.cfg.manual_stale_days
                                      if provider == "manual" else "No new reading for a while.")
            if provider == "manual" and e.get("windows") and age is not None:
                caveat = f"Entered by hand, {short_duration(age)} ago"
            elif status == "stale" and age is not None:
                caveat = f"Old reading, {short_duration(age)} ago"
            subs.append({
                "id": s["id"], "name": s["name"], "group": s["group"], "provider": provider,
                "plan": e.get("plan_label") or s.get("plan") or "",
                "use_via": s.get("use_via"), "free": bool(s.get("free")),
                "manual": provider == "manual", "status": status or "unavailable", "message": message,
                "observed_at": e.get("observed_at"), "source": e.get("source"), "caveat": caveat,
                "windows": [relabel(w) for w in e.get("windows") or []],
                "period_days": s.get("period_days") or 30, "unit": s.get("unit"),
            })
        groups = [{"id": g["id"], "label": g["label"], "logo_url": f"logo/{g['id']}" if g.get("logo") else None}
                  for g in self.cfg.groups]
        return {
            "version": __version__, "title": self.cfg.title, "now": iso(now),
            "last_collect": iso(self.last_collect) if self.last_collect else None,
            "refresh_seconds": int(self.cfg.refresh_minutes * 60), "groups": groups,
            "apk_url": "android/tokenpace.apk" if self.apk_file() else None,
            "subscriptions": subs, "advice": build_advice(subs, self.cfg.group_ids(), now),
        }

    def widget_view(self) -> dict[str, Any]:
        view = self.view()
        now = now_ts()
        out: dict[str, Any] = {"version": __version__, "title": view["title"], "now": view["now"], "groups": []}
        if self.cfg.apk.get("version_code") and self.apk_file():
            out["apk"] = {"version_code": int(self.cfg.apk["version_code"]),
                          "version": str(self.cfg.apk.get("version") or ""), "url": "android/tokenpace.apk"}
        for g in view["groups"]:
            items = []
            for r in view["advice"]["groups"].get(g["id"], []):
                reset = parse_time(r["period"]["resets_at"])
                items.append({
                    "rank": r["rank"], "name": r["name"], "window": r["period"]["label"], "level": r["level"],
                    "verdict": r["verdict"], "used_percent": r["period"]["used_percent"],
                    "elapsed_percent": r["period"]["elapsed_percent"], "need": r["need"], "free": r["free"],
                    "resets_epoch": int(reset) if reset else None,
                    "released_epoch": int(parse_time(r["released_at"])) if r["released_at"] else None,
                    "note": r["caveat"],
                })
            out["groups"].append({"id": g["id"], "label": g["label"], "logo_url": g["logo_url"], "items": items})
        return out


class Handler(BaseHTTPRequestHandler):
    app: App
    server_version = f"tokenpace/{__version__}"
    timeout = 10   # seconds per socket read

    def setup(self) -> None:
        super().setup()
        # The per-read timeout restarts with every byte, so a client dripping one byte at a time
        # could hold a slot forever; this watchdog ends any request that takes too long overall.
        self._watchdog = threading.Timer(REQUEST_DEADLINE, self._cut_off)
        self._watchdog.daemon = True
        self._watchdog.start()

    def _cut_off(self) -> None:
        with contextlib.suppress(OSError):
            self.connection.shutdown(socket.SHUT_RDWR)

    def finish(self) -> None:
        self._watchdog.cancel()
        with contextlib.suppress(OSError):
            super().finish()

    def log_message(self, fmt: str, *args: Any) -> None:  # quieter than the default
        if os.environ.get("TOKENPACE_LOG_REQUESTS"):
            super().log_message(fmt, *args)

    # ------------------------------------------------------------ helpers

    def send_body(self, code: int, body: bytes, ctype: str, extra: dict[str, str] | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_json(self, code: int, data: Any) -> None:
        self.send_body(code, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def authorized(self) -> bool:
        token = self.app.cfg.token
        if not token:
            return True
        given = self.headers.get("Authorization", "")
        given = given[7:] if given.startswith("Bearer ") else ""
        return hmac.compare_digest(given.encode(), token.encode())

    def host_allowed(self) -> bool:
        """Without a token, refuse Host names that could come from DNS rebinding: a web page on
        evil.example that rebinds its name to this machine. IP literals, localhost, this
        machine's own name and [server] allowed_hosts are fine; with a token any Host is."""
        cfg = self.app.cfg
        if cfg.token or "*" in cfg.allowed_hosts:
            return True
        raw = (self.headers.get("Host") or "").strip().lower()
        host = raw[1:raw.index("]")] if raw.startswith("[") and "]" in raw else raw.rsplit(":", 1)[0] if raw.count(":") == 1 else raw
        if not host:
            return False
        try:
            ipaddress.ip_address(host)
            return True
        except ValueError:
            pass
        names = {"localhost", socket.gethostname().lower(), socket.getfqdn().lower(), cfg.host.lower()}
        names.update(h.lower() for h in cfg.allowed_hosts)
        return host in names or host.endswith(".localhost")

    def read_json(self) -> Any:
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            raise ValueError("Content-Type must be application/json")
        if self.headers.get("Transfer-Encoding"):
            raise ValueError("Transfer-Encoding is not supported; send Content-Length")
        raw = (self.headers.get("Content-Length") or "0").strip()
        if not raw.isdigit():
            raise ValueError("invalid Content-Length")
        length = int(raw)
        if length > MAX_BODY:
            raise ValueError("body too large")
        return json.loads(self.rfile.read(length) or b"{}")

    def same_origin(self) -> bool:
        """Browsers send Origin on cross-site POSTs; refuse any that is not this server."""
        origin = self.headers.get("Origin")
        if not origin:
            return True   # curl, tokenpace push, the Android widget
        return urlsplit(origin).netloc.lower() == (self.headers.get("Host") or "").strip().lower()

    # ------------------------------------------------------------ routes

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        try:
            self.route_get(urlsplit(self.path).path)
        except Exception as exc:  # noqa: BLE001 - never leak details to the client
            print(f"tokenpace: GET failed: {type(exc).__name__}", flush=True)
            self.send_json(500, {"error": "internal error"})

    def route_get(self, path: str) -> None:
        if not self.host_allowed():
            self.send_json(421, {"error": "unknown Host; add it to [server] allowed_hosts or set a token"})
            return
        if path in ("/", "/index.html"):
            page = resources.files("tokenpace").joinpath("web/index.html").read_bytes()
            self.send_body(200, page, "text/html; charset=utf-8", {"Content-Security-Policy": PAGE_CSP})
        elif path == "/app.js":
            script = resources.files("tokenpace").joinpath("web/app.js").read_bytes()
            self.send_body(200, script, "text/javascript; charset=utf-8")
        elif path == "/healthz":
            self.send_json(200, {"ok": True, "version": __version__})
        elif path.startswith("/logo/"):
            self.send_logo(path[len("/logo/"):])
        elif path == "/android/tokenpace.apk" and self.app.apk_file():
            self.send_file(self.app.apk_file(), "application/vnd.android.package-archive")
        elif path in ("/api/usage", "/api/widget"):
            if not self.authorized():
                self.send_json(401, {"error": "token required"})
                return
            self.send_json(200, self.app.view() if path == "/api/usage" else self.app.widget_view())
        else:
            self.send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        try:
            self.route_post(urlsplit(self.path).path)
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError, RecursionError) as exc:
            self.send_json(400, {"error": str(exc)[:300]})
        except Exception as exc:  # noqa: BLE001 - never leak details to the client
            print(f"tokenpace: POST failed: {type(exc).__name__}", flush=True)
            self.send_json(500, {"error": "internal error"})

    def route_post(self, path: str) -> None:
        if not self.host_allowed():
            self.send_json(421, {"error": "unknown Host; add it to [server] allowed_hosts or set a token"})
            return
        if not self.same_origin():
            self.send_json(403, {"error": "cross-origin request refused"})
            return
        if not self.authorized():
            self.send_json(401, {"error": "token required"})
            return
        if path == "/api/refresh":
            self.read_json()
            self.send_json(202, {"queued": self.app.request_refresh()})
        elif path == "/api/report":
            self.send_json(200, self.app.report_manual(self.read_json()))
        elif path == "/api/push":
            if not self.app.cfg.token:
                self.send_json(403, {"error": "set [server] token (or TOKENPACE_TOKEN) to accept pushes"})
                return
            self.send_json(200, self.app.ingest_push(self.read_json()))
        else:
            self.send_json(404, {"error": "not found"})

    def send_logo(self, gid: str) -> None:
        g = next((g for g in self.app.cfg.groups if g["id"] == gid and g.get("logo")), None)
        if not g:
            self.send_json(404, {"error": "no logo"})
            return
        self.send_file(self.app.cfg.resolve(g["logo"]), None, MAX_LOGO, cache=True)

    def send_file(self, path: Path, ctype: str | None, limit: int = 64 * 1024 * 1024, cache: bool = False) -> None:
        try:
            data = path.read_bytes()
        except OSError:
            self.send_json(404, {"error": "file not found"})
            return
        if len(data) > limit:
            self.send_json(413, {"error": "file too large"})
            return
        ctype = ctype or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if ctype not in ("image/png", "image/jpeg", "image/webp", "image/svg+xml", "image/gif",
                         "application/vnd.android.package-archive"):
            self.send_json(415, {"error": "unsupported file type"})
            return
        extra = {"Cache-Control": "max-age=3600"} if cache else None
        if ctype == "image/svg+xml":
            extra = {**(extra or {}), "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"}
        self.send_body(200, data, ctype, extra)


class BoundedServer(ThreadingHTTPServer):
    """ThreadingHTTPServer with a cap on concurrent connections; extra ones are closed at once."""
    daemon_threads = True

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.slots = threading.BoundedSemaphore(MAX_CONNECTIONS)

    def process_request(self, request: Any, client_address: Any) -> None:
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


def serve(cfg: Config, collect: bool = True) -> None:
    app = App(cfg)
    handler = type("BoundHandler", (Handler,), {"app": app})
    httpd = BoundedServer((cfg.host, cfg.port), handler)
    if collect:
        threading.Thread(target=app.run_refresher, daemon=True, name="refresher").start()
    shown = "localhost" if cfg.host in ("127.0.0.1", "::1") else cfg.host
    print(f"tokenpace {__version__} on http://{shown}:{cfg.port}/  (config: {cfg.path or 'demo'})", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
