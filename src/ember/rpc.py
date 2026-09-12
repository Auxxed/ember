"""Newline-delimited JSON client for the Ember daemon."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Callable, Optional

from .paths import socket_path

EventHandler = Callable[[str, Any], None]


class DaemonNotRunning(RuntimeError):
    pass


async def rpc(
    cmd: str,
    args: dict | None = None,
    timeout: float = 30.0,
    path: Path | None = None,
) -> Any:
    sock = path or socket_path()
    if not sock.exists():
        raise DaemonNotRunning(f"Ember daemon is not running ({sock})")
    reader, writer = await asyncio.open_unix_connection(str(sock))
    try:
        writer.write((json.dumps({"id": 1, "cmd": cmd, "args": args or {}}) + "\n").encode())
        await writer.drain()
        while True:
            line = await asyncio.wait_for(reader.readline(), timeout=timeout)
            if not line:
                raise RuntimeError("Daemon closed the connection")
            msg = json.loads(line.decode())
            if msg.get("event"):
                continue
            if not msg.get("ok"):
                raise RuntimeError(msg.get("error") or "command failed")
            return msg.get("result")
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


class EmberClient:
    """Long-lived socket client. Events fire on the asyncio loop."""

    def __init__(self, path: Path | None = None):
        self.path = path or socket_path()
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._pending: dict[int, asyncio.Future] = {}
        self._next_id = 1
        self._lock = asyncio.Lock()
        self._pump: Optional[asyncio.Task] = None
        self.on_event: Optional[EventHandler] = None

    @property
    def connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    async def open(self) -> None:
        if self.connected:
            return
        if not self.path.exists():
            raise DaemonNotRunning(f"Ember daemon is not running ({self.path})")
        self._reader, self._writer = await asyncio.open_unix_connection(str(self.path))
        self._pump = asyncio.create_task(self._read_loop())

    async def close(self) -> None:
        if self._pump:
            self._pump.cancel()
            self._pump = None
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
        self._writer = None
        self._reader = None
        for fut in self._pending.values():
            if not fut.done():
                fut.cancel()
        self._pending.clear()

    async def request(self, cmd: str, args: dict | None = None, timeout: float = 30.0) -> Any:
        await self.open()
        async with self._lock:
            req_id = self._next_id
            self._next_id += 1
            loop = asyncio.get_running_loop()
            fut: asyncio.Future = loop.create_future()
            self._pending[req_id] = fut
            assert self._writer is not None
            self._writer.write(
                (json.dumps({"id": req_id, "cmd": cmd, "args": args or {}}) + "\n").encode()
            )
            await self._writer.drain()
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except Exception:
            self._pending.pop(req_id, None)
            raise

    async def _read_loop(self) -> None:
        assert self._reader is not None
        try:
            while True:
                line = await self._reader.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line.decode())
                except json.JSONDecodeError:
                    continue
                event = msg.get("event")
                if event:
                    if self.on_event:
                        try:
                            self.on_event(str(event), msg.get("data"))
                        except Exception:
                            pass
                    continue
                req_id = msg.get("id")
                fut = self._pending.pop(req_id, None)
                if not fut or fut.done():
                    continue
                if msg.get("ok"):
                    fut.set_result(msg.get("result"))
                else:
                    fut.set_exception(RuntimeError(msg.get("error") or "command failed"))
        finally:
            for fut in list(self._pending.values()):
                if not fut.done():
                    fut.set_exception(RuntimeError("Daemon disconnected"))
            self._pending.clear()
