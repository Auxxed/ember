"""Regression cover for the stale-socket lockout.

A daemon killed with SIGKILL leaves its socket file behind. Treating that
file as proof of life meant ensure_daemon() never respawned, and every
CLI call failed until the socket was deleted by hand.
"""

import socket

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
