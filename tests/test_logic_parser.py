from pathlib import Path

from logic2ableton.logic_parser import (
    discover_audio_files,
    extract_plugins,
    extract_regions,
    parse_logic_project,
    parse_metadata,
    parse_project_info,
)

TEST_PROJECT = Path("Might Last Forever.logicx")


# Task 3 tests
def test_parse_project_info():
    info = parse_project_info(TEST_PROJECT)
    assert info["name"] == "Might Last Forever"
    assert "Logic Pro" in info["last_saved_from"]
    assert info["variant_names"]["0"] == "Might Last Forever"


def test_parse_metadata():
    meta = parse_metadata(TEST_PROJECT, alternative=0)
    assert meta["tempo"] == 120.0
    assert meta["time_sig_numerator"] == 4
    assert meta["time_sig_denominator"] == 4
    assert meta["sample_rate"] == 44100
    assert meta["num_tracks"] == 12
    assert meta["song_key"] == "C"
    assert meta["song_gender_key"] == "major"
    assert len(meta["audio_files"]) == 28
    assert len(meta["unused_audio_files"]) == 7


# Task 4 tests
def test_extract_plugins_from_project_data():
    plugins = extract_plugins(TEST_PROJECT, alternative=0)
    assert len(plugins) == 24
    subtypes = [p.au_subtype for p in plugins]
    assert "TG5M" in subtypes
    assert "76CM" in subtypes
    assert "L1CM" in subtypes


def test_extract_plugins_vintage_vocal():
    plugins = extract_plugins(TEST_PROJECT, alternative=0)
    vintage = [p for p in plugins if p.name == "Vintage Vocal"]
    assert len(vintage) == 1
    assert vintage[0].au_subtype == "TG5M"


# Task 5 tests
def test_discover_audio_files():
    refs = discover_audio_files(TEST_PROJECT)
    assert len(refs) == 38
    track_names = sorted(set(r.track_name for r in refs))
    assert "KICK IN" in track_names
    assert "Tyler Amp" in track_names
    assert "scratch vox 2" in track_names


def test_discover_audio_files_takes():
    refs = discover_audio_files(TEST_PROJECT)
    kick_refs = [r for r in refs if r.track_name == "KICK IN"]
    assert len(kick_refs) == 3
    take_numbers = sorted(r.take_number for r in kick_refs)
    assert take_numbers == [1, 2, 3]


def test_discover_audio_files_comps():
    refs = discover_audio_files(TEST_PROJECT)
    comp_refs = [r for r in refs if r.is_comp]
    assert len(comp_refs) == 1
    assert comp_refs[0].comp_name == "Comp A"


# Task 6 tests
def test_parse_logic_project():
    project = parse_logic_project(TEST_PROJECT, alternative=0)
    assert project.name == "Might Last Forever"
    assert project.tempo == 120.0
    assert project.time_sig_numerator == 4
    assert project.sample_rate == 44100
    assert len(project.audio_files) == 38
    assert len(project.plugins) == 24
    assert project.alternative == 0


def test_parse_logic_project_track_names():
    project = parse_logic_project(TEST_PROJECT, alternative=0)
    assert "KICK IN" in project.track_names
    assert "Tyler Amp" in project.track_names
    assert "SNARE" in project.track_names
    assert "BASS GUITAR" in project.track_names
    assert "keys" in project.track_names
    assert "scratch vox 1" in project.track_names
    assert "scratch vox 2" in project.track_names


# Phase 2: Region timing tests

def test_extract_regions_count():
    regions = extract_regions(TEST_PROJECT, alternative=0)
    # BWF timestamps exist for all recorded WAV files (not imported MP3s etc.)
    assert len(regions) >= 35


def test_extract_regions_kick_in_01():
    """KICK IN#01 starts at bar 2 (beat 4) = 88,200 samples after SMPTE offset."""
    regions = extract_regions(TEST_PROJECT, alternative=0)
    assert regions["KICK IN#01.wav"] == 88_200


def test_extract_regions_kick_in_02():
    """KICK IN#02 starts at bar 320 (beat 1276) = 28,135,800 samples after SMPTE offset."""
    regions = extract_regions(TEST_PROJECT, alternative=0)
    assert regions["KICK IN#02.wav"] == 28_135_800


def test_extract_regions_missing_project():
    regions = extract_regions(Path("/nonexistent/project.logicx"), alternative=0)
    assert regions == {}


def test_parse_logic_project_has_start_positions():
    project = parse_logic_project(TEST_PROJECT, alternative=0)
    kick_01 = [r for r in project.audio_files if r.filename == "KICK IN#01.wav"]
    assert len(kick_01) == 1
    assert kick_01[0].start_position_samples == 88_200  # bar 2

    kick_02 = [r for r in project.audio_files if r.filename == "KICK IN#02.wav"]
    assert len(kick_02) == 1
    assert kick_02[0].start_position_samples == 28_135_800  # bar 320
