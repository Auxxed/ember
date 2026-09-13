import struct
import time

from omapuffco import audit, history

NOW = 1_800_000_000.0


def reached(index, ts, slot, nominal_c):
    raw = bytearray(16)
    struct.pack_into("<IB", raw, 0, ts, audit.REACHED_TEMP)
    raw[6] = slot
    raw[7] = nominal_c - 150
    return audit.parse_entry(index, bytes(raw))


def test_sessions_name_their_profile_and_temperature():
    found = audit.sessions([reached(4, 1_790_000_000, 2, 252), reached(5, 1_790_000_100, 7, 230)], 0, NOW)
    assert found[0]["profile"] == 2 and found[0]["temp_c"] == 252
    assert found[1]["profile"] == -1  # a one-off temperature, not a saved profile


def test_profile_usage_counts_recent_sessions_by_profile():
    now = time.time()
    sessions = [
        {"index": 1, "ts": now - 3600, "profile": 0, "temp_c": 252},
        {"index": 2, "ts": now - 7200, "profile": 0, "temp_c": 252},
        {"index": 3, "ts": now - 9000, "profile": 1, "temp_c": 266},
        {"index": 4, "ts": now - 40 * 86400, "profile": 1, "temp_c": 266},  # outside the window
    ]
    history.record_device_sessions(sessions, last_index=4, serial="PEAK")
    usage = history.get_stats()["profiles"]
    assert [(p["index"], p["count"]) for p in usage] == [(0, 2), (1, 1)]
    assert usage[0]["share"] == round(2 / 3, 3)
    assert usage[0]["temp_f"] == round(252 * 9 / 5 + 32)


def test_a_reread_adds_profiles_to_sessions_stored_without_them():
    now = time.time()
    history.record_device_sessions([{"index": 1, "ts": now - 60}], last_index=1, serial="PEAK")
    assert history.needs_profile_backfill()
    history.record_device_sessions([{"index": 1, "ts": now - 60, "profile": 3, "temp_c": 271}], last_index=1, serial="PEAK")
    assert history.get_stats()["profiles"][0]["index"] == 3
    assert not history.needs_profile_backfill()


def test_backfill_runs_only_once_even_if_the_log_has_no_profiles():
    history.record_device_sessions([{"index": 1, "ts": time.time() - 60}], last_index=1, serial="PEAK")
    history.mark_profile_backfilled()
    assert not history.needs_profile_backfill()


def test_usual_temperature_follows_recent_sessions_after_a_change():
    now = time.time()
    older = [{"index": i, "ts": now - 20 * 86400 + i, "profile": 2, "temp_c": 290} for i in range(1, 30)]
    recent = [{"index": 100 + i, "ts": now - 3600 + i, "profile": 2, "temp_c": 279} for i in range(10)]
    history.record_device_sessions(older + recent, last_index=110, serial="PEAK")
    usage = history.get_stats()["profiles"][0]
    assert usage["count"] == 39
    assert usage["temp_c"] == 279
