"""The daemon keeps the Peak's radio quiet unless someone is using it."""

import asyncio

from quickpuff import daemon as daemon_module
from quickpuff.constants import OperatingState
from quickpuff.daemon import (
    FULL_SNAPSHOT_EVERY_S,
    IDLE_POLL_S,
    WATCHED_COUNTERS_S,
    WATCHED_POLL_S,
    IDLE_SLEEP_S,
    QuickPuffDaemon,
    idle_sleep_due,
    poll_delay,
    snapshot_kind,
)

IDLE = int(OperatingState.IDLE)
PREHEAT = int(OperatingState.HEAT_CYCLE_PREHEAT)
FADE = int(OperatingState.HEAT_CYCLE_FADE)


def make_daemon(tmp_path):
    return QuickPuffDaemon(sock=tmp_path / "quickpuff.sock")


def test_poll_speed_follows_what_the_peak_is_doing():
    assert poll_delay(PREHEAT, watching=False, watched_interval=1.5) == 0.7
    assert poll_delay(FADE, watching=True, watched_interval=1.5) == 0.7
    assert poll_delay(IDLE, watching=True, watched_interval=1.5) == 1.5
    assert poll_delay(IDLE, watching=False, watched_interval=1.5) == IDLE_POLL_S
    assert poll_delay(None, watching=False, watched_interval=1.5) == IDLE_POLL_S


def test_idle_sleep_needs_long_quiet_on_both_the_peak_and_the_user():
    now = 10_000.0
    long_ago = now - IDLE_SLEEP_S - 1
    assert idle_sleep_due(long_ago, now, last_user_cmd=long_ago, watching=False)
    assert not idle_sleep_due(long_ago, now, last_user_cmd=now - 60, watching=False)
    assert not idle_sleep_due(long_ago, now, last_user_cmd=long_ago, watching=True)
    assert not idle_sleep_due(now - 60, now, last_user_cmd=long_ago, watching=False)
    assert not idle_sleep_due(None, now, last_user_cmd=long_ago, watching=False)


def test_panel_status_request_marks_watching_and_wakes_the_poll(tmp_path):
    d = make_daemon(tmp_path)

    async def run():
        assert not d._watching()
        await d.handle("status", {})  # the bar widget: not watching
        assert not d._watching() and not d._poll_wake.is_set()
        await d.handle("status", {"watch": True})  # the open panel
        assert d._watching() and d._poll_wake.is_set()

    asyncio.run(run())


def test_poll_wait_returns_early_when_woken(tmp_path):
    d = make_daemon(tmp_path)

    async def run():
        loop = asyncio.get_running_loop()
        loop.call_later(0.01, d._poll_wake.set)
        start = loop.time()
        await d._poll_wait(5.0)
        assert loop.time() - start < 1.0
        assert not d._poll_wake.is_set()

    asyncio.run(run())


def test_battery_saver_sleeps_a_peak_left_idle(tmp_path, monkeypatch):
    d = make_daemon(tmp_path)
    d.battery_saver = True
    scheduled = []
    d._schedule_saver_sleep = lambda: scheduled.append(True)
    clock = [50_000.0]
    monkeypatch.setattr(daemon_module.time, "monotonic", lambda: clock[0])

    async def run():
        await d._maybe_idle_sleep(IDLE, watching=False)  # starts the idle timer
        clock[0] += IDLE_SLEEP_S / 2
        await d._maybe_idle_sleep(IDLE, watching=False)
        assert scheduled == []
        clock[0] += IDLE_SLEEP_S / 2 + 1
        await d._maybe_idle_sleep(IDLE, watching=False)
        assert scheduled == [True]

    asyncio.run(run())


def test_opening_the_panel_or_heating_resets_the_idle_timer(tmp_path, monkeypatch):
    d = make_daemon(tmp_path)
    d.battery_saver = True
    scheduled = []
    d._schedule_saver_sleep = lambda: scheduled.append(True)
    clock = [50_000.0]
    monkeypatch.setattr(daemon_module.time, "monotonic", lambda: clock[0])

    async def run():
        await d._maybe_idle_sleep(IDLE, watching=False)
        clock[0] += IDLE_SLEEP_S - 5
        await d._maybe_idle_sleep(IDLE, watching=True)  # panel opened
        await d._maybe_idle_sleep(PREHEAT, watching=False)  # then a dab
        clock[0] += 10
        await d._maybe_idle_sleep(IDLE, watching=False)
        assert scheduled == []

    asyncio.run(run())


def test_idle_sleep_is_part_of_battery_saver_only(tmp_path, monkeypatch):
    d = make_daemon(tmp_path)
    d.battery_saver = False
    scheduled = []
    d._schedule_saver_sleep = lambda: scheduled.append(True)
    clock = [50_000.0]
    monkeypatch.setattr(daemon_module.time, "monotonic", lambda: clock[0])

    async def run():
        await d._maybe_idle_sleep(IDLE, watching=False)
        clock[0] += IDLE_SLEEP_S * 2
        await d._maybe_idle_sleep(IDLE, watching=False)
        assert scheduled == []

    asyncio.run(run())


def test_profiles_are_reread_when_the_panel_opens_and_rarely_after():
    long = FULL_SNAPSHOT_EVERY_S
    assert snapshot_kind(True, False, False, since_full=10, since_counters=10) == "full"  # panel just opened
    assert snapshot_kind(True, True, False, since_full=10, since_counters=10) is None  # still open
    assert snapshot_kind(True, True, False, since_full=10, since_counters=WATCHED_COUNTERS_S) == "counters"
    assert snapshot_kind(True, True, False, since_full=long, since_counters=10) == "full"


def test_closed_panel_refreshes_counters_every_few_minutes():
    long = FULL_SNAPSHOT_EVERY_S
    assert snapshot_kind(False, False, False, since_full=long * 3, since_counters=long - 1) is None
    assert snapshot_kind(False, False, False, since_full=long * 3, since_counters=long) == "counters"


def test_a_session_gets_quick_polls_only():
    long = FULL_SNAPSHOT_EVERY_S * 10
    assert snapshot_kind(True, False, True, since_full=long, since_counters=long) is None
    assert snapshot_kind(False, False, True, since_full=long, since_counters=long) is None


def test_open_panel_polls_every_few_seconds(tmp_path):
    assert make_daemon(tmp_path).poll_interval == WATCHED_POLL_S
    assert poll_delay(IDLE, watching=True, watched_interval=WATCHED_POLL_S) == WATCHED_POLL_S
