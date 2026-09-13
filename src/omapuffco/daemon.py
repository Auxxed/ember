"""Session daemon: owns the BLE link and serves a Unix-socket JSON API."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import subprocess
import time
import traceback
from pathlib import Path
from typing import Any, Optional

from . import audit, faults, history
from .ble import LoraxError, PuffcoBLE
from .constants import PROFILE_COUNT, OperatingState
from .paths import load_config, log_path, save_config, socket_path
from .product_info import is_proxy
from .utils import PuffcoUtils
from .vapor import snap as snap_vapor, value_for as vapor_value

log = logging.getLogger("omapuffco.daemon")

HEAT_STATES = {
    int(OperatingState.HEAT_CYCLE_PREHEAT),
    int(OperatingState.HEAT_CYCLE_ACTIVE),
}
CYCLE_STATES = HEAT_STATES | {int(OperatingState.HEAT_CYCLE_FADE)}
# After a cycle returns to idle, wait so a second dab isn't cut off.
BATTERY_SAVER_SLEEP_S = 30.0


def _as_bool(value: Any) -> bool:
    # A hand-edited config or raw RPC can carry "false", which bool() calls true.
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "on", "yes"}
    return bool(value)


def cycle_just_ended(prev_state: Any, new_state: Any) -> bool:
    """True when a heat cycle (preheat / ready / cool) lands back on idle."""
    try:
        prev = int(prev_state)
        new = int(new_state)
    except (TypeError, ValueError):
        return False
    return prev in CYCLE_STATES and new == int(OperatingState.IDLE)


# Peak Pro's own firmware/app range. Enforced here so no client (CLI or a
# raw RPC call) can push the heater past what the hardware is rated for —
# this is the one chokepoint every profile write passes through.
MIN_TEMP_F, MAX_TEMP_F = 400.0, 620.0
MIN_TIME_S, MAX_TIME_S = 5.0, 180.0
# Connect Boost Mode: extra heat and extra seconds on a double-click.
MIN_BOOST_TEMP_F, MAX_BOOST_TEMP_F = 0.0, 36.0
MIN_BOOST_TIME_S, MAX_BOOST_TIME_S = 0.0, 60.0
# Lantern auto-off. Firmware default on this Peak is 7200s (2h).
MIN_LANTERN_S, MAX_LANTERN_S = 60.0, 28800.0
# Chamber-clean reminder. Steps of 10, default one charge (~30 dabs).
CLEAN_EVERY_MIN, CLEAN_EVERY_MAX, CLEAN_EVERY_STEP = 10, 100, 10
DEFAULT_CLEAN_EVERY = 30


def snap_clean_every(value: Any) -> int:
    try:
        n = int(round(float(value)))
    except (TypeError, ValueError):
        n = DEFAULT_CLEAN_EVERY
    n = int(round(n / CLEAN_EVERY_STEP) * CLEAN_EVERY_STEP)
    return max(CLEAN_EVERY_MIN, min(CLEAN_EVERY_MAX, n))


def clean_remaining(total: Any, last: Any, every: int) -> int:
    """Dabs left until the reminder. Full interval until a baseline exists."""
    try:
        every_n = int(every)
    except (TypeError, ValueError):
        every_n = DEFAULT_CLEAN_EVERY
    try:
        used = max(0, int(total) - int(last))
    except (TypeError, ValueError):
        return every_n
    return max(0, every_n - used)


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _validate_index(args: dict) -> int:
    index = int(args["index"])
    if not 0 <= index < PROFILE_COUNT:
        raise ValueError(f"Profile index must be 0-{PROFILE_COUNT - 1}")
    return index


class OmaPuffcoDaemon:
    def __init__(self, sock: Path, debug: bool = False):
        self.socket_path = sock
        self.debug = debug
        self.device: Optional[PuffcoBLE] = None
        self.status: dict[str, Any] = self._empty_status()
        self.clients: set[asyncio.StreamWriter] = set()
        self._cmd_lock = asyncio.Lock()
        self._sync_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()
        self._fault_lock = asyncio.Lock()
        self.preheat_scale = history.preheat_scale()
        self._preheat_backfilled = False
        self._fault_cache: dict[str, Any] = {}
        self._poll_task: Optional[asyncio.Task] = None
        self._reconnect_task: Optional[asyncio.Task] = None
        self._auto_reconnect = True
        self._want_connected = False
        self._connect_name: Optional[str] = None
        self._connect_mac: Optional[str] = None
        self.lantern = False
        self.brightness = {"base": 80, "mid": 80, "glass": 80, "logo": 80}
        self.poll_interval = 1.5
        self.battery_saver = _as_bool(load_config().get("battery_saver"))
        self._last_user_cmd = float("-inf")
        self._saver_sleep_task: Optional[asyncio.Task] = None
        cfg = load_config()
        self.clean_every = snap_clean_every(cfg.get("clean_every"))
        self.clean_at_total = cfg.get("clean_at_total")
        self.clean_notified = _as_bool(cfg.get("clean_notified"))
        self._server: Optional[asyncio.AbstractServer] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self.status["battery_saver"] = self.battery_saver
        self.status.update(self._clean_fields())

    @staticmethod
    def _empty_status() -> dict[str, Any]:
        return {
            "connected": False,
            "device_name": "",
            "device_mac": "",
            "product": {},
            "serial": "",
            "firmware": "",
            "bootloader": "",
            "uptime_seconds": 0,
            "uptime": "",
            "battery": 0,
            "charge_state": "",
            "charge_state_id": -1,
            "charge_source": "",
            "charge_source_id": -1,
            "chamber": "",
            "chamber_id": -1,
            "operating_state": "Disconnected",
            "operating_state_id": -1,
            "heater_temp_c": None,
            "heater_temp_f": None,
            "state_elapsed_s": None,
            "state_total_s": None,
            "stealth": False,
            "lantern": False,
            "lantern_timeout": None,
            "battery_saver": False,
            "clean_every": DEFAULT_CLEAN_EVERY,
            "clean_remaining": DEFAULT_CLEAN_EVERY,
            "clean_due": False,
            "birthday": None,
            "birthday_label": "",
            "dabs_remaining": 0,
            "dabs_per_day": 0,
            "total_dabs": 0,
            "current_profile": 0,
            "profiles": [],
            "brightness": {"base": 80, "mid": 80, "glass": 80, "logo": 80},
            "telemetry": history.get_stats(),
        }

    def _cycle_meta(self) -> dict[str, Any]:
        """Temp / time / color of the profile that just hit ready."""
        try:
            index = int(self.status.get("current_profile") or 0)
        except (TypeError, ValueError):
            index = 0
        meta: dict[str, Any] = {}
        for profile in self.status.get("profiles") or []:
            try:
                if int(profile.get("index")) != index:
                    continue
            except (TypeError, ValueError):
                continue
            if profile.get("temp_f") is not None:
                meta["temp_f"] = profile["temp_f"]
            if profile.get("time") is not None:
                meta["time_s"] = profile["time"]
            color = profile.get("color")
            if isinstance(color, str) and color.startswith("#"):
                meta["color"] = color
            break
        return meta

    def _stamp_local(self, snap: dict[str, Any]) -> dict[str, Any]:
        snap["lantern"] = self.lantern
        snap["brightness"] = dict(self.brightness)
        snap["battery_saver"] = self.battery_saver
        snap.update(self._clean_fields(snap.get("total_dabs", self.status.get("total_dabs"))))
        if (
            snap.get("operating_state_id") == int(OperatingState.HEAT_CYCLE_PREHEAT)
            and snap.get("state_total_s")
            and self.preheat_scale
        ):
            # The Peak reports its own preheat estimate, which runs short.
            snap["state_total_s"] = float(snap["state_total_s"]) * self.preheat_scale
        return snap

    def _apply_snapshot(self, snap: dict[str, Any]) -> None:
        """Merge a full device snapshot into status and refresh telemetry."""
        self._stamp_local(snap)
        total = snap.get("total_dabs")
        history.record_total(total)
        if total is None:
            snap["total_dabs"] = self.status.get("total_dabs") or 0
        self._baseline_clean(snap.get("total_dabs"))
        self.status.update(snap)
        self.status["telemetry"] = history.get_stats()
        self.status.update(self._clean_fields())

    def _on_ble_drop(self) -> None:
        log.warning("BLE link dropped")
        self.status["connected"] = False
        self.status["operating_state"] = "Disconnected"
        self.status["operating_state_id"] = -1
        loop = self._loop
        if not loop:
            return

        def _after_drop():
            asyncio.create_task(self._broadcast_event("status", self.status))
            if self._want_connected and self._auto_reconnect:
                self._schedule_reconnect()

        loop.call_soon_threadsafe(_after_drop)

    def _schedule_reconnect(self) -> None:
        if self._reconnect_task and not self._reconnect_task.done():
            return

        async def _retry():
            delay = 2.0
            while self._want_connected and not (self.device and self.device.is_connected):
                log.info("Reconnect in %.1fs", delay)
                await asyncio.sleep(delay)
                try:
                    await self._connect(self._connect_name, self._connect_mac)
                    return
                except Exception as exc:
                    log.warning("Reconnect failed: %s", exc)
                    delay = min(delay * 1.6, 20.0)

        self._reconnect_task = asyncio.create_task(_retry())

    async def _broadcast(self, payload: dict) -> None:
        blob = (json.dumps(payload, default=str) + "\n").encode("utf-8")
        dead = []
        for writer in list(self.clients):
            try:
                writer.write(blob)
                await writer.drain()
            except Exception:
                dead.append(writer)
        for writer in dead:
            self.clients.discard(writer)
            try:
                writer.close()
            except Exception:
                pass

    async def _broadcast_event(self, event: str, data: Any) -> None:
        await self._broadcast({"event": event, "data": data})

    async def _connect(self, device_name: Optional[str], device_mac: Optional[str]) -> dict:
        if self.device and self.device.is_connected:
            return self.status

        if self.device:
            try:
                await self.device.disconnect()
            except Exception:
                pass
            self.device = None

        cfg = load_config()
        name = (device_name or cfg.get("device_name") or "").strip() or None
        mac = (device_mac or cfg.get("device_mac") or "").strip() or None
        self._connect_name = name
        self._connect_mac = mac
        ble = PuffcoBLE(
            device_name=name,
            device_mac=mac,
            debug=self.debug,
            disconnected_callback=self._on_ble_drop,
        )
        await ble.connect()
        try:
            await ble.require_peak_pro()
        except Exception:
            try:
                await ble.disconnect()
            except Exception:
                pass
            raise
        self.device = ble
        self._want_connected = True
        snap = await ble.snapshot(include_profiles=True)
        if is_proxy(snap.get("product")):
            await ble.disconnect()
            self.device = None
            raise RuntimeError("That device is a Proxy/Pivot. OmaPuffco only talks to Peak Pro.")
        self._apply_snapshot(snap)
        save_config(
            {
                **cfg,
                "device_mac": snap.get("device_mac") or mac or "",
                "device_name": snap.get("device_name") or name or "",
            }
        )
        self._start_poll()
        await self._refresh_clean(self.status.get("total_dabs"), notify=True)
        self._spawn(self._sync_usage_safe())
        return self.status

    async def _sync_usage(self) -> dict:
        """Pull new heat sessions from the Peak's audit log into history."""
        async with self._sync_lock:
            dev = self._require_device()
            serial = str(self.status.get("serial") or "")
            begin, end = await dev.get_log_bounds()
            state = history.device_log_state()
            start = begin + 1
            # A stored index at or past the ring's end means the log was cleared.
            # Re-read the whole ring once when no stored session has preheat timing yet.
            backfill = self.preheat_scale is None and not self._preheat_backfilled
            if (
                not backfill
                and state.get("serial") == serial
                and state.get("index") is not None
                and int(state["index"]) < end
            ):
                start = max(start, int(state["index"]) + 1)
            entries = [
                audit.parse_entry(i, await dev.read_log_entry(i)) for i in range(start, end)
            ]
            clock = await dev.get_device_clock()
            found = audit.sessions(entries, clock, time.time())
            added = history.record_device_sessions(found, last_index=max(end - 1, start - 1), serial=serial)
            self._preheat_backfilled = True
            self.preheat_scale = history.preheat_scale()
            self.status["telemetry"] = history.get_stats()
            await self._broadcast_event("status", self.status)
            log.info("Usage sync: read %d log entries, %d new sessions", len(entries), added)
            return {"read": len(entries), "added": added}

    async def _read_faults(self) -> dict:
        """The Peak's fault log, read incrementally and cached for this session."""
        async with self._fault_lock:
            dev = self._require_device()
            serial = str(self.status.get("serial") or "")
            begin, end = await dev.get_log_bounds("flt")
            cache = self._fault_cache
            if cache.get("serial") != serial or int(cache.get("end", 0)) > end:
                cache = {"serial": serial, "end": begin, "entries": {}}
            start = max(begin + 1, int(cache["end"]))
            for i in range(start, end):
                cache["entries"][i] = audit.parse_entry(i, await dev.read_log_entry(i, "flt"))
                cache["end"] = i + 1
            cache["entries"] = {i: e for i, e in cache["entries"].items() if i > begin}
            cache["end"] = end
            self._fault_cache = cache
            clock = await dev.get_device_clock()
            return {
                "faults": faults.decode(list(cache["entries"].values()), clock, time.time()),
                "read": max(0, end - start),
            }

    def _spawn(self, coro) -> None:
        # The loop only holds weak references to tasks.
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _sync_usage_safe(self, delay: float = 0.0) -> None:
        if delay:
            await asyncio.sleep(delay)
        try:
            await self._sync_usage()
        except Exception as exc:
            log.warning("Usage sync failed: %s", exc)

    async def _disconnect(self, forget: bool = False) -> dict:
        if forget:
            self._want_connected = False
            # A reconnect already mid-attempt would otherwise grab the Peak back.
            if self._reconnect_task and not self._reconnect_task.done():
                self._reconnect_task.cancel()
            self._reconnect_task = None
        self._cancel_saver_sleep()
        self._stop_poll()
        if self.device:
            try:
                await self.device.disconnect()
            except Exception:
                pass
            self.device = None
        self.status = self._empty_status()
        self.status["battery_saver"] = self.battery_saver
        self.status.update(self._clean_fields())
        await self._broadcast_event("status", self.status)
        return self.status

    def _require_device(self) -> PuffcoBLE:
        if not self.device or not self.device.is_connected:
            raise RuntimeError("Not connected")
        return self.device

    def _start_poll(self) -> None:
        self._stop_poll()

        async def _loop():
            ticks = 0
            while self.device and self.device.is_connected:
                try:
                    heating = self.status.get("operating_state_id") in HEAT_STATES
                    await asyncio.sleep(0.7 if heating else self.poll_interval)
                    ticks += 1
                    prev_state = self.status.get("operating_state_id")
                    if ticks % 8 == 0:
                        snap = await self.device.snapshot(include_profiles=True)
                        self._apply_snapshot(snap)
                    else:
                        snap = await self.device.poll_fast()
                        self._stamp_local(snap)
                        self.status.update(snap)
                    await self._broadcast_event("status", self.status)
                    new_state = self.status.get("operating_state_id")
                    if self.battery_saver:
                        if new_state in CYCLE_STATES:
                            self._cancel_saver_sleep()
                        elif cycle_just_ended(prev_state, new_state):
                            self._schedule_saver_sleep()
                    if prev_state != new_state and new_state == int(OperatingState.HEAT_CYCLE_ACTIVE):
                        history.record_cycle(**self._cycle_meta())
                        self.status["telemetry"] = history.get_stats()
                        try:
                            counted = int(self.status.get("total_dabs") or 0) + 1
                        except (TypeError, ValueError):
                            counted = 0
                        self.status["total_dabs"] = counted
                        await self._refresh_clean(counted, notify=True)
                        self._spawn(self._sync_usage_safe(delay=5.0))
                        await self._broadcast_event(
                            "notify",
                            {"title": "OmaPuffco", "body": "Peak Pro is ready"},
                        )
                    if self.status.get("battery", 100) <= 15 and ticks % 12 == 0:
                        await self._broadcast_event(
                            "notify",
                            {
                                "title": "Peak Pro battery low",
                                "body": f"{self.status.get('battery')}% remaining",
                            },
                        )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    log.warning("poll failed: %s", exc)
                    if not (self.device and self.device.is_connected):
                        self._on_ble_drop()
                        return

        self._poll_task = asyncio.create_task(_loop())

    def _stop_poll(self) -> None:
        if self._poll_task:
            self._poll_task.cancel()
            self._poll_task = None

    def _cancel_saver_sleep(self) -> None:
        task = self._saver_sleep_task
        self._saver_sleep_task = None
        if task and not task.done():
            task.cancel()

    def _schedule_saver_sleep(self) -> None:
        if self._saver_sleep_task and not self._saver_sleep_task.done():
            return
        self._saver_sleep_task = asyncio.create_task(self._run_saver_sleep())
        self._tasks.add(self._saver_sleep_task)
        self._saver_sleep_task.add_done_callback(self._tasks.discard)

    async def _run_saver_sleep(self) -> None:
        try:
            await asyncio.sleep(BATTERY_SAVER_SLEEP_S)
            async with self._cmd_lock:
                if not self.battery_saver:
                    return
                # Someone is using the Peak from the panel or CLI; don't sleep it under them.
                if time.monotonic() - self._last_user_cmd < BATTERY_SAVER_SLEEP_S:
                    return
                dev = self.device
                if not dev or not dev.is_connected:
                    return
                # The last poll can be seconds old, and a press of the Peak's
                # own button may have started a cycle since.
                state = int(await dev.get_operating_state())
                self.status["operating_state_id"] = state
                if state != int(OperatingState.IDLE):
                    return
                if self.lantern:
                    try:
                        await dev.stop_lantern()
                        self.lantern = False
                        self.status["lantern"] = False
                    except Exception:
                        log.debug("battery saver: lantern off failed", exc_info=True)
                await dev.enter_sleep_mode()
                log.info("Battery saver: slept Peak after idle")
                await self._broadcast_event("status", self.status)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("Battery saver sleep failed: %s", exc)

    def _clean_fields(self, total: Any = None) -> dict[str, Any]:
        if total is None:
            total = self.status.get("total_dabs")
        remaining = clean_remaining(total, self.clean_at_total, self.clean_every)
        return {
            "clean_every": self.clean_every,
            "clean_remaining": remaining,
            "clean_due": remaining <= 0,
        }

    def _save_clean(self) -> None:
        cfg = load_config()
        cfg["clean_every"] = self.clean_every
        cfg["clean_at_total"] = self.clean_at_total
        cfg["clean_notified"] = self.clean_notified
        save_config(cfg)

    def _baseline_clean(self, total: Any) -> None:
        if self.clean_at_total is not None:
            return
        try:
            n = int(total)
        except (TypeError, ValueError):
            return
        if n < 0:
            return
        self.clean_at_total = n
        self.clean_notified = False
        self._save_clean()

    async def _refresh_clean(self, total: Any, *, notify: bool = False) -> dict[str, Any]:
        self._baseline_clean(total)
        fields = self._clean_fields(total)
        self.status.update(fields)
        if fields["clean_remaining"] > 0 and self.clean_notified:
            self.clean_notified = False
            self._save_clean()
        if notify and fields["clean_due"] and not self.clean_notified:
            self.clean_notified = True
            self._save_clean()
            await self._notify_clean()
        await self._broadcast_event("status", self.status)
        return fields

    async def _notify_clean(self) -> None:
        title = "Peak Pro needs a clean"
        body = "Swab the chamber, then mark it cleaned in the panel."
        await self._broadcast_event("notify", {"title": title, "body": body})
        try:
            subprocess.Popen(
                ["notify-send", "-a", "OmaPuffco", "-u", "normal", title, body],
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            pass

    async def _set_clean_every(self, dabs: Any) -> dict[str, Any]:
        self.clean_every = snap_clean_every(dabs)
        self._save_clean()
        return await self._refresh_clean(self.status.get("total_dabs"))

    async def _mark_cleaned(self) -> dict[str, Any]:
        try:
            total = int(self.status.get("total_dabs") or 0)
        except (TypeError, ValueError):
            total = 0
        self.clean_at_total = max(0, total)
        self.clean_notified = False
        self._save_clean()
        return await self._refresh_clean(total)

    async def _set_battery_saver(self, enable: bool) -> dict[str, Any]:
        self.battery_saver = bool(enable)
        if not self.battery_saver:
            self._cancel_saver_sleep()
        cfg = load_config()
        cfg["battery_saver"] = self.battery_saver
        save_config(cfg)
        self.status["battery_saver"] = self.battery_saver
        if self.battery_saver and self.device and self.device.is_connected and self.lantern:
            try:
                await self.device.stop_lantern()
                self.lantern = False
                self.status["lantern"] = False
            except Exception:
                log.debug("battery saver: lantern off failed", exc_info=True)
        await self._broadcast_event("status", self.status)
        return {"battery_saver": self.battery_saver}

    async def handle(self, cmd: str, args: dict) -> Any:
        args = args or {}
        if cmd == "ping":
            return {"version": "0.2.0", "pid": os.getpid()}
        if cmd == "scan":
            timeout = float(args.get("timeout", 6))
            scanner = PuffcoBLE(
                device_name=args.get("device_name") or self._connect_name,
                device_mac=args.get("device_mac") or self._connect_mac,
                debug=self.debug,
            )
            devices = await scanner.scan(timeout=timeout)
            return {"devices": devices}
        if cmd == "connect":
            self._auto_reconnect = bool(args.get("auto_reconnect", True))
            return await self._connect(args.get("device_name"), args.get("device_mac"))
        if cmd == "disconnect":
            return await self._disconnect(forget=True)
        if cmd == "status":
            self.status["telemetry"] = history.get_stats()
            return self.status
        if cmd == "refresh":
            dev = self._require_device()
            snap = await dev.snapshot(include_profiles=True)
            self._apply_snapshot(snap)
            await self._broadcast_event("status", self.status)
            return self.status
        if cmd == "set_poll_interval":
            self.poll_interval = max(0.6, float(args.get("seconds", 1.5)))
            return {"poll_interval": self.poll_interval}
        if cmd == "get_config":
            return load_config()
        if cmd == "set_config":
            cfg = load_config()
            cfg.update(args)
            save_config(cfg)
            if "battery_saver" in args:
                await self._set_battery_saver(_as_bool(args.get("battery_saver")))
            if "clean_every" in args:
                await self._set_clean_every(args.get("clean_every"))
            return load_config()
        if cmd == "set_battery_saver":
            return await self._set_battery_saver(_as_bool(args.get("enable")))
        if cmd == "set_clean_every":
            return await self._set_clean_every(args.get("dabs"))
        if cmd == "mark_cleaned":
            return await self._mark_cleaned()
        if cmd == "peek":
            path = str(args["path"])
            size = int(args.get("size") or 12)
            try:
                raw = await self._require_device().read_short(path, 0, size)
            except LoraxError as exc:
                return {"path": path, "ok": False, "status": exc.status, "error": str(exc)}
            except Exception as exc:
                return {"path": path, "ok": False, "error": str(exc)}
            return {
                "path": path,
                "ok": True,
                "hex": raw.hex(),
                "n": len(raw),
            }
        if cmd == "poke":
            path = str(args["path"])
            raw = bytes.fromhex(str(args["hex"]))
            await self._require_device().write_short(path, 0, 0, raw)
            return {"path": path, "ok": True, "n": len(raw)}
        if cmd == "stats":
            stats = history.get_stats(days=int(args.get("days", 14)))
            stats["total_dabs"] = self.status.get("total_dabs", 0)
            stats["dabs_remaining"] = self.status.get("dabs_remaining", 0)
            stats["dabs_per_day"] = self.status.get("dabs_per_day", 0)
            return stats

        dev = self._require_device()
        self._last_user_cmd = time.monotonic()

        if cmd == "start_heat":
            self._cancel_saver_sleep()
            await dev.start_heat_cycle()
            return {"ok": True}
        if cmd == "stop_heat":
            await dev.stop_heat_cycle()
            return {"ok": True}
        if cmd == "boost_heat":
            await dev.boost_heat_cycle()
            return {"ok": True}
        if cmd == "start_lantern":
            await dev.start_lantern()
            self.lantern = True
            self.status["lantern"] = True
            await self._broadcast_event("status", self.status)
            return {"lantern": True}
        if cmd == "stop_lantern":
            await dev.stop_lantern()
            self.lantern = False
            self.status["lantern"] = False
            await self._broadcast_event("status", self.status)
            return {"lantern": False}
        if cmd == "set_lantern_timeout":
            seconds = _clamp(float(args["seconds"]), MIN_LANTERN_S, MAX_LANTERN_S)
            await dev.set_lantern_timeout(seconds)
            self.status["lantern_timeout"] = seconds
            await self._broadcast_event("status", self.status)
            return {"lantern_timeout": seconds}
        if cmd == "set_brightness":
            if "level" in args and not any(k in args for k in ("base", "mid", "glass", "logo")):
                level = int(args["level"])
                args = {"base": level, "mid": level, "glass": level, "logo": level}
            self.brightness = {
                "base": int(args.get("base", self.brightness["base"])),
                "mid": int(args.get("mid", self.brightness["mid"])),
                "glass": int(args.get("glass", self.brightness["glass"])),
                "logo": int(args.get("logo", self.brightness["logo"])),
            }
            await dev.set_led_brightness(
                self.brightness["base"],
                self.brightness["mid"],
                self.brightness["glass"],
                self.brightness["logo"],
            )
            self.status["brightness"] = dict(self.brightness)
            await self._broadcast_event("status", self.status)
            return self.brightness
        if cmd == "set_profile":
            index = _validate_index(args)
            await dev.set_current_profile(index)
            self.status["current_profile"] = index
            await self._broadcast_event("status", self.status)
            return {"current_profile": index}
        if cmd == "set_profile_name":
            index = _validate_index(args)
            await dev.set_profile_name(index, str(args["name"]))
            return await self.handle("refresh", {})
        if cmd == "set_profile_temp":
            index = _validate_index(args)
            if "celsius" in args:
                fahrenheit = PuffcoUtils.c_to_f(float(args["celsius"]))
            else:
                fahrenheit = float(args["fahrenheit"])
            fahrenheit = _clamp(fahrenheit, MIN_TEMP_F, MAX_TEMP_F)
            await dev.set_profile_temp_c(index, PuffcoUtils.f_to_c(fahrenheit))
            return await self.handle("refresh", {})
        if cmd == "set_profile_time":
            index = _validate_index(args)
            seconds = _clamp(float(args["seconds"]), MIN_TIME_S, MAX_TIME_S)
            await dev.set_profile_time(index, seconds)
            return await self.handle("refresh", {})
        if cmd == "set_profile_vapor":
            index = _validate_index(args)
            if "name" in args:
                level = vapor_value(str(args["name"]))
            else:
                level = snap_vapor(float(args["level"]))
            await dev.set_profile_vapor(index, level)
            return await self.handle("refresh", {})
        if cmd == "set_profile_boost":
            index = _validate_index(args)
            if "temp_f" in args:
                await dev.set_profile_boost_temp_f(
                    index, _clamp(float(args["temp_f"]), MIN_BOOST_TEMP_F, MAX_BOOST_TEMP_F)
                )
            if "seconds" in args:
                await dev.set_profile_boost_time(
                    index, _clamp(float(args["seconds"]), MIN_BOOST_TIME_S, MAX_BOOST_TIME_S)
                )
            return await self.handle("refresh", {})
        if cmd == "set_profile_color":
            index = args.get("index")
            if index is not None:
                index = _validate_index({"index": index})
            await dev.set_profile_solid_color(index, str(args["hex"]))
            self.lantern = True
            self.status["lantern"] = True
            return await self.handle("refresh", {})
        if cmd == "set_stealth":
            enable = bool(args.get("enable"))
            await dev.set_stealth_mode(enable)
            self.status["stealth"] = enable
            await self._broadcast_event("status", self.status)
            return {"stealth": enable}
        if cmd == "show_battery":
            await dev.show_battery_level()
            return {"ok": True}
        if cmd == "show_version":
            await dev.show_version()
            return {"ok": True}
        if cmd == "sleep":
            self._cancel_saver_sleep()
            await dev.enter_sleep_mode()
            return {"ok": True}
        if cmd == "power_off":
            self._cancel_saver_sleep()
            await dev.power_off()
            return {"ok": True}
        if cmd == "factory_reset":
            await dev.factory_reset()
            return {"ok": True}
        if cmd == "set_device_name":
            await dev.set_device_name(str(args["name"]))
            return await self.handle("refresh", {})

        raise ValueError(f"Unknown command: {cmd}")

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.clients.add(writer)
        try:
            await self._broadcast({"event": "status", "data": self.status})
            while True:
                line = await reader.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line.decode("utf-8"))
                except json.JSONDecodeError as exc:
                    await self._send(writer, {"ok": False, "error": f"bad json: {exc}"})
                    continue
                req_id = msg.get("id")
                cmd = msg.get("cmd")
                args = msg.get("args") or {}
                try:
                    # A full log read takes minutes; holding the command lock
                    # for it would leave Heat/Stop unresponsive meanwhile.
                    if cmd == "sync_usage":
                        result = await self._sync_usage()
                    elif cmd == "faults":
                        result = await self._read_faults()
                    else:
                        async with self._cmd_lock:
                            result = await self.handle(str(cmd), args)
                    await self._send(writer, {"id": req_id, "ok": True, "result": result})
                except Exception as exc:
                    log.exception("command %s failed", cmd)
                    err = str(exc) or exc.__class__.__name__
                    await self._send(
                        writer,
                        {
                            "id": req_id,
                            "ok": False,
                            "error": err,
                            "trace": traceback.format_exc() if self.debug else None,
                        },
                    )
        except (ConnectionError, asyncio.IncompleteReadError):
            # The client went away mid-reply, e.g. a stalled `omapuffco waybar` that got killed.
            pass
        finally:
            self.clients.discard(writer)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    @staticmethod
    async def _send(writer: asyncio.StreamWriter, payload: dict) -> None:
        writer.write((json.dumps(payload, default=str) + "\n").encode("utf-8"))
        await writer.drain()

    async def start(self) -> None:
        if self.socket_path.exists():
            try:
                self.socket_path.unlink()
            except OSError:
                pass
        self.socket_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.socket_path.parent, 0o700)
        self._loop = asyncio.get_running_loop()
        # Belt-and-suspenders against another local user connecting in the
        # instant between bind() and the chmod below: bind() creates the
        # socket file with umask-derived permissions, so tighten the umask
        # first (the parent dir being 0700 already blocks other users, but
        # this also covers XDG_RUNTIME_DIR overrides with looser modes).
        old_umask = os.umask(0o077)
        try:
            self._server = await asyncio.start_unix_server(self._client, path=str(self.socket_path))
        finally:
            os.umask(old_umask)
        os.chmod(self.socket_path, 0o600)
        log.info("Listening on %s", self.socket_path)

    async def close(self) -> None:
        self._want_connected = False
        self._stop_poll()
        if self.device:
            try:
                await self.device.disconnect()
            except Exception:
                pass
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        if self.socket_path.exists():
            try:
                self.socket_path.unlink()
            except OSError:
                pass


async def amain(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="OmaPuffco Peak Pro BLE daemon")
    parser.add_argument("--socket", type=Path, default=socket_path())
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    daemon = OmaPuffcoDaemon(args.socket, debug=args.debug)
    await daemon.start()

    stop = asyncio.Event()

    def _stop(*_):
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _stop)
        except NotImplementedError:
            pass

    await stop.wait()
    await daemon.close()
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(amain()))


if __name__ == "__main__":
    main()
