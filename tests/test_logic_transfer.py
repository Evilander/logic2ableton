import json
import math
import struct
import wave
from pathlib import Path

from conftest import create_test_als

from logic2ableton.ableton_parser import parse_ableton_project
from logic2ableton.logic_parser import _get_bwf_time_reference
from logic2ableton.logic_transfer import _read_decoded_audio, build_logic_transfer_report, generate_logic_transfer
from logic2ableton.models import AbletonAudioClip, AbletonProject, AbletonTrack


def _encode_extended_float80(value: float) -> bytes:
    if value == 0:
        return b"\x00" * 10

    fraction, exponent = math.frexp(abs(value))
    mantissa = int(fraction * (1 << 64))
    biased_exponent = exponent + 16382
    return struct.pack(">H", biased_exponent) + mantissa.to_bytes(8, "big")


def _write_test_aiff(path: Path, samples: list[int], *, sample_rate: int = 10) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    sound_data = b"".join(sample.to_bytes(2, "big", signed=True) for sample in samples)
    comm_payload = (
        struct.pack(">hIh", 1, len(samples), 16)
        + _encode_extended_float80(float(sample_rate))
    )
    ssnd_payload = struct.pack(">II", 0, 0) + sound_data
    form_payload = (
        b"AIFF"
        + b"COMM"
        + struct.pack(">I", len(comm_payload))
        + comm_payload
        + b"SSND"
        + struct.pack(">I", len(ssnd_payload))
        + ssnd_payload
    )
    path.write_bytes(b"FORM" + struct.pack(">I", len(form_payload)) + form_payload)
    return path


def test_generate_logic_transfer_creates_expected_files(tmp_path):
    als_path = create_test_als(tmp_path)
    project = parse_ableton_project(als_path)

    transfer = generate_logic_transfer(project, tmp_path / "output")

    assert transfer.package_path.exists()
    assert transfer.artifact_path.name == "IMPORT_TO_LOGIC.md"
    assert transfer.report_path.exists()
    assert transfer.timeline_path is not None
    assert transfer.timeline_path.exists()
    assert (transfer.package_path / "timeline_manifest.json").exists()
    assert (transfer.package_path / "timeline_manifest.csv").exists()
    assert (transfer.package_path / "locators.csv").exists()
    assert (transfer.package_path / "Track Stems").exists()


def test_generate_logic_transfer_copies_audio_by_track(tmp_path):
    als_path = create_test_als(tmp_path)
    project = parse_ableton_project(als_path)

    transfer = generate_logic_transfer(project, tmp_path / "output")

    audio_root = transfer.package_path / "Audio Files"
    exported = sorted(audio_root.rglob("*.wav"))
    assert len(exported) == len(project.clips)
    assert any("Drums" in str(path.parent) for path in exported)
    assert any("Vocals" in str(path.parent) for path in exported)


def test_generate_logic_transfer_manifest_includes_new_import_artifacts(tmp_path):
    als_path = create_test_als(tmp_path, include_missing_clip=True)
    project = parse_ableton_project(als_path)

    transfer = generate_logic_transfer(project, tmp_path / "output")
    manifest = json.loads((transfer.package_path / "timeline_manifest.json").read_text(encoding="utf-8"))

    assert manifest["format"] == "ableton2logic.transfer/v2"
    assert manifest["project_name"] == "Demo Set"
    assert manifest["compatibility_warnings"]
    assert manifest["logic_timeline_midi"] == "Logic Timeline/Logic Timeline.mid"
    assert len(manifest["tracks"]) == 2
    assert manifest["tracks"][0]["stem_export_name"].endswith(".wav")


def test_build_logic_transfer_report_mentions_import_layers(tmp_path):
    als_path = create_test_als(tmp_path)
    project = parse_ableton_project(als_path)

    report = build_logic_transfer_report(project)

    assert "Ableton Live to Logic Transfer Report" in report
    assert "Track Stems/:" in report
    assert "Logic Timeline/" in report
    assert "Edit > Move > To Recorded Position" not in report


def test_generate_logic_transfer_exports_timestamped_clip_wavs(tmp_path):
    als_path = create_test_als(tmp_path)
    project = parse_ableton_project(als_path)

    transfer = generate_logic_transfer(project, tmp_path / "output")

    exported_vox = next(path for path in (transfer.package_path / "Audio Files").rglob("*.wav") if "Lead Vox" in path.name)
    lead_vox = next(clip for clip in project.clips if clip.clip_name == "Lead Vox")

    with wave.open(str(exported_vox), "rb") as export_handle:
        export_frames = export_handle.getnframes()
        export_rate = export_handle.getframerate()

    expected_frames = int(round((lead_vox.duration_beats * 60.0 / project.tempo) * export_rate))
    expected_time_reference = int(round((lead_vox.start_beats * 60.0 / project.tempo) * export_rate))

    assert export_frames == expected_frames
    assert _get_bwf_time_reference(exported_vox) == expected_time_reference


def test_generate_logic_transfer_renders_track_stems_for_clean_import(tmp_path):
    als_path = create_test_als(tmp_path)
    project = parse_ableton_project(als_path)

    transfer = generate_logic_transfer(project, tmp_path / "output")
    stems = sorted((transfer.package_path / "Track Stems").glob("*.wav"))

    assert stems
    assert transfer.rendered_stem_files == len(stems)

    with wave.open(str(stems[0]), "rb") as stem_handle:
        assert stem_handle.getnframes() > 44_100
        assert _get_bwf_time_reference(stems[0]) == 0


def test_generate_logic_transfer_timeline_midi_contains_locator_names(tmp_path):
    als_path = create_test_als(tmp_path)
    project = parse_ableton_project(als_path)

    transfer = generate_logic_transfer(project, tmp_path / "output")
    midi_bytes = transfer.timeline_path.read_bytes() if transfer.timeline_path is not None else b""

    assert midi_bytes.startswith(b"MThd")
    assert b"Verse" in midi_bytes


def test_generate_logic_transfer_manifest_omits_absolute_source_paths(tmp_path):
    als_path = create_test_als(tmp_path)
    project = parse_ableton_project(als_path)

    transfer = generate_logic_transfer(project, tmp_path / "output")
    manifest_text = (transfer.package_path / "timeline_manifest.json").read_text(encoding="utf-8")

    assert '"source_path"' not in manifest_text
    assert str(tmp_path) not in manifest_text


def test_read_decoded_audio_normalizes_aiff_pcm_to_wave_bytes(tmp_path):
    aiff_path = _write_test_aiff(tmp_path / "source.aiff", [1, -2, 32767, -32768], sample_rate=22050)

    decoded = _read_decoded_audio(aiff_path, {})

    assert decoded is not None
    assert decoded.frame_rate == 22050
    assert decoded.channels == 1
    assert decoded.sample_width == 2
    assert decoded.frames == (
        (1).to_bytes(2, "little", signed=True)
        + (-2).to_bytes(2, "little", signed=True)
        + (32767).to_bytes(2, "little", signed=True)
        + (-32768).to_bytes(2, "little", signed=True)
    )


def test_generate_logic_transfer_exports_timestamped_wavs_from_aiff_sources(tmp_path):
    aiff_path = _write_test_aiff(tmp_path / "Samples" / "Imported" / "line.aiff", list(range(20)), sample_rate=10)
    track = AbletonTrack(
        name="AIFF Track",
        clips=[
            AbletonAudioClip(
                clip_name="AIFF Line",
                track_name="AIFF Track",
                source_path=aiff_path,
                relative_source_path="Samples/Imported/line.aiff",
                start_beats=1.0,
                end_beats=2.0,
                source_in_beats=0.5,
            )
        ],
    )
    project = AbletonProject(
        name="AIFF Demo",
        tempo=60.0,
        time_sig_numerator=4,
        time_sig_denominator=4,
        audio_tracks=[track],
        locators=[],
    )

    transfer = generate_logic_transfer(project, tmp_path / "output")
    exported = next((transfer.package_path / "Audio Files").rglob("*.wav"))
    stem = next((transfer.package_path / "Track Stems").glob("*.wav"))
    expected_frames = b"".join(sample.to_bytes(2, "little", signed=True) for sample in range(5, 15))

    with wave.open(str(exported), "rb") as clip_handle:
        assert clip_handle.getframerate() == 10
        assert clip_handle.readframes(clip_handle.getnframes()) == expected_frames

    with wave.open(str(stem), "rb") as stem_handle:
        stem_frames = stem_handle.readframes(stem_handle.getnframes())

    assert transfer.rendered_stem_files == 1
    assert stem_frames[:20] == b"\x00" * 20
    assert stem_frames[20:40] == expected_frames
    assert _get_bwf_time_reference(exported) == 10
