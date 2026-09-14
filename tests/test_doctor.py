from quickpuff import doctor


def test_stale_daemon_after_an_update_is_flagged():
    check = doctor.check_daemon("0.3.1", installed="0.3.2")
    assert check.ok is False
    assert "restart quickpuff-daemon" in check.fix
    assert doctor.check_daemon("0.3.2", installed="0.3.2").ok is True
    assert doctor.check_daemon(None).ok is False


def test_adapter_that_is_off_says_how_to_turn_it_on():
    check = doctor.check_adapters([{"name": "hci0", "powered": False}])
    assert check.ok is False and check.fix == "bluetoothctl power on"
    assert doctor.check_adapters([{"name": "hci0", "powered": False}, {"name": "hci1", "powered": True}]).ok
    assert doctor.check_adapters([]).ok is False
    assert doctor.check_adapters(None, "no system bus").ok is False


def test_bluetooth_service():
    assert doctor.check_bluetooth_service("active").ok
    assert "enable --now bluetooth" in doctor.check_bluetooth_service("inactive").fix


def test_widget_states():
    assert doctor.check_widget(None, omarchy_found=False).ok is None
    assert doctor.check_widget([], omarchy_found=True).ok is False
    disabled = [{"id": doctor.PLUGIN_ID, "enabled": False}]
    assert "plugin enable" in doctor.check_widget(disabled, omarchy_found=True).fix
    assert doctor.check_widget([{"id": doctor.PLUGIN_ID, "enabled": True}], omarchy_found=True).ok


def test_unpaired_peak_gets_pairing_instructions():
    cfg = {"device_mac": "AA:BB:CC:11:22:33", "device_name": "Peak Pro"}
    check = doctor.check_saved_peak(cfg, paired=False)
    assert check.ok is False and "glows blue" in check.fix
    assert doctor.check_saved_peak(cfg, paired=True).ok
    assert doctor.check_saved_peak({}, paired=None).ok is None


def test_connection_names_model_firmware_and_led_api():
    status = {"connected": True, "product": {"label": "Peak Pro Onyx"}, "firmware": "AW", "led_api": 3}
    assert doctor.check_connection(status).detail == "Peak Pro Onyx, firmware AW, LED API 3"
    assert doctor.check_connection({"connected": False}).ok is None


def test_report_counts_problems_and_shows_fixes_only_for_them():
    report = doctor.format_report(
        [
            doctor.Check("Daemon", True, "running 0.3.2", "never shown"),
            doctor.Check("Bluetooth adapter", False, "hci0 (off)", "bluetoothctl power on"),
        ]
    )
    assert "never shown" not in report
    assert "→ bluetoothctl power on" in report
    assert report.endswith("1 problem to fix.")


def test_handoff_off_is_flagged_only_as_something_to_know():
    check = doctor.check_handoff({"handoff": False}, None)
    assert check.ok is None
    assert "quickpuff handoff on" in check.fix


def test_handoff_says_when_another_computer_has_the_peak():
    check = doctor.check_handoff({"handoff": True}, {"handed_off": True})
    assert check.ok
    assert "another computer" in check.detail


def test_handoff_says_when_stay_awake_stops_the_screen_locking():
    """Handoff hands over on a lock. Stay Awake means that never happens, and
    a user watching it not happen deserves to know why."""
    check = doctor.check_handoff({"handoff": True}, None, {"stayAwake": True})
    # Worth knowing, but the user's own setting is not a fault to be counted.
    assert check.ok is None
    assert "1 problem" not in doctor.format_report([check])
    assert "Stay Awake" in check.detail
    assert "Super+Ctrl+I" in check.fix


def test_handoff_is_quiet_about_idle_when_the_screen_does_lock():
    check = doctor.check_handoff({"handoff": True}, None, {"stayAwake": False})
    assert "Stay Awake" not in check.detail
    # Not an Omarchy desktop at all.
    assert "Stay Awake" not in doctor.check_handoff({"handoff": True}, None, None).detail
