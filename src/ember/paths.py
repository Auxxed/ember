"""XDG paths and saved settings."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

APP_ID = "app.ember.Ember"
APP_NAME = "Ember"
SOCKET_NAME = "ember.sock"

DEFAULTS: dict[str, Any] = {
    "device_mac": "",
    "device_name": "",
    "auto_connect": True,
    "units": "F",
    "notify_ready": True,
    "notify_low_battery": True,
    "poll_interval": 1.5,
}


def runtime_dir() -> Path:
    raw = os.environ.get("XDG_RUNTIME_DIR")
    return Path(raw) if raw else Path(f"/tmp/ember-{os.getuid()}")


def config_dir() -> Path:
    raw = os.environ.get("XDG_CONFIG_HOME")
    base = Path(raw) if raw else Path.home() / ".config"
    return base / "ember"


def data_dir() -> Path:
    raw = os.environ.get("XDG_DATA_HOME")
    base = Path(raw) if raw else Path.home() / ".local" / "share"
    return base / "ember"


def socket_path() -> Path:
    override = os.environ.get("EMBER_SOCKET")
    if override:
        return Path(override)
    return runtime_dir() / SOCKET_NAME


def config_path() -> Path:
    return config_dir() / "config.json"


def log_path() -> Path:
    return runtime_dir() / "emberd.log"


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
    return data


def save_config(data: dict[str, Any]) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    merged = dict(DEFAULTS)
    merged.update(data)
    path.write_text(json.dumps(merged, indent=2) + "\n")
