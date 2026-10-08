"""Session trust boundaries and bounded parsing of compact loop descriptions."""

import gzip
import io
import os
import stat
import subprocess
import struct
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import pytest

from logic2ableton import limits, logic_parser, protools_parser
from logic2ableton.ableton_parser import _parse_midi_clip, _parse_midi_tracks, _resolve_source_path, parse_ableton_project
from logic2ableton.cli import main
from logic2ableton.logic_parser import _read_project_data, _unroll_regions, parse_logic_project
from logic2ableton.logic_project_data import (
    PROJECT_START_TICKS, SEQUENCE_ORIGIN_TICKS, LogicArrangement, LogicNote, LogicPlacement,
    LogicSequence, decode_project_data,
)
from logic2ableton.models import AudioFileRef, LogicMidiNote, LogicMidiRegion
from logic2ableton.paths import contained_source_path
from logic2ableton.protools_import import _source_audio_path
from logic2ableton.protools_parser import parse_protools_session
from logic2ableton.timeline import TempoEvent, TempoMap
from scripts.fixture_builders import (
    _pt_block, _pt_midi_event, build_logic_arrangement_project_data, build_synthetic_logicx, write_test_wav,
)


@pytest.mark.parametrize("reference", ["../outside.wav", r"\\review.invalid\share\clip.wav", r"\\?\UNC\review.invalid\clip.wav"])
def test_external_media_is_rejected_without_filesystem_resolution(tmp_path, monkeypatch, reference):
    def unexpected_access(*args, **kwargs):
        raise AssertionError("Untrusted external reference reached the filesystem")

    def resolve_trusted_root(path, *args, **kwargs):
        assert path == tmp_path, "Untrusted external reference reached resolve()"
        return path

    monkeypatch.setattr(Path, "resolve", resolve_trusted_root)
    monkeypatch.setattr(Path, "lstat", unexpected_access)
    assert contained_source_path(tmp_path, reference) is None
    ref = ET.Element("FileRef")
    ET.SubElement(ref, "Path", Value=reference)
    assert _resolve_source_path(tmp_path / "session.als", ref)[2] == "external-media-blocked"
    warnings = []
    assert _source_audio_path(tmp_path, reference, warnings) is None
    assert "Blocked source audio reference" in warnings[0]


def test_contained_media_and_missing_media_keep_their_paths(tmp_path):
    audio = tmp_path / "Samples" / "clip.wav"
    audio.parent.mkdir()
    audio.touch()
    assert contained_source_path(tmp_path, r"Samples\clip.wav") == audio.resolve()
    assert contained_source_path(tmp_path, str(audio)) == audio.resolve()
    assert contained_source_path(tmp_path, "Samples/missing.wav") == audio.parent.resolve() / "missing.wav"


def test_trusted_root_alias_accepts_canonical_absolute_references(tmp_path, monkeypatch):
    alias = tmp_path / "alias"
    actual = tmp_path / "actual"
    actual.mkdir()
    audio = actual / "clip.wav"
    audio.touch()
    original = Path.resolve

    def resolve(path, *args, **kwargs):
        return actual if path == alias else original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    assert contained_source_path(alias, str(audio)) == audio
    assert contained_source_path(alias, "clip.wav") == audio


def test_reparse_point_is_rejected_before_visiting_its_children(tmp_path, monkeypatch):
    inspected = []
    original = Path.lstat

    def lstat(path, *args, **kwargs):
        if path == tmp_path / "remote":
            inspected.append(path)
            return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400, st_reparse_tag=0xA0000003)
        if path == tmp_path / "remote" / "clip.wav":
            raise AssertionError("Reparse target was visited")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", lstat)
    assert contained_source_path(tmp_path, "remote/clip.wav") is None
    assert inspected == [tmp_path / "remote"]


@pytest.mark.parametrize("tag", [0x9000701A, 0x80000017, 0x80000013])  # OneDrive placeholder, compressed, deduplicated
def test_reparse_points_that_do_not_name_another_path_are_ordinary_files(tmp_path, monkeypatch, tag):
    original = Path.lstat

    def lstat(path, *args, **kwargs):
        if path == tmp_path / "Samples":
            return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400, st_reparse_tag=tag)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", lstat)
    assert contained_source_path(tmp_path, "Samples/clip.wav") == tmp_path.resolve() / "Samples" / "clip.wav"


@pytest.mark.skipif(os.name != "nt", reason="Windows junctions")
def test_a_real_junction_below_the_project_is_rejected(tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "clip.wav").touch()
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(["cmd", "/c", "mklink", "/J", str(project / "linked"), str(elsewhere)], check=True, capture_output=True)
    assert contained_source_path(project, "linked/clip.wav") is None


@pytest.mark.skipif(os.name != "nt", reason="names Windows cannot hold")
@pytest.mark.parametrize("name", ["Samples/kick?.wav", "x|y.wav", "<x>.wav", "a" * 300 + ".wav"])
def test_a_media_name_windows_cannot_hold_is_reported_missing(tmp_path, name):
    ref = ET.Element("FileRef")
    ET.SubElement(ref, "RelativePath", Value=name)
    assert _resolve_source_path(tmp_path / "session.als", ref)[2] == "missing-file-reference"
    warnings = []
    path = _source_audio_path(tmp_path, name, warnings)
    assert path is not None and not path.is_file() and not warnings


@pytest.mark.parametrize("compressed", [False, True])
def test_live_set_read_rejects_oversized_metadata(tmp_path, monkeypatch, compressed):
    monkeypatch.setattr(limits, "MAX_SESSION_BYTES", 256)
    data = b"<Ableton><LiveSet><Padding>" + b" " * 300 + b"</Padding></LiveSet></Ableton>"
    source = tmp_path / "large.als"
    source.write_bytes(gzip.compress(data) if compressed else data)
    with pytest.raises(ValueError, match="session-data limit"):
        parse_ableton_project(source)


def test_session_reader_requests_only_limit_plus_one(monkeypatch):
    monkeypatch.setattr(limits, "MAX_SESSION_BYTES", 16)
    stream = io.BytesIO(b"x" * 40)
    with pytest.raises(ValueError, match="session-data limit"):
        limits.read_session_bytes(stream, "Test")
    assert stream.tell() == 17


def test_logic_projectdata_reader_and_decoder_share_size_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(limits, "MAX_SESSION_BYTES", 16)
    source = tmp_path / "Alternatives" / "000" / "ProjectData"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"x" * 17)
    with pytest.raises(ValueError, match="session-data limit"):
        _read_project_data(tmp_path)
    with pytest.raises(ValueError, match="session-data limit"):
        decode_project_data(b"x" * 17)


def test_protools_session_reader_enforces_size_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(limits, "MAX_SESSION_BYTES", 16)
    source = tmp_path / "large.ptx"
    source.write_bytes(b"x" * 17)
    with pytest.raises(ValueError, match="session-data limit"):
        parse_protools_session(source)


@pytest.mark.parametrize("mode", ["ableton2logic", "ableton2protools"])
def test_arbitrary_xml_is_a_parse_failure_not_a_successful_transfer(tmp_path, capsys, mode):
    source = tmp_path / "wrong.als"
    source.write_text("<Unrelated><Data/></Unrelated>", encoding="utf-8")
    destination = tmp_path / "out"
    assert main([mode, str(source), "--output", str(destination), "--json-progress"]) == 1
    assert '"stage": "error"' in capsys.readouterr().out
    assert not any(item.is_dir() for item in destination.iterdir())


@pytest.mark.parametrize("xml", ["<Ableton><LiveSet><Tracks/></LiveSet></Ableton>", "<LiveSet><Tracks/></LiveSet>"])
def test_recognized_empty_live_set_is_still_supported(tmp_path, xml):
    source = tmp_path / "empty.als"
    source.write_text(xml, encoding="utf-8")
    assert parse_ableton_project(source).audio_tracks == []


def _midi_clip(*, length=12, disabled=False, notes=True):
    clip = ET.fromstring(
        f'<MidiClip><CurrentStart Value="0"/><CurrentEnd Value="{length}"/>'
        f'<Disabled Value="{str(disabled).lower()}"/><Loop><LoopStart Value="0"/>'
        '<LoopEnd Value="4"/><LoopOn Value="true"/></Loop></MidiClip>'
    )
    if notes:
        key = ET.SubElement(clip, "KeyTrack")
        ET.SubElement(key, "MidiKey", Value="60")
        ET.SubElement(key, "MidiNoteEvent", Time="0", Duration="1", Velocity="100")
    return clip


def test_empty_and_disabled_midi_do_not_expand_huge_spans(monkeypatch):
    monkeypatch.setattr(limits, "MAX_EXPANSION_WORK", 1)
    assert _parse_midi_clip(_midi_clip(length=1e12, disabled=True), "MIDI", 120) is None
    assert _parse_midi_clip(_midi_clip(length=1e12, notes=False), "MIDI", 120).notes == []


def test_nonempty_midi_rejects_huge_expansion_before_allocating(monkeypatch):
    monkeypatch.setattr(limits, "MAX_EXPANSION_WORK", 100)
    with pytest.raises(ValueError, match="loop expansion limit"):
        _parse_midi_clip(_midi_clip(length=1e12), "MIDI", 120)


def test_midi_item_limit_is_shared_across_clips(monkeypatch):
    monkeypatch.setattr(limits, "MAX_EXPANDED_ITEMS", 3)
    live_set = ET.Element("LiveSet")
    events = ET.SubElement(ET.SubElement(ET.SubElement(ET.SubElement(live_set, "Tracks"), "MidiTrack"), "ArrangerAutomation"), "Events")
    events.extend([_midi_clip(length=8), _midi_clip(length=8)])
    with pytest.raises(ValueError, match="loop expansion limit"):
        _parse_midi_tracks(live_set, 120)


def test_logic_midi_expansion_is_bounded_and_empty_regions_are_skipped(monkeypatch):
    monkeypatch.setattr(limits, "MAX_EXPANSION_WORK", 100)
    region = LogicMidiRegion("Huge", 0, 1, loop_span_beats=1e12)
    assert _unroll_regions([region]) == []
    region.notes = [LogicMidiNote(60, 0, 1, 100)]
    with pytest.raises(ValueError, match="loop expansion limit"):
        _unroll_regions([region])


def test_logic_audio_loop_expansion_is_bounded(tmp_path, monkeypatch):
    data = build_logic_arrangement_project_data(
        tracks={1: "Audio"}, sequences=[], midi_regions=[], audio_files={10: "clip.wav"},
        audio_regions=[{"file": 10, "index": 0, "name": "Short", "offset": 0, "length": 1}],
        audio_placements=[{"bar": 1, "track": 1, "file": 10, "loop_beats": 16}],
    )
    source = build_synthetic_logicx(tmp_path, project_data=data)
    write_test_wav(source / "Media" / "Audio Files" / "clip.wav", frames=1)
    monkeypatch.setattr(limits, "MAX_EXPANDED_ITEMS", 20)
    with pytest.raises(ValueError, match="loop expansion limit"):
        parse_logic_project(source)


def test_take_folder_expansion_is_bounded(monkeypatch):
    data = build_logic_arrangement_project_data(
        tracks={1: "Audio"}, sequences=[], midi_regions=[], audio_files={10: "clip.wav"},
        take_folders=[{"bar": 1, "track": 1, "id": 300, "length_beats": 1, "loop_beats": 100,
                       "takes": [{"file": 10}]}],
    )
    monkeypatch.setattr(limits, "MAX_EXPANDED_ITEMS", 20)
    with pytest.raises(ValueError, match="loop expansion limit"):
        decode_project_data(data)


def _repeated_ptx_midi(*, separate_tracks):
    zero = 500_000_000
    events = b"".join(_pt_midi_event(zero + i * 960000, 60, 960000, 100) for i in range(2))
    midi = _pt_block(1, 0x2000, b"MdNLB" + b"\0" * 6 + struct.pack("<I", 2) + events)
    region = _pt_block(1, 0x2634, _pt_block(2, 0x2633, _pt_block(2, 0x2628, b"\0\0") + struct.pack("<I", 0)))
    placement = _pt_block(2, 0x104F, b"\0\0" + struct.pack("<I", 0) + b"\0"
                          + protools_parser._PT_ZERO_TICKS.to_bytes(5, "little"))
    entry = _pt_block(2, 0x1056, placement)
    lanes = _pt_block(2, 0x1057, entry) * 2 if separate_tracks else _pt_block(2, 0x1057, entry * 2)
    reader = protools_parser._SessionReader(b"\0" * 20 + midi + region + _pt_block(1, 0x1058, b"\0\0" + lanes))
    return reader, reader.parse_all_blocks()


@pytest.mark.parametrize("separate_tracks", [False, True])
def test_protools_reused_midi_is_bounded_before_note_allocation(monkeypatch, separate_tracks):
    reader, blocks = _repeated_ptx_midi(separate_tracks=separate_tracks)
    original = protools_parser.ProToolsMidiNote
    allocated = []

    def note(**values):
        allocated.append(values)
        assert len(allocated) <= 3, "MIDI note allocated before expansion-limit check"
        return original(**values)

    monkeypatch.setattr(limits, "MAX_EXPANDED_ITEMS", 3)
    monkeypatch.setattr(protools_parser, "ProToolsMidiNote", note)
    with pytest.raises(ValueError, match="loop expansion limit"):
        protools_parser._parse_midi(reader, blocks)
    assert len(allocated) == 3


def test_protools_reused_midi_at_limit_still_parses(monkeypatch):
    monkeypatch.setattr(limits, "MAX_EXPANDED_ITEMS", 4)
    reader, blocks = _repeated_ptx_midi(separate_tracks=True)
    tracks = protools_parser._parse_midi(reader, blocks)
    assert [track.note_count for track in tracks] == [2, 2]


def _repeated_logic_midi(note_count, placement_count, *, overlap=False):
    sequence = LogicSequence(1, "Notes", 0, note_count * 960, [
        LogicNote(SEQUENCE_ORIGIN_TICKS + i * 960, 60, 100, 480) for i in range(note_count)
    ])
    step = 240 if overlap else sequence.content_length
    placements = [
        LogicPlacement("midi", PROJECT_START_TICKS + i * step, 1, 0, 0, None, 1, None, None)
        for i in range(placement_count)
    ]
    return LogicArrangement(sequences={1: sequence}, placements=placements)


def test_logic_reused_midi_checks_limit_before_copying_region_content(monkeypatch):
    arrangement = _repeated_logic_midi(20, 20)
    original = logic_parser.LogicMidiNote
    allocated = []

    def note(**values):
        allocated.append(values)
        assert len(allocated) <= 3, "Region content allocated before expansion-limit check"
        return original(**values)

    monkeypatch.setattr(limits, "MAX_EXPANDED_ITEMS", 3)
    monkeypatch.setattr(logic_parser, "LogicMidiNote", note)
    with pytest.raises(ValueError, match="loop expansion limit"):
        logic_parser._arrangement_midi_tracks(arrangement, bar_beats=4, track_names_by_id={}, warnings=[])
    assert len(allocated) == 3


def test_logic_midi_merge_and_final_output_have_separate_item_allowances(monkeypatch):
    monkeypatch.setattr(limits, "MAX_EXPANDED_ITEMS", 4)
    arrangement = _repeated_logic_midi(2, 2, overlap=True)
    tracks = logic_parser._arrangement_midi_tracks(arrangement, bar_beats=4, track_names_by_id={}, warnings=[])
    assert len(tracks) == 1 and tracks[0].note_count == 4


@pytest.mark.parametrize("tempo_events", [[], [TempoEvent(1, 120)]])
def test_logic_audio_loop_at_item_limit_does_not_count_initial_pass_twice(tmp_path, monkeypatch, tempo_events):
    arrangement = decode_project_data(build_logic_arrangement_project_data(
        tracks={1: "Audio"}, sequences=[], midi_regions=[], audio_files={10: "clip.wav"},
        audio_regions=[{"file": 10, "index": 0, "name": "Clip", "offset": 0, "length": 44100}],
        audio_placements=[{"bar": 1, "track": 1, "file": 10, "loop_beats": 4}],
    ))
    source = tmp_path / "clip.wav"
    write_test_wav(source, sample_rate=44100, frames=44100)
    ref = AudioFileRef("clip.wav", "Audio", 0, False, "", source)
    monkeypatch.setattr(limits, "MAX_EXPANDED_ITEMS", 2)
    refs, _ = logic_parser._arrangement_audio_refs(
        arrangement, [ref], tempo_map=TempoMap(120, tempo_events), bar_beats=4,
        track_names_by_id={}, warnings=[],
    )
    assert len(refs) == 2
    assert [ref.start_position_samples for ref in refs] == [0, 44100]
