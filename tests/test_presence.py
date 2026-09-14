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
