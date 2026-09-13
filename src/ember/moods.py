"""Connect-app mood lights: exclusive presets and animation styles.

Fill / Fade / Disco / Split / Spin are the five styles the official mood
editor exposes. The exclusive presets (Puffcon, Hologram, …) are the
stock palettes Puffco ships in the app.
"""

from __future__ import annotations

import re
from typing import Any

from .constants import AnimationCode

Q16 = 65536


def even_offsets(n: int = 20, step: int = Q16) -> list[int]:
    return [i * step for i in range(n)]


def split_offsets(n: int = 20) -> list[int]:
    mid = n // 2
    return [0] * mid + [Q16] * (n - mid)


def disco_offsets(n: int = 20) -> list[int]:
    return [(i * 3 * Q16) % (8 * Q16) for i in range(n)]


ZEROS = [0] * 20

# Connect mood-editor styles → Lorax pikaled2 animation + LED offsets.
STYLES: dict[str, dict[str, Any]] = {
    "solid": {"anim": None, "offsets": ZEROS, "speed": 20},
    "fill": {"anim": AnimationCode.RISING, "offsets": ZEROS, "speed": 24},
    "fade": {"anim": AnimationCode.BREATHING, "offsets": ZEROS, "speed": 16},
    "disco": {"anim": AnimationCode.HEAT_CYCLE_ACTIVE, "offsets": disco_offsets(), "speed": 32},
    "split": {"anim": AnimationCode.CIRCLING, "offsets": split_offsets(), "speed": 22},
    "spin": {"anim": AnimationCode.CIRCLING, "offsets": even_offsets(), "speed": 28},
}

# Aliases so `ember anim breathing` and `ember anim fade` both work.
STYLE_ALIASES = {
    "breathing": "fade",
    "rising": "fill",
    "circling": "spin",
    "heat": "disco",
}

EXCLUSIVE: dict[str, dict[str, Any]] = {
    "puffcon": {
        "label": "Puffcon",
        "style": "spin",
        "colors": ["#ff4fa3", "#3b9eff"],
    },
    "july4": {
        "label": "4th of July",
        "style": "fill",
        "colors": ["#ff4d4d", "#ffffff", "#3b9eff"],
    },
    "candle": {
        "label": "Candle Flicker",
        "style": "fade",
        "colors": ["#ffb07a", "#e8955a"],
    },
    "hologram": {
        "label": "Hologram",
        "style": "spin",
        "colors": ["#7c3aed", "#3b9eff", "#22d3ee"],
    },
    "lupus": {
        "label": "Lupus Awareness",
        "style": "fade",
        "colors": ["#6d28d9", "#c4b5fd"],
    },
    "disco": {
        "label": "Disco",
        "style": "disco",
        "colors": ["#ff4d4d", "#f6d32d", "#3dd68c", "#3b9eff", "#a855f7", "#ff4fa3"],
    },
}

_MOOD_ALIASES = {
    "4th": "july4",
    "4thofjuly": "july4",
    "july": "july4",
    "candleflicker": "candle",
    "lupusawareness": "lupus",
}


def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(name).lower())


def resolve_style(name: str) -> dict[str, Any]:
    key = _key(name)
    key = STYLE_ALIASES.get(key, key)
    style = STYLES.get(key)
    if not style:
        raise ValueError(f"Unknown animation: {name}")
    return {"name": key, **style}


def pikaled2_payload(
    anim: int,
    colors: list[str],
    *,
    speed: int = 20,
    bright: int = 255,
    offsets: list[int] | None = None,
) -> dict[str, Any]:
    """Compact pikaled2 lamp. Do not pad to 32 colours — that ballooned the
    CBOR past one Lorax write, so the Peak previewed leftover/factory green
    until the later chunks landed (if they ever did)."""
    if not colors:
        colors = ["#ffffff"]
    if offsets is None:
        offsets = [0] * 20
    return {
        "lamp": {
            "name": "pikaled2",
            "param": {
                "anim": int(anim),
                "color": list(colors),
                "plNum": 0,
                "speed": int(speed),
                "bright": int(bright),
                "diFrac": 0,
                "offset": list(offsets)[:20] + [0] * max(0, 20 - len(offsets)),
                "plDenom": 0,
                "colorLen": len(colors),
                "speedDi0": 4,
                "speedDi1": 40,
            },
        }
    }


def resolve_mood(name: str) -> dict[str, Any]:
    key = _MOOD_ALIASES.get(_key(name), _key(name))
    mood = EXCLUSIVE.get(key)
    if not mood:
        known = ", ".join(EXCLUSIVE)
        raise ValueError(f"Unknown mood {name!r}. Try: {known}")
    style = resolve_style(mood["style"])
    return {
        "id": key,
        "label": mood["label"],
        "colors": list(mood["colors"]),
        "anim": style["anim"],
        "offsets": list(style["offsets"]),
        "speed": style["speed"],
        "style": style["name"],
    }
