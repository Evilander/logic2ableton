"""Regressions from the 2026-09-16 review: region trims, loops, track identity, bounds."""

import gzip
import json
import struct
import wave
import xml.etree.ElementTree as ET
from pathlib import Path

from logic2ableton.ableton_generator import _BUNDLED_TEMPLATE, generate_als
from logic2ableton.ableton_parser import parse_ableton_project
from logic2ableton.cli import main
from logic2ableton.logic_parser import parse_logic_project
from logic2ableton.logic_project_data import decode_project_data
from logic2ableton.protools_parser import PT_TICKS_PER_QUARTER, _deobfuscate, parse_protools_session
from logic2ableton.protools_transfer import generate_protools_transfer_from_logic
from logic2ableton.timeline import TempoEvent, Timeline
from scripts.fixture_builders import (
    _PT_ZERO_TICKS,
    _logic_object,
    _pt_block,
    _pt_midi_event,
    _pt_string,
    build_logic_arrangement_project_data,
    build_synthetic_logicx,
    build_synthetic_ptx,
)

RATE = 44_100


def _three_level_wav(path: Path) -> Path:
    """Six seconds of mono PCM: two seconds each at sample values 1000, 2000, 3000."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
        for value in (1000, 2000, 3000):
            handle.writeframes(struct.pack("<h", value) * RATE * 2)
    return path


def _logic_project(tmp_path: Path, *, tracks=None, placements=None) -> Path:
    """A 120 BPM Logic project whose Guitar region plays seconds 2-4 of Guitar.wav."""
    data = build_logic_arrangement_project_data(
        tracks=tracks or {1: "Guitar"},
        sequences=[],
        midi_regions=[],
        audio_files={10: "Guitar.wav"},
        audio_regions=[{"file": 10, "index": 0, "name": "Middle", "offset": 2 * RATE, "length": 2 * RATE}],
        audio_placements=placements or [{"bar": 3, "track": 1, "file": 10, "lane": 1}],
    )
    logicx = build_synthetic_logicx(tmp_path / "source", project_data=data)
    _three_level_wav(logicx / "Media" / "Audio Files" / "Guitar.wav")
    return logicx


def test_logic_to_protools_renders_only_the_region_slice(tmp_path):
    project = parse_logic_project(_logic_project(tmp_path))
    result = generate_protools_transfer_from_logic(project, tmp_path / "out")

    exported = next(result.package_path.glob("Audio Files/**/*.wav"))
    with wave.open(str(exported)) as handle:
        frames = handle.getnframes()
        first = struct.unpack("<h", handle.readframes(1))[0]
    assert (frames, first) == (2 * RATE, 2000)

    manifest = json.loads((result.package_path / "manifest.json").read_text(encoding="utf-8"))
    entry = manifest["tracks"][0]["clips"][0]
    assert (entry["content_offset_samples"], entry["content_duration_samples"]) == (2 * RATE, 2 * RATE)
    # bar 3 at 120 BPM is eight beats, four seconds after bar 1
    assert entry["time_reference_samples"] == 4 * RATE


def test_tempo_map_keeps_the_trimmed_source_offset(tmp_path):
    project = parse_logic_project(_logic_project(tmp_path))
    project.timeline = Timeline(tempo_events=[TempoEvent(beat=4.0, bpm=60.0)], markers=[])
    als = generate_als(project, tmp_path / "out", copy_audio=False, template_path=_BUNDLED_TEMPLATE)

    clip = parse_ableton_project(als).clips[0]
    assert clip.start_beats == 8.0
    assert clip.source_in_seconds == 2.0


def test_tempo_breakpoint_inside_a_trimmed_clip_maps_from_the_offset(tmp_path):
    project = parse_logic_project(_logic_project(tmp_path, placements=[{"bar": 1.5, "track": 1, "file": 10, "lane": 1}]))
    project.timeline = Timeline(tempo_events=[TempoEvent(beat=4.0, bpm=60.0)], markers=[])
    als = generate_als(project, tmp_path / "out", copy_audio=False, template_path=_BUNDLED_TEMPLATE)

    root = ET.fromstring(gzip.decompress(als.read_bytes()))
    markers = [(float(m.get("SecTime")), float(m.get("BeatTime"))) for m in root.findall(".//AudioClip/WarpMarkers/WarpMarker")]
    # Region at beat 2 plays from source second 2 (content beat 4 at 120 BPM);
    # the change to 60 BPM at beat 4 is one second later in source and two beats later in content.
    assert markers[:3] == [(0.0, 0.0), (2.0, 4.0), (3.0, 6.0)]
    assert parse_ableton_project(als).clips[0].source_in_seconds == 2.0


def test_looped_audio_region_becomes_repeated_clips(tmp_path):
    # The region is two seconds (four beats at 120 BPM); the loop spans 14 beats.
    logicx = _logic_project(tmp_path, placements=[{"bar": 1, "track": 1, "file": 10, "lane": 1, "loop_beats": 14}])
    project = parse_logic_project(logicx)

    clips = [(c.start_beats, c.content_offset_samples, c.content_duration_samples) for c in project.audio_files]
    assert clips == [
        (0.0, 2 * RATE, 2 * RATE),
        (4.0, 2 * RATE, 2 * RATE),
        (8.0, 2 * RATE, 2 * RATE),
        (12.0, 2 * RATE, RATE),
    ]
    assert any("looped audio region" in warning and "x4" in warning for warning in project.compatibility_warnings)

    als = generate_als(project, tmp_path / "out", copy_audio=False, template_path=_BUNDLED_TEMPLATE)
    rendered = parse_ableton_project(als).clips
    assert [(c.start_beats, c.end_beats) for c in rendered] == [(0.0, 4.0), (4.0, 8.0), (8.0, 12.0), (12.0, 14.0)]


def test_same_named_logic_tracks_stay_separate(tmp_path):
    logicx = _logic_project(
        tmp_path,
        tracks={1: "Guitar", 2: "Guitar"},
        placements=[
            {"bar": 1, "track": 1, "file": 10, "lane": 1},
            {"bar": 1, "track": 2, "file": 10, "lane": 2},
        ],
    )
    project = parse_logic_project(logicx)
    assert project.track_names == ["Guitar", "Guitar (2)"]
    assert sorted(ref.track_name for ref in project.audio_files) == ["Guitar", "Guitar (2)"]
    assert any("were numbered" in warning for warning in project.compatibility_warnings)

    als = generate_als(project, tmp_path / "out", copy_audio=False, template_path=_BUNDLED_TEMPLATE)
    root = ET.fromstring(gzip.decompress(als.read_bytes()))
    assert len(root.findall(".//Tracks/AudioTrack")) == 2


def test_short_project_data_does_not_raise():
    blob = _logic_object("Song", b"\x00" * 142)
    assert len(blob) == 174
    assert decode_project_data(blob).regions == []


def test_protools_midi_chunk_search_stays_inside_its_block(tmp_path):
    ptx = build_synthetic_ptx(tmp_path, midi=False)
    raw = _deobfuscate(ptx.read_bytes())
    for index, pitch in enumerate((60, 64, 67)):
        event = _pt_midi_event(500_000_000, pitch, PT_TICKS_PER_QUARTER, 100)
        payload = b"MdNLB" + b"\x00" * 6 + struct.pack("<I", 1) + event
        if index == 0:
            payload += b"\x00" * 40  # unused tail bytes inside the first chunk's block
        raw += _pt_block(1, 0x2000, payload)
    raw += _pt_block(1, 0x2519, _pt_block(2, 0x251A, b"\x00\x00" + _pt_string("Third chunk")))
    raw += _pt_block(1, 0x2634, _pt_block(2, 0x2633, _pt_block(2, 0x2628, b"\x00\x00") + struct.pack("<I", 2)))
    placement = _pt_block(
        2, 0x104F,
        b"\x00\x00" + struct.pack("<I", 0) + b"\x00" + (_PT_ZERO_TICKS + 4 * PT_TICKS_PER_QUARTER).to_bytes(5, "little"),
    )
    raw += _pt_block(1, 0x1058, b"\x00\x00" + _pt_block(2, 0x1057, _pt_block(2, 0x1056, placement)))
    ptx.write_bytes(_deobfuscate(raw))

    session = parse_protools_session(ptx)
    assert [note.pitch for note in session.midi_tracks[0].notes] == [67]


def test_generation_failure_is_written_into_the_saved_report(tmp_path, capsys):
    logicx = _logic_project(tmp_path)
    bad_template = tmp_path / "invalid-template.als"
    bad_template.write_bytes(gzip.compress(b"<Ableton><LiveSet><Tracks/></LiveSet></Ableton>"))

    exit_code = main([
        str(logicx), "--output", str(tmp_path / "out"), "--json-progress", "--template", str(bad_template),
    ])
    event = json.loads(capsys.readouterr().out.strip().splitlines()[-1])

    assert exit_code == 1
    assert event["stage"] == "error"
    assert event["failure_stage"] == "generating"
    assert "No AudioTrack found" in event["error"]
    assert "No AudioTrack found" in event["report"]
    saved = Path(event["report_path"]).read_text(encoding="utf-8")
    assert "CONVERSION FAILED" in saved and "No AudioTrack found" in saved
    assert "TRACKS TRANSFERRED" in saved  # the analysis report is kept


def test_undo_history_snapshots_are_not_read_as_the_current_arrangement(tmp_path):
    """Logic keeps undo history in the save: extra Song objects followed by old copies of
    whatever each step changed. Only the state before the second Song is current."""
    old_state = dict(
        tracks={1: "Gtr old name"},
        sequences=[{"id": 44, "name": "Riff", "length": 3840, "notes": [(0, 40, 90, 240), (960, 41, 90, 240)]}],
        midi_regions=[{"bar": 9, "track": 2, "sequence": 44, "lane": 2}],
        audio_files={10: "Guitar.wav"},
        audio_regions=[{"file": 10, "index": 0, "name": "Old trim", "offset": 0, "length": RATE}],
        audio_placements=[{"bar": 5, "track": 1, "file": 10, "lane": 1}],
        markers=[(17, 12, "Old marker")],
    )
    data = build_logic_arrangement_project_data(
        tracks={1: "Guitar", 2: "Keys"},
        sequences=[{"id": 44, "name": "Riff", "length": 3840, "notes": [(0, 60, 100, 240)]}],
        midi_regions=[{"bar": 2, "track": 2, "sequence": 44, "lane": 2}],
        audio_files={10: "Guitar.wav"},
        audio_regions=[{"file": 10, "index": 0, "name": "Middle", "offset": 2 * RATE, "length": 2 * RATE}],
        audio_placements=[{"bar": 3, "track": 1, "file": 10, "lane": 1}],
        markers=[(5, 12, "Verse")],
        history=[old_state, old_state, old_state],
    )
    arrangement = decode_project_data(data)
    assert arrangement.history_steps == 3
    assert len(arrangement.regions) == 2
    assert arrangement.track_names == {1: "Guitar", 2: "Keys"}

    logicx = build_synthetic_logicx(tmp_path / "source", project_data=data)
    _three_level_wav(logicx / "Media" / "Audio Files" / "Guitar.wav")
    project = parse_logic_project(logicx)

    assert [(c.track_name, c.clip_name, c.start_beats, c.content_offset_samples) for c in project.audio_files] == [
        ("Guitar", "Middle", 8.0, 2 * RATE),
    ]
    assert [(t.name, len(t.regions), t.regions[0].start_beats) for t in project.midi_tracks] == [("Keys", 1, 4.0)]
    assert [(n.pitch, n.start_beats) for n in project.midi_tracks[0].notes] == [(60, 4.0)]
    assert [(m.beat, m.name) for m in project.timeline.markers] == [(16.0, "Verse")]
