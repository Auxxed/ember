import asyncio
import time
from datetime import datetime, timedelta

import pytest

from omapuffco import history
from omapuffco.cli import print_sessions
from omapuffco.daemon import OmaPuffcoDaemon, recap_message, recap_week_start
from omapuffco.paths import load_config, save_config


def daemon(tmp_path):
    d = OmaPuffcoDaemon(sock=tmp_path / "omapuffco.sock")
    d.sent = []
    d._desktop_notify = lambda title, body, urgency="normal": d.sent.append((title, body))
    return d


def seed_sessions(offsets_s, profile=1, temp_c=266):
    now = time.time()
    sessions = [
        {"index": 100 + i, "ts": now - offset, "profile": profile, "temp_c": temp_c, "preheat_s": 41.0}
        for i, offset in enumerate(offsets_s)
    ]
    history.record_device_sessions(sessions, last_index=100 + len(sessions), serial="PEAK")
    return sessions


# ---- sessions and notes


def test_sessions_come_newest_first_with_their_details():
    seed_sessions([7200, 60, 3600])
    rows = history.list_sessions()["sessions"]
    assert [r["key"] for r in rows] == ["d101", "d102", "d100"]
    assert rows[0]["temp_f"] == round(266 * 9 / 5 + 32)
    assert rows[0]["preheat_s"] == 41.0 and rows[0]["note"] == ""


def test_notes_can_be_added_edited_and_cleared():
    seed_sessions([60])
    history.set_note("d100", "Great flavor,\n  thick clouds")
    assert history.list_sessions()["sessions"][0]["note"] == "Great flavor, thick clouds"
    history.set_note("d100", "Edited later")
    assert history.list_sessions()["sessions"][0]["note"] == "Edited later"
    history.set_note("d100", "   ")
    assert history.list_sessions()["sessions"][0]["note"] == ""


def test_notes_survive_later_usage_syncs():
    seed_sessions([60])
    history.set_note("d100", "keep me")
    seed_sessions([60, 30])  # a re-read merges the same sessions and adds one
    rows = {r["key"]: r["note"] for r in history.list_sessions()["sessions"]}
    assert rows["d100"] == "keep me"


def test_bad_session_key_is_refused():
    with pytest.raises(ValueError):
        history.set_note("../etc", "nope")


def test_locally_seen_dabs_before_the_log_still_get_notes():
    now = time.time()
    history.record_total(100)
    data = history._load()
    data["events"] = [{"ts": now - 40 * 86400, "delta": 1, "total": 101, "temp_f": 510}]
    history._save(data)
    seed_sessions([60])
    rows = history.list_sessions()["sessions"]
    assert [r["key"][0] for r in rows] == ["d", "t"]
    assert rows[1]["temp_f"] == 510
    history.set_note(rows[1]["key"], "old one")
    assert history.list_sessions()["sessions"][1]["note"] == "old one"


def test_paging():
    seed_sessions([10 * i for i in range(1, 8)])
    page = history.list_sessions(limit=3, offset=3)
    assert page["total"] == 7 and len(page["sessions"]) == 3


def test_daemon_serves_sessions_and_notes_while_disconnected(tmp_path):
    seed_sessions([60])
    d = daemon(tmp_path)
    assert asyncio.run(d.handle("set_note", {"key": "d100", "text": "hi"}))["note"] == "hi"
    assert asyncio.run(d.handle("sessions", {"limit": 5}))["sessions"][0]["note"] == "hi"


def test_cli_lists_sessions_with_notes(capsys):
    rows = {"sessions": [{"key": "d7", "ts": time.time(), "profile": 2, "temp_c": 279, "temp_f": 535, "preheat_s": 41.2, "note": "smooth"}]}
    print_sessions(rows, "F")
    out = capsys.readouterr().out
    assert "d7" in out and "P2" in out and "535°F" in out and "heated in 41s" in out and out.strip().endswith("— smooth")


# ---- daily limit


def test_daily_limit_notifies_once_a_day(tmp_path):
    d = daemon(tmp_path)
    asyncio.run(d._set_daily_limit(3))
    assert load_config()["daily_limit"] == 3
    assert not asyncio.run(d._check_daily_limit(2))
    assert asyncio.run(d._check_daily_limit(3))
    assert not asyncio.run(d._check_daily_limit(4))
    assert d.sent == [("3 dabs today", "That's your daily limit of 3.")]


def test_daily_limit_off_never_notifies(tmp_path):
    d = daemon(tmp_path)
    assert not asyncio.run(d._check_daily_limit(40))
    assert asyncio.run(d._set_daily_limit(999))["daily_limit"] == 50
    assert asyncio.run(d._set_daily_limit(-4))["daily_limit"] == 0


# ---- weekly recap


def test_recap_is_due_from_sunday_evening():
    sunday_evening = datetime(2026, 9, 13, 19, 30)  # a Sunday
    assert sunday_evening.weekday() == 6
    assert recap_week_start(sunday_evening) == datetime(2026, 9, 7)
    assert recap_week_start(sunday_evening.replace(hour=18)) == datetime(2026, 8, 31)
    assert recap_week_start(datetime(2026, 9, 16, 9)) == datetime(2026, 9, 7)  # Wednesday: last week's


def test_recap_wording():
    names = lambda i: ["HASH", "TerpBlaster", "High", "WHITEHOT"][i]
    title, body = recap_message({"count": 18, "previous": 22, "top_profile": 2}, names, True)
    assert title == "Weekly recap"
    assert body == "18 sessions this week, mostly High. 4 fewer than the week before."
    _, body = recap_message({"count": 1, "previous": 1, "top_profile": None}, names, False)
    assert body == "1 session last week. Same as the week before."


def test_recap_waits_for_the_next_sunday_on_first_run_then_sends_once(tmp_path, monkeypatch):
    d = daemon(tmp_path)
    monkeypatch.setattr(history, "week_summary", lambda start: {"count": 5, "previous": 3, "top_profile": None})
    wednesday = datetime(2026, 9, 9, 12)
    assert not asyncio.run(d._maybe_send_recap(wednesday))  # first run only sets the baseline
    assert d.sent == []
    sunday_evening = datetime(2026, 9, 13, 20)
    assert asyncio.run(d._maybe_send_recap(sunday_evening))
    assert d.sent == [("Weekly recap", "5 sessions this week. 2 more than the week before.")]
    assert not asyncio.run(d._maybe_send_recap(sunday_evening.replace(hour=22)))
    assert len(d.sent) == 1


def test_recap_missed_on_sunday_goes_out_next_time_as_last_week(tmp_path, monkeypatch):
    d = daemon(tmp_path)
    monkeypatch.setattr(history, "week_summary", lambda start: {"count": 2, "previous": 2, "top_profile": None})
    save_config({**load_config(), "weekly_recap_sent": "2026-W36"})
    assert asyncio.run(d._maybe_send_recap(datetime(2026, 9, 15, 9)))  # Tuesday after
    assert d.sent == [("Weekly recap", "2 sessions last week. Same as the week before.")]


def test_recap_can_be_turned_off(tmp_path):
    d = daemon(tmp_path)
    asyncio.run(d._set_weekly_recap(False))
    assert load_config()["weekly_recap"] is False
    save_config({**load_config(), "weekly_recap_sent": "2000-W01"})
    assert not asyncio.run(d._maybe_send_recap(datetime(2026, 9, 13, 20)))


def test_week_summary_counts_both_weeks_and_the_top_profile():
    now = datetime.now()
    monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    base = monday.timestamp()
    sessions = [
        {"index": 1, "ts": base - 3 * 86400, "profile": 0},  # week before
        {"index": 2, "ts": base + 3600, "profile": 2},
        {"index": 3, "ts": base + 7200, "profile": 2},
        {"index": 4, "ts": base + 9000, "profile": 1},
    ]
    history.record_device_sessions([s for s in sessions if s["ts"] < time.time()], last_index=4, serial="PEAK")
    summary = history.week_summary(monday)
    expected_this_week = sum(1 for s in sessions[1:] if s["ts"] < time.time())
    assert summary["count"] == expected_this_week
    assert summary["previous"] == 1
    if expected_this_week >= 2:
        assert summary["top_profile"] == 2
