"""Handoff: a laptop and a desktop sharing one Peak.

The Peak Pro keeps a single Bluetooth link, so two machines running QuickPuff
would otherwise take it from each other every few seconds. Handoff gives it to
whichever computer someone is sitting at.
"""

import asyncio
import json
import time

from quickpuff.cli import print_waybar
from quickpuff.daemon import (
    AWAY_BACKOFF_S,
    CLAIM_WINDOW_S,
    CONTENTION_BACKOFF_S,
    CONTENTION_LINK_S,
    QuickPuffDaemon,
    reconnect_delay,
    should_hold_peak,
)


class FakePeak:
    def __init__(self):
        self.is_connected = True
        self.address = "AA:BB:CC:11:22:33"
        self.device_mac = None

    async def disconnect(self):
        self.is_connected = False


class FakePresence:
    def __init__(self, active=True, available=True):
        self.active = active
        self.available = available


def make_daemon(tmp_path, peak=None, active=True):
    d = QuickPuffDaemon(sock=tmp_path / "quickpuff.sock")
    d._handoff = True
    d._presence = FakePresence(active=active)
    d._want_connected = True
    if peak:
        d.device = peak
        d.status.update({"connected": True})
    return d


# --- the two decisions, on their own ---------------------------------------

def test_a_quiet_link_reconnects_at_the_old_gentle_pace():
    assert reconnect_delay(0, True, 2.0) == 2.0
    assert reconnect_delay(0, True, 12.8) == 12.8


def test_each_strike_stands_further_off():
    delays = [reconnect_delay(n, True, 2.0) for n in range(1, len(CONTENTION_BACKOFF_S) + 1)]
    assert delays == list(CONTENTION_BACKOFF_S)
    assert delays == sorted(delays)


def test_the_standoff_stops_growing_at_the_last_step():
    assert reconnect_delay(99, True, 2.0) == CONTENTION_BACKOFF_S[-1]


def test_an_empty_seat_stops_racing_for_the_peak():
    assert reconnect_delay(0, False, 2.0) == AWAY_BACKOFF_S
    assert reconnect_delay(3, False, 2.0) == AWAY_BACKOFF_S


def test_handoff_off_keeps_the_peak_whatever_the_other_computer_wants():
    assert should_hold_peak(False, False, 10_000) is True


def test_an_occupied_seat_keeps_the_peak():
    assert should_hold_peak(True, True, 10_000) is True


def test_a_locked_seat_lets_the_peak_go():
    assert should_hold_peak(True, False, CLAIM_WINDOW_S + 1) is False


def test_a_command_over_ssh_holds_the_peak_on_a_locked_machine():
    assert should_hold_peak(True, False, CLAIM_WINDOW_S - 1) is True


# --- spotting the other computer -------------------------------------------

def test_a_link_that_dies_young_counts_as_the_other_computer(tmp_path):
    d = make_daemon(tmp_path, FakePeak())
    d._link_started = time.monotonic()
    d._on_ble_drop()
    assert d._strikes == 1
    d._on_ble_drop()
    assert d._strikes == 2


def test_a_link_that_lasted_was_simply_lost(tmp_path):
    d = make_daemon(tmp_path, FakePeak())
    d._strikes = 3
    d._link_started = time.monotonic() - (CONTENTION_LINK_S + 1)
    d._on_ble_drop()
    assert d._strikes == 0


def test_letting_go_on_purpose_is_not_a_strike(tmp_path):
    d = make_daemon(tmp_path, FakePeak())
    d._yielded = True
    d._link_started = time.monotonic()
    d._on_ble_drop()
    assert d._strikes == 0


# --- handing over and taking back ------------------------------------------

def test_locking_this_computer_hands_the_peak_over(tmp_path):
    peak = FakePeak()
    d = make_daemon(tmp_path, peak)
    d._last_user_cmd = float("-inf")
    d._presence.active = False

    asyncio.run(d._release_for_handoff())

    assert d._yielded is True
    assert peak.is_connected is False
    assert d.device is None
    assert d.status["handed_off"] is True
    assert d.status["connected"] is False


def test_a_locked_computer_someone_is_driving_keeps_the_peak(tmp_path):
    peak = FakePeak()
    d = make_daemon(tmp_path, peak)
    d._presence.active = False
    d._last_user_cmd = time.monotonic()

    asyncio.run(d._release_for_handoff())

    assert d._yielded is False
    assert peak.is_connected is True


def test_coming_back_claims_the_peak_and_drops_the_standoff(tmp_path):
    d = make_daemon(tmp_path, active=False)
    d._yielded = True
    d._strikes = 4
    scheduled = []
    d._schedule_reconnect = lambda: scheduled.append(True)

    asyncio.run(d._claim_peak("back at this computer"))

    assert d._strikes == 0
    assert d._yielded is False
    assert d.status["handed_off"] is False
    assert scheduled == [True]


def test_claiming_while_already_connected_changes_nothing(tmp_path):
    d = make_daemon(tmp_path, FakePeak())
    scheduled = []
    d._schedule_reconnect = lambda: scheduled.append(True)

    asyncio.run(d._claim_peak("claimed by hand"))

    assert scheduled == []


def test_no_logind_means_this_seat_always_counts_as_in_use(tmp_path):
    d = make_daemon(tmp_path)
    d._presence = FakePresence(active=False, available=False)
    assert d._seat_occupied() is True
    assert d._hold_allowed() is True


def test_handoff_switched_off_ignores_an_empty_seat(tmp_path):
    d = make_daemon(tmp_path)
    d._handoff = False
    d._presence.active = False
    assert d._seat_occupied() is True
    assert d._hold_allowed() is True


def test_a_link_lost_mid_handshake_counts_as_the_other_computer(tmp_path):
    """The clearest tell there is: the Peak goes away before setup finishes."""
    d = make_daemon(tmp_path, FakePeak())
    d._link_started = time.monotonic()
    d.device = None
    d._on_ble_drop()
    assert d._strikes == 1


def test_a_drop_before_any_link_is_not_a_strike(tmp_path):
    d = make_daemon(tmp_path)
    d.device = None
    d._on_ble_drop()
    assert d._strikes == 0


def test_each_retry_inside_one_connect_is_timed_on_its_own(tmp_path):
    """connect() retries internally; a later attempt must not be measured
    from the first one, or a string of short links reads as one long one."""
    d = make_daemon(tmp_path, FakePeak())
    d._link_started = time.monotonic()
    d._on_ble_drop()
    assert d._strikes == 1
    # The clock restarts, so the next short link is a strike too.
    d._on_ble_drop()
    assert d._strikes == 2
    # And a long gap after a drop still reads as a link that held.
    d._link_started = time.monotonic() - (CONTENTION_LINK_S + 1)
    d._on_ble_drop()
    assert d._strikes == 0


# --- what the bar says ------------------------------------------------------

def test_bar_shows_the_last_battery_when_another_computer_has_the_peak(capsys):
    """A handed-off Peak is healthy, so the bar shouldn't look broken."""
    print_waybar(
        {
            "connected": False,
            "handed_off": True,
            "battery": 84,
            "operating_state": "Handed off",
            "operating_state_id": -1,
        }
    )
    out = json.loads(capsys.readouterr().out)
    assert out["class"] == "resting"
    assert out["text"] == "84%"
    assert out["tooltip"].startswith("Another computer has the Peak")


def test_a_genuinely_disconnected_peak_still_looks_disconnected(capsys):
    print_waybar({"connected": False, "operating_state": "Disconnected", "operating_state_id": -1})
    out = json.loads(capsys.readouterr().out)
    assert out["class"] == "disconnected"
    assert out["text"] == "Peak"


def test_resting_wins_over_handoff_in_the_bar(capsys):
    """Battery saver's own wording stays put if both flags are somehow set."""
    print_waybar(
        {"connected": False, "resting": True, "handed_off": True, "battery": 84,
         "operating_state": "Resting", "operating_state_id": -1}
    )
    out = json.loads(capsys.readouterr().out)
    assert out["tooltip"].startswith("Resting to save battery")


def test_pressing_connect_takes_the_peak_back(tmp_path):
    """Connect is deliberate, so it must beat an ongoing standoff."""
    d = make_daemon(tmp_path)
    d._strikes = 4
    d._yielded = True
    seen = []

    async def fake_connect(name, mac):
        seen.append((name, mac))
        return d.status

    d._connect = fake_connect
    asyncio.run(d.handle("connect", {}))

    assert d._strikes == 0
    assert d._yielded is False
    assert seen == [(None, None)]


def test_a_retry_that_never_linked_still_restarts_the_clock(tmp_path):
    """A try that fails outright never reaches _on_ble_drop, so only the
    on_attempt hook keeps the next drop measured against the right try."""
    d = make_daemon(tmp_path, FakePeak())
    d._link_started = time.monotonic() - 600
    d._mark_attempt()
    d._on_ble_drop()
    assert d._strikes == 1


def test_ble_reports_every_internal_attempt(tmp_path):
    """The hook has to fire per try inside connect(), not once per call."""
    import inspect

    from quickpuff.ble import PuffcoBLE

    src = inspect.getsource(PuffcoBLE.connect)
    body = src.split("for attempt in range", 1)
    assert len(body) == 2, "connect() no longer loops over attempts"
    assert "self._on_attempt()" in body[1], "on_attempt is not called inside the retry loop"


def test_slow_setup_is_not_counted_as_time_holding_the_peak(tmp_path):
    """Establishing a link can take tens of seconds on a busy radio. Counting
    that as time we held the Peak made short links look long and lost strikes.
    """
    d = make_daemon(tmp_path, FakePeak())
    d._mark_attempt()
    # ...a slow setup, then the link becomes usable, then dies soon after.
    d._link_started = time.monotonic()
    d._on_ble_drop()
    assert d._strikes == 1
