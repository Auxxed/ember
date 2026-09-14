"""Working out whether someone is sitting at this computer.

Omarchy's lock never tells logind, so LockedHint stays "no" right through a
lock: the compositor has to be asked separately. Getting this wrong makes
handoff quietly do nothing, which is the failure worth pinning down.
"""

import asyncio

from quickpuff.presence import SeatPresence


def settle(p, *, logind_here=True, screen_locked=False):
    p._logind_here = logind_here
    p._screen_locked = screen_locked
    p._settle()


def test_an_unlocked_session_is_someone_sitting_there():
    p = SeatPresence()
    p.active = False
    settle(p, logind_here=True, screen_locked=False)
    assert p.active is True


def test_a_locked_screen_is_an_empty_seat_even_when_logind_says_active():
    """The case Omarchy actually produces: logind sees nothing, the
    compositor holds the lock."""
    p = SeatPresence()
    settle(p, logind_here=True, screen_locked=True)
    assert p.active is False


def test_a_switched_away_session_is_an_empty_seat():
    p = SeatPresence()
    settle(p, logind_here=False, screen_locked=False)
    assert p.active is False


def test_changes_are_announced_once():
    seen = []
    p = SeatPresence(on_change=seen.append)
    settle(p, screen_locked=True)
    settle(p, screen_locked=True)
    settle(p, screen_locked=False)
    assert seen == [False, True]


def test_an_undetermined_compositor_is_not_a_lock(monkeypatch):
    """Hyprland answers "undetermined" before a monitor has a workspace.
    Reading that as locked would drop the Peak for no reason."""
    p = SeatPresence()
    p._helper = "/does/not/matter"

    async def fake_exec(*a, **kw):
        class P:
            async def wait(self):
                return 2

        return P()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    assert asyncio.run(p._read_screen_lock()) is False


def test_a_locked_compositor_reads_as_locked(monkeypatch):
    p = SeatPresence()
    p._helper = "/does/not/matter"

    async def fake_exec(*a, **kw):
        class P:
            async def wait(self):
                return 0

        return P()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    assert asyncio.run(p._read_screen_lock()) is True


def test_a_broken_helper_keeps_the_last_answer(monkeypatch):
    """A helper that fails shouldn't flip the seat and start moving the Peak."""
    p = SeatPresence()
    p._helper = "/does/not/matter"
    p._screen_locked = True

    async def boom(*a, **kw):
        raise OSError("no such thing")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", boom)
    assert asyncio.run(p._read_screen_lock()) is True


def test_with_nothing_to_ask_the_seat_counts_as_occupied():
    p = SeatPresence()
    assert p.active is True
    assert p.available is False


def test_a_wedged_helper_is_not_left_running(monkeypatch):
    """wait_for gives up on the waiting, not on the process. Without a kill
    this leaves one behind every poll for the life of the daemon."""
    killed = []

    class Hung:
        def __init__(self):
            self.returncode = None

        async def wait(self):
            if killed:
                return -9
            await asyncio.sleep(30)

        def kill(self):
            killed.append(True)
            self.returncode = -9

    async def fake_exec(*a, **kw):
        return Hung()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr("quickpuff.presence.LOCK_PROBE_TIMEOUT_S", 0.01)
    p = SeatPresence()
    p._helper = "/does/not/matter"
    assert asyncio.run(p._read_screen_lock()) is False
    assert killed == [True], "a helper that never answers was left running"


def test_the_lock_watch_survives_an_unexpected_failure(monkeypatch):
    """If this loop dies the daemon keeps running and silently never hands the
    Peak over again — the worst kind of failure for this feature."""
    p = SeatPresence()
    p._helper = "/does/not/matter"
    calls = []

    async def flaky():
        calls.append(True)
        if len(calls) == 1:
            raise RuntimeError("boom")
        return True

    p._read_screen_lock = flaky
    monkeypatch.setattr("quickpuff.presence.LOCK_POLL_S", 0)

    async def run():
        task = asyncio.create_task(p._lock_loop())
        for _ in range(50):
            await asyncio.sleep(0)
            if len(calls) >= 2:
                break
        task.cancel()

    asyncio.run(run())
    assert len(calls) >= 2, "the loop stopped watching after one failure"
