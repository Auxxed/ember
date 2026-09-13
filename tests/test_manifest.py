"""The repo root is the Omarchy plugin folder.

`omarchy plugin add` clones it and runs omarchy-plugin-validate; these tests
mirror those checks so a broken manifest fails CI instead of a user's install.
"""

import json
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((ROOT / "manifest.json").read_text())
ENTRY_POINT_FOR_KIND = {
    "bar": "bar",
    "bar-widget": "barWidget",
    "menu": "menu",
    "overlay": "overlay",
    "panel": "panel",
    "service": "service",
}


def test_schema_version_and_required_fields():
    assert MANIFEST["schemaVersion"] == 1
    for field in ("id", "name", "version", "kinds", "entryPoints"):
        assert field in MANIFEST


def test_id_is_valid_and_outside_the_reserved_namespace():
    plugin_id = MANIFEST["id"]
    assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", plugin_id)
    assert ".." not in plugin_id
    assert not plugin_id.startswith("omarchy.")


def test_every_kind_has_an_entry_point_that_exists():
    assert MANIFEST["kinds"]
    for kind in MANIFEST["kinds"]:
        key = ENTRY_POINT_FOR_KIND.get(kind)
        if key:
            assert key in MANIFEST["entryPoints"]
    for path in MANIFEST["entryPoints"].values():
        assert not path.startswith("/")
        assert ".." not in Path(path).parts
        assert (ROOT / path).is_file()


def test_default_bar_section_is_valid():
    section = MANIFEST.get("barWidget", {}).get("defaultSection", "right")
    assert section in ("left", "center", "right")


def test_marketplace_root_files_exist():
    for name in ("README.md", "LICENSE"):
        assert (ROOT / name).is_file()


def test_no_symlinks_are_tracked():
    listing = subprocess.run(
        ["git", "ls-files", "-s"], cwd=ROOT, capture_output=True, text=True
    )
    if listing.returncode != 0:
        pytest.skip("not a git checkout")
    links = [line for line in listing.stdout.splitlines() if line.startswith("120000")]
    assert links == []
