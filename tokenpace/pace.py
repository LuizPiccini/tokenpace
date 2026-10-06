"""Usage windows, pace and the "use first" ranking.

A window is one quota with a reset: "5 hours", "Week", "Month", "Day". For each
subscription the ranking looks at its longest window and asks how fast you would
have to use what is left to finish it before the reset:

    needed pace = % of quota left / % of the period left

1x is the steady pace. Above 1.15x quota is being left on the table ("Use first" for
the highest in a group, "Use too" for the others); between 0.85x and 1.15x it is on
pace; below 0.85x it runs out before the reset ("Save"). A short window that is
used up (for example Claude's 5 hours) moves the subscription to the end until it
frees up. Free tiers stay at the end as a reserve.
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Any

WINDOW_KINDS: dict[str, tuple[str, int]] = {
    "five_hour": ("5 hours", 5 * 3600),
    "daily": ("Day", 86400),
    "weekly": ("Week", 7 * 86400),
    "weekly_opus": ("Week · Opus", 7 * 86400),
    "weekly_sonnet": ("Week · Sonnet", 7 * 86400),
    "monthly": ("Month", 30 * 86400),
}

LEVELS = {
    "use": "Use first",
    "lean_use": "Use too",
    "on_pace": "On pace",
    "save": "Save",
    "free": "Free reserve",
    "blocked": "Used up now",
}
SLACK_NEED = 1.15
SAVE_NEED = 0.85
METHOD = (
    "For each paid subscription tokenpace compares the quota left with the time left until the reset "
    "of its longest window. Needed pace = % of quota left ÷ % of the period left: 1× is the steady pace, "
    "3× means you would have to use it three times faster to lose nothing at the reset. Rows go from the "
    "highest needed pace to the lowest; the first with slack (above 1.15×) gets “Use first”, the others "
    "with slack “Use too”. Between 0.85× and 1.15× it is on pace; below 0.85× the quota runs out before "
    "the reset (“Save”). A used-up short window moves the subscription to the end until it frees up. "
    "Free tiers stay at the end as a reserve."
)
# Per-model windows (Claude's Opus/Sonnet weeks) limit one model, not the subscription.
MODEL_KINDS = {"weekly_opus", "weekly_sonnet"}
EXHAUSTED_LEFT = 0.5   # under this % left a window counts as used up (99% used is not)

PERIOD_NAMES = {
    "five_hour": "of the 5 hours", "daily": "of the day", "weekly": "of the week",
    "weekly_opus": "of the week", "weekly_sonnet": "of the week", "monthly": "of the month",
}


def iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


MIN_TS, MAX_TS = 946684800.0, 7258118400.0   # 2000-01-01 .. 2200-01-01: anything else is garbage


def parse_time(value: Any) -> float | None:
    """Epoch seconds from epoch seconds, epoch milliseconds or an ISO 8601 string; None if out of range."""
    if value is None or value == "" or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        v = num(value)
        if v is None:
            return None
        v = v / 1000.0 if v > 1e12 else v
        return v if MIN_TS <= v <= MAX_TS else None
    if isinstance(value, str):
        text = value.strip()
        try:
            return parse_time(float(text))
        except ValueError:
            pass
        try:
            parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        try:
            return parse_time(parsed.timestamp())
        except (OverflowError, OSError, ValueError):
            return None
    return None


def num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return v


def kind_for_seconds(seconds: float | None) -> str | None:
    if not seconds:
        return None
    s = int(seconds)
    if abs(s - 5 * 3600) < 600:
        return "five_hour"
    if abs(s - 86400) < 600:
        return "daily"
    if abs(s - 7 * 86400) < 3 * 3600:
        return "weekly"
    if 27 * 86400 <= s <= 32 * 86400:
        return "monthly"
    return None


def make_window(kind: str | None, used_percent: float, resets_at: Any, seconds: float | None = None,
                label: str | None = None) -> dict[str, Any]:
    used = max(0.0, min(100.0, num(used_percent) or 0.0))
    default_label, default_seconds = WINDOW_KINDS.get(kind or "", (None, None))
    seconds = num(seconds)
    if not seconds or not 60 <= seconds <= 400 * 86400:
        seconds = default_seconds
    if not label:
        label = default_label or (f"{round(seconds / 3600)} h" if seconds else "Window")
    return {
        "kind": kind,
        "label": label,
        "used_percent": round(used, 1),
        "remaining_percent": round(100.0 - used, 1),
        "resets_at": iso(parse_time(resets_at)),
        "window_seconds": int(seconds) if seconds else None,
    }


def sort_windows(windows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(windows, key=lambda w: (w.get("window_seconds") or 0, w.get("label") or ""))


def relabel(window: dict[str, Any]) -> dict[str, Any]:
    """Labels follow the kind, so stored readings pick up renamed labels."""
    kind = window.get("kind")
    if kind in WINDOW_KINDS:
        return {**window, "label": WINDOW_KINDS[kind][0]}
    return window


def short_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h{minutes:02d}"
    return f"{minutes} min" if minutes else "under 1 min"


NEED_CAP = 99.0


def _x(value: float) -> str:
    return "over 99×" if value >= NEED_CAP else f"{value:.1f}×"


def window_pace(win: dict[str, Any], now: float) -> dict[str, Any] | None:
    reset = parse_time(win.get("resets_at"))
    seconds = num(win.get("window_seconds"))
    used = num(win.get("used_percent"))
    if reset is None or not seconds or seconds <= 0 or used is None:
        return None
    used = max(0.0, min(100.0, used))
    renewed = reset <= now
    if renewed:
        # The window renewed after the reading: a fresh window, usage unknown but near zero.
        periods = math.floor((now - reset) / seconds) + 1   # next reset strictly in the future
        reset = reset + periods * seconds
        used = 0.0
    elapsed = max(0.0, min(1.0, 1.0 - (reset - now) / seconds)) * 100.0
    projected_left = None
    if elapsed >= 10.0:
        projected_left = max(0.0, 100.0 - used / (elapsed / 100.0))
    remaining = 100.0 - used
    need = min(NEED_CAP, remaining / max(1e-9, 100.0 - elapsed))   # capped for display
    return {
        "label": win.get("label"),
        "kind": win.get("kind"),
        "window_seconds": int(seconds),
        "used_percent": round(used, 1),
        "remaining_percent": round(remaining, 1),
        "elapsed_percent": round(elapsed, 1),
        "gap_pp": round(elapsed - used, 1),
        "need": round(need, 2),
        "projected_left_percent": None if projected_left is None else round(projected_left),
        "resets_at": iso(reset),
        "renewed_since_reading": renewed,
    }


def build_advice(subscriptions: list[dict[str, Any]], group_ids: list[str], now: float) -> dict[str, Any]:
    """Rank the subscriptions of each group. Each subscription needs id, name, group,
    windows, status and optionally free, use_via and a caveat."""
    groups: dict[str, list[dict[str, Any]]] = {g: [] for g in group_ids}
    left_out = []
    for sub in subscriptions:
        paces = [p for p in (window_pace(w, now) for w in sub.get("windows") or []) if p]
        if not paces or sub.get("status") == "unavailable":
            reason = sub.get("message") or "No reading."
            if sub.get("windows") and not paces:
                reason = "Reading without a reset time; it cannot be compared with the period."
            left_out.append({"id": sub["id"], "name": sub["name"], "group": sub["group"], "reason": reason})
            continue
        general = [p for p in paces if p["kind"] not in MODEL_KINDS] or paces
        period = max(general, key=lambda p: p["window_seconds"])
        exhausted = [p for p in paces if p["remaining_percent"] < EXHAUSTED_LEFT and not p["renewed_since_reading"]]
        blocking = [p for p in exhausted if p["kind"] not in MODEL_KINDS]   # a model limit never blocks the plan
        model_out = [p for p in exhausted if p not in blocking]
        free = bool(sub.get("free"))
        need = period["need"]
        if blocking:
            state = "blocked"
        elif free:
            state = "save" if period["remaining_percent"] < 20 else "free"
        elif need >= SLACK_NEED:
            state = "slack"
        elif need >= SAVE_NEED:
            state = "on_pace"
        else:
            state = "save"
        period_name = PERIOD_NAMES.get(period["kind"] or "", "of the period")
        if free:
            reason = (f"Used {period['used_percent']:.0f}% of the free allowance; keep it for light tasks")
        else:
            reason = (f"Used {period['used_percent']:.0f}% of the quota; {period['elapsed_percent']:.0f}% "
                      f"{period_name} has passed. Using the rest before the reset takes {_x(need)} "
                      "the steady pace (the pace that spends 100% over the whole period)")
            if period["projected_left_percent"] is not None and state != "blocked":
                reason += f"; at the current pace ~{period['projected_left_percent']}% is left at the reset"
        if blocking:
            # Usable again only when every exhausted window has reset.
            last = max(blocking, key=lambda p: p["resets_at"] or "")
            reason = f"{last['label'] or 'A'} window used up. " + reason
        for p in model_out:
            reason += f". {p['label']} used up until its reset"
        row = {
            "id": sub["id"],
            "name": sub["name"],
            "group": sub["group"],
            "plan": sub.get("plan"),
            "state": state,
            "need": need,
            "gap_pp": period["gap_pp"],
            "period": period,
            "short_windows": [p for p in paces if p is not period],
            "released_at": max((p["resets_at"] for p in blocking), default=None),
            "reason": reason,
            "caveat": sub.get("caveat") or ("Reset since the last reading; usage assumed near zero until the next one"
                                            if period["renewed_since_reading"] else None),
            "use_via": sub.get("use_via"),
            "free": free,
        }
        groups.setdefault(sub["group"], []).append(row)
    for rows in groups.values():
        rows.sort(key=lambda r: (2 if r["state"] == "blocked" else 1 if r["free"] else 0,
                                 -r["need"], r["period"]["resets_at"] or ""))
        first_taken = False
        for i, row in enumerate(rows, 1):
            if row["state"] == "slack":
                row["level"] = "lean_use" if first_taken else "use"
                first_taken = True
            else:
                row["level"] = row["state"]
            row["verdict"] = LEVELS[row["level"]]
            row["rank"] = i
    return {"method": METHOD, "groups": groups, "left_out": left_out}
