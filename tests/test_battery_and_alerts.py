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


def test_capacity_is_coulombs_converted_to_mah_against_the_rated_size():
    fields = history.battery_capacity_fields(5216.2, 1800)
    assert fields == {"battery_capacity_mah": 1449, "battery_rated_mah": 1800, "battery_health_pct": 80}
    assert history.battery_capacity_fields(5216.2, None)["battery_rated_mah"] == 1700
    assert history.battery_capacity_fields(9000, 1700)["battery_health_pct"] == 100  # capped


def test_nonsense_capacity_and_sizes_are_ignored():
    assert history.battery_capacity_fields(3, 1700)["battery_capacity_mah"] is None
    assert history.battery_capacity_fields(None, 1700)["battery_health_pct"] is None
    assert history.battery_capacity_fields(5216.2, 12)["battery_rated_mah"] == 1700


class FakeBatteryPeak:
    is_connected = True
    max_charge = 100.0

    async def set_max_charge(self, percent):
        self.max_charge = percent

    async def get_max_charge(self):
        return self.max_charge


def test_battery_preservation_sets_80_and_back_to_100(tmp_path):
    d = daemon(tmp_path)
    d.device = FakeBatteryPeak()
    assert asyncio.run(d.handle("set_max_charge", {"preserve": True}))["max_charge"] == 80.0
    assert asyncio.run(d.handle("set_max_charge", {"preserve": False}))["max_charge"] == 100.0


def test_battery_size_cannot_be_changed_from_outside(tmp_path):
    d = daemon(tmp_path)
    try:
        asyncio.run(d.handle("set_battery_rated", {"mah": 900}))
    except Exception:
        pass
    assert load_config().get("battery_rated_mah") in (None, 1700)


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
