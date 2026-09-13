"""Battery saver rests the Peak by letting go of its Bluetooth link.

AW firmware ignores the sleep command and a held link keeps the Peak's radio
busy, so the daemon disconnects and reconnects when someone needs the Peak.
"""

import asyncio
import json

import pytest

from omapuffco import daemon as daemon_module
from omapuffco.cli import print_waybar
from omapuffco.constants import OperatingState
from omapuffco.daemon import OmaPuffcoDaemon
from omapuffco.paths import load_config

IDLE = int(OperatingState.IDLE)
PREHEAT = int(OperatingState.HEAT_CYCLE_PREHEAT)


class FakePeak:
    def __init__(self, state=IDLE):
        self.is_connected = True
        self.state = state
        self.slept = False
        self.heated = False
        self.address = "AA:BB:CC:11:22:33"
        self.device_mac = None

    async def get_operating_state(self):
        return self.state

    async def enter_sleep_mode(self):
        self.slept = True

    async def stop_lantern(self):
        pass

    async def start_heat_cycle(self):
        self.heated = True

    async def disconnect(self):
        self.is_connected = False


@pytest.fixture(autouse=True)
def no_saver_delay(monkeypatch):
    monkeypatch.setattr(daemon_module, "BATTERY_SAVER_SLEEP_S", 0)


def make_daemon(tmp_path, peak):
    d = OmaPuffcoDaemon(sock=tmp_path / "omapuffco.sock")
    d.battery_saver = True
    d._want_connected = True
    d.device = peak
    d.status.update({"connected": True, "battery": 84, "operating_state_id": IDLE})
    return d


def reachable(d, peak, calls):
    """Stands in for _connect: the link comes back and the rest ends."""

    async def connect(name, mac, **options):
        calls.append(options)
        peak.is_connected = True
        d.device = peak
        d.status.update({"connected": True, "operating_state_id": peak.state})
        d._end_rest()
        return d.status

    return connect


async def unreachable(name, mac, **options):
    raise ConnectionError("Peak Pro not found")


def test_saver_lets_go_of_an_idle_peak_and_keeps_the_last_reading(tmp_path):
    peak = FakePeak()
    d = make_daemon(tmp_path, peak)

    async def run():
        d._loop = asyncio.get_running_loop()
        await d._run_saver_sleep()
        assert peak.slept and not peak.is_connected
        assert d.device is None
        assert d.status["resting"] is True and d.status["connected"] is False
        assert d.status["battery"] == 84
        d._on_ble_drop()  # BlueZ reporting the disconnect we just made
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert d._reconnect_task is None
        assert d.status["operating_state"] == "Resting"

    asyncio.run(run())


def test_saver_keeps_the_link_while_the_panel_is_open(tmp_path):
    peak = FakePeak()
    d = make_daemon(tmp_path, peak)

    async def run():
        await d.handle("status", {"watch": True})
        await d._run_saver_sleep()

    asyncio.run(run())
    assert d.device is peak and peak.is_connected
    assert not d._resting and not peak.slept


def test_opening_the_panel_wakes_a_resting_peak_but_the_bar_does_not(tmp_path):
    peak = FakePeak()
    d = make_daemon(tmp_path, peak)
    calls = []

    async def run():
        await d._rest()
        d._connect = reachable(d, peak, calls)
        await d.handle("status", {})  # the bar widget's refresh
        await asyncio.sleep(0)
        assert calls == [] and d._resting
        await d.handle("status", {"watch": True})  # the panel
        await d._wake_task
        assert calls == [{}]
        assert d.device is peak and not d._resting and d.status["resting"] is False

    asyncio.run(run())


def test_a_command_reconnects_first_then_runs(tmp_path):
    peak = FakePeak()
    d = make_daemon(tmp_path, peak)
    calls = []

    async def run():
        await d._rest()
        d._connect = reachable(d, peak, calls)
        await d.handle("start_heat", {})

    asyncio.run(run())
    assert calls == [{}] and peak.heated and not d._resting


def test_settings_and_history_leave_it_resting(tmp_path):
    peak = FakePeak()
    d = make_daemon(tmp_path, peak)
    calls = []

    async def run():
        await d._rest()
        d._connect = reachable(d, peak, calls)
        await d.handle("set_daily_limit", {"limit": 3})
        await d.handle("sessions", {})
        await d.handle("get_config", {})

    asyncio.run(run())
    assert calls == [] and d._resting


def test_a_failed_wake_falls_back_to_the_usual_reconnect_retries(tmp_path):
    peak = FakePeak()
    d = make_daemon(tmp_path, peak)

    async def run():
        d._loop = asyncio.get_running_loop()
        await d._rest()
        d._connect = unreachable
        with pytest.raises(ConnectionError):
            await d.handle("start_heat", {})
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not d._resting
        assert d.status["connected"] is False and d.status["resting"] is False
        assert d._reconnect_task is not None
        d._reconnect_task.cancel()

    asyncio.run(run())


def test_check_in_refreshes_lightly_then_rests_again_when_idle(tmp_path):
    peak = FakePeak()
    d = make_daemon(tmp_path, peak)
    calls, synced = [], []

    async def run():
        await d._rest()
        d._connect = reachable(d, peak, calls)

        async def sync(delay=0.0):
            synced.append(True)

        d._sync_usage_safe = sync
        await d._check_in()

    asyncio.run(run())
    assert calls == [{"profiles": False, "sync": False}]
    assert synced == [True]
    assert d._resting and d.device is None and not peak.is_connected


def test_check_in_stays_connected_when_the_peak_is_heating(tmp_path):
    peak = FakePeak()
    d = make_daemon(tmp_path, peak)
    calls = []

    async def run():
        await d._rest()
        peak.state = PREHEAT  # someone pressed the Peak's button
        d._connect = reachable(d, peak, calls)

        async def sync(delay=0.0):
            pass

        d._sync_usage_safe = sync
        await d._check_in()

    asyncio.run(run())
    assert not d._resting and d.device is peak and d.status["resting"] is False


def test_unreachable_check_in_keeps_resting_without_retrying(tmp_path):
    peak = FakePeak()
    d = make_daemon(tmp_path, peak)

    async def run():
        d._loop = asyncio.get_running_loop()
        await d._rest()
        d._connect = unreachable
        await d._check_in()
        await asyncio.sleep(0)

    asyncio.run(run())
    assert d._resting and d.status["resting"] is True
    assert d._reconnect_task is None


def test_turning_battery_saver_off_wakes_it(tmp_path):
    peak = FakePeak()
    d = make_daemon(tmp_path, peak)
    calls = []

    async def run():
        await d._rest()
        d._connect = reachable(d, peak, calls)
        await d.handle("set_battery_saver", {"enable": False})
        await d._wake_task

    asyncio.run(run())
    assert calls == [{}] and not d._resting


def test_disconnect_while_resting_ends_the_rest(tmp_path):
    peak = FakePeak()
    d = make_daemon(tmp_path, peak)

    async def run():
        await d._rest()
        await d.handle("disconnect", {})

    asyncio.run(run())
    assert not d._resting and d.status["resting"] is False
    assert load_config()["auto_connect"] is False


def test_bar_shows_the_last_battery_while_resting(capsys):
    print_waybar(
        {"connected": False, "resting": True, "battery": 84, "operating_state": "Resting", "operating_state_id": -1}
    )
    out = json.loads(capsys.readouterr().out)
    assert out["class"] == "resting"
    assert out["text"] == "84%"
    assert out["tooltip"].startswith("Resting to save battery")
