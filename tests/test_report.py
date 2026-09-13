import json
from pathlib import Path

import pytest

from logic2ableton.report import generate_report
from logic2ableton.logic_parser import parse_logic_project
from logic2ableton.plugin_matcher import match_plugins
from logic2ableton.models import LogicProject, TrackMixerState

from conftest import TEST_PROJECT, TEST_PROJECT_NAME

VST3_PATH = Path("C:/Program Files/Common Files/VST3")


@pytest.mark.needs_test_project
@pytest.mark.needs_vst3
def test_generate_report():
    project = parse_logic_project(TEST_PROJECT)
    matches = match_plugins(project.plugins, VST3_PATH)
    report = generate_report(project, matches)
    assert TEST_PROJECT_NAME in report
    assert "120" in report
    assert "KICK IN" in report
    assert "PLUGINS FOUND" in report
    assert "NOT TRANSFERRED" in report


@pytest.mark.needs_test_project
@pytest.mark.needs_vst3
def test_report_contains_track_count():
    project = parse_logic_project(TEST_PROJECT)
    matches = match_plugins(project.plugins, VST3_PATH)
    report = generate_report(project, matches)
    assert "TRACKS TRANSFERRED" in report


@pytest.mark.needs_test_project
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


def test_report_contains_compatibility_warnings():
    project = LogicProject(
        name="Warnings Demo",
        tempo=123.5,
        time_sig_numerator=7,
        time_sig_denominator=8,
        sample_rate=48000,
        audio_files=[],
        plugins=[],
        track_names=[],
        alternative=0,
        compatibility_warnings=["2 audio file(s) had no embedded timeline timestamp and will default to bar 1"],
    )

    report = generate_report(project, [])
    assert "COMPATIBILITY WARNINGS" in report
    assert "default to bar 1" in report


def _demo_project(**overrides):
    fields = dict(
        name="Demo",
        tempo=120.0,
        time_sig_numerator=4,
        time_sig_denominator=4,
        sample_rate=44100,
        audio_files=[],
        plugins=[],
        track_names=[],
        alternative=0,
    )
    fields.update(overrides)
    return LogicProject(**fields)


def test_report_smpte_start_default():
    report = generate_report(_demo_project(), [])
    assert "SMPTE start: 01:00:00:00 (default)" in report


def test_report_smpte_start_inferred():
    report = generate_report(
        _demo_project(smpte_start_seconds=7200.0, smpte_start_inferred=True), [],
    )
    assert "SMPTE start: 02:00:00:00 (inferred from the earliest recording)" in report


def test_report_smpte_start_from_flag():
    report = generate_report(
        _demo_project(smpte_start_seconds=7200.0, smpte_start_inferred=False), [],
    )
    assert "SMPTE start: 02:00:00:00 (from --smpte-start)" in report


def test_report_audio_files_line_package_and_folder():
    package_report = generate_report(
        _demo_project(audio_dir=Path("/proj/Media/Audio Files"), audio_layout="package"), [],
    )
    assert "Audio files: package (" in package_report

    folder_report = generate_report(
        _demo_project(audio_dir=Path("/proj/Audio Files"), audio_layout="folder"), [],
    )
    assert "Audio files: folder-style project (" in folder_report


def test_report_timeline_section_lists_tempo_and_markers():
    from logic2ableton.timeline import Timeline, TempoEvent, TimelineMarker

    project = _demo_project(timeline=Timeline(
        tempo_events=[TempoEvent(beat=8.0, bpm=140.0)],
        markers=[TimelineMarker(beat=32.0, name="Chorus")],
    ))
    report = generate_report(project, [])
    assert "TIMELINE:" in report
    assert "Bar 3 beat 1: 140 BPM" in report
    assert "Bar 9 beat 1: Chorus" in report
    assert "Live tempo automation and locators from --timeline are written into the .als" in report
    assert "Logic tempo track and markers (not decoded yet" not in report


def test_report_timeline_note_does_not_claim_completion_in_report_only():
    # generate_report has no way to know whether the caller is about to skip
    # generation (--report-only), so the note must stay true either way
    # instead of asserting the .als write already happened.
    from logic2ableton.timeline import Timeline, TempoEvent

    project = _demo_project(timeline=Timeline(tempo_events=[TempoEvent(beat=0.0, bpm=120.0)], markers=[]))
    report = generate_report(project, [])
    assert "were written into the .als." not in report
    assert "--report-only" in report


def test_report_timeline_bar_beat_round_trips_non_4_4_meter(tmp_path):
    # A 6/8 project's report should show the same bar/beat the user typed
    # into the --timeline JSON, not just a 4/4 one; _bar_beat in report.py
    # duplicates the bar-length formula load_timeline uses, so a divergence
    # between the two would only show up in a non-4/4 project like this.
    from logic2ableton.timeline import load_timeline

    numerator, denominator = 6, 8

    timeline_json = {
        "tempo": [{"bar": 2, "beat": 1, "bpm": 100}, {"bar": 3, "beat": 2, "bpm": 90}],
        "markers": [{"bar": 4, "beat": 1, "name": "Bridge"}],
    }
    timeline_path = tmp_path / "timeline.json"
    timeline_path.write_text(json.dumps(timeline_json), encoding="utf-8")

    loaded = load_timeline(timeline_path, numerator=numerator, denominator=denominator, base_tempo=120)
    project = _demo_project(
        time_sig_numerator=numerator,
        time_sig_denominator=denominator,
        timeline=loaded,
    )
    report = generate_report(project, [])

    assert "Bar 2 beat 1: 100 BPM" in report
    assert "Bar 3 beat 2: 90 BPM" in report
    assert "Bar 4 beat 1: Bridge" in report


def test_report_not_transferred_footer_notes_missing_timeline():
    report = generate_report(_demo_project(), [])
    assert "NOT TRANSFERRED" in report
    assert "Logic tempo track and markers (not decoded yet; supply --timeline to reproduce them)" in report


def test_report_keep_unwarped_lists_matched_tracks():
    project = _demo_project(track_names=["Guitar", "Vocals", "LTC"])
    report = generate_report(project, [], keep_unwarped=["Guitar*", "LTC"])
    assert "Unwarped (won't stretch with tempo changes): Guitar, LTC" in report


def test_report_keep_unwarped_omitted_when_no_match():
    project = _demo_project(track_names=["Vocals"])
    report = generate_report(project, [], keep_unwarped=["Guitar*"])
    assert "Unwarped" not in report


def test_report_smpte_start_explicit_default_value_is_labelled_as_passed():
    report = generate_report(_demo_project(), [], smpte_start_explicit=True)
    assert "SMPTE start: 01:00:00:00 (from --smpte-start)" in report
