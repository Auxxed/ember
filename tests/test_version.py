import asyncio

from omapuffco import __version__
from omapuffco.daemon import OmaPuffcoDaemon


def test_daemon_reports_the_package_version(tmp_path):
    daemon = OmaPuffcoDaemon(sock=tmp_path / "omapuffco.sock")
    reply = asyncio.run(daemon.handle("ping", {}))
    assert reply["version"] == __version__
