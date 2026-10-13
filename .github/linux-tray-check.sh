#!/usr/bin/env bash
# CI: run the Linux tray against the demo server, on X11 with a window manager and on Wayland.
set -euo pipefail

URL=http://127.0.0.1:8799
TRAY=(/usr/bin/python3 contrib/linux/tokenpace-tray.py --url "$URL" --show)
pids=()
cleanup() { for p in "${pids[@]}"; do kill "$p" 2>/dev/null || true; done; }
trap cleanup EXIT

/usr/bin/python3 -m tokenpace serve --demo --port 8799 > server.log 2>&1 &
pids+=($!)
for _ in $(seq 30); do curl -fs "$URL/healthz" > /dev/null && break; sleep 1; done
curl -fs "$URL/healthz" > /dev/null

no_errors() {
  if grep -E 'Traceback|CRITICAL' "$1"; then echo "errors in $1"; exit 1; fi
}
popover() { xdotool search --onlyvisible --name '^Token Pace$' 2>/dev/null | head -1 || true; }
wait_for_popover() {
  for _ in $(seq 30); do win=$(popover); [ -n "$win" ] && return 0; sleep 1; done
  echo "$1"; exit 1
}

# X11: a window manager decides focus, as on a desktop.
Xvfb :99 -screen 0 1280x900x24 > /dev/null 2>&1 &
pids+=($!)
export DISPLAY=:99
sleep 2
eval "$(dbus-launch --sh-syntax)"
pids+=("$DBUS_SESSION_BUS_PID")
openbox > openbox.log 2>&1 &
pids+=($!)
sleep 1

"${TRAY[@]}" > tray-x11.log 2>&1 &
tray=$!; pids+=("$tray")
wait_for_popover "popover did not open on X11"
sleep 6   # let WebKit render the page
import -window root tray-x11.png
xdotool getwindowgeometry "$win"
colours=$(convert tray-x11.png -format %k info:)
echo "screenshot colours: $colours"
[ "$colours" -gt 200 ] || { echo "the page did not render"; exit 1; }
[ "$(xdotool getwindowfocus)" = "$win" ] || { echo "popover did not take keyboard focus"; exit 1; }
xdotool key Escape
sleep 1
[ -z "$(popover)" ] || { echo "Escape did not close the popover"; exit 1; }
kill "$tray"
no_errors tray-x11.log

"${TRAY[@]}" > tray-x11-focus.log 2>&1 &
tray=$!; pids+=("$tray")
wait_for_popover "popover did not open again on X11"
xmessage -title other-window "another window" &
pids+=($!)
for _ in $(seq 10); do other=$(xdotool search --onlyvisible --name '^other-window$' | head -1 || true); [ -n "$other" ] && break; sleep 1; done
xdotool windowactivate --sync "$other"
sleep 1
[ -z "$(popover)" ] || { echo "focusing another window did not close the popover"; exit 1; }
kill "$tray"
no_errors tray-x11-focus.log

# Wayland: the popover must map as an ordinary window. Headless Weston has no keyboard or
# pointer, so GTK's "no seat" criticals are reported here and not treated as failures.
export XDG_RUNTIME_DIR; XDG_RUNTIME_DIR=$(mktemp -d); chmod 700 "$XDG_RUNTIME_DIR"
weston --backend=headless --socket=tp-wayland --width=1280 --height=900 > weston.log 2>&1 &
pids+=($!)
for _ in $(seq 20); do [ -S "$XDG_RUNTIME_DIR/tp-wayland" ] && break; sleep 0.5; done
WAYLAND_DISPLAY=tp-wayland GDK_BACKEND=wayland WAYLAND_DEBUG=client "${TRAY[@]}" > tray-wayland.log 2>&1 &
tray=$!; pids+=("$tray")
sleep 10
kill -0 "$tray" || { tail -50 tray-wayland.log; echo "tray exited on Wayland"; exit 1; }
grep -E 'xdg_toplevel@[0-9]+\.set_title\("Token Pace"\)' tray-wayland.log \
  || { tail -50 tray-wayland.log; echo "popover was not mapped as a Wayland window"; exit 1; }
if grep -E 'Traceback' tray-wayland.log; then echo "errors in tray-wayland.log"; exit 1; fi
echo "Wayland criticals (headless, no input devices):"
grep -E 'CRITICAL' tray-wayland.log | sed -E 's/.*CRITICAL \*\*: [0-9:.]+: //' | sort | uniq -c || true
echo "Linux tray checks passed"
