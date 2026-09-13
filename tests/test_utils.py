from ember.constants import BatteryChargeSource, CHARGE_SOURCE_LABELS
from ember.utils import PuffcoUtils


class TestTemperature:
    def test_known_conversions(self):
        assert PuffcoUtils.c_to_f(0) == 32
        assert PuffcoUtils.c_to_f(100) == 212
        assert PuffcoUtils.f_to_c(32) == 0
        assert PuffcoUtils.f_to_c(212) == 100

    def test_round_trip_holds_across_the_working_range(self):
        for fahrenheit in range(400, 621, 10):
            assert PuffcoUtils.c_to_f(PuffcoUtils.f_to_c(fahrenheit)) == fahrenheit


class TestCString:
    def test_stops_at_the_first_nul(self):
        assert PuffcoUtils.c_string(b"Blue\x00GreenTail") == "Blue"

    def test_strips_trailing_nuls_and_whitespace(self):
        assert PuffcoUtils.c_string(b"  Peak  \x00\x00") == "Peak"

    def test_empty_and_none(self):
        assert PuffcoUtils.c_string(None) == ""
        assert PuffcoUtils.c_string(b"") == ""
        assert PuffcoUtils.c_string(b"\x00leftover") == ""

    def test_already_decoded_text(self):
        assert PuffcoUtils.c_string("Blue\x00Green") == "Blue"


class TestFormatUptime:
    def test_seconds_only(self):
        assert PuffcoUtils.format_uptime(90) == "1m 30s"

    def test_promotes_to_hours(self):
        assert PuffcoUtils.format_uptime(3700) == "1h 1m"

    def test_promotes_to_days(self):
        assert PuffcoUtils.format_uptime(90000) == "1d 1h 0m"

    def test_negative_clamps_to_zero(self):
        assert PuffcoUtils.format_uptime(-5) == "0m 0s"


class TestChargeSourceLabels:
    def test_dock_and_usb_are_named(self):
        assert CHARGE_SOURCE_LABELS[BatteryChargeSource.USB] == "USB"
        assert CHARGE_SOURCE_LABELS[BatteryChargeSource.POWER_DOCK] == "Dock"
        assert CHARGE_SOURCE_LABELS[BatteryChargeSource.NONE] == "Unplugged"


class TestFormatBirthday:
    def test_known_unix_stamp(self):
        # 2025-10-03 06:55:32 local-ish; we only assert the calendar day.
        label = PuffcoUtils.format_birthday(1759488932)
        assert "2025" in label
        assert "Oct" in label

    def test_empty_on_junk(self):
        assert PuffcoUtils.format_birthday(None) == ""
        assert PuffcoUtils.format_birthday(0) == ""
        assert PuffcoUtils.format_birthday("nope") == ""


class TestRevisionNumber:
    def test_zero_is_unset(self):
        assert PuffcoUtils.revision_number_to_string(0) == "X*"

    def test_single_letters(self):
        assert PuffcoUtils.revision_number_to_string(1) == "A"
        assert PuffcoUtils.revision_number_to_string(2) == "B"

    def test_skips_ambiguous_letters(self):
        # I, L, O, Q, S are omitted so revisions can't be misread.
        produced = {PuffcoUtils.revision_number_to_string(n) for n in range(1, 22)}
        assert not produced & {"I", "L", "O", "Q", "S"}

    def test_rolls_over_to_two_letters(self):
        assert PuffcoUtils.revision_number_to_string(21) == "Z"
        assert PuffcoUtils.revision_number_to_string(22) == "AA"

    def test_non_integer_passes_through(self):
        assert PuffcoUtils.revision_number_to_string("beta") == "beta"
        assert PuffcoUtils.revision_number_to_string(-1) == "-1"
