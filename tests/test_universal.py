"""Peak Pros other than the one OmaPuffco was built on: older firmware and models."""

import asyncio
import struct

from omapuffco import audit
from omapuffco.ble import LoraxError, PuffcoBLE
from omapuffco.lights import rgbt_color, rgbt_to_hex
from omapuffco.product_info import get_product_info
from omapuffco.utils import PuffcoUtils

NOW = 1_800_000_000.0


def test_revision_strings_round_trip():
    for n in (1, 5, 21, 22, 27, 39, 440):
        assert PuffcoUtils.revision_string_to_number(PuffcoUtils.revision_number_to_string(n)) == n
    # The alphabet skips I, L, O, Q and S.
    assert PuffcoUtils.revision_string_to_number("AF") == 27
    assert PuffcoUtils.revision_number_to_string(39) == "AW"


def test_rgbt_color_is_eight_bytes_of_plain_rgb():
    assert rgbt_color("#3dd68c") == bytes([0x3D, 0xD6, 0x8C, 0, 0, 0, 0, 0])
    assert rgbt_to_hex(rgbt_color("#3dd68c")) == "#3dd68c"
    assert rgbt_to_hex(bytes([10, 20, 30, 1, 0, 0, 0, 0])) is None  # a colour table, not RGB


def test_older_heat_cycle_records_count_as_sessions():
    raw = bytearray(16)
    struct.pack_into("<IB", raw, 0, 1_790_000_000, audit.REACHED_TEMP_V1)
    raw[5] = 0x80 | (2 << 4)  # profile slot 2
    struct.pack_into("<hhhHH", raw, 6, 2544, 2544, 2530, 2400, 4100)
    found = audit.sessions([audit.parse_entry(7, bytes(raw))], device_clock=0, host_now=NOW)
    assert found == [
        {
            "index": 7,
            "ts": 1_790_000_000.0,
            "preheat_estimate_s": 24.0,
            "preheat_s": 41.0,
            "profile": 2,
            "temp_c": 254,
        }
    ]


def test_newest_colorways_are_known():
    assert get_product_info(product_code=83).marketing_name == "Plasma"
    assert get_product_info(product_code=84).marketing_name == "Glacier"


def fake_peak(api_version=None, ota_version=41, has_preheat_color=False):
    ble = PuffcoBLE()

    async def read(path, offset=0, size=None, data_type="bytes", count=1):
        if path == "/p/sys/fw/api":
            if api_version is None:
                raise LoraxError("missing", 2)
            return api_version
        raise AssertionError(path)

    async def read_short(path, offset, size):
        if path == "/p/sys/fw/ver":
            return bytes([ota_version])
        if path == "/u/app/hc/0/phcl":
            if has_preheat_color:
                return bytes(8)
            raise LoraxError("missing", 2)
        raise AssertionError(path)

    ble.read = read
    ble.read_short = read_short
    return ble


def test_led_api_matches_puffco_connect():
    af = PuffcoUtils.revision_string_to_number("AF")
    assert asyncio.run(fake_peak(api_version=af - 1).get_led_api()) == 2
    assert asyncio.run(fake_peak(api_version=41).get_led_api()) == 3
    assert asyncio.run(fake_peak(api_version=41, has_preheat_color=True).get_led_api()) == 2
    # No /p/sys/fw/api file: fall back to the OTA version byte.
    assert asyncio.run(fake_peak(api_version=None, ota_version=20).get_led_api()) == 2


def test_older_firmware_writes_rgbt_to_profile_preheat_active_and_live_colour():
    ble = PuffcoBLE()
    ble._led_api = 2
    writes = []

    async def write_short(path, offset, flags, value):
        writes.append((path, bytes(value)))

    async def current_profile():
        return 1

    async def lantern_on():
        writes.append(("lantern on", b""))

    ble.write_short = write_short
    ble.get_current_profile = current_profile
    ble.start_lantern = lantern_on
    asyncio.run(ble.set_profile_solid_color(1, "3dd68c"))
    color = rgbt_color("#3dd68c")
    paths = [p for p, _ in writes]
    assert paths == [
        "/p/app/ltrn/colr",
        "lantern on",
        "/u/app/hc/1/colr",
        "/u/app/hc/1/phcl",
        "/u/app/hc/1/accl",
        "/p/app/thc/colr",
        "/p/app/thc/phcl",
        "/p/app/thc/accl",
    ]
    assert all(v == color for p, v in writes if p != "lantern on")
