"""Is someone sitting at this computer?

Two machines running QuickPuff both want the same Peak, and the Peak only
takes one Bluetooth link at a time. Handoff needs to know which seat is in
use, and there are two ways to ask.

logind knows when a session is switched away, and reports LockedHint when a
lock manager bothers to set it. Omarchy's lock doesn't: it drives the
compositor's own ext-session-lock and never tells logind, so LockedHint stays
"no" through a lock. Hyprland has no lock property either, but an active lock
is one of the reasons a monitor can't go solitary, which is what
omarchy-hyprland-session-locked reads. So the compositor is polled as well,
when that helper is around.

If neither can be reached the seat counts as occupied, so a machine that
can't tell keeps behaving the way it always has.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from typing import Callable, Optional

from dbus_fast import BusType
from dbus_fast.aio import MessageBus

log = logging.getLogger("quickpuff.presence")

LOGIND = "org.freedesktop.login1"
MANAGER_PATH = "/org/freedesktop/login1"
MANAGER_IFACE = "org.freedesktop.login1.Manager"
SESSION_IFACE = "org.freedesktop.login1.Session"
PROPS_IFACE = "org.freedesktop.DBus.Properties"

# Reads the compositor, not the Peak, so this is cheap — a few milliseconds.
# Handing the Peak over a few seconds late costs nothing.
LOCK_POLL_S = 15.0
LOCK_HELPER = "omarchy-hyprland-session-locked"


class SeatPresence:
    """Follows this session's logind Active and LockedHint, and the
    compositor's own lock when Omarchy's helper is installed.

    IdleHint is deliberately ignored: under Wayland compositors it is often
    never set, and resting an idle Peak is already battery saver's job.
    """

    def __init__(self, on_change: Optional[Callable[[bool], None]] = None):
        self._on_change = on_change
        self._bus = None
        self._props = None
        self._lock_task: Optional[asyncio.Task] = None
        self._helper = shutil.which(LOCK_HELPER)
        self.available = False
        # Until something says otherwise, assume the user is right here.
        self.active = True
        self._logind_here = True
        self._screen_locked = False

    async def start(self) -> bool:
        logind = await self._start_logind()
        if self._helper:
            self._screen_locked = await self._read_screen_lock()
            self._lock_task = asyncio.create_task(self._lock_loop())
            log.info("Watching the compositor's lock via %s", self._helper)
        elif not logind:
            log.info("No way to tell whether this seat is in use; treating it as occupied")
        self.available = logind or bool(self._helper)
        self.active = self._logind_here and not self._screen_locked
        if self.available:
            log.info("Seat is %s", "active" if self.active else "away")
        return self.available

    async def _start_logind(self) -> bool:
        try:
            self._bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
            path = await self._session_path()
            introspect = await self._bus.introspect(LOGIND, path)
            obj = self._bus.get_proxy_object(LOGIND, path, introspect)
            self._props = obj.get_interface(PROPS_IFACE)
            self._props.on_properties_changed(self._changed)
            self._logind_here = await self._read_logind()
            log.info("Seat presence via logind %s", path)
            return True
        except Exception as exc:
            log.info("logind unavailable (%s)", exc)
            await self._stop_logind()
            self._logind_here = True
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

    async def _read_logind(self) -> bool:
        active = await self._props.call_get(SESSION_IFACE, "Active")
        try:
            locked = await self._props.call_get(SESSION_IFACE, "LockedHint")
            locked_v = bool(locked.value)
        except Exception:
            # Needs a lock manager that reports it; absent is unlocked.
            locked_v = False
        return bool(active.value) and not locked_v

    async def _read_screen_lock(self) -> bool:
        """0 locked, 1 unlocked, 2 undetermined — and undetermined means the
        compositor was never asked, so it is not a lock."""
        try:
            proc = await asyncio.create_subprocess_exec(
                self._helper,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            code = await asyncio.wait_for(proc.wait(), timeout=5.0)
        except Exception as exc:
            log.debug("lock helper failed: %s", exc)
            return self._screen_locked
        return code == 0

    async def _lock_loop(self) -> None:
        while True:
            await asyncio.sleep(LOCK_POLL_S)
            locked = await self._read_screen_lock()
            if locked != self._screen_locked:
                self._screen_locked = locked
                self._settle()

    def _changed(self, iface: str, changed: dict, invalidated: list) -> None:
        if iface != SESSION_IFACE:
            return
        if not ({"Active", "LockedHint"} & (set(changed) | set(invalidated))):
            return
        asyncio.get_running_loop().create_task(self._refresh_logind())

    async def _refresh_logind(self) -> None:
        try:
            here = await self._read_logind()
        except Exception as exc:
            log.debug("Seat re-read failed: %s", exc)
            return
        if here != self._logind_here:
            self._logind_here = here
            self._settle()

    def _settle(self) -> None:
        now = self._logind_here and not self._screen_locked
        if now == self.active:
            return
        self.active = now
        log.info(
            "Seat is now %s%s",
            "active" if now else "away",
            "" if now else (" (screen locked)" if self._screen_locked else " (session switched away)"),
        )
        if self._on_change:
            self._on_change(now)

    async def _stop_logind(self) -> None:
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

    async def stop(self) -> None:
        if self._lock_task:
            self._lock_task.cancel()
            self._lock_task = None
        await self._stop_logind()
