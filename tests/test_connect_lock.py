import asyncio

from quickpuff.daemon import QuickPuffDaemon


class FakePeak:
    is_connected = True
    address = "AA:BB:CC:11:22:33"
    device_mac = None


def daemon(tmp_path):
    return QuickPuffDaemon(sock=tmp_path / "quickpuff.sock")


def test_connects_never_overlap(tmp_path):
    d = daemon(tmp_path)
    active = 0
    peak = 0

    async def slow_connect(name, mac):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return {}

    d._connect_unlocked = slow_connect

    async def run():
        await asyncio.gather(d._connect(None, None), d._connect(None, "AA:BB:CC:DD:EE:FF"))

    asyncio.run(run())
    assert peak == 1


def test_already_connected_to_the_same_peak_is_a_no_op(tmp_path):
    d = daemon(tmp_path)
    d.device = FakePeak()
    assert asyncio.run(d._connect_unlocked(None, "aa:bb:cc:11:22:33")) is d.status
    assert asyncio.run(d._connect_unlocked(None, None)) is d.status


def test_picking_a_different_peak_disconnects_the_current_one_first(tmp_path):
    d = daemon(tmp_path)
    d.device = FakePeak()

    class Switched(Exception):
        pass

    async def disconnect(forget=False):
        assert forget is False  # a switch, not a user Disconnect
        raise Switched

    d._disconnect = disconnect
    try:
        asyncio.run(d._connect_unlocked(None, "AA:BB:CC:DD:EE:FF"))
    except Switched:
        pass
    else:
        raise AssertionError("did not disconnect before switching")
