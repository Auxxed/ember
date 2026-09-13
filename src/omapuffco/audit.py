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

# Anything below this is seconds since boot rather than a Unix timestamp.
ABSOLUTE_EPOCH = 1_000_000_000


@dataclass(frozen=True)
class Entry:
    index: int
    ts: int
    code: int


def parse_entry(index: int, raw: bytes) -> Entry:
    ts, code = struct.unpack_from("<IB", raw)
    return Entry(index=index, ts=ts, code=code)


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
        if e.code != REACHED_TEMP:
            continue
        if e.ts >= ABSOLUTE_EPOCH:
            ts = float(e.ts)
        elif (
            device_clock < ABSOLUTE_EPOCH
            and (last_boot is None or e.index > last_boot)
            and e.ts <= device_clock
        ):
            ts = host_now - (device_clock - e.ts)
        else:
            continue
        found.append({"index": e.index, "ts": ts})
    return found
