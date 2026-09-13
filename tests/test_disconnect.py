import asyncio

from omapuffco.daemon import OmaPuffcoDaemon


def test_disconnect_cancels_a_pending_reconnect_and_stops_retrying(tmp_path):
    daemon = OmaPuffcoDaemon(sock=tmp_path / "omapuffco.sock")

    async def run() -> None:
        async def retry_forever() -> None:
            await asyncio.sleep(3600)

        daemon._want_connected = True
        task = asyncio.create_task(retry_forever())
        daemon._reconnect_task = task
        status = await daemon.handle("disconnect", {})
        await asyncio.sleep(0)
        assert task.cancelled()
        assert daemon._reconnect_task is None
        assert daemon._want_connected is False
        assert status["connected"] is False

    asyncio.run(run())
