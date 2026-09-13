import json

import pytest

from omapuffco import paths


class TestConfig:
    def test_round_trip(self):
        paths.save_config({"units": "C", "device_name": "Peak"})
        cfg = paths.load_config()
        assert cfg["units"] == "C"
        assert cfg["device_name"] == "Peak"

    def test_defaults_fill_in_missing_keys(self):
        paths.save_config({"units": "C"})
        assert paths.load_config()["auto_connect"] is True

    def test_missing_file_yields_defaults(self):
        assert paths.load_config() == paths.DEFAULTS

    def test_corrupt_file_falls_back_to_defaults(self):
        path = paths.config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{ this is not json")
        assert paths.load_config() == paths.DEFAULTS

    def test_unknown_keys_survive_a_round_trip(self):
        paths.save_config({"last_fact": 7})
        assert paths.load_config()["last_fact"] == 7


class TestWriteJsonAtomic:
    @pytest.fixture
    def workdir(self, tmp_path):
        """A directory holding nothing but what the writer puts there."""
        path = tmp_path / "atomic"
        path.mkdir()
        return path

    def test_writes_readable_json(self, workdir):
        target = workdir / "out.json"
        paths.write_json_atomic(target, {"a": 1})
        assert json.loads(target.read_text()) == {"a": 1}

    def test_creates_missing_parents(self, workdir):
        target = workdir / "deep" / "nested" / "out.json"
        paths.write_json_atomic(target, {"a": 1})
        assert target.exists()

    def test_leaves_no_temp_files_behind(self, workdir):
        target = workdir / "out.json"
        paths.write_json_atomic(target, {"a": 1})
        assert [p.name for p in workdir.iterdir()] == ["out.json"]

    def test_overwrites_existing_content(self, workdir):
        target = workdir / "out.json"
        paths.write_json_atomic(target, {"a": 1})
        paths.write_json_atomic(target, {"b": 2})
        assert json.loads(target.read_text()) == {"b": 2}

    def test_failed_write_leaves_the_original_intact(self, workdir):
        """The whole point of the temp-file dance."""
        target = workdir / "out.json"
        paths.write_json_atomic(target, {"good": True})

        with pytest.raises(TypeError):
            paths.write_json_atomic(target, {"bad": {1, 2, 3}})  # sets aren't JSON

        assert json.loads(target.read_text()) == {"good": True}
        assert [p.name for p in workdir.iterdir()] == ["out.json"]


class TestPathLayout:
    def test_socket_env_override_wins(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OMAPUFFCO_SOCKET", str(tmp_path / "custom.sock"))
        assert paths.socket_path() == tmp_path / "custom.sock"

    def test_config_and_data_live_under_their_xdg_roots(self, isolated_xdg):
        assert paths.config_path().is_relative_to(isolated_xdg / "config")
        assert paths.data_dir().is_relative_to(isolated_xdg / "data")
