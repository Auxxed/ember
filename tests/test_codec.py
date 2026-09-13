from omapuffco.codec import (
    decode_puffco_json,
    first_color,
    hexify,
    ulid16_to_str,
    ulid26_to_16,
)

SAMPLE_ULID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"


class TestUlid:
    def test_round_trip(self):
        assert ulid16_to_str(ulid26_to_16(SAMPLE_ULID)) == SAMPLE_ULID

    def test_encodes_to_sixteen_bytes(self):
        assert len(ulid26_to_16(SAMPLE_ULID)) == 16

    def test_ambiguous_characters_alias_to_digits(self):
        # I/L alias to 1 and O aliases to 0, so a transcribed ULID still decodes.
        assert ulid26_to_16("O1ARZ3NDEKTSV4RRFFQ69G5FAV") == ulid26_to_16(SAMPLE_ULID)


class TestHexify:
    def test_bare_hex_string_becomes_bytes(self):
        assert hexify("#ff6a1a") == b"\xff\x6a\x1a"

    def test_accepts_hex_without_hash(self):
        assert hexify("ff6a1a") == b"\xff\x6a\x1a"

    def test_color_lists_concatenate(self):
        assert hexify(["#ff0000", "#00ff00"], key="color") == b"\xff\x00\x00\x00\xff\x00"

    def test_user_colors_stay_separate(self):
        assert hexify(["#ff0000", "#00ff00"], key="userColors") == [b"\xff\x00\x00", b"\x00\xff\x00"]

    def test_non_colour_strings_pass_through(self):
        assert hexify("breathing") == "breathing"

    def test_ulid_keys_are_encoded(self):
        assert hexify(SAMPLE_ULID, key="profileUlid") == ulid26_to_16(SAMPLE_ULID)

    def test_walks_nested_structures(self):
        out = hexify({"lamp": {"param": {"color": ["#ff6a1a"]}}})
        assert out["lamp"]["param"]["color"] == b"\xff\x6a\x1a"


class TestDecode:
    def test_three_byte_values_become_hex(self):
        assert decode_puffco_json({"tint": b"\xff\x6a\x1a"}) == {"tint": "#ff6a1a"}

    def test_colour_payloads_split_on_colour_length(self):
        decoded = decode_puffco_json({"colorLen": 2, "color": b"\xff\x00\x00\x00\xff\x00"})
        assert decoded["color"] == ["#ff0000", "#00ff00"]

    def test_colour_without_length_falls_back_to_triples(self):
        decoded = decode_puffco_json({"color": b"\xff\x00\x00\x00\xff\x00"})
        assert decoded["color"] == ["#ff0000", "#00ff00"]

    def test_ulid_bytes_decode_back_to_text(self):
        decoded = decode_puffco_json({"profileUlid": ulid26_to_16(SAMPLE_ULID)})
        assert decoded["profileUlid"] == SAMPLE_ULID

    def test_round_trip_through_hexify(self):
        original = {"lamp": {"param": {"color": ["#ff6a1a"], "colorLen": 1}}}
        assert decode_puffco_json(hexify(original)) == original


class TestFirstColor:
    def test_reads_lamp_parameter(self):
        assert first_color({"lamp": {"param": {"color": ["#ff6a1a", "#000000"]}}}) == "#ff6a1a"

    def test_falls_back_to_user_colours(self):
        assert first_color({"meta": {"userColors": ["#3dd68c"]}}) == "#3dd68c"

    def test_missing_colour_is_none(self):
        assert first_color({}) is None
        assert first_color(None) is None
        assert first_color({"lamp": {"param": {"color": []}}}) is None
