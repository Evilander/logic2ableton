import json
import wave

from conftest import create_test_als

from logic2ableton.ableton_parser import parse_ableton_project
from logic2ableton.logic_transfer import build_logic_transfer_report, generate_logic_transfer


def test_generate_logic_transfer_creates_expected_files(tmp_path):
    als_path = create_test_als(tmp_path)
    project = parse_ableton_project(als_path)

    transfer = generate_logic_transfer(project, tmp_path / "output")

    assert transfer.package_path.exists()
    assert transfer.artifact_path.name == "IMPORT_TO_LOGIC.md"
    assert transfer.report_path.exists()
    assert (transfer.package_path / "timeline_manifest.json").exists()
    assert (transfer.package_path / "timeline_manifest.csv").exists()
    assert (transfer.package_path / "locators.csv").exists()


def test_generate_logic_transfer_copies_audio_by_track(tmp_path):
    als_path = create_test_als(tmp_path)
    project = parse_ableton_project(als_path)

    transfer = generate_logic_transfer(project, tmp_path / "output")

    audio_root = transfer.package_path / "Audio Files"
    exported = sorted(audio_root.rglob("*.wav"))
    assert len(exported) == len(project.clips)
    assert any("Drums" in str(path.parent) for path in exported)
    assert any("Vocals" in str(path.parent) for path in exported)


def test_generate_logic_transfer_manifest_includes_warnings(tmp_path):
    als_path = create_test_als(tmp_path, include_missing_clip=True)
    project = parse_ableton_project(als_path)

    transfer = generate_logic_transfer(project, tmp_path / "output")
    manifest = json.loads((transfer.package_path / "timeline_manifest.json").read_text(encoding="utf-8"))

    assert manifest["format"] == "ableton2logic.transfer/v1"
    assert manifest["project_name"] == "Demo Set"
    assert manifest["compatibility_warnings"]
    assert len(manifest["tracks"]) == 2


def test_build_logic_transfer_report_mentions_transfer_package(tmp_path):
    als_path = create_test_als(tmp_path)
    project = parse_ableton_project(als_path)

    report = build_logic_transfer_report(project)

    assert "Ableton Live to Logic Transfer Report" in report
    assert "TRANSFER PACKAGE CONTENTS" in report
    assert "IMPORT_TO_LOGIC.md" in report


def test_generate_logic_transfer_trims_wav_clips(tmp_path):
    als_path = create_test_als(tmp_path)
    project = parse_ableton_project(als_path)

    transfer = generate_logic_transfer(project, tmp_path / "output")

    exported_vox = next(path for path in (transfer.package_path / "Audio Files").rglob("*.wav") if "Lead Vox" in path.name)
    source_vox = next(clip.source_path for clip in project.clips if clip.clip_name == "Lead Vox")
    assert source_vox is not None

    with wave.open(str(source_vox), "rb") as source_handle:
        source_frames = source_handle.getnframes()
    with wave.open(str(exported_vox), "rb") as export_handle:
        export_frames = export_handle.getnframes()

    assert export_frames < source_frames


def test_generate_logic_transfer_manifest_omits_absolute_source_paths(tmp_path):
    als_path = create_test_als(tmp_path)
    project = parse_ableton_project(als_path)

    transfer = generate_logic_transfer(project, tmp_path / "output")
    manifest_text = (transfer.package_path / "timeline_manifest.json").read_text(encoding="utf-8")

    assert '"source_path"' not in manifest_text
    assert str(tmp_path) not in manifest_text
