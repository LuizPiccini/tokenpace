r"""Token Pace Mini for Windows: a floating pill above the taskbar and a tray icon that show
which AI plan to use first. Python standard library only (ctypes, GDI+), so it runs wherever
Python does, including PCs with Smart App Control, which blocks unsigned .exe files.

    pythonw -m tokenpace mini                  # no console window
    tokenpace mini --snapshot out.png --server http://localhost:8787 --expanded

It reads /api/widget like the Android widget and shares its settings file with the .exe
build (%APPDATA%\TokenPace\mini.json, token encrypted with Windows DPAPI).
"""
from __future__ import annotations

import base64
import ctypes
import http.client
import json
import math
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from ctypes import wintypes as W
from typing import Any

from . import __version__

MAX_BODY = 1024 * 1024
MAX_ROWS = 50   # per group: more can't be a real list of plans, and each row costs drawing time
TOTAL_TIMEOUT = 30.0
WIDTH, PAD, GAP = 340.0, 14.0, 8.0
POLL_SECONDS, TICK_SECONDS = 120, 30


class MiniError(Exception):
    pass


# ---------------------------------------------------------------- data (platform independent)

def normalize(server: str | None) -> str:
    s = (server or "").strip()
    if not s:
        return ""
    if "://" not in s:
        s = "http://" + s
    scheme, rest = s.split("://", 1)
    s = scheme.lower() + "://" + rest.rstrip("/")   # a lowercase scheme: fetch() decides the proxy on it
    if s.lower().endswith("/api/widget"):
        s = s[: -len("/api/widget")]
    u = urllib.parse.urlsplit(s)
    try:
        u.port   # raises on a port that isn't a number
    except ValueError:
        return ""
    # Only web addresses: the value is later opened with the shell ("Open page").
    return s if u.scheme in ("http", "https") and u.hostname else ""


def _num(d: dict, key: str, default: float | None) -> float | None:
    v = d.get(key)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return default   # JSON numbers only; strings such as "NaN" are refused
    v = float(v)
    return v if math.isfinite(v) else default


def parse(body: str) -> list[dict[str, Any]]:
    """The /api/widget document as [{"label", "items": [plan...]}]. Accepts the current shape
    (groups as a list) and older private servers' shape (groups as {label: rows})."""
    try:
        root = json.loads(body)
    except ValueError:
        raise MiniError("The server did not answer with Token Pace data") from None
    if not isinstance(root, dict) or "groups" not in root:
        raise MiniError("The server did not answer with Token Pace data")
    raw = root["groups"]
    pairs: list[tuple[str, Any]] = []
    if isinstance(raw, list):
        for g in raw:
            if isinstance(g, dict):
                pairs.append((str(g.get("label") or g.get("id") or ""), g.get("items")))
    elif isinstance(raw, dict):
        pairs = [(str(k), v) for k, v in raw.items()]
    groups = []
    for label, items in pairs:
        plans = []
        for i, r in enumerate((items if isinstance(items, list) else [])[:MAX_ROWS]):
            if not isinstance(r, dict):
                continue
            elapsed = _num(r, "elapsed_percent", None)
            plans.append({
                "rank": int(_num(r, "rank", i + 1) or i + 1),
                "name": str(r.get("name") or "?"),
                "window": str(r.get("window") or ""),
                "level": str(r.get("level") or "on_pace"),
                "verdict": str(r.get("verdict") or ""),
                "used": max(0.0, min(100.0, _num(r, "used_percent", 0.0) or 0.0)),
                "elapsed": None if elapsed is None else max(0.0, min(100.0, elapsed)),
                "need": _num(r, "need", 0.0) or 0.0,
                "free": r.get("free") is True,
                "resets": _num(r, "resets_epoch", None),
                "released": _num(r, "released_epoch", None),
            })
        groups.append({"label": label, "items": plans})
    return groups


def top_plan(groups: list[dict]) -> dict | None:
    """The plan the pill shows: the first group's "use first", else its top row."""
    for g in groups:
        for p in g["items"]:
            if p["level"] == "use":
                return p
        if g["items"]:
            return g["items"][0]
    return None


def duration(seconds: float) -> str:
    if seconds <= 0:
        return "now"
    minutes = int(seconds // 60)
    if minutes < 1:
        return "under 1 min"
    d, h, m = minutes // 1440, (minutes % 1440) // 60, minutes % 60
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {m:02d}m"
    return f"{m} min"


def pace_text(p: dict) -> str:
    if p["free"]:
        return "free"
    if p["level"] == "blocked":
        return "used up"
    return "99×+" if p["need"] >= 99 else f"{p['need']:.1f}×"


def when_text(p: dict, now: float | None = None) -> str:
    now = time.time() if now is None else now
    if p["level"] == "blocked" and p["released"]:
        return "frees in " + duration(p["released"] - now)
    return "resets in " + duration(p["resets"] - now) if p["resets"] else "reset unknown"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None   # never resend the token to another host


def fetch(server: str, token: str = "") -> list[dict]:
    if token and any(not 33 <= ord(c) <= 126 for c in token):
        raise MiniError("The token contains spaces or unusual characters")
    url = server + "/api/widget"
    conns: list[Any] = []

    class _Http(urllib.request.HTTPHandler):
        def http_open(self, req: Any) -> Any:
            return self.do_open(lambda host, **kw: conns.append(http.client.HTTPConnection(host, **kw)) or conns[-1], req)

    class _Https(urllib.request.HTTPSHandler):
        def https_open(self, req: Any) -> Any:
            return self.do_open(lambda host, **kw: conns.append(http.client.HTTPSConnection(host, **kw)) or conns[-1],
                                req, context=self._context)

    handlers: list[Any] = [_NoRedirect(), _Http(), _Https()]
    if urllib.parse.urlsplit(url).scheme == "http":
        handlers.append(urllib.request.ProxyHandler({}))   # no proxy may see the token in clear text
    opener = urllib.request.build_opener(*handlers)
    timed_out = threading.Event()

    def cut() -> None:
        # The whole exchange is capped, connecting and headers included: a server that drips
        # bytes would otherwise keep every per-read timeout alive.
        timed_out.set()
        for c in conns:
            try:
                if c.sock is not None:
                    c.sock.shutdown(2)
            except OSError:
                pass

    timer = threading.Timer(TOTAL_TIMEOUT, cut)
    timer.daemon = True
    timer.start()
    try:
        return _exchange(opener, url, token, timed_out)
    finally:
        timer.cancel()


def _exchange(opener: Any, url: str, token: str, timed_out: threading.Event) -> list[dict]:
    headers = {"Accept": "application/json", "User-Agent": f"tokenpace-mini/{__version__}"}
    if token:
        headers["Authorization"] = "Bearer " + token
    try:
        with opener.open(urllib.request.Request(url, headers=headers), timeout=15) as resp:
            if resp.status != 200:
                raise MiniError(f"Server answered HTTP {resp.status}")
            length = resp.headers.get("Content-Length")
            if length and length.isdigit() and int(length) > MAX_BODY:
                raise MiniError("Server answer too large")
            chunks, total = [], 0
            while True:
                chunk = resp.read1(16384)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_BODY:
                    raise MiniError("Server answer too large")
                chunks.append(chunk)
    except urllib.error.HTTPError as exc:
        exc.close()
        if exc.code == 401:
            raise MiniError("Wrong or missing token") from None
        if exc.code == 421:
            raise MiniError("Server refused this address: set a token or allowed_hosts") from None
        raise MiniError(f"Server answered HTTP {exc.code}") from None
    except MiniError:
        raise
    except (urllib.error.URLError, OSError, http.client.HTTPException, ValueError):
        if timed_out.is_set():
            raise MiniError("Server too slow to answer") from None
        raise MiniError("Can't reach the server") from None
    if timed_out.is_set():
        raise MiniError("Server too slow to answer")
    return parse(b"".join(chunks).decode("utf-8", "replace"))


# ---------------------------------------------------------------- Windows plumbing

if sys.platform == "win32":
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    shell32 = ctypes.WinDLL("shell32")
    crypt32 = ctypes.WinDLL("crypt32")
    gdiplus = ctypes.WinDLL("gdiplus")

    LRESULT = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, W.HWND, W.UINT, W.WPARAM, W.LPARAM)
    HANDLE = ctypes.c_void_p

    def _f(dll: Any, name: str, res: Any, *args: Any) -> Any:
        fn = getattr(dll, name)
        fn.restype, fn.argtypes = res, list(args)
        return fn

    class WNDCLASSEXW(ctypes.Structure):
        _fields_ = [("cbSize", W.UINT), ("style", W.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                    ("cbWndExtra", ctypes.c_int), ("hInstance", HANDLE), ("hIcon", HANDLE), ("hCursor", HANDLE),
                    ("hbrBackground", HANDLE), ("lpszMenuName", W.LPCWSTR), ("lpszClassName", W.LPCWSTR), ("hIconSm", HANDLE)]

    class MSG(ctypes.Structure):
        _fields_ = [("hwnd", W.HWND), ("message", W.UINT), ("wParam", W.WPARAM), ("lParam", W.LPARAM),
                    ("time", W.DWORD), ("pt", W.POINT), ("lPrivate", W.DWORD)]

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", W.DWORD), ("biWidth", W.LONG), ("biHeight", W.LONG), ("biPlanes", W.WORD),
                    ("biBitCount", W.WORD), ("biCompression", W.DWORD), ("biSizeImage", W.DWORD),
                    ("biXPelsPerMeter", W.LONG), ("biYPelsPerMeter", W.LONG), ("biClrUsed", W.DWORD), ("biClrImportant", W.DWORD)]

    class BLENDFUNCTION(ctypes.Structure):
        _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                    ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", W.DWORD), ("rcMonitor", W.RECT), ("rcWork", W.RECT), ("dwFlags", W.DWORD)]

    class GUID(ctypes.Structure):
        _fields_ = [("data", ctypes.c_ubyte * 16)]

        @classmethod
        def of(cls, text: str) -> "GUID":
            g = cls()
            ctypes.memmove(g.data, uuid.UUID(text).bytes_le, 16)
            return g

    class NOTIFYICONDATAW(ctypes.Structure):
        _fields_ = [("cbSize", W.DWORD), ("hWnd", W.HWND), ("uID", W.UINT), ("uFlags", W.UINT),
                    ("uCallbackMessage", W.UINT), ("hIcon", HANDLE), ("szTip", ctypes.c_wchar * 128),
                    ("dwState", W.DWORD), ("dwStateMask", W.DWORD), ("szInfo", ctypes.c_wchar * 256),
                    ("uVersion", W.UINT), ("szInfoTitle", ctypes.c_wchar * 64), ("dwInfoFlags", W.DWORD),
                    ("guidItem", GUID), ("hBalloonIcon", HANDLE)]

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", W.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    class GdiplusStartupInput(ctypes.Structure):
        _fields_ = [("GdiplusVersion", ctypes.c_uint32), ("DebugEventCallback", ctypes.c_void_p),
                    ("SuppressBackgroundThread", W.BOOL), ("SuppressExternalCodecs", W.BOOL)]

    class RectF(ctypes.Structure):
        _fields_ = [("X", ctypes.c_float), ("Y", ctypes.c_float), ("Width", ctypes.c_float), ("Height", ctypes.c_float)]

    class PointF(ctypes.Structure):
        _fields_ = [("X", ctypes.c_float), ("Y", ctypes.c_float)]

    RegisterClassExW = _f(user32, "RegisterClassExW", W.ATOM, ctypes.POINTER(WNDCLASSEXW))
    CreateWindowExW = _f(user32, "CreateWindowExW", W.HWND, W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD, ctypes.c_int,
                         ctypes.c_int, ctypes.c_int, ctypes.c_int, W.HWND, HANDLE, HANDLE, ctypes.c_void_p)
    DefWindowProcW = _f(user32, "DefWindowProcW", LRESULT, W.HWND, W.UINT, W.WPARAM, W.LPARAM)
    GetMessageW = _f(user32, "GetMessageW", W.BOOL, ctypes.POINTER(MSG), W.HWND, W.UINT, W.UINT)
    TranslateMessage = _f(user32, "TranslateMessage", W.BOOL, ctypes.POINTER(MSG))
    DispatchMessageW = _f(user32, "DispatchMessageW", LRESULT, ctypes.POINTER(MSG))
    PostMessageW = _f(user32, "PostMessageW", W.BOOL, W.HWND, W.UINT, W.WPARAM, W.LPARAM)
    PostQuitMessage = _f(user32, "PostQuitMessage", None, ctypes.c_int)
    DestroyWindow = _f(user32, "DestroyWindow", W.BOOL, W.HWND)
    ShowWindow = _f(user32, "ShowWindow", W.BOOL, W.HWND, ctypes.c_int)
    IsWindowVisible = _f(user32, "IsWindowVisible", W.BOOL, W.HWND)
    SetWindowPos = _f(user32, "SetWindowPos", W.BOOL, W.HWND, W.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, W.UINT)
    UpdateLayeredWindow = _f(user32, "UpdateLayeredWindow", W.BOOL, W.HWND, W.HDC, ctypes.POINTER(W.POINT),
                             ctypes.POINTER(W.SIZE), W.HDC, ctypes.POINTER(W.POINT), W.COLORREF,
                             ctypes.POINTER(BLENDFUNCTION), W.DWORD)
    SetTimer = _f(user32, "SetTimer", ctypes.c_size_t, W.HWND, ctypes.c_size_t, W.UINT, ctypes.c_void_p)
    SetCapture = _f(user32, "SetCapture", W.HWND, W.HWND)
    ReleaseCapture = _f(user32, "ReleaseCapture", W.BOOL)
    GetCursorPos = _f(user32, "GetCursorPos", W.BOOL, ctypes.POINTER(W.POINT))
    LoadCursorW = _f(user32, "LoadCursorW", HANDLE, HANDLE, ctypes.c_void_p)
    SetCursor = _f(user32, "SetCursor", HANDLE, HANDLE)
    GetDC = _f(user32, "GetDC", W.HDC, W.HWND)
    ReleaseDC = _f(user32, "ReleaseDC", ctypes.c_int, W.HWND, W.HDC)
    MonitorFromPoint = _f(user32, "MonitorFromPoint", HANDLE, W.POINT, W.DWORD)
    GetMonitorInfoW = _f(user32, "GetMonitorInfoW", W.BOOL, HANDLE, ctypes.POINTER(MONITORINFO))
    CreatePopupMenu = _f(user32, "CreatePopupMenu", HANDLE)
    AppendMenuW = _f(user32, "AppendMenuW", W.BOOL, HANDLE, W.UINT, ctypes.c_size_t, W.LPCWSTR)
    TrackPopupMenu = _f(user32, "TrackPopupMenu", ctypes.c_int, HANDLE, W.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, W.HWND, ctypes.c_void_p)
    DestroyMenu = _f(user32, "DestroyMenu", W.BOOL, HANDLE)
    SetForegroundWindow = _f(user32, "SetForegroundWindow", W.BOOL, W.HWND)
    RegisterWindowMessageW = _f(user32, "RegisterWindowMessageW", W.UINT, W.LPCWSTR)
    DestroyIcon = _f(user32, "DestroyIcon", W.BOOL, HANDLE)
    MessageBoxW = _f(user32, "MessageBoxW", ctypes.c_int, W.HWND, W.LPCWSTR, W.LPCWSTR, W.UINT)
    CreateCompatibleDC = _f(gdi32, "CreateCompatibleDC", W.HDC, W.HDC)
    CreateDIBSection = _f(gdi32, "CreateDIBSection", HANDLE, W.HDC, ctypes.POINTER(BITMAPINFOHEADER), W.UINT,
                          ctypes.POINTER(ctypes.c_void_p), HANDLE, W.DWORD)
    SelectObject = _f(gdi32, "SelectObject", HANDLE, W.HDC, HANDLE)
    DeleteObject = _f(gdi32, "DeleteObject", W.BOOL, HANDLE)
    DeleteDC = _f(gdi32, "DeleteDC", W.BOOL, W.HDC)
    GetDeviceCaps = _f(gdi32, "GetDeviceCaps", ctypes.c_int, W.HDC, ctypes.c_int)
    GetModuleHandleW = _f(kernel32, "GetModuleHandleW", HANDLE, W.LPCWSTR)
    CreateMutexW = _f(kernel32, "CreateMutexW", HANDLE, ctypes.c_void_p, W.BOOL, W.LPCWSTR)
    LocalFree = _f(kernel32, "LocalFree", HANDLE, HANDLE)
    Shell_NotifyIconW = _f(shell32, "Shell_NotifyIconW", W.BOOL, W.DWORD, ctypes.POINTER(NOTIFYICONDATAW))
    ShellExecuteW = _f(shell32, "ShellExecuteW", HANDLE, W.HWND, W.LPCWSTR, W.LPCWSTR, W.LPCWSTR, W.LPCWSTR, ctypes.c_int)
    CryptProtectData = _f(crypt32, "CryptProtectData", W.BOOL, ctypes.POINTER(DATA_BLOB), W.LPCWSTR, ctypes.c_void_p,
                          ctypes.c_void_p, ctypes.c_void_p, W.DWORD, ctypes.POINTER(DATA_BLOB))
    CryptUnprotectData = _f(crypt32, "CryptUnprotectData", W.BOOL, ctypes.POINTER(DATA_BLOB), ctypes.c_void_p,
                            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, W.DWORD, ctypes.POINTER(DATA_BLOB))

    P = ctypes.c_void_p
    PP = ctypes.POINTER(ctypes.c_void_p)
    F = ctypes.c_float
    gp = {name: _f(gdiplus, name, ctypes.c_int, *args) for name, args in {
        "GdiplusStartup": (ctypes.POINTER(ctypes.c_size_t), ctypes.POINTER(GdiplusStartupInput), ctypes.c_void_p),
        "GdipCreateBitmapFromScan0": (ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_void_p, PP),
        "GdipGetImageGraphicsContext": (P, PP),
        "GdipDeleteGraphics": (P,),
        "GdipDisposeImage": (P,),
        "GdipSetSmoothingMode": (P, ctypes.c_int),
        "GdipSetPixelOffsetMode": (P, ctypes.c_int),
        "GdipSetTextRenderingHint": (P, ctypes.c_int),
        "GdipGraphicsClear": (P, ctypes.c_uint32),
        "GdipScaleWorldTransform": (P, F, F, ctypes.c_int),
        "GdipCreatePath": (ctypes.c_int, PP),
        "GdipDeletePath": (P,),
        "GdipAddPathArc": (P, F, F, F, F, F, F),
        "GdipAddPathLine": (P, F, F, F, F),
        "GdipStartPathFigure": (P,),
        "GdipClosePathFigure": (P,),
        "GdipFillPath": (P, P, P),
        "GdipDrawPath": (P, P, P),
        "GdipFillRectangle": (P, P, F, F, F, F),
        "GdipCreateSolidFill": (ctypes.c_uint32, PP),
        "GdipCreateHatchBrush": (ctypes.c_int, ctypes.c_uint32, ctypes.c_uint32, PP),
        "GdipCreateLineBrush": (ctypes.POINTER(PointF), ctypes.POINTER(PointF), ctypes.c_uint32, ctypes.c_uint32, ctypes.c_int, PP),
        "GdipSetLinePresetBlend": (P, ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_float), ctypes.c_int),
        "GdipDeleteBrush": (P,),
        "GdipCreatePen1": (ctypes.c_uint32, F, ctypes.c_int, PP),
        "GdipSetPenStartCap": (P, ctypes.c_int),
        "GdipSetPenEndCap": (P, ctypes.c_int),
        "GdipSetPenLineJoin": (P, ctypes.c_int),
        "GdipDeletePen": (P,),
        "GdipSetClipPath": (P, P, ctypes.c_int),
        "GdipSetClipRect": (P, F, F, F, F, ctypes.c_int),
        "GdipResetClip": (P,),
        "GdipCreateFontFamilyFromName": (W.LPCWSTR, P, PP),
        "GdipDeleteFontFamily": (P,),
        "GdipGetEmHeight": (P, ctypes.c_int, ctypes.POINTER(ctypes.c_uint16)),
        "GdipGetCellAscent": (P, ctypes.c_int, ctypes.POINTER(ctypes.c_uint16)),
        "GdipGetCellDescent": (P, ctypes.c_int, ctypes.POINTER(ctypes.c_uint16)),
        "GdipCreateFont": (P, F, ctypes.c_int, ctypes.c_int, PP),
        "GdipDeleteFont": (P,),
        "GdipStringFormatGetGenericTypographic": (PP,),
        "GdipCloneStringFormat": (P, PP),
        "GdipDeleteStringFormat": (P,),
        "GdipSetStringFormatFlags": (P, ctypes.c_int),
        "GdipSetStringFormatTrimming": (P, ctypes.c_int),
        "GdipDrawString": (P, W.LPCWSTR, ctypes.c_int, P, ctypes.POINTER(RectF), P, P),
        "GdipMeasureString": (P, W.LPCWSTR, ctypes.c_int, P, ctypes.POINTER(RectF), P, ctypes.POINTER(RectF),
                              ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)),
        "GdipSaveImageToFile": (P, W.LPCWSTR, ctypes.POINTER(GUID), ctypes.c_void_p),
        "GdipCreateHICONFromBitmap": (P, PP),
    }.items()}

    _gdiplus_token = ctypes.c_size_t()

    def gdiplus_start() -> None:
        if not _gdiplus_token.value:
            inp = GdiplusStartupInput(1, None, False, False)
            if gp["GdiplusStartup"](ctypes.byref(_gdiplus_token), ctypes.byref(inp), None) != 0:
                raise OSError("GDI+ failed to start")

    def protect(text: str) -> str:
        if not text:
            return ""
        raw = text.encode("utf-8")
        buf = ctypes.create_string_buffer(raw, len(raw))
        inp, out = DATA_BLOB(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), DATA_BLOB()
        if not CryptProtectData(ctypes.byref(inp), "", None, None, None, 1, ctypes.byref(out)):
            return ""
        try:
            return base64.b64encode(ctypes.string_at(out.pbData, out.cbData)).decode()
        finally:
            LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))

    def unprotect(stored: str) -> str:
        if not stored:
            return ""
        try:
            raw = base64.b64decode(stored)
        except ValueError:
            return ""
        buf = ctypes.create_string_buffer(raw, len(raw))
        inp, out = DATA_BLOB(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), DATA_BLOB()
        if not CryptUnprotectData(ctypes.byref(inp), None, None, None, None, 1, ctypes.byref(out)):
            return ""
        try:
            return ctypes.string_at(out.pbData, out.cbData).decode("utf-8", "replace")
        finally:
            LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


# ---------------------------------------------------------------- settings

def settings_path() -> str:
    return os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "TokenPace", "mini.json")


class Settings:
    """Shared with the .exe build: same file, same fields, positions in 1/96-inch units."""

    def __init__(self) -> None:
        self.server, self.token = "", ""
        self.left: float | None = None
        self.top: float | None = None
        self.expanded = False

    @classmethod
    def load(cls) -> "Settings":
        s = cls()
        try:
            with open(settings_path(), encoding="utf-8-sig") as f:
                d = json.load(f)
        except (OSError, ValueError):
            d = {}
        if isinstance(d, dict):
            s.server = normalize(d.get("server") if isinstance(d.get("server"), str) else "")
            s.token = unprotect(d["token"]) if isinstance(d.get("token"), str) else ""
            s.left = _num(d, "left", None)
            s.top = _num(d, "top", None)
            s.expanded = d.get("expanded") is True
        return s

    def save(self) -> None:
        d: dict[str, Any] = {"server": self.server, "token": protect(self.token), "expanded": self.expanded}
        if self.left is not None and self.top is not None:
            d["left"], d["top"] = round(self.left), round(self.top)
        path = settings_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(d, f)
            os.replace(tmp, path)
        except OSError:
            pass


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def start_command() -> str:
    exe = sys.executable
    w = os.path.join(os.path.dirname(exe), "pythonw.exe")
    py = w if os.path.exists(w) else exe
    parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    import site
    installed = {os.path.normcase(os.path.abspath(p)) for p in site.getsitepackages() + [site.getusersitepackages()]}
    if os.path.normcase(parent) in installed:
        return f'"{py}" -m tokenpace mini'
    # Run from a clone: at logon the working directory isn't the clone, so say where the package is.
    return f'"{py}" -c "import sys; sys.path.insert(0, r\'{parent}\'); from tokenpace.mini import run; run([])"'


def starts_with_windows() -> bool:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            return winreg.QueryValueEx(k, "TokenPaceMini")[0] == start_command()   # not an older .exe entry
    except OSError:
        return False


def set_starts_with_windows(on: bool) -> None:
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
        if on:
            winreg.SetValueEx(k, "TokenPaceMini", 0, winreg.REG_SZ, start_command())
        else:
            try:
                winreg.DeleteValue(k, "TokenPaceMini")
            except OSError:
                pass


# ---------------------------------------------------------------- drawing

def argb(hex_: str, alpha: int = 255) -> int:
    h = hex_.lstrip("#")
    return (alpha << 24) | int(h, 16)


C = {
    "pill": argb("#1F2023"), "panel": argb("#232427"), "edge": argb("#34363B"), "ink": argb("#F2F3F5"),
    "soft": argb("#B4B7BD"), "muted": argb("#8A8E95"), "track": argb("#33363D"), "used": argb("#7E9BD0"),
    "link": argb("#9DB4E0"), "warn": argb("#F5C46B"), "slack": "#4FB8A6", "ahead": "#F5A623",
}
LEVEL_INK = {"use": "#8FD6A0", "lean_use": "#8FD6A0", "save": "#F5C46B", "blocked": "#FF7A6E", "free": "#9DB4E0"}
CHIP = {  # fill, stroke, ink
    "use": ("#BFE6B0", None, "#18301B"), "lean_use": (None, "#5FAE7A", "#8FD6A0"),
    "save": ("#F5C46B", None, "#2A1A00"), "blocked": ("#F0453A", None, "#FFFFFF"),
    "free": ("#34363B", None, "#9DB4E0"),
}


def level_ink(level: str) -> int:
    return argb(LEVEL_INK.get(level, "#D6D8DC"))


class Canvas:
    """GDI+ drawing in 1/96-inch units on a premultiplied ARGB bitmap."""
    REGULAR, SEMIBOLD, BOLD = "Segoe UI", "Segoe UI Semibold", "Segoe UI Bold"

    def __init__(self, bitmap: Any, scale: float) -> None:
        self.g = ctypes.c_void_p()
        gp["GdipGetImageGraphicsContext"](bitmap, ctypes.byref(self.g))
        gp["GdipSetSmoothingMode"](self.g, 4)          # antialias
        gp["GdipSetPixelOffsetMode"](self.g, 4)        # half
        gp["GdipSetTextRenderingHint"](self.g, 4)      # antialias: ClearType can't blend with per-pixel alpha
        gp["GdipGraphicsClear"](self.g, 0)
        gp["GdipScaleWorldTransform"](self.g, scale, scale, 0)
        self._fonts: dict[tuple, tuple] = {}
        fmt = ctypes.c_void_p()
        gp["GdipStringFormatGetGenericTypographic"](ctypes.byref(fmt))
        self.fmt = ctypes.c_void_p()
        gp["GdipCloneStringFormat"](fmt, ctypes.byref(self.fmt))
        gp["GdipSetStringFormatFlags"](self.fmt, 0x1000 | 0x800 | 0x4000)   # no wrap, trailing spaces, no clip
        self.ellipsis = ctypes.c_void_p()
        gp["GdipCloneStringFormat"](fmt, ctypes.byref(self.ellipsis))
        gp["GdipSetStringFormatFlags"](self.ellipsis, 0x1000 | 0x800)
        gp["GdipSetStringFormatTrimming"](self.ellipsis, 3)

    def close(self) -> None:
        for font, family, _, _ in self._fonts.values():
            gp["GdipDeleteFont"](font)
            gp["GdipDeleteFontFamily"](family)
        gp["GdipDeleteStringFormat"](self.fmt)
        gp["GdipDeleteStringFormat"](self.ellipsis)
        gp["GdipDeleteGraphics"](self.g)

    def font(self, face: str, size: float) -> tuple:
        key = (face, size)
        if key not in self._fonts:
            family = ctypes.c_void_p()
            style = 0
            if gp["GdipCreateFontFamilyFromName"](face, None, ctypes.byref(family)) != 0:
                style = 1 if face != self.REGULAR else 0
                gp["GdipCreateFontFamilyFromName"](self.REGULAR, None, ctypes.byref(family))
            em, asc, desc = ctypes.c_uint16(), ctypes.c_uint16(), ctypes.c_uint16()
            gp["GdipGetEmHeight"](family, style, ctypes.byref(em))
            gp["GdipGetCellAscent"](family, style, ctypes.byref(asc))
            gp["GdipGetCellDescent"](family, style, ctypes.byref(desc))
            font = ctypes.c_void_p()
            gp["GdipCreateFont"](family, ctypes.c_float(size), style, 2, ctypes.byref(font))   # UnitPixel
            k = size / max(1, em.value)
            self._fonts[key] = (font, family, asc.value * k, desc.value * k)
        return self._fonts[key]

    def _brush(self, color: int) -> Any:
        b = ctypes.c_void_p()
        gp["GdipCreateSolidFill"](color, ctypes.byref(b))
        return b

    def _round_path(self, x: float, y: float, w: float, h: float, r: float) -> Any:
        path = ctypes.c_void_p()
        gp["GdipCreatePath"](0, ctypes.byref(path))
        r = max(0.0, min(r, w / 2, h / 2))
        d = 2 * r
        if r <= 0:
            for a, b, c, e in ((x, y, x + w, y), (x + w, y, x + w, y + h), (x + w, y + h, x, y + h)):
                gp["GdipAddPathLine"](path, a, b, c, e)
        else:
            gp["GdipAddPathArc"](path, x, y, d, d, 180, 90)
            gp["GdipAddPathArc"](path, x + w - d, y, d, d, 270, 90)
            gp["GdipAddPathArc"](path, x + w - d, y + h - d, d, d, 0, 90)
            gp["GdipAddPathArc"](path, x, y + h - d, d, d, 90, 90)
        gp["GdipClosePathFigure"](path)
        return path

    def round_rect(self, x: float, y: float, w: float, h: float, r: float, fill: int | None = None,
                   stroke: int | None = None, width: float = 1.0) -> None:
        path = self._round_path(x, y, w, h, r)
        if fill is not None:
            b = self._brush(fill)
            gp["GdipFillPath"](self.g, b, path)
            gp["GdipDeleteBrush"](b)
        if stroke is not None:
            pen = ctypes.c_void_p()
            gp["GdipCreatePen1"](stroke, ctypes.c_float(width), 2, ctypes.byref(pen))
            gp["GdipDrawPath"](self.g, pen, path)
            gp["GdipDeletePen"](pen)
        gp["GdipDeletePath"](path)

    def shadow(self, x: float, y: float, w: float, h: float, r: float) -> None:
        # Soft shadow below the shape: stacked translucent outlines (DropShadow blur 18, depth 3, 45%).
        for i in range(10, 0, -1):
            e = i * 1.0
            self.round_rect(x - e, y + 3 - e, w + 2 * e, h + 2 * e, r + e, fill=argb("#000000", 11))

    def rect(self, x: float, y: float, w: float, h: float, color: int) -> None:
        if w <= 0 or h <= 0:
            return
        b = self._brush(color)
        gp["GdipFillRectangle"](self.g, b, x, y, w, h)
        gp["GdipDeleteBrush"](b)

    def hatch(self, x: float, y: float, w: float, h: float, hex_: str) -> None:
        if w <= 0:
            return
        # Hard-edged stripes from a repeating gradient, as the .exe draws them (3.5-unit period).
        b = ctypes.c_void_p()
        gp["GdipCreateLineBrush"](ctypes.byref(PointF(0, 0)), ctypes.byref(PointF(3.5, 3.5)), argb(hex_), argb(hex_, 80),
                                  0, ctypes.byref(b))   # WrapModeTile
        colors = (ctypes.c_uint32 * 4)(argb(hex_), argb(hex_), argb(hex_, 80), argb(hex_, 80))
        stops = (ctypes.c_float * 4)(0, 0.42, 0.42, 1)
        gp["GdipSetLinePresetBlend"](b, colors, stops, 4)
        gp["GdipFillRectangle"](self.g, b, x, y, w, h)
        gp["GdipDeleteBrush"](b)

    def clip_round(self, x: float, y: float, w: float, h: float, r: float) -> None:
        path = self._round_path(x, y, w, h, r)
        gp["GdipSetClipPath"](self.g, path, 0)
        gp["GdipDeletePath"](path)

    def clip_rect(self, x: float, y: float, w: float, h: float) -> None:
        gp["GdipSetClipRect"](self.g, x, y, w, h, 0)

    def unclip(self) -> None:
        gp["GdipResetClip"](self.g)

    def measure(self, text: str, face: str, size: float) -> float:
        font = self.font(face, size)[0]
        box, out = RectF(0, 0, 10000, 1000), RectF()
        gp["GdipMeasureString"](self.g, text, _u16(text), font, ctypes.byref(box), self.fmt, ctypes.byref(out), None, None)
        return out.Width

    def runs(self, runs: list[tuple[str, str, float, int]], x: float, top: float, height: float,
             max_w: float, right: bool = False) -> float:
        """Draws (text, face, size, color) runs on one shared baseline, centred in the line box.
        Trims the last run that doesn't fit with an ellipsis. Returns the width used."""
        asc = max(self.font(f, s)[2] for _, f, s, _ in runs)
        desc = max(self.font(f, s)[3] for _, f, s, _ in runs)
        base = top + (height - (asc + desc)) / 2 + asc
        widths = [self.measure(t, f, s) for t, f, s, _ in runs]
        total = sum(widths)
        if right:
            x = x + max_w - min(total, max_w)
        used = 0.0
        for (text, face, size, color), w in zip(runs, widths):
            font, _, a, _ = self.font(face, size)
            room = max_w - used
            if room <= 4:
                break
            b = self._brush(color)
            box = RectF(x + used, base - a, room if w > room else w + 2, a + self.font(face, size)[3] + 2)
            gp["GdipDrawString"](self.g, text, _u16(text), font, ctypes.byref(box), self.ellipsis if w > room else self.fmt, b)
            gp["GdipDeleteBrush"](b)
            used += min(w, room)
        return used

    def line(self, pts: list[tuple[float, float]], color: int, width: float) -> None:
        path = ctypes.c_void_p()
        gp["GdipCreatePath"](0, ctypes.byref(path))
        for (a, b), (c, d) in zip(pts, pts[1:]):
            gp["GdipAddPathLine"](path, a, b, c, d)
        pen = ctypes.c_void_p()
        gp["GdipCreatePen1"](color, ctypes.c_float(width), 2, ctypes.byref(pen))
        gp["GdipSetPenStartCap"](pen, 2)
        gp["GdipSetPenEndCap"](pen, 2)
        gp["GdipSetPenLineJoin"](pen, 2)
        gp["GdipDrawPath"](self.g, pen, path)
        gp["GdipDeletePen"](pen)
        gp["GdipDeletePath"](path)

    def pace_bar(self, x: float, y: float, w: float, h: float, used: float, elapsed: float | None) -> None:
        """One bar: blue = quota used; hatched teal to the time tick = slack, hatched amber past it =
        ahead of pace. Same encoding as the page and the widgets. y is the top of the track."""
        self.round_rect(x, y, w, h, h / 2, fill=C["track"])
        self.clip_round(x, y, w, h, h / 2)
        u = used / 100 * w
        self.rect(x, y, u, h, C["used"])
        if elapsed is not None:
            e = elapsed / 100 * w
            if abs(e - u) > 0.5:
                self.hatch(x + min(u, e), y, abs(e - u), h, C["slack"] if e >= u else C["ahead"])
        self.unclip()
        if elapsed is not None:
            e = elapsed / 100 * w
            self.rect(x + max(0.0, min(w - 2, e - 1)), y - 3, 2, h + 6, C["ink"])

    def mark(self, x: float, y: float, size: float) -> None:
        k = size / 32
        self.round_rect(x + 2 * k, y + 2 * k, 28 * k, 28 * k, 8 * k, fill=argb("#14263F"))
        for by, fill, color in ((9, 8, "#7894C5"), (14.4, 14, "#7894C5"), (19.8, 4.5, "#4FB8A6")):
            self.round_rect(x + 7 * k, y + by * k, 18 * k, 3.2 * k, 1.6 * k, fill=argb("#2E6B62"))
            self.round_rect(x + 7 * k, y + by * k, fill * k, 3.2 * k, 1.6 * k, fill=argb(color))
        self.round_rect(x + 18.2 * k, y + 7 * k, 1.4 * k, 18 * k, 0.7 * k, fill=argb("#E8E8E8"))


LH = 1.33   # line height per font size, as WPF lays out Segoe UI


def _u16(text: str) -> int:
    """Length in UTF-16 code units, which is what Windows counts (emoji take two)."""
    return len(text.encode("utf-16-le")) // 2


def clip16(text: str, units: int) -> str:
    out = []
    for ch in text:
        units -= _u16(ch)
        if units < 0:
            break
        out.append(ch)
    return "".join(out)


ROW_H = 6 + 15 * LH + 2 + 12 + 1 + 11.5 * LH + 4


class View:
    """Layout and drawing of the pill and its panel, in 1/96-inch units. Fills self.hits with
    (x, y, w, h, action, cursor) for the window to dispatch clicks."""

    def __init__(self) -> None:
        self.groups: list[dict] | None = None
        self.error: str | None = None
        self.at = 0.0
        self.configured = False
        self.server = ""
        self.expanded = False
        self.above = False
        self.scroll = 0.0
        self.max_list_h: float | None = None
        self.hits: list[tuple[float, float, float, float, str, str]] = []
        self.list_view = (0.0, 0.0, 0.0, 0.0)
        self.list_content_h = 0.0

    # --- sizes
    def pill_h(self) -> float:
        return 1 + 9 + (15 * LH + 12.5 * LH + 14) + 9 + 1

    def rows_h(self) -> float:
        if not self.groups or not any(g["items"] for g in self.groups):
            return 13 * LH
        h, first = 0.0, True
        for g in self.groups:
            if not g["items"]:
                continue
            h += (2 if first else 12) + 11 * LH + 2
            first = False
            h += len(g["items"]) * ROW_H
        return h

    def panel_h(self) -> float:
        list_h = self.rows_h()
        if self.max_list_h is not None:
            list_h = min(list_h, self.max_list_h)
        return 1 + 10 + list_h + 10 + 12 * LH + 12 + 1

    def panel_chrome_h(self) -> float:
        return self.panel_h() - min(self.rows_h(), self.max_list_h if self.max_list_h is not None else 1e9)

    def size(self) -> tuple[float, float]:
        h = self.pill_h() + (GAP + self.panel_h() if self.expanded else 0)
        return WIDTH + 2 * PAD, h + 2 * PAD

    def pill_y(self) -> float:
        return PAD + (self.panel_h() + GAP if self.expanded and self.above else 0)

    # --- drawing
    def draw(self, c: Canvas) -> None:
        self.hits = []
        py = self.pill_y()
        if self.expanded:
            ph = self.panel_h()
            panel_y = PAD if self.above else py + self.pill_h() + GAP
            self.draw_panel(c, PAD, panel_y, ph)
        self.draw_pill(c, PAD, py)

    def draw_pill(self, c: Canvas, x: float, y: float) -> None:
        h = self.pill_h()
        c.shadow(x, y, WIDTH, h, 30)
        c.round_rect(x, y, WIDTH, h, 30, fill=C["pill"], stroke=C["edge"], width=1)
        self.hits.append((x, y, WIDTH, h, "pill", "move"))
        ix, iy, ih = x + 1 + 12, y + 1 + 9, h - 2 - 18
        c.mark(ix, iy + (ih - 38) / 2, 38)
        top = top_plan(self.groups) if self.groups is not None else None
        if not self.configured:
            title, sub, chip, level, action = [("Token Pace", Canvas.SEMIBOLD, 15, C["ink"])], \
                [("Connect it to your server", Canvas.REGULAR, 12.5, C["soft"])], "Set up", "use", "settings"
        elif self.groups is not None and top is None:
            title = [("No readings yet", Canvas.SEMIBOLD, 15, C["ink"])]
            sub = [(self.error or "Enter numbers or sign in", Canvas.REGULAR, 12.5, C["warn"] if self.error else C["soft"])]
            chip, level, action = "Open page", "on_pace", "open"
        elif top is None:
            title = [("No reading yet" if self.error else "Reading…", Canvas.SEMIBOLD, 15, C["ink"])]
            sub = [(self.error or self.server, Canvas.REGULAR, 12.5, C["warn"] if self.error else C["soft"])]
            chip, level, action = ("Retry" if self.error else "…"), "on_pace", "refresh"
        else:
            title = [(top["name"], Canvas.SEMIBOLD, 15, C["ink"]), ("  " + top["window"], Canvas.REGULAR, 12, C["muted"])]
            sub = [(pace_text(top), Canvas.BOLD, 12.5, level_ink(top["level"])),
                   (" · " + when_text(top), Canvas.REGULAR, 12.5, C["soft"])]
            if self.error:
                sub.append((" · old reading", Canvas.REGULAR, 12.5, C["warn"]))
            chip, level, action = top["verdict"], top["level"], "toggle"
        # right side: chevron, then chip
        cx = x + WIDTH - 1 - 10 - 26
        chev_y = iy + (ih - 26) / 2
        self.hits.append((cx, chev_y, 26, 26, "toggle", "hand"))
        up = (not self.above) if self.expanded else self.above
        mx, my = cx + 13, chev_y + 13
        pts = [(mx - 6, my + 3), (mx, my - 3), (mx + 6, my + 3)] if up else [(mx - 6, my - 3), (mx, my + 3), (mx + 6, my - 3)]
        c.line(pts, C["soft"], 1.8)
        fill, stroke, ink = CHIP.get(level, ("#34363B", None, "#D6D8DC"))
        tw = c.measure(chip, Canvas.SEMIBOLD, 13.5)
        chip_w, chip_h = tw + 26, 6 + 13.5 * LH + 7
        chip_x, chip_y = cx - 6 - chip_w, iy + (ih - chip_h) / 2
        c.round_rect(chip_x, chip_y, chip_w, chip_h, 17, fill=argb(fill) if fill else None,
                     stroke=argb(stroke) if stroke else None, width=1.2)
        c.runs([(chip, Canvas.SEMIBOLD, 13.5, argb(ink))], chip_x + 13, chip_y + 6, 13.5 * LH, tw + 4)
        self.hits.append((chip_x, chip_y, chip_w, chip_h, action, "hand"))
        # text column
        tx = ix + 38 + 10
        tw_max = chip_x - 10 - tx
        col_h = 15 * LH + 12.5 * LH + 14
        ty = iy + (ih - col_h) / 2
        c.runs(title, tx, ty, 15 * LH, tw_max)
        c.runs(sub, tx, ty + 15 * LH, 12.5 * LH, tw_max)
        if top is not None and self.configured:
            c.pace_bar(tx, ty + 15 * LH + 12.5 * LH + 3 + 3, tw_max, 5, top["used"], top["elapsed"])

    def draw_panel(self, c: Canvas, x: float, y: float, h: float) -> None:
        c.shadow(x, y, WIDTH, h, 22)
        c.round_rect(x, y, WIDTH, h, 22, fill=C["panel"], stroke=C["edge"], width=1)
        self.hits.append((x, y, WIDTH, h, "panel", "arrow"))
        ix, iw = x + 1 + 16, WIDTH - 2 - 32
        list_y = y + 1 + 10
        content_h = self.rows_h()
        view_h = min(content_h, self.max_list_h) if self.max_list_h is not None else content_h
        self.list_view, self.list_content_h = (ix, list_y, iw, view_h), content_h
        self.scroll = max(0.0, min(self.scroll, content_h - view_h))
        c.clip_rect(x, list_y, WIDTH, view_h)
        yy = list_y - self.scroll
        if not self.groups or not any(g["items"] for g in self.groups):
            msg = (self.error if self.groups is None and self.error else
                   "No reading yet." if self.groups is None else "No plan has a reading yet.")
            c.runs([(msg, Canvas.REGULAR, 13, C["soft"])], ix, yy, 13 * LH, iw)
        else:
            first = True
            for g in self.groups:
                if not g["items"]:
                    continue
                yy += 2 if first else 12
                first = False
                c.runs([(g["label"].upper(), Canvas.SEMIBOLD, 11, C["muted"])], ix, yy, 11 * LH, iw)
                yy += 11 * LH + 2
                for p in g["items"]:
                    if yy + ROW_H < list_y or yy > list_y + view_h:
                        yy += ROW_H   # outside the visible list: nothing to draw
                    else:
                        yy = self.draw_row(c, p, ix, yy, iw)
        c.unclip()
        if content_h > view_h:   # thin scroll indicator
            frac = view_h / content_h
            th = max(24.0, view_h * frac)
            ty = list_y + (view_h - th) * (self.scroll / max(1.0, content_h - view_h))
            c.round_rect(x + WIDTH - 7, ty, 3, th, 1.5, fill=argb("#FFFFFF", 50))
        fy = list_y + view_h + 10
        status = "" if self.groups is None else (self.error or "Updated " + ago(self.at))
        c.runs([(status or " ", Canvas.REGULAR, 11.5, C["warn"] if self.error else C["muted"])], ix, fy, 12 * LH, iw * 0.55)
        right = ix + iw
        for label, action in (("Open page", "open"), ("Refresh", "refresh")):
            w = c.measure(label, Canvas.SEMIBOLD, 12)
            c.runs([(label, Canvas.SEMIBOLD, 12, C["link"])], right - w, fy, 12 * LH, w + 2)
            self.hits.append((right - w - 4, fy - 2, w + 8, 12 * LH + 4, action, "hand"))
            right -= w + 14

    def draw_row(self, c: Canvas, p: dict, x: float, y: float, w: float) -> float:
        y += 6
        pace = pace_text(p)
        pw = c.measure(pace, Canvas.BOLD, 15)
        c.runs([(pace, Canvas.BOLD, 15, level_ink(p["level"]))], x + w - pw - 1, y, 15 * LH, pw + 2)
        c.runs([(f"{p['rank']}. ", Canvas.REGULAR, 13, C["muted"]), (p["name"], Canvas.SEMIBOLD, 13.5, C["ink"]),
                ("  " + p["window"], Canvas.REGULAR, 12, C["muted"])], x, y, 15 * LH, w - pw - 8)
        y += 15 * LH + 2
        c.pace_bar(x, y + 3, w, 6, p["used"], p["elapsed"])
        y += 12 + 1
        c.runs([(p["verdict"], Canvas.SEMIBOLD, 11.5, level_ink(p["level"])),
                (f" · {round(p['used'])}% used · {when_text(p)}", Canvas.REGULAR, 11.5, C["muted"])], x, y, 11.5 * LH, w)
        return y + 11.5 * LH + 4


def ago(t: float) -> str:
    s = time.time() - t
    return "just now" if s < 60 else duration(s) + " ago"


def new_bitmap(w: int, h: int, scan0: Any = None) -> Any:
    bmp = ctypes.c_void_p()
    status = gp["GdipCreateBitmapFromScan0"](w, h, w * 4 if scan0 else 0, 0xE200B, scan0, ctypes.byref(bmp))   # 32bpp PARGB
    if status != 0:
        raise OSError(f"GDI+ bitmap failed ({status})")
    return bmp


def render_png(view: View, path: str, scale: float = 2.0, backdrop: str | None = None) -> None:
    gdiplus_start()
    w, h = view.size()
    bw, bh = int(math.ceil(w * scale)), int(math.ceil(h * scale))
    bmp = new_bitmap(bw, bh)
    c = Canvas(bmp, scale)
    if backdrop:
        c.rect(0, 0, w, h, argb(backdrop))
    view.draw(c)
    c.close()
    png = GUID.of("557CF406-1A04-11D3-9A73-0000F81EF32E")
    gp["GdipSaveImageToFile"](bmp, path, ctypes.byref(png), None)
    gp["GdipDisposeImage"](bmp)


# ---------------------------------------------------------------- the window and tray icon

WM_DESTROY, WM_TIMER, WM_SETCURSOR, WM_MOUSEMOVE = 0x0002, 0x0113, 0x0020, 0x0200
WM_LBUTTONDOWN, WM_LBUTTONUP, WM_RBUTTONUP, WM_MOUSEWHEEL = 0x0201, 0x0202, 0x0205, 0x020A
WM_MOUSEACTIVATE, WM_DISPLAYCHANGE, WM_SETTINGCHANGE, WM_APP = 0x0021, 0x007E, 0x001A, 0x8000
WM_TRAY, WM_DATA = WM_APP + 1, WM_APP + 2
WM_CAPTURECHANGED = 0x0215


class Mini:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.view = View()
        self.view.expanded = settings.expanded
        self.fetching = False
        self.generation = 0
        self.result: tuple[int, list | None, str | None] | None = None
        self.lock = threading.Lock()
        self.down: tuple | None = None
        self.dragging = False
        self.win_px = (0, 0)
        self.size_px = (0, 0)
        self.hicon = None
        self.visible = True
        self.dialog: Any = None
        self.quit_after_dialog = False
        gdiplus_start()
        dc = GetDC(None)
        self.scale = GetDeviceCaps(dc, 88) / 96.0   # LOGPIXELSX; the process is system-DPI aware
        ReleaseDC(None, dc)
        self._proc = WNDPROC(self.wndproc)   # keep a reference: Windows calls back into it
        hinst = GetModuleHandleW(None)
        wc = WNDCLASSEXW(ctypes.sizeof(WNDCLASSEXW), 0, self._proc, 0, 0, hinst, None, None, None, None,
                         "TokenPaceMini", None)
        RegisterClassExW(ctypes.byref(wc))
        ex = 0x00080000 | 0x00000080 | 0x00000008 | 0x08000000   # layered, tool window, topmost, no-activate
        self.hwnd = CreateWindowExW(ex, "TokenPaceMini", "Token Pace", 0x80000000, 0, 0, 1, 1, None, None, hinst, None)
        self.taskbar_created = RegisterWindowMessageW("TaskbarCreated")
        self.add_tray()
        SetTimer(self.hwnd, 1, TICK_SECONDS * 1000, None)
        SetTimer(self.hwnd, 2, POLL_SECONDS * 1000, None)
        self.place_initially()
        self.render()
        ShowWindow(self.hwnd, 4)   # show without activating
        self.refresh()

    # --- geometry (pill position is in 1/96-inch units, like the .exe's settings)
    def work_area(self, left: float, top: float) -> tuple[float, float, float, float]:
        pt = W.POINT(int(round((left + 30) * self.scale)), int(round((top + 20) * self.scale)))
        mon = MonitorFromPoint(pt, 2)   # nearest
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        GetMonitorInfoW(mon, ctypes.byref(mi))
        r, s = mi.rcWork, self.scale
        return r.left / s, r.top / s, r.right / s, r.bottom / s

    def place_initially(self) -> None:
        s = self.settings
        ok = False
        if s.left is not None and s.top is not None:
            pt = W.POINT(int(round((s.left + 30) * self.scale)), int(round((s.top + 20) * self.scale)))
            ok = bool(MonitorFromPoint(pt, 0))   # 0: null when the point is on no monitor
        if not ok:
            l, t, r, b = self.work_area(0, 0) if s.left is None else self.work_area(s.left, s.top or 0)
            mon = MonitorFromPoint(W.POINT(0, 0), 1)   # primary
            mi = MONITORINFO()
            mi.cbSize = ctypes.sizeof(MONITORINFO)
            GetMonitorInfoW(mon, ctypes.byref(mi))
            r, b = mi.rcWork.right / self.scale, mi.rcWork.bottom / self.scale
            s.left, s.top = r - WIDTH - 12, b - 70

    def layout(self) -> None:
        """Keeps the pill inside its screen; the panel opens toward the larger free side and
        scrolls when the plans don't fit."""
        s, v = self.settings, self.view
        l, t, r, b = self.work_area(s.left, s.top)
        ph = v.pill_h()
        s.left = max(l, min(r - WIDTH, s.left))
        s.top = max(t, min(b - ph, s.top))
        v.above = s.top + ph / 2 > t + (b - t) / 2
        v.max_list_h = None
        if v.expanded:
            room = (s.top - t) if v.above else (b - s.top - ph)
            over = (GAP + v.panel_h()) - room
            if over > 0:
                v.max_list_h = max(80.0, v.rows_h() - over)

    def render(self) -> None:
        if self.dragging:
            return
        v, s = self.view, self.settings
        v.configured = bool(s.server)
        v.server = s.server
        v.expanded = s.expanded
        self.layout()
        w, h = v.size()
        k = self.scale
        bw, bh = int(math.ceil(w * k)), int(math.ceil(h * k))
        x = int(round((s.left - PAD) * k))
        y = int(round((s.top - v.pill_y()) * k))
        screen = GetDC(None)
        mem = CreateCompatibleDC(screen)
        bi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), bw, -bh, 1, 32, 0, 0, 0, 0, 0, 0)
        bits = ctypes.c_void_p()
        dib = CreateDIBSection(mem, ctypes.byref(bi), 0, ctypes.byref(bits), None, 0)
        old = SelectObject(mem, dib)
        bmp = new_bitmap(bw, bh, bits)
        c = Canvas(bmp, k)
        v.draw(c)
        c.close()
        gp["GdipDisposeImage"](bmp)
        blend = BLENDFUNCTION(0, 0, 255, 1)
        UpdateLayeredWindow(self.hwnd, screen, ctypes.byref(W.POINT(x, y)), ctypes.byref(W.SIZE(bw, bh)), mem,
                            ctypes.byref(W.POINT(0, 0)), 0, ctypes.byref(blend), 2)
        SelectObject(mem, old)
        DeleteObject(dib)
        DeleteDC(mem)
        ReleaseDC(None, screen)
        self.win_px, self.size_px = (x, y), (bw, bh)
        self.update_tray()

    # --- data
    def refresh(self) -> None:
        if self.fetching:
            return
        if not self.settings.server:
            self.view.error = None
            self.render()
            return
        self.fetching = True
        gen, server, token = self.generation, self.settings.server, self.settings.token

        def work() -> None:
            groups, err = None, None
            try:
                groups = fetch(server, token)
            except MiniError as exc:
                err = str(exc)
            except Exception:   # noqa: BLE001 - never let a reading crash the app
                err = "Reading failed"
            with self.lock:
                if self.result is None or self.result[0] <= gen:   # an older fetch never overwrites a newer one
                    self.result = (gen, groups, err)
            PostMessageW(self.hwnd, WM_DATA, 0, 0)

        threading.Thread(target=work, daemon=True).start()

    def take_result(self) -> None:
        with self.lock:
            res, self.result = self.result, None
        if not res or res[0] != self.generation:
            return   # the server changed meanwhile
        self.fetching = False
        _, groups, err = res
        if groups is not None:
            self.view.groups, self.view.error, self.view.at = groups, None, time.time()
        else:
            self.view.error = err
        self.render()

    def server_changed(self) -> None:
        self.generation += 1
        self.fetching = False
        self.view.groups, self.view.error = None, None
        self.refresh()

    # --- actions
    def act(self, action: str) -> None:
        if action == "toggle":
            if self.view.groups is not None and top_plan(self.view.groups) is not None:
                self.settings.expanded = not self.settings.expanded
                self.view.scroll = 0
                self.settings.save()
                self.render()
        elif action == "refresh":
            self.refresh()
        elif action == "open":
            self.open_page()
        elif action == "settings":
            self.open_settings()

    def open_page(self) -> None:
        if not self.settings.server:
            self.open_settings()
        elif normalize(self.settings.server):
            ShellExecuteW(None, "open", self.settings.server + "/", None, None, 1)

    def open_settings(self) -> None:
        try:
            import tkinter as tk
        except ImportError:
            MessageBoxW(self.hwnd, "Settings need tkinter, which this Python lacks. Quit Token Pace Mini from "
                        "its tray icon, then run:\npythonw -m tokenpace mini --server http://host:8787\n"
                        "with the server token, if any, in the TOKENPACE_TOKEN environment variable.",
                        "Token Pace Mini", 0x40)
            return
        if self.dialog is not None:
            self.dialog.lift()
            self.dialog.focus_force()
            return
        s = self.settings
        root = tk.Tk()
        self.dialog = root
        root.title("Token Pace Mini")
        root.attributes("-topmost", True)
        root.resizable(False, False)
        frame = tk.Frame(root, padx=18, pady=14)
        frame.pack()
        tk.Label(frame, text="Server address", font=("Segoe UI Semibold", 10)).pack(anchor="w")
        server = tk.Entry(frame, width=46, font=("Segoe UI", 10))
        server.insert(0, s.server or "http://localhost:8787")
        server.pack(anchor="w", pady=(2, 0))
        tk.Label(frame, text="The address of your Token Pace server, as you open it in a browser.",
                 fg="gray", font=("Segoe UI", 9)).pack(anchor="w")
        tk.Label(frame, text="Token (if the server has one)", font=("Segoe UI Semibold", 10)).pack(anchor="w", pady=(10, 0))
        token = tk.Entry(frame, width=46, show="•", font=("Segoe UI", 10))
        token.insert(0, s.token)
        token.pack(anchor="w", pady=(2, 0))
        tk.Label(frame, text="Stored encrypted for your Windows user.", fg="gray", font=("Segoe UI", 9)).pack(anchor="w")
        start = tk.BooleanVar(value=starts_with_windows())
        tk.Checkbutton(frame, text="Start with Windows", variable=start, font=("Segoe UI", 10)).pack(anchor="w", pady=(10, 0))
        msg = tk.Label(frame, text="", wraplength=360, justify="left", font=("Segoe UI", 9))
        msg.pack(anchor="w", pady=(8, 0))
        buttons = tk.Frame(frame)
        buttons.pack(anchor="e", pady=(8, 0))
        state: dict[str, Any] = {"closed": False, "result": None}

        def close() -> None:
            state["closed"] = True
            root.destroy()

        def poll() -> None:
            if state["closed"]:
                return
            res = state["result"]
            if res is None:
                root.after(100, poll)
                return
            url, tok, err = res
            save.config(state="normal")
            if err:
                msg.config(text=err + ". Check the address and token.")
                state["result"] = None
                return
            s.server, s.token = url, tok
            s.save()
            try:
                set_starts_with_windows(start.get())
            except OSError:
                pass
            close()
            self.server_changed()

        def submit() -> None:
            if str(save["state"]) == "disabled":
                return   # a check is already running
            url, tok = normalize(server.get()), token.get().strip()
            if not url:
                msg.config(text="Enter an http:// or https:// address.")
                return
            save.config(state="disabled")
            msg.config(text="Checking…")

            def check() -> None:
                try:
                    fetch(url, tok)
                    state["result"] = (url, tok, None)
                except MiniError as exc:
                    state["result"] = (url, tok, str(exc))
                except Exception:   # noqa: BLE001
                    state["result"] = (url, tok, "Reading failed")

            threading.Thread(target=check, daemon=True).start()
            root.after(100, poll)

        save = tk.Button(buttons, text="Test and save", command=submit, padx=12)
        save.pack(side="left")
        tk.Button(buttons, text="Cancel", command=close, padx=12).pack(side="left", padx=(8, 0))
        root.protocol("WM_DELETE_WINDOW", close)
        root.bind("<Return>", lambda e: submit())
        root.bind("<Escape>", lambda e: close())
        try:
            root.mainloop()   # Tk's loop also dispatches this window's messages
        finally:
            self.dialog = None
        if self.quit_after_dialog:
            self.quit()   # Quit was chosen while the dialog was open: finish it outside Tk's loop

    def show_menu(self) -> None:
        items = [(1, "Hide pill" if self.visible else "Show pill"), (2, "Show all plans"), (3, "Reset position"),
                 (4, "Refresh now"), (5, "Open page"), (0, None), (6, "Settings…"),
                 (7, "Start with Windows"), (0, None), (8, "Quit")]
        menu = CreatePopupMenu()
        checked = starts_with_windows()
        for cmd, label in items:
            if label is None:
                AppendMenuW(menu, 0x800, 0, None)
            else:
                AppendMenuW(menu, 0x8 if (cmd == 7 and checked) else 0, cmd, label)
        pt = W.POINT()
        GetCursorPos(ctypes.byref(pt))
        SetForegroundWindow(self.hwnd)
        cmd = TrackPopupMenu(menu, 0x0100 | 0x0002, pt.x, pt.y, 0, self.hwnd, None)   # return command, right button
        PostMessageW(self.hwnd, 0, 0, 0)
        DestroyMenu(menu)
        if cmd == 1:
            self.toggle_visible()
        elif cmd == 2:
            if not self.visible:
                self.toggle_visible()
            if not self.settings.expanded:
                self.act("toggle")
        elif cmd == 3:
            self.settings.left = self.settings.top = None
            self.place_initially()
            self.settings.save()
            if not self.visible:
                self.toggle_visible()
            self.render()
        elif cmd == 4:
            self.refresh()
        elif cmd == 5:
            self.open_page()
        elif cmd == 6:
            self.open_settings()
        elif cmd == 7:
            try:
                set_starts_with_windows(not checked)
            except OSError:
                pass
        elif cmd == 8:
            self.quit()

    def toggle_visible(self) -> None:
        self.visible = not self.visible
        ShowWindow(self.hwnd, 4 if self.visible else 0)
        if self.visible:
            self.render()

    def quit(self) -> None:
        if self.dialog is not None:
            # Inside Tk's loop: close the dialog and let open_settings() finish the quit once the
            # loop returns. Posting WM_QUIT under Tk makes it spin instead of returning.
            self.quit_after_dialog = True
            try:
                self.dialog.destroy()
            except Exception:   # noqa: BLE001
                pass
            return
        self.settings.save()
        nid = self.nid()
        Shell_NotifyIconW(2, ctypes.byref(nid))   # delete
        if self.hicon:
            DestroyIcon(self.hicon)
        DestroyWindow(self.hwnd)

    # --- tray icon
    def nid(self) -> Any:
        n = NOTIFYICONDATAW()
        n.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        n.hWnd, n.uID = self.hwnd, 1
        return n

    def add_tray(self) -> None:
        n = self.nid()
        n.uFlags, n.uCallbackMessage = 0x1, WM_TRAY
        Shell_NotifyIconW(0, ctypes.byref(n))
        self.update_tray()

    def update_tray(self) -> None:
        groups = self.view.groups
        top = top_plan(groups) if groups is not None else None
        tip = (f"{top['name']}: {top['verdict']} · {pace_text(top)} · {when_text(top)}" if top
               else "Token Pace · " + (self.view.error or "no reading yet"))
        size = max(16, int(round(16 * self.scale)))
        bmp = new_bitmap(size, size)
        c = Canvas(bmp, size / 16)
        ring = argb({"use": "#8FD6A0", "lean_use": "#8FD6A0", "save": "#F5C46B", "blocked": "#FF7A6E"}.get(
            top["level"] if top else "", "#9DB4E0" if top else "#8A8E95"))
        c.round_rect(0.8, 0.8, 14.4, 14.4, 4, fill=C["pill"], stroke=ring, width=1.6)
        c.rect(3, 6.3, 10, 3.4, argb("#454850"))
        if top:
            u = top["used"] / 100 * 10
            c.rect(3, 6.3, u, 3.4, C["used"])
            if top["elapsed"] is not None:
                e = top["elapsed"] / 100 * 10
                c.rect(3 + min(u, e), 6.3, abs(e - u), 3.4, argb(C["slack"] if e >= u else C["ahead"]))
                c.rect(3 + e - 0.6, 4.7, 1.2, 6.6, argb("#FFFFFF"))
        c.close()
        n = self.nid()
        n.uFlags, n.uCallbackMessage = 0x1 | 0x2 | 0x4, WM_TRAY
        n.szTip = clip16(tip, 127)
        icon = ctypes.c_void_p()
        gp["GdipCreateHICONFromBitmap"](bmp, ctypes.byref(icon))
        gp["GdipDisposeImage"](bmp)
        n.hIcon = icon
        Shell_NotifyIconW(1, ctypes.byref(n))   # modify
        if self.hicon:
            DestroyIcon(self.hicon)
        self.hicon = icon

    # --- input
    def hit(self, px: int, py: int) -> tuple[str, str] | None:
        x, y = (px - self.win_px[0]) / self.scale, (py - self.win_px[1]) / self.scale
        for hx, hy, hw, hh, action, cursor in reversed(self.view.hits):
            if hx <= x <= hx + hw and hy <= y <= hy + hh:
                return action, cursor
        return None

    def end_drag(self) -> None:
        self.down = None
        if self.dragging:
            self.dragging = False
            self.settings.left = self.win_px[0] / self.scale + PAD
            self.settings.top = self.win_px[1] / self.scale + self.view.pill_y()
            self.settings.save()
            self.render()   # the panel may now open on the other side

    def wndproc(self, hwnd: Any, msg: int, wp: int, lp: int) -> int:
        try:
            return self._wndproc(hwnd, msg, wp, lp)
        except Exception:   # noqa: BLE001 - an exception must not cross the Windows callback
            return DefWindowProcW(hwnd, msg, wp, lp)

    def _wndproc(self, hwnd: Any, msg: int, wp: int, lp: int) -> int:
        if msg == WM_MOUSEACTIVATE:
            return 3   # don't take focus from the app the user is in
        if msg == WM_SETCURSOR:
            pt = W.POINT()
            GetCursorPos(ctypes.byref(pt))
            h = self.hit(pt.x, pt.y)
            kind = 32649 if h and h[1] == "hand" else 32646 if h and h[1] == "move" else 32512
            SetCursor(LoadCursorW(None, ctypes.c_void_p(kind)))
            return 1
        if msg == WM_LBUTTONDOWN:
            pt = W.POINT()
            GetCursorPos(ctypes.byref(pt))
            h = self.hit(pt.x, pt.y)
            if h:
                self.down = (pt.x, pt.y, self.win_px, h[0])
                SetCapture(hwnd)
            return 0
        if msg == WM_CAPTURECHANGED and self.down:
            self.end_drag()   # capture lost (menu, lock screen, UAC): stop following the cursor
            return 0
        if msg == WM_MOUSEMOVE and self.down and wp & 1:
            pt = W.POINT()
            GetCursorPos(ctypes.byref(pt))
            x0, y0, (wx, wy), action = self.down
            if action in ("pill", "panel") and (self.dragging or abs(pt.x - x0) + abs(pt.y - y0) > 4):
                self.dragging = True
                nx, ny = wx + pt.x - x0, wy + pt.y - y0
                SetWindowPos(hwnd, None, nx, ny, 0, 0, 0x0001 | 0x0004 | 0x0010)   # no size, z-order, activate
                self.win_px = (nx, ny)
            return 0
        if msg == WM_LBUTTONUP and self.down:
            pt = W.POINT()
            GetCursorPos(ctypes.byref(pt))
            _, _, _, action = self.down
            was_dragging = self.dragging
            self.end_drag()   # clears the state first: ReleaseCapture sends WM_CAPTURECHANGED
            ReleaseCapture()
            if was_dragging:
                return 0
            h = self.hit(pt.x, pt.y)
            if h and h[0] == action:
                self.act("toggle" if action == "pill" else action)
            return 0
        if msg == WM_RBUTTONUP:
            self.show_menu()
            return 0
        if msg == WM_MOUSEWHEEL and self.view.expanded and self.view.max_list_h is not None:
            delta = ctypes.c_short(wp >> 16 & 0xFFFF).value
            self.view.scroll -= delta / 120 * 40
            self.render()
            return 0
        if msg == WM_TIMER:
            if wp == 2:
                self.refresh()
            else:
                self.render()
            return 0
        if msg == WM_DATA:
            self.take_result()
            return 0
        if msg == WM_TRAY:
            if lp == WM_LBUTTONUP:
                self.toggle_visible()
            elif lp == WM_RBUTTONUP:
                self.show_menu()
            return 0
        if msg == self.taskbar_created:   # Explorer restarted: put the icon back
            self.add_tray()
            return 0
        if msg in (WM_DISPLAYCHANGE, WM_SETTINGCHANGE):
            self.render()
            return 0
        if msg == WM_DESTROY:
            PostQuitMessage(0)
            return 0
        return DefWindowProcW(hwnd, msg, wp, lp)


def run(argv: list[str]) -> int:
    import argparse
    p = argparse.ArgumentParser(prog="tokenpace mini", description="Floating pill and tray icon (Windows).")
    p.add_argument("--server", help="server address; saved unless --snapshot is used")
    p.add_argument("--snapshot", help="render to a PNG and exit (no window)")
    p.add_argument("--expanded", action="store_true")
    p.add_argument("--backdrop", help="background colour for --snapshot, e.g. #2B3A55")
    args = p.parse_args(argv)
    if sys.platform != "win32":
        print("tokenpace mini runs on Windows. Elsewhere, open the server's page.", file=sys.stderr)
        return 2
    ctypes.windll.user32.SetProcessDPIAware()
    if args.snapshot:
        server = normalize(args.server or "http://localhost:8787")
        view = View()
        view.configured, view.server, view.expanded = True, server, args.expanded
        try:
            if not server:
                raise MiniError("Not an http:// or https:// address")
            view.groups, view.at = fetch(server, os.environ.get("TOKENPACE_TOKEN", "")), time.time()
        except MiniError as exc:
            view.error = str(exc)
        render_png(view, args.snapshot, backdrop=args.backdrop)
        return 0 if view.error is None else 2
    mutex = CreateMutexW(None, False, "Local\\TokenPaceMini")   # shared with the .exe build
    if ctypes.get_last_error() == 183:
        return 0   # already running: its tray icon is there
    settings = Settings.load()
    if args.server:
        settings.server = normalize(args.server)
        settings.token = os.environ.get("TOKENPACE_TOKEN", settings.token)
        settings.save()
    app = Mini(settings)
    if not settings.server:
        app.open_settings()
    msg = MSG()
    while GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        TranslateMessage(ctypes.byref(msg))
        DispatchMessageW(ctypes.byref(msg))
    del mutex
    return 0

