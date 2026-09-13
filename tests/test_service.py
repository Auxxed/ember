"""Regression cover for the stale-socket lockout.

A daemon killed with SIGKILL leaves its socket file behind. Treating that
file as proof of life meant ensure_daemon() never respawned, and every
CLI call failed until the socket was deleted by hand.
"""

import socket
from types import SimpleNamespace

from omapuffco import service


class TestDaemonRunning:
    def test_absent_socket_is_not_running(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OMAPUFFCO_SOCKET", str(tmp_path / "nothing.sock"))
        assert service.daemon_running() is False

    def test_stale_socket_file_is_not_running(self, monkeypatch, tmp_path):
        stale = tmp_path / "omapuffco.sock"
        stale.write_text("")
        monkeypatch.setenv("OMAPUFFCO_SOCKET", str(stale))
        assert service.daemon_running() is False

    def test_listening_socket_is_running(self, monkeypatch, tmp_path):
        live = tmp_path / "omapuffco.sock"
        monkeypatch.setenv("OMAPUFFCO_SOCKET", str(live))
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            server.bind(str(live))
            server.listen(1)
            assert service.daemon_running() is True
        finally:
            server.close()

    def test_socket_stops_counting_once_the_listener_goes_away(self, monkeypatch, tmp_path):
        """Exactly the SIGKILL shape: the file outlives the process."""
        path = tmp_path / "omapuffco.sock"
        monkeypatch.setenv("OMAPUFFCO_SOCKET", str(path))
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(path))
        server.listen(1)
        assert service.daemon_running() is True

        server.close()  # file remains on disk, nothing is listening
        assert path.exists()
        assert service.daemon_running() is False


class TestSystemdOwnsDaemon:
    def test_active_unit_is_owned(self, monkeypatch):
        def fake_run(*_args, **_kwargs):
            return SimpleNamespace(
                returncode=0,
                stdout="ActiveState=active\nUnitFileState=enabled\n",
            )

        monkeypatch.setattr(service.subprocess, "run", fake_run)
        assert service.systemd_owns_daemon() is True

    def test_restart_window_is_owned(self, monkeypatch):
        def fake_run(*_args, **_kwargs):
            return SimpleNamespace(
                returncode=0,
                stdout="ActiveState=deactivating\nUnitFileState=enabled\n",
            )

        monkeypatch.setattr(service.subprocess, "run", fake_run)
        assert service.systemd_owns_daemon() is True

    def test_disabled_and_inactive_is_not_owned(self, monkeypatch):
        def fake_run(*_args, **_kwargs):
            return SimpleNamespace(
                returncode=0,
                stdout="ActiveState=inactive\nUnitFileState=disabled\n",
            )

        monkeypatch.setattr(service.subprocess, "run", fake_run)
        assert service.systemd_owns_daemon() is False

    def test_missing_systemctl_is_not_owned(self, monkeypatch):
        def fake_run(*_args, **_kwargs):
            raise FileNotFoundError("systemctl")

        monkeypatch.setattr(service.subprocess, "run", fake_run)
        assert service.systemd_owns_daemon() is False

    def test_ensure_waits_instead_of_spawning_when_systemd_owns(self, monkeypatch, tmp_path):
        spawned = []
        monkeypatch.setattr(service, "daemon_running", lambda: False)
        monkeypatch.setattr(service, "systemd_owns_daemon", lambda: True)
        monkeypatch.setattr(service, "log_path", lambda: tmp_path / "daemon.log")
        monkeypatch.setattr(service.time, "time", lambda: 0)
        monkeypatch.setattr(
            service.subprocess,
            "Popen",
            lambda *a, **k: spawned.append(True),
        )

        try:
            service.ensure_daemon(timeout=0)
        except RuntimeError as exc:
            assert "systemd daemon did not come back" in str(exc)
        else:
            raise AssertionError("expected ensure_daemon to fail closed")
        assert spawned == []
