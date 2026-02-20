from logic2ableton.plugin_database import lookup_au_plugin, AU_PLUGINS


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


def test_database_has_150_plus_entries():
    assert len(AU_PLUGINS) >= 150


def test_lookup_izotope_neutron():
    info = lookup_au_plugin("iZtp", "ZNNZ")
    assert info is not None
    assert "Neutron" in info.name


def test_lookup_soundtoys_echoboy():
    info = lookup_au_plugin("SToy", "EB  ")
    assert info is not None
    assert "EchoBoy" in info.name
    assert info.category == "delay"


def test_lookup_arturia_mini_v3():
    info = lookup_au_plugin("Artu", "MIN3")
    assert info is not None
    assert "Mini" in info.name
    assert info.category == "synth"


def test_lookup_native_instruments_kontakt():
    info = lookup_au_plugin("-NI-", "NiK7")
    assert info is not None
    assert "Kontakt" in info.name


def test_lookup_brainworx_ssl():
    info = lookup_au_plugin("Brwx", "bslj")
    assert info is not None
    assert "SSL" in info.name


def test_lookup_spectrasonics_omnisphere():
    info = lookup_au_plugin("GOSW", "Omn2")
    assert info is not None
    assert "Omnisphere" in info.name


def test_lookup_antares_autotune():
    info = lookup_au_plugin("Antr", "ATpr")
    assert info is not None
    assert "Auto-Tune" in info.name
    assert info.category == "pitch_correction"


def test_lookup_eventide_blackhole():
    info = lookup_au_plugin("Evnt", "EvBl")
    assert info is not None
    assert "Blackhole" in info.name
    assert info.category == "reverb"


def test_lookup_waves_ssl_comp():
    info = lookup_au_plugin("ksWV", "SSCM")
    assert info is not None
    assert "SSL" in info.name
    assert info.category == "compressor"


def test_lookup_apple_alchemy():
    info = lookup_au_plugin("appl", "alcn")
    assert info is not None
    assert "Alchemy" in info.name
    assert info.category == "synth"


def test_all_entries_have_valid_categories():
    valid_categories = {
        "eq", "compressor", "limiter", "de_esser", "reverb", "delay",
        "saturation", "modulation", "channel_strip", "synth", "sampler",
        "utility", "multi_fx", "gate", "pitch_correction", "mastering",
        "noise_reduction",
    }
    for key, info in AU_PLUGINS.items():
        assert info.category in valid_categories, f"{key}: invalid category '{info.category}'"
