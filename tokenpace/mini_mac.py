r"""Token Pace Mini for macOS: the Windows mini's floating pill and panel, drawn from the same
layout code, plus a menu bar icon. Python standard library only: ctypes into AppKit, Core
Graphics and Core Text, so any Python 3.11+ on a Mac runs it, with nothing to compile or sign.

    python3 -m tokenpace mini
    tokenpace mini --snapshot out.png --server http://localhost:8787 --expanded

It reads /api/widget like the other widgets. Settings live in
~/Library/Application Support/TokenPace/mini.json and the token, if the server has one, in the
login Keychain. "Start at login" writes ~/Library/LaunchAgents/app.tokenpace.mini.plist.
"""
from __future__ import annotations

import ctypes
import fcntl
import json
import math
import os
import platform
import plistlib
import signal
import site
import subprocess
import sys
import threading
import time
import traceback
from ctypes import POINTER, byref, c_bool, c_char_p, c_double, c_int32, c_long, c_size_t, c_ubyte, c_uint32, c_void_p
from typing import Any, Callable

from .mini import C, PAD, POLL_SECONDS, TICK_SECONDS, WIDTH, Canvas, MiniError, View, _num, argb, fetch, \
    normalize, pace_text, top_plan, when_text


# ---------------------------------------------------------------- frameworks

def _framework(name: str) -> ctypes.CDLL:
    return ctypes.cdll.LoadLibrary(f"/System/Library/Frameworks/{name}.framework/{name}")


objc = ctypes.cdll.LoadLibrary("/usr/lib/libobjc.A.dylib")
cf = _framework("CoreFoundation")
cg = _framework("CoreGraphics")
ct = _framework("CoreText")
imageio = _framework("ImageIO")
security = _framework("Security")
foundation = _framework("Foundation")
appkit = _framework("AppKit")


def _fn(lib: ctypes.CDLL, name: str, res: Any, *args: Any) -> Any:
    f = getattr(lib, name)
    f.restype, f.argtypes = res, list(args)
    return f


class CGPoint(ctypes.Structure):
    _fields_ = [("x", c_double), ("y", c_double)]


class CGSize(ctypes.Structure):
    _fields_ = [("width", c_double), ("height", c_double)]


class CGRect(ctypes.Structure):
    _fields_ = [("origin", CGPoint), ("size", CGSize)]


class CGAffineTransform(ctypes.Structure):
    _fields_ = [("a", c_double), ("b", c_double), ("c", c_double), ("d", c_double), ("tx", c_double), ("ty", c_double)]


def _rect(x: float, y: float, w: float, h: float) -> CGRect:
    return CGRect(CGPoint(x, y), CGSize(w, h))


CFRelease = _fn(cf, "CFRelease", None, c_void_p)
CFRetain = _fn(cf, "CFRetain", c_void_p, c_void_p)
CFStringCreateWithBytes = _fn(cf, "CFStringCreateWithBytes", c_void_p, c_void_p, c_char_p, c_long, c_uint32, c_bool)
CFDictionaryCreate = _fn(cf, "CFDictionaryCreate", c_void_p, c_void_p, POINTER(c_void_p), POINTER(c_void_p), c_long,
                         c_void_p, c_void_p)
CFAttributedStringCreate = _fn(cf, "CFAttributedStringCreate", c_void_p, c_void_p, c_void_p, c_void_p)
CFDataCreate = _fn(cf, "CFDataCreate", c_void_p, c_void_p, c_char_p, c_long)
CFDataGetLength = _fn(cf, "CFDataGetLength", c_long, c_void_p)
CFDataGetBytePtr = _fn(cf, "CFDataGetBytePtr", c_void_p, c_void_p)
CFURLCreateFromFileSystemRepresentation = _fn(cf, "CFURLCreateFromFileSystemRepresentation", c_void_p, c_void_p,
                                              c_char_p, c_long, c_bool)
_KEY_CALLBACKS = ctypes.addressof(c_ubyte.in_dll(cf, "kCFTypeDictionaryKeyCallBacks"))
_VALUE_CALLBACKS = ctypes.addressof(c_ubyte.in_dll(cf, "kCFTypeDictionaryValueCallBacks"))
kCFBooleanTrue = c_void_p.in_dll(cf, "kCFBooleanTrue").value

CGColorSpaceCreateWithName = _fn(cg, "CGColorSpaceCreateWithName", c_void_p, c_void_p)
CGColorSpaceRelease = _fn(cg, "CGColorSpaceRelease", None, c_void_p)
CGColorCreateSRGB = _fn(cg, "CGColorCreateSRGB", c_void_p, c_double, c_double, c_double, c_double)
CGColorRelease = _fn(cg, "CGColorRelease", None, c_void_p)
CGBitmapContextCreate = _fn(cg, "CGBitmapContextCreate", c_void_p, c_void_p, c_size_t, c_size_t, c_size_t, c_size_t,
                            c_void_p, c_uint32)
CGBitmapContextCreateImage = _fn(cg, "CGBitmapContextCreateImage", c_void_p, c_void_p)
CGContextRelease = _fn(cg, "CGContextRelease", None, c_void_p)
CGImageRelease = _fn(cg, "CGImageRelease", None, c_void_p)
CGContextTranslateCTM = _fn(cg, "CGContextTranslateCTM", None, c_void_p, c_double, c_double)
CGContextScaleCTM = _fn(cg, "CGContextScaleCTM", None, c_void_p, c_double, c_double)
CGContextSaveGState = _fn(cg, "CGContextSaveGState", None, c_void_p)
CGContextRestoreGState = _fn(cg, "CGContextRestoreGState", None, c_void_p)
CGContextSetFillColorWithColor = _fn(cg, "CGContextSetFillColorWithColor", None, c_void_p, c_void_p)
CGContextSetStrokeColorWithColor = _fn(cg, "CGContextSetStrokeColorWithColor", None, c_void_p, c_void_p)
CGContextFillRect = _fn(cg, "CGContextFillRect", None, c_void_p, CGRect)
CGContextClearRect = _fn(cg, "CGContextClearRect", None, c_void_p, CGRect)
CGContextClipToRect = _fn(cg, "CGContextClipToRect", None, c_void_p, CGRect)
CGContextAddPath = _fn(cg, "CGContextAddPath", None, c_void_p, c_void_p)
CGContextFillPath = _fn(cg, "CGContextFillPath", None, c_void_p)
CGContextStrokePath = _fn(cg, "CGContextStrokePath", None, c_void_p)
CGContextClip = _fn(cg, "CGContextClip", None, c_void_p)
CGContextBeginPath = _fn(cg, "CGContextBeginPath", None, c_void_p)
CGContextMoveToPoint = _fn(cg, "CGContextMoveToPoint", None, c_void_p, c_double, c_double)
CGContextAddLineToPoint = _fn(cg, "CGContextAddLineToPoint", None, c_void_p, c_double, c_double)
CGContextClosePath = _fn(cg, "CGContextClosePath", None, c_void_p)
CGContextSetLineWidth = _fn(cg, "CGContextSetLineWidth", None, c_void_p, c_double)
CGContextSetLineCap = _fn(cg, "CGContextSetLineCap", None, c_void_p, c_int32)
CGContextSetLineJoin = _fn(cg, "CGContextSetLineJoin", None, c_void_p, c_int32)
CGContextSetTextMatrix = _fn(cg, "CGContextSetTextMatrix", None, c_void_p, CGAffineTransform)
CGContextSetTextPosition = _fn(cg, "CGContextSetTextPosition", None, c_void_p, c_double, c_double)
CGPathCreateWithRoundedRect = _fn(cg, "CGPathCreateWithRoundedRect", c_void_p, CGRect, c_double, c_double, c_void_p)
CGPathRelease = _fn(cg, "CGPathRelease", None, c_void_p)
kCGColorSpaceSRGB = c_void_p.in_dll(cg, "kCGColorSpaceSRGB").value

CTLineCreateWithAttributedString = _fn(ct, "CTLineCreateWithAttributedString", c_void_p, c_void_p)
CTLineCreateTruncatedLine = _fn(ct, "CTLineCreateTruncatedLine", c_void_p, c_void_p, c_double, c_uint32, c_void_p)
CTLineGetTypographicBounds = _fn(ct, "CTLineGetTypographicBounds", c_double, c_void_p, c_void_p, c_void_p, c_void_p)
CTLineDraw = _fn(ct, "CTLineDraw", None, c_void_p, c_void_p)
CTFontGetAscent = _fn(ct, "CTFontGetAscent", c_double, c_void_p)
CTFontGetDescent = _fn(ct, "CTFontGetDescent", c_double, c_void_p)
kCTFontAttributeName = c_void_p.in_dll(ct, "kCTFontAttributeName").value
kCTForegroundColorAttributeName = c_void_p.in_dll(ct, "kCTForegroundColorAttributeName").value
TRUNCATE_END = 1

CGImageDestinationCreateWithURL = _fn(imageio, "CGImageDestinationCreateWithURL", c_void_p, c_void_p, c_void_p,
                                      c_size_t, c_void_p)
CGImageDestinationAddImage = _fn(imageio, "CGImageDestinationAddImage", None, c_void_p, c_void_p, c_void_p)
CGImageDestinationFinalize = _fn(imageio, "CGImageDestinationFinalize", c_bool, c_void_p)

SecItemCopyMatching = _fn(security, "SecItemCopyMatching", c_int32, c_void_p, POINTER(c_void_p))
SecItemAdd = _fn(security, "SecItemAdd", c_int32, c_void_p, c_void_p)
SecItemUpdate = _fn(security, "SecItemUpdate", c_int32, c_void_p, c_void_p)
SecItemDelete = _fn(security, "SecItemDelete", c_int32, c_void_p)
SEC = {name: c_void_p.in_dll(security, name).value for name in (
    "kSecClass", "kSecClassGenericPassword", "kSecAttrService", "kSecAttrAccount", "kSecValueData",
    "kSecReturnData", "kSecMatchLimit", "kSecMatchLimitOne")}
ERR_SEC_NOT_FOUND = -25300

NSDefaultRunLoopMode = c_void_p.in_dll(foundation, "NSDefaultRunLoopMode").value
WEIGHTS = {Canvas.REGULAR: c_double.in_dll(appkit, "NSFontWeightRegular").value,
           Canvas.SEMIBOLD: c_double.in_dll(appkit, "NSFontWeightSemibold").value,
           Canvas.BOLD: c_double.in_dll(appkit, "NSFontWeightBold").value}


def cfstr(text: str) -> int:
    data = text.encode("utf-8", "replace")   # a lone half-emoji becomes "?" instead of invalid UTF-8
    return CFStringCreateWithBytes(None, data, len(data), 0x08000100, False)


def cfdict(pairs: dict[int, int]) -> int:
    n = len(pairs)
    keys, values = (c_void_p * n)(*pairs.keys()), (c_void_p * n)(*pairs.values())
    return CFDictionaryCreate(None, keys, values, n, _KEY_CALLBACKS, _VALUE_CALLBACKS)


# ---------------------------------------------------------------- Objective-C

_get_class = _fn(objc, "objc_getClass", c_void_p, c_char_p)
_register_selector = _fn(objc, "sel_registerName", c_void_p, c_char_p)
_allocate_class = _fn(objc, "objc_allocateClassPair", c_void_p, c_void_p, c_char_p, c_size_t)
_register_class = _fn(objc, "objc_registerClassPair", None, c_void_p)
_add_method = _fn(objc, "class_addMethod", c_bool, c_void_p, c_void_p, c_void_p, c_char_p)
pool_push = _fn(objc, "objc_autoreleasePoolPush", c_void_p)
pool_pop = _fn(objc, "objc_autoreleasePoolPop", None, c_void_p)
_MSG = ctypes.cast(objc.objc_msgSend, c_void_p).value
# Intel returns structs over 16 bytes (NSRect) through objc_msgSend_stret; Apple silicon has no such variant.
_MSG_STRET = ctypes.cast(objc.objc_msgSend_stret, c_void_p).value if platform.machine() == "x86_64" else None

_classes: dict[str, int] = {}
_selectors: dict[str, int] = {}
_prototypes: dict[tuple, Any] = {}


def cls(name: str) -> int:
    if name not in _classes:
        _classes[name] = _get_class(name.encode())
    return _classes[name]


def sel(name: str) -> int:
    if name not in _selectors:
        _selectors[name] = _register_selector(name.encode())
    return _selectors[name]


def _argtype(a: Any) -> Any:
    if isinstance(a, bool):
        return c_bool
    if isinstance(a, float):
        return c_double
    if a is None or isinstance(a, int):
        return c_void_p if a is None or a >= 0 else c_long   # objects, selectors and NSUInteger share a register
    if isinstance(a, bytes):
        return c_char_p
    if isinstance(a, (ctypes.Structure, ctypes._SimpleCData)):
        return type(a)
    raise TypeError(f"unsupported Objective-C argument {a!r}")


def send(obj: Any, name: str, *args: Any, res: Any = c_void_p) -> Any:
    """objc_msgSend cast to the exact signature: floats as double, structs by value, the rest as words."""
    types = tuple(_argtype(a) for a in args)
    fn = _prototypes.get((res, types))
    if fn is None:
        big = isinstance(res, type) and issubclass(res, ctypes.Structure) and ctypes.sizeof(res) > 16
        fn = ctypes.CFUNCTYPE(res, c_void_p, c_void_p, *types)(_MSG_STRET if big and _MSG_STRET else _MSG)
        _prototypes[(res, types)] = fn
    return fn(obj, sel(name), *args)


def ns(text: str) -> int:
    return send(cls("NSString"), "stringWithUTF8String:", text.replace("\0", "").encode("utf-8", "replace"))


def py(obj: Any) -> str:
    data = send(obj, "UTF8String", res=c_char_p) if obj else None
    return (data or b"").decode("utf-8", "replace")


V_NONE = ctypes.CFUNCTYPE(None, c_void_p, c_void_p)
V_ID = ctypes.CFUNCTYPE(None, c_void_p, c_void_p, c_void_p)
B_NONE = ctypes.CFUNCTYPE(c_bool, c_void_p, c_void_p)
B_ID = ctypes.CFUNCTYPE(c_bool, c_void_p, c_void_p, c_void_p)
_imps: list[Any] = []   # Objective-C keeps raw pointers to these callbacks: they must never be collected


def define(name: str, base: str, methods: list[tuple[str, str, Any, Callable]]) -> int:
    existing = _get_class(name.encode())
    if existing:
        return existing
    c = _allocate_class(cls(base), name.encode(), 0)
    for selector, types, proto, fn in methods:
        imp = proto(fn)
        _imps.append(imp)
        _add_method(c, sel(selector), ctypes.cast(imp, c_void_p).value, types.encode())
    _register_class(c)
    return c


# ---------------------------------------------------------------- drawing

class MacCanvas(Canvas):
    """Core Graphics drawing for View, in points, on a context whose y axis grows downward.
    Same calls as the GDI+ Canvas; shadow(), pace_bar() and mark() are inherited."""

    def __init__(self, ctx: int) -> None:   # noqa: D107 - the GDI+ constructor is not called
        self.ctx = ctx
        self._fonts: dict[tuple, tuple] = {}
        self._colors: dict[int, int] = {}
        self.base_clip: tuple | None = None
        self._depth = 0
        CGContextSetTextMatrix(ctx, CGAffineTransform(1, 0, 0, -1, 0, 0))   # upright glyphs on a flipped context

    def close(self) -> None:
        self._pop(0)
        for font, _, _, _ in self._fonts.values():
            CFRelease(font)
        for color in self._colors.values():
            CGColorRelease(color)
        self._fonts, self._colors = {}, {}

    def font(self, face: str, size: float) -> tuple:
        key = (face, size)
        if key not in self._fonts:
            font = send(cls("NSFont"), "systemFontOfSize:weight:", float(size), WEIGHTS.get(face, WEIGHTS[Canvas.REGULAR]))
            CFRetain(font)   # NSFont is a CTFont; keep it past the autorelease pool
            self._fonts[key] = (font, None, CTFontGetAscent(font), CTFontGetDescent(font))
        return self._fonts[key]

    def _color(self, color: int) -> int:
        if color not in self._colors:
            self._colors[color] = CGColorCreateSRGB(((color >> 16) & 255) / 255, ((color >> 8) & 255) / 255,
                                                    (color & 255) / 255, ((color >> 24) & 255) / 255)
        return self._colors[color]

    def _path(self, x: float, y: float, w: float, h: float, r: float) -> int:
        r = max(0.0, min(r, w / 2, h / 2))   # Core Graphics aborts on a radius over half a side
        return CGPathCreateWithRoundedRect(_rect(x, y, w, h), r, r, None)

    def round_rect(self, x: float, y: float, w: float, h: float, r: float, fill: int | None = None,
                   stroke: int | None = None, width: float = 1.0) -> None:
        if w <= 0 or h <= 0:
            return
        path = self._path(x, y, w, h, r)
        if fill is not None:
            CGContextAddPath(self.ctx, path)
            CGContextSetFillColorWithColor(self.ctx, self._color(fill))
            CGContextFillPath(self.ctx)
        if stroke is not None:
            CGContextAddPath(self.ctx, path)
            CGContextSetStrokeColorWithColor(self.ctx, self._color(stroke))
            CGContextSetLineWidth(self.ctx, width)
            CGContextStrokePath(self.ctx)
        CGPathRelease(path)

    def rect(self, x: float, y: float, w: float, h: float, color: int) -> None:
        if w <= 0 or h <= 0:
            return
        CGContextSetFillColorWithColor(self.ctx, self._color(color))
        CGContextFillRect(self.ctx, _rect(x, y, w, h))

    def hatch(self, x: float, y: float, w: float, h: float, hex_: str) -> None:
        """The GDI+ brush's stripes: solid where (x + y) mod 7 < 2.94, faint elsewhere."""
        if w <= 0 or h <= 0:
            return
        CGContextSaveGState(self.ctx)
        CGContextClipToRect(self.ctx, _rect(x, y, w, h))
        self.rect(x, y, w, h, argb(hex_, 80))
        CGContextSetFillColorWithColor(self.ctx, self._color(argb(hex_)))
        CGContextBeginPath(self.ctx)
        k, end, y1 = math.floor((x + y) / 7.0) * 7.0 - 7.0, x + w + y + h, y + h
        while k < end:
            CGContextMoveToPoint(self.ctx, k - y, y)
            CGContextAddLineToPoint(self.ctx, k + 2.94 - y, y)
            CGContextAddLineToPoint(self.ctx, k + 2.94 - y1, y1)
            CGContextAddLineToPoint(self.ctx, k - y1, y1)
            CGContextClosePath(self.ctx)
            k += 7.0
        CGContextFillPath(self.ctx)
        CGContextRestoreGState(self.ctx)

    # Core Graphics clips only narrow, so each clip is a saved state: the base clip (the panel's
    # visible list) one level down, a rounded bar clip one level above it.
    def _push(self) -> None:
        CGContextSaveGState(self.ctx)
        self._depth += 1

    def _pop(self, depth: int) -> None:
        while self._depth > depth:
            CGContextRestoreGState(self.ctx)
            self._depth -= 1

    def _base_depth(self) -> int:
        return 1 if self.base_clip else 0

    def clip_round(self, x: float, y: float, w: float, h: float, r: float) -> None:
        self._pop(self._base_depth())
        self._push()
        if w <= 0 or h <= 0:
            CGContextClipToRect(self.ctx, _rect(x, y, 0, 0))
            return
        path = self._path(x, y, w, h, r)
        CGContextAddPath(self.ctx, path)
        CGContextClip(self.ctx)
        CGPathRelease(path)

    def clip_rect(self, x: float, y: float, w: float, h: float) -> None:
        self._pop(0)
        self.base_clip = (x, y, w, h)
        self._push()
        CGContextClipToRect(self.ctx, _rect(x, y, w, h))

    def unclip(self) -> None:
        self._pop(self._base_depth())

    def unclip_all(self) -> None:
        self.base_clip = None
        self._pop(0)

    def _line(self, text: str, face: str, size: float, color: int) -> int:
        s = cfstr(text) or cfstr("?")
        attrs = cfdict({kCTFontAttributeName: self.font(face, size)[0], kCTForegroundColorAttributeName: self._color(color)})
        attributed = CFAttributedStringCreate(None, s, attrs)
        line = CTLineCreateWithAttributedString(attributed)
        for obj in (attributed, attrs, s):
            CFRelease(obj)
        return line

    def measure(self, text: str, face: str, size: float) -> float:
        line = self._line(text, face, size, C["ink"])
        width = CTLineGetTypographicBounds(line, None, None, None)
        CFRelease(line)
        return width

    def runs(self, runs: list[tuple[str, str, float, int]], x: float, top: float, height: float,
             max_w: float, right: bool = False) -> float:
        """Draws (text, face, size, color) runs on one shared baseline, centred in the line box.
        Trims the last run that doesn't fit with an ellipsis. Returns the width used."""
        asc = max(self.font(f, s)[2] for _, f, s, _ in runs)
        desc = max(self.font(f, s)[3] for _, f, s, _ in runs)
        base = top + (height - (asc + desc)) / 2 + asc
        lines = [self._line(t, f, s, c) for t, f, s, c in runs]
        widths = [CTLineGetTypographicBounds(line, None, None, None) for line in lines]
        if right:
            x = x + max_w - min(sum(widths), max_w)
        used = 0.0
        try:
            for (_, face, size, color), line, w in zip(runs, lines, widths):
                room = max_w - used
                if room <= 4:
                    break
                cut = None
                if w > room:
                    ellipsis = self._line("…", face, size, color)
                    cut = CTLineCreateTruncatedLine(line, room, TRUNCATE_END, ellipsis)
                    CFRelease(ellipsis)
                    if not cut:
                        break   # not even the ellipsis fits
                CGContextSetTextPosition(self.ctx, x + used, base)
                CTLineDraw(cut or line, self.ctx)
                if cut:
                    CFRelease(cut)
                used += min(w, room)
        finally:
            for line in lines:
                CFRelease(line)
        return used

    def line(self, pts: list[tuple[float, float]], color: int, width: float) -> None:
        if len(pts) < 2:
            return
        CGContextSetStrokeColorWithColor(self.ctx, self._color(color))
        CGContextSetLineWidth(self.ctx, width)
        CGContextSetLineCap(self.ctx, 1)    # round
        CGContextSetLineJoin(self.ctx, 1)   # round
        CGContextBeginPath(self.ctx)
        CGContextMoveToPoint(self.ctx, *pts[0])
        for p in pts[1:]:
            CGContextAddLineToPoint(self.ctx, *p)
        CGContextStrokePath(self.ctx)


def draw_image(w: float, h: float, scale: float, paint: Callable[[MacCanvas], None]) -> int:
    """A CGImage of w x h points at scale pixels per point; the caller releases it."""
    bw, bh = max(1, int(math.ceil(w * scale))), max(1, int(math.ceil(h * scale)))
    space = CGColorSpaceCreateWithName(kCGColorSpaceSRGB)
    ctx = CGBitmapContextCreate(None, bw, bh, 8, 0, space, 1)   # 8-bit RGBA, premultiplied
    CGColorSpaceRelease(space)
    if not ctx:
        raise OSError("Core Graphics could not make a bitmap")
    CGContextTranslateCTM(ctx, 0, bh)
    CGContextScaleCTM(ctx, scale, -scale)
    c = MacCanvas(ctx)
    try:
        paint(c)
    finally:
        c.close()
    image = CGBitmapContextCreateImage(ctx)
    CGContextRelease(ctx)
    return image


def save_png(image: int, path: str) -> None:
    raw = os.fsencode(os.path.abspath(path))
    url = CFURLCreateFromFileSystemRepresentation(None, raw, len(raw), False)
    kind = cfstr("public.png")
    dest = CGImageDestinationCreateWithURL(url, kind, 1, None)
    CFRelease(kind)
    CFRelease(url)
    if not dest:
        raise OSError(f"can't write {path}")
    CGImageDestinationAddImage(dest, image, None)
    ok = CGImageDestinationFinalize(dest)
    CFRelease(dest)
    if not ok:
        raise OSError(f"can't write {path}")


def render_png(view: View, path: str, scale: float = 2.0, backdrop: str | None = None) -> None:
    w, h = view.size()

    def paint(c: MacCanvas) -> None:
        if backdrop:
            c.rect(0, 0, w, h, argb(backdrop))
        view.draw(c)

    image = draw_image(w, h, scale, paint)
    try:
        save_png(image, path)
    finally:
        CGImageRelease(image)


def paint_tray(c: MacCanvas, top: dict | None) -> None:
    """The Windows tray icon, in its 16-unit grid: a ring in the verdict's colour around the top bar."""
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


# ---------------------------------------------------------------- settings, Keychain, login item

KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT = "Token Pace Mini", "server token"
AGENT_LABEL = "app.tokenpace.mini"


def support_dir() -> str:
    return os.path.expanduser("~/Library/Application Support/TokenPace")


def log_path() -> str:
    return os.path.expanduser("~/Library/Logs/TokenPace/mini.log")


def agent_path() -> str:
    return os.path.expanduser(f"~/Library/LaunchAgents/{AGENT_LABEL}.plist")


def _keychain_query(service: str, extra: dict[int, int] | None = None) -> tuple[int, list[int]]:
    owned = [cfstr(service), cfstr(KEYCHAIN_ACCOUNT)]
    pairs = {SEC["kSecClass"]: SEC["kSecClassGenericPassword"], SEC["kSecAttrService"]: owned[0],
             SEC["kSecAttrAccount"]: owned[1], **(extra or {})}
    return cfdict(pairs), owned


def keychain_get(service: str = KEYCHAIN_SERVICE) -> str:
    query, owned = _keychain_query(service, {SEC["kSecReturnData"]: kCFBooleanTrue,
                                             SEC["kSecMatchLimit"]: SEC["kSecMatchLimitOne"]})
    out = c_void_p()
    status = SecItemCopyMatching(query, byref(out))
    for obj in [query, *owned]:
        CFRelease(obj)
    if status == ERR_SEC_NOT_FOUND:
        return ""
    if status != 0 or not out.value:
        raise OSError(f"Keychain read failed ({status})")
    try:
        data = ctypes.string_at(CFDataGetBytePtr(out.value), CFDataGetLength(out.value))
    finally:
        CFRelease(out.value)
    return data.decode("utf-8", "replace")


def keychain_set(token: str, service: str = KEYCHAIN_SERVICE) -> None:
    """Stores the token, or deletes it when empty."""
    query, owned = _keychain_query(service)
    try:
        if not token:
            status = SecItemDelete(query)
            if status not in (0, ERR_SEC_NOT_FOUND):
                raise OSError(f"Keychain delete failed ({status})")
            return
        raw = token.encode("utf-8")
        data = CFDataCreate(None, raw, len(raw))
        change = cfdict({SEC["kSecValueData"]: data})
        status = SecItemUpdate(query, change)
        if status == ERR_SEC_NOT_FOUND:
            item, item_owned = _keychain_query(service, {SEC["kSecValueData"]: data})
            status = SecItemAdd(item, None)
            for obj in [item, *item_owned]:
                CFRelease(obj)
        CFRelease(change)
        CFRelease(data)
        if status != 0:
            raise OSError(f"Keychain write failed ({status})")
    finally:
        for obj in [query, *owned]:
            CFRelease(obj)


class MacSettings:
    """mini.json under Application Support, with positions in points from the top left of the main
    screen; the token sits in the Keychain and is written only when it changes."""

    def __init__(self) -> None:
        self.server, self.token = "", ""
        self.left: float | None = None
        self.top: float | None = None
        self.expanded = False
        self._stored_token = ""

    @staticmethod
    def path() -> str:
        return os.path.join(support_dir(), "mini.json")

    @classmethod
    def load(cls) -> "MacSettings":
        s = cls()
        try:
            with open(cls.path(), encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            d = {}
        if isinstance(d, dict):
            s.server = normalize(d.get("server") if isinstance(d.get("server"), str) else "")
            s.left = _num(d, "left", None)
            s.top = _num(d, "top", None)
            s.expanded = d.get("expanded") is True
        if s.server:
            try:
                s.token = keychain_get()
            except OSError:
                s.token = ""
        s._stored_token = s.token
        return s

    def save(self) -> None:
        d: dict[str, Any] = {"server": self.server, "expanded": self.expanded}
        if self.left is not None and self.top is not None:
            d["left"], d["top"] = round(self.left), round(self.top)
        path = self.path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(d, f)
            os.replace(tmp, path)
        except OSError:
            pass
        if self.token != self._stored_token:
            try:
                keychain_set(self.token)
                self._stored_token = self.token
            except OSError:
                pass


def start_args(parent: str | None = None) -> list[str]:
    parent = parent or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    installed = {os.path.abspath(p) for p in site.getsitepackages() + [site.getusersitepackages()]}
    if os.path.abspath(parent) in installed:
        return [sys.executable, "-m", "tokenpace", "mini"]
    # Run from a clone: launchd starts in /, so pass where the package is.
    return [sys.executable, "-c", "import sys; sys.path.insert(0, sys.argv[1]); from tokenpace.mini import run; run([])",
            parent]


def starts_at_login() -> bool:
    try:
        with open(agent_path(), "rb") as f:
            d = plistlib.load(f)
    except (OSError, ValueError, plistlib.InvalidFileException):
        return False
    return isinstance(d, dict) and d.get("ProgramArguments") == start_args()


def set_starts_at_login(on: bool) -> None:
    path = agent_path()
    if not on:
        if os.path.exists(path):
            os.remove(path)
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    os.makedirs(os.path.dirname(log_path()), exist_ok=True)
    agent = {"Label": AGENT_LABEL, "ProgramArguments": start_args(), "RunAtLoad": True, "KeepAlive": False,
             "ProcessType": "Interactive", "LimitLoadToSessionType": "Aqua",
             "StandardOutPath": log_path(), "StandardErrorPath": log_path()}
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        plistlib.dump(agent, f)
    os.replace(tmp, path)


def single_instance() -> int | None:
    """An exclusive lock held for the life of the process; None when another copy has it."""
    os.makedirs(support_dir(), exist_ok=True)
    fd = os.open(os.path.join(support_dir(), "mini.lock"), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd


# ---------------------------------------------------------------- the window and menu bar icon

_APP: "MacMini | None" = None


def _on(handler: str) -> Callable:
    def imp(_self: int, _cmd: int, arg: int) -> None:
        if _APP is not None:
            try:
                getattr(_APP, handler)(arg)
            except Exception:   # noqa: BLE001 - an exception must not cross into AppKit
                traceback.print_exc()
    return imp


def _draw(_self: int, _cmd: int) -> None:   # drawRect: also passes the dirty rect, unused here
    if _APP is not None:
        try:
            _APP.draw()
        except Exception:   # noqa: BLE001
            traceback.print_exc()


def _yes(_self: int, _cmd: int) -> bool:
    return True


def _yes_for(_self: int, _cmd: int, _arg: int) -> bool:
    return True


def _no(_self: int, _cmd: int) -> bool:
    return False


VIEW_METHODS = [
    ("drawRect:", "v@:{CGRect={CGPoint=dd}{CGSize=dd}}", V_NONE, _draw),
    ("isFlipped", "B@:", B_NONE, _yes),                    # y grows downward, as View lays out
    ("acceptsFirstMouse:", "B@:@", B_ID, _yes_for),         # a click works without activating first
    ("mouseDownCanMoveWindow", "B@:", B_NONE, _no),         # dragging is handled here, with click detection
    ("mouseDown:", "v@:@", V_ID, _on("mouse_down")),
    ("mouseDragged:", "v@:@", V_ID, _on("mouse_dragged")),
    ("mouseUp:", "v@:@", V_ID, _on("mouse_up")),
    ("rightMouseDown:", "v@:@", V_ID, _on("show_context_menu")),
    ("scrollWheel:", "v@:@", V_ID, _on("scroll")),
    ("mouseMoved:", "v@:@", V_ID, _on("mouse_moved")),
    ("mouseExited:", "v@:@", V_ID, _on("mouse_exited")),
]
DELEGATE_METHODS = [
    ("tick:", "v@:@", V_ID, _on("on_tick")),
    ("poll:", "v@:@", V_ID, _on("on_poll")),
    ("dataReady:", "v@:@", V_ID, _on("on_data")),
    ("menuNeedsUpdate:", "v@:@", V_ID, _on("fill_menu")),
    ("menuAction:", "v@:@", V_ID, _on("menu_action")),
    ("screensChanged:", "v@:@", V_ID, _on("on_tick")),
    ("didWake:", "v@:@", V_ID, _on("on_wake")),
    ("openSettings:", "v@:@", V_ID, _on("on_open_settings")),
]

NS_FLOATING_WINDOW_LEVEL = 3
NS_NONACTIVATING_PANEL = 1 << 7
NS_ALL_SPACES, NS_STATIONARY, NS_IGNORES_CYCLE, NS_FULLSCREEN_AUX = 1 << 0, 1 << 4, 1 << 6, 1 << 8
NS_CONTROL_KEY = 1 << 18
NS_ALERT_FIRST_BUTTON = 1000
MENU_ITEMS = 8


class MacMini:
    def __init__(self, settings: MacSettings) -> None:
        self.settings = settings
        self.view = View()
        self.view.expanded = settings.expanded
        self.fetching = False
        self.generation = 0
        self.result: tuple[int, list | None, str | None] | None = None
        self.lock = threading.Lock()
        self.down: tuple | None = None
        self.dragging = False
        self.visible = True
        self.dialog_open = False
        self.quit_after_dialog = False
        self._icon_key: Any = ()

        self.app = send(cls("NSApplication"), "sharedApplication")
        send(self.app, "setActivationPolicy:", 1)   # accessory: no Dock icon, no app menu
        define("TokenPaceMiniView", "NSView", VIEW_METHODS)
        define("TokenPaceMiniDelegate", "NSObject", DELEGATE_METHODS)
        self.delegate = send(send(cls("TokenPaceMiniDelegate"), "alloc"), "init")
        icon = draw_image(32, 32, 8, lambda c: c.mark(0, 0, 32))
        app_icon = send(send(cls("NSImage"), "alloc"), "initWithCGImage:size:", icon, CGSize(128, 128))
        CGImageRelease(icon)
        send(self.app, "setApplicationIconImage:", app_icon)   # shown in the settings dialog
        send(app_icon, "release")
        self.install_edit_menu()

        self.panel = send(send(cls("NSPanel"), "alloc"), "initWithContentRect:styleMask:backing:defer:",
                          _rect(0, 0, 100, 100), NS_NONACTIVATING_PANEL, 2, False)
        for name, value in (("setOpaque:", False), ("setHasShadow:", False), ("setLevel:", NS_FLOATING_WINDOW_LEVEL),
                            ("setHidesOnDeactivate:", False), ("setReleasedWhenClosed:", False),
                            ("setFloatingPanel:", True), ("setBecomesKeyOnlyIfNeeded:", True), ("setMovable:", False),
                            ("setCollectionBehavior:", NS_ALL_SPACES | NS_STATIONARY | NS_IGNORES_CYCLE | NS_FULLSCREEN_AUX)):
            send(self.panel, name, value, res=None)
        send(self.panel, "setBackgroundColor:", send(cls("NSColor"), "clearColor"), res=None)
        self.nsview = send(send(cls("TokenPaceMiniView"), "alloc"), "initWithFrame:", _rect(0, 0, 100, 100))
        send(self.panel, "setContentView:", self.nsview, res=None)
        area = send(send(cls("NSTrackingArea"), "alloc"), "initWithRect:options:owner:userInfo:",
                    _rect(0, 0, 0, 0), 0x01 | 0x02 | 0x80 | 0x200, self.nsview, None)   # moves, exits, always, visible rect
        send(self.nsview, "addTrackingArea:", area, res=None)
        send(area, "release", res=None)

        self.menu = send(send(cls("NSMenu"), "alloc"), "initWithTitle:", ns("Token Pace"))
        send(self.menu, "setAutoenablesItems:", False, res=None)
        send(self.menu, "setDelegate:", self.delegate, res=None)   # built when opened, so it is always current
        self.item = send(send(send(cls("NSStatusBar"), "systemStatusBar"), "statusItemWithLength:", -1.0), "retain")
        # Keeps the place the user Cmd-drags the icon to. On a MacBook with a full menu bar macOS hides
        # icons that don't fit beside the notch; the pill's right-click menu has the same entries.
        send(self.item, "setAutosaveName:", ns(AGENT_LABEL), res=None)
        send(self.item, "setMenu:", self.menu, res=None)
        self.button = send(self.item, "button")

        for interval, name in ((TICK_SECONDS, "tick:"), (POLL_SECONDS, "poll:")):
            send(cls("NSTimer"), "scheduledTimerWithTimeInterval:target:selector:userInfo:repeats:",
                 float(interval), self.delegate, sel(name), None, True)
        center = send(cls("NSNotificationCenter"), "defaultCenter")
        send(center, "addObserver:selector:name:object:", self.delegate, sel("screensChanged:"),
             c_void_p.in_dll(appkit, "NSApplicationDidChangeScreenParametersNotification").value, None, res=None)
        workspace = send(send(cls("NSWorkspace"), "sharedWorkspace"), "notificationCenter")
        send(workspace, "addObserver:selector:name:object:", self.delegate, sel("didWake:"),
             c_void_p.in_dll(appkit, "NSWorkspaceDidWakeNotification").value, None, res=None)

        self.place_initially()
        self.render()
        send(self.panel, "orderFrontRegardless", res=None)
        self.refresh()

    def install_edit_menu(self) -> None:
        """Without a main menu, Cmd-V and friends do nothing in the settings fields."""
        bar = send(send(cls("NSMenu"), "alloc"), "init")
        for title, entries in (("Token Pace Mini", [("Quit Token Pace Mini", "menuAction:", "q")]),
                               ("Edit", [("Undo", "undo:", "z"), ("Cut", "cut:", "x"), ("Copy", "copy:", "c"),
                                         ("Paste", "paste:", "v"), ("Select All", "selectAll:", "a")])):
            holder = send(send(cls("NSMenuItem"), "alloc"), "init")
            sub = send(send(cls("NSMenu"), "alloc"), "initWithTitle:", ns(title))
            for label, action, key in entries:
                entry = send(sub, "addItemWithTitle:action:keyEquivalent:", ns(label), sel(action), ns(key))
                if action == "menuAction:":
                    send(entry, "setTarget:", self.delegate, res=None)
                    send(entry, "setTag:", MENU_ITEMS, res=None)
            send(holder, "setSubmenu:", sub, res=None)
            send(bar, "addItem:", holder, res=None)
            send(holder, "release", res=None)
            send(sub, "release", res=None)
        send(self.app, "setMainMenu:", bar, res=None)
        send(bar, "release", res=None)

    # --- geometry: positions are points from the top left of the main screen
    def screens(self) -> tuple[float, list[tuple[tuple, tuple]]]:
        arr = send(cls("NSScreen"), "screens")
        found = []
        for i in range(send(arr, "count", res=c_size_t)):
            s = send(arr, "objectAtIndex:", i)
            found.append((send(s, "frame", res=CGRect), send(s, "visibleFrame", res=CGRect)))
        if not found:
            return 900.0, [((0, 0, 1440, 900), (0, 25, 1440, 900))]
        h0 = found[0][0].size.height   # the main screen's frame starts at (0, 0)

        def flip(r: CGRect) -> tuple:
            top = h0 - (r.origin.y + r.size.height)
            return r.origin.x, top, r.origin.x + r.size.width, top + r.size.height
        return h0, [(flip(f), flip(v)) for f, v in found]

    def work_area(self, left: float, top: float) -> tuple[float, float, float, float]:
        _, screens = self.screens()
        px, py = left + 30, top + 20

        def distance(frame: tuple) -> float:
            dx = max(frame[0] - px, 0.0, px - frame[2])
            dy = max(frame[1] - py, 0.0, py - frame[3])
            return dx * dx + dy * dy
        return min(screens, key=lambda s: distance(s[0]))[1]

    def place_initially(self) -> None:
        s = self.settings
        _, screens = self.screens()
        if s.left is not None and s.top is not None and any(
                f[0] <= s.left + 30 < f[2] and f[1] <= s.top + 20 < f[3] for f, _ in screens):
            return
        _, t, r, _ = screens[0][1]
        s.left, s.top = r - WIDTH - 12, t + 12   # top right, under the menu bar icon

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
            over = (8.0 + v.panel_h()) - room
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
        h0, _ = self.screens()
        x, top = s.left - PAD, s.top - v.pill_y()
        send(self.panel, "setFrame:display:", _rect(x, h0 - (top + h), w, h), True, res=None)
        send(self.nsview, "setNeedsDisplay:", True, res=None)
        self.update_tray()

    def draw(self) -> None:
        ctx = send(send(cls("NSGraphicsContext"), "currentContext"), "CGContext")
        w, h = self.view.size()
        CGContextClearRect(ctx, _rect(0, 0, w, h))
        c = MacCanvas(ctx)
        try:
            self.view.draw(c)
        finally:
            c.close()

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
            pool = pool_push()
            try:
                send(self.delegate, "performSelectorOnMainThread:withObject:waitUntilDone:", sel("dataReady:"), None,
                     False, res=None)
            finally:
                pool_pop(pool)

        threading.Thread(target=work, daemon=True).start()

    def on_data(self, _arg: int) -> None:
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

    def on_tick(self, _arg: int) -> None:
        self.render()

    def on_poll(self, _arg: int) -> None:
        self.refresh()

    def on_wake(self, _arg: int) -> None:
        # The network needs a moment after sleep; read again shortly instead of showing an error.
        send(self.delegate, "performSelector:withObject:afterDelay:", sel("poll:"), None, 5.0, res=None)

    def on_open_settings(self, _arg: int) -> None:
        self.open_settings()

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
            subprocess.Popen(["/usr/bin/open", self.settings.server + "/"])

    def activate(self) -> None:
        send(self.app, "activateIgnoringOtherApps:", True, res=None)

    def open_settings(self) -> None:
        if self.dialog_open:
            self.activate()
            return
        self.dialog_open = True
        try:
            self._settings_dialog()
        finally:
            self.dialog_open = False
        if self.quit_after_dialog:
            self.quit()

    def _settings_dialog(self) -> None:
        s = self.settings
        server_text, token_text, start = s.server or "http://localhost:8787", s.token, starts_at_login()
        note = ""
        while True:
            alert = send(send(cls("NSAlert"), "alloc"), "init")
            send(alert, "setMessageText:", ns("Token Pace Mini"), res=None)
            send(alert, "setInformativeText:", ns(note or "Your Token Pace server's address, as you open it in a "
                                                  "browser, and its token if it has one. The token is kept in "
                                                  "your login Keychain."), res=None)
            box = send(send(cls("NSView"), "alloc"), "initWithFrame:", _rect(0, 0, 320, 134))
            fields = []
            for label, y, klass, value in (("Server address", 114, "NSTextField", server_text),
                                           ("Token (if the server has one)", 60, "NSSecureTextField", token_text)):
                caption = send(cls("NSTextField"), "labelWithString:", ns(label))
                send(caption, "setFrame:", _rect(0, y, 320, 17), res=None)
                send(box, "addSubview:", caption, res=None)
                field = send(send(cls(klass), "alloc"), "initWithFrame:", _rect(0, y - 26, 320, 24))
                send(field, "setStringValue:", ns(value), res=None)
                send(box, "addSubview:", field, res=None)
                send(field, "release", res=None)
                fields.append(field)
            check = send(cls("NSButton"), "checkboxWithTitle:target:action:", ns("Start at login"), None, None)
            send(check, "setFrame:", _rect(0, 0, 320, 20), res=None)
            send(check, "setState:", 1 if start else 0, res=None)
            send(box, "addSubview:", check, res=None)
            send(alert, "setAccessoryView:", box, res=None)
            send(box, "release", res=None)
            send(alert, "addButtonWithTitle:", ns("Test and save"))
            send(alert, "addButtonWithTitle:", ns("Cancel"))
            send(alert, "layout", res=None)
            window = send(alert, "window")
            send(window, "setInitialFirstResponder:", fields[0], res=None)
            send(window, "setLevel:", NS_FLOATING_WINDOW_LEVEL, res=None)
            self.activate()
            answer = send(alert, "runModal", res=c_long)
            server_text = py(send(fields[0], "stringValue"))
            token_text = py(send(fields[1], "stringValue")).strip()
            start = send(check, "state", res=c_long) == 1
            send(alert, "release", res=None)
            if answer != NS_ALERT_FIRST_BUTTON or self.quit_after_dialog:
                return
            url = normalize(server_text)
            if not url:
                note = "Enter an http:// or https:// address."
                continue
            err = self.check(url, token_text)
            if err:
                note = err + ". Check the address and token."
                continue
            s.server, s.token = url, token_text
            s.save()
            try:
                set_starts_at_login(start)
            except OSError:
                pass
            self.server_changed()
            return

    def check(self, url: str, token: str) -> str | None:
        """Reads the server off the main thread while the run loop keeps the app responsive."""
        done, out = threading.Event(), {}

        def work() -> None:
            try:
                fetch(url, token)
            except MiniError as exc:
                out["err"] = str(exc)
            except Exception:   # noqa: BLE001
                out["err"] = "Reading failed"
            finally:
                done.set()

        threading.Thread(target=work, daemon=True).start()
        loop = send(cls("NSRunLoop"), "currentRunLoop")
        while not done.is_set():
            soon = send(cls("NSDate"), "dateWithTimeIntervalSinceNow:", 0.05)
            if not send(loop, "runMode:beforeDate:", NSDefaultRunLoopMode, soon, res=c_bool):
                time.sleep(0.05)
        return out.get("err")

    # --- menu bar icon and menu
    def summary(self) -> str:
        groups = self.view.groups
        top = top_plan(groups) if groups is not None else None
        if top:
            line = f"{top['name']}: {top['verdict']} · {pace_text(top)} · {when_text(top)}"
            return line + (" · old reading" if self.view.error else "")
        if not self.settings.server:
            return "Token Pace · not set up"
        return "Token Pace · " + (self.view.error or "no reading yet")

    def update_tray(self) -> None:
        groups = self.view.groups
        top = top_plan(groups) if groups is not None else None
        key = (top["level"], round(top["used"]), None if top["elapsed"] is None else round(top["elapsed"])) if top else None
        if key != self._icon_key:
            image = draw_image(16, 16, 2.25, lambda c: paint_tray(c, top))   # 18 points at 2x
            icon = send(send(cls("NSImage"), "alloc"), "initWithCGImage:size:", image, CGSize(18, 18))
            CGImageRelease(image)
            send(self.button, "setImage:", icon, res=None)
            send(icon, "release", res=None)
            self._icon_key = key
        send(self.button, "setToolTip:", ns(self.summary()), res=None)

    def fill_menu(self, menu: int) -> None:
        send(menu, "removeAllItems", res=None)

        def add(title: str, tag: int = 0, state: int = 0) -> None:
            entry = send(send(cls("NSMenuItem"), "alloc"), "initWithTitle:action:keyEquivalent:", ns(title),
                         sel("menuAction:") if tag else None, ns(""))
            send(entry, "setTarget:", self.delegate, res=None)
            send(entry, "setTag:", tag, res=None)
            send(entry, "setEnabled:", bool(tag), res=None)
            send(entry, "setState:", state, res=None)
            send(menu, "addItem:", entry, res=None)
            send(entry, "release", res=None)

        def separator() -> None:
            send(menu, "addItem:", send(cls("NSMenuItem"), "separatorItem"), res=None)

        add(self.summary())
        separator()
        add("Hide pill" if self.visible else "Show pill", 1)
        add("Show all plans", 2)
        add("Reset position", 3)
        add("Refresh now", 4)
        add("Open page", 5)
        separator()
        add("Settings…", 6)
        add("Start at login", 7, 1 if starts_at_login() else 0)
        separator()
        add("Quit Token Pace Mini", MENU_ITEMS)

    def menu_action(self, sender: int) -> None:
        tag = send(sender, "tag", res=c_long)
        if tag == 1:
            self.toggle_visible()
        elif tag == 2:
            if not self.visible:
                self.toggle_visible()
            if not self.settings.expanded:
                self.act("toggle")
        elif tag == 3:
            self.settings.left = self.settings.top = None
            self.place_initially()
            self.settings.save()
            if not self.visible:
                self.toggle_visible()
            self.render()
        elif tag == 4:
            self.refresh()
        elif tag == 5:
            self.open_page()
        elif tag == 6:
            # After the menu has closed: a modal dialog inside menu tracking misbehaves.
            send(self.delegate, "performSelector:withObject:afterDelay:", sel("openSettings:"), None, 0.0, res=None)
        elif tag == 7:
            try:
                set_starts_at_login(not starts_at_login())
            except OSError:
                pass
        elif tag == MENU_ITEMS:
            self.quit()

    def show_context_menu(self, event: int) -> None:
        send(cls("NSMenu"), "popUpContextMenu:withEvent:forView:", self.menu, event, self.nsview, res=None)

    def toggle_visible(self) -> None:
        self.visible = not self.visible
        if self.visible:
            self.render()
            send(self.panel, "orderFrontRegardless", res=None)
        else:
            send(self.panel, "orderOut:", None, res=None)

    def quit(self) -> None:
        if self.dialog_open:
            self.quit_after_dialog = True
            send(self.app, "abortModal", res=None)   # open_settings() finishes the quit once the dialog is gone
            return
        self.settings.save()
        send(send(cls("NSStatusBar"), "systemStatusBar"), "removeStatusItem:", self.item, res=None)
        send(self.app, "terminate:", None, res=None)

    # --- input
    def local(self, event: int) -> tuple[float, float]:
        p = send(event, "locationInWindow", res=CGPoint)
        q = send(self.nsview, "convertPoint:fromView:", p, None, res=CGPoint)
        return q.x, q.y

    def hit(self, x: float, y: float) -> tuple[str, str] | None:
        for hx, hy, hw, hh, action, cursor in reversed(self.view.hits):
            if hx <= x <= hx + hw and hy <= y <= hy + hh:
                return action, cursor
        return None

    def mouse_down(self, event: int) -> None:
        if send(event, "modifierFlags", res=c_size_t) & NS_CONTROL_KEY:
            self.show_context_menu(event)
            return
        h = self.hit(*self.local(event))
        if h:
            m = send(cls("NSEvent"), "mouseLocation", res=CGPoint)
            f = send(self.panel, "frame", res=CGRect)
            self.down = (m.x, m.y, f.origin.x, f.origin.y, h[0])

    def mouse_dragged(self, _event: int) -> None:
        if not self.down or self.down[4] not in ("pill", "panel"):
            return
        x0, y0, fx, fy, _ = self.down
        m = send(cls("NSEvent"), "mouseLocation", res=CGPoint)
        if self.dragging or abs(m.x - x0) + abs(m.y - y0) > 4:
            self.dragging = True
            send(self.panel, "setFrameOrigin:", CGPoint(fx + m.x - x0, fy + m.y - y0), res=None)

    def mouse_up(self, event: int) -> None:
        if not self.down:
            return
        action, was_dragging = self.down[4], self.dragging
        self.end_drag()
        if was_dragging:
            return
        h = self.hit(*self.local(event))
        if h and h[0] == action:
            self.act("toggle" if action == "pill" else action)

    def end_drag(self) -> None:
        self.down = None
        if self.dragging:
            self.dragging = False
            f = send(self.panel, "frame", res=CGRect)
            h0, _ = self.screens()
            self.settings.left = f.origin.x + PAD
            self.settings.top = h0 - (f.origin.y + f.size.height) + self.view.pill_y()
            self.settings.save()
            self.render()   # the panel may now open on the other side

    def scroll(self, event: int) -> None:
        v = self.view
        if not (v.expanded and v.max_list_h is not None):
            return
        dy = send(event, "scrollingDeltaY", res=c_double)
        if not send(event, "hasPreciseScrollingDeltas", res=c_bool):
            dy *= 12   # a wheel reports lines
        v.scroll = max(0.0, min(v.scroll - dy, v.list_content_h - v.list_view[3]))
        send(self.nsview, "setNeedsDisplay:", True, res=None)

    def mouse_moved(self, event: int) -> None:
        h = self.hit(*self.local(event))
        cursor = "pointingHandCursor" if h and h[1] == "hand" else "arrowCursor"
        send(send(cls("NSCursor"), cursor), "set", res=None)

    def mouse_exited(self, _event: int) -> None:
        send(send(cls("NSCursor"), "arrowCursor"), "set", res=None)


def run_mac(args: Any) -> int:
    """tokenpace mini on macOS, with the options parsed by tokenpace.mini.run."""
    pool = pool_push()
    try:
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
        lock = single_instance()
        if lock is None:
            return 0   # already running: its menu bar icon is there
        signal.signal(signal.SIGINT, signal.SIG_DFL)   # Ctrl-C quits even while AppKit holds the thread
        settings = MacSettings.load()
        if args.server:
            settings.server = normalize(args.server)
            settings.token = os.environ.get("TOKENPACE_TOKEN", settings.token)
            settings.save()
        global _APP
        _APP = app = MacMini(settings)
        if not settings.server:
            send(app.delegate, "performSelector:withObject:afterDelay:", sel("openSettings:"), None, 0.3, res=None)
        send(app.app, "run", res=None)
        return 0
    finally:
        pool_pop(pool)
