"""Start the QuickPuff daemon if it is not already listening."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from .paths import log_path, runtime_dir, socket_path


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def python_executable() -> str:
    venv = project_root() / ".venv" / "bin" / "python"
    if venv.exists():
        return str(venv)
    return sys.executable


def daemon_running() -> bool:
    """Probe the socket rather than trusting the file.

    A daemon killed with SIGKILL leaves its socket file behind. Taking
    that file as proof of life meant we never respawned, and every call
    failed with 'connection refused' until it was deleted by hand.
    """
    sock = socket_path()
    if not sock.exists():
        return False
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    probe.settimeout(0.5)
    try:
        probe.connect(str(sock))
        return True
    except OSError:
        return False
    finally:
        probe.close()


def systemd_owns_daemon() -> bool:
    """True when the user unit is enabled or in the middle of a restart.

    Spawning a second process in that window steals the Unix socket and
    the Peak's one BLE write handle, which is how Connect hangs after a
    `systemctl restart`.
    """
    try:
        result = subprocess.run(
            [
                "systemctl",
                "--user",
                "show",
                "-p",
                "ActiveState",
                "-p",
                "UnitFileState",
                "quickpuff-daemon.service",
            ],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0:
        return False
    props = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    state = props.get("ActiveState", "")
    enabled = props.get("UnitFileState", "")
    return state in {"active", "activating", "deactivating", "reloading"} or enabled == "enabled"


def ensure_daemon(timeout: float = 8.0) -> None:
    if daemon_running():
        return
    if systemd_owns_daemon():
        deadline = time.time() + timeout
        while time.time() < deadline:
            if daemon_running():
                return
            time.sleep(0.1)
        raise RuntimeError(
            f"QuickPuff systemd daemon did not come back. Check {log_path()}"
        )
    runtime_dir().mkdir(parents=True, exist_ok=True, mode=0o700)
    log = log_path()
    env = os.environ.copy()
    src = str(project_root() / "src")
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = src if not existing else f"{src}:{existing}"
    handle = open(log, "ab", buffering=0)
    subprocess.Popen(
        [python_executable(), "-m", "quickpuff.daemon"],
        cwd=str(project_root()),
        stdout=handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        env=env,
    )
    deadline = time.time() + timeout
    while time.time() < deadline:
        if daemon_running():
            return
        time.sleep(0.1)
    raise RuntimeError(
        f"QuickPuff daemon did not start. Check {log}"
    )
