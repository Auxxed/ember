"""ember — Peak Pro companion CLI. No args launches the GUI."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from . import __version__
from .paths import load_config
from .rpc import DaemonNotRunning, rpc
from .service import ensure_daemon


def _ensure() -> None:
    try:
        ensure_daemon()
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc


async def call(cmd: str, args: dict | None = None, timeout: float = 30.0) -> Any:
    _ensure()
    try:
        return await rpc(cmd, args, timeout=timeout)
    except DaemonNotRunning as exc:
        raise SystemExit(str(exc)) from exc
    except TimeoutError as exc:
        raise SystemExit(
            "Timed out talking to emberd. If a connect is already running, wait for it to finish."
        ) from exc


def _profile_temp(data: dict, units: str) -> str:
    current = data.get("current_profile", 0)
    for p in data.get("profiles") or []:
        if p.get("index") == current:
            if units == "C":
                return f"{p.get('temp_c')}°C"
            return f"{p.get('temp_f')}°F"
    return ""


def print_status(data: dict, as_json: bool, units: str | None = None) -> None:
    if as_json:
        print(json.dumps(data, indent=2, default=str))
        return
    units = units or load_config().get("units") or "F"
    if not data.get("connected"):
        print("Disconnected")
        return
    product = (data.get("product") or {}).get("label") or "Peak Pro"
    print(f"{data.get('device_name')}  ·  {product}")
    print(f"  {data.get('device_mac')}")
    heat = ""
    if data.get("heater_temp_f") is not None:
        if units == "C":
            heat = f"   chamber {data.get('heater_temp_c')}°C"
        else:
            heat = f"   chamber {data.get('heater_temp_f')}°F"
    print(
        f"  {data.get('operating_state')}   battery {data.get('battery')}%"
        f"  {data.get('charge_state') or ''}{heat}"
    )
    print(
        f"  chamber {data.get('chamber')}   stealth {data.get('stealth')}"
        f"   lantern {data.get('lantern')}"
    )
    print(
        f"  firmware {data.get('firmware')}   serial {data.get('serial')}"
        f"   uptime {data.get('uptime')}"
    )
    telemetry = data.get("telemetry") or {}
    print(
        f"  dabs {data.get('total_dabs')} total   ~{data.get('dabs_remaining')} left"
        f"   {data.get('dabs_per_day')}/day"
        + (
            f"   ({telemetry.get('today', 0)} today, {telemetry.get('this_month', 0)} this month)"
            if telemetry
            else ""
        )
    )
    current = data.get("current_profile", 0)
    for p in data.get("profiles") or []:
        mark = "*" if p.get("index") == current else " "
        if units == "C":
            temp = f"{p.get('temp_c')}°C"
        else:
            temp = f"{p.get('temp_f')}°F"
        print(
            f" {mark} P{p.get('index')}: {str(p.get('name') or ''):<16} "
            f"{temp}  {p.get('time')}s  {p.get('color') or ''}"
        )


SPARK_BLOCKS = " ▁▂▃▄▅▆▇█"


def print_stats(data: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, indent=2, default=str))
        return
    print(f"Total dabs      {data.get('total_dabs', 0)}  (lifetime, reported by device)")
    print(f"Dabs remaining  ~{data.get('dabs_remaining', 0)} (approx. left in current chamber)")
    print(f"Dabs / day      {data.get('dabs_per_day', 0)} (device running average)")
    print()
    print(f"Today           {data.get('today', 0)}")
    print(f"This week       {data.get('this_week', 0)}")
    print(f"This month      {data.get('this_month', 0)}")
    print(f"This year       {data.get('this_year', 0)}")
    daily = data.get("daily") or []
    if daily:
        counts = [d.get("count", 0) for d in daily]
        peak = max(counts) or 1
        spark = "".join(
            SPARK_BLOCKS[min(len(SPARK_BLOCKS) - 1, int(c / peak * (len(SPARK_BLOCKS) - 1)))]
            for c in counts
        )
        print(f"\nLast {len(daily)} days   {spark}")
    since = data.get("tracking_since")
    if since:
        import datetime as _dt

        print(f"\nTracked locally since {_dt.datetime.fromtimestamp(since):%Y-%m-%d}")
    else:
        print("\nNo local dab history yet — connect and take a dab to start tracking.")


def print_waybar(data: dict) -> None:
    units = load_config().get("units") or "F"
    connected = bool(data.get("connected"))
    state = data.get("operating_state") or "Disconnected"
    state_id = int(data.get("operating_state_id") or -1)
    battery = data.get("battery") or 0
    if units == "C" and data.get("heater_temp_c") is not None:
        temp = f"{int(round(float(data['heater_temp_c'])))}°C"
    elif data.get("heater_temp_f") is not None:
        temp = f"{int(data['heater_temp_f'])}°F"
    else:
        temp = _profile_temp(data, units)
    css = "disconnected"
    if not connected:
        text = "Peak"
    elif state_id == 7:
        css, text = "preheat", f"{temp or 'heat'} ↑"
    elif state_id == 8:
        css, text = "ready", f"{temp or 'ready'} ●"
    elif state_id == 9:
        css, text = "cool", f"{temp or 'cool'} ↓"
    else:
        css, text = "idle", f"{battery}%"
        if temp:
            text = f"{temp}  {battery}%"
    tooltip = state if not connected else f"{state} · {battery}% · {temp}".strip(" ·")
    print(
        json.dumps(
            {
                "text": text,
                "tooltip": tooltip,
                "class": css,
                "alt": state,
                "percentage": battery,
            }
        )
    )


def launch_gui() -> int:
    from .gui.app import run

    return run()


async def async_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ember",
        description="Ember — Peak Pro companion for Linux",
    )
    parser.add_argument("--json", action="store_true", help="Print raw JSON")
    parser.add_argument("--version", action="version", version=f"ember {__version__}")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("gui", help="Open the desktop app")
    sub.add_parser("daemon", help="Run the BLE daemon in the foreground")
    sub.add_parser("ping")
    scan = sub.add_parser("scan")
    scan.add_argument("--timeout", type=float, default=6)
    conn = sub.add_parser("connect")
    conn.add_argument("--name", default="")
    conn.add_argument("--mac", default="")
    sub.add_parser("disconnect")
    sub.add_parser("status")
    sub.add_parser("refresh")
    sub.add_parser("waybar", help="One-shot Waybar JSON")
    sub.add_parser("stats", help="Dab telemetry: today/week/month/year + lifetime")

    heat = sub.add_parser("heat")
    heat.add_argument("action", choices=["start", "stop", "boost"])

    lantern = sub.add_parser("lantern")
    lantern.add_argument("action", choices=["on", "off"])

    bright = sub.add_parser("brightness")
    bright.add_argument("level", type=int, nargs="?", help="0-255 applied to all zones")
    bright.add_argument("--base", type=int)
    bright.add_argument("--mid", type=int)
    bright.add_argument("--glass", type=int)
    bright.add_argument("--logo", type=int)

    prof = sub.add_parser("profile")
    prof.add_argument("index", type=int, nargs="?")
    prof.add_argument("--name")
    prof.add_argument("--temp-f", type=float)
    prof.add_argument("--temp-c", type=float)
    prof.add_argument("--time", type=float)
    prof.add_argument("--color")

    anim = sub.add_parser("anim")
    anim.add_argument("name", choices=["solid", "breathing", "rising", "circling", "heat"])
    anim.add_argument("--index", type=int)
    anim.add_argument("--color", action="append", dest="colors")

    stealth = sub.add_parser("stealth")
    stealth.add_argument("action", choices=["on", "off"])

    units = sub.add_parser("units")
    units.add_argument("value", choices=["F", "C", "f", "c"])

    sub.add_parser("battery")
    sub.add_parser("sleep")
    sub.add_parser("off")
    reset = sub.add_parser("factory-reset")
    reset.add_argument("--yes", action="store_true")

    args = parser.parse_args(argv)
    raw = args.json
    cmd = args.cmd

    if cmd is None or cmd == "gui":
        return launch_gui()
    if cmd == "daemon":
        from .daemon import main as daemon_main

        daemon_main()
        return 0

    if cmd == "ping":
        print_status_raw = await call("ping")
        print(json.dumps(print_status_raw, indent=2) if raw else f"emberd pid {print_status_raw.get('pid')}")
    elif cmd == "scan":
        result = await call("scan", {"timeout": args.timeout}, timeout=max(45, args.timeout + 25))
        if raw:
            print(json.dumps(result, indent=2))
        else:
            devices = result.get("devices") or []
            if not devices:
                print("No Peak Pro found. Wake it and keep it near the PC.")
                return 0
            for d in devices:
                rssi = f"  rssi {d['rssi']}" if d.get("rssi") else ""
                print(f"{d.get('name') or 'Peak Pro':<20} {d.get('address')}{rssi}")
    elif cmd == "connect":
        print_status(
            await call(
                "connect",
                {"device_name": args.name, "device_mac": args.mac},
                timeout=90,
            ),
            raw,
        )
    elif cmd == "disconnect":
        print_status(await call("disconnect"), raw)
    elif cmd == "status":
        print_status(await call("status"), raw)
    elif cmd == "refresh":
        print_status(await call("refresh", timeout=20), raw)
    elif cmd == "waybar":
        try:
            data = await call("status", timeout=5)
        except Exception:
            data = {"connected": False}
        print_waybar(data)
    elif cmd == "stats":
        print_stats(await call("stats", timeout=10), raw)
    elif cmd == "heat":
        await call(f"{args.action}_heat")
        print_status(await call("status"), raw)
    elif cmd == "lantern":
        await call("start_lantern" if args.action == "on" else "stop_lantern")
        print_status(await call("status"), raw)
    elif cmd == "brightness":
        payload = {}
        if args.level is not None:
            payload["level"] = args.level
        for key in ("base", "mid", "glass", "logo"):
            value = getattr(args, key)
            if value is not None:
                payload[key] = value
        if not payload:
            raise SystemExit("Give a level or --base/--mid/--glass/--logo")
        print(json.dumps(await call("set_brightness", payload), indent=2 if raw else None, default=str))
    elif cmd == "profile":
        if args.index is None:
            print_status(await call("status"), raw)
            return 0
        await call("set_profile", {"index": args.index})
        if args.name:
            await call("set_profile_name", {"index": args.index, "name": args.name})
        if args.temp_c is not None:
            await call("set_profile_temp", {"index": args.index, "celsius": args.temp_c})
        if args.temp_f is not None:
            await call("set_profile_temp", {"index": args.index, "fahrenheit": args.temp_f})
        if args.time is not None:
            await call("set_profile_time", {"index": args.index, "seconds": args.time})
        if args.color:
            await call("set_profile_color", {"index": args.index, "hex": args.color})
        print_status(await call("status"), raw)
    elif cmd == "anim":
        await call(
            "set_animation",
            {"anim": args.name, "index": args.index, "colors": args.colors or ["#ffffff"]},
        )
        print_status(await call("status"), raw)
    elif cmd == "stealth":
        await call("set_stealth", {"enable": args.action == "on"})
        print_status(await call("status"), raw)
    elif cmd == "units":
        from .paths import save_config

        cfg = load_config()
        cfg["units"] = args.value.upper()
        save_config(cfg)
        print(f"Units set to °{cfg['units']}")
    elif cmd == "battery":
        await call("show_battery")
    elif cmd == "sleep":
        await call("sleep")
    elif cmd == "off":
        await call("power_off")
    elif cmd == "factory-reset":
        if not args.yes:
            raise SystemExit("Refusing to factory reset without --yes")
        await call("factory_reset")
    return 0


def main(argv: list[str] | None = None) -> None:
    argv = argv if argv is not None else sys.argv[1:]
    if argv and argv[0] == "daemon":
        from .daemon import main as daemon_main

        daemon_main()
        return
    if not argv or argv[0] == "gui":
        raise SystemExit(launch_gui())
    raise SystemExit(asyncio.run(async_main(argv)))


if __name__ == "__main__":
    main()
