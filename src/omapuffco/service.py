"""Start the OmaPuffco daemon if it is not already listening."""

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


def ensure_daemon(timeout: float = 8.0) -> None:
    if daemon_running():
        return
    runtime_dir().mkdir(parents=True, exist_ok=True, mode=0o700)
    log = log_path()
    env = os.environ.copy()
    src = str(project_root() / "src")
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = src if not existing else f"{src}:{existing}"
    handle = open(log, "ab", buffering=0)
    subprocess.Popen(
        [python_executable(), "-m", "omapuffco.daemon"],
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
        f"OmaPuffco daemon did not start. Check {log}"
    )
