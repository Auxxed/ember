"""Is someone sitting at this computer?

Two machines running QuickPuff both want the same Peak, and the Peak only
takes one Bluetooth link at a time. Handoff needs to know which seat is in
use, and logind already tracks that: a locked or switched-away session is not
where the Peak should be.

If logind can't be reached the seat counts as occupied, so a machine that
can't tell keeps behaving the way it always has.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Callable, Optional

from dbus_fast import BusType
from dbus_fast.aio import MessageBus

log = logging.getLogger("quickpuff.presence")

LOGIND = "org.freedesktop.login1"
MANAGER_PATH = "/org/freedesktop/login1"
MANAGER_IFACE = "org.freedesktop.login1.Manager"
SESSION_IFACE = "org.freedesktop.login1.Session"
PROPS_IFACE = "org.freedesktop.DBus.Properties"


class SeatPresence:
    """Follows this session's Active and LockedHint.

    IdleHint is deliberately ignored: under Wayland compositors it is often
    never set, and resting an idle Peak is already battery saver's job.
    """

    def __init__(self, on_change: Optional[Callable[[bool], None]] = None):
        self._on_change = on_change
        self._bus = None
        self._props = None
        self.available = False
        # Until logind says otherwise, assume the user is right here.
        self.active = True

    async def start(self) -> bool:
        try:
            self._bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
            path = await self._session_path()
            introspect = await self._bus.introspect(LOGIND, path)
            obj = self._bus.get_proxy_object(LOGIND, path, introspect)
            self._props = obj.get_interface(PROPS_IFACE)
            self._props.on_properties_changed(self._changed)
            self.active = await self._read()
            self.available = True
            log.info("Seat presence via logind %s: %s", path, "active" if self.active else "away")
            return True
        except Exception as exc:
            log.info("Seat presence unavailable (%s); treating this seat as occupied", exc)
            await self.stop()
            self.available = False
            self.active = True
            return False

    async def _session_path(self) -> str:
        introspect = await self._bus.introspect(LOGIND, MANAGER_PATH)
        obj = self._bus.get_proxy_object(LOGIND, MANAGER_PATH, introspect)
        manager = obj.get_interface(MANAGER_IFACE)
        sid = (os.environ.get("XDG_SESSION_ID") or "").strip()
        if sid:
            try:
                return await manager.call_get_session(sid)
            except Exception:
                pass
        # A user service may not inherit XDG_SESSION_ID.
        return await manager.call_get_session_by_pid(os.getpid())

    async def _read(self) -> bool:
        active = await self._props.call_get(SESSION_IFACE, "Active")
        try:
            locked = await self._props.call_get(SESSION_IFACE, "LockedHint")
            locked_v = bool(locked.value)
        except Exception:
            # LockedHint needs a lock manager that reports it; absent is unlocked.
            locked_v = False
        return bool(active.value) and not locked_v

    def _changed(self, iface: str, changed: dict, invalidated: list) -> None:
        if iface != SESSION_IFACE:
            return
        if not ({"Active", "LockedHint"} & (set(changed) | set(invalidated))):
            return
        loop = asyncio.get_running_loop()
        loop.create_task(self._refresh())

    async def _refresh(self) -> None:
        try:
            now = await self._read()
        except Exception as exc:
            log.debug("Seat re-read failed: %s", exc)
            return
        if now == self.active:
            return
        self.active = now
        log.info("Seat is now %s", "active" if now else "away")
        if self._on_change:
            self._on_change(now)

    async def stop(self) -> None:
        if self._props:
            try:
                self._props.off_properties_changed(self._changed)
            except Exception:
                pass
            self._props = None
        if self._bus:
            try:
                self._bus.disconnect()
            except Exception:
                pass
            self._bus = None
