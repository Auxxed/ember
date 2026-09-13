from omapuffco.vapor import name_for, snap, value_for


class TestVaporLevels:
    def test_connect_names(self):
        assert value_for("smooth") == 0.0
        assert value_for("bold") == 0.5
        assert value_for("intense") == 1.0
        assert value_for("extreme") == 1.5

    def test_old_app_names_still_map(self):
        assert value_for("standard") == 0.0
        assert value_for("high") == 0.5
        assert value_for("max") == 1.0
        assert value_for("xl") == 1.5

    def test_nearest_name(self):
        assert name_for(0.0) == "smooth"
        assert name_for(0.49) == "bold"
        assert name_for(1.4) == "extreme"

    def test_snap_to_firmware_steps(self):
        assert snap(0.2) == 0.0
        assert snap(0.6) == 0.5
        assert snap(1.2) == 1.0
        assert snap(1.4) == 1.5
