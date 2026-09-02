"""Tests for the clean-room Pro Tools session parser and import mappers."""

import os
from pathlib import Path

import pytest

from scripts.fixture_builders import build_synthetic_ptx

from logic2ableton.protools_import import (
    build_protools_import_report,
    protools_to_ableton_project,
    protools_to_logic_project,
)
from logic2ableton.protools_parser import (
    ProToolsMidiNote,
    ProToolsMidiTrack,
    ProToolsParseError,
    ProToolsRegion,
    ProToolsSession,
    ProToolsTrack,
    parse_protools_session,
)

_PTX_FIXTURE_PATH = os.environ.get("L2A_PTX_FIXTURE")
PTX_FIXTURE = Path(_PTX_FIXTURE_PATH) if _PTX_FIXTURE_PATH else None


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
    assert PTX_FIXTURE is not None
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
    assert PTX_FIXTURE is not None
    session = parse_protools_session(PTX_FIXTURE)
    project = protools_to_logic_project(session, tempo=120.0)
    # Lane pairs merge to one track each; empty tracks are dropped.
    assert len(project.track_names) == len(set(project.track_names))
    assert "Scrt Keys.03" in project.track_names
    assert "PRINT.04" not in project.track_names  # no regions
    vox = [ref for ref in project.audio_files if ref.track_name == "Scrt Vox.03"]
    assert len(vox) == 32
