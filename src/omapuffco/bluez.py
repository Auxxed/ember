"""Small BlueZ helpers: adapter discovery, and connecting without a live scan."""

from __future__ import annotations

import asyncio
import logging

from dbus_fast import BusType, Variant
from dbus_fast.aio import MessageBus

from .paths import load_config

log = logging.getLogger("puffcoble.bluez")

BLUEZ = "org.bluez"
ADAPTER_IFACE = "org.bluez.Adapter1"
DEVICE_IFACE = "org.bluez.Device1"

_adapter_path: str | None = None


async def _bus():
    return await MessageBus(bus_type=BusType.SYSTEM).connect()


async def _managed_objects(bus) -> dict:
    introspect = await bus.introspect(BLUEZ, "/")
    obj = bus.get_proxy_object(BLUEZ, "/", introspect)
    manager = obj.get_interface("org.freedesktop.DBus.ObjectManager")
    return await manager.call_get_managed_objects()


def _normalize(name: str) -> str:
    name = (name or "").strip()
    if not name:
        return ""
    return name if name.startswith("/") else f"/org/bluez/{name}"


async def _resolve_adapter(bus) -> str:
    objects = await _managed_objects(bus)
    adapters = sorted(p for p, ifaces in objects.items() if ADAPTER_IFACE in ifaces)
    if not adapters:
        raise RuntimeError("No Bluetooth adapter found. Is the radio enabled?")

    want = _normalize(str(load_config().get("adapter") or ""))
    if want:
        if want not in adapters:
            raise RuntimeError(
                f"Configured Bluetooth adapter {want} not found. "
                f"Available: {', '.join(adapters)}"
            )
        return want

    for path in adapters:
        powered = objects[path][ADAPTER_IFACE].get("Powered")
        if powered is not None and powered.value:
            return path
    return adapters[0]


async def list_adapters() -> list[dict]:
    """Every adapter BlueZ knows, by hciN name, and whether it's powered on."""
    bus = await _bus()
    try:
        objects = await _managed_objects(bus)
    finally:
        bus.disconnect()
    found = []
    for path in sorted(p for p, ifaces in objects.items() if ADAPTER_IFACE in ifaces):
        powered = objects[path][ADAPTER_IFACE].get("Powered")
        found.append({"name": path.rsplit("/", 1)[-1], "powered": bool(powered is not None and powered.value)})
    return found


async def adapter_path(bus=None) -> str:
    """Resolve the adapter object path once, then remember it.

    hci0 is the common case, not a guarantee — a USB dongle or a second
    radio lands on hci1+, and assuming hci0 made every connect on such a
    machine fail with an unexplained InterfaceNotFoundError.
    """
    global _adapter_path
    if _adapter_path:
        return _adapter_path
    if bus is not None:
        _adapter_path = await _resolve_adapter(bus)
    else:
        own = await _bus()
        try:
            _adapter_path = await _resolve_adapter(own)
        finally:
            own.disconnect()
    log.info("Using Bluetooth adapter %s", _adapter_path)
    return _adapter_path


async def adapter_name() -> str | None:
    """Short name ("hci1") for bleak's BlueZ backend arguments."""
    try:
        return (await adapter_path()).rsplit("/", 1)[-1]
    except Exception as exc:
        log.debug("adapter lookup failed: %r", exc)
        return None


async def bleak_args() -> dict:
    """Keep bleak on the same adapter as these D-Bus helpers."""
    name = await adapter_name()
    return {"bluez": {"adapter": name}} if name else {}


async def _device_path(bus, address: str) -> str:
    """Find the device object wherever BlueZ filed it."""
    suffix = f"dev_{address.replace(':', '_').upper()}"
    objects = await _managed_objects(bus)
    for path, ifaces in objects.items():
        if DEVICE_IFACE in ifaces and path.rsplit("/", 1)[-1] == suffix:
            return path
    return f"{await adapter_path(bus)}/{suffix}"


async def stop_discovery() -> None:
    """Best effort — this runs in the connect path and must not abort it."""
    try:
        bus = await _bus()
    except Exception as exc:
        log.debug("stop_discovery bus: %r", exc)
        return
    try:
        path = await adapter_path(bus)
        introspect = await bus.introspect(BLUEZ, path)
        obj = bus.get_proxy_object(BLUEZ, path, introspect)
        adapter = obj.get_interface(ADAPTER_IFACE)
        await adapter.call_stop_discovery()
        log.info("Stopped BlueZ discovery")
    except Exception as exc:
        log.debug("StopDiscovery: %s", exc)
    finally:
        bus.disconnect()


async def trust_device(address: str) -> None:
    bus = await _bus()
    try:
        path = await _device_path(bus, address)
        introspect = await bus.introspect(BLUEZ, path)
        obj = bus.get_proxy_object(BLUEZ, path, introspect)
        props = obj.get_interface("org.freedesktop.DBus.Properties")
        await props.call_set(DEVICE_IFACE, "Trusted", Variant("b", True))
        log.info("Trusted %s", address)
    except Exception as exc:
        log.debug("trust %s: %s", address, exc)
    finally:
        bus.disconnect()


async def _device_flag(address: str, name: str) -> bool:
    bus = await _bus()
    try:
        path = await _device_path(bus, address)
        introspect = await bus.introspect(BLUEZ, path)
        obj = bus.get_proxy_object(BLUEZ, path, introspect)
        props = obj.get_interface("org.freedesktop.DBus.Properties")
        value = await props.call_get(DEVICE_IFACE, name)
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
        path = await _device_path(bus, address)
        introspect = await bus.introspect(BLUEZ, path)
        obj = bus.get_proxy_object(BLUEZ, path, introspect)
        dev = obj.get_interface(DEVICE_IFACE)
        await dev.call_connect()
        log.info("BlueZ Connect succeeded for %s", address)
    finally:
        bus.disconnect()


async def pair_device(address: str) -> None:
    """LE Just Works pair. Lorax command writes stall until the link is bonded."""
    bus = await _bus()
    try:
        path = await _device_path(bus, address)
        introspect = await bus.introspect(BLUEZ, path)
        obj = bus.get_proxy_object(BLUEZ, path, introspect)
        dev = obj.get_interface(DEVICE_IFACE)
        await dev.call_pair()
        log.info("Paired %s", address)
    finally:
        bus.disconnect()
