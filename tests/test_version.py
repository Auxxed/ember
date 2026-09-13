import asyncio

from quickpuff import __version__
from quickpuff.daemon import QuickPuffDaemon


def test_daemon_reports_the_package_version(tmp_path):
    daemon = QuickPuffDaemon(sock=tmp_path / "quickpuff.sock")
    reply = asyncio.run(daemon.handle("ping", {}))
    assert reply["version"] == __version__
