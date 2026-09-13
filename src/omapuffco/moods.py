"""Mood lights, built the way the official Puffco app builds them.

Each animated mood below ports a pikaled2 mood definition embedded in the
Puffco app (2.4.3): the formulas that turn a colour list and a tempo into the
lamp parameters the Peak animates. Hand-built payloads with other animation
codes, colour tables and offsets are stored by the Peak but never animated.

The exclusive presets (Puffcon, Hologram, ...) are palettes on top of those
moods; Solid stays on the plain `solid` lamp, which the Peak already renders.
"""

from __future__ import annotations

import math
import re
from typing import Any

HEX6 = re.compile(r"#?([0-9a-fA-F]{6})$")
TABLE_SIZE = 32
BLACK = "#000000"

# Row n: the LED regions n colours claim in "No animation" (the app's ogOffsets).
NO_ANIMATION_OFFSETS = [
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 65536, 0, 0, 65536, 0, 0, 65536, 65536, 0, 0, 65536, 65536, 65536, 0],
    [0, 0, 0, 0, 0, 0, 65536, 0, 0, 65536, 4096, 4096, 65536, 65536, 4096, 4096, 65536, 65536, 65536, 4096],
    [0, 0, 0, 0, 0, 0, 65536, 8192, 8192, 65536, 4096, 4096, 65536, 65536, 4096, 4096, 65536, 65536, 65536, 4096],
    [0, 0, 0, 0, 0, 0, 65536, 8192, 12288, 65536, 4096, 4096, 65536, 65536, 4096, 4096, 65536, 65536, 65536, 4096],
    [0, 0, 16384, 16384, 16384, 0, 65536, 8192, 12288, 65536, 4096, 4096, 65536, 65536, 4096, 4096, 65536, 65536, 65536, 4096],
    [0, 0, 16384, 16384, 16384, 0, 65536, 8192, 12288, 65536, 4096, 4096, 65536, 65536, 20480, 20480, 65536, 65536, 65536, 4096],
]
DISCO_OFFSETS = [15360, 18773, 1707, 5120, 8533, 11947, 15360, 10240, 10240, 5120, 2844, 1138, 853, 19627, 19342, 17636, 0, 0, 0, 0]
SPLIT_OFFSETS_2 = [0, 0, 0, 0, 0, 0, 7680, 25600, 15360, 7680, 12800, 12800, 17920, 17920, 12800, 12800, 15360, 15360, 15360, 15360]
SPLIT_OFFSETS_4 = [0, 0, 0, 0, 0, 0, 7680, 46080, 15360, 7680, 33280, 33280, 38400, 38400, 33280, 33280, 15360, 15360, 15360, 15360]
SPLIT_OFFSETS_6 = [0, 0, 0, 0, 0, 0, 7680, 66560, 15360, 7680, 53760, 53760, 58880, 58880, 53760, 53760, 15360, 15360, 15360, 15360]
SLIDESHOW_OFFSETS = [20480, 20480, 20480, 20480, 20480, 20480, 15930, 9100, 11835, 15930, 0, 0, 6825, 6825, 0, 0, 20480, 20480, 20480, 20480]

# Colour counts each mood's editor accepts in the app.
MOOD_COLOR_LIMITS: dict[str, tuple[int, int]] = {
    "no_animation": (1, 6),
    "disco": (2, 6),
    "fade": (2, 6),
    "spin": (1, 6),
    "split_gradient": (2, 6),
    "vertical_slideshow": (2, 6),
    "breathing": (2, 6),
    "circling_slow": (1, 6),
}

# Panel and CLI style names. None means the plain solid lamp.
STYLE_KINDS: dict[str, str | None] = {
    "solid": None,
    "fill": "vertical_slideshow",
    "fade": "fade",
    "disco": "disco",
    "split": "split_gradient",
    "spin": "spin",
    "breathing": "breathing",
    "rising": "vertical_slideshow",
    "circling": "circling_slow",
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


def _js_round(value: float) -> int:
    # The app's formulas use JavaScript rounding (half up), not Python's half-even.
    return int(math.floor(value + 0.5))


def _num(value: float) -> int | float:
    # JavaScript numbers carry no int/float split; whole values encode as CBOR ints.
    return int(value) if float(value).is_integer() else float(value)


def _normalize(color: str) -> str:
    match = HEX6.fullmatch(str(color).strip())
    if not match:
        raise ValueError(f"Not a #rrggbb colour: {color!r}")
    return f"#{match.group(1).lower()}"


def _rgb(color: str) -> tuple[int, int, int]:
    return tuple(int(color[i : i + 2], 16) for i in (1, 3, 5))  # type: ignore[return-value]


def _cycle(colors: list[str], count: int, steady: float) -> list[str]:
    """Blend a looping colour cycle of `count` steps.

    Approximates the app's `lchycle`: eased (zero-derivative) transitions,
    and with `steady` each colour holds for that fraction of its step.
    """
    if len(colors) == 1:
        return [colors[0]] * count
    rgbs = [_rgb(c) for c in colors]
    hold = max(0.0, min(0.9, steady)) / 2
    out = []
    for i in range(count):
        pos = i / count * len(rgbs)
        base = int(pos) % len(rgbs)
        frac = pos - int(pos)
        if hold:
            if frac < hold:
                frac = 0.0
            elif frac > 1 - hold:
                frac = 1.0
            else:
                frac = (frac - hold) / (1 - 2 * hold)
        ease = 0.5 - math.cos(frac * math.pi) / 2
        a, b = rgbs[base], rgbs[(base + 1) % len(rgbs)]
        out.append("#%02x%02x%02x" % tuple(_js_round(x + (y - x) * ease) for x, y in zip(a, b)))
    return out


def _pad(colors: list[str]) -> list[str]:
    return list(colors) + [BLACK] * (TABLE_SIZE - len(colors))


def mood_payload(kind: str, colors: list[str], *, tempo: float = 0.5) -> dict[str, Any]:
    """The pikaled2 lamp for an official mood, as the Puffco app would write it."""
    if kind not in MOOD_COLOR_LIMITS:
        raise ValueError(f"Unknown mood type: {kind}")
    low, high = MOOD_COLOR_LIMITS[kind]
    user = [_normalize(c) for c in colors][:high] or ["#ffffff"]
    # A single colour on a two-colour mood cycles against black, so the
    # panel's animation buttons still work from one profile colour.
    while len(user) < low:
        user.append(BLACK)
    n = len(user)

    if kind == "no_animation":
        param: dict[str, Any] = {
            "bright": 255,
            "speed": 64,
            "anim": 1,
            "plNum": 0,
            "plDenom": 1,
            "offset": list(NO_ANIMATION_OFFSETS[n]),
            "color": _pad(user),
            "colorLen": TABLE_SIZE,
        }
        return {"lamp": {"name": "pikaled2", "param": param}}

    tempo = max(0.0, min(1.0, float(tempo)))
    tempo_cpm = tempo * tempo * 480
    if kind in ("spin", "circling_slow"):
        speed = min(_js_round(tempo_cpm * 256 / 480), 255)
    elif kind in ("fade", "breathing"):
        speed = _js_round(tempo_cpm / 3)
    else:
        speed = _js_round(tempo_cpm / 3) if tempo_cpm > 0 else 64
    speed_di1 = min(speed * 2, 255)
    steady = 0.3 if kind in ("fade", "breathing", "spin", "circling_slow") else 0.0
    table_len = n * 5
    phase_lock = 0 if tempo_cpm > 0 else 1

    param = {
        "bright": 255,
        "speed": speed,
        "speedDi0": _num(speed_di1 / 8),
        "speedDi1": speed_di1,
        "anim": 1,
        "plNum": 0,
        "plDenom": 0,
        "offset": [0] * 20,
        "color": _pad(_cycle(user, table_len, steady)),
        "colorLen": table_len,
        "diFrac": 0,
    }
    if kind == "disco":
        param.update(offset=[_js_round(v * n) for v in DISCO_OFFSETS], plDenom=phase_lock)
    elif kind == "split_gradient":
        offsets = SPLIT_OFFSETS_2 if n == 2 else SPLIT_OFFSETS_4 if n <= 4 else SPLIT_OFFSETS_6
        param.update(offset=list(offsets), plDenom=phase_lock)
    elif kind == "vertical_slideshow":
        param.update(offset=list(SLIDESHOW_OFFSETS), plDenom=phase_lock)
    elif kind == "breathing":
        param["anim"] = 5
    elif kind == "spin":
        param.update(anim=7, plNum=1, plDenom=n)
    elif kind == "circling_slow":
        param.update(anim=21, plNum=1, plDenom=n)
    return {"lamp": {"name": "pikaled2", "param": param}}


def resolve_style(name: str) -> dict[str, Any]:
    key = _key(name)
    if key not in STYLE_KINDS:
        raise ValueError(f"Unknown animation: {name}")
    return {"name": key, "kind": STYLE_KINDS[key]}


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
        "style": style["name"],
        "kind": style["kind"],
    }
