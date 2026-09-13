"""Keep every test inside a throwaway XDG tree.

Without this, importing and exercising history/paths would read and
rewrite the developer's real config.json and dabs.json.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolated_xdg(tmp_path, monkeypatch):
    for name, sub in (
        ("XDG_CONFIG_HOME", "config"),
        ("XDG_DATA_HOME", "data"),
        ("XDG_RUNTIME_DIR", "run"),
    ):
        target = tmp_path / sub
        target.mkdir()
        monkeypatch.setenv(name, str(target))
    monkeypatch.delenv("QUICKPUFF_SOCKET", raising=False)
    from quickpuff import history

    history.use_device(None)
    return tmp_path
