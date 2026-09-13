"""The command queue can't freeze the panel: status skips it, profile edits
re-read only the profiles once taps settle, and clients that give up are quiet."""

import asyncio
import json
import logging
import time

from omapuffco import daemon as daemon_module
from omapuffco.daemon import OmaPuffcoDaemon
from omapuffco.rpc import rpc


class FakePeak:
    is_connected = True
    address = "AA:BB:CC:11:22:33"
    device_mac = None

    def __init__(self):
        self.profile_reads = 0
        self.on_read = None

    async def set_profile_temp_c(self, index, celsius):
        pass

    async def set_profile_solid_color(self, index, hex_color):
        pass

    async def set_device_name(self, name):
        self.name = name

    async def snapshot(self, **_):
        raise AssertionError("an edit must not re-read the whole Peak")

    async def snapshot_profile(self, index):
        self.profile_reads += 1
        if self.on_read:
            self.on_read()
        return {"index": index, "name": f"P{index}", "temp_f": 490 if index == 1 else 500, "color": "#ff00aa"}


def make_daemon(tmp_path, peak=None):
    d = OmaPuffcoDaemon(sock=tmp_path / "omapuffco.sock")
    d.device = peak
    d.status["current_profile"] = 0
    d.status["profiles"] = [{"index": i, "temp_f": 500, "temp_c": 260.0, "color": "#000000"} for i in range(4)]
    return d


def test_status_does_not_wait_behind_a_slow_command(tmp_path):
    d = make_daemon(tmp_path)

    async def run():
        await d.start()
        try:
            async with d._cmd_lock:  # a long Bluetooth command in progress
                reply = await asyncio.wait_for(rpc("status", {}, path=d.socket_path), 2)
                assert reply["connected"] is False
                assert (await asyncio.wait_for(rpc("ping", {}, path=d.socket_path), 2))["version"]
        finally:
            await d.close()

    asyncio.run(run())


def test_a_client_that_gave_up_is_not_logged_as_a_failed_command(tmp_path, caplog):
    d = make_daemon(tmp_path)

    class GoneWriter:
        def write(self, data):
            raise BrokenPipeError(32, "Broken pipe")

        async def drain(self):
            pass

        def close(self):
            pass

        async def wait_closed(self):
            pass

    async def run():
        reader = asyncio.StreamReader()
        reader.feed_data((json.dumps({"id": 1, "cmd": "set_daily_limit", "args": {"limit": 2}}) + "\n").encode())
        reader.feed_eof()
        with caplog.at_level(logging.ERROR, logger="omapuffco.daemon"):
            await d._client(reader, GoneWriter())

    asyncio.run(run())
    assert d.daily_limit == 2  # the command itself ran
    assert "failed" not in caplog.text


def test_a_burst_of_profile_edits_shows_at_once_and_rereads_the_profiles_once(tmp_path, monkeypatch):
    monkeypatch.setattr(daemon_module, "PROFILE_REFRESH_SETTLE_S", 0.05)
    peak = FakePeak()
    d = make_daemon(tmp_path, peak)

    async def run():
        for fahrenheit in (480, 485, 490):
            await d.handle("set_profile_temp", {"index": 1, "fahrenheit": fahrenheit})
        assert d.status["profiles"][1]["temp_f"] == 490  # shown straight away
        await d.handle("set_profile_color", {"hex": "FF00AA"})  # the selected profile
        assert d.status["profiles"][0]["color"] == "#ff00aa"
        assert peak.profile_reads == 0  # nothing re-read while the taps land
        await d._profile_refresh_task

    asyncio.run(run())
    assert peak.profile_reads == 4  # one pass over the four profiles
    assert d.status["profiles"][0]["name"] == "P0"


def test_an_edit_during_the_reread_gets_one_more_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(daemon_module, "PROFILE_REFRESH_SETTLE_S", 0.02)
    peak = FakePeak()
    d = make_daemon(tmp_path, peak)

    def edit_once():
        if peak.profile_reads == 2:
            d._profiles_dirty_at = time.monotonic()

    peak.on_read = edit_once

    async def run():
        await d.handle("set_profile_temp", {"index": 1, "fahrenheit": 490})
        await d._profile_refresh_task

    asyncio.run(run())
    assert peak.profile_reads == 8


def test_renaming_the_peak_skips_the_full_reread(tmp_path):
    peak = FakePeak()
    d = make_daemon(tmp_path, peak)
    name = "A very long Peak name that goes past the limit"
    result = asyncio.run(d.handle("set_device_name", {"name": name}))
    assert peak.name == name
    assert result["device_name"] == name.encode()[:32].decode()
