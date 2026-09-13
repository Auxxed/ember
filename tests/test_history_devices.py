import json
import time

from quickpuff import history


def sessions(n, start_index=1):
    now = time.time()
    return [{"index": start_index + i, "ts": now - 60 * (i + 1)} for i in range(n)]


def test_each_peak_keeps_its_own_usage():
    history.use_device("PEAK-A")
    history.record_device_sessions(sessions(3), last_index=3, serial="PEAK-A")
    history.use_device("PEAK-B")
    assert history.get_stats()["tracked_total"] == 0
    history.record_device_sessions(sessions(1), last_index=1, serial="PEAK-B")
    assert history.get_stats()["tracked_total"] == 1
    history.use_device("PEAK-A")
    assert history.get_stats()["tracked_total"] == 3


def test_existing_history_moves_to_the_peak_it_was_recorded_from():
    legacy = history.history_path()
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(json.dumps({"device_log_serial": "MINE", "device_sessions": sessions(2), "events": []}))
    history.use_device("MINE")
    assert history.get_stats()["tracked_total"] == 2
    assert not legacy.exists()
    assert legacy.with_name("dabs.json.migrated").exists()


def test_another_peak_does_not_adopt_someone_elses_history():
    legacy = history.history_path()
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(json.dumps({"device_log_serial": "MINE", "device_sessions": sessions(2), "events": []}))
    history.use_device("FRIEND")
    assert history.get_stats()["tracked_total"] == 0
    assert legacy.exists()


def test_serial_is_made_safe_for_a_file_name():
    history.use_device("../79AAN/BTBA 264")
    path = history.history_path()
    assert path.parent == history.data_dir() / "devices"
    assert "/" not in path.name
    assert not path.name.startswith(".")
