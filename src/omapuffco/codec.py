"""Encode / decode Puffco CBOR color payloads (hex colors + ULIDs)."""

from __future__ import annotations

import ast
import re

HEX6 = re.compile(r"#?([0-9a-fA-F]{6})$")
C32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def ulid26_to_16(u: str) -> bytes:
    table = {ch: i for i, ch in enumerate(C32)}
    for k in "iIlL":
        table[k] = table["1"]
    for k in "oO":
        table[k] = table["0"]
    value = 0
    for ch in u:
        value = (value << 5) | table[ch]
    value &= (1 << 128) - 1
    return value.to_bytes(16, "big")


def ulid16_to_str(raw: bytes) -> str:
    n = int.from_bytes(raw, "big")
    bits = "00" + bin(n)[2:].zfill(128)
    return "".join(C32[int(bits[i : i + 5], 2)] for i in range(0, 130, 5))


def hexify(obj, key=None):
    if isinstance(obj, dict):
        return {k: hexify(v, k) for k, v in obj.items()}
    if isinstance(obj, list):
        if key == "color":
            return b"".join(bytes.fromhex(HEX6.fullmatch(x).group(1)) for x in obj)
        if key == "userColors":
            return [bytes.fromhex(HEX6.fullmatch(x).group(1)) for x in obj]
        return [hexify(v) for v in obj]
    if isinstance(obj, str):
        if key and key.endswith("Ulid") and len(obj) == 26:
            return ulid26_to_16(obj)
        match = HEX6.fullmatch(obj)
        return bytes.fromhex(match.group(1)) if match else obj
    return obj


def _as_bytes_literal(s: str):
    if not isinstance(s, str):
        return None
    if s.startswith('b""'):
        s = 'b"' + s[3:]
    if s.startswith('b"') and s.endswith('"'):
        return ast.literal_eval(s)
    return None


def _hex(raw: bytes) -> str:
    return f"#{raw[0]:02x}{raw[1]:02x}{raw[2]:02x}"


def _decode(obj, parent=None, key=None):
    if isinstance(obj, dict):
        return {k: _decode(v, obj, k) for k, v in obj.items()}
    if isinstance(obj, list):
        if key == "userColors":
            return [
                _hex(x) if isinstance(x, (bytes, bytearray)) and len(x) >= 3 else _decode(x)
                for x in obj
            ]
        return [_decode(v, parent, None) for v in obj]
    if isinstance(obj, str):
        raw = _as_bytes_literal(obj)
        return _decode(raw, parent, key) if raw is not None else obj
    if isinstance(obj, (bytes, bytearray)):
        raw = bytes(obj)
        if key == "color":
            n = parent.get("colorLen") if isinstance(parent, dict) else None
            n = int(n) if isinstance(n, int) and n > 0 else len(raw) // 3
            return [_hex(raw[i * 3 : i * 3 + 3]) for i in range(n) if len(raw[i * 3 : i * 3 + 3]) == 3]
        if isinstance(key, str) and key.endswith("Ulid") and len(raw) == 16:
            return ulid16_to_str(raw)
        if len(raw) == 3:
            return _hex(raw)
    return obj


def decode_puffco_json(payload: dict) -> dict:
    return _decode(payload)


def first_color(decoded) -> str | None:
    if not isinstance(decoded, dict):
        return None
    lamp = decoded.get("lamp") or {}
    param = lamp.get("param") or {}
    colors = param.get("color") or decoded.get("meta", {}).get("userColors") or []
    if isinstance(colors, list) and colors:
        value = colors[0]
        if isinstance(value, str) and value.startswith("#"):
            return value
    return None
