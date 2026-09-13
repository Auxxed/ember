import json

import pytest

from quickpuff import paths


class TestConfig:
    def test_round_trip(self):
        paths.save_config({"units": "C", "device_name": "Peak"})
        cfg = paths.load_config()
        assert cfg["units"] == "C"
        assert cfg["device_name"] == "Peak"

    def test_defaults_fill_in_missing_keys(self):
        paths.save_config({"units": "C"})
        loaded = paths.load_config()
        assert loaded["auto_connect"] is True
        assert loaded["battery_saver"] is False
        assert loaded["clean_every"] == 30
        assert loaded["clean_at_total"] is None

    def test_missing_file_yields_defaults(self):
        assert paths.load_config() == paths.DEFAULTS

    def test_corrupt_file_falls_back_to_defaults(self):
        path = paths.config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{ this is not json")
        assert paths.load_config() == paths.DEFAULTS

    def test_unknown_keys_survive_a_round_trip(self):
        paths.save_config({"future_setting": 7})
        assert paths.load_config()["future_setting"] == 7

    def test_retired_settings_are_dropped(self):
        paths.save_config({"last_fact": 8, "poll_interval": 1.5, "units": "C"})
        cfg = paths.load_config()
        assert "last_fact" not in cfg and "poll_interval" not in cfg
        assert cfg["units"] == "C"
        assert "last_fact" not in paths.config_path().read_text()


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
        monkeypatch.setenv("QUICKPUFF_SOCKET", str(tmp_path / "custom.sock"))
        assert paths.socket_path() == tmp_path / "custom.sock"

    def test_config_and_data_live_under_their_xdg_roots(self, isolated_xdg):
        assert paths.config_path().is_relative_to(isolated_xdg / "config")
        assert paths.data_dir().is_relative_to(isolated_xdg / "data")
