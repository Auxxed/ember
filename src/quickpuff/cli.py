"""quickpuff — Peak Pro companion CLI. No args prints usage."""

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
    except RuntimeError as exc:
        # A command the daemon refused (not connected, bad value): say why, no traceback.
        raise SystemExit(str(exc)) from exc
    except TimeoutError as exc:
        raise SystemExit(
            "Timed out talking to the QuickPuff daemon. If a connect is already running, wait for it to finish."
        ) from exc


def print_sessions(result: dict, units: str = "F") -> None:
    from datetime import datetime

    rows = result.get("sessions") or []
    if not rows:
        print("No sessions yet.")
        return
    for row in rows:
        bits = [datetime.fromtimestamp(row["ts"]).strftime("%b %d %H:%M"), f"{row['key']:<7}"]
        if row.get("profile") is not None:
            bits.append("custom" if row["profile"] < 0 else f"P{row['profile']}")
        temp = row.get("temp_c") if units == "C" else row.get("temp_f")
        if temp is not None:
            bits.append(f"{temp}°{units}")
        if row.get("preheat_s"):
            bits.append(f"heated in {round(row['preheat_s'])}s")
        line = "  ".join(bits)
        if row.get("note"):
            line += f"  — {row['note']}"
        print(line)


LOW_HEAT_BATTERY = 10


def low_battery_heat_warning(data: dict) -> str | None:
    """The Peak refuses to heat near 5%; say so before it silently does."""
    if not data.get("connected"):
        return None
    try:
        battery = int(data.get("battery"))
    except (TypeError, ValueError):
        return None
    plugged = (data.get("charge_source") or "Unplugged") != "Unplugged"
    if plugged or battery > LOW_HEAT_BATTERY:
        return None
    return f"Battery {battery}%: the Peak may refuse to heat. Plug it in first."


def format_eta(seconds: Any) -> str:
    try:
        minutes = max(1, int(round(float(seconds) / 60)))
    except (TypeError, ValueError):
        return ""
    if minutes < 60:
        return f"{minutes} min"
    hours, rest = divmod(minutes, 60)
    return f"{hours} h {rest} min" if rest else f"{hours} h"


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
        if data.get("resting"):
            print(f"Resting to save battery ({data.get('battery')}% at the last check); reconnecting now")
        else:
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
    source = data.get("charge_source") or ""
    charge = data.get("charge_state") or ""
    if source and source != "Unplugged" and source not in charge:
        charge = f"{charge} {source}".strip()
    if data.get("charge_eta_s"):
        charge = f"{charge}, full in {format_eta(data['charge_eta_s'])}"
    health = ""
    if data.get("battery_health_pct") is not None:
        health = (
            f"   health {data['battery_health_pct']}%"
            f" ({data.get('battery_capacity_mah')} of {data.get('battery_rated_mah')} mAh)"
        )
    if data.get("max_charge") is not None and float(data["max_charge"]) < 100:
        health += f"   max charge {round(float(data['max_charge']))}%"
    print(
        f"  {data.get('operating_state')}   battery {data.get('battery')}%"
        f"  {charge}{heat}{health}"
    )
    timeout = data.get("lantern_timeout")
    lantern = data.get("lantern")
    if timeout:
        try:
            seconds = int(round(float(timeout)))
            if seconds >= 3600 and seconds % 3600 == 0:
                pretty = f"{seconds // 3600}h"
            elif seconds >= 60 and seconds % 60 == 0:
                pretty = f"{seconds // 60}m"
            else:
                pretty = f"{seconds}s"
            lantern = f"{lantern}  off after {pretty}"
        except (TypeError, ValueError):
            pass
    print(
        f"  chamber {data.get('chamber')}   stealth {data.get('stealth')}"
        f"   lantern {lantern}   saver {data.get('battery_saver')}   qtip {data.get('qtip_reminder')}"
    )
    extra = ""
    if data.get("birthday_label"):
        extra = f"   since {data.get('birthday_label')}"
    print(
        f"  firmware {data.get('firmware')}   serial {data.get('serial')}"
        f"   uptime {data.get('uptime')}{extra}"
    )
    telemetry = data.get("telemetry") or {}
    clean = ""
    if data.get("clean_every"):
        clean = "   clean due" if data.get("clean_due") else f"   clean {data.get('clean_remaining')} left"
    tracked = ""
    if telemetry:
        tracked = f"   ({telemetry.get('today', 0)} today, {telemetry.get('this_month', 0)} this month)"
    print(
        f"  dabs {data.get('total_dabs')} total   ~{data.get('dabs_remaining')} left on charge"
        f"   {data.get('dabs_per_day')}/day{clean}{tracked}"
    )
    current = data.get("current_profile", 0)
    for p in data.get("profiles") or []:
        mark = "*" if p.get("index") == current else " "
        if units == "C":
            temp = f"{p.get('temp_c')}°C"
        else:
            temp = f"{p.get('temp_f')}°F"
        vapor = p.get("vapor") or ""
        boost_t = p.get("boost_temp_f")
        boost_s = p.get("boost_time")
        boost = ""
        if boost_t is not None or boost_s is not None:
            try:
                bt = int(round(float(boost_t or 0)))
                bs = int(round(float(boost_s or 0)))
                boost = f"+{bt}°/+{bs}s"
            except (TypeError, ValueError):
                boost = ""
        print(
            f" {mark} P{p.get('index')}: {str(p.get('name') or ''):<16} "
            f"{temp}  {p.get('time')}s  {vapor:<8}  {boost:<10}  {p.get('color') or ''}"
        )


SPARK_BLOCKS = " ▁▂▃▄▅▆▇█"


def print_stats(data: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, indent=2, default=str))
        return
    print(f"Total dabs      {data.get('total_dabs', 0)}  (lifetime, reported by device)")
    print(f"Dabs remaining  ~{data.get('dabs_remaining', 0)} (approx. left on this charge)")
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

        print(f"\nTracking since {_dt.datetime.fromtimestamp(since):%Y-%m-%d}")
    else:
        print("\nNo local dab history yet — connect and take a dab to start tracking.")


def _seconds_left(data: dict) -> int | None:
    elapsed, total = data.get("state_elapsed_s"), data.get("state_total_s")
    if elapsed is None or total is None:
        return None
    return max(0, int(round(float(total) - float(elapsed))))


def print_waybar(data: dict) -> None:
    units = load_config().get("units") or "F"
    connected = bool(data.get("connected"))
    state = data.get("operating_state") or "Disconnected"
    state_id = int(data.get("operating_state_id") or -1)
    battery = data.get("battery") or 0
    plugged = (data.get("charge_source") or "Unplugged") != "Unplugged"
    battery_text = f" {battery}%" if plugged else f"{battery}%"
    if units == "C" and data.get("heater_temp_c") is not None:
        temp = f"{int(round(float(data['heater_temp_c'])))}°C"
    elif data.get("heater_temp_f") is not None:
        temp = f"{int(data['heater_temp_f'])}°F"
    else:
        temp = _profile_temp(data, units)
    css = "disconnected"
    resting = not connected and bool(data.get("resting"))
    # Handed to another computer: the Peak is fine, it just isn't ours right
    # now, so show the last reading rather than a dead-looking bar. It borrows
    # the resting style, which already means "let go, last reading shown".
    handed_off = not connected and not resting and bool(data.get("handed_off"))
    if resting or handed_off:
        css, text = "resting", battery_text if battery else "Peak"
    elif not connected:
        text = "Peak"
    elif state_id == 7:
        css, text = "preheat", f"{temp or 'heat'} ↑"
        if (left := _seconds_left(data)) is not None:
            text += f" {left}s"
    elif state_id == 8:
        css, text = "ready", f"{temp or 'ready'} ●"
        if (left := _seconds_left(data)) is not None:
            text += f" {left}s"
    elif state_id == 9:
        css, text = "cool", f"{temp or 'cool'} ↓"
    else:
        css, text = "idle", battery_text
        if temp:
            text = f"{temp}  {battery_text}"
    tooltip = state if not connected else f"{state} · {battery_text} · {temp}".strip(" ·")
    if resting:
        tooltip = f"Resting to save battery · {battery_text} at the last check"
    if handed_off:
        # A daemon started while the other computer holds the Peak has never
        # read a battery, so don't report 0% as though it had.
        tooltip = "Another computer has the Peak"
        if battery:
            tooltip += f" · {battery_text} at the last check"
    if connected and data.get("charge_eta_s"):
        tooltip = f"{tooltip} · full in {format_eta(data['charge_eta_s'])}"
    if connected and data.get("clean_due"):
        tooltip = f"{tooltip} · clean chamber".strip(" ·")
        if css == "idle":
            css, text = "clean", f"clean  {battery_text}"
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


async def async_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="quickpuff",
        description="QuickPuff — Peak Pro companion for Linux",
    )
    parser.add_argument("--json", action="store_true", help="Print raw JSON")
    parser.add_argument("--version", action="version", version=f"quickpuff {__version__}")
    sub = parser.add_subparsers(dest="cmd")

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
    sub.add_parser("waybar", help="One-shot status JSON for the bar widget")
    sub.add_parser("stats", help="Dab telemetry: today/week/month/year + lifetime")
    sub.add_parser("sync", help="Pull usage history from the Peak's own log")
    sub.add_parser("faults", help="Heater, battery and pairing faults the Peak recorded")
    sub.add_parser("doctor", help="Check Bluetooth, the daemon, the widget and your Peak, with fixes")

    heat = sub.add_parser("heat")
    heat.add_argument("action", choices=["start", "stop", "boost"])

    lantern = sub.add_parser("lantern")
    lantern.add_argument("action", choices=["on", "off"], nargs="?")
    lantern.add_argument("--timeout", type=float, help="Lantern auto-off in seconds (60–28800)")

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
    prof.add_argument("--vapor", help="smooth, bold, intense, extreme")
    prof.add_argument("--boost-temp", type=float, help="Boost Δ temperature in °F (0–36)")
    prof.add_argument("--boost-time", type=float, help="Boost extra seconds (0–60)")

    color = sub.add_parser("color", help="Set a heat profile's LED color")
    color.add_argument("hex", help="#rrggbb")
    color.add_argument("--index", type=int, help="Profile 0-3 (default: the selected one)")

    peek = sub.add_parser("peek", help="Read a Lorax path (debug)")
    peek.add_argument("path")
    peek.add_argument("--size", type=int, default=12)
    poke = sub.add_parser("poke", help="Write hex bytes to a Lorax path (debug)")
    poke.add_argument("path")
    poke.add_argument("hex")

    stealth = sub.add_parser("stealth")
    stealth.add_argument("action", choices=["on", "off"])

    saver = sub.add_parser("saver", help="Rest the Peak 30 s after each session or 10 min idle: lantern off, Bluetooth let go until needed")
    saver.add_argument("action", choices=["on", "off"])

    handoff = sub.add_parser("handoff", help="Share the Peak with another computer: let go when this one locks, take it back when you return")
    handoff.add_argument("action", choices=["on", "off"])

    sub.add_parser("claim", help="Take the Peak from whichever computer has it")

    preserve = sub.add_parser("preserve", help="Battery Preservation: charge to 80%% only (on) or to 100%% (off)")
    preserve.add_argument("action", choices=["on", "off"])

    sessions = sub.add_parser("sessions", help="Recent dabs with their notes")
    sessions.add_argument("--limit", type=int, default=20)

    note = sub.add_parser("note", help="Add or edit the note on a dab (no text clears it)")
    note.add_argument("key", help="Session key from `quickpuff sessions`, like d1473")
    note.add_argument("text", nargs="*")

    limit = sub.add_parser("limit", help="Notify after N dabs in a day (0 turns it off)")
    limit.add_argument("count", type=int)

    recap = sub.add_parser("recap", help="This week so far, or turn the Sunday recap on or off")
    recap.add_argument("action", choices=["on", "off"], nargs="?")

    qtip = sub.add_parser("qtip", help="Q-tip reminder notification after each dab")
    qtip.add_argument("action", choices=["on", "off"])

    clean = sub.add_parser("clean", help="Chamber-clean reminder after N dabs")
    clean.add_argument("action", choices=["done"], nargs="?", help="Reset the countdown after you clean")
    clean.add_argument("--every", type=int, help="Remind every N dabs (10–100, steps of 10)")

    name = sub.add_parser("name", help="Rename the Peak")
    name.add_argument("value", nargs="?", help="New device name")

    units = sub.add_parser("units")
    units.add_argument("value", choices=["F", "C", "f", "c"])

    sub.add_parser("battery", help="Flash battery level on the Peak")
    sub.add_parser("off")
    reset = sub.add_parser("factory-reset")
    reset.add_argument("--yes", action="store_true")

    args = parser.parse_args(argv)
    raw = args.json
    cmd = args.cmd

    if cmd is None:
        parser.print_help()
        return 0
    if cmd == "doctor":
        # Runs without starting the daemon: it's for when things are broken.
        from .doctor import format_report, gather

        checks = await gather()
        if raw:
            print(json.dumps([check.__dict__ for check in checks], indent=2))
        else:
            print(format_report(checks))
        return 1 if any(check.ok is False for check in checks) else 0
    if cmd == "daemon":
        from .daemon import main as daemon_main

        daemon_main()
        return 0

    if cmd == "ping":
        print_status_raw = await call("ping")
        print(json.dumps(print_status_raw, indent=2) if raw else f"quickpuff daemon pid {print_status_raw.get('pid')}")
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
        # Marks someone as watching, so the daemon polls the Peak at full speed.
        print_status(await call("status", {"watch": True}), raw)
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
    elif cmd == "sync":
        result = await call("sync_usage", None, 1800.0)
        print(f"Read {result['read']} log entries from the Peak, {result['added']} new sessions.")
    elif cmd == "faults":
        result = await call("faults", None, 900.0)
        if raw:
            print(json.dumps(result, indent=2))
        elif not result["faults"]:
            print("No faults recorded.")
        else:
            from datetime import datetime

            for fault in result["faults"]:
                ts = fault.get("ts")
                when = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "before last restart"
                print(f"  {when:<20} {fault['label']}")
    elif cmd == "heat":
        if args.action == "start":
            warning = low_battery_heat_warning(await call("status"))
            if warning:
                print(warning, file=sys.stderr)
        await call(f"{args.action}_heat")
        print_status(await call("status"), raw)
    elif cmd == "lantern":
        if args.timeout is not None:
            await call("set_lantern_timeout", {"seconds": args.timeout})
        if args.action == "on":
            await call("start_lantern")
        elif args.action == "off":
            await call("stop_lantern")
        elif args.timeout is None:
            raise SystemExit("Give on, off, or --timeout SECONDS")
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
        editing = any(
            [
                args.name,
                args.temp_c is not None,
                args.temp_f is not None,
                args.time is not None,
                args.color,
                args.vapor,
                args.boost_temp is not None,
                args.boost_time is not None,
            ]
        )
        # Selecting first flashes the stock profile colour (medium = green)
        # over the paint we haven't written yet. Paint, then select.
        if not editing:
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
        if args.vapor:
            await call("set_profile_vapor", {"index": args.index, "name": args.vapor})
        if args.boost_temp is not None or args.boost_time is not None:
            payload: dict[str, Any] = {"index": args.index}
            if args.boost_temp is not None:
                payload["temp_f"] = args.boost_temp
            if args.boost_time is not None:
                payload["seconds"] = args.boost_time
            await call("set_profile_boost", payload)
        if editing:
            await call("set_profile", {"index": args.index})
        print_status(await call("status"), raw)
    elif cmd == "color":
        await call("set_profile_color", {"index": args.index, "hex": args.hex})
        print_status(await call("status"), raw)
    elif cmd == "peek":
        print(json.dumps(await call("peek", {"path": args.path, "size": args.size}), indent=2 if raw else None, default=str))
    elif cmd == "poke":
        print(json.dumps(await call("poke", {"path": args.path, "hex": args.hex}), indent=2 if raw else None, default=str))
    elif cmd == "stealth":
        await call("set_stealth", {"enable": args.action == "on"})
        print_status(await call("status"), raw)
    elif cmd == "saver":
        await call("set_battery_saver", {"enable": args.action == "on"})
        print_status(await call("status"), raw)
    elif cmd == "handoff":
        await call("set_handoff", {"enable": args.action == "on"})
        print_status(await call("status"), raw)
    elif cmd == "claim":
        print_status(await call("claim"), raw)
    elif cmd == "preserve":
        result = await call("set_max_charge", {"preserve": args.action == "on"})
        limit = result.get("max_charge")
        if raw:
            print(json.dumps(result))
        else:
            print(f"Charging stops at {round(limit)}%." if limit is not None else "Couldn't read the charge limit back.")
    elif cmd == "sessions":
        result = await call("sessions", {"limit": args.limit})
        if raw:
            print(json.dumps(result, indent=2))
        else:
            print_sessions(result, str(load_config().get("units") or "F").upper())
    elif cmd == "note":
        result = await call("set_note", {"key": args.key, "text": " ".join(args.text)})
        if raw:
            print(json.dumps(result, indent=2))
        else:
            print("Note saved." if result.get("note") else "Note cleared.")
    elif cmd == "limit":
        result = await call("set_daily_limit", {"limit": args.count})
        if raw:
            print(json.dumps(result))
        else:
            print(f"Daily limit: {result['daily_limit']} dabs." if result["daily_limit"] else "Daily limit off.")
    elif cmd == "recap":
        if args.action:
            result = await call("set_weekly_recap", {"enable": args.action == "on"})
            print(json.dumps(result) if raw else f"Weekly recap {'on' if result['weekly_recap'] else 'off'}.")
        else:
            result = await call("recap")
            print(json.dumps(result, indent=2) if raw else result["body"])
    elif cmd == "qtip":
        await call("set_qtip_reminder", {"enable": args.action == "on"})
        print_status(await call("status"), raw)
    elif cmd == "clean":
        if args.every is not None:
            await call("set_clean_every", {"dabs": args.every})
        if args.action == "done":
            await call("mark_cleaned")
        if args.every is None and args.action is None:
            data = await call("status")
            if raw:
                print(json.dumps(data, indent=2, default=str))
            elif data.get("clean_due"):
                print(f"Clean due  (every {data.get('clean_every')} dabs)")
            else:
                print(f"{data.get('clean_remaining')} dabs left  (every {data.get('clean_every')})")
            return 0
        print_status(await call("status"), raw)
    elif cmd == "name":
        if not args.value:
            print_status(await call("status"), raw)
            return 0
        print_status(await call("set_device_name", {"name": args.value}), raw)
    elif cmd == "units":
        from .paths import save_config

        cfg = load_config()
        cfg["units"] = args.value.upper()
        save_config(cfg)
        print(f"Units set to °{cfg['units']}")
    elif cmd == "battery":
        await call("show_battery")
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
    raise SystemExit(asyncio.run(async_main(argv)))


if __name__ == "__main__":
    main()
