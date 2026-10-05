"""Logic's tempo track: read from the project, and applied to everything that depends on it."""

import gzip
import json
import re
from pathlib import Path

from logic2ableton.ableton_generator import _BUNDLED_TEMPLATE, generate_als
from logic2ableton.ableton_parser import parse_ableton_project
from logic2ableton.logic_parser import _decoded_tempo, parse_logic_project
from logic2ableton.logic_project_data import LogicArrangement, LogicTempoEvent
from logic2ableton.protools_transfer import generate_protools_transfer_from_logic
from logic2ableton.timeline import TempoEvent, Timeline
from scripts.fixture_builders import build_logic_arrangement_project_data, build_synthetic_logicx, write_test_wav

RATE = 44_100


def _project(tmp_path: Path, *, tempo_changes, audio_regions, audio_placements, tempo=120.0, **extra) -> Path:
    """One 20-second Stem.wav, cut into the given regions, under a 120 BPM start tempo.

    At 120 BPM a second is two beats, at 60 one beat, at 240 four.
    """
    data = build_logic_arrangement_project_data(
        tracks={1: "Stem", 2: "Other"},
        sequences=[],
        midi_regions=[],
        audio_files={10: "Stem.wav"},
        audio_regions=audio_regions,
        audio_placements=audio_placements,
        tempo=tempo,
        tempo_changes=tempo_changes,
        **extra,
    )
    logicx = build_synthetic_logicx(tmp_path / "source", project_data=data, used_audio_files=["Stem.wav"])
    write_test_wav(logicx / "Media" / "Audio Files" / "Stem.wav", frames=20 * RATE)
    return logicx


def _region(seconds: float, *, index=0, name="Stem", offset_seconds=0.0) -> dict:
    return {"file": 10, "index": index, "name": name, "offset": int(offset_seconds * RATE), "length": int(seconds * RATE)}


def _clips(project):
    return sorted(
        (c.start_beats, c.clip_name, c.content_offset_samples, c.content_duration_samples) for c in project.audio_files
    )


def test_tempo_changes_are_read_into_the_timeline(tmp_path):
    logicx = _project(
        tmp_path,
        tempo=81.5,
        tempo_changes=[(17, 83.0), (25, 81.5)],
        audio_regions=[_region(2)],
        audio_placements=[{"bar": 1, "track": 1, "file": 10, "lane": 1}],
        markers=[(17, 12, "Chorus")],
    )
    project = parse_logic_project(logicx)

    assert project.tempo == 81.5
    assert project.tempo_track_decoded
    assert [(e.beat, e.bpm) for e in project.timeline.tempo_events] == [(64.0, 83.0), (96.0, 81.5)]
    assert project.timeline.tempo_from_project and project.timeline.markers_from_project
    assert [m.name for m in project.timeline.markers] == ["Chorus"]
    assert not any("tempo" in warning.lower() for warning in project.compatibility_warnings)


def test_project_without_tempo_changes_has_no_tempo_events(tmp_path):
    logicx = _project(
        tmp_path, tempo_changes=None, audio_regions=[_region(2)],
        audio_placements=[{"bar": 1, "track": 1, "file": 10, "lane": 1}],
    )
    project = parse_logic_project(logicx)
    assert project.tempo == 120.0 and project.tempo_track_decoded
    assert project.timeline is None


def test_region_length_is_measured_through_the_tempo_map(tmp_path):
    # Four seconds of audio from bar 1: two seconds at 120 are four beats, the
    # next two at 240 are eight. Logic stores that musical length (12 beats)
    # with the region. Measured at one tempo it looked like 8 beats, and the
    # converter added a second, bogus pass to fill the difference.
    logicx = _project(
        tmp_path,
        tempo_changes=[(2, 240.0)],
        audio_regions=[_region(4)],
        audio_placements=[{"bar": 1, "track": 1, "file": 10, "lane": 1, "loop_beats": 12}],
    )
    project = parse_logic_project(logicx)

    assert _clips(project) == [(0.0, "Stem", 0, 4 * RATE)]
    assert not any("looped" in warning for warning in project.compatibility_warnings)

    als = generate_als(project, tmp_path / "out", copy_audio=False, template_path=_BUNDLED_TEMPLATE)
    rendered = parse_ableton_project(als).clips
    assert [(c.start_beats, round(c.end_beats, 6)) for c in rendered] == [(0.0, 12.0)]


def test_loop_passes_follow_the_tempo_under_them(tmp_path):
    # Two seconds of audio looped across eight beats, with the tempo halving at
    # bar 2: the first pass covers four beats, the later ones two beats each.
    logicx = _project(
        tmp_path,
        tempo_changes=[(2, 60.0)],
        audio_regions=[_region(2)],
        audio_placements=[{"bar": 1, "track": 1, "file": 10, "lane": 1, "loop_beats": 7}],
    )
    project = parse_logic_project(logicx)

    assert _clips(project) == [
        (0.0, "Stem", 0, 2 * RATE),
        (4.0, "Stem", 0, 2 * RATE),
        (6.0, "Stem", 0, RATE),       # the last pass is cut at beat 7: one beat, one second
    ]
    assert [c.start_position_samples for c in sorted(project.audio_files, key=lambda c: c.start_beats)] == [
        0, 2 * RATE, 4 * RATE,
    ]
    assert any("1 looped audio region(s) were written as repeated clips: Stem: Stem x3" in w
               for w in project.compatibility_warnings)


def test_region_time_stamp_and_pro_tools_position_follow_the_tempo_map(tmp_path):
    # Bar 3 is beat 8: four beats at 120 take two seconds, four more at 60 take four.
    logicx = _project(
        tmp_path,
        tempo_changes=[(2, 60.0)],
        audio_regions=[_region(2)],
        audio_placements=[{"bar": 3, "track": 1, "file": 10, "lane": 1}],
    )
    project = parse_logic_project(logicx)
    assert [(c.start_beats, c.start_position_samples) for c in project.audio_files] == [(8.0, 6 * RATE)]

    result = generate_protools_transfer_from_logic(project, tmp_path / "pt")
    manifest = json.loads((result.package_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["tracks"][0]["clips"][0]["time_reference_samples"] == 6 * RATE
    assert any("recreate the tempo changes in Pro Tools" in warning for warning in manifest["compatibility_warnings"])


def test_overlap_cut_is_measured_through_the_tempo_map(tmp_path):
    # Tempo halves at bar 2 (beat 4). "Long" is eight seconds from bar 1: four
    # beats, then six more, ending at beat 10. "Patch" is one second dropped on
    # it at beat 6, so it covers beats 6 to 7.
    logicx = _project(
        tmp_path,
        tempo_changes=[(2, 60.0)],
        audio_regions=[_region(8, name="Long"), _region(1, index=1, name="Patch", offset_seconds=8)],
        audio_placements=[
            {"bar": 1, "track": 1, "file": 10, "lane": 1},
            {"bar": 2.5, "track": 1, "file": 10, "lane": 1, "index": 1},
        ],
    )
    project = parse_logic_project(logicx)

    assert _clips(project) == [
        (0.0, "Long", 0, 4 * RATE),              # beats 0-6 take two seconds plus two
        (6.0, "Patch", 8 * RATE, RATE),
        (7.0, "Long", 5 * RATE, 3 * RATE),       # beat 7 is five seconds in; three seconds remain
    ]


def test_generated_set_carries_the_tempo_changes(tmp_path):
    logicx = _project(
        tmp_path,
        tempo_changes=[(3, 60.0), (5, 240.0)],
        audio_regions=[_region(10)],
        audio_placements=[{"bar": 1, "track": 1, "file": 10, "lane": 1}],
    )
    project = parse_logic_project(logicx)
    als = generate_als(project, tmp_path / "out", copy_audio=False, template_path=_BUNDLED_TEMPLATE)
    xml = gzip.decompress(als.read_bytes()).decode("utf-8")

    points = {(float(time), float(value)) for time, value in re.findall(r'<FloatEvent Id="\d+" Time="([-\d.]+)" Value="([\d.]+)"', xml)}
    assert {(8.0, 60.0), (16.0, 240.0)} <= points
    # Ten seconds from bar 1: 8 beats in 4 s, 8 beats in 8 s... the clip ends inside the 60 BPM stretch.
    clip = parse_ableton_project(als).clips[0]
    assert (clip.start_beats, round(clip.end_beats, 6)) == (0.0, 14.0)
    # one warp marker per tempo change the clip crosses, so playback speed never changes
    markers = [(float(m), float(b)) for m, b in re.findall(r'<WarpMarker Id="\d+" SecTime="([\d.]+)" BeatTime="([\d.]+)"', xml)]
    assert (4.0, 8.0) in markers


def test_supplied_timeline_tempo_replaces_the_projects_own(tmp_path):
    logicx = _project(
        tmp_path,
        tempo_changes=[(2, 240.0)],
        audio_regions=[_region(4)],
        audio_placements=[{"bar": 1, "track": 1, "file": 10, "lane": 1, "loop_beats": 12}],
        markers=[(3, 12, "Count")],
    )
    supplied = Timeline(tempo_events=[TempoEvent(beat=4.0, bpm=60.0)], markers=[], source_path="timeline.json")
    project = parse_logic_project(logicx, timeline=supplied)

    assert [(e.beat, e.bpm) for e in project.timeline.tempo_events] == [(4.0, 60.0)]
    assert not project.timeline.tempo_from_project
    assert project.timeline.markers_from_project and [m.name for m in project.timeline.markers] == ["Count"]
    # Under the supplied map the four seconds cover 4 + 2 = 6 beats, so the
    # stored 12-beat extent is a loop again: a pass from beat 6 (four beats
    # at 60 BPM) and a last one from beat 10, cut at beat 12.
    assert [(c.start_beats, c.content_duration_samples) for c in sorted(project.audio_files, key=lambda c: c.start_beats)] == [
        (0.0, 4 * RATE), (6.0, 4 * RATE), (10.0, 2 * RATE),
    ]


def test_tempo_track_that_does_not_add_up_is_reported(tmp_path):
    # Bar 2 falls two seconds in at 120 BPM; a stored time of three seconds
    # means something other than a plain step happened before it.
    logicx = _project(
        tmp_path,
        tempo_changes=[(2, 60.0, 3.0)],
        audio_regions=[_region(2)],
        audio_placements=[{"bar": 1, "track": 1, "file": 10, "lane": 1}],
    )
    project = parse_logic_project(logicx)
    assert any("does not add up as plain tempo steps around bar 2" in w for w in project.compatibility_warnings)
    assert [(e.beat, e.bpm) for e in project.timeline.tempo_events] == [(4.0, 60.0)]


def test_tempo_changes_are_not_applied_when_the_arrangement_cannot_be_read(tmp_path):
    data = build_logic_arrangement_project_data(
        tracks={1: "Stem"}, sequences=[], midi_regions=[], tempo=120.0, tempo_changes=[(3, 90.0)],
    )
    logicx = build_synthetic_logicx(tmp_path / "source", project_data=data)
    project = parse_logic_project(logicx)

    assert not project.arrangement_decoded and not project.tempo_track_decoded
    assert project.timeline is None
    assert project.compatibility_warnings[0].startswith(
        "Logic's tempo track changes tempo 1 time(s) in this project, first at bar 3 (120 to 90 BPM)."
    )
    assert "not applied" in project.compatibility_warnings[0]


def _events(*triples) -> LogicArrangement:
    """An arrangement whose tempo track holds (bar, bpm, stored seconds) events."""
    return LogicArrangement(tempo_events=[
        LogicTempoEvent(tick=38400 + int(round((bar - 1) * 3840)), bpm=bpm, seconds=seconds)
        for bar, bpm, seconds in triples
    ])


def _decode(arrangement, fallback=100.0):
    warnings: list[str] = []
    base, changes = _decoded_tempo(arrangement, fallback, 4.0, warnings)
    return base, [(event.beat, event.bpm) for event in changes], warnings


def test_decoded_tempo_edge_cases():
    # no tempo track at all: the metadata tempo stands
    assert _decode(LogicArrangement()) == (100.0, [], [])
    # a repeated tempo is not a change
    assert _decode(_events((1, 120.0, 3600.0), (3, 120.0, 3604.0), (5, 90.0, 3608.0))) == (120.0, [(16.0, 90.0)], [])
    # the first event sits after bar 1: its tempo is the one the song starts with
    assert _decode(_events((3, 90.0, 3600.0), (5, 60.0, 3605.333333))) == (90.0, [(16.0, 60.0)], [])


def test_tempos_outside_the_range_live_accepts_are_limited_and_reported():
    base, changes, warnings = _decode(_events((1, 10.0, 3600.0), (2, 15.0, 3624.0), (3, 300.0, 3640.0)))
    # 10 and 15 BPM both become 20, which is then no change at all
    assert (base, changes) == (20.0, [(8.0, 300.0)])
    assert len(warnings) == 1 and "outside the range Live accepts" in warnings[0]


def test_tempo_self_check_runs_when_the_song_starts_at_time_zero():
    # SMPTE start 00:00:00:00: bar 1 is stored as zero. Bar 2 at 120 BPM falls
    # two seconds in, not three.
    _, _, warnings = _decode(_events((1, 120.0, 0.0), (2, 60.0, 3.0)))
    assert len(warnings) == 1 and "does not add up as plain tempo steps around bar 2" in warnings[0]
    assert _decode(_events((1, 120.0, 0.0), (2, 60.0, 2.0)))[2] == []
    # an event before bar 1 has a negative time then, and is still consistent
    assert _decode(_events((0, 120.0, -2.0), (1, 120.0, 0.0), (2, 60.0, 2.0)))[2] == []


def test_tempo_change_before_bar_one_is_reported_and_the_trim_uses_the_bar_one_tempo(tmp_path):
    # A count-in bar at 60 BPM before bar 1, then 120. Four seconds of audio
    # placed on that bar end exactly at bar 1 in Logic. The Live set starts at
    # bar 1 and the trim is worked out at the bar-1 tempo, so two seconds of
    # it are kept: a known limit, and the report says to check it.
    logicx = _project(
        tmp_path,
        tempo_changes=[(0, 60.0, -4.0)],
        audio_regions=[_region(4)],
        audio_placements=[{"bar": 0, "track": 1, "file": 10, "lane": 1}],
        project_start_bar=0,
    )
    project = parse_logic_project(logicx)

    assert project.tempo == 120.0 and project.timeline is None
    assert any("The tempo changes before bar 1 in Logic" in w for w in project.compatibility_warnings)
    assert _clips(project) == [(0.0, "Stem", 2 * RATE, 2 * RATE)]


def test_region_starting_before_bar_one_is_trimmed_through_the_tempo_map(tmp_path):
    # Half a bar before bar 1 at 120 BPM is one second; the tempo change at bar 3 does not touch it.
    logicx = _project(
        tmp_path,
        tempo_changes=[(3, 60.0)],
        audio_regions=[_region(6)],
        audio_placements=[{"bar": 0.5, "track": 1, "file": 10, "lane": 1}],
        project_start_bar=0,
    )
    project = parse_logic_project(logicx)
    assert _clips(project) == [(0.0, "Stem", RATE, 5 * RATE)]

    als = generate_als(project, tmp_path / "out", copy_audio=False, template_path=_BUNDLED_TEMPLATE)
    clip = parse_ableton_project(als).clips[0]
    # five seconds from bar 1: eight beats in four seconds, then one more at 60 BPM
    assert (clip.start_beats, round(clip.end_beats, 6)) == (0.0, 9.0)


def test_supplied_tempo_is_used_when_the_arrangement_cannot_be_read(tmp_path):
    data = build_logic_arrangement_project_data(
        tracks={1: "Stem"}, sequences=[], midi_regions=[], tempo=120.0, tempo_changes=[(3, 90.0)],
    )
    logicx = build_synthetic_logicx(tmp_path / "source", project_data=data)
    supplied = Timeline(tempo_events=[TempoEvent(beat=8.0, bpm=100.0)], markers=[], source_path="timeline.json")
    project = parse_logic_project(logicx, timeline=supplied)

    assert [(e.beat, e.bpm) for e in project.timeline.tempo_events] == [(8.0, 100.0)]
    assert not project.timeline.tempo_from_project
    assert not any("tempo track" in warning for warning in project.compatibility_warnings)
