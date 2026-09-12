"""Local dab-count history.

The Peak Pro itself only reports a lifetime total (`total_dabs`), an
estimated dabs-remaining-in-chamber count, and a rolling dabs-per-day
average — it has no notion of "this month" or "this week". Ember watches
the lifetime total as it polls the device and logs every increase with a
timestamp, so it can reconstruct the daily/weekly/monthly telemetry the
official app shows.

Tracking only starts once Ember first sees the device, so totals here are
"since Ember started watching", not the device's full lifetime history.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from .paths import data_dir

RETENTION_DAYS = 730


def history_path() -> Path:
    return data_dir() / "dabs.json"


def _load() -> dict[str, Any]:
    path = history_path()
    if path.exists():
        try:
            data = json.loads(path.read_text())
            if isinstance(data, dict):
                data.setdefault("last_total", None)
                data.setdefault("events", [])
                data.setdefault("first_seen", None)
                return data
        except (OSError, json.JSONDecodeError):
            pass
    return {"last_total": None, "events": [], "first_seen": None}


def _save(data: dict[str, Any]) -> None:
    path = history_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data) + "\n")


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
    if last is None:
        # First observation: set a baseline, don't invent history for
        # dabs taken before Ember was installed / first connected.
        data["last_total"] = total_dabs
        data["first_seen"] = time.time()
        _save(data)
        return None

    delta = total_dabs - int(last)
    if delta == 0:
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
    _save(data)
    return event


def _sum_since(events: list[dict], since_ts: float) -> int:
    return sum(int(e.get("delta", 0)) for e in events if e.get("ts", 0) >= since_ts)


def get_stats(days: int = 14) -> dict[str, Any]:
    """Locally tracked telemetry: today / this week / this month / this year,
    plus a day-by-day series for the last `days` days."""
    data = _load()
    events = data.get("events", [])
    now = datetime.now()
    today = datetime(now.year, now.month, now.day)
    week_start = today - timedelta(days=today.weekday())
    month_start = today.replace(day=1)
    year_start = today.replace(month=1, day=1)

    daily: dict[str, int] = {}
    for e in events:
        day = datetime.fromtimestamp(e.get("ts", 0)).strftime("%Y-%m-%d")
        daily[day] = daily.get(day, 0) + int(e.get("delta", 0))

    series = []
    for i in range(days - 1, -1, -1):
        day = today - timedelta(days=i)
        key = day.strftime("%Y-%m-%d")
        series.append({"date": key, "count": daily.get(key, 0)})

    return {
        "today": _sum_since(events, today.timestamp()),
        "this_week": _sum_since(events, week_start.timestamp()),
        "this_month": _sum_since(events, month_start.timestamp()),
        "this_year": _sum_since(events, year_start.timestamp()),
        "tracked_total": sum(int(e.get("delta", 0)) for e in events),
        "tracking_since": data.get("first_seen"),
        "daily": series,
    }
