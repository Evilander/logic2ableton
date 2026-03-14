from conftest import create_test_als

from logic2ableton.ableton_parser import parse_ableton_project


def test_parse_ableton_project_extracts_core_metadata(tmp_path):
    als_path = create_test_als(tmp_path)

    project = parse_ableton_project(als_path)

    assert project.name == "Demo Set"
    assert project.tempo == 128.0
    assert project.time_sig_numerator == 4
    assert project.time_sig_denominator == 4
    assert len(project.audio_tracks) == 2
    assert len(project.clips) == 2
    assert len(project.locators) == 1
    assert project.locators[0].name == "Verse"


def test_parse_ableton_project_flags_warped_and_missing_files(tmp_path):
    als_path = create_test_als(tmp_path, include_missing_clip=True, include_warped_clip=True)

    project = parse_ableton_project(als_path)

    assert len(project.clips) == 3
    assert any(clip.is_warped for clip in project.clips)
    assert any("could not be resolved" in warning for warning in project.compatibility_warnings)
    assert any("use Ableton warping" in warning for warning in project.compatibility_warnings)


def test_parse_ableton_project_preserves_clip_positions(tmp_path):
    als_path = create_test_als(tmp_path, include_warped_clip=False)

    project = parse_ableton_project(als_path)

    clips = {clip.clip_name: clip for clip in project.clips}
    assert clips["Kick Loop"].start_beats == 1.0
    assert clips["Kick Loop"].end_beats == 5.0
    assert clips["Lead Vox"].source_in_beats == 0.5


def test_parse_ableton_project_falls_back_to_relative_paths_when_absolute_is_stale(tmp_path):
    als_path = create_test_als(tmp_path, stale_absolute_paths=True)

    project = parse_ableton_project(als_path)

    assert all(clip.source_path is not None for clip in project.clips)
    assert not any("could not be resolved" in warning for warning in project.compatibility_warnings)
