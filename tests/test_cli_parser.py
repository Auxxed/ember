"""Every command's parser must build: one bad help string breaks the whole CLI,
the bar widget included."""

import asyncio

import pytest

from omapuffco.cli import async_main


def test_help_builds_for_every_command(capsys):
    with pytest.raises(SystemExit) as exit_info:
        asyncio.run(async_main(["--help"]))
    assert exit_info.value.code == 0
    out = capsys.readouterr().out
    for command in ("status", "preserve", "battery", "sessions", "note", "limit", "recap", "doctor"):
        assert command in out


@pytest.mark.parametrize("command", ["preserve", "battery", "sessions", "note", "limit", "recap", "qtip", "saver"])
def test_each_subcommand_help_formats(command, capsys):
    with pytest.raises(SystemExit) as exit_info:
        asyncio.run(async_main([command, "--help"]))
    assert exit_info.value.code == 0
