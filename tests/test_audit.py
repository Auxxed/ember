import struct
import time

from quickpuff import audit, history

NOW = 1_800_000_000.0


def entry(index, ts, code):
    return audit.parse_entry(index, struct.pack("<IB", ts, code) + bytes(11))


class TestSessions:
    def test_boot_relative_stamps_are_placed_against_the_device_clock(self):
        found = audit.sessions([entry(10, 900, audit.REACHED_TEMP)], device_clock=1000, host_now=NOW)
        assert found == [{"index": 10, "ts": NOW - 100}]

    def test_absolute_stamps_are_kept_as_is(self):
        found = audit.sessions([entry(3, 1_785_744_162, audit.REACHED_TEMP)], device_clock=5, host_now=NOW)
        assert found == [{"index": 3, "ts": 1_785_744_162.0}]

    def test_relative_stamps_from_before_a_reboot_are_dropped(self):
        entries = [
            entry(1, 500, audit.REACHED_TEMP),
            entry(2, 0, audit.SYSTEM_BOOT),
            entry(3, 40, audit.REACHED_TEMP),
        ]
        found = audit.sessions(entries, device_clock=100, host_now=NOW)
        assert [s["index"] for s in found] == [3]

    def test_relative_stamps_are_dropped_once_the_clock_is_set(self):
        found = audit.sessions([entry(4, 900, audit.REACHED_TEMP)], device_clock=1_790_000_000, host_now=NOW)
        assert found == []

    def test_only_cycles_that_reached_temperature_count(self):
        entries = [
            entry(1, 10, audit.PREHEAT_START),
            entry(2, 30, audit.REACHED_TEMP),
            entry(3, 70, audit.CYCLE_COMPLETE),
        ]
        found = audit.sessions(entries, device_clock=100, host_now=NOW)
        assert [s["index"] for s in found] == [2]


class TestDeviceSessionsInStats:
    def seed(self):
        now = time.time()
        history._save(
            {
                "last_total": 862,
                "first_seen": now - 30 * 86400,
                "device_total_seen": True,
                "events": [
                    {"ts": now - 20 * 86400, "delta": 1, "total": 752},
                    # A counter jump logged as one event: not real dabs.
                    {"ts": now - 60, "delta": 109, "total": 862},
                ],
            }
        )
        return now

    def test_device_log_replaces_local_counts_for_the_period_it_covers(self):
        now = self.seed()
        added = history.record_device_sessions(
            [{"index": 5, "ts": now - 3600}, {"index": 6, "ts": now - 1800}],
            last_index=6,
            serial="A",
        )
        assert added == 2
        stats = history.get_stats()
        assert stats["tracked_total"] == 3
        assert stats["source"] == "device"

    def test_resync_does_not_double_count(self):
        now = self.seed()
        sessions = [{"index": 5, "ts": now - 3600}]
        history.record_device_sessions(sessions, last_index=5, serial="A")
        assert history.record_device_sessions(sessions, last_index=5, serial="A") == 0
        assert history.get_stats()["tracked_total"] == 2

    def test_a_different_device_starts_its_log_over(self):
        now = self.seed()
        history.record_device_sessions(
            [{"index": 5, "ts": now - 3600}, {"index": 6, "ts": now - 1800}], last_index=6, serial="A"
        )
        history.record_device_sessions([{"index": 1, "ts": now - 900}], last_index=1, serial="B")
        assert history.get_stats()["tracked_total"] == 2
        assert history.device_log_state() == {"index": 1, "serial": "B"}


class TestPreheatTiming:
    def test_reached_temp_entries_carry_estimate_and_real_preheat(self):
        raw = struct.pack("<IB", 900, audit.REACHED_TEMP) + bytes(5) + struct.pack("<HH", 1439, 2771) + bytes(2)
        found = audit.sessions([audit.parse_entry(7, raw)], device_clock=1000, host_now=NOW)
        assert found[0]["preheat_estimate_s"] == 14.39
        assert found[0]["preheat_s"] == 27.71

    def test_resync_fills_in_timing_and_scale_is_the_median_ratio(self):
        now = time.time()
        history.record_device_sessions([{"index": 1, "ts": now - 60}], last_index=1, serial="A")
        assert history.preheat_scale() is None
        added = history.record_device_sessions(
            [
                {"index": 1, "ts": now - 60, "preheat_estimate_s": 15.0, "preheat_s": 27.0},
                {"index": 2, "ts": now - 30, "preheat_estimate_s": 16.0, "preheat_s": 32.0},
                {"index": 3, "ts": now - 10, "preheat_estimate_s": 10.0, "preheat_s": 19.0},
            ],
            last_index=3,
            serial="A",
        )
        assert added == 2
        assert history.get_stats()["tracked_total"] == 3
        assert history.preheat_scale() == 1.9
