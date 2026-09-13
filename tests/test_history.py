import json
import time
from datetime import datetime

from omapuffco import history


def write_events(deltas_at_offsets, first_seen=None):
    """Seed the history file with events at N seconds before now."""
    now = time.time()
    data = {
        "last_total": 100,
        "first_seen": first_seen if first_seen is not None else now - 86400,
        "events": [
            {"ts": now - offset, "delta": delta, "total": 100}
            for offset, delta in deltas_at_offsets
        ],
    }
    path = history.history_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


class TestRecordTotal:
    def test_first_reading_sets_a_baseline_without_inventing_history(self):
        assert history.record_total(500) is None
        assert history.get_stats()["tracked_total"] == 0

    def test_increase_is_logged_as_a_delta(self):
        history.record_total(500)
        event = history.record_total(503)
        assert event is not None
        assert event["delta"] == 3
        assert history.get_stats()["today"] == 3

    def test_unchanged_total_logs_nothing(self):
        history.record_total(500)
        assert history.record_total(500) is None

    def test_counter_going_backwards_rebaselines_silently(self):
        # A factory reset or a different device restarts the lifetime count.
        history.record_total(500)
        history.record_total(505)
        assert history.record_total(2) is None
        assert history.record_total(4) == {"ts": history._load()["events"][-1]["ts"], "delta": 2, "total": 4}

    def test_zero_does_not_wipe_a_real_lifetime_total(self):
        history.record_total(751)
        history.record_total(752)
        assert history.record_total(0) is None
        assert history._load()["last_total"] == 752

    def test_none_and_junk_are_ignored(self):
        assert history.record_total(None) is None
        assert history.record_total("many") is None
        assert history.record_total(-5) is None

    def test_survives_a_corrupt_history_file(self):
        path = history.history_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json")
        assert history.record_total(10) is None
        assert history.record_total(12)["delta"] == 2


class TestGetStats:
    def test_recent_event_counts_in_every_window(self):
        write_events([(5, 2)])
        stats = history.get_stats()
        assert stats["today"] == 2
        assert stats["this_week"] == 2
        assert stats["this_month"] == 2
        assert stats["this_year"] == 2

    def test_windows_are_nested(self):
        # Whatever today's weekday is, each window must contain the smaller one.
        write_events([(5, 1), (3600 * 30, 1), (86400 * 10, 1), (86400 * 200, 1)])
        stats = history.get_stats()
        assert stats["today"] <= stats["this_week"] <= stats["this_month"] <= stats["this_year"]

    def test_old_event_is_outside_the_year_but_still_totalled(self):
        write_events([(86400 * 400, 7)])
        stats = history.get_stats()
        assert stats["this_year"] == 0
        assert stats["tracked_total"] == 7

    def test_daily_series_length_matches_request(self):
        write_events([(5, 1)])
        assert len(history.get_stats(days=14)["daily"]) == 14
        assert len(history.get_stats(days=3)["daily"]) == 3

    def test_daily_series_ends_today_and_counts_today(self):
        write_events([(5, 4)])
        series = history.get_stats(days=7)["daily"]
        assert series[-1]["count"] == 4
        assert series[-1]["date"] == time.strftime("%Y-%m-%d")

    def test_empty_history_reports_zeroes(self):
        stats = history.get_stats()
        assert stats["today"] == 0
        assert stats["tracked_total"] == 0
        assert stats["tracking_since"] is None


class TestFailedReadPoison:
    def test_zero_baseline_with_no_events_is_not_a_real_total(self):
        history.record_total(0)
        assert history.has_device_total() is False
        assert history.record_total(50) is None
        assert history.get_stats()["today"] == 0
        assert history._load()["last_total"] == 50
        assert history.has_device_total() is True

    def test_none_is_ignored(self):
        assert history.record_total(None) is None
        assert history._load()["last_total"] is None


class TestRecordCycle:
    def test_counts_a_heat_cycle_locally(self):
        event = history.record_cycle()
        assert event["delta"] == 1
        assert history.get_stats()["today"] == 1

    def test_does_not_turn_a_zero_baseline_into_a_lifetime_total(self):
        history.record_total(0)
        history.record_cycle()
        assert history._load()["last_total"] == 0
        assert history.get_stats()["today"] == 1


class TestMetrics:
    def test_current_streak_counts_consecutive_days_ending_today(self):
        write_events([(5, 1), (86400, 1), (86400 * 2, 1)])
        stats = history.get_stats()
        assert stats["streak"] == 3
        assert stats["streak_best"] == 3

    def test_broken_streak_is_zero_when_today_is_empty(self):
        write_events([(86400, 1), (86400 * 2, 1)])
        assert history.get_stats()["streak"] == 0

    def test_top_hour_follows_the_busiest_bucket(self):
        now = time.time()
        path = history.history_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        six_am = datetime.now().replace(hour=6, minute=0, second=0, microsecond=0).timestamp()
        noon = datetime.now().replace(hour=12, minute=0, second=0, microsecond=0).timestamp()
        path.write_text(
            json.dumps(
                {
                    "last_total": 3,
                    "first_seen": now,
                    "events": [
                        {"ts": six_am, "delta": 2, "total": 2},
                        {"ts": noon, "delta": 1, "total": 3},
                    ],
                }
            )
        )
        stats = history.get_stats()
        assert stats["top_hour"] == 6
        assert stats["hours"][6] == 2
        assert stats["hours"][12] == 1

    def test_averages_come_from_cycle_metadata(self):
        history.record_cycle(temp_f=510, time_s=40, color="#3dd68c")
        history.record_cycle(temp_f=550, time_s=30, color="#ff4d4d")
        stats = history.get_stats()
        assert stats["avg_temp_f"] == 530
        assert stats["avg_time_s"] == 35
        assert stats["colors"][0] in {"#3dd68c", "#ff4d4d"}


class TestRetention:
    def test_events_past_the_window_are_pruned_on_write(self):
        stale = time.time() - (history.RETENTION_DAYS + 10) * 86400
        path = history.history_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "last_total": 10,
                    "first_seen": stale,
                    "events": [{"ts": stale, "delta": 99, "total": 10}],
                }
            )
        )
        history.record_total(11)
        kept = history._load()["events"]
        assert all(e["delta"] != 99 for e in kept)
        assert len(kept) == 1
