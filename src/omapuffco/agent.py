"""BlueZ Just Works pairing agent.

GNOME/blueman cancel unsolicited Pair() requests from a systemd service
(AuthenticationCanceled). This agent auto-accepts LE Just Works so the
Peak can bond after the Lorax VERSION_CHAR read.
"""

from __future__ import annotations

import logging

from dbus_fast import BusType
from dbus_fast.aio import MessageBus
from dbus_fast.service import ServiceInterface, method

log = logging.getLogger("puffcoble.agent")

AGENT_PATH = "/puffco/agent"
BLUEZ = "org.bluez"
AGENT_MANAGER = "org.bluez.AgentManager1"


class JustWorksAgent(ServiceInterface):
    def __init__(self):
        super().__init__("org.bluez.Agent1")

    @method()
    def Release(self):
        log.debug("agent Release")

    @method()
    def Cancel(self):
        log.debug("agent Cancel")

    @method()
    def RequestPinCode(self, device: "o") -> "s":
        log.info("agent PIN requested for %s", device)
        return "000000"

    @method()
    def DisplayPinCode(self, device: "o", pincode: "s"):
        log.info("agent display PIN %s for %s", pincode, device)

    @method()
    def RequestPasskey(self, device: "o") -> "u":
        log.info("agent passkey requested for %s", device)
        return 0

    @method()
    def DisplayPasskey(self, device: "o", passkey: "u", entered: "q"):
        log.info("agent display passkey %s for %s", passkey, device)

    @method()
    def RequestConfirmation(self, device: "o", passkey: "u"):
        log.info("agent confirming passkey %s for %s", passkey, device)

    @method()
    def RequestAuthorization(self, device: "o"):
        log.info("agent authorizing %s", device)

    @method()
    def AuthorizeService(self, device: "o", uuid: "s"):
        log.info("agent authorizing service %s on %s", uuid, device)


class PairingAgent:
    def __init__(self):
        self._bus: MessageBus | None = None
        self._registered = False

    async def start(self) -> None:
        if self._registered:
            return
        self._bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        self._bus.export(AGENT_PATH, JustWorksAgent())
        introspect = await self._bus.introspect(BLUEZ, "/org/bluez")
        obj = self._bus.get_proxy_object(BLUEZ, "/org/bluez", introspect)
        manager = obj.get_interface(AGENT_MANAGER)
        try:
            await manager.call_register_agent(AGENT_PATH, "NoInputNoOutput")
        except Exception as exc:
            log.debug("RegisterAgent: %s", exc)
            try:
                await manager.call_unregister_agent(AGENT_PATH)
            except Exception:
                pass
            await manager.call_register_agent(AGENT_PATH, "NoInputNoOutput")
        try:
            await manager.call_request_default_agent(AGENT_PATH)
            log.info("Registered default NoInputNoOutput (Just Works) Bluetooth agent")
        except Exception as exc:
            log.warning("Could not become default Bluetooth agent: %s", exc)
        self._registered = True

    async def stop(self) -> None:
        if not self._bus:
            return
        try:
            introspect = await self._bus.introspect(BLUEZ, "/org/bluez")
            obj = self._bus.get_proxy_object(BLUEZ, "/org/bluez", introspect)
            manager = obj.get_interface(AGENT_MANAGER)
            await manager.call_unregister_agent(AGENT_PATH)
        except Exception:
            pass
        try:
            self._bus.disconnect()
        except Exception:
            pass
        self._bus = None
        self._registered = False

    async def __aenter__(self) -> "PairingAgent":
        await self.start()
        return self

    async def __aexit__(self, *_exc) -> None:
        await self.stop()
