"""Small BlueZ helpers: stop discovery and connect without a live scan."""

from __future__ import annotations

import asyncio
import logging

from dbus_fast import BusType, Variant
from dbus_fast.aio import MessageBus

log = logging.getLogger("puffcoble.bluez")

BLUEZ = "org.bluez"
ADAPTER = "/org/bluez/hci0"


def _device_path(address: str) -> str:
    return f"{ADAPTER}/dev_{address.replace(':', '_').upper()}"


async def _bus():
    return await MessageBus(bus_type=BusType.SYSTEM).connect()


async def stop_discovery() -> None:
    bus = await _bus()
    try:
        introspect = await bus.introspect(BLUEZ, ADAPTER)
        obj = bus.get_proxy_object(BLUEZ, ADAPTER, introspect)
        adapter = obj.get_interface("org.bluez.Adapter1")
        try:
            await adapter.call_stop_discovery()
            log.info("Stopped BlueZ discovery")
        except Exception as exc:
            log.debug("StopDiscovery: %s", exc)
    finally:
        bus.disconnect()


async def trust_device(address: str) -> None:
    bus = await _bus()
    try:
        path = _device_path(address)
        introspect = await bus.introspect(BLUEZ, path)
        obj = bus.get_proxy_object(BLUEZ, path, introspect)
        props = obj.get_interface("org.freedesktop.DBus.Properties")
        await props.call_set("org.bluez.Device1", "Trusted", Variant("b", True))
        log.info("Trusted %s", address)
    except Exception as exc:
        log.debug("trust %s: %s", address, exc)
    finally:
        bus.disconnect()


async def _device_flag(address: str, name: str) -> bool:
    bus = await _bus()
    try:
        path = _device_path(address)
        introspect = await bus.introspect(BLUEZ, path)
        obj = bus.get_proxy_object(BLUEZ, path, introspect)
        props = obj.get_interface("org.freedesktop.DBus.Properties")
        value = await props.call_get("org.bluez.Device1", name)
        return bool(getattr(value, "value", value))
    except Exception:
        return False
    finally:
        bus.disconnect()


async def device_connected(address: str) -> bool:
    return await _device_flag(address, "Connected")


async def device_paired(address: str) -> bool:
    return await _device_flag(address, "Paired") or await _device_flag(address, "Bonded")


async def wait_services_resolved(address: str, timeout: float = 8.0) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if await _device_flag(address, "ServicesResolved"):
            return True
        await asyncio.sleep(0.15)
    return await _device_flag(address, "ServicesResolved")


async def wait_bonded(address: str, timeout: float = 8.0) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if await device_paired(address):
            return True
        await asyncio.sleep(0.25)
    return await device_paired(address)


async def bluez_connect(address: str) -> None:
    """Call Device1.Connect the same way bluetoothctl does."""
    bus = await _bus()
    try:
        path = _device_path(address)
        introspect = await bus.introspect(BLUEZ, path)
        obj = bus.get_proxy_object(BLUEZ, path, introspect)
        dev = obj.get_interface("org.bluez.Device1")
        await dev.call_connect()
        log.info("BlueZ Connect succeeded for %s", address)
    finally:
        bus.disconnect()


async def pair_device(address: str) -> None:
    """LE Just Works pair. Lorax command writes stall until the link is bonded."""
    bus = await _bus()
    try:
        path = _device_path(address)
        introspect = await bus.introspect(BLUEZ, path)
        obj = bus.get_proxy_object(BLUEZ, path, introspect)
        dev = obj.get_interface("org.bluez.Device1")
        await dev.call_pair()
        log.info("Paired %s", address)
    finally:
        bus.disconnect()
