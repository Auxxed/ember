import asyncio

import pytest

import quickpuff.daemon as daemon_mod
from quickpuff.constants import OperatingState
from quickpuff.daemon import QuickPuffDaemon
from quickpuff.paths import load_config

IDLE = int(OperatingState.IDLE)
PREHEAT = int(OperatingState.HEAT_CYCLE_PREHEAT)
ACTIVE = int(OperatingState.HEAT_CYCLE_ACTIVE)
FADE = int(OperatingState.HEAT_CYCLE_FADE)
SLEEP = int(OperatingState.SLEEP)


@pytest.fixture(autouse=True)
def no_reminder_delay(monkeypatch):
    monkeypatch.setattr(daemon_mod, "QTIP_REMINDER_DELAY_S", 0)


def daemon(tmp_path):
    d = QuickPuffDaemon(sock=tmp_path / "quickpuff.sock")
    d.sent = []
    d._desktop_notify = lambda title, body, urgency="normal": d.sent.append(title)
    return d


def run_states(d, states):
    async def go():
        for prev, new in zip(states, states[1:]):
            await d._track_session_end(prev, new)
        await asyncio.gather(*d._tasks)

    asyncio.run(go())


def test_reminds_once_after_a_dab(tmp_path):
    d = daemon(tmp_path)
    run_states(d, [IDLE, PREHEAT, ACTIVE, ACTIVE, FADE, IDLE, IDLE])
    assert d.sent == ["Q-tip time"]


def test_reminds_even_if_the_peak_goes_straight_to_sleep(tmp_path):
    d = daemon(tmp_path)
    run_states(d, [PREHEAT, ACTIVE, SLEEP])
    assert d.sent == ["Q-tip time"]


def test_aborted_preheat_is_not_a_dab(tmp_path):
    d = daemon(tmp_path)
    run_states(d, [IDLE, PREHEAT, IDLE])
    assert d.sent == []


def test_reminder_can_be_switched_off_and_stays_off(tmp_path):
    d = daemon(tmp_path)
    asyncio.run(d._set_qtip_reminder(False))
    assert load_config()["qtip_reminder"] is False
    run_states(d, [PREHEAT, ACTIVE, FADE, IDLE])
    assert d.sent == []
    assert daemon(tmp_path).qtip_reminder is False


def test_reminder_waits_after_the_session_ends(tmp_path, monkeypatch):
    monkeypatch.setattr(daemon_mod, "QTIP_REMINDER_DELAY_S", 12.0)
    d = daemon(tmp_path)

    async def go():
        await d._track_session_end(PREHEAT, ACTIVE)
        await d._track_session_end(ACTIVE, IDLE)
        await asyncio.sleep(0.05)
        assert len(d._tasks) == 1
        assert d.sent == []
        for task in d._tasks:
            task.cancel()

    asyncio.run(go())


def test_on_by_default(tmp_path):
    assert daemon(tmp_path).status["qtip_reminder"] is True
