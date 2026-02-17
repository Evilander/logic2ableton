from pathlib import Path

from logic2ableton.report import generate_report
from logic2ableton.logic_parser import parse_logic_project
from logic2ableton.plugin_matcher import match_plugins
from logic2ableton.models import TrackMixerState

TEST_PROJECT = Path("Might Last Forever.logicx")
VST3_PATH = Path("C:/Program Files/Common Files/VST3")


def test_generate_report():
    project = parse_logic_project(TEST_PROJECT)
    matches = match_plugins(project.plugins, VST3_PATH)
    report = generate_report(project, matches)
    assert "Might Last Forever" in report
    assert "120" in report
    assert "KICK IN" in report
    assert "PLUGINS FOUND" in report
    assert "NOT TRANSFERRED" in report


def test_report_contains_track_count():
    project = parse_logic_project(TEST_PROJECT)
    matches = match_plugins(project.plugins, VST3_PATH)
    report = generate_report(project, matches)
    assert "TRACKS TRANSFERRED" in report


def test_report_contains_mixer_state():
    project = parse_logic_project(TEST_PROJECT)
    project.mixer_state = {
        "KICK IN": TrackMixerState(volume_db=-6.0, pan=0.3, is_muted=True),
    }
    report = generate_report(project, [])
    assert "MIXER STATE APPLIED" in report
    assert "-6.0 dB" in report
    assert "pan 30%R" in report
    assert "MUTED" in report
