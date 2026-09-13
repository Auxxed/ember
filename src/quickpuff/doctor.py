"""`quickpuff doctor`: check what QuickPuff needs, and say how to fix what's missing.

Each check is a small function over plain inputs so it can be tested without
Bluetooth; `gather` does the real probing.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any

from . import __version__
from .paths import load_config

PLUGIN_ID = "auxxed.quickpuff"


@dataclass
class Check:
    name: str
    ok: bool | None  # None: nothing wrong, but nothing to confirm either
    detail: str
    fix: str = ""


def _run(argv: list[str], timeout: float = 5.0) -> tuple[int, str]:
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return 127, ""
    return done.returncode, (done.stdout or "").strip()


def check_bluetooth_service(state: str) -> Check:
    if state == "active":
        return Check("Bluetooth service", True, "running")
    return Check(
        "Bluetooth service",
        False,
        state or "not running",
        "sudo systemctl enable --now bluetooth",
    )


def check_adapters(adapters: list[dict] | None, error: str = "") -> Check:
    if adapters is None:
        return Check("Bluetooth adapter", False, f"couldn't ask BlueZ ({error})", "Start the Bluetooth service first.")
    if not adapters:
        return Check("Bluetooth adapter", False, "none found", "Enable the laptop's Bluetooth or plug in an adapter.")
    names = ", ".join(f"{a['name']} ({'on' if a['powered'] else 'off'})" for a in adapters)
    if not any(a["powered"] for a in adapters):
        return Check("Bluetooth adapter", False, names, "bluetoothctl power on")
    return Check("Bluetooth adapter", True, names)


def check_daemon(daemon_version: str | None, installed: str = __version__) -> Check:
    if daemon_version is None:
        return Check(
            "Daemon",
            False,
            "not running",
            "systemctl --user restart quickpuff-daemon  (or re-run install.sh)",
        )
    if daemon_version != installed:
        return Check(
            "Daemon",
            False,
            f"running {daemon_version}, but {installed} is installed",
            "systemctl --user restart quickpuff-daemon",
        )
    return Check("Daemon", True, f"running {daemon_version}")


def check_widget(plugins: list[dict] | None, omarchy_found: bool) -> Check:
    if not omarchy_found:
        return Check("Bar widget", None, "Omarchy not found; the quickpuff command still works")
    if plugins is None:
        return Check("Bar widget", None, "couldn't list Omarchy plugins")
    entry = next((p for p in plugins if p.get("id") == PLUGIN_ID), None)
    if entry is None:
        return Check(
            "Bar widget",
            False,
            "not installed",
            "omarchy plugin add https://github.com/Auxxed/quickpuff --enable",
        )
    if not entry.get("enabled"):
        return Check("Bar widget", False, "installed but disabled", f"omarchy plugin enable {PLUGIN_ID}")
    return Check("Bar widget", True, "enabled")


def check_saved_peak(cfg: dict[str, Any], paired: bool | None) -> Check:
    mac = str(cfg.get("device_mac") or "")
    name = str(cfg.get("device_name") or "")
    if not mac and not name:
        return Check(
            "Peak",
            None,
            "none connected yet",
            "Wake the Peak, disconnect the phone app, then press Connect (or run: quickpuff connect).",
        )
    label = f"{name} ({mac})" if name and mac else name or mac
    if paired is False:
        return Check(
            "Peak",
            False,
            f"{label} isn't paired with this computer",
            "Hold the Peak's button until the logo glows blue, then connect again.",
        )
    if cfg.get("auto_connect") is False:
        return Check("Peak", True, f"{label}; disconnected on purpose, Connect brings it back")
    return Check("Peak", True, label)


def check_connection(status: dict[str, Any] | None) -> Check:
    if status is None:
        return Check("Connection", None, "unknown while the daemon is down")
    if not status.get("connected"):
        return Check(
            "Connection",
            None,
            "not connected",
            "Wake the Peak, keep it close, and press Connect (or run: quickpuff connect).",
        )
    product = (status.get("product") or {}).get("label") or "Peak Pro"
    detail = f"{product}, firmware {status.get('firmware') or '?'}"
    if status.get("led_api"):
        detail += f", LED API {status['led_api']}"
    return Check("Connection", True, detail)


def check_notifications(found: bool) -> Check:
    if found:
        return Check("Notifications", True, "notify-send found")
    return Check(
        "Notifications",
        False,
        "notify-send is missing",
        "Install libnotify for the ready, battery, cleaning and Q-tip alerts.",
    )


async def gather() -> list[Check]:
    from . import bluez
    from .rpc import rpc
    from .service import daemon_running

    daemon_version = None
    status = None
    if daemon_running():
        try:
            daemon_version = (await rpc("ping", None, timeout=5)).get("version")
            status = await rpc("status", None, timeout=10)
        except Exception:
            pass

    _code, state = _run(["systemctl", "is-active", "bluetooth"])
    adapters: list[dict] | None
    error = ""
    try:
        adapters = await bluez.list_adapters()
    except Exception as exc:
        adapters, error = None, str(exc) or exc.__class__.__name__

    cfg = load_config()
    paired = None
    if cfg.get("device_mac"):
        try:
            paired = await bluez.device_paired(str(cfg["device_mac"]))
        except Exception:
            paired = None

    omarchy_found = shutil.which("omarchy") is not None
    plugins = None
    if omarchy_found:
        code, out = _run(["omarchy", "plugin", "list", "--json"], timeout=10)
        try:
            plugins = json.loads(out) if code == 0 and out else None
        except ValueError:
            plugins = None

    return [
        check_bluetooth_service(state),
        check_adapters(adapters, error),
        check_daemon(daemon_version),
        check_widget(plugins, omarchy_found),
        check_saved_peak(cfg, paired),
        check_connection(status),
        check_notifications(shutil.which("notify-send") is not None),
    ]


def format_report(checks: list[Check]) -> str:
    lines = []
    for check in checks:
        mark = "✓" if check.ok else ("✗" if check.ok is False else "·")
        lines.append(f"{mark} {check.name:<18} {check.detail}")
        if check.fix and check.ok is not True:
            lines.append(f"  {'':<18} → {check.fix}")
    problems = sum(1 for check in checks if check.ok is False)
    lines.append("")
    lines.append("Everything looks good." if not problems else f"{problems} problem{'s' if problems != 1 else ''} to fix.")
    return "\n".join(lines)
