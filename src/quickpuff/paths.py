"""XDG paths and saved settings."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

SOCKET_NAME = "quickpuff.sock"

DEFAULTS: dict[str, Any] = {
    "device_mac": "",
    "device_name": "",
    "adapter": "",  # "" auto-detects; set "hci1" to pin a specific radio
    "auto_connect": True,
    # Two computers can't share a Peak: let go of it when this seat is locked
    # or switched away, and take it back when someone returns.
    "handoff": True,
    "units": "F",
    "notify_ready": True,
    "notify_low_battery": True,
    "qtip_reminder": True,
    "daily_limit": 0,
    "weekly_recap": True,
    "battery_rated_mah": 1700,
    "battery_saver": False,
    "clean_every": 30,
    "clean_at_total": None,
    "clean_notified": False,
}


def runtime_dir() -> Path:
    raw = os.environ.get("XDG_RUNTIME_DIR")
    return Path(raw) if raw else Path(f"/tmp/quickpuff-{os.getuid()}")


def config_dir() -> Path:
    raw = os.environ.get("XDG_CONFIG_HOME")
    base = Path(raw) if raw else Path.home() / ".config"
    return base / "quickpuff"


def data_dir() -> Path:
    raw = os.environ.get("XDG_DATA_HOME")
    base = Path(raw) if raw else Path.home() / ".local" / "share"
    return base / "quickpuff"


def socket_path() -> Path:
    override = os.environ.get("QUICKPUFF_SOCKET")
    if override:
        return Path(override)
    return runtime_dir() / SOCKET_NAME


def config_path() -> Path:
    return config_dir() / "config.json"


def log_path() -> Path:
    return runtime_dir() / "quickpuff-daemon.log"


def write_json_atomic(path: Path, data: Any, *, indent: int | None = None) -> None:
    """Write JSON through a temp file and rename.

    The daemon and the CLI both write these files, and a crash or a full
    disk part-way through a plain write leaves a truncated file that the
    next load silently discards.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(data, handle, indent=indent)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# Settings earlier versions wrote that nothing reads any more.
RETIRED_KEYS = ("last_fact", "poll_interval")


def load_config() -> dict[str, Any]:
    path = config_path()
    data = dict(DEFAULTS)
    if path.exists():
        try:
            loaded = json.loads(path.read_text())
            if isinstance(loaded, dict):
                data.update(loaded)
        except (OSError, json.JSONDecodeError):
            pass
    for key in RETIRED_KEYS:
        data.pop(key, None)
    return data


def save_config(data: dict[str, Any]) -> None:
    merged = dict(DEFAULTS)
    merged.update(data)
    for key in RETIRED_KEYS:
        merged.pop(key, None)
    write_json_atomic(config_path(), merged, indent=2)
