import struct

from quickpuff import faults
from quickpuff.audit import parse_entry


def raw(ts, code):
    return struct.pack("<IB", ts, code) + bytes(11)


def test_cache_survives_a_restart():
    cache = faults.empty_cache("79AAN-BTBA264-04886")
    cache["entries"][5] = parse_entry(5, raw(1_790_000_000, 8))
    cache["end"] = 6
    cache["placed"][5] = 1_790_000_000.0
    faults.save_cache(cache)
    loaded = faults.load_cache("79AAN-BTBA264-04886")
    assert loaded["end"] == 6
    assert loaded["entries"][5].code == 8
    assert loaded["placed"] == {5: 1_790_000_000.0}


def test_each_peak_has_its_own_cache():
    cache = faults.empty_cache("PEAK-A")
    cache["end"] = 9
    faults.save_cache(cache)
    assert faults.load_cache("PEAK-B")["end"] == 0


def test_a_date_worked_out_once_is_kept_after_the_peak_restarts():
    placed = {}
    faults.remember_times([{"index": 3, "ts": 1_790_000_000.0}], placed)
    later = [{"index": 3, "ts": None}]
    faults.remember_times(later, placed)
    assert later[0]["ts"] == 1_790_000_000.0


def test_corrupt_cache_starts_fresh():
    path = faults.cache_path("PEAK")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json")
    assert faults.load_cache("PEAK")["entries"] == {}
