"""Broken and dead inputs stay gone; the lantern and brightness show what the Peak has."""

import asyncio

import pytest

from quickpuff import daemon as daemon_module
from quickpuff.ble import PuffcoBLE
from quickpuff.cli import async_main
from quickpuff.daemon import QuickPuffDaemon
from quickpuff.paths import load_config, save_config


class FakePeak:
    is_connected = True
    address = "AA:BB:CC:11:22:33"
    device_mac = None

    async def start_lantern(self):
        pass

    async def stop_lantern(self):
        pass


def make_daemon(tmp_path):
    return QuickPuffDaemon(sock=tmp_path / "quickpuff.sock")


@pytest.mark.parametrize("command", ["sleep", "version"])
def test_cli_no_longer_offers_sleep_or_the_version_flash(command):
    # AW firmware ignores sleep, and the version flash left the Peak stuck.
    with pytest.raises(SystemExit) as exit_info:
        asyncio.run(async_main([command]))
    assert exit_info.value.code == 2


@pytest.mark.parametrize("command", ["sleep", "show_version", "set_poll_interval", "get_config", "set_config"])
def test_daemon_rejects_removed_commands(tmp_path, command):
    d = make_daemon(tmp_path)
    d.device = FakePeak()
    with pytest.raises(ValueError, match="Unknown command"):
        asyncio.run(d.handle(command, {"battery_rated_mah": 900}))
    # set_config used to write any setting it was handed.
    assert load_config()["battery_rated_mah"] == 1700


def test_lantern_shows_off_once_the_peaks_own_timer_runs_out(tmp_path, monkeypatch):
    d = make_daemon(tmp_path)
    clock = [5_000.0]
    monkeypatch.setattr(daemon_module.time, "monotonic", lambda: clock[0])
    d.device = FakePeak()
    d.status["lantern_timeout"] = 1800.0
    asyncio.run(d.handle("start_lantern", {}))
    clock[0] += 1799
    assert d._stamp_local({})["lantern"] is True
    clock[0] += 2
    assert asyncio.run(d.handle("status", {}))["lantern"] is False
    assert d._stamp_local({})["lantern"] is False


def test_lantern_without_a_known_timeout_stays_as_set(tmp_path, monkeypatch):
    d = make_daemon(tmp_path)
    clock = [5_000.0]
    monkeypatch.setattr(daemon_module.time, "monotonic", lambda: clock[0])
    d.status["lantern_timeout"] = None
    d._set_lantern(True)
    clock[0] += 100_000
    assert d._stamp_local({})["lantern"] is True


def test_turning_the_lantern_off_clears_its_timer(tmp_path):
    d = make_daemon(tmp_path)
    d.device = FakePeak()
    asyncio.run(d.handle("start_lantern", {}))
    asyncio.run(d.handle("stop_lantern", {}))
    assert d.lantern is False and d._lantern_started is None and d.status["lantern"] is False


def test_brightness_is_read_back_from_the_peak():
    ble = PuffcoBLE()

    async def read_short(path, offset, size):
        assert path == "/u/app/ui/lbrt"
        return bytes([80, 90, 100, 110])

    ble.read_short = read_short
    assert asyncio.run(ble.get_led_brightness()) == {"base": 80, "mid": 90, "glass": 100, "logo": 110}


def test_panel_brightness_follows_what_the_peak_reported(tmp_path):
    d = make_daemon(tmp_path)
    d._take_brightness({"brightness": {"base": 40, "mid": 50, "glass": 60, "logo": 70}})
    assert d._stamp_local({})["brightness"] == {"base": 40, "mid": 50, "glass": 60, "logo": 70}
    d._take_brightness({"brightness": None})  # a failed read keeps the last value
    assert d.brightness["base"] == 40


def test_per_peak_cleaning_save_retires_the_old_single_copy(tmp_path):
    save_config({"clean_at_total": 863, "clean_notified": True, "last_serial": "MINE"})
    d = make_daemon(tmp_path)
    d._load_clean("MINE")
    assert d.clean_at_total == 863
    d._save_clean()
    cfg = load_config()
    assert cfg["clean_by_serial"]["MINE"]["at_total"] == 863
    assert cfg["clean_at_total"] is None and cfg["clean_notified"] is False
