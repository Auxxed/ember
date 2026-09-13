"""Per-profile Vapor Control (Connect: Smooth / Bold / Intense / Extreme).

Stored on the device as a float at `/u/app/hc/{n}/intn`. The four values
the firmware accepts are 0.0, 0.5, 1.0, 1.5 — Extreme (1.5) is what the
app used to call XL and needs a 3DXL chamber in the official UI, but this
Peak already holds 1.5 so we expose all four.
"""

from __future__ import annotations

LEVELS: tuple[tuple[str, float], ...] = (
    ("smooth", 0.0),
    ("bold", 0.5),
    ("intense", 1.0),
    ("extreme", 1.5),
)

_BY_NAME = {name: value for name, value in LEVELS}
_NAMES = [name for name, _value in LEVELS]


def names() -> list[str]:
    return list(_NAMES)


def value_for(name: str) -> float:
    key = str(name).strip().lower()
    aliases = {
        "standard": "smooth",
        "high": "bold",
        "max": "intense",
        "xl": "extreme",
    }
    key = aliases.get(key, key)
    if key not in _BY_NAME:
        raise ValueError(f"Unknown vapor level {name!r}. Try: {', '.join(_NAMES)}")
    return _BY_NAME[key]


def name_for(value: float) -> str:
    return min(LEVELS, key=lambda pair: abs(pair[1] - float(value)))[0]


def snap(value: float) -> float:
    return min(LEVELS, key=lambda pair: abs(pair[1] - float(value)))[1]
