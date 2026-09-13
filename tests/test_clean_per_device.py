from omapuffco.daemon import OmaPuffcoDaemon
from omapuffco.paths import load_config, save_config


def daemon(tmp_path):
    return OmaPuffcoDaemon(sock=tmp_path / "omapuffco.sock")


def test_each_peak_keeps_its_own_cleaning_baseline(tmp_path):
    d = daemon(tmp_path)
    d._load_clean("PEAK-A")
    d.clean_at_total = 863
    d._save_clean()
    d._load_clean("PEAK-B")
    assert d.clean_at_total is None
    d.clean_at_total = 120
    d._save_clean()
    d._load_clean("PEAK-A")
    assert d.clean_at_total == 863
    assert load_config()["clean_by_serial"]["PEAK-B"]["at_total"] == 120


def test_settings_from_before_per_peak_belong_to_the_last_peak(tmp_path):
    save_config({"clean_at_total": 863, "clean_notified": True, "last_serial": "MINE"})
    d = daemon(tmp_path)
    d._load_clean("MINE")
    assert d.clean_at_total == 863 and d.clean_notified is True
    d._load_clean("FRIEND")
    assert d.clean_at_total is None and d.clean_notified is False


def test_interval_stays_one_shared_preference(tmp_path):
    save_config({"clean_every": 50})
    d = daemon(tmp_path)
    d._load_clean("PEAK-A")
    assert d.clean_every == 50
    d._load_clean("PEAK-B")
    assert d.clean_every == 50
