from pathlib import Path

from logic2ableton.plugin_matcher import match_plugins, _name_similarity, _tokenize
from logic2ableton.models import PluginInstance

VST3_PATH = Path("C:/Program Files/Common Files/VST3")


def test_match_known_compressor():
    plugins = [PluginInstance("Guitar", "aufx", "76CM", "ksWV", True, {})]
    matches = match_plugins(plugins, VST3_PATH)
    assert len(matches) == 1
    assert matches[0].logic_plugin_name == "Waves CLA-76"
    assert matches[0].preset_name == "Guitar"
    assert matches[0].category == "compressor"
    assert len(matches[0].suggested_vst3s) > 0


def test_match_unknown_plugin():
    plugins = [PluginInstance("Mystery", "aufx", "XXXX", "YYYY", False, {})]
    matches = match_plugins(plugins, VST3_PATH)
    assert len(matches) == 1
    assert matches[0].logic_plugin_name == "Unknown (YYYY/XXXX)"
    assert matches[0].category == "unknown"


def test_match_returns_all_plugins():
    plugins = [
        PluginInstance("Big Guitar", "aufx", "TG5M", "ksWV", True, {}),
        PluginInstance("Untitled", "aufx", "L1CM", "ksWV", True, {}),
    ]
    matches = match_plugins(plugins, VST3_PATH)
    assert len(matches) == 2


def test_tokenize_basic():
    assert "waves" in _tokenize("Waves CLA-76")
    assert "cla" in _tokenize("Waves CLA-76")
    assert "76" in _tokenize("Waves CLA-76")


def test_tokenize_strips_version():
    tokens = _tokenize("FabFilter Pro-Q V3")
    assert "v3" not in tokens
    assert "fabfilter" in tokens
    assert "pro" in tokens
    assert "q" in tokens


def test_name_similarity_identical():
    score = _name_similarity("EchoBoy", "EchoBoy")
    assert score == 1.0


def test_name_similarity_partial():
    score = _name_similarity("Waves CLA-76", "CLA-76 Compressor")
    assert score > 0.3


def test_name_similarity_unrelated():
    score = _name_similarity("Waves CLA-76", "FabFilter Pro-Q 3")
    assert score < 0.1


def test_name_similarity_empty():
    assert _name_similarity("", "anything") == 0.0
    assert _name_similarity("anything", "") == 0.0


def test_match_suggests_at_most_5():
    plugins = [PluginInstance("Guitar", "aufx", "76CM", "ksWV", True, {})]
    matches = match_plugins(plugins, VST3_PATH)
    assert len(matches[0].suggested_vst3s) <= 5
