from pathlib import Path
from unittest.mock import patch

from logic2ableton.vst3_scanner import scan_vst3_plugins, default_vst3_path

VST3_PATH = Path("C:/Program Files/Common Files/VST3")


def test_scan_finds_plugins():
    plugins = scan_vst3_plugins(VST3_PATH)
    assert len(plugins) > 50


def test_scan_categorizes_fabfilter():
    plugins = scan_vst3_plugins(VST3_PATH)
    fabfilter = [p for p in plugins if "FabFilter" in p.name]
    assert len(fabfilter) >= 1


def test_scan_empty_dir(tmp_path):
    plugins = scan_vst3_plugins(tmp_path)
    assert plugins == []


def test_default_vst3_path_windows():
    with patch("logic2ableton.vst3_scanner.sys") as mock_sys:
        mock_sys.platform = "win32"
        p = default_vst3_path()
        assert "Common Files" in str(p)


def test_default_vst3_path_macos():
    with patch("logic2ableton.vst3_scanner.sys") as mock_sys:
        mock_sys.platform = "darwin"
        p = default_vst3_path()
        assert p.parts[-3:] == ("Audio", "Plug-Ins", "VST3")
