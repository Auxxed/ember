"""Session daemon: owns the BLE link and serves a Unix-socket JSON API."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import traceback
from pathlib import Path
from typing import Any, Optional

from . import history
from .ble import PuffcoBLE
from .constants import AnimationCode, OperatingState
from .paths import load_config, log_path, save_config, socket_path
from .product_info import is_proxy

log = logging.getLogger("ember.daemon")

ANIM_ALIASES = {
    "solid": None,
    "breathing": AnimationCode.BREATHING,
    "rising": AnimationCode.RISING,
    "circling": AnimationCode.CIRCLING,
    "heat": AnimationCode.HEAT_CYCLE_ACTIVE,
}

HEAT_STATES = {
    int(OperatingState.HEAT_CYCLE_PREHEAT),
    int(OperatingState.HEAT_CYCLE_ACTIVE),
}


class EmberDaemon:
    def __init__(self, sock: Path, debug: bool = False):
        self.socket_path = sock
        self.debug = debug
        self.device: Optional[PuffcoBLE] = None
        self.status: dict[str, Any] = self._empty_status()
        self.clients: set[asyncio.StreamWriter] = set()
        self._cmd_lock = asyncio.Lock()
        self._poll_task: Optional[asyncio.Task] = None
        self._reconnect_task: Optional[asyncio.Task] = None
        self._auto_reconnect = True
        self._want_connected = False
        self._connect_name: Optional[str] = None
        self._connect_mac: Optional[str] = None
        self.lantern = False
        self.brightness = {"base": 80, "mid": 80, "glass": 80, "logo": 80}
        self.poll_interval = 1.5
        self._server: Optional[asyncio.AbstractServer] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

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
            "chamber": "",
            "chamber_id": -1,
            "operating_state": "Disconnected",
            "operating_state_id": -1,
            "heater_temp_c": None,
            "heater_temp_f": None,
            "stealth": False,
            "lantern": False,
            "dabs_remaining": 0,
            "dabs_per_day": 0,
            "total_dabs": 0,
            "current_profile": 0,
            "profiles": [],
            "brightness": {"base": 80, "mid": 80, "glass": 80, "logo": 80},
            "telemetry": {},
        }

    def _apply_snapshot(self, snap: dict[str, Any]) -> None:
        """Merge a full device snapshot into status and refresh telemetry."""
        snap["lantern"] = self.lantern
        snap["brightness"] = dict(self.brightness)
        self.status.update(snap)
        history.record_total(self.status.get("total_dabs"))
        self.status["telemetry"] = history.get_stats()

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
            raise RuntimeError("That device is a Proxy/Pivot. Ember only talks to Peak Pro.")
        self._apply_snapshot(snap)
        save_config(
            {
                **cfg,
                "device_mac": snap.get("device_mac") or mac or "",
                "device_name": snap.get("device_name") or name or "",
            }
        )
        self._start_poll()
        await self._broadcast_event("status", self.status)
        return self.status

    async def _disconnect(self, forget: bool = False) -> dict:
        if forget:
            self._want_connected = False
        self._stop_poll()
        if self.device:
            try:
                await self.device.disconnect()
            except Exception:
                pass
            self.device = None
        self.status = self._empty_status()
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
                        snap["lantern"] = self.lantern
                        snap["brightness"] = dict(self.brightness)
                        self.status.update(snap)
                    await self._broadcast_event("status", self.status)
                    new_state = self.status.get("operating_state_id")
                    if prev_state != new_state and new_state == int(OperatingState.HEAT_CYCLE_ACTIVE):
                        await self._broadcast_event(
                            "notify",
                            {"title": "Ember", "body": "Peak Pro is ready"},
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

    async def handle(self, cmd: str, args: dict) -> Any:
        args = args or {}
        if cmd == "ping":
            return {"version": "0.1.0", "pid": os.getpid()}
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
            return cfg
        if cmd == "stats":
            stats = history.get_stats(days=int(args.get("days", 14)))
            stats["total_dabs"] = self.status.get("total_dabs", 0)
            stats["dabs_remaining"] = self.status.get("dabs_remaining", 0)
            stats["dabs_per_day"] = self.status.get("dabs_per_day", 0)
            return stats

        dev = self._require_device()

        if cmd == "start_heat":
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
            index = int(args["index"])
            await dev.set_current_profile(index)
            self.status["current_profile"] = index
            await self._broadcast_event("status", self.status)
            return {"current_profile": index}
        if cmd == "set_profile_name":
            await dev.set_profile_name(int(args["index"]), str(args["name"]))
            return await self.handle("refresh", {})
        if cmd == "set_profile_temp":
            index = int(args["index"])
            if "celsius" in args:
                await dev.set_profile_temp_c(index, float(args["celsius"]))
            else:
                await dev.set_profile_temp_f(index, float(args["fahrenheit"]))
            return await self.handle("refresh", {})
        if cmd == "set_profile_time":
            await dev.set_profile_time(int(args["index"]), float(args["seconds"]))
            return await self.handle("refresh", {})
        if cmd == "set_profile_color":
            await dev.set_profile_solid_color(args.get("index"), str(args["hex"]))
            return await self.handle("refresh", {})
        if cmd == "set_animation":
            name = str(args.get("anim", "solid")).lower()
            colors = args.get("colors") or [args.get("hex") or "#ffffff"]
            index = args.get("index")
            if name == "solid":
                await dev.set_profile_solid_color(index, colors[0])
            else:
                anim = ANIM_ALIASES.get(name)
                if anim is None:
                    raise ValueError(f"Unknown animation: {name}")
                await dev.set_profile_animation(
                    index,
                    anim,
                    list(colors),
                    speed=int(args.get("speed", 20)),
                    bright=int(args.get("bright", 255)),
                )
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
            await dev.enter_sleep_mode()
            return {"ok": True}
        if cmd == "power_off":
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
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        self._loop = asyncio.get_running_loop()
        self._server = await asyncio.start_unix_server(self._client, path=str(self.socket_path))
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
    parser = argparse.ArgumentParser(description="Ember Peak Pro BLE daemon")
    parser.add_argument("--socket", type=Path, default=socket_path())
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    daemon = EmberDaemon(args.socket, debug=args.debug)
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
