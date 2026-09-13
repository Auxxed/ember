import cbor2
import pytest

from quickpuff.codec import hexify
from quickpuff.lights import SINGLE_COLOR_OFFSETS, normalize_color, solid_color_payload


def test_single_color_matches_the_official_no_animation_lamp():
    param = solid_color_payload("#FF6A1A")["lamp"]["param"]
    assert solid_color_payload("#ff6a1a")["lamp"]["name"] == "pikaled2"
    assert param["color"] == ["#ff6a1a"]
    assert (param["anim"], param["speed"], param["plNum"], param["plDenom"], param["colorLen"]) == (1, 64, 0, 1, 32)
    assert param["offset"] == SINGLE_COLOR_OFFSETS


def test_color_is_sent_as_three_bytes_and_fits_one_write():
    blob = cbor2.dumps(hexify(solid_color_payload("3dd68c")), canonical=True)
    assert cbor2.loads(blob)["lamp"]["param"]["color"] == bytes.fromhex("3dd68c")
    assert len(blob) <= 250


@pytest.mark.parametrize("bad", ["red", "#12345", "#gggggg", ""])
def test_rejects_anything_but_hex(bad):
    with pytest.raises(ValueError):
        normalize_color(bad)
