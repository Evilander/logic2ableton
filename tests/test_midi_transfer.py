"""Tests for Ableton MIDI clip extraction and Standard MIDI File export."""

import gzip
import struct
import xml.etree.ElementTree as ET
from pathlib import Path

from logic2ableton.ableton_parser import parse_ableton_project
from logic2ableton.logic_transfer import _build_midi_note_file, generate_logic_transfer
from logic2ableton.models import AbletonMidiClip, AbletonMidiNote, AbletonMidiTrack
from logic2ableton.smf import MIDI_TICKS_PER_QUARTER


def _add_midi_clip(
    events,
    *,
    clip_start,
    clip_end,
    name,
    key_tracks,
    looping=False,
    loop_start=0.0,
    loop_end=None,
    start_relative=0.0,
):
    """key_tracks: list of (pitch, [(time, duration, velocity), ...])."""
    clip = ET.SubElement(events, "MidiClip")
    clip.set("Time", str(clip_start))
    ET.SubElement(clip, "CurrentStart").set("Value", str(clip_start))
    ET.SubElement(clip, "CurrentEnd").set("Value", str(clip_end))
    ET.SubElement(clip, "Name").set("Value", name)
    loop = ET.SubElement(clip, "Loop")
    ET.SubElement(loop, "LoopStart").set("Value", str(loop_start))
    ET.SubElement(loop, "LoopEnd").set("Value", str(clip_end - clip_start if loop_end is None else loop_end))
    ET.SubElement(loop, "StartRelative").set("Value", str(start_relative))
    ET.SubElement(loop, "LoopOn").set("Value", "true" if looping else "false")
    ET.SubElement(clip, "Disabled").set("Value", "false")
    notes_el = ET.SubElement(clip, "Notes")
    key_tracks_el = ET.SubElement(notes_el, "KeyTracks")
    for pitch, note_specs in key_tracks:
        kt = ET.SubElement(key_tracks_el, "KeyTrack")
        kt_notes = ET.SubElement(kt, "Notes")
        for time, duration, velocity in note_specs:
            event = ET.SubElement(kt_notes, "MidiNoteEvent")
            event.set("Time", str(time))
            event.set("Duration", str(duration))
            event.set("Velocity", str(velocity))
            event.set("IsEnabled", "true")
        ET.SubElement(kt, "MidiKey").set("Value", str(pitch))


def create_midi_als(tmp_path: Path) -> Path:
    project_dir = tmp_path / "Midi Project"
    project_dir.mkdir(parents=True)

    root = ET.Element("Ableton")
    live_set = ET.SubElement(root, "LiveSet")
    transport = ET.SubElement(live_set, "Transport")
    ET.SubElement(ET.SubElement(transport, "Tempo"), "Manual").set("Value", "120")
    ts = ET.SubElement(ET.SubElement(transport, "TimeSignatures"), "RemoteableTimeSignature")
    ET.SubElement(ts, "Numerator").set("Value", "4")
    ET.SubElement(ts, "Denominator").set("Value", "4")

    tracks = ET.SubElement(live_set, "Tracks")
    midi_track = ET.SubElement(tracks, "MidiTrack")
    ET.SubElement(ET.SubElement(midi_track, "Name"), "EffectiveName").set("Value", "Bass")
    events = ET.SubElement(
        ET.SubElement(
            ET.SubElement(ET.SubElement(midi_track, "DeviceChain"), "MainSequencer"),
            "ClipTimeable",
        ),
        "ArrangerAutomation",
    )
    events = ET.SubElement(events, "Events")
    _add_midi_clip(
        events,
        clip_start=4.0,
        clip_end=8.0,
        name="Bassline",
        key_tracks=[
            (36, [(0.0, 1.0, 100), (2.0, 0.5, 80)]),
            (48, [(1.0, 1.0, 120)]),
        ],
    )

    als_path = project_dir / "Midi Project.als"
    with gzip.open(als_path, "wb") as handle:
        handle.write(ET.tostring(root, encoding="utf-8", xml_declaration=True))
    return als_path


def read_midi_note_ons(data: bytes) -> list[tuple[int, int, int]]:
    """Return (tick, pitch, velocity) for every note-on in a Type-0 SMF."""
    assert data[:4] == b"MThd"
    idx = data.index(b"MTrk")
    length = struct.unpack(">I", data[idx + 4 : idx + 8])[0]
    track = data[idx + 8 : idx + 8 + length]

    def read_var(pos):
        val = 0
        while True:
            byte = track[pos]
            pos += 1
            val = (val << 7) | (byte & 0x7F)
            if not byte & 0x80:
                return val, pos

    notes: list[tuple[int, int, int]] = []
    pos = 0
    tick = 0
    while pos < len(track):
        delta, pos = read_var(pos)
        tick += delta
        status = track[pos]
        if status == 0xFF:
            pos += 1
            pos += 1  # meta type
            mlen, pos = read_var(pos)
            pos += mlen
        elif status & 0xF0 in (0x80, 0x90):
            event = status & 0xF0
            pitch = track[pos + 1]
            velocity = track[pos + 2]
            pos += 3
            if event == 0x90 and velocity > 0:
                notes.append((tick, pitch, velocity))
        else:
            pos += 1
    return notes


def test_parse_midi_track_extracts_absolute_notes(tmp_path):
    als = create_midi_als(tmp_path)
    project = parse_ableton_project(als)

    assert len(project.midi_tracks) == 1
    track = project.midi_tracks[0]
    assert track.name == "Bass"
    assert track.note_count == 3

    # Sorted by (start_beats, pitch); clip starts at beat 4.
    positions = [(n.pitch, n.start_beats, n.duration_beats, n.velocity) for n in track.notes]
    assert positions == [
        (36, 4.0, 1.0, 100),
        (48, 5.0, 1.0, 120),
        (36, 6.0, 0.5, 80),
    ]
    assert project.total_midi_notes == 3


def _clip_scenario_als(tmp_path: Path, scenarios) -> Path:
    project_dir = tmp_path / "Loop Project"
    project_dir.mkdir(parents=True)
    root = ET.Element("Ableton")
    live_set = ET.SubElement(root, "LiveSet")
    transport = ET.SubElement(live_set, "Transport")
    ET.SubElement(ET.SubElement(transport, "Tempo"), "Manual").set("Value", "120")
    tracks = ET.SubElement(live_set, "Tracks")
    midi_track = ET.SubElement(tracks, "MidiTrack")
    ET.SubElement(ET.SubElement(midi_track, "Name"), "EffectiveName").set("Value", "Keys")
    events = ET.SubElement(
        ET.SubElement(
            ET.SubElement(ET.SubElement(midi_track, "DeviceChain"), "MainSequencer"),
            "ClipTimeable",
        ),
        "ArrangerAutomation",
    )
    events = ET.SubElement(events, "Events")
    for scenario in scenarios:
        _add_midi_clip(events, **scenario)
    als_path = project_dir / "Loop Project.als"
    with gzip.open(als_path, "wb") as handle:
        handle.write(ET.tostring(root, encoding="utf-8", xml_declaration=True))
    return als_path


def _key_tracks(notes):
    """notes: (content_time, duration, pitch) -> the helper's per-pitch layout."""
    by_pitch: dict[int, list[tuple[float, float, int]]] = {}
    for time, duration, pitch in notes:
        by_pitch.setdefault(pitch, []).append((time, duration, 100))
    return sorted(by_pitch.items())


# Expected placements were produced by Live 12.4.3 itself: the same clips were
# consolidated in the Arrangement (Ctrl+J), and the rendered notes read back.
LIVE_VERIFIED_SCENARIOS = [
    (
        dict(name="S1 trimmed no loop", clip_start=0.0, clip_end=4.0, loop_start=4.0, loop_end=8.0,
             key_tracks=_key_tracks([(0, 0.5, 36), (1, 0.5, 37), (2, 0.5, 38), (3, 0.5, 39), (3.5, 2.0, 46),
                                     (4, 0.5, 40), (5, 0.5, 41), (6, 0.5, 42), (7, 0.5, 43), (8, 0.5, 44), (9, 0.5, 45)])),
        [(0.0, 40, 0.5), (0.0, 46, 1.5), (1.0, 41, 0.5), (2.0, 42, 0.5), (3.0, 43, 0.5)],
    ),
    (
        dict(name="S2 loop with start offset", clip_start=8.0, clip_end=18.0, loop_start=2.0, loop_end=6.0,
             start_relative=1.0, looping=True, key_tracks=_key_tracks([(t, 0.5, 48 + t) for t in range(8)])),
        [(8.0, 51, 0.5), (9.0, 52, 0.5), (10.0, 53, 0.5), (11.0, 50, 0.5), (12.0, 51, 0.5), (13.0, 52, 0.5),
         (14.0, 53, 0.5), (15.0, 50, 0.5), (16.0, 51, 0.5), (17.0, 52, 0.5)],
    ),
    (
        dict(name="S3 unroll and loop-end crossing", clip_start=20.0, clip_end=30.0, loop_start=0.0, loop_end=4.0,
             looping=True, key_tracks=_key_tracks([(0, 0.5, 62), (3.5, 1.0, 60)])),
        [(20.0, 62, 0.5), (23.5, 60, 0.5), (24.0, 62, 0.5), (27.5, 60, 0.5), (28.0, 62, 0.5)],
    ),
    (
        dict(name="S4 clip shorter than loop", clip_start=32.0, clip_end=36.0, loop_start=0.0, loop_end=8.0,
             looping=True, key_tracks=_key_tracks([(0, 0.5, 64), (2, 0.5, 65), (4, 0.5, 66), (6, 0.5, 67)])),
        [(32.0, 64, 0.5), (34.0, 65, 0.5)],
    ),
    (
        dict(name="S5 note crossing clip end", clip_start=40.0, clip_end=44.0, loop_start=0.0, loop_end=4.0,
             key_tracks=_key_tracks([(1, 0.5, 72), (3, 3.0, 70)])),
        [(41.0, 72, 0.5), (43.0, 70, 1.0)],
    ),
    (
        dict(name="S6 loop start nonzero with offset", clip_start=48.0, clip_end=58.0, loop_start=4.0, loop_end=8.0,
             start_relative=2.0, looping=True, key_tracks=_key_tracks([(t, 0.5, 80 + t) for t in range(10)])),
        [(48.0, 86, 0.5), (49.0, 87, 0.5), (50.0, 84, 0.5), (51.0, 85, 0.5), (52.0, 86, 0.5), (53.0, 87, 0.5),
         (54.0, 84, 0.5), (55.0, 85, 0.5), (56.0, 86, 0.5), (57.0, 87, 0.5)],
    ),
    (
        dict(name="S7 loop on, note straddles start marker", clip_start=60.0, clip_end=68.0, loop_start=0.0,
             loop_end=4.0, start_relative=1.0, looping=True, key_tracks=_key_tracks([(0.5, 1.0, 90), (2, 0.5, 91)])),
        [(60.0, 90, 0.5), (61.0, 91, 0.5), (63.5, 90, 1.0), (65.0, 91, 0.5), (67.5, 90, 0.5)],
    ),
    (
        dict(name="S8 loop on, note starts outside the brace", clip_start=72.0, clip_end=80.0, loop_start=2.0,
             loop_end=6.0, looping=True, key_tracks=_key_tracks([(1.5, 1.0, 92), (5.5, 1.0, 93), (3, 0.5, 94)])),
        [(73.0, 94, 0.5), (75.5, 93, 0.5), (77.0, 94, 0.5), (79.5, 93, 0.5)],
    ),
    (
        dict(name="S9 loop off, clip longer than its window", clip_start=84.0, clip_end=88.0, loop_start=2.0,
             loop_end=6.0, start_relative=1.0, key_tracks=_key_tracks([(t, 0.5, 100 + t) for t in range(8)])),
        [(84.0, 103, 0.5), (85.0, 104, 0.5), (86.0, 105, 0.5), (87.0, 102, 0.5)],
    ),
]


def test_midi_clip_playback_matches_live_consolidation(tmp_path):
    als = _clip_scenario_als(tmp_path, [scenario for scenario, _ in LIVE_VERIFIED_SCENARIOS])
    project = parse_ableton_project(als)

    (track,) = project.midi_tracks
    by_clip = {clip.clip_name: clip for clip in track.clips}
    for scenario, expected in LIVE_VERIFIED_SCENARIOS:
        clip = by_clip[scenario["name"]]
        rendered = [(round(note.start_beats, 9), note.pitch, round(note.duration_beats, 9)) for note in clip.notes]
        assert rendered == expected, scenario["name"]
    assert not any("loop" in warning for warning in project.compatibility_warnings)


def test_build_midi_note_file_roundtrips_notes():
    track = AbletonMidiTrack(
        name="Lead",
        clips=[
            AbletonMidiClip(
                clip_name="Riff",
                track_name="Lead",
                start_beats=0.0,
                end_beats=4.0,
                notes=[
                    AbletonMidiNote(pitch=60, start_beats=0.0, duration_beats=1.0, velocity=100),
                    AbletonMidiNote(pitch=64, start_beats=1.0, duration_beats=1.0, velocity=90),
                ],
            )
        ],
    )
    data = _build_midi_note_file(track, tempo=120.0, numerator=4, denominator=4)

    assert data[:4] == b"MThd"
    note_ons = read_midi_note_ons(data)
    assert note_ons == [
        (0, 60, 100),
        (MIDI_TICKS_PER_QUARTER, 64, 90),
    ]


def test_generate_logic_transfer_writes_midi_files(tmp_path):
    als = create_midi_als(tmp_path)
    project = parse_ableton_project(als)
    output = tmp_path / "out"

    artifact = generate_logic_transfer(project, output, copy_audio=False)

    assert artifact.rendered_midi_files == 1
    assert artifact.transferred_midi_notes == 3
    midi_files = list((artifact.package_path / "MIDI Tracks").glob("*.mid"))
    assert len(midi_files) == 1
    assert midi_files[0].name == "01 - Bass.mid"

    note_ons = read_midi_note_ons(midi_files[0].read_bytes())
    assert len(note_ons) == 3
    # First note: pitch 36 at beat 4 -> tick 4 * 960.
    assert note_ons[0] == (4 * MIDI_TICKS_PER_QUARTER, 36, 100)

    import json

    manifest = json.loads((artifact.package_path / "timeline_manifest.json").read_text())
    assert manifest["midi_tracks"][0]["note_count"] == 3
    assert manifest["midi_tracks"][0]["track_name"] == "Bass"


def test_disabled_midi_clip_is_skipped(tmp_path):
    project_dir = tmp_path / "Disabled Project"
    project_dir.mkdir(parents=True)
    root = ET.Element("Ableton")
    live_set = ET.SubElement(root, "LiveSet")
    transport = ET.SubElement(live_set, "Transport")
    ET.SubElement(ET.SubElement(transport, "Tempo"), "Manual").set("Value", "120")
    tracks = ET.SubElement(live_set, "Tracks")
    midi_track = ET.SubElement(tracks, "MidiTrack")
    ET.SubElement(ET.SubElement(midi_track, "Name"), "EffectiveName").set("Value", "Muted")
    events = ET.SubElement(
        ET.SubElement(
            ET.SubElement(ET.SubElement(midi_track, "DeviceChain"), "MainSequencer"),
            "ClipTimeable",
        ),
        "ArrangerAutomation",
    )
    events = ET.SubElement(events, "Events")
    clip = ET.SubElement(events, "MidiClip")
    ET.SubElement(clip, "CurrentStart").set("Value", "0")
    ET.SubElement(clip, "CurrentEnd").set("Value", "4")
    ET.SubElement(clip, "Name").set("Value", "Off")
    ET.SubElement(clip, "Disabled").set("Value", "true")
    notes_el = ET.SubElement(ET.SubElement(clip, "Notes"), "KeyTracks")
    kt = ET.SubElement(notes_el, "KeyTrack")
    ktn = ET.SubElement(kt, "Notes")
    ev = ET.SubElement(ktn, "MidiNoteEvent")
    ev.set("Time", "0")
    ev.set("Duration", "1")
    ev.set("Velocity", "100")
    ET.SubElement(kt, "MidiKey").set("Value", "60")

    als_path = project_dir / "Disabled Project.als"
    with gzip.open(als_path, "wb") as handle:
        handle.write(ET.tostring(root, encoding="utf-8", xml_declaration=True))

    project = parse_ableton_project(als_path)
    assert project.midi_tracks[0].note_count == 0
