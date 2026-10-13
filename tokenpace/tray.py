"""Optional GTK 3 / Ayatana AppIndicator client that shows the server's own page.

The popover is the same index.html the browser gets, so the tray cannot drift from
the page's design: only the page chrome that does not fit a panel is hidden.
"""
from __future__ import annotations

import json
import threading
import urllib.parse
import urllib.request
import webbrowser

from .providers import _OPENER

WIDTH = 360          # the page is one column below 860px, like a panel dropdown
HEIGHT = 680
POLL_SECONDS = 30

# The groups are the answer; the hero tiles, the reset timeline and the legal
# footer need more room than a dropdown has.
POPOVER_CSS = """
header.top, .topgrid, .legend, footer, .gate, .err { display: none !important; }
main { max-width: none !important; padding: 10px 8px 14px !important; }
.group { padding: 4px 11px 6px !important; }
"""

POPOVER_SCRIPT = ("""(function () {
  var s = document.createElement("style");
  s.textContent = %s;
  (document.head || document.documentElement).appendChild(s);
})();""" % json.dumps(POPOVER_CSS))


def trouble(view: dict) -> bool:
    """The page shows every number; the icon only has to say whether to warn."""
    return any(s.get("status") != "ok" for s in (view or {}).get("subscriptions") or [])


class Client:
    def __init__(self, url: str, token: str | None = None):
        parts = urllib.parse.urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.netloc or parts.query or parts.fragment or parts.username:
            raise ValueError("tray URL must be an http(s) server address without credentials, query or fragment")
        self.url = url.rstrip("/")
        self.token = token

    def request(self, refresh: bool = False) -> dict:
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        req = urllib.request.Request(self.url + ("/api/refresh" if refresh else "/api/usage"),
                                     data=b"{}" if refresh else None, headers=headers)
        with _OPENER.open(req, timeout=10) as response:
            return json.loads(response.read(1024 * 1024))

    def dashboard(self) -> str:
        return self.url + "/" + ("#token=" + urllib.parse.quote(self.token, safe="") if self.token else "")


def run(url: str, token: str | None = None, show: bool = False) -> int:
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        gi.require_version("AyatanaAppIndicator3", "0.1")
        gi.require_version("WebKit2", "4.1")
        from gi.repository import AyatanaAppIndicator3, Gdk, GLib, Gtk, WebKit2
    except (ImportError, ValueError):
        raise ValueError("Linux tray needs system Python, python3-gi, gir1.2-gtk-3.0, "
                         "gir1.2-webkit2-4.1 and gir1.2-ayatanaappindicator3-0.1") from None

    client = Client(url, token)

    manager = WebKit2.UserContentManager()
    manager.add_script(WebKit2.UserScript.new(POPOVER_SCRIPT, WebKit2.UserContentInjectedFrames.TOP_FRAME,
                                              WebKit2.UserScriptInjectionTime.START, None, None))
    web = WebKit2.WebView.new_with_user_content_manager(manager)
    web.get_settings().set_enable_developer_extras(False)
    web.load_uri(client.dashboard())

    # A normal undecorated window: desktops give it keyboard focus, so Escape and
    # clicking elsewhere close it. A POPUP window is override-redirect on X11 and
    # never receives focus. Wayland compositors choose where it appears.
    window = Gtk.Window(title="Token Pace")
    window.set_decorated(False)
    window.set_skip_taskbar_hint(True)
    window.set_skip_pager_hint(True)
    window.set_keep_above(True)
    window.set_type_hint(Gdk.WindowTypeHint.UTILITY)
    window.set_resizable(False)
    window.set_default_size(WIDTH, HEIGHT)
    # AyatanaAppIndicator3 reports no panel position, so open under the pointer (X11).
    window.set_position(Gtk.WindowPosition.MOUSE)
    window.add(web)
    window.connect("focus-out-event", lambda *_: (window.hide(), True)[1])
    window.connect("delete-event", lambda *_: (window.hide(), True)[1])
    window.connect("key-press-event",
                   lambda _, ev: (window.hide(), True)[1] if ev.keyval == Gdk.KEY_Escape else False)

    state = {"busy": False, "error": None}

    def toggle(*_):
        if window.get_visible():
            window.hide()
        else:
            window.show_all()
            # The menu click's timestamp lets the window take focus despite
            # focus-stealing prevention.
            window.present_with_time(Gtk.get_current_event_time())

    def refresh():
        if state["busy"]:
            return
        state["busy"] = True
        indicator.set_label("Refreshing…", "")

        def worker():
            try:
                client.request(True)
                data = client.request()
                GLib.idle_add(received, data, None)
            except Exception as exc:
                # Exceptions may carry a URL, so report only the type, never tokens.
                GLib.idle_add(received, None, "unreachable (" + type(exc).__name__ + ")")
        threading.Thread(target=worker, daemon=True).start()

    def received(data, error):
        state["busy"] = False
        state["error"] = error
        indicator.set_label("", "")
        indicator.set_icon_full("dialog-warning-symbolic" if error or trouble(data)
                                else "view-statistics-symbolic", "Token Pace")
        return False

    def poll():
        if not state["busy"]:

            def worker():
                try:
                    GLib.idle_add(received, client.request(), None)
                except Exception:
                    GLib.idle_add(received, None, "unreachable")
            threading.Thread(target=worker, daemon=True).start()
        return True

    menu = Gtk.Menu()
    show_item = Gtk.MenuItem(label="Show Token Pace")
    show_item.connect("activate", lambda *_: toggle())
    menu.append(show_item)
    menu.append(Gtk.SeparatorMenuItem())
    for text, action in [("Refresh now", refresh),
                         ("Open page", lambda: webbrowser.open(client.dashboard())),
                         ("Quit tray", Gtk.main_quit)]:
        item = Gtk.MenuItem(label=text)
        item.connect("activate", lambda _, fn=action: fn())
        menu.append(item)
    menu.show_all()

    indicator = AyatanaAppIndicator3.Indicator.new(
        "tokenpace", "view-statistics-symbolic", AyatanaAppIndicator3.IndicatorCategory.APPLICATION_STATUS)
    indicator.set_status(AyatanaAppIndicator3.IndicatorStatus.ACTIVE)
    indicator.set_menu(menu)

    refresh()
    GLib.timeout_add_seconds(POLL_SECONDS, poll)
    if show:
        toggle()
    Gtk.main()
    return 0
