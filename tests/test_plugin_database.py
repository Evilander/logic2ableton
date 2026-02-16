from logic2ableton.plugin_database import lookup_au_plugin


def test_lookup_waves_cla76():
    info = lookup_au_plugin("ksWV", "76CM")
    assert info is not None
    assert info.name == "Waves CLA-76"
    assert info.category == "compressor"


def test_lookup_waves_l1_mono():
    info = lookup_au_plugin("ksWV", "L1CM")
    assert info is not None
    assert "L1" in info.name
    assert info.category == "limiter"


def test_lookup_waves_tg_mastering():
    info = lookup_au_plugin("ksWV", "TG5M")
    assert info is not None
    assert "Abbey Road" in info.name or "TG" in info.name


def test_lookup_unknown_plugin():
    assert lookup_au_plugin("XXXX", "YYYY") is None


def test_lookup_logic_channel_eq():
    info = lookup_au_plugin("appl", "bceq")
    assert info is not None
    assert info.category == "eq"
