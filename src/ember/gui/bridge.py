"""GTK-facing wrapper around the Ember daemon RPC client."""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Callable, Optional

from gi.repository import GLib

from ..paths import load_config
from ..rpc import EmberClient
from ..service import ensure_daemon

OkFn = Callable[[Any], None]
ErrFn = Callable[[str], None]


class Bridge:
    def __init__(self):
        self.status: dict[str, Any] = {"connected": False}
        self.on_status: Optional[Callable[[dict], None]] = None
        self.on_error: Optional[Callable[[str], None]] = None
        self.on_notify: Optional[Callable[[str, str], None]] = None
        self.on_ready: Optional[Callable[[], None]] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._client: Optional[EmberClient] = None
        self._thread = threading.Thread(target=self._run, name="ember-rpc", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.create_task(self._boot())
        self._loop.run_forever()

    async def _boot(self) -> None:
        try:
            await asyncio.to_thread(ensure_daemon)
            self._client = EmberClient()
            self._client.on_event = self._on_event
            await self._client.open()
            status = await self._client.request("status")
            if isinstance(status, dict):
                self.status = status
            GLib.idle_add(self._fire_ready)
        except Exception as exc:
            GLib.idle_add(self._fire_error, str(exc) or exc.__class__.__name__)

    def _fire_ready(self) -> bool:
        if self.on_ready:
            self.on_ready()
        if self.on_status:
            self.on_status(self.status)
        return False

    def _fire_error(self, message: str) -> bool:
        if self.on_error:
            self.on_error(message)
        return False

    def _on_event(self, event: str, data: Any) -> None:
        if event == "status" and isinstance(data, dict):
            self.status = data
            GLib.idle_add(self._emit_status, data)
        elif event == "notify" and isinstance(data, dict):
            cfg = load_config()
            title = str(data.get("title") or "Ember")
            body = str(data.get("body") or "")
            if "ready" in body.lower() and not cfg.get("notify_ready", True):
                return
            if "battery" in title.lower() and not cfg.get("notify_low_battery", True):
                return
            GLib.idle_add(self._emit_notify, title, body)

    def _emit_status(self, data: dict) -> bool:
        if self.on_status:
            self.on_status(data)
        return False

    def _emit_notify(self, title: str, body: str) -> bool:
        if self.on_notify:
            self.on_notify(title, body)
        return False

    def call(
        self,
        cmd: str,
        args: dict | None = None,
        on_ok: OkFn | None = None,
        on_err: ErrFn | None = None,
        timeout: float = 30.0,
    ) -> None:
        if not self._loop:
            if on_err:
                on_err("Ember is still starting")
            return
        asyncio.run_coroutine_threadsafe(
            self._request(cmd, args or {}, on_ok, on_err, timeout),
            self._loop,
        )

    async def _request(
        self,
        cmd: str,
        args: dict,
        on_ok: OkFn | None,
        on_err: ErrFn | None,
        timeout: float,
    ) -> None:
        try:
            if not self._client:
                raise RuntimeError("Not connected to emberd")
            result = await self._client.request(cmd, args, timeout=timeout)
            if isinstance(result, dict) and "connected" in result:
                self.status = result
                GLib.idle_add(self._emit_status, result)
            if on_ok:
                GLib.idle_add(lambda: on_ok(result) or False)
        except Exception as exc:
            message = str(exc) or exc.__class__.__name__
            if on_err:
                GLib.idle_add(lambda: on_err(message) or False)
            else:
                GLib.idle_add(self._fire_error, message)

    def close(self) -> None:
        if not self._loop:
            return

        async def _shutdown():
            if self._client:
                await self._client.close()
            self._loop.stop()

        asyncio.run_coroutine_threadsafe(_shutdown(), self._loop)
