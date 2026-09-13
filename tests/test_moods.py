import cbor2
import pytest

from omapuffco.codec import hexify
from omapuffco.moods import (
    DISCO_OFFSETS,
    NO_ANIMATION_OFFSETS,
    SPLIT_OFFSETS_2,
    mood_payload,
    resolve_mood,
    resolve_style,
)


def param(kind, colors, **kw):
    return mood_payload(kind, colors, **kw)["lamp"]["param"]


class TestStyles:
    def test_panel_styles_map_to_official_moods(self):
        assert resolve_style("fill")["kind"] == "vertical_slideshow"
        assert resolve_style("fade")["kind"] == "fade"
        assert resolve_style("disco")["kind"] == "disco"
        assert resolve_style("split")["kind"] == "split_gradient"
        assert resolve_style("spin")["kind"] == "spin"
        assert resolve_style("solid")["kind"] is None

    def test_cli_aliases_still_work(self):
        assert resolve_style("breathing")["kind"] == "breathing"
        assert resolve_style("circling")["kind"] == "circling_slow"

    def test_unknown_style_raises(self):
        with pytest.raises(ValueError, match="sparkle"):
            resolve_style("sparkle")


class TestOfficialPayloads:
    def test_no_animation_pads_colors_and_claims_regions(self):
        p = param("no_animation", ["#FF0000", "#00ff00"])
        assert p["anim"] == 1 and p["plDenom"] == 1 and p["speed"] == 64
        assert p["colorLen"] == 32
        assert p["color"][:2] == ["#ff0000", "#00ff00"]
        assert p["color"][2:] == ["#000000"] * 30
        assert p["offset"] == NO_ANIMATION_OFFSETS[2]

    def test_disco_offsets_scale_with_color_count(self):
        p = param("disco", ["#ff0000", "#00ff00", "#0000ff"])
        assert p["anim"] == 1
        assert p["colorLen"] == 15
        assert len(p["color"]) == 32
        assert p["offset"] == [int(v * 3 + 0.5) for v in DISCO_OFFSETS]
        assert p["plDenom"] == 0

    def test_disco_phase_locks_when_tempo_is_zero(self):
        p = param("disco", ["#ff0000", "#00ff00"], tempo=0)
        assert p["speed"] == 64 and p["plDenom"] == 1

    def test_spin_locks_phase_to_the_color_count(self):
        p = param("spin", ["#ff4fa3", "#3b9eff"])
        assert (p["anim"], p["plNum"], p["plDenom"]) == (7, 1, 2)
        assert p["speed"] == 64  # tempo 0.5 -> 120 cpm -> 120 * 256 / 480

    def test_split_gradient_picks_offsets_by_color_count(self):
        assert param("split_gradient", ["#ff0000", "#00ff00"])["offset"] == SPLIT_OFFSETS_2

    def test_color_cycle_starts_on_each_user_color(self):
        p = param("disco", ["#ff0000", "#0000ff"])
        assert p["color"][0] == "#ff0000"
        assert p["color"][5] == "#0000ff"

    def test_single_color_on_a_two_color_mood_cycles_against_black(self):
        p = param("fade", ["#ff6a1a"])
        assert p["colorLen"] == 10
        assert p["color"][0] == "#ff6a1a"
        assert "#000000" in p["color"][:10]

    def test_rejects_non_hex_colors(self):
        with pytest.raises(ValueError):
            mood_payload("fade", ["red", "#00ff00"])

    @pytest.mark.parametrize(
        "kind",
        ["no_animation", "disco", "fade", "spin", "split_gradient", "vertical_slideshow", "breathing", "circling_slow"],
    )
    def test_every_mood_encodes_to_cbor(self, kind):
        blob = cbor2.dumps(hexify(mood_payload(kind, ["#ff0000", "#00ff00", "#0000ff"])), canonical=True)
        decoded = cbor2.loads(blob)
        assert len(decoded["lamp"]["param"]["color"]) == 32 * 3


class TestExclusiveMoods:
    def test_hologram_is_a_spin_of_purple_and_blues(self):
        mood = resolve_mood("hologram")
        assert mood["label"] == "Hologram"
        assert "#7c3aed" in mood["colors"]
        assert mood["kind"] == "spin"

    def test_july4_alias(self):
        assert resolve_mood("4th of July")["id"] == "july4"
        assert resolve_mood("july4")["colors"] == ["#ff4d4d", "#ffffff", "#3b9eff"]

    def test_every_preset_builds_a_payload(self):
        for key in ("puffcon", "july4", "candle", "hologram", "lupus", "disco"):
            mood = resolve_mood(key)
            assert mood_payload(mood["kind"], mood["colors"])["lamp"]["name"] == "pikaled2"

    def test_unknown_mood_lists_options(self):
        with pytest.raises(ValueError, match="puffcon"):
            resolve_mood("neon")
