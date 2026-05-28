"""Tests for forward-lane MIDI extraction from Logic ProjectData and .mid export."""

import struct
from pathlib import Path

import pytest

from logic2ableton.logic_parser import _MIDI_NOTE_SIGNATURE, extract_midi_notes
from logic2ableton.models import LogicMidiNote, LogicMidiTrack, LogicProject
from logic2ableton.cli import _export_logic_midi


def _note_record(pitch: int, velocity: int, position_ticks: int, duration_ticks: int) -> bytes:
    """Build a synthetic Logic note record matching the reverse-engineered layout.

    Layout relative to the signature start S:
      S-9..S-6 = position (LE32), S-5..S-3 = filler, S-2 = velocity, S-1 = pitch,
      S..S+14 = signature, S+15..S+18 = duration (LE32).
    """
    return (
        struct.pack("<I", position_ticks)
        + b"\x00\x00\x00"
        + bytes([velocity, pitch])
        + _MIDI_NOTE_SIGNATURE
        + struct.pack("<I", duration_ticks)
    )


def _project_data(sequences: list[list[tuple]]) -> bytes:
    """sequences: list of sequences, each a list of (pitch, vel, pos_ticks, dur_ticks)."""
    blob = b"HEADERPAD" * 4
    for seq in sequences:
        blob += b"qSvE" + b"\x00" * 8  # sequence chunk marker + small header
        for pitch, vel, pos, dur in seq:
            blob += _note_record(pitch, vel, pos, dur)
        blob += b"\xf1\x00\x00\x00" + b"\x00" * 8  # end-of-sequence padding
    return blob


def test_extract_midi_notes_single_sequence():
    PPQ = 960
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
