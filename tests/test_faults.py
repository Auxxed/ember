from omapuffco import faults
from omapuffco.audit import Entry

NOW = 1_800_000_000.0


def test_faults_are_labelled_and_newest_first():
    found = faults.decode(
        [Entry(1, 1_790_000_000, 8), Entry(2, 1_790_000_100, 11)],
        device_clock=5000,
        host_now=NOW,
    )
    assert [f["name"] for f in found] == ["BOND_FAILED", "HEATER_SAFETY_THERMAL_VIOLATION"]
    assert found[0]["label"] == "Bluetooth pairing failed"
    assert found[0]["ts"] == 1_790_000_100.0


def test_restart_clock_and_undefined_codes_are_not_faults():
    entries = [Entry(1, 10, faults.SYSTEM_BOOT), Entry(2, 20, faults.CLOCK_ADJUST), Entry(3, 30, 15), Entry(4, 40, 7)]
    found = faults.decode(entries, device_clock=100, host_now=NOW)
    assert [f["code"] for f in found] == [7]


def test_boot_relative_faults_are_placed_against_the_device_clock():
    found = faults.decode([Entry(5, 900, 2)], device_clock=1000, host_now=NOW)
    assert found[0]["ts"] == NOW - 100


def test_faults_from_before_a_later_restart_have_no_time():
    entries = [Entry(1, 500, 4), Entry(2, 0, faults.SYSTEM_BOOT), Entry(3, 40, 9)]
    found = {f["code"]: f["ts"] for f in faults.decode(entries, device_clock=100, host_now=NOW)}
    assert found[4] is None
    assert found[9] == NOW - 60
