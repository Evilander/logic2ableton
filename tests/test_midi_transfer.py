"""Tests for Ableton MIDI clip extraction and Standard MIDI File export."""

import gzip
import struct
import xml.etree.ElementTree as ET
from pathlib import Path

from logic2ableton.ableton_parser import parse_ableton_project
from logic2ableton.logic_transfer import (
    MIDI_TICKS_PER_QUARTER,
    _build_midi_note_file,
    generate_logic_transfer,
)
from logic2ableton.models import AbletonMidiClip, AbletonMidiNote, AbletonMidiTrack


def _add_midi_clip(events, *, clip_start, clip_end, name, key_tracks, looping=False):
    """key_tracks: list of (pitch, [(time, duration, velocity), ...])."""
    clip = ET.SubElement(events, "MidiClip")
    clip.set("Time", str(clip_start))
    ET.SubElement(clip, "CurrentStart").set("Value", str(clip_start))
    ET.SubElement(clip, "CurrentEnd").set("Value", str(clip_end))
    ET.SubElement(clip, "Name").set("Value", name)
    loop = ET.SubElement(clip, "Loop")
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
