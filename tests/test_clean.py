"""Chamber-clean reminder: remaining dabs, 10-step interval, persist."""

from __future__ import annotations

import asyncio
from pathlib import Path

from omapuffco.daemon import (
    CLEAN_EVERY_MAX,
    CLEAN_EVERY_MIN,
    DEFAULT_CLEAN_EVERY,
    OmaPuffcoDaemon,
    clean_remaining,
    snap_clean_every,
)
from omapuffco.paths import load_config


def _daemon(tmp_path: Path) -> OmaPuffcoDaemon:
    return OmaPuffcoDaemon(sock=tmp_path / "omapuffco.sock")


class TestSnapCleanEvery:
    def test_default_on_junk(self):
        assert snap_clean_every(None) == DEFAULT_CLEAN_EVERY
        assert snap_clean_every("nope") == DEFAULT_CLEAN_EVERY

    def test_rounds_to_tens(self):
        assert snap_clean_every(14) == 10
        assert snap_clean_every(15) == 20
        assert snap_clean_every(30) == 30

    def test_clamps_to_the_allowed_range(self):
        assert snap_clean_every(0) == CLEAN_EVERY_MIN
        assert snap_clean_every(7) == CLEAN_EVERY_MIN
        assert snap_clean_every(999) == CLEAN_EVERY_MAX


class TestCleanRemaining:
    def test_full_interval_until_there_is_a_baseline(self):
        assert clean_remaining(862, None, 30) == 30

    def test_counts_down_from_the_mark(self):
        assert clean_remaining(850, 840, 30) == 20

    def test_floors_at_zero_when_overdue(self):
        assert clean_remaining(900, 840, 30) == 0

    def test_used_cannot_go_negative(self):
        assert clean_remaining(10, 40, 30) == 30


class TestCleanCommands:
    def test_set_every_persists_and_snaps(self, tmp_path):
        daemon = _daemon(tmp_path)
        result = asyncio.run(daemon.handle("set_clean_every", {"dabs": 44}))
        assert result["clean_every"] == 40
        assert load_config()["clean_every"] == 40
        assert daemon.status["clean_every"] == 40

    def test_mark_cleaned_resets_the_countdown(self, tmp_path):
        daemon = _daemon(tmp_path)
        daemon.status["total_dabs"] = 800
        asyncio.run(daemon.handle("set_clean_every", {"dabs": 20}))
        daemon.clean_at_total = 780
        daemon.clean_notified = True
        result = asyncio.run(daemon.handle("mark_cleaned", {}))
        assert result["clean_remaining"] == 20
        assert result["clean_due"] is False
        assert daemon.clean_at_total == 800
        assert daemon.clean_notified is False
        assert load_config()["clean_at_total"] == 800

    def test_does_not_come_due_on_first_connect_lifetime_count(self, tmp_path):
        daemon = _daemon(tmp_path)
        fields = asyncio.run(daemon._refresh_clean(862))
        assert daemon.clean_at_total == 862
        assert fields["clean_remaining"] == DEFAULT_CLEAN_EVERY
        assert fields["clean_due"] is False

    def test_notifies_once_when_the_countdown_hits_zero(self, tmp_path, monkeypatch):
        daemon = _daemon(tmp_path)
        daemon.clean_every = 10
        daemon.clean_at_total = 100
        daemon.clean_notified = False
        sent = []

        async def fake_notify() -> None:
            sent.append(True)

        monkeypatch.setattr(daemon, "_notify_clean", fake_notify)
        asyncio.run(daemon._refresh_clean(110, notify=True))
        asyncio.run(daemon._refresh_clean(111, notify=True))
        assert sent == [True]
        assert daemon.clean_notified is True
        assert daemon.status["clean_due"] is True
