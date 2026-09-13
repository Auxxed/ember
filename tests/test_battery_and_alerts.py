import asyncio

from omapuffco import history
from omapuffco.cli import format_eta, low_battery_heat_warning
from omapuffco.daemon import OmaPuffcoDaemon
from omapuffco.paths import load_config, save_config


def daemon(tmp_path):
    d = OmaPuffcoDaemon(sock=tmp_path / "omapuffco.sock")
    d.sent = []
    d._desktop_notify = lambda title, body, urgency="normal": d.sent.append(title)
    return d


def test_battery_health_is_relative_to_the_best_reading():
    first = history.record_battery_capacity(2600)
    assert first["battery_health_pct"] == 100
    later = history.record_battery_capacity(2340)
    assert later["battery_best_mah"] == 2600
    assert later["battery_health_pct"] == 90
    assert history.record_battery_capacity(None)["battery_health_pct"] is None


def test_nonsense_capacity_readings_are_ignored():
    assert history.record_battery_capacity(3)["battery_capacity_mah"] is None
    assert history.record_battery_capacity(3)["battery_best_mah"] is None


def test_low_battery_notifies_once_and_rearms_after_charging(tmp_path):
    d = daemon(tmp_path)
    d.status.update({"device_name": "Delly", "charge_source": "Unplugged"})

    async def run():
        for level in (16, 15, 14, 12):
            d.status["battery"] = level
            await d._check_low_battery()
        assert d.sent == ["Delly battery low"]
        d.status["battery"] = 21
        await d._check_low_battery()
        d.status["battery"] = 15
        await d._check_low_battery()
        assert len(d.sent) == 2

    asyncio.run(run())


def test_plugged_in_peak_does_not_warn(tmp_path):
    d = daemon(tmp_path)
    d.status.update({"battery": 8, "charge_source": "USB"})
    asyncio.run(d._check_low_battery())
    assert d.sent == []


def test_low_battery_notification_can_be_turned_off(tmp_path):
    save_config({**load_config(), "notify_low_battery": False})
    d = daemon(tmp_path)
    d.status.update({"battery": 10, "charge_source": "Unplugged"})
    asyncio.run(d._check_low_battery())
    assert d.sent == []


def test_restart_reconnects_to_the_last_peak(tmp_path):
    save_config({**load_config(), "device_mac": "F0:AD:4E:38:6E:3C", "device_name": "Delly"})
    d = daemon(tmp_path)

    async def run():
        assert d._resume_last_device()
        assert d._want_connected
        d._reconnect_task.cancel()

    asyncio.run(run())


def test_disconnect_keeps_it_disconnected_after_a_restart(tmp_path):
    save_config({**load_config(), "device_mac": "F0:AD:4E:38:6E:3C"})
    d = daemon(tmp_path)
    asyncio.run(d._disconnect(forget=True))
    assert load_config()["auto_connect"] is False
    assert not daemon(tmp_path)._resume_last_device()


def test_heat_warning_only_when_low_and_unplugged():
    assert low_battery_heat_warning({"connected": True, "battery": 8, "charge_source": "Unplugged"})
    assert low_battery_heat_warning({"connected": True, "battery": 8, "charge_source": "USB"}) is None
    assert low_battery_heat_warning({"connected": True, "battery": 40, "charge_source": "Unplugged"}) is None
    assert low_battery_heat_warning({"connected": False, "battery": 0}) is None


def test_eta_reads_naturally():
    assert format_eta(2400) == "40 min"
    assert format_eta(4800) == "1 h 20 min"
    assert format_eta(20) == "1 min"
