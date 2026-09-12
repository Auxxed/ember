"""Start the Ember daemon if it is not already listening."""

from __future__ import annotations

import os
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
    sock = socket_path()
    return sock.exists()


def ensure_daemon(timeout: float = 8.0) -> None:
    if daemon_running():
        return
    runtime_dir().mkdir(parents=True, exist_ok=True)
    log = log_path()
    env = os.environ.copy()
    src = str(project_root() / "src")
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = src if not existing else f"{src}:{existing}"
    handle = open(log, "ab", buffering=0)
    subprocess.Popen(
        [python_executable(), "-m", "ember.daemon"],
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
        f"Ember daemon did not start. Check {log}"
    )
