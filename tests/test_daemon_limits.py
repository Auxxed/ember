"""The daemon is the single chokepoint every profile write passes through.

The GUI's sliders already bound what it can send, but the CLI and a raw
RPC call do not — so these limits are what actually stands between a
malformed request and the heater.
"""

import pytest

from ember.daemon import (
    MAX_TEMP_F,
    MAX_TIME_S,
    MIN_TEMP_F,
    MIN_TIME_S,
    _clamp,
    _validate_index,
)
from ember.utils import PuffcoUtils


class TestClamp:
    def test_leaves_in_range_values_alone(self):
        assert _clamp(500, MIN_TEMP_F, MAX_TEMP_F) == 500

    def test_pulls_back_to_the_bounds(self):
        assert _clamp(99999, MIN_TEMP_F, MAX_TEMP_F) == MAX_TEMP_F
        assert _clamp(-40, MIN_TEMP_F, MAX_TEMP_F) == MIN_TEMP_F

    def test_bounds_are_inclusive(self):
        assert _clamp(MIN_TEMP_F, MIN_TEMP_F, MAX_TEMP_F) == MIN_TEMP_F
        assert _clamp(MAX_TEMP_F, MIN_TEMP_F, MAX_TEMP_F) == MAX_TEMP_F


class TestTemperatureLimits:
    def test_range_is_sane(self):
        assert MIN_TEMP_F < MAX_TEMP_F
        assert MAX_TEMP_F <= 620

    def test_absurd_celsius_request_still_lands_in_range(self):
        # `ember profile 0 --temp-c 500` must not reach the device as-is.
        requested = PuffcoUtils.c_to_f(500)
        assert _clamp(requested, MIN_TEMP_F, MAX_TEMP_F) == MAX_TEMP_F

    def test_time_limits_bracket_a_usable_cycle(self):
        assert MIN_TIME_S < MAX_TIME_S
        assert _clamp(0, MIN_TIME_S, MAX_TIME_S) == MIN_TIME_S
        assert _clamp(10_000, MIN_TIME_S, MAX_TIME_S) == MAX_TIME_S


class TestValidateIndex:
    @pytest.mark.parametrize("index", [0, 1, 2, 3])
    def test_accepts_the_four_device_profiles(self, index):
        assert _validate_index({"index": index}) == index

    @pytest.mark.parametrize("index", [-1, 4, 99, -100])
    def test_rejects_anything_else(self, index):
        with pytest.raises(ValueError, match="0-3"):
            _validate_index({"index": index})

    def test_accepts_numeric_strings(self):
        assert _validate_index({"index": "2"}) == 2

    def test_rejects_non_numeric(self):
        with pytest.raises((ValueError, TypeError)):
            _validate_index({"index": "second"})
