"""Tests for forward-lane MIDI extraction from Logic ProjectData and .mid export."""

import struct
from pathlib import Path

from scripts.fixture_builders import (
    build_logic_project_data as _project_data,
    build_synthetic_logicx as _make_logicx,
)

from logic2ableton.logic_parser import extract_midi_notes, parse_logic_project
from logic2ableton.models import LogicMidiNote, LogicMidiTrack, LogicProject
from logic2ableton.cli import _export_logic_midi


def test_extract_midi_notes_single_sequence():
    data = _project_data([[(61, 99, 38400, 960), (73, 77, 39360, 960), (85, 111, 40320, 1920)]])
    tracks = extract_midi_notes(Path("/x.logicx"), _data=data)
    assert len(tracks) == 1
    notes = tracks[0].notes
    assert [(n.pitch, n.velocity, n.start_beats, n.duration_beats) for n in notes] == [
        (61, 99, 0.0, 1.0),
        (73, 77, 1.0, 1.0),
        (85, 111, 2.0, 2.0),
    ]


def test_extract_midi_notes_multiple_sequences_and_chord():
    data = _project_data([
        [(60, 100, 38400, 960), (64, 100, 39360, 960)],
        [(48, 70, 38400, 1920), (52, 70, 38400, 1920), (55, 70, 38400, 1920)],  # chord
    ])
    tracks = extract_midi_notes(Path("/x.logicx"), _data=data)
    assert len(tracks) == 2
    assert tracks[0].note_count == 2
    assert tracks[1].note_count == 3
    # Chord: all three notes share start 0.0
    assert {n.start_beats for n in tracks[1].notes} == {0.0}
    assert sorted(n.pitch for n in tracks[1].notes) == [48, 52, 55]


def test_extract_midi_notes_rejects_out_of_range():
    # velocity 0 and pitch 200 are invalid -> record skipped
    data = _project_data([[(200, 0, 38400, 960)]])
    assert extract_midi_notes(Path("/x.logicx"), _data=data) == []


def test_extract_midi_notes_empty():
    assert extract_midi_notes(Path("/x.logicx"), _data=b"") == []


def test_extract_midi_notes_absolute_placement_for_later_region():
    """Regions after bar 1 keep their absolute arrangement position (bar-1 anchor = tick 38400)."""
    data = _project_data([[(60, 100, 46080, 960)]])  # 38400 + 8 beats * 960
    warnings: list[str] = []
    tracks = extract_midi_notes(Path("/x.logicx"), _data=data, warnings=warnings)
    assert tracks[0].notes[0].start_beats == 8.0
    assert warnings == []


def test_extract_midi_notes_relative_fallback_before_bar1_anchor():
    """Notes before the bar-1 anchor fall back to relative placement and warn."""
    data = _project_data([[(60, 100, 20000, 960), (62, 100, 21920, 960)]])
    warnings: list[str] = []
    tracks = extract_midi_notes(Path("/x.logicx"), _data=data, warnings=warnings)
    starts = [n.start_beats for n in tracks[0].notes]
    assert starts == [0.0, 2.0]
    assert len(warnings) == 1
    assert "relative to the earliest note" in warnings[0]


def test_parse_warns_when_instruments_present_but_midi_undecodable(tmp_path):
    """Instrument files without decodable notes must not claim MIDI was exported."""
    blob = b"qSvE" + b"\x00" * 32 + b"qSvE" + b"\x01" * 32  # sequences, no note records
    logicx = _make_logicx(tmp_path, project_data=blob, sampler_files=["Piano.exs"])
    project = parse_logic_project(logicx)
    assert project.midi_tracks == []
    assert any("could be decoded" in w and "not transferred" in w for w in project.compatibility_warnings)
    assert not any("MIDI notes were transferred" in w for w in project.compatibility_warnings)


def test_parse_reports_native_midi_when_instruments_and_notes_decodable(tmp_path):
    blob = _project_data([[(60, 100, 38400, 960)]])
    logicx = _make_logicx(tmp_path, project_data=blob, sampler_files=["Piano.exs"])
    project = parse_logic_project(logicx)
    assert project.total_midi_notes == 1
    assert any("transferred as native MIDI tracks" in w for w in project.compatibility_warnings)


def _read_midi_note_ons(data: bytes):
    assert data[:4] == b"MThd"
    idx = data.index(b"MTrk")
    length = struct.unpack(">I", data[idx + 4 : idx + 8])[0]
    track = data[idx + 8 : idx + 8 + length]

    def read_var(pos):
        val = 0
        while True:
            b = track[pos]
            pos += 1
            val = (val << 7) | (b & 0x7F)
            if not b & 0x80:
                return val, pos

    notes = []
    pos = 0
    tick = 0
    while pos < len(track):
        delta, pos = read_var(pos)
        tick += delta
        status = track[pos]
        if status == 0xFF:
            pos += 2
            mlen, pos = read_var(pos)
            pos += mlen
        elif status & 0xF0 in (0x80, 0x90):
            pitch, vel = track[pos + 1], track[pos + 2]
            pos += 3
            if status & 0xF0 == 0x90 and vel > 0:
                notes.append((tick, pitch, vel))
        else:
            pos += 1
    return notes


def test_export_logic_midi_writes_importable_files(tmp_path):
    project = LogicProject(
        name="MidiProj",
        tempo=120.0,
        time_sig_numerator=4,
        time_sig_denominator=4,
        sample_rate=44100,
        audio_files=[],
        plugins=[],
        track_names=[],
        alternative=0,
        midi_tracks=[
            LogicMidiTrack(
                name="MIDI 1",
                notes=[
                    LogicMidiNote(pitch=61, start_beats=0.0, duration_beats=1.0, velocity=99),
                    LogicMidiNote(pitch=73, start_beats=1.0, duration_beats=1.0, velocity=77),
                ],
            )
        ],
    )
    project_folder = tmp_path / "MidiProj Project"
    project_folder.mkdir()
    count = _export_logic_midi(project, project_folder)
    assert count == 1
    mid_files = list((project_folder / "MIDI").glob("*.mid"))
    assert len(mid_files) == 1

    note_ons = _read_midi_note_ons(mid_files[0].read_bytes())
    assert note_ons == [(0, 61, 99), (960, 73, 77)]
