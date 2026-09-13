import cbor2

from omapuffco.codec import hexify
from omapuffco.constants import AnimationCode
from omapuffco.moods import pikaled2_payload, resolve_mood, resolve_style


class TestStyles:
    def test_connect_names_resolve(self):
        assert resolve_style("fade")["anim"] == AnimationCode.BREATHING
        assert resolve_style("fill")["anim"] == AnimationCode.RISING
        assert resolve_style("spin")["anim"] == AnimationCode.CIRCLING
        assert resolve_style("split")["anim"] == AnimationCode.CIRCLING
        assert resolve_style("disco")["anim"] == AnimationCode.HEAT_CYCLE_ACTIVE
        assert resolve_style("solid")["anim"] is None

    def test_legacy_names_still_work(self):
        assert resolve_style("breathing")["name"] == "fade"
        assert resolve_style("circling")["name"] == "spin"

    def test_unknown_style_raises(self):
        try:
            resolve_style("sparkle")
        except ValueError as exc:
            assert "sparkle" in str(exc)
        else:
            raise AssertionError("expected ValueError")


class TestPikaled2Payload:
    def test_keeps_the_colours_you_passed(self):
        payload = pikaled2_payload(7, ["#ff4d4d", "#3b9eff"])
        param = payload["lamp"]["param"]
        assert param["color"] == ["#ff4d4d", "#3b9eff"]
        assert param["colorLen"] == 2

    def test_fits_in_one_lorax_write(self):
        payload = pikaled2_payload(5, ["#ff4d4d", "#ffffff", "#3b9eff"])
        blob = cbor2.dumps(hexify(payload), canonical=True)
        assert len(blob) <= 250

        disco = resolve_mood("disco")
        disco_blob = cbor2.dumps(
            hexify(
                pikaled2_payload(
                    disco["anim"],
                    disco["colors"],
                    speed=disco["speed"],
                    offsets=disco["offsets"],
                )
            ),
            canonical=True,
        )
        assert len(disco_blob) <= 250


class TestExclusiveMoods:
    def test_hologram_is_purple_and_blue(self):
        mood = resolve_mood("hologram")
        assert mood["label"] == "Hologram"
        assert "#7c3aed" in mood["colors"]
        assert mood["anim"] == AnimationCode.CIRCLING

    def test_july4_alias(self):
        assert resolve_mood("4th of July")["id"] == "july4"
        assert resolve_mood("july4")["colors"] == ["#ff4d4d", "#ffffff", "#3b9eff"]

    def test_unknown_mood_lists_options(self):
        try:
            resolve_mood("neon")
        except ValueError as exc:
            assert "puffcon" in str(exc)
        else:
            raise AssertionError("expected ValueError")
