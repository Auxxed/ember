"""Battery saver: rest after a heat cycle, lantern off, preference persists."""

from __future__ import annotations

import asyncio
from pathlib import Path

from omapuffco.constants import OperatingState
from omapuffco.daemon import (
    CYCLE_STATES,
    HEAT_STATES,
    OmaPuffcoDaemon,
    cycle_just_ended,
)
from omapuffco.paths import load_config


PREHEAT = int(OperatingState.HEAT_CYCLE_PREHEAT)
READY = int(OperatingState.HEAT_CYCLE_ACTIVE)
FADE = int(OperatingState.HEAT_CYCLE_FADE)
IDLE = int(OperatingState.IDLE)
SLEEP = int(OperatingState.SLEEP)
SELECT = int(OperatingState.TEMP_SELECT)


class FakePeak:
    def __init__(self) -> None:
        self.is_connected = True
        self.disconnected = False
        self.lantern_stopped = False
        self.state = IDLE

    async def get_operating_state(self) -> int:
        return self.state

    async def disconnect(self) -> None:
        self.disconnected = True
        self.is_connected = False

    async def stop_lantern(self) -> None:
        self.lantern_stopped = True


def _daemon(tmp_path: Path) -> OmaPuffcoDaemon:
    return OmaPuffcoDaemon(sock=tmp_path / "omapuffco.sock")


class TestCycleJustEnded:
    def test_fade_to_idle_is_the_normal_end(self):
        assert cycle_just_ended(FADE, IDLE)

    def test_abort_during_preheat_counts(self):
        assert cycle_just_ended(PREHEAT, IDLE)

    def test_abort_while_ready_counts(self):
        assert cycle_just_ended(READY, IDLE)

    def test_idle_staying_idle_does_not(self):
        assert not cycle_just_ended(IDLE, IDLE)

    def test_waking_from_sleep_does_not(self):
        assert not cycle_just_ended(SLEEP, IDLE)

    def test_leaving_temp_select_does_not(self):
        assert not cycle_just_ended(SELECT, IDLE)

    def test_preheat_to_ready_does_not(self):
        assert not cycle_just_ended(PREHEAT, READY)

    def test_junk_states_are_not_an_end(self):
        assert not cycle_just_ended(None, IDLE)
        assert not cycle_just_ended(FADE, "idle")
        assert not cycle_just_ended(FADE, None)

    def test_cycle_states_cover_preheat_ready_and_fade(self):
        assert HEAT_STATES <= CYCLE_STATES
        assert FADE in CYCLE_STATES
        assert IDLE not in CYCLE_STATES


class TestSetBatterySaver:
    def test_persists_without_a_device(self, tmp_path):
        daemon = _daemon(tmp_path)

        result = asyncio.run(daemon.handle("set_battery_saver", {"enable": True}))

        assert result == {"battery_saver": True}
        assert daemon.battery_saver is True
        assert daemon.status["battery_saver"] is True
        assert load_config()["battery_saver"] is True

    def test_disable_cancels_a_pending_sleep(self, tmp_path):
        daemon = _daemon(tmp_path)
        daemon.battery_saver = True

        async def _run() -> None:
            async def _never() -> None:
                await asyncio.sleep(3600)

            task = asyncio.create_task(_never())
            daemon._saver_sleep_task = task
            await daemon.handle("set_battery_saver", {"enable": False})
            await asyncio.sleep(0)
            assert daemon.battery_saver is False
            assert daemon._saver_sleep_task is None
            assert task.cancelled()

        asyncio.run(_run())
        assert load_config()["battery_saver"] is False

    def test_reads_the_saved_flag_on_start(self, tmp_path):
        asyncio.run(_daemon(tmp_path).handle("set_battery_saver", {"enable": True}))
        fresh = _daemon(tmp_path)
        assert fresh.battery_saver is True
        assert fresh.status["battery_saver"] is True


class TestSaverSleep:
    def test_rests_when_idle_and_turns_lantern_off(self, tmp_path, monkeypatch):
        import omapuffco.daemon as daemon_mod

        monkeypatch.setattr(daemon_mod, "BATTERY_SAVER_SLEEP_S", 0)
        daemon = _daemon(tmp_path)
        daemon.battery_saver = True
        daemon.lantern = True
        peak = FakePeak()
        daemon.device = peak
        daemon.status["operating_state_id"] = IDLE

        asyncio.run(daemon._run_saver_sleep())

        assert peak.disconnected is True
        assert peak.lantern_stopped is True
        assert daemon.lantern is False

    def test_skips_sleep_if_the_peak_started_a_cycle_since_the_last_poll(self, tmp_path, monkeypatch):
        import omapuffco.daemon as daemon_mod

        monkeypatch.setattr(daemon_mod, "BATTERY_SAVER_SLEEP_S", 0)
        daemon = _daemon(tmp_path)
        daemon.battery_saver = True
        daemon.lantern = True
        peak = FakePeak()
        peak.state = PREHEAT
        daemon.device = peak
        daemon.status["operating_state_id"] = IDLE

        asyncio.run(daemon._run_saver_sleep())

        assert peak.disconnected is False
        assert peak.lantern_stopped is False
        assert daemon.status["operating_state_id"] == PREHEAT

    def test_skips_sleep_while_the_user_is_active(self, tmp_path, monkeypatch):
        import omapuffco.daemon as daemon_mod

        monkeypatch.setattr(daemon_mod, "BATTERY_SAVER_SLEEP_S", 30)
        daemon = _daemon(tmp_path)
        daemon.battery_saver = True
        peak = FakePeak()
        daemon.device = peak

        async def _run() -> None:
            async def _no_wait(_seconds) -> None:
                return None

            monkeypatch.setattr(daemon_mod.asyncio, "sleep", _no_wait)
            daemon._last_user_cmd = daemon_mod.time.monotonic()
            await daemon._run_saver_sleep()

        asyncio.run(_run())
        assert peak.disconnected is False

    def test_string_false_does_not_enable_the_saver(self, tmp_path):
        daemon = _daemon(tmp_path)
        asyncio.run(daemon.handle("set_battery_saver", {"enable": "false"}))
        assert daemon.battery_saver is False

    def test_start_heat_cancels_the_pending_sleep(self, tmp_path):
        daemon = _daemon(tmp_path)
        daemon.battery_saver = True
        peak = FakePeak()
        peak.heated = False

        async def _heat() -> None:
            peak.heated = True

        peak.start_heat_cycle = _heat  # type: ignore[method-assign]
        daemon.device = peak

        async def _start() -> None:
            async def _never() -> None:
                await asyncio.sleep(3600)

            daemon._saver_sleep_task = asyncio.create_task(_never())
            await daemon.handle("start_heat", {})
            await asyncio.sleep(0)
            assert daemon._saver_sleep_task is None
            assert peak.heated is True
            assert peak.disconnected is False

        asyncio.run(_start())
