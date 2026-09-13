"""Dab-count history, kept per Peak.

Each Peak's history lives in its own file keyed by serial number, so a Peak
brings its usage along to any computer (rebuilt from its own audit log) and a
friend's Peak shows its own stats rather than mixing into yours.

Two sources: heat sessions read from the Peak's own audit log (see
audit.py), and cycles OmaPuffco watched locally while connected. The device log
is authoritative for the period it covers; local events fill in before it
and still supply per-session temperature, duration and color.
"""

from __future__ import annotations

import json
import re
import statistics
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from .paths import data_dir, write_json_atomic

RETENTION_DAYS = 730

_device_serial: str | None = None


def use_device(serial: str | None) -> None:
    """Point history at one Peak's file; None falls back to the shared file."""
    global _device_serial
    _device_serial = (serial or "").strip() or None
    if _device_serial:
        _adopt_legacy(_device_serial)


def current_device() -> str | None:
    return _device_serial


def _legacy_path() -> Path:
    return data_dir() / "dabs.json"


def history_path() -> Path:
    if _device_serial:
        # A leading dot would hide the file or read as a relative path part.
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", _device_serial).lstrip(".") or "peak"
        return data_dir() / "devices" / f"{safe}.json"
    return _legacy_path()


def _adopt_legacy(serial: str) -> None:
    """Move history from before it was kept per Peak onto the Peak it came from."""
    target = history_path()
    legacy = _legacy_path()
    if target.exists() or not legacy.exists():
        return
    try:
        data = json.loads(legacy.read_text())
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(data, dict) or data.get("device_log_serial") not in (None, serial):
        return
    write_json_atomic(target, data)
    # Renamed rather than deleted, and so a second Peak can't adopt it too.
    legacy.rename(legacy.with_name("dabs.json.migrated"))


def _load() -> dict[str, Any]:
    path = history_path()
    if path.exists():
        try:
            data = json.loads(path.read_text())
            if isinstance(data, dict):
                data.setdefault("last_total", None)
                data.setdefault("events", [])
                data.setdefault("first_seen", None)
                data.setdefault("device_total_seen", False)
                return data
        except (OSError, json.JSONDecodeError):
            pass
    return {
        "last_total": None,
        "events": [],
        "first_seen": None,
        "device_total_seen": False,
    }


def _save(data: dict[str, Any]) -> None:
    write_json_atomic(history_path(), data)


def record_total(total_dabs: Optional[int]) -> Optional[dict[str, Any]]:
    """Log an increase in the device's lifetime dab counter, if any.

    Call this every time a fresh `total_dabs` reading comes back from the
    device. Returns the logged event, or None if nothing changed.
    """
    if total_dabs is None:
        return None
    try:
        total_dabs = int(total_dabs)
    except (TypeError, ValueError):
        return None
    if total_dabs < 0:
        return None

    data = _load()
    last = data.get("last_total")
    seen = bool(data.get("device_total_seen"))
    if last is not None and int(last) > 0 and not seen:
        # History written before this flag existed still counts as a real
        # device total; a last_total of 0 with no events does not — that
        # was the failed-read poison.
        seen = True
        data["device_total_seen"] = True

    if last is None or not seen:
        # First real observation: set a baseline, don't invent history for
        # dabs taken before OmaPuffco was installed / first connected. A zero
        # does not count as "seen" — that's also the shape of a failed
        # read that used to get stored as last_total.
        data["last_total"] = total_dabs
        data["device_total_seen"] = total_dabs > 0
        if data.get("first_seen") is None:
            data["first_seen"] = time.time()
        _save(data)
        return None

    delta = total_dabs - int(last)
    if delta == 0:
        return None
    if total_dabs == 0 and int(last) > 0:
        # A real Peak with history never reports zero; this is the failed
        # Lorax read we used to store as last_total.
        return None
    if delta < 0:
        # Counter went backwards: factory reset, or a different device.
        data["last_total"] = total_dabs
        _save(data)
        return None

    event = {"ts": time.time(), "delta": delta, "total": total_dabs}
    events = data.get("events", [])
    events.append(event)
    cutoff = time.time() - RETENTION_DAYS * 86400
    data["events"] = [e for e in events if e.get("ts", 0) >= cutoff]
    data["last_total"] = total_dabs
    data["device_total_seen"] = True
    _save(data)
    return event


def has_device_total() -> bool:
    """True once we've successfully read the Peak's lifetime counter."""
    data = _load()
    if data.get("device_total_seen"):
        return True
    last = data.get("last_total")
    return last is not None and int(last) > 0


def record_cycle(
    *,
    temp_f: float | None = None,
    time_s: float | None = None,
    color: str | None = None,
) -> dict[str, Any]:
    """Log one heat cycle OmaPuffco actually watched reach temperature."""
    data = _load()
    now = time.time()
    last = data.get("last_total")
    # Don't promote a failed-read zero into a fake lifetime total.
    usable = last is not None and (int(last) > 0 or data.get("device_total_seen"))
    new_total = int(last) + 1 if usable else None
    event: dict[str, Any] = {"ts": now, "delta": 1, "total": new_total}
    if temp_f is not None:
        event["temp_f"] = float(temp_f)
    if time_s is not None:
        event["time_s"] = float(time_s)
    if color:
        event["color"] = str(color)
    events = data.get("events", [])
    events.append(event)
    cutoff = now - RETENTION_DAYS * 86400
    data["events"] = [e for e in events if e.get("ts", 0) >= cutoff]
    if new_total is not None:
        data["last_total"] = new_total
    if data.get("first_seen") is None:
        data["first_seen"] = now
    _save(data)
    return event


def device_log_state() -> dict[str, Any]:
    data = _load()
    return {"index": data.get("device_log_index"), "serial": data.get("device_log_serial")}


def record_device_sessions(sessions: list[dict], *, last_index: int, serial: str) -> int:
    """Merge sessions read from the Peak's audit log. Returns how many were new."""
    data = _load()
    known = data.get("device_sessions") or []
    if data.get("device_log_serial") not in (None, serial):
        known = []
    by_index = {int(s["index"]): s for s in known}
    added = 0
    for s in sessions:
        index = int(s["index"])
        timing = {k: float(s[k]) for k in PREHEAT_KEYS if s.get(k)}
        timing.update({k: s[k] for k in PROFILE_KEYS if s.get(k) is not None})
        if index in by_index:
            # A re-read fills in timing and profile on sessions stored before they were kept.
            by_index[index].update(timing)
        else:
            by_index[index] = {"index": index, "ts": float(s["ts"]), **timing}
            added += 1
    cutoff = time.time() - RETENTION_DAYS * 86400
    data["device_sessions"] = sorted(
        (s for s in by_index.values() if s["ts"] >= cutoff), key=lambda s: s["index"]
    )
    data["device_log_index"] = int(last_index)
    data["device_log_serial"] = serial
    _save(data)
    return added


PREHEAT_KEYS = ("preheat_s", "preheat_estimate_s")
PROFILE_KEYS = ("profile", "temp_c")
PROFILE_WINDOW_DAYS = 30


def needs_profile_backfill() -> bool:
    """Sessions stored before profiles were kept need one full re-read of the log."""
    data = _load()
    sessions = data.get("device_sessions") or []
    if data.get("profile_backfilled") or not sessions:
        return False
    return not any("profile" in s for s in sessions)


def mark_profile_backfilled() -> None:
    data = _load()
    data["profile_backfilled"] = True
    _save(data)


def record_battery_capacity(mah: float | None) -> dict[str, Any]:
    """Pack capacity the Peak reports now, against the best it has reported.

    Puffco publishes no design capacity, so health is relative to this Peak's
    own best reading since OmaPuffco started watching it.
    """
    data = _load()
    best = data.get("battery_best_mah")
    since = data.get("battery_tracked_since")
    valid = mah is not None and 100 <= float(mah) <= 20000
    if valid:
        changed = False
        if not since:
            since = data["battery_tracked_since"] = time.time()
            changed = True
        if not best or float(mah) > float(best):
            best = data["battery_best_mah"] = float(mah)
            changed = True
        if changed:
            _save(data)
    return {
        "battery_capacity_mah": round(float(mah)) if valid else None,
        "battery_best_mah": round(float(best)) if best else None,
        "battery_health_pct": round(min(100.0, float(mah) / float(best) * 100)) if valid and best else None,
        "battery_tracked_since": since,
    }


def _profile_usage(sessions: list[dict], now: float) -> list[dict[str, Any]]:
    cutoff = now - PROFILE_WINDOW_DAYS * 86400
    by_profile: dict[int, list[float]] = {}
    for s in sessions:
        if s.get("profile") is None or float(s.get("ts", 0)) < cutoff:
            continue
        by_profile.setdefault(int(s["profile"]), []).append(float(s.get("temp_c") or 0))
    total = sum(len(v) for v in by_profile.values())
    usage = []
    for index, temps in sorted(by_profile.items(), key=lambda item: (-len(item[1]), item[0])):
        known = [t for t in temps if t]
        usual = statistics.median(known) if known else None
        usage.append(
            {
                "index": index,
                "count": len(temps),
                "share": round(len(temps) / total, 3),
                "temp_c": None if usual is None else round(usual),
                "temp_f": None if usual is None else round(usual * 9 / 5 + 32),
            }
        )
    return usage


def preheat_scale(samples: int = 20) -> float | None:
    """How much longer real preheats run than the Peak's own estimate.

    The live preheat length the Peak reports is that estimate, which on AW
    firmware is roughly half the real time; the median over recent sessions
    corrects it for this particular Peak.
    """
    ratios = [
        s["preheat_s"] / s["preheat_estimate_s"]
        for s in (_load().get("device_sessions") or [])
        if s.get("preheat_s") and s.get("preheat_estimate_s")
    ]
    if not ratios:
        return None
    return float(statistics.median(ratios[-samples:]))


def _counted_events(data: dict[str, Any]) -> list[dict]:
    events = data.get("events", [])
    device = data.get("device_sessions") or []
    if not device:
        return events
    since = min(float(s["ts"]) for s in device)
    local = [e for e in events if e.get("ts", 0) < since]
    return local + [{"ts": float(s["ts"]), "delta": 1} for s in device]


def _sum_since(events: list[dict], since_ts: float) -> int:
    return sum(int(e.get("delta", 0)) for e in events if e.get("ts", 0) >= since_ts)


def _day_key(value: datetime) -> str:
    return value.strftime("%Y-%m-%d")


def _current_streak(daily: dict[str, int], today: datetime) -> int:
    n = 0
    day = today
    while daily.get(_day_key(day), 0) > 0:
        n += 1
        day -= timedelta(days=1)
        if n > RETENTION_DAYS:
            break
    return n


def _best_streak(daily: dict[str, int]) -> int:
    dates = sorted(
        datetime.strptime(key, "%Y-%m-%d") for key, count in daily.items() if count > 0
    )
    if not dates:
        return 0
    best = run = 1
    for prev, nxt in zip(dates, dates[1:]):
        if (nxt - prev).days == 1:
            run += 1
            if run > best:
                best = run
        else:
            run = 1
    return best


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def get_stats(days: int = 14) -> dict[str, Any]:
    """Locally tracked telemetry: today / this week / this month / this year,
    plus a day-by-day series for the last `days` days."""
    data = _load()
    events = data.get("events", [])
    counted = _counted_events(data)
    now = datetime.now()
    today = datetime(now.year, now.month, now.day)
    week_start = today - timedelta(days=today.weekday())
    month_start = today.replace(day=1)
    year_start = today.replace(month=1, day=1)

    daily: dict[str, int] = {}
    hours = [0] * 24
    temps: list[float] = []
    times: list[float] = []
    color_counts: dict[str, int] = {}
    for e in counted:
        delta = int(e.get("delta", 0))
        stamp = datetime.fromtimestamp(e.get("ts", 0))
        day = stamp.strftime("%Y-%m-%d")
        daily[day] = daily.get(day, 0) + delta
        hours[stamp.hour] += delta
    for e in events:
        delta = int(e.get("delta", 0))
        if e.get("temp_f") is not None:
            try:
                temps.extend([float(e["temp_f"])] * max(1, delta))
            except (TypeError, ValueError):
                pass
        if e.get("time_s") is not None:
            try:
                times.extend([float(e["time_s"])] * max(1, delta))
            except (TypeError, ValueError):
                pass
        color = str(e.get("color") or "")
        if color.startswith("#") and len(color) == 7:
            color_counts[color.lower()] = color_counts.get(color.lower(), 0) + delta

    series = []
    for i in range(days - 1, -1, -1):
        day = today - timedelta(days=i)
        key = _day_key(day)
        series.append({"date": key, "count": daily.get(key, 0), "day": day.day})

    sunday = today - timedelta(days=(today.weekday() + 1) % 7)
    weekdays = []
    for i in range(7):
        day = sunday + timedelta(days=i)
        weekdays.append(
            {
                "date": _day_key(day),
                "count": daily.get(_day_key(day), 0),
                "today": day == today,
            }
        )

    tracked = sum(int(e.get("delta", 0)) for e in counted)
    first = data.get("first_seen")
    if first:
        span = max(1, (today - datetime.fromtimestamp(first).replace(
            hour=0, minute=0, second=0, microsecond=0
        )).days + 1)
    else:
        span = 1
    hour_total = sum(hours)
    top_hour = max(range(24), key=lambda h: hours[h]) if hour_total else None
    colors = [
        hex_color
        for hex_color, _count in sorted(
            color_counts.items(), key=lambda item: item[1], reverse=True
        )[:8]
    ]
    avg_temp = _mean(temps)
    avg_time = _mean(times)

    return {
        "today": _sum_since(counted, today.timestamp()),
        "this_week": _sum_since(counted, week_start.timestamp()),
        "this_month": _sum_since(counted, month_start.timestamp()),
        "this_year": _sum_since(counted, year_start.timestamp()),
        "source": "device" if data.get("device_sessions") else "local",
        "tracked_total": tracked,
        "tracking_since": first,
        "daily": series,
        "avg_per_day": round(tracked / span, 1) if tracked else 0,
        "streak": _current_streak(daily, today),
        "streak_best": _best_streak(daily),
        "weekdays": weekdays,
        "hours": hours,
        "top_hour": top_hour,
        "top_hour_share": round(hours[top_hour] / hour_total, 3) if top_hour is not None and hour_total else 0,
        "avg_temp_f": None if avg_temp is None else round(avg_temp),
        "avg_time_s": None if avg_time is None else round(avg_time),
        "colors": colors,
        "profiles": _profile_usage(data.get("device_sessions") or [], time.time()),
        "profile_days": PROFILE_WINDOW_DAYS,
    }
