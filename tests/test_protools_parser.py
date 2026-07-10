"""Tests for the clean-room Pro Tools session parser and import mappers."""

import os
import struct
from pathlib import Path

import pytest

from logic2ableton.protools_import import (
    build_protools_import_report,
    protools_to_ableton_project,
    protools_to_logic_project,
)
from logic2ableton.protools_parser import (
    _PT_ZERO_TICKS,
    ProToolsMidiNote,
    ProToolsMidiTrack,
    ProToolsParseError,
    ProToolsRegion,
    ProToolsSession,
    ProToolsTrack,
    PT_TICKS_PER_QUARTER,
    _deobfuscate,
    parse_protools_session,
)

PTX_FIXTURE = Path(
    os.environ.get(
        "L2A_PTX_FIXTURE",
        "D:/New Mixes/Miley Cyrus - Flowers - Scratch tracks (No Band)/"
        "MC - Flowers - Scratch tracks (No Band).ptx",
    )
)


# ---------------------------------------------------------------------------
# Synthetic session builder: assembles the block structure byte-by-byte from
# the documented format, then applies the (symmetric) XOR obfuscation.
# ---------------------------------------------------------------------------

def _blk(btype: int, ctype: int, payload: bytes) -> bytes:
    return (
        b"\x5a"
        + struct.pack("<H", btype)
        + struct.pack("<I", len(payload) + 2)
        + struct.pack("<H", ctype)
        + payload
    )


def _pstring(text: str) -> bytes:
    raw = text.encode("utf-8")
    return struct.pack("<I", len(raw)) + raw


def _three_point(start: int, offset: int, length: int) -> bytes:
    # descriptor: widths (4 bytes each) in the high nibbles of bytes 1..3
    return bytes([0, 0x40, 0x40, 0x40, 0]) + struct.pack("<III", offset, length, start)


def _midi_event(pos_ticks: int, note: int, length_ticks: int, velocity: int) -> bytes:
    ev = bytearray(35)
    ev[0:5] = pos_ticks.to_bytes(5, "little")
    ev[8] = note
    ev[9:14] = length_ticks.to_bytes(5, "little")
    ev[17] = velocity
    return bytes(ev)


def build_synthetic_ptx(
    tmp_path: Path,
    *,
    sample_rate: int = 48000,
    wav_name: str = "Guitar.wav",
    wav_frames: int = 44100,
    region=(96000, 1000, 22050),  # (start, offset, length) in samples
    track_name: str = "Guitar",
    midi: bool = True,
) -> Path:
    start, offset, length = region

    header = bytes([0x03]) + b"0010111100101011" + bytes([0x00, 0x05, 77])
    assert len(header) == 20

    first = _blk(1, 0x2206, b"\x00\x00")  # 11 bytes -> next block lands at 0x1F
    version = _blk(1, 0x2067, b"\x00" * 18 + struct.pack("<I", 10))  # 2 + 10 = v12

    rate = _blk(2, 0x1028, b"\x00\x00" + struct.pack("<I", sample_rate))

    wav_entry = _pstring(wav_name) + b"WAVE" + b"\x00" * 5
    wav_names = _blk(2, 0x103A, b"\x00" * 9 + wav_entry)
    wav_meta = _blk(2, 0x1003, _blk(2, 0x1001, b"\x00" * 6 + struct.pack("<Q", wav_frames)))
    wav_list = _blk(1, 0x1004, struct.pack("<I", 1) + wav_names + wav_meta)

    inner = _blk(2, 0x2628, b"\x00\x00")
    region_entry = _blk(
        2,
        0x2629,
        b"\x00" * 9 + _pstring("Guitar-01") + _three_point(start, offset, length)
        + inner
        + struct.pack("<I", 0),  # wav index trailing the inner block
    )
    region_list = _blk(1, 0x262A, struct.pack("<I", 1) + region_entry)

    placement = _blk(2, 0x104F, b"\x00\x00" + struct.pack("<I", 0) + b"\x00" + struct.pack("<I", start))
    lane_entry_payload = bytearray(placement)
    lane_entry_payload += b"\x00" * (45 - len(lane_entry_payload))  # fade byte at 44 stays 0
    lane_entry = _blk(2, 0x1050, bytes(lane_entry_payload))
    lane = _blk(2, 0x1052, _pstring(track_name) + lane_entry)
    track_map = _blk(1, 0x1054, b"\x00\x00" + lane)

    parts = [rate, wav_list, region_list, track_map]

    if midi:
        note_region_ticks = 4 * PT_TICKS_PER_QUARTER
        # Event positions are absolute ticks; the first event's position doubles
        # as the chunk's zero reference (there is no separate zero field).
        zero = 500_000_000
        events = _midi_event(zero, 60, 2 * PT_TICKS_PER_QUARTER, 100) + _midi_event(
            zero + 2 * PT_TICKS_PER_QUARTER, 64, PT_TICKS_PER_QUARTER, 90
        )
        midi_block = _blk(
            1,
            0x2000,
            b"MdNLB" + b"\x00" * 6 + struct.pack("<I", 2) + events,
        )
        midi_names = _blk(1, 0x2519, _blk(2, 0x251A, b"\x00\x00" + _pstring("Synth")))
        mr_entry = _blk(2, 0x2628, b"\x00\x00")
        midi_regions = _blk(
            1, 0x2634, _blk(2, 0x2633, mr_entry + struct.pack("<I", 0))
        )
        midi_placement = _blk(
            2,
            0x104F,
            b"\x00\x00" + struct.pack("<I", 0) + b"\x00"
            + (_PT_ZERO_TICKS + note_region_ticks).to_bytes(5, "little"),
        )
        midi_lane = _blk(2, 0x1057, _blk(2, 0x1056, midi_placement))
        midi_map = _blk(1, 0x1058, b"\x00\x00" + midi_lane)
        parts += [midi_block, midi_names, midi_regions, midi_map]

    # Pad past the first 4 KiB page so content is genuinely XOR-obfuscated
    # (page 0's key byte is always zero).
    plaintext = header + first + version
    plaintext += b"\x00" * (4096 - len(plaintext))
    plaintext += b"".join(parts)

    # The XOR transform is symmetric: applying the deobfuscation routine to
    # plaintext produces the obfuscated file.
    obfuscated = _deobfuscate(plaintext)
    assert obfuscated[0x1000:] != plaintext[0x1000:], "expected content pages to be obfuscated"

    ptx = tmp_path / "Synthetic Session.ptx"
    ptx.write_bytes(obfuscated)
    return ptx


# ---------------------------------------------------------------------------
# Parser tests (synthetic, always run)
# ---------------------------------------------------------------------------

def test_parse_synthetic_session_audio(tmp_path):
    ptx = build_synthetic_ptx(tmp_path)
    session = parse_protools_session(ptx)

    assert session.version == 12
    assert session.sample_rate == 48000
    assert [w.filename for w in session.audio_files] == ["Guitar.wav"]
    assert session.audio_files[0].length_frames == 44100

    assert [t.name for t in session.tracks] == ["Guitar"]
    (region,) = session.tracks[0].regions
    assert region.name == "Guitar-01"
    assert region.filename == "Guitar.wav"
    assert (region.start_samples, region.offset_samples, region.length_samples) == (96000, 1000, 22050)


def test_parse_synthetic_session_midi(tmp_path):
    ptx = build_synthetic_ptx(tmp_path)
    session = parse_protools_session(ptx)

    assert [t.name for t in session.midi_tracks] == ["Synth"]
    notes = session.midi_tracks[0].notes
    assert [(n.pitch, n.start_beats, n.duration_beats, n.velocity) for n in notes] == [
        (60, 4.0, 2.0, 100),
        (64, 6.0, 1.0, 90),
    ]


def test_parse_rejects_non_protools_file(tmp_path):
    bogus = tmp_path / "fake.ptx"
    bogus.write_bytes(b"\x03not a real session at all" + b"\x00" * 64)
    with pytest.raises(ProToolsParseError):
        parse_protools_session(bogus)


# ---------------------------------------------------------------------------
# Import mapper tests (pure model transforms)
# ---------------------------------------------------------------------------

def _stereo_session(tmp_path: Path) -> ProToolsSession:
    region_l = ProToolsRegion(
        name="Keys-01.L", index=0, start_samples=48000, offset_samples=100,
        length_samples=24000, wav_index=0, filename="Keys.wav",
    )
    region_r = ProToolsRegion(
        name="Keys-01.R", index=0, start_samples=48000, offset_samples=100,
        length_samples=24000, wav_index=0, filename="Keys.wav",
    )
    return ProToolsSession(
        name="Stereo Demo",
        path=tmp_path / "Stereo Demo.ptx",
        version=12,
        sample_rate=48000,
        audio_files=[],
        tracks=[
            ProToolsTrack(name="Keys", index=0, regions=[region_l]),
            ProToolsTrack(name="Keys", index=1, regions=[region_r]),
        ],
        midi_tracks=[
            ProToolsMidiTrack(
                name="Lead",
                notes=[ProToolsMidiNote(pitch=72, start_beats=0.0, duration_beats=1.0, velocity=110)],
            )
        ],
    )


def test_import_merges_stereo_lanes_and_strips_suffix(tmp_path):
    project = protools_to_logic_project(_stereo_session(tmp_path), tempo=120.0)
    assert project.track_names == ["Keys"]
    assert len(project.audio_files) == 1
    ref = project.audio_files[0]
    assert ref.clip_name == "Keys-01"
    assert ref.start_position_samples == 48000
    assert ref.content_offset_samples == 100
    assert ref.content_duration_samples == 24000
    assert [t.name for t in project.midi_tracks] == ["Lead"]
    assert any("tempo" in w.lower() for w in project.compatibility_warnings)
    assert any("not found" in w for w in project.compatibility_warnings)  # Keys.wav missing


def test_import_to_ableton_project_positions_are_tempo_consistent(tmp_path):
    session = _stereo_session(tmp_path)
    project = protools_to_ableton_project(session, tempo=90.0)
    (track,) = project.audio_tracks
    (clip,) = track.clips
    # beats -> samples round trip must land back on the source positions
    sr = session.sample_rate
    assert round(clip.start_beats * 60 * sr / project.tempo) == 48000
    assert round(clip.source_in_beats * 60 * sr / project.tempo) == 100
    assert round(clip.duration_beats * 60 * sr / project.tempo) == 24000


def test_import_report_mentions_session_and_limits(tmp_path):
    session = _stereo_session(tmp_path)
    report = build_protools_import_report(session, destination="Ableton Live", tempo=120.0)
    assert "Stereo Demo" in report
    assert "Pro Tools to Ableton Live" in report
    assert "NOT TRANSFERRED" in report
    assert "Keys - 1 clip(s)" in report


# ---------------------------------------------------------------------------
# End-to-end: synthetic .ptx -> .als -> parse back
# ---------------------------------------------------------------------------

def test_synthetic_ptx_to_als_round_trip(tmp_path):
    from logic2ableton.ableton_generator import generate_als
    from logic2ableton.ableton_parser import parse_ableton_project
    from conftest import write_test_wav

    ptx = build_synthetic_ptx(tmp_path, sample_rate=48000)
    audio_dir = ptx.parent / "Audio Files"
    write_test_wav(audio_dir / "Guitar.wav", frames=118050, sample_rate=48000)

    session = parse_protools_session(ptx)
    project = protools_to_logic_project(session, tempo=120.0)
    als_path = generate_als(project, tmp_path / "out", copy_audio=False)

    parsed = parse_ableton_project(als_path)
    assert [t.name for t in parsed.audio_tracks] == ["Guitar"]
    (clip,) = parsed.audio_tracks[0].clips
    # 96000 samples @48k @120bpm = 4 beats; 22050-sample slice = 0.91875 beats
    # Generator formatting keeps 6 decimals; 1e-5 beats is far below one sample.
    assert clip.start_beats == pytest.approx(4.0, abs=1e-5)
    assert clip.duration_beats == pytest.approx(22050 / 48000 / 60 * 120, abs=1e-5)
    assert clip.source_in_beats == pytest.approx(1000 / 48000 / 60 * 120, abs=1e-5)

    assert [t.name for t in parsed.midi_tracks] == ["Synth"]
    notes = {(n.pitch, n.start_beats, n.duration_beats) for n in parsed.midi_tracks[0].notes}
    assert notes == {(60, 4.0, 2.0), (64, 6.0, 1.0)}


# ---------------------------------------------------------------------------
# Real-session tests (gated on the local fixture)
# ---------------------------------------------------------------------------

@pytest.mark.needs_ptx_fixture
def test_parse_real_session_ground_truth():
    session = parse_protools_session(PTX_FIXTURE)
    assert session.version == 12
    assert session.sample_rate == 96000
    assert len(session.audio_files) == 8
    names = {t.name for t in session.tracks}
    assert {"Scrt Keys.03", "Scrt Vox.03", "Live Chamber.cm"} <= names

    # Stereo lanes mirror each other exactly
    keys_lanes = [t for t in session.tracks if t.name == "Scrt Keys.03"]
    assert len(keys_lanes) == 2
    left = [(r.start_samples, r.offset_samples, r.length_samples) for r in keys_lanes[0].regions]
    right = [(r.start_samples, r.offset_samples, r.length_samples) for r in keys_lanes[1].regions]
    assert left == right


@pytest.mark.needs_ptx_fixture
def test_import_real_session_merges_lanes():
    session = parse_protools_session(PTX_FIXTURE)
    project = protools_to_logic_project(session, tempo=120.0)
    # Lane pairs merge to one track each; empty tracks are dropped.
    assert len(project.track_names) == len(set(project.track_names))
    assert "Scrt Keys.03" in project.track_names
    assert "PRINT.04" not in project.track_names  # no regions
    vox = [ref for ref in project.audio_files if ref.track_name == "Scrt Vox.03"]
    assert len(vox) == 32
