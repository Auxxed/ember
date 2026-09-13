import json

from omapuffco.cli import print_waybar


def waybar(capsys, **status):
    base = {"connected": True, "battery": 80, "heater_temp_f": 545}
    print_waybar({**base, **status})
    return json.loads(capsys.readouterr().out)


def test_ready_shows_seconds_left_in_the_session(capsys):
    out = waybar(capsys, operating_state="Ready", operating_state_id=8, state_elapsed_s=12.4, state_total_s=40.0)
    assert out["text"] == "545°F ● 28s"


def test_preheat_shows_seconds_until_ready(capsys):
    out = waybar(capsys, operating_state="Preheating", operating_state_id=7, state_elapsed_s=5.0, state_total_s=27.0)
    assert out["text"] == "545°F ↑ 22s"


def test_no_countdown_without_a_known_total(capsys):
    out = waybar(capsys, operating_state="Preheating", operating_state_id=7, state_elapsed_s=5.0, state_total_s=None)
    assert out["text"] == "545°F ↑"


def test_countdown_never_goes_negative(capsys):
    out = waybar(capsys, operating_state="Ready", operating_state_id=8, state_elapsed_s=45.0, state_total_s=40.0)
    assert out["text"] == "545°F ● 0s"


def test_tooltip_shows_time_until_full_while_charging(capsys):
    import json as _json

    from omapuffco.cli import print_waybar

    print_waybar(
        {
            "connected": True,
            "operating_state": "Idle",
            "operating_state_id": 5,
            "battery": 60,
            "charge_source": "USB",
            "charge_eta_s": 2400,
            "heater_temp_f": 75,
        }
    )
    tooltip = _json.loads(capsys.readouterr().out)["tooltip"]
    assert tooltip.endswith("full in 40 min")
