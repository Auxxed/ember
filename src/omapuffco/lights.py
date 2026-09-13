"""Plain LED colours for heat profiles and the lantern.

On LED API V3 firmware (Peak Pro "peach" on AW and later) the official Puffco
app never writes the old `solid` lamp: a single steady colour is its "No
animation" mood, a pikaled2 lamp carrying just that colour. This builds that
payload the way Puffco Connect does.
"""

from __future__ import annotations

import re
from typing import Any

HEX6 = re.compile(r"#?([0-9a-fA-F]{6})$")

# The LED regions one colour claims in the app's "No animation" mood (ogOffsets row 1).
SINGLE_COLOR_OFFSETS = [0, 0, 0, 0, 0, 0, 65536, 0, 0, 65536, 0, 0, 65536, 65536, 0, 0, 65536, 65536, 65536, 0]


def normalize_color(color: str) -> str:
    match = HEX6.fullmatch(str(color).strip())
    if not match:
        raise ValueError(f"Not a #rrggbb colour: {color!r}")
    return f"#{match.group(1).lower()}"


def solid_color_payload(color: str) -> dict[str, Any]:
    return {
        "lamp": {
            "name": "pikaled2",
            "param": {
                "bright": 255,
                "speed": 64,
                "anim": 1,
                "plNum": 0,
                "plDenom": 1,
                "offset": list(SINGLE_COLOR_OFFSETS),
                "color": [normalize_color(color)],
                "colorLen": 32,
            },
        }
    }
