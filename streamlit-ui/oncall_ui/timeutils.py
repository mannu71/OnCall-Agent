"""Date parsing, relative labels and local-time ↔ UTC-cron conversion.

The React UI used the *browser's* local zone for schedules; here every
function takes an explicit ``tz`` (the viewer's zone, see
:func:`oncall_ui.ui.viewer_tz`) so the behaviour is the same.
"""
from __future__ import annotations

from datetime import date, datetime, time as dtime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def zone(tz: Optional[str]) -> ZoneInfo:
    try:
        return ZoneInfo(tz or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def parse_date(value) -> Optional[datetime]:
    """Parse API timestamps (tolerates ``…+00:00Z`` and naive-as-UTC)."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    s = str(value).strip().replace("+00:00Z", "Z").replace(" ", "T", 1)
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def fmt_datetime(value, tz: Optional[str]) -> str:
    dt = parse_date(value)
    return dt.astimezone(zone(tz)).strftime("%Y-%m-%d %H:%M:%S") if dt else "N/A"


def fmt_clock(value, tz: Optional[str]) -> str:
    dt = parse_date(value)
    return dt.astimezone(zone(tz)).strftime("%H:%M") if dt else "—"


def relative_time(value, now: Optional[datetime] = None) -> Optional[str]:
    dt = parse_date(value)
    if not dt:
        return None
    now = now or datetime.now(timezone.utc)
    mins = int((now - dt).total_seconds() // 60)
    if mins < 1:
        return "just now"
    if mins < 60:
        return f"{mins}m ago"
    if mins < 60 * 24:
        return f"{mins // 60}h ago"
    return f"{mins // (60 * 24)}d ago"


def run_started_compact(value, tz: Optional[str], now: Optional[datetime] = None) -> str:
    """``Today · 14:05`` / ``Yesterday · 09:12`` / ``Mon, Oct 6 · 18:00``."""
    dt = parse_date(value)
    if not dt:
        return "—"
    z = zone(tz)
    local = dt.astimezone(z)
    today = (now or datetime.now(timezone.utc)).astimezone(z).date()
    clock = local.strftime("%H:%M")
    if local.date() == today:
        return f"Today · {clock}"
    if local.date() == today - timedelta(days=1):
        return f"Yesterday · {clock}"
    return f"{local.strftime('%a, %b')} {local.day} · {clock}"


def parse_hm(value: Optional[str]) -> Optional[dtime]:
    try:
        h, m = str(value or "").strip().split(":")[:2]
        return dtime(int(h), int(m))
    except (ValueError, TypeError):
        return None


def generate_cron(utc_minute: int, utc_hour: int, recurrence: str) -> str:
    if recurrence == "weekly":
        return f"{utc_minute} {utc_hour} * * 1"  # Monday
    if recurrence == "monthly":
        return f"{utc_minute} {utc_hour} 1 * *"  # 1st of month
    return f"{utc_minute} {utc_hour} * * *"


def local_time_to_cron(hm: str, recurrence: str, tz: Optional[str], on: Optional[date] = None) -> str:
    """Local ``HH:MM`` → UTC cron (offset taken on ``on``, default today)."""
    t = parse_hm(hm) or dtime(9, 0)
    local = datetime.combine(on or datetime.now(zone(tz)).date(), t, tzinfo=zone(tz))
    utc = local.astimezone(timezone.utc)
    return generate_cron(utc.minute, utc.hour, recurrence)


def cron_to_local_time(cron: Optional[str], tz: Optional[str]) -> str:
    """UTC cron's minute/hour → local ``HH:MM`` (fixed mid-January date, as before)."""
    if not cron:
        return "09:00"
    parts = cron.split()
    try:
        minute, hour = int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        return "09:00"
    utc = datetime(2024, 1, 15, hour, minute, tzinfo=timezone.utc)
    return utc.astimezone(zone(tz)).strftime("%H:%M")


def next_occurrence(hm: Optional[str], tz: Optional[str], now: Optional[datetime] = None) -> Optional[datetime]:
    """Next local wall-clock occurrence of ``HH:MM`` (rolls to tomorrow if passed)."""
    t = parse_hm(hm)
    if not t:
        return None
    z = zone(tz)
    now_l = (now or datetime.now(timezone.utc)).astimezone(z)
    cand = now_l.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
    if cand <= now_l:
        cand += timedelta(days=1)
    return cand


def scheduled_relative(target: Optional[datetime], tz: Optional[str], now: Optional[datetime] = None) -> str:
    """``tomorrow · in 5h 42m``."""
    if not target:
        return ""
    z = zone(tz)
    now_l = (now or datetime.now(timezone.utc)).astimezone(z)
    tl = target.astimezone(z)
    if tl.date() == now_l.date():
        day = "today"
    elif tl.date() == now_l.date() + timedelta(days=1):
        day = "tomorrow"
    else:
        day = tl.strftime("%A")
    secs = max(0, int((tl - now_l).total_seconds()))
    h, m = secs // 3600, (secs % 3600) // 60
    return f"{day} · " + (f"in {h}h {m:02d}m" if h > 0 else f"in {m}m")
