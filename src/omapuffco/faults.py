"""The Peak Pro's fault log: heater, battery and pairing problems it recorded.

Entries use the audit log's layout (u32 timestamp, u8 code) and the fault codes
from the official Puffco app. Restart and clock-change markers are not faults,
and codes the app doesn't define (on AW firmware, runs of diagnostic records)
are left out.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .audit import Entry, parse_entry, place
from .paths import data_dir, write_json_atomic

SYSTEM_BOOT = 12
CLOCK_ADJUST = 13

FAULTS: dict[int, tuple[str, str]] = {
    0: ("CHARGE_LOW_CAPACITY", "Battery charged below its expected capacity"),
    1: ("CHARGE_TEMP_STOP", "Charging paused: battery temperature"),
    2: ("HEAT_CYCLE_START_LOW_BATTERY", "Heat cycle refused: battery too low"),
    3: ("HEATER_CURRENT_SENSE", "Heater current sensor fault"),
    4: ("HEATER_SHORT_CIRCUIT", "Heater short circuit"),
    5: ("HEATER_OPEN_CIRCUIT", "Heater open circuit: check the chamber"),
    6: ("HEATER_TEMP_ERRATIC", "Erratic heater temperature"),
    7: ("HEATER_TEMP_LOST", "Heater temperature reading lost"),
    8: ("HEATER_SAFETY_THERMAL_VIOLATION", "Heater safety cutoff"),
    9: ("LOW_BATTERY_VOLTAGE", "Battery voltage too low"),
    10: ("HIGH_BATTERY_TEMP", "Battery too hot"),
    11: ("BOND_FAILED", "Bluetooth pairing failed"),
}


def decode(entries: list[Entry], device_clock: int, host_now: float) -> list[dict[str, Any]]:
    """Faults newest first. `ts` is None when the entry predates a restart
    the Peak's clock can't account for."""
    ordered = sorted(entries, key=lambda e: e.index)
    last_boot = max((e.index for e in ordered if e.code == SYSTEM_BOOT), default=None)
    found = []
    for e in ordered:
        if e.code not in FAULTS:
            continue
        name, label = FAULTS[e.code]
        found.append(
            {
                "index": e.index,
                "code": e.code,
                "name": name,
                "label": label,
                "ts": place(e, last_boot, device_clock, host_now),
            }
        )
    found.reverse()
    return found


def empty_cache(serial: str) -> dict[str, Any]:
    return {"serial": serial, "end": 0, "entries": {}, "placed": {}}


def cache_path(serial: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", serial).lstrip(".") or "peak"
    return data_dir() / "devices" / f"{safe}.faults.json"


def load_cache(serial: str) -> dict[str, Any]:
    """The fault log entries already read from this Peak, so a daemon restart
    doesn't walk the whole ring again."""
    try:
        raw = json.loads(cache_path(serial).read_text())
        entries = {int(i): parse_entry(int(i), bytes.fromhex(h)) for i, h in raw.get("entries", {}).items()}
        placed = {int(i): float(t) for i, t in raw.get("placed", {}).items()}
        return {"serial": serial, "end": int(raw.get("end", 0)), "entries": entries, "placed": placed}
    except (OSError, ValueError, TypeError, AttributeError):
        return empty_cache(serial)


def save_cache(cache: dict[str, Any]) -> None:
    path = cache_path(cache["serial"])
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(
        path,
        {
            "end": int(cache["end"]),
            "entries": {str(i): e.raw.hex() for i, e in cache["entries"].items()},
            "placed": {str(i): t for i, t in cache["placed"].items()},
        },
    )


def remember_times(found: list[dict[str, Any]], placed: dict[int, float]) -> None:
    """Keep a fault's date once it has been worked out: after a later restart
    the Peak's clock can no longer place it."""
    for fault in found:
        index = int(fault["index"])
        if fault["ts"] is None and index in placed:
            fault["ts"] = placed[index]
        elif fault["ts"] is not None:
            placed[index] = float(fault["ts"])
