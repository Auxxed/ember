"""Peak Pro BLE client.

Linux/BlueZ fork of Fr0st3h/PuffcoBLE (MIT), trimmed to Peak Pro.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import struct
from base64 import b64decode
from hashlib import sha256
from typing import Any, Literal, Optional, Union

import cbor2
from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice

from .codec import decode_puffco_json, first_color, hexify
from .lights import rgbt_color, rgbt_to_hex, solid_color_payload
from .constants import (
    CHAMBER_LABELS,
    CHARGE_SOURCE_LABELS,
    CHARGE_STATE_LABELS,
    OPERATING_STATE_LABELS,
    PROFILE_COUNT,
    BatteryChargeSource,
    BatteryChargeState,
    ChamberType,
    LoraxOpCodes,
    LoraxService,
    ModeCommands,
    OperatingState,
    UnlockKeys,
)
from .product_info import get_product_info, is_proxy
from .utils import PuffcoUtils
from .vapor import name_for as vapor_name_for

log = logging.getLogger("quickpuff.ble")

_STRUCT_FORMATS = {
    "int8": "b",
    "uint8": "B",
    "int16": "h",
    "uint16": "H",
    "int32": "i",
    "uint32": "I",
    "int64": "q",
    "uint64": "Q",
    "float32": "f",
    "float64": "d",
    "bool": "?",
}

DataType = Literal[
    "int8",
    "uint8",
    "int16",
    "uint16",
    "int32",
    "uint32",
    "int64",
    "uint64",
    "float32",
    "float64",
    "bool",
    "bytes",
]

PEAK_PRO_NAME_HINTS = ("puffco", "peak")
EXCLUDE_NAME_HINTS = ("proxy", "pivot")
HEATER_TEMP_PATHS = ("/p/app/htr/temp", "/p/htr/temp")
HEAT_CYCLE_STATES = {
    int(OperatingState.HEAT_CYCLE_PREHEAT),
    int(OperatingState.HEAT_CYCLE_ACTIVE),
    int(OperatingState.HEAT_CYCLE_FADE),
}


def _enum_or_raw(enum_cls, value: int):
    # Newer firmware can report codes this table doesn't know; keep the raw
    # number rather than failing the whole snapshot.
    try:
        return enum_cls(value)
    except ValueError:
        return int(value)


class LoraxError(RuntimeError):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


class PuffcoBLE:
    def __init__(
        self,
        device_name: Optional[str] = None,
        device_mac: Optional[str] = None,
        debug: bool = False,
        disconnected_callback=None,
    ):
        self.device_name = device_name
        self.device_mac = device_mac
        self.debug = debug
        self._user_disconnected_cb = disconnected_callback
        self.lorax_sequence = 1
        self.client: Optional[BleakClient] = None
        self._pending: dict[int, asyncio.Future] = {}
        self._notify_started = False
        self._lock = asyncio.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self.address: Optional[str] = None
        self.advertised_name: Optional[str] = None
        self._pairing_agent = None
        self._write_fd: Optional[int] = None
        self._write_bus = None
        # Largest single Lorax message; lowered to the link's ATT MTU and the
        # Peak's reported limit once connected.
        self._max_message = 125
        # 2 or 3, read once per connection (see get_led_api).
        self._led_api: Optional[int] = None
        if debug:
            logging.getLogger("puffcoble").setLevel(logging.DEBUG)

    def _dbg(self, message: str) -> None:
        if self.debug:
            log.debug(message)

    def _on_disconnected(self, _client: BleakClient) -> None:
        self._notify_started = False
        self._release_command_write_sync()
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_exception(LoraxError("Device disconnected"))
        self._pending.clear()
        if self._user_disconnected_cb:
            try:
                self._user_disconnected_cb()
            except Exception:
                log.exception("disconnected callback failed")

    def _on_reply(self, _characteristic, data: bytearray) -> None:
        raw = bytes(data)
        log.info("Lorax reply %d bytes: %s", len(raw), raw.hex())
        if len(data) < 3:
            return
        seq, status = struct.unpack_from("<HB", data)
        payload = bytes(data[3:])
        loop = self._loop or asyncio.get_event_loop()

        def settle():
            fut = self._pending.pop(seq, None)
            if not fut or fut.done():
                return
            if status != 0:
                fut.set_exception(LoraxError(f"Lorax status 0x{status:02x}", status))
            else:
                fut.set_result(payload)

        try:
            loop.call_soon_threadsafe(settle)
        except RuntimeError:
            settle()

    def _on_event(self, _characteristic, data: bytearray) -> None:
        log.info("Lorax event %s", bytes(data).hex())

    @staticmethod
    def _looks_like_peak_pro(name: str | None, service_uuids: list[str] | None = None) -> bool:
        lowered = (name or "").lower()
        if any(hint in lowered for hint in EXCLUDE_NAME_HINTS):
            return False
        if service_uuids:
            wanted = LoraxService.UUID.lower()
            if any(u.lower() == wanted for u in service_uuids):
                return True
        if not name:
            return False
        return any(hint in lowered for hint in PEAK_PRO_NAME_HINTS)

    async def scan(self, timeout: float = 6.0) -> list[dict[str, str]]:
        from . import bluez

        found: dict[str, dict[str, str]] = {}
        discovered = await BleakScanner.discover(
            timeout=timeout, return_adv=True, **await bluez.bleak_args()
        )
        for address, (device, adv) in discovered.items():
            name = device.name or adv.local_name or ""
            uuids = list(adv.service_uuids or [])
            if not self._looks_like_peak_pro(name, uuids) and not (
                self.device_name and self.device_name.lower() in name.lower()
            ) and not (
                self.device_mac and self.device_mac.lower() == address.lower()
            ):
                continue
            found[address] = {
                "name": name or "Peak Pro",
                "address": address,
                "rssi": str(adv.rssi if adv.rssi is not None else ""),
            }
        return list(found.values())

    def _match_device(self, device: BLEDevice, adv=None) -> bool:
        name = device.name or (getattr(adv, "local_name", None) if adv else "") or ""
        if self.device_mac and self.device_mac.lower() == (device.address or "").lower():
            return True
        if self.device_name and self.device_name.lower() in name.lower():
            return True
        return False

    async def search_for_device(self, timeout: float = 10.0) -> Optional[BLEDevice]:
        from . import bluez

        if not self.device_mac and not self.device_name:
            hits = await self.scan(timeout=timeout)
            if len(hits) == 1:
                self.device_mac = hits[0]["address"]
                self.device_name = hits[0]["name"]
            elif not hits:
                raise ValueError("No Peak Pro found. Wake it, keep it near the PC, and disconnect the phone app.")
            else:
                raise ValueError(
                    "Multiple Peak Pros found. Pass a name or MAC: "
                    + ", ".join(f"{h['name']} ({h['address']})" for h in hits)
                )

        self._dbg("Scanning for devices...")
        bleak_args = await bluez.bleak_args()
        if self.device_mac:
            device = await BleakScanner.find_device_by_address(
                self.device_mac, timeout=timeout, **bleak_args
            )
            if device:
                return device

        return await BleakScanner.find_device_by_filter(
            lambda d, ad: self._match_device(d, ad) or self._looks_like_peak_pro(
                d.name or ad.local_name, list(ad.service_uuids or [])
            ),
            timeout=timeout,
            **bleak_args,
        )

    async def _gatt_connect(self, address: str) -> BleakClient:
        from . import bluez

        await bluez.stop_discovery()
        await asyncio.sleep(0.35)
        try:
            await bluez.trust_device(address)
        except Exception:
            pass

        # Prefer a plain BlueZ Connect (what bluetoothctl does). Bleak's
        # Connect-while-discovery-is-active hangs on this MediaTek adapter.
        if not await bluez.device_connected(address):
            try:
                await asyncio.wait_for(bluez.bluez_connect(address), timeout=15)
            except Exception as exc:
                log.info("BlueZ Connect: %r — waiting for in-progress link", exc)
                for _ in range(20):
                    if await bluez.device_connected(address):
                        break
                    await asyncio.sleep(0.4)

        client = BleakClient(
            address,
            disconnected_callback=self._on_disconnected,
            timeout=15.0,
            pair=False,
            **await bluez.bleak_args(),
        )
        await client.connect()
        try:
            resolved = await bluez.wait_services_resolved(address, timeout=6.0)
            log.info("ServicesResolved=%s", resolved)
        except Exception as exc:
            log.debug("wait ServicesResolved: %r", exc)
        return client

    async def _lorax_handshake(self, client: BleakClient) -> None:
        try:
            services = {service.uuid.lower() for service in client.services}
        except Exception:
            services = set()
        if services and LoraxService.UUID.lower() not in services:
            raise LoraxError(
                "This Peak's firmware predates the Bluetooth protocol QuickPuff uses. "
                "Update it once in the Puffco app, then connect again."
            )
        self.client = client
        self._notify_started = False
        self.lorax_sequence = 1
        await self.trigger_bonding()
        await asyncio.sleep(0.35)
        await self._ensure_notify()
        await asyncio.sleep(0.35)
        try:
            limits = await self.run_command(
                LoraxOpCodes.GET_LIMITS, b"", timeout=5.0, log_msg="GetLimits"
            )
            log.info("Lorax limits %s", limits.hex())
            if len(limits) >= 2:
                self._max_message = min(self._max_message, struct.unpack_from("<H", limits)[0])
        except Exception as exc:
            log.warning("GetLimits failed: %r — continuing with auth", exc)
        await self.auth_device()

    async def connect(self) -> BleakClient:
        from .agent import PairingAgent
        from . import bluez

        self._loop = asyncio.get_running_loop()
        last_error: Exception | None = None
        agent = PairingAgent()
        try:
            await agent.start()
        except Exception as exc:
            log.warning("pairing agent failed to start: %s", exc)
        self._pairing_agent = agent

        try:
            for attempt in range(1, 4):
                address = (self.device_mac or "").strip()
                if not address:
                    device = await self.search_for_device(timeout=8.0)
                    await bluez.stop_discovery()
                    if not device:
                        last_error = RuntimeError("Peak Pro not found. Wake it and keep it near the PC.")
                        log.warning("scan attempt %s found nothing", attempt)
                        await asyncio.sleep(1.0)
                        continue
                    address = device.address
                    self.advertised_name = device.name

                self.address = address
                log.info("Connecting to %s attempt %s", address, attempt)
                client = None
                try:
                    client = await self._gatt_connect(address)
                    if not client.is_connected:
                        raise ConnectionError("BlueZ reported disconnected after Connect")
                    await self._lorax_handshake(client)
                    return client
                except Exception as exc:
                    last_error = exc
                    log.warning("connect attempt %s failed: %r", attempt, exc)
                    if client:
                        try:
                            await client.disconnect()
                        except Exception:
                            pass
                    self.client = None
                    # Refresh the BlueZ device object, then stop discovery again.
                    try:
                        await BleakScanner.find_device_by_address(
                            address, timeout=5.0, **await bluez.bleak_args()
                        )
                    except Exception:
                        pass
                    await bluez.stop_discovery()
                    await asyncio.sleep(1.0)
        except Exception:
            await agent.stop()
            self._pairing_agent = None
            raise

        await agent.stop()
        self._pairing_agent = None
        detail = f"{last_error.__class__.__name__}: {last_error or last_error.__class__.__name__}" if last_error else "unknown error"
        raise ConnectionError(
            f"Bluetooth connect failed ({detail}). "
            "Tap the Peak Pro to wake it, keep it close, and try again."
        )

    @property
    def is_connected(self) -> bool:
        return bool(self.client and self.client.is_connected)

    async def disconnect(self) -> None:
        if self.client:
            try:
                if self._notify_started and self.client.is_connected:
                    await self.client.stop_notify(LoraxService.REPLY_CHAR)
            except Exception:
                pass
            try:
                if self.client.is_connected:
                    await self.client.disconnect()
            finally:
                self.client = None
                self._notify_started = False
                self._release_command_write_sync()
                self._dbg("Disconnected from device")
        if self._pairing_agent:
            try:
                await self._pairing_agent.stop()
            except Exception:
                pass
            self._pairing_agent = None

    async def trigger_bonding(self) -> bool:
        from . import bluez

        if not self.client:
            raise RuntimeError("Client not connected")
        raw = await self.client.read_gatt_char(LoraxService.VERSION_CHAR)
        log.info("Lorax version %s", bytes(raw).hex())
        address = (self.address or self.device_mac or "").strip()
        if not address:
            return True
        if await bluez.device_paired(address):
            log.info("Already bonded %s", address)
            return True
        try:
            await asyncio.wait_for(bluez.pair_device(address), timeout=15)
        except Exception as exc:
            log.warning("Pair %s: %r", address, exc)
        bonded = await bluez.wait_bonded(address, timeout=8.0)
        log.info("Bonded %s after version read: %s", address, bonded)
        return bonded

    async def _ensure_notify(self) -> None:
        if not self.client:
            raise RuntimeError("Client not connected")
        if self._notify_started:
            return
        await self.client.start_notify(LoraxService.REPLY_CHAR, self._on_reply)
        try:
            await self.client.start_notify(LoraxService.EVENT_CHAR, self._on_event)
        except Exception as exc:
            log.debug("EVENT_CHAR notify: %r", exc)
        await asyncio.sleep(0.35)
        self._notify_started = True

    def _release_command_write_sync(self) -> None:
        fd = self._write_fd
        self._write_fd = None
        bus = self._write_bus
        self._write_bus = None
        if fd is not None:
            try:
                os.close(fd)
            except Exception:
                pass
        if bus is not None:
            try:
                bus.disconnect()
            except Exception:
                pass

    async def _acquire_command_write(self) -> None:
        if self._write_fd is not None:
            return
        if not self.client:
            raise RuntimeError("Client not connected")
        char = self.client.services.get_characteristic(LoraxService.COMMAND_CHAR)
        if char is None:
            raise RuntimeError("Lorax command characteristic missing")
        char_path = char.obj[0]
        from dbus_fast import BusType, Message
        from dbus_fast.aio import MessageBus

        bus = await MessageBus(bus_type=BusType.SYSTEM, negotiate_unix_fd=True).connect()
        reply = await bus.call(
            Message(
                destination="org.bluez",
                path=char_path,
                interface="org.bluez.GattCharacteristic1",
                member="AcquireWrite",
                signature="a{sv}",
                body=[{}],
            )
        )
        if reply.error_name:
            bus.disconnect()
            raise RuntimeError(f"AcquireWrite failed: {reply.error_name}")
        fds = reply.unix_fds or []
        if not fds:
            bus.disconnect()
            raise RuntimeError("AcquireWrite returned no file descriptor")
        self._write_bus = bus
        self._write_fd = int(fds[0])
        mtu = reply.body[1] if len(reply.body) > 1 else "?"
        if isinstance(mtu, int) and mtu > 3:
            # One write must fit a single ATT packet (MTU minus its 3-byte header).
            self._max_message = min(self._max_message, mtu - 3)
        log.info("Lorax command write fd %s mtu %s", self._write_fd, mtu)

    async def run_command(
        self,
        opcode: int,
        body: bytes = b"",
        timeout: float = 4.0,
        log_msg: str = "",
    ) -> bytes:
        if not self.client or not self.client.is_connected:
            raise RuntimeError("Client not connected")

        await self._ensure_notify()
        await self._acquire_command_write()
        async with self._lock:
            seq = self.lorax_sequence & 0xFFFF
            if seq == 0:
                seq = 1
            self.lorax_sequence = seq + 1
            if self.lorax_sequence > 0xFFFF:
                self.lorax_sequence = 1
            header = struct.pack("<HB", seq, opcode)
            msg = header + body
            self._dbg(f"→ seq {seq:04X} {log_msg}")

            loop = asyncio.get_running_loop()
            fut: asyncio.Future = loop.create_future()
            self._pending[seq] = fut
            try:
                os.write(self._write_fd, msg)
                reply = await asyncio.wait_for(fut, timeout=timeout)
                self._dbg(f"← seq {seq:04X} reply ({len(reply)} bytes)")
                return reply
            except Exception:
                self._pending.pop(seq, None)
                raise

    async def read(
        self,
        path: str,
        offset: int = 0,
        size: Optional[int] = None,
        data_type: DataType = "bytes",
        count: int = 1,
    ) -> Union[float, int, bool, bytes, list]:
        if data_type == "bytes":
            if size is None:
                raise ValueError("size is required when data_type='bytes'")
            fmt = None
            elem_size = None
        else:
            base_format = _STRUCT_FORMATS.get(data_type)
            if not base_format:
                raise ValueError(f"Unsupported data_type: {data_type}")
            fmt = f"<{count}{base_format}"
            elem_size = struct.calcsize(f"<{base_format}")
            required_size = elem_size * max(1, count)
            size = required_size if size is None else size
            if size < required_size:
                raise ValueError(
                    f"size {size} is too small for {count} {data_type} elements "
                    f"(need {required_size} bytes)"
                )

        body = struct.pack("<HH", offset, size) + path.encode("utf-8")
        result = await self.run_command(
            LoraxOpCodes.READ_SHORT,
            body,
            timeout=3.0,
            log_msg=f"Read {path} ({size} bytes @ offset {offset})",
        )
        self._dbg(f"Read {path}: {result.hex()} @ offset {offset}")

        if data_type == "bytes":
            return result

        data = result[: elem_size * max(1, count)]
        if len(data) < elem_size * max(1, count):
            raise LoraxError(f"Short read on {path}: got {len(data)} bytes")
        values = struct.unpack(fmt, data)
        return values[0] if count == 1 else list(values)

    async def read_short(self, path: str, offset: int, size: int) -> bytes:
        return await self.read(path, offset, size, data_type="bytes")

    async def write_short(self, path: str, offset: int, flags: int, value_bytes: bytes) -> None:
        body = struct.pack("<HB", offset, flags) + path.encode("utf-8") + b"\x00" + bytes(value_bytes)
        await self.run_command(
            LoraxOpCodes.WRITE_SHORT,
            body,
            timeout=3.0,
            log_msg=f"WriteShort {path} (flags={flags}, data={value_bytes.hex()})",
        )

    async def write(
        self,
        path: str,
        value: Union[int, float, bool, bytes],
        offset: int = 0,
        flags: int = 0,
        data_type: DataType = "bytes",
    ) -> None:
        if data_type == "bytes":
            if not isinstance(value, bytes):
                raise ValueError("value must be bytes when data_type='bytes'")
            value_bytes = value
        else:
            base_format = _STRUCT_FORMATS.get(data_type)
            if not base_format:
                raise ValueError(f"Unsupported data_type: {data_type}")
            value_bytes = struct.pack(f"<{base_format}", value)
        await self.write_short(path, offset, flags, value_bytes)

    async def auth_device(self) -> bool:
        seed = await self.read_access_seed_key()
        key = self._make_key(seed, bytearray(b64decode(UnlockKeys.LORAX_KEY)))
        await self.unlock_access(key)
        return True

    async def unlock_access(self, key: bytes) -> bytes:
        return await self.run_command(LoraxOpCodes.UNLOCK_ACCESS, key, timeout=3.0, log_msg="UnlockAccess")

    async def read_access_seed_key(self) -> list[int]:
        try:
            result = await self.run_command(
                LoraxOpCodes.GET_ACCESS_SEED, b"", timeout=6.0, log_msg="GetAccessSeed"
            )
        except (TimeoutError, asyncio.TimeoutError) as exc:
            raise LoraxError(
                "Peak Pro is on Bluetooth but did not answer Lorax auth. "
                "Disconnect it from the phone app, bring it next to the PC, "
                "tap the button to wake it, then connect again."
            ) from exc
        if len(result) < 16:
            raise LoraxError(f"Access seed too short: {len(result)} bytes")
        return list(result[:16])

    def _make_key(self, access_seed: list[int], handshake_key: bytearray) -> bytearray:
        buf = bytearray(32)
        for i in range(16):
            buf[i] = handshake_key[i]
            buf[i + 16] = access_seed[i]
        digest = sha256(buf).digest()
        return bytearray(digest[:16])

    async def read_bytes_all(
        self,
        path: str,
        *,
        chunk_size: int = 125,
        max_len: Optional[int] = None,
    ) -> bytes:
        out = bytearray()
        idx = 0
        cap = 1_048_576
        while True:
            req = chunk_size if max_len is None else min(chunk_size, max_len - len(out))
            if req <= 0:
                break
            chunk = await self.read(path, idx, req, data_type="bytes")
            if not chunk:
                break
            out.extend(chunk)
            idx += len(chunk)
            if len(chunk) < req:
                break
            if max_len is not None and len(out) >= max_len:
                break
            if len(out) >= cap:
                raise RuntimeError(f"read_bytes_all exceeded cap of {cap} bytes")
        return bytes(out)

    async def write_cbor_full(self, path: str, obj: dict) -> None:
        blob = cbor2.dumps(hexify(obj), canonical=True)
        # Every Lorax message has to fit one BLE packet (MTU 131 on a Peak Pro
        # link). A larger write is dropped without a reply, which is how colour
        # and mood writes used to time out.
        room = self._max_message - (6 + len(path.encode("utf-8")) + 1)
        if room <= 0:
            raise LoraxError(f"Path too long to write: {path}")
        if len(blob) <= room:
            written = False
            for flags in (1, 4, 0):
                try:
                    await self.write_short(path, 0, flags, blob)
                    written = True
                    break
                except LoraxError:
                    continue
            if not written:
                await self.write_short(path, 0, 0, blob)
        else:
            offset = 0
            flags = 1
            while offset < len(blob):
                piece = blob[offset : offset + room]
                try:
                    await self.write_short(path, offset, flags, piece)
                except LoraxError:
                    if flags == 0:
                        raise
                    await self.write_short(path, offset, 0, piece)
                offset += len(piece)
                flags = 0
        # The firmware ignores truncate flags, so a shorter payload would leave
        # the previous one's tail behind it in the file.
        try:
            await self.write_short(path, len(blob), 0, b"\x00" * min(64, room))
        except LoraxError:
            pass

    async def _read_and_decode(self, path: str) -> str:
        raw = await self.read_short(path, 0, 125)
        return PuffcoUtils.c_string(raw)

    async def get_device_info(self) -> dict[str, Any]:
        mdcd = await self.read("/p/sys/hw/mdcd", 0, 4, "uint32")
        info = get_product_info(model_code=int(mdcd))
        if not info:
            return {"type": "unknown", "model_code": int(mdcd), "label": f"Peak Pro ({mdcd})"}
        data = info.to_dict()
        data["model_code"] = int(mdcd)
        return data

    async def require_peak_pro(self) -> dict[str, Any]:
        info = await self.get_device_info()
        if is_proxy(info):
            raise RuntimeError("That device is a Proxy/Pivot. QuickPuff only talks to Peak Pro.")
        return info

    async def get_heater_temp_c(self) -> float | None:
        for path in HEATER_TEMP_PATHS:
            try:
                value = float(await self.read(path, 0, data_type="float32"))
                if -20.0 <= value <= 400.0:
                    return value
            except Exception:
                log.debug("heater temp %s failed", path, exc_info=True)
        return None

    async def get_serial_number(self) -> str:
        return await self._read_and_decode("/p/sys/hw/ser")

    async def get_device_name(self) -> str:
        return await self._read_and_decode("/u/sys/name")

    async def set_device_name(self, name: str) -> None:
        encoded = name.encode("utf-8")[:32] + b"\x00"
        await self.write_short("/u/sys/name", 0, 0, encoded)

    async def get_software_version(self) -> str:
        data = await self.read_short("/p/sys/fw/ver", 0, 125)
        return PuffcoUtils.revision_number_to_string(data[0] if data else 0)

    async def get_bootloader_version(self) -> str:
        data = await self.read("/p/sys/fw/bver", 0, 12, "uint8")
        return PuffcoUtils.revision_number_to_string(int(data))

    async def get_uptime(self) -> int:
        # float32 seconds; reading it as uint32 reported ~14000 days.
        return int(float(await self.read("/p/sys/uptm", 0, 4, "float32")))

    async def get_chamber_type(self) -> ChamberType:
        data = await self.read_short("/p/htr/chmt", 0, 1)
        return _enum_or_raw(ChamberType, int(data[0]))

    async def get_battery_charge_state(self) -> BatteryChargeState:
        data = await self.read_short("/p/bat/chg/stat", 0, 1)
        return _enum_or_raw(BatteryChargeState, int(data[0]))

    async def get_battery_charge_source(self) -> BatteryChargeSource:
        data = await self.read_short("/p/bat/chg/src", 0, 1)
        return _enum_or_raw(BatteryChargeSource, int(data[0]))

    async def get_device_birthday(self) -> int:
        return int(await self.read("/u/sys/bday", 0, 4, "uint32"))

    async def get_lantern_timeout(self) -> float:
        return float(await self.read("/p/app/ltrn/time", 0, 4, "float32"))

    async def set_lantern_timeout(self, seconds: float) -> None:
        await self.write("/p/app/ltrn/time", float(seconds), data_type="float32")

    async def get_battery_level(self) -> int:
        # /p/bat/soc is charge percent; /p/bat/cap is pack capacity in mAh,
        # which clamped to a permanent 100%.
        value = float(await self.read("/p/bat/soc", 0, 4, "float32"))
        return max(0, min(100, int(round(value))))

    async def get_charge_eta(self) -> float | None:
        """Seconds until full, as the Peak estimates it while charging."""
        value = float(await self.read("/p/bat/chg/etf", 0, 4, "float32"))
        return value if math.isfinite(value) and 0 < value < 86400 else None

    async def _charge_eta(self, charge: Any) -> float | None:
        if int(charge) not in (int(BatteryChargeState.BULK), int(BatteryChargeState.TOPUP)):
            return None
        try:
            return await self.get_charge_eta()
        except Exception:
            log.debug("charge ETA read failed", exc_info=True)
            return None

    async def get_max_charge(self) -> float | None:
        """Charge limit in percent (/u/bat/msoc); Puffco's Battery Preservation sets 80."""
        value = float(await self.read("/u/bat/msoc", 0, 4, "float32"))
        return value if math.isfinite(value) and 0 < value <= 100 else None

    async def set_max_charge(self, percent: float) -> None:
        await self.write("/u/bat/msoc", float(percent), data_type="float32")

    async def get_battery_capacity(self) -> float | None:
        """Pack capacity in coulombs as the Peak's fuel gauge has learned it."""
        value = float(await self.read("/p/bat/cap", 0, 4, "float32"))
        return value if math.isfinite(value) and value > 0 else None

    async def get_operating_state(self) -> OperatingState:
        data = await self.read_short("/p/app/stat/id", 0, 1)
        return _enum_or_raw(OperatingState, int(data[0]))

    async def _read_dab_count(self, path: str) -> int:
        # These counters are float32 values in a 12-byte Lorax file. Asking
        # for 4 bytes as uint32 is rejected with status 0x02 on current
        # Peak Pro firmware, which is how QuickPuff used to report 0 forever.
        return int(round(float(await self.read(path, 0, 12, "float32"))))

    async def get_approx_dabs_remaining(self) -> int:
        return await self._read_dab_count("/p/app/info/drem")

    async def get_dabs_per_day(self) -> int:
        return await self._read_dab_count("/p/app/info/dpd")

    async def get_total_dabs(self) -> int:
        # The official app reads the lifetime odometer; /p/app/info/dtot is
        # rejected (status 0x02) on current Peak Pro firmware.
        try:
            return await self._read_dab_count("/p/app/odom/0/nc")
        except LoraxError:
            return await self._read_dab_count("/p/app/info/dtot")

    async def get_log_bounds(self, log: str = "aud") -> tuple[int, int]:
        """Ring bounds of the audit ("aud") or fault ("flt") log; readable
        entries are strictly between the two."""
        begin = int(await self.read(f"/p/logv/{log}/begn", 0, 4, "uint32"))
        end = int(await self.read(f"/p/logv/{log}/end", 0, 4, "uint32"))
        return begin, end

    async def get_device_clock(self) -> int:
        return int(await self.read("/p/sys/time", 0, 4, "uint32"))

    async def read_log_entry(self, index: int, log: str = "aud") -> bytes:
        # The entry file serves whatever the selector points at, so wait for
        # the cursor to land or a slow write hands back the previous entry.
        await self.write_short(f"/p/logv/{log}/sel", 0, 0, struct.pack("<I", index))
        for _ in range(5):
            if int(await self.read(f"/p/logv/{log}/curr", 0, 4, "uint32")) == index:
                return await self.read_short(f"/p/logv/{log}/entr", 0, 16)
            await asyncio.sleep(0.05)
        raise LoraxError(f"Log cursor did not move to {index}")

    async def send_mode_command(self, command: ModeCommands) -> None:
        await self.write_short("/p/app/mc", 0, 0, bytes([int(command)]))

    async def start_heat_cycle(self) -> None:
        await self.send_mode_command(ModeCommands.HEAT_CYCLE_START)

    async def stop_heat_cycle(self) -> None:
        await self.send_mode_command(ModeCommands.HEAT_CYCLE_ABORT)

    async def boost_heat_cycle(self) -> None:
        await self.send_mode_command(ModeCommands.HEAT_CYCLE_BOOST)

    async def start_lantern(self) -> None:
        await self.write_short("/p/app/ltrn/cmd", 0, 0, bytes([1]))

    async def stop_lantern(self) -> None:
        await self.write_short("/p/app/ltrn/cmd", 0, 0, bytes([0]))

    async def set_led_brightness(self, base: int, mid: int, glass: int, logo: int) -> None:
        clamp = lambda n: max(0, min(255, int(n)))
        await self.write_short(
            "/u/app/ui/lbrt",
            0,
            0,
            bytes([clamp(base), clamp(mid), clamp(glass), clamp(logo)]),
        )

    async def get_led_brightness(self) -> dict[str, int]:
        raw = await self.read_short("/u/app/ui/lbrt", 0, 4)
        if len(raw) < 4:
            raise LoraxError(f"Brightness read returned {len(raw)} bytes")
        return {"base": raw[0], "mid": raw[1], "glass": raw[2], "logo": raw[3]}

    async def show_battery_level(self) -> None:
        await self.send_mode_command(ModeCommands.SHOW_BATTERY_LEVEL)

    async def power_off(self) -> None:
        await self.send_mode_command(ModeCommands.MASTER_OFF)

    async def factory_reset(self) -> None:
        await self.write_short("/p/app/facr", 0, 0, bytes([1]))

    async def is_stealth_mode(self) -> bool:
        stealth = await self.read("/u/app/ui/stlm", 0, 4, data_type="uint8")
        return int(stealth) == 1

    async def set_stealth_mode(self, enable: bool) -> None:
        await self.write_short("/u/app/ui/stlm", 0, 0, bytes([int(bool(enable))]))

    async def get_current_profile(self) -> int:
        return int(await self.read("/p/app/hcs", 0, 1, "int8"))

    async def set_current_profile(self, index: int) -> None:
        if index < 0 or index >= PROFILE_COUNT:
            raise ValueError(f"Profile index must be 0-{PROFILE_COUNT - 1}")
        await self.write_short("/p/app/hcs", 0, 0, bytes([int(index)]))

    async def get_current_profile_colour(self) -> Any:
        raw = await self.read_bytes_all("/p/app/thc/colr")
        decoded = cbor2.loads(raw)
        return decode_puffco_json(decoded)

    async def get_profile_colours(self, index: Optional[int] = None) -> Any:
        path = "/p/app/thc/colr" if index is None else f"/u/app/hc/{index}/colr"
        raw = await self.read_bytes_all(path)
        return decode_puffco_json(cbor2.loads(raw))

    async def _reload_if_current(self, index: int) -> None:
        try:
            current = await self.get_current_profile()
        except Exception:
            current = None
        if current == index:
            await self.set_current_profile(index)

    async def get_api_version(self) -> int:
        """Firmware API revision: the low half of /p/sys/fw/api, or the OTA
        version on firmware that predates that file (as Puffco Connect does)."""
        try:
            return int(await self.read("/p/sys/fw/api", 0, 4, "uint32")) & 0xFFFF
        except LoraxError:
            data = await self.read_short("/p/sys/fw/ver", 0, 125)
            return int(data[0]) if data else 0

    async def get_led_api(self) -> int:
        """3 for CBOR lamp colours, 2 for the older 8-byte RGBT colours.

        Same test as Puffco Connect: firmware before AF is API 2; later firmware
        is API 2 only while it still has the separate preheat-colour file.
        """
        if self._led_api is None:
            if await self.get_api_version() < PuffcoUtils.revision_string_to_number("AF"):
                self._led_api = 2
            else:
                try:
                    await self.read_short("/u/app/hc/0/phcl", 0, 8)
                    self._led_api = 2
                except LoraxError:
                    self._led_api = 3
        return self._led_api

    async def _set_profile_color_rgbt(self, index: Optional[int], hex_color: str) -> None:
        if index is None:
            index = await self.get_current_profile()
        color = rgbt_color(hex_color)
        try:
            await self.write_short("/p/app/ltrn/colr", 0, 0, color)
            await self.start_lantern()
        except Exception:
            log.debug("live lantern preview failed", exc_info=True)
        await self.write_short(f"/u/app/hc/{index}/colr", 0, 0, color)
        # Firmware through AV also keeps separate preheat and active colours.
        for suffix in ("phcl", "accl"):
            try:
                await self.write_short(f"/u/app/hc/{index}/{suffix}", 0, 0, color)
            except LoraxError:
                pass
        try:
            current = await self.get_current_profile()
        except Exception:
            current = None
        if current == index:
            for suffix in ("colr", "phcl", "accl"):
                try:
                    await self.write_short(f"/p/app/thc/{suffix}", 0, 0, color)
                except LoraxError:
                    pass

    async def set_lantern_colour(self, colour: dict) -> None:
        await self.write_cbor_full("/p/app/ltrn/colr", colour)

    async def set_profile_colour(
        self,
        index: Optional[int] = None,
        *,
        colour: dict,
        preview: bool = True,
    ) -> None:
        if index is None:
            index = await self.get_current_profile()
        # Live lantern first so the Peak shows the new colour immediately
        # instead of the factory-green leftover sitting in /p/app/ltrn/colr.
        # Do not reselect the heat profile — that plays the stock preview
        # (medium = green) over the colour we just wrote.
        if preview:
            try:
                await self.set_lantern_colour(colour)
                await self.start_lantern()
            except Exception:
                log.debug("live lantern preview failed", exc_info=True)
        await self.write_cbor_full(f"/u/app/hc/{index}/colr", colour)
        try:
            current = await self.get_current_profile()
        except Exception:
            current = None
        if current == index:
            try:
                await self.write_cbor_full("/p/app/thc/colr", colour)
            except Exception:
                log.debug("live heat-cycle colour mirror failed", exc_info=True)

    async def set_profile_solid_color(self, index: Optional[int], hex_color: str) -> None:
        if not hex_color.startswith("#"):
            hex_color = f"#{hex_color}"
        if await self.get_led_api() == 2:
            await self._set_profile_color_rgbt(index, hex_color)
            return
        await self.set_profile_colour(index, colour=solid_color_payload(hex_color))

    async def get_profile_name(self, index: Optional[int] = None) -> str:
        if index is None:
            return await self._read_and_decode("/p/app/thc/name")
        return await self._read_and_decode(f"/u/app/hc/{index}/name")

    async def set_profile_name(self, index: int, name: str) -> None:
        encoded = name.encode("utf-8")[:20] + b"\x00"
        await self.write_short(f"/u/app/hc/{index}/name", 0, 0, encoded)

    async def get_profile_temp_c(self, index: Optional[int] = None) -> float:
        if index is None:
            return float(await self.read("/p/app/thc/temp", 0, data_type="float32"))
        return float(await self.read(f"/u/app/hc/{index}/temp", 0, data_type="float32"))

    async def get_profile_temp(self, index: Optional[int] = None) -> int:
        return PuffcoUtils.c_to_f(await self.get_profile_temp_c(index))

    async def set_profile_temp_c(self, index: int, celsius: float) -> None:
        await self.write(f"/u/app/hc/{index}/temp", float(celsius), data_type="float32")

    async def set_profile_temp_f(self, index: int, fahrenheit: float) -> None:
        await self.set_profile_temp_c(index, PuffcoUtils.f_to_c(fahrenheit))

    async def get_profile_time(self, index: Optional[int] = None) -> int:
        if index is None:
            return int(round(float(await self.read("/p/app/thc/time", 0, data_type="float32"))))
        return int(round(float(await self.read(f"/u/app/hc/{index}/time", 0, data_type="float32"))))

    async def set_profile_time(self, index: int, seconds: float) -> None:
        await self.write(f"/u/app/hc/{index}/time", float(seconds), data_type="float32")

    async def get_profile_vapor(self, index: Optional[int] = None) -> float:
        path = "/p/app/thc/intn" if index is None else f"/u/app/hc/{index}/intn"
        return float(await self.read(path, 0, 4, "float32"))

    async def set_profile_vapor(self, index: int, level: float) -> None:
        await self.write(f"/u/app/hc/{index}/intn", float(level), data_type="float32")
        await self._reload_if_current(index)

    async def get_profile_boost_temp_f(self, index: Optional[int] = None) -> float:
        path = "/p/app/thc/btmp" if index is None else f"/u/app/hc/{index}/btmp"
        return float(await self.read(path, 0, 4, "float32"))

    async def set_profile_boost_temp_f(self, index: int, fahrenheit: float) -> None:
        await self.write(f"/u/app/hc/{index}/btmp", float(fahrenheit), data_type="float32")
        await self._reload_if_current(index)

    async def get_profile_boost_time(self, index: Optional[int] = None) -> float:
        path = "/p/app/thc/btim" if index is None else f"/u/app/hc/{index}/btim"
        return float(await self.read(path, 0, 4, "float32"))

    async def set_profile_boost_time(self, index: int, seconds: float) -> None:
        await self.write(f"/u/app/hc/{index}/btim", float(seconds), data_type="float32")
        await self._reload_if_current(index)

    get_current_profile_name = get_profile_name
    get_current_profile_temp = get_profile_temp
    get_current_profile_duration = get_profile_time

    async def snapshot_profile(self, index: int) -> dict[str, Any]:
        name = await self.get_profile_name(index)
        temp_c = await self.get_profile_temp_c(index)
        time_s = await self.get_profile_time(index)
        color = None
        try:
            if await self.get_led_api() == 2:
                color = rgbt_to_hex(await self.read_short(f"/u/app/hc/{index}/colr", 0, 8))
            else:
                decoded = await self.get_profile_colours(index)
                color = first_color(decoded)
        except Exception:
            log.debug("Could not decode colour for profile %s", index, exc_info=True)
        vapor = None
        try:
            vapor = await self.get_profile_vapor(index)
        except Exception:
            log.debug("Could not read vapor for profile %s", index, exc_info=True)
        boost_temp_f = None
        boost_time = None
        try:
            boost_temp_f = round(await self.get_profile_boost_temp_f(index), 1)
            boost_time = round(await self.get_profile_boost_time(index), 1)
        except Exception:
            log.debug("Could not read boost for profile %s", index, exc_info=True)
        return {
            "index": index,
            "name": name or f"Profile {index + 1}",
            "temp_c": round(temp_c, 1),
            "temp_f": PuffcoUtils.c_to_f(temp_c),
            "time": time_s,
            "color": color,
            "vapor": None if vapor is None else vapor_name_for(vapor),
            "vapor_level": vapor,
            "boost_temp_f": boost_temp_f,
            "boost_time": boost_time,
        }

    async def snapshot(self, *, include_profiles: bool = True) -> dict[str, Any]:
        async def _optional(coro, default=None):
            try:
                return await coro
            except Exception:
                log.debug("optional snapshot field failed", exc_info=True)
                return default

        state = await self.get_operating_state()
        charge = await self.get_battery_charge_state()
        source = await _optional(self.get_battery_charge_source())
        chamber = await self.get_chamber_type()
        current = await self.get_current_profile()
        info: dict[str, Any] = {}
        try:
            info = await self.get_device_info()
        except Exception:
            log.debug("product info failed", exc_info=True)

        heater_c = await self.get_heater_temp_c()
        birthday = await _optional(self.get_device_birthday())
        data: dict[str, Any] = {
            "connected": True,
            "device_name": (await self.get_device_name()) or self.advertised_name or "Peak Pro",
            "device_mac": self.address or self.device_mac or "",
            "product": info,
            "serial": await self.get_serial_number(),
            "firmware": await self.get_software_version(),
            "bootloader": await self.get_bootloader_version(),
            "uptime_seconds": await self.get_uptime(),
            "battery_capacity_raw": await _optional(self.get_battery_capacity()),
            "max_charge": await _optional(self.get_max_charge()),
            "led_api": await _optional(self.get_led_api()),
            "battery": await self.get_battery_level(),
            "charge_state": CHARGE_STATE_LABELS.get(charge, f"Unknown ({int(charge)})"),
            "charge_state_id": int(charge),
            "charge_source": CHARGE_SOURCE_LABELS.get(source, "") if source is not None else "",
            "charge_source_id": int(source) if source is not None else -1,
            "charge_eta_s": await self._charge_eta(charge),
            "chamber": CHAMBER_LABELS.get(chamber, f"Unknown ({int(chamber)})"),
            "chamber_id": int(chamber),
            "operating_state": OPERATING_STATE_LABELS.get(state, f"Unknown ({int(state)})"),
            "operating_state_id": int(state),
            "heater_temp_c": heater_c,
            "heater_temp_f": None if heater_c is None else PuffcoUtils.c_to_f(heater_c),
            **(await self._heat_timer(state)),
            "stealth": await _optional(self.is_stealth_mode(), False),
            "dabs_remaining": await _optional(self.get_approx_dabs_remaining(), 0),
            "dabs_per_day": await _optional(self.get_dabs_per_day(), 0),
            # None on failure, not 0 — a failed read used to poison local
            # history by looking like a brand-new Peak with zero dabs.
            "total_dabs": await _optional(self.get_total_dabs(), None),
            "birthday": birthday,
            "birthday_label": PuffcoUtils.format_birthday(birthday),
            "lantern_timeout": await _optional(self.get_lantern_timeout()),
            "brightness": await _optional(self.get_led_brightness()),
            "current_profile": current,
            "profiles": [],
        }
        data["uptime"] = PuffcoUtils.format_uptime(data["uptime_seconds"])
        if include_profiles:
            profiles = []
            for i in range(PROFILE_COUNT):
                try:
                    profiles.append(await self.snapshot_profile(i))
                except Exception:
                    log.debug("profile %s snapshot failed", i, exc_info=True)
                    profiles.append(
                        {
                            "index": i,
                            "name": f"Profile {i + 1}",
                            "temp_c": 0,
                            "temp_f": 0,
                            "time": 0,
                            "color": None,
                            "vapor": None,
                            "vapor_level": None,
                            "boost_temp_f": None,
                            "boost_time": None,
                        }
                    )
            data["profiles"] = profiles
        return data

    async def _heat_timer(self, state: Any) -> dict[str, float | None]:
        """Seconds spent in the current heat-cycle state and its planned length.

        Only read while heating: outside a cycle the total is infinite and two
        extra reads per idle poll would be wasted radio time.
        """
        timer: dict[str, float | None] = {"state_elapsed_s": None, "state_total_s": None}
        if int(state) not in HEAT_CYCLE_STATES:
            return timer
        try:
            elapsed = float(await self.read("/p/app/stat/elap", 0, 4, "float32"))
            total = float(await self.read("/p/app/stat/tott", 0, 4, "float32"))
        except Exception:
            log.debug("heat timer read failed", exc_info=True)
            return timer
        timer["state_elapsed_s"] = elapsed if math.isfinite(elapsed) else None
        timer["state_total_s"] = total if math.isfinite(total) and total > 0 else None
        return timer

    async def poll_fast(self) -> dict[str, Any]:
        state = await self.get_operating_state()
        charge = await self.get_battery_charge_state()
        heater_c = await self.get_heater_temp_c()
        try:
            source = await self.get_battery_charge_source()
        except Exception:
            source = None
        return {
            "connected": True,
            "battery": await self.get_battery_level(),
            "charge_state": CHARGE_STATE_LABELS.get(charge, f"Unknown ({int(charge)})"),
            "charge_state_id": int(charge),
            "charge_source": CHARGE_SOURCE_LABELS.get(source, "") if source is not None else "",
            "charge_source_id": int(source) if source is not None else -1,
            "charge_eta_s": await self._charge_eta(charge),
            "operating_state": OPERATING_STATE_LABELS.get(state, f"Unknown ({int(state)})"),
            "operating_state_id": int(state),
            "current_profile": await self.get_current_profile(),
            "heater_temp_c": heater_c,
            "heater_temp_f": None if heater_c is None else PuffcoUtils.c_to_f(heater_c),
            **(await self._heat_timer(state)),
        }
