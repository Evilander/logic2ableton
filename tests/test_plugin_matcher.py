from pathlib import Path

from logic2ableton.plugin_matcher import match_plugins
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
