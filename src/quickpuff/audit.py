"""Decode the Peak Pro's on-device audit log into heat sessions.

The firmware keeps a ring of 16-byte entries (u32 timestamp, u8 type code)
readable through /p/logv/aud/*. Timestamps count seconds since boot until the
phone app sets the clock; after that they are Unix time.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

ENTRY_SIZE = 16
SYSTEM_BOOT = 8
PREHEAT_START = 15
CYCLE_COMPLETE = 18
REACHED_TEMP = 20
# Firmware from before the "2" heat-cycle records logs this, in another layout.
REACHED_TEMP_V1 = 9

# Anything below this is seconds since boot rather than a Unix timestamp.
ABSOLUTE_EPOCH = 1_000_000_000


@dataclass(frozen=True)
class Entry:
    index: int
    ts: int
    code: int
    raw: bytes = b""


def parse_entry(index: int, raw: bytes) -> Entry:
    ts, code = struct.unpack_from("<IB", raw)
    return Entry(index=index, ts=ts, code=code, raw=bytes(raw))


def place(entry: Entry, last_boot: int | None, device_clock: int, host_now: float) -> float | None:
    """Host time for a log entry, or None when its boot can't be placed."""
    if entry.ts >= ABSOLUTE_EPOCH:
        return float(entry.ts)
    if (
        device_clock < ABSOLUTE_EPOCH
        and (last_boot is None or entry.index > last_boot)
        and entry.ts <= device_clock
    ):
        return host_now - (device_clock - entry.ts)
    return None


def _v1_fields(raw: bytes) -> dict:
    """Older heat-cycle records: flags at byte 5 (bit 7 set when bits 4-6 hold
    the profile), temperatures as int16 tenths of a degree, and the state's
    planned and elapsed time in centiseconds at offsets 12 and 14."""
    found: dict = {}
    if len(raw) < 16:
        return found
    total, elapsed = struct.unpack_from("<HH", raw, 12)
    if total and elapsed:
        found["preheat_estimate_s"] = total / 100
        found["preheat_s"] = elapsed / 100
    flags = raw[5]
    nominal = struct.unpack_from("<h", raw, 6)[0]
    if flags & 0x80 and nominal > 0:
        slot = (flags >> 4) & 7
        found["profile"] = -1 if slot == 7 else slot
        found["temp_c"] = round(nominal / 10)
    return found


def sessions(entries: list[Entry], device_clock: int, host_now: float) -> list[dict]:
    """Heat cycles that reached temperature, stamped in host time.

    Boot-relative stamps can only be placed for the boot the Peak is still in
    (whose clock reads `device_clock` now); relative entries logged before a
    later reboot, or before the clock was set, are dropped rather than guessed
    onto the wrong day.
    """
    ordered = sorted(entries, key=lambda e: e.index)
    last_boot = max((e.index for e in ordered if e.code == SYSTEM_BOOT), default=None)
    found = []
    for e in ordered:
        if e.code not in (REACHED_TEMP, REACHED_TEMP_V1):
            continue
        ts = place(e, last_boot, device_clock, host_now)
        if ts is None:
            continue
        session: dict = {"index": e.index, "ts": ts}
        if e.code == REACHED_TEMP_V1:
            session.update(_v1_fields(e.raw))
            found.append(session)
            continue
        # Reached-temperature entries carry the firmware's preheat estimate
        # and the real preheat time, in centiseconds at offsets 10 and 12.
        if len(e.raw) >= 14:
            estimate, actual = struct.unpack_from("<HH", e.raw, 10)
            if estimate and actual:
                session["preheat_estimate_s"] = estimate / 100
                session["preheat_s"] = actual / 100
        # They also name the heat profile (low 3 bits of byte 6; 7 means a
        # one-off temperature) and its nominal temperature (byte 7, +150 °C).
        if len(e.raw) >= 8 and e.raw[7]:
            slot = e.raw[6] & 7
            session["profile"] = -1 if slot == 7 else slot
            session["temp_c"] = e.raw[7] + 150
        found.append(session)
    return found
