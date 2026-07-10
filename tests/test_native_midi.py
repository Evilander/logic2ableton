"""Tests for native MIDI track/clip injection into generated .als files."""

import gzip
import xml.etree.ElementTree as ET
from pathlib import Path

from logic2ableton.ableton_generator import _BUNDLED_TEMPLATE, generate_als
from logic2ableton.ableton_parser import parse_ableton_project
from logic2ableton.models import AudioFileRef, LogicMidiNote, LogicMidiTrack, LogicProject

from conftest import write_test_wav


def _midi_project(midi_tracks, *, name="MidiNative", tempo=120.0, num=4, den=4, audio_files=None, track_names=None):
    return LogicProject(
        name=name,
        tempo=tempo,
        time_sig_numerator=num,
        time_sig_denominator=den,
        sample_rate=44100,
        audio_files=audio_files or [],
        plugins=[],
        track_names=track_names or [],
        alternative=0,
        midi_tracks=midi_tracks,
    )


def _bass_track():
    return LogicMidiTrack(
        name="Bass",
        notes=[
            LogicMidiNote(pitch=36, start_beats=0.0, duration_beats=1.0, velocity=100),
            LogicMidiNote(pitch=38, start_beats=1.5, duration_beats=0.5, velocity=90),
            LogicMidiNote(pitch=36, start_beats=4.0, duration_beats=2.0, velocity=127),
        ],
    )


def _chord_track():
    return LogicMidiTrack(
        name="Keys",
        notes=[
            LogicMidiNote(pitch=60, start_beats=8.0, duration_beats=4.0, velocity=70),
            LogicMidiNote(pitch=64, start_beats=8.0, duration_beats=4.0, velocity=70),
            LogicMidiNote(pitch=67, start_beats=8.0, duration_beats=4.0, velocity=70),
        ],
    )


def _read_root(als_path: Path) -> ET.Element:
    with gzip.open(als_path, "rb") as f:
        return ET.fromstring(f.read())


def test_midi_notes_round_trip_through_parser(tmp_path):
    """Notes injected into the .als must parse back at their original absolute positions."""
    project = _midi_project([_bass_track(), _chord_track()])
    als_path = generate_als(project, tmp_path / "out", copy_audio=False)

    parsed = parse_ableton_project(als_path)
    assert [t.name for t in parsed.midi_tracks] == ["Bass", "Keys"]

    bass_notes = {(n.pitch, n.start_beats, n.duration_beats, n.velocity) for n in parsed.midi_tracks[0].notes}
    assert bass_notes == {(36, 0.0, 1.0, 100), (38, 1.5, 0.5, 90), (36, 4.0, 2.0, 127)}

    chord_notes = {(n.pitch, n.start_beats, n.duration_beats, n.velocity) for n in parsed.midi_tracks[1].notes}
    assert chord_notes == {(60, 8.0, 4.0, 70), (64, 8.0, 4.0, 70), (67, 8.0, 4.0, 70)}


def test_midi_clip_structure_mirrors_live_schema(tmp_path):
    project = _midi_project([_bass_track()])
    als_path = generate_als(project, tmp_path / "out", copy_audio=False)
    root = _read_root(als_path)

    midi_tracks = root.findall(".//Tracks/MidiTrack")
    assert len(midi_tracks) == 1
    track = midi_tracks[0]
    assert track.find("Name/EffectiveName").get("Value") == "Bass"

    clips = track.findall(".//MainSequencer/ClipTimeable/ArrangerAutomation/Events/MidiClip")
    assert len(clips) == 1
    clip = clips[0]

    # Clip spans whole bars around the notes
    assert float(clip.find("CurrentStart").get("Value")) == 0.0
    assert float(clip.find("CurrentEnd").get("Value")) == 8.0  # notes end at beat 6 -> 2 bars
    assert clip.find("Loop/LoopOn").get("Value") == "false"
    assert float(clip.find("Loop/OutMarker").get("Value")) == 8.0

    # One KeyTrack per distinct pitch, ascending
    key_tracks = clip.findall("Notes/KeyTracks/KeyTrack")
    assert [kt.find("MidiKey").get("Value") for kt in key_tracks] == ["36", "38"]

    # NoteIds are clip-local, unique, and the generator points past them
    note_ids = [int(ev.get("NoteId")) for ev in clip.findall(".//MidiNoteEvent")]
    assert len(note_ids) == 3
    assert len(set(note_ids)) == 3
    next_id = int(clip.find("Notes/NoteIdGenerator/NextId").get("Value"))
    assert next_id == max(note_ids) + 1

    # Live 12 companion blocks exist
    assert clip.find("Notes/PerNoteEventStore/EventLists") is not None
    assert clip.find("Notes/ProbabilityGroupIdGenerator/NextId") is not None
    assert clip.find("BankSelectCoarse").get("Value") == "-1"

    # Clip color matches track color
    assert clip.find("Color").get("Value") == track.find("Color").get("Value")


def test_midi_tracks_ordered_before_return_tracks(tmp_path):
    wav = write_test_wav(tmp_path / "media" / "Guitar.wav")
    ref = AudioFileRef(
        filename="Guitar.wav",
        track_name="Guitar",
        take_number=0,
        is_comp=False,
        comp_name="",
        file_path=wav,
    )
    project = _midi_project(
        [_bass_track()],
        audio_files=[ref],
        track_names=["Guitar"],
    )
    als_path = generate_als(project, tmp_path / "out", copy_audio=False)
    root = _read_root(als_path)

    tags = [t.tag for t in root.find(".//Tracks")]
    assert tags.index("AudioTrack") < tags.index("MidiTrack")
    assert tags.index("MidiTrack") < tags.index("ReturnTrack")

    # Audio and MIDI coexist and both parse back
    parsed = parse_ableton_project(als_path)
    assert [t.name for t in parsed.audio_tracks] == ["Guitar"]
    assert len(parsed.audio_tracks[0].clips) == 1
    assert [t.name for t in parsed.midi_tracks] == ["Bass"]
    assert parsed.total_midi_notes == 3


def test_template_without_midi_track_falls_back_with_warning(tmp_path):
    # Build a template with the MidiTracks stripped out
    with gzip.open(_BUNDLED_TEMPLATE, "rb") as f:
        root = ET.fromstring(f.read())
    tracks = root.find("LiveSet/Tracks")
    for track in list(tracks):
        if track.tag == "MidiTrack":
            tracks.remove(track)
    stripped = tmp_path / "NoMidiTemplate.als"
    with gzip.open(stripped, "wb") as f:
        f.write(ET.tostring(root, encoding="utf-8", xml_declaration=True))

    project = _midi_project([_bass_track()])
    als_path = generate_als(project, tmp_path / "out", copy_audio=False, template_path=stripped)

    out_root = _read_root(als_path)
    assert out_root.findall(".//Tracks/MidiTrack") == []
    assert any("no MIDI track" in w for w in project.compatibility_warnings)


def test_project_without_midi_adds_no_midi_tracks(tmp_path):
    wav = write_test_wav(tmp_path / "media" / "Vox.wav")
    ref = AudioFileRef(
        filename="Vox.wav",
        track_name="Vox",
        take_number=0,
        is_comp=False,
        comp_name="",
        file_path=wav,
    )
    project = _midi_project([], audio_files=[ref], track_names=["Vox"])
    als_path = generate_als(project, tmp_path / "out", copy_audio=False)
    root = _read_root(als_path)
    assert root.findall(".//Tracks/MidiTrack") == []
    assert project.compatibility_warnings == []


def test_midi_clip_in_odd_time_signature(tmp_path):
    """Bar rounding must respect the project time signature (7/8 bars = 3.5 beats)."""
    track = LogicMidiTrack(
        name="Odd",
        notes=[LogicMidiNote(pitch=50, start_beats=3.5, duration_beats=1.0, velocity=80)],
    )
    project = _midi_project([track], num=7, den=8)
    als_path = generate_als(project, tmp_path / "out", copy_audio=False)
    root = _read_root(als_path)
    clip = root.find(".//Tracks/MidiTrack//Events/MidiClip")
    assert float(clip.find("CurrentStart").get("Value")) == 3.5
    assert float(clip.find("CurrentEnd").get("Value")) == 7.0

    parsed = parse_ableton_project(als_path)
    note = parsed.midi_tracks[0].notes[0]
    assert (note.pitch, note.start_beats, note.duration_beats) == (50, 3.5, 1.0)
