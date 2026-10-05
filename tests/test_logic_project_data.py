"""Structured ProjectData decoding: regions, loops, tracks, markers, audio regions."""

import json
import os
from pathlib import Path

import pytest

from logic2ableton.logic_parser import parse_logic_project
from logic2ableton.logic_project_data import (
    PROJECT_START_TICKS,
    SEQUENCE_ORIGIN_TICKS,
    decode_project_data,
    iter_records,
    scan_objects,
)
from scripts.fixture_builders import (
    _logic_object,
    _logic_placement_record,
    build_logic_arrangement_project_data,
    build_synthetic_logicx,
    logic_note_record,
    write_test_wav,
)

BAR = 3840


def _two_track_blob(**overrides):
    spec = dict(
        tracks={0x50: "Click", 0x58: "Cue Vocal", 0x60: "Stems"},
        sequences=[
            {
                "id": 44,
                "name": "Click",
                "length": BAR,
                "notes": [
                    (0, 60, 100, 120),
                    logic_note_record(960, 62, 90, 120, flag=0x81, variant=0x00, nudge=-51, extension=True),
                    (1920, 62, 90, 120),
                    (2880, 62, 90, 120),
                    (4800, 64, 90, 120),  # past the content window: never plays
                ],
            },
            {
                "id": 80,
                "name": "Cue",
                "length": 2 * BAR,
                "start": 960,  # content window begins one beat in
                "notes": [(0, 48, 80, 480), (960, 53, 86, 1809), (2738, 55, 86, 1809)],
            },
        ],
        midi_regions=[
            {"bar": 3, "track": 0x50, "sequence": 44, "loop_bars": 4, "lane": 2},
            {"bar": 5, "track": 0x58, "sequence": 80, "lane": 3},
            {"bar": 9, "track": 0x58, "sequence": 80, "lane": 3, "muted": True},
        ],
        audio_files={0: "Stem.wav", 4: "Tone.wav"},
        audio_regions=[
            {"file": 0, "index": 0, "name": "Stem", "offset": 0, "length": 88200},
            {"file": 0, "index": 1, "name": "Stem.1", "offset": 44100, "length": 44100},
            {"file": 4, "index": 0, "name": "Tone", "offset": 0, "length": 22050},
        ],
        audio_placements=[
            {"bar": 3, "track": 0x60, "file": 0, "index": 0, "lane": 5},
            {"bar": 7, "track": 0x60, "file": 0, "index": 1, "lane": 5},
            {"bar": 3, "track": 0x60, "file": 4, "index": 0, "lane": 6, "muted": True},
        ],
        markers=[(3, 48, "Count"), (5, 16, "Intro"), (9, 12, "1. Verse für alle")],
        project_start_bar=-3,
        tempo=150.0,
    )
    spec.update(overrides)
    return build_logic_arrangement_project_data(**spec)


def test_decode_regions_loops_markers_and_tracks():
    arrangement = decode_project_data(_two_track_blob())

    assert arrangement.format_version == 1
    assert arrangement.project_start_bar == -3
    assert arrangement.tempo_bpm == 150.0
    assert arrangement.track_names == {0x50: "Click", 0x58: "Cue Vocal", 0x60: "Stems"}

    click = arrangement.sequences[44]
    assert [(n.tick - SEQUENCE_ORIGIN_TICKS, n.pitch, n.velocity, n.duration) for n in click.notes] == [
        (0, 60, 100, 120), (960, 62, 90, 120), (1920, 62, 90, 120), (2880, 62, 90, 120), (4800, 64, 90, 120),
    ]
    assert click.notes[1].nudge == -51
    assert click.content_length == BAR
    cue = arrangement.sequences[80]
    assert (cue.content_start, cue.content_length) == (960, 2 * BAR)

    midi = [p for p in arrangement.regions if p.kind == "midi"]
    assert [(arrangement.region_beats(p.tick, 4.0), p.track_id, p.sequence_id, p.looped, p.loop_span, p.muted)
            for p in midi] == [
        (8.0, 0x50, 44, True, 4 * BAR, False),
        (16.0, 0x58, 80, False, None, False),
        (32.0, 0x58, 80, False, None, True),
    ]
    assert midi[0].tick == PROJECT_START_TICKS + 6 * BAR  # bar 3 is six bars after a project start at bar -3

    assert [(arrangement.marker_beats(m.tick), m.name) for m in arrangement.markers] == [
        (8.0, "Count"), (16.0, "Intro"), (32.0, "1. Verse für alle"),
    ]

    audio = [p for p in arrangement.regions if p.kind == "audio"]
    assert [(arrangement.region_beats(p.tick, 4.0), p.audio_file_id, p.audio_region_index, p.muted) for p in audio] == [
        (8.0, 0, 0, False), (8.0, 4, 0, True), (24.0, 0, 1, False),
    ]
    assert arrangement.audio_files == {0: "Stem.wav", 4: "Tone.wav"}
    stem_cut = arrangement.audio_regions[(0, 1)]
    assert (stem_cut.name, stem_cut.content_offset, stem_cut.content_length) == ("Stem.1", 44100, 44100)


def test_version_two_header_decodes_with_bar_one_start():
    arrangement = decode_project_data(_two_track_blob(version=2, project_start_bar=None))
    assert arrangement.format_version == 2
    assert arrangement.project_start_bar is None
    click = next(p for p in arrangement.regions if p.kind == "midi")
    assert arrangement.region_beats(click.tick, 4.0) == 8.0


def test_record_walk_steps_over_unknown_events_at_their_real_size():
    # An event type the decoder does not know, three units long: its two
    # continuation units carry a class byte with the high bit set.
    unknown = b"\x77\x00\x00\x00" + b"\x00" * 12 + (b"\x00" * 7 + b"\xaa" + b"\x00" * 8) * 2
    blob = _two_track_blob(sequences=[{
        "id": 44,
        "name": "Click",
        "length": BAR,
        "notes": [(0, 60, 100, 120), unknown, (960, 62, 90, 120)],
    }])
    arrangement = decode_project_data(blob)
    assert [n.pitch for n in arrangement.sequences[44].notes] == [60, 62]
    seq = next(o for o in scan_objects(blob) if o.tag == "EvSq" and o.id1 == 44)
    assert [(status, size) for status, _, _, size in iter_records(blob, seq)] == [(0x90, 32), (0x77, 48), (0x90, 32)]


def test_region_with_attached_fade_data_does_not_hide_the_regions_after_it():
    """A region that has fades carries ten extra units. Reading it with a fixed
    record size stopped the walk there and dropped every later region."""
    blob = build_logic_arrangement_project_data(
        tracks={1: "Original", 2: "Time Code", 3: "Pilot"},
        sequences=[],
        midi_regions=[],
        audio_files={10: "Original.aif", 20: "LTC.wav", 30: "Pilot.aif"},
        audio_regions=[
            {"file": 10, "index": 0, "name": "Original", "offset": 0, "length": 44_100},
            {"file": 20, "index": 0, "name": "LTC", "offset": 0, "length": 44_100},
            {"file": 30, "index": 0, "name": "Pilot", "offset": 0, "length": 44_100},
        ],
        audio_placements=[
            {"bar": 3, "track": 1, "file": 10, "lane": 1, "muted": True, "fades": True},
            {"bar": 3, "track": 2, "file": 20, "lane": 2},
            {"bar": 3, "track": 3, "file": 30, "lane": 3},
        ],
    )
    arrangement = decode_project_data(blob)
    audio = [p for p in arrangement.regions if p.kind == "audio"]
    assert [(arrangement.audio_files[p.audio_file_id], p.muted) for p in audio] == [
        ("Original.aif", True), ("LTC.wav", False), ("Pilot.aif", False),
    ]
    seq = next(o for o in scan_objects(blob) if o.tag == "EvSq" and o.id1 == 4)
    assert [size for _, _, _, size in iter_records(blob, seq)] == [240, 80, 80]


def _event_sequence(records: bytes, terminator: bytes) -> tuple[bytes, object]:
    """An EvSq object holding ``records`` and the given 12-byte terminator."""
    payload = b"\x00\x00\x00\x00" + records + terminator
    blob = _logic_object("EvSq", payload, kind=1, id1=7, id2=1)
    return blob, next(o for o in scan_objects(blob) if o.tag == "EvSq")


def test_record_walk_ends_at_either_terminator_and_skips_stray_units():
    note = logic_note_record(0, 60, 100, 120)
    stray = b"\x00" * 7 + b"\x88" + b"\x00" * 8          # a continuation unit with no event before it
    after = logic_note_record(960, 62, 90, 120)

    usual = b"\xf1\x00\x00\x00\xff\xff\xff\x3f\x00\x00\x00\x00"
    blob, seq = _event_sequence(stray + note, usual)
    assert [(status, size) for status, _, _, size in iter_records(blob, seq)] == [(0x90, 32)]

    # Some sequences end with a zero status instead of 0xF1; nothing after it is an event.
    zero_status = b"\x00\x00\x00\x00\xff\xff\xff\x3f\x00\x00\x00\x00"
    blob, seq = _event_sequence(note + zero_status + b"\x00" * 4 + after, usual)
    assert [(status, tick) for status, tick, _, _ in iter_records(blob, seq)] == [(0x90, 38400)]


def test_region_event_without_its_class_bytes_is_not_a_region():
    good = _logic_placement_record(audio=True, tick=34560, track=1, lane=1, flags=0, loop_span=None, audio_file=10)
    wrong_class = bytearray(_logic_placement_record(
        audio=True, tick=38400, track=2, lane=2, flags=0, loop_span=None, audio_file=20,
    ))
    wrong_class[39] = 0x88  # an audio region carries 0xBC here
    blob, seq = _event_sequence(good + bytes(wrong_class), b"\xf1\x00\x00\x00\xff\xff\xff\x3f\x00\x00\x00\x00")
    assert [size for _, _, _, size in iter_records(blob, seq)] == [80, 80]
    arrangement = decode_project_data(b"\x00" * 24 + _logic_object("Song", b"\x00" * 376, kind=3) + blob)
    assert [(p.kind, p.track_id) for p in arrangement.placements] == [("audio", 1)]


def test_tempo_track_is_read_as_steps_from_bar_one():
    blob = _two_track_blob(tempo=81.5, tempo_changes=[(17, 83.0), (25, 81.5)])
    arrangement = decode_project_data(blob)
    assert [(event.tick, event.bpm) for event in arrangement.tempo_events] == [
        (38400, 81.5), (38400 + 16 * BAR, 83.0), (38400 + 24 * BAR, 81.5),
    ]
    # a project without changes still has its one tempo event
    assert [event.bpm for event in decode_project_data(_two_track_blob()).tempo_events] == [150.0]


def test_empty_or_foreign_data_yields_nothing():
    assert decode_project_data(b"").regions == []
    assert decode_project_data(b"qSvE" * 50).regions == []


def _synthetic_project(tmp_path: Path, blob: bytes) -> Path:
    logicx = build_synthetic_logicx(tmp_path, project_data=blob)
    audio_dir = logicx / "Media" / "Audio Files"
    write_test_wav(audio_dir / "Stem.wav", frames=176_400)
    write_test_wav(audio_dir / "Tone.wav", frames=44_100)
    return logicx


def test_parse_logic_project_places_regions_from_the_arrangement(tmp_path):
    logicx = _synthetic_project(tmp_path, _two_track_blob())
    project = parse_logic_project(logicx)

    assert project.arrangement_decoded is True
    assert project.project_start_bar == -3

    assert [t.name for t in project.midi_tracks] == ["Click", "Cue Vocal"]
    click = project.midi_tracks[0]
    assert len(click.regions) == 1
    region = click.regions[0]
    assert (region.start_beats, region.length_beats, region.loop_span_beats, region.is_looping) == (8.0, 4.0, 16.0, True)
    # the note past the content window is dropped; the four inside repeat four times
    assert [(n.start_beats, n.pitch) for n in region.notes] == [(0.0, 60), (1.0, 62), (2.0, 62), (3.0, 62)]
    assert len(click.notes) == 16
    assert click.notes[0].start_beats == 8.0 and click.notes[-1].start_beats == 23.0

    cue = project.midi_tracks[1]
    assert len(cue.regions) == 1  # the muted copy at bar 9 is skipped
    assert cue.regions[0].start_beats == 16.0
    # content starts one beat in: the note at 0 is outside the window, the rest shift left
    assert [(round(n.start_beats, 4), n.pitch, round(n.duration_beats, 4)) for n in cue.regions[0].notes] == [
        (0.0, 53, 1.8844), (1.8521, 55, 1.8844),
    ]
    assert any("muted MIDI region" in w for w in project.compatibility_warnings)

    assert project.track_names == ["Stems"]
    clips = sorted(project.audio_files, key=lambda r: r.start_beats)
    assert [(c.clip_name, c.start_beats, c.content_offset_samples, c.content_duration_samples) for c in clips] == [
        ("Stem", 8.0, 0, 88200), ("Stem.1", 24.0, 44100, 44100),
    ]
    assert clips[0].start_position_samples == round(8.0 * 60 / project.tempo * 44100)
    assert any("muted audio region" in w for w in project.compatibility_warnings)

    assert project.timeline is not None
    assert [(m.beat, m.name) for m in project.timeline.markers] == [
        (8.0, "Count"), (16.0, "Intro"), (32.0, "1. Verse für alle"),
    ]


def test_parse_logic_project_falls_back_to_signature_scan_without_arrangement(tmp_path):
    from scripts.fixture_builders import build_logic_project_data

    logicx = build_synthetic_logicx(tmp_path, project_data=build_logic_project_data([[(60, 100, 38400, 480)]]))
    project = parse_logic_project(logicx)
    assert project.arrangement_decoded is False
    assert [t.name for t in project.midi_tracks] == ["MIDI 1"]
    assert project.midi_tracks[0].regions == []


def test_region_before_bar_one_is_moved_and_trimmed(tmp_path):
    blob = _two_track_blob(
        midi_regions=[{"bar": -1, "track": 0x50, "sequence": 44, "loop_bars": 4, "lane": 2}],
        audio_placements=[
            {"bar": 0.5, "track": 0x60, "file": 0, "index": 0, "lane": 5},
            {"bar": -2, "track": 0x60, "file": 4, "index": 0, "lane": 6},
        ],
        markers=[],
    )
    logicx = _synthetic_project(tmp_path, blob)
    project = parse_logic_project(logicx)
    assert project.midi_tracks[0].regions[0].start_beats == 0.0
    assert any("starts before bar 1" in w for w in project.compatibility_warnings)
    assert [c.clip_name for c in project.audio_files] == ["Stem"]  # the tone ends before bar 1
    assert any("ends before bar 1" in w for w in project.compatibility_warnings)
    clip = project.audio_files[0]
    assert clip.start_beats == 0.0
    # half a bar (two beats at the project tempo) was trimmed off the front of the file
    trimmed = round(2.0 * 60 / project.tempo * 44100)
    assert clip.content_offset_samples == trimmed
    assert clip.content_duration_samples == 88200 - trimmed


def test_project_with_tempo_changes_says_they_are_not_converted(tmp_path):
    changing = parse_logic_project(_synthetic_project(
        tmp_path / "changing", _two_track_blob(tempo=81.5, tempo_changes=[(17, 83.0), (25, 81.5)]),
    ))
    assert changing.compatibility_warnings[0].startswith(
        "Logic's tempo track changes tempo 2 time(s) in this project, first at bar 17 (81.5 to 83 BPM)."
    )
    # The set keeps the project tempo (120 in this bundle's metadata), whatever the first event says.
    assert "the Live set stays at 120 BPM throughout" in changing.compatibility_warnings[0]
    steady = parse_logic_project(_synthetic_project(tmp_path / "steady", _two_track_blob()))
    assert not any("tempo track" in warning for warning in steady.compatibility_warnings)

    off_bar = parse_logic_project(_synthetic_project(
        tmp_path / "off-bar", _two_track_blob(tempo=120.0, tempo_changes=[(3.625, 90.0)]),
    ))
    assert "first at bar 3 beat 3.5 (120 to 90 BPM)" in off_bar.compatibility_warnings[0]


# Real Logic projects can be checked against expectations kept outside the
# repository: L2A_LOGIC_ARRANGEMENT_CASES points at a JSON list of
# {"logicx": path, "alternative": int, "project_start_bar": int, "markers": [[beat, name], ...],
#  "midi_tracks": {name: [regions, unrolled_notes]}, "audio_clips": [[track, clip, beat], ...],
#  "audio_regions": int, "tempo_events": int}. Only "logicx" is required.
_CASES = os.environ.get("L2A_LOGIC_ARRANGEMENT_CASES")


@pytest.mark.skipif(not _CASES or not Path(_CASES).exists(), reason="no real-project expectations configured")
def test_real_projects_match_recorded_expectations():
    for case in json.loads(Path(_CASES).read_text(encoding="utf-8")):
        label = f"{case['logicx']} alternative {case.get('alternative')}"
        project = parse_logic_project(Path(case["logicx"]), alternative=case.get("alternative"))
        assert project.arrangement_decoded, label
        # Logic's own list of used audio files is the yardstick: a file on it
        # without a region means part of the arrangement was not read.
        assert not any("MISSING from the result" in w for w in project.compatibility_warnings), label
        if "project_start_bar" in case:
            assert project.project_start_bar == case["project_start_bar"], label
        if "markers" in case:
            assert [[m.beat, m.name] for m in project.timeline.markers] == case["markers"], label
        if "midi_tracks" in case:
            assert {t.name: [len(t.regions), t.note_count] for t in project.midi_tracks} == {
                name: list(values) for name, values in case["midi_tracks"].items()
            }, label
        if "audio_clips" in case:
            clips = sorted(
                [[c.track_name, c.clip_name, c.start_beats] for c in project.audio_files],
                key=lambda c: (c[2], c[0], c[1]),
            )
            assert clips == sorted(case["audio_clips"], key=lambda c: (c[2], c[0], c[1])), label
        if "audio_regions" in case or "tempo_events" in case:
            data = (
                Path(case["logicx"]) / "Alternatives" / f"{project.alternative:03d}" / "ProjectData"
            ).read_bytes()
            arrangement = decode_project_data(data)
            if "audio_regions" in case:
                assert sum(1 for p in arrangement.regions if p.kind == "audio") == case["audio_regions"], label
            if "tempo_events" in case:
                assert len(arrangement.tempo_events) == case["tempo_events"], label
