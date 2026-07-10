import json
import wave

from conftest import create_test_als, write_test_wav

from logic2ableton.ableton_parser import parse_ableton_project
from logic2ableton.logic_parser import _get_bwf_time_reference
from logic2ableton.models import (
    AbletonAudioClip,
    AbletonMidiClip,
    AbletonMidiNote,
    AbletonMidiTrack,
    AbletonProject,
    AbletonTrack,
    AudioFileRef,
    LogicProject,
)
from logic2ableton.protools_transfer import (
    build_protools_transfer_report,
    generate_protools_transfer,
    generate_protools_transfer_from_logic,
)


def test_generate_protools_transfer_creates_expected_layout(tmp_path):
    als_path = create_test_als(tmp_path)
    project = parse_ableton_project(als_path)

    result = generate_protools_transfer(project, tmp_path / "output")

    assert result.package_path.name == "Demo Set Pro Tools Transfer"
    assert result.package_path.exists()
    assert result.artifact_path.name == "IMPORT GUIDE.txt"
    assert result.artifact_path.exists()
    assert result.report_path.exists()
    assert (result.package_path / "manifest.json").exists()

    audio_root = result.package_path / "Audio Files"
    exported = sorted(audio_root.rglob("*.wav"))
    assert len(exported) == len(project.clips)
    assert any("Drums" in str(path.parent) for path in exported)
    assert any("Vocals" in str(path.parent) for path in exported)


def test_generate_protools_transfer_stamps_time_reference_without_offset(tmp_path):
    als_path = create_test_als(tmp_path)
    project = parse_ableton_project(als_path)

    result = generate_protools_transfer(project, tmp_path / "output")

    exported_vox = next(
        path for path in (result.package_path / "Audio Files").rglob("*.wav") if "Lead Vox" in path.name
    )
    lead_vox = next(clip for clip in project.clips if clip.clip_name == "Lead Vox")

    with wave.open(str(exported_vox), "rb") as handle:
        export_rate = handle.getframerate()

    expected_time_reference = int(round((lead_vox.start_beats * 60.0 / project.tempo) * export_rate))

    assert _get_bwf_time_reference(exported_vox) == expected_time_reference
    # No SMPTE offset: a naive 1-hour-offset stamp would be far larger than this.
    assert _get_bwf_time_reference(exported_vox) < 3600 * export_rate


def test_generate_protools_transfer_exports_midi_tracks(tmp_path):
    midi_track = AbletonMidiTrack(
        name="Synth",
        clips=[
            AbletonMidiClip(
                clip_name="Synth clip",
                track_name="Synth",
                start_beats=0.0,
                end_beats=4.0,
                notes=[
                    AbletonMidiNote(pitch=60, start_beats=0.0, duration_beats=1.0, velocity=100),
                    AbletonMidiNote(pitch=64, start_beats=1.0, duration_beats=1.0, velocity=100),
                ],
            )
        ],
    )
    project = AbletonProject(
        name="MIDI Demo",
        tempo=120.0,
        time_sig_numerator=4,
        time_sig_denominator=4,
        audio_tracks=[],
        locators=[],
        midi_tracks=[midi_track],
    )

    result = generate_protools_transfer(project, tmp_path / "output")
    midi_files = sorted((result.package_path / "MIDI").glob("*.mid"))

    assert len(midi_files) == 1
    assert result.rendered_midi_files == 1
    assert result.transferred_midi_notes == 2
    assert midi_files[0].read_bytes().startswith(b"MThd")


def test_generate_protools_transfer_from_logic_stamps_time_reference_without_offset(tmp_path):
    wav_path = write_test_wav(tmp_path / "audio" / "BASS#01.wav", frames=4410, sample_rate=44100)
    ref = AudioFileRef(
        filename="BASS#01.wav",
        track_name="Bass",
        take_number=1,
        is_comp=False,
        comp_name="",
        file_path=wav_path.resolve(),
        start_position_samples=123_456,
    )
    project = LogicProject(
        name="Logic Demo",
        tempo=120.0,
        time_sig_numerator=4,
        time_sig_denominator=4,
        sample_rate=44100,
        audio_files=[ref],
        plugins=[],
        track_names=["Bass"],
        alternative=0,
    )

    result = generate_protools_transfer_from_logic(project, tmp_path / "output")

    exported = next((result.package_path / "Audio Files").rglob("*.wav"))
    assert _get_bwf_time_reference(exported) == 123_456


def test_generate_protools_transfer_from_logic_creates_track_folders(tmp_path):
    wav_a = write_test_wav(tmp_path / "audio" / "KICK#01.wav", frames=4410, sample_rate=44100)
    wav_b = write_test_wav(tmp_path / "audio" / "VOX#01.wav", frames=4410, sample_rate=44100)
    refs = [
        AudioFileRef(
            filename="KICK#01.wav",
            track_name="Kick",
            take_number=1,
            is_comp=False,
            comp_name="",
            file_path=wav_a.resolve(),
            start_position_samples=0,
        ),
        AudioFileRef(
            filename="VOX#01.wav",
            track_name="Vox",
            take_number=1,
            is_comp=False,
            comp_name="",
            file_path=wav_b.resolve(),
            start_position_samples=44_100,
        ),
    ]
    project = LogicProject(
        name="Two Track Demo",
        tempo=100.0,
        time_sig_numerator=4,
        time_sig_denominator=4,
        sample_rate=44100,
        audio_files=refs,
        plugins=[],
        track_names=["Kick", "Vox"],
        alternative=0,
    )

    result = generate_protools_transfer_from_logic(project, tmp_path / "output")

    audio_root = result.package_path / "Audio Files"
    exported = sorted(audio_root.rglob("*.wav"))
    assert len(exported) == 2
    assert any("Kick" in str(path.parent) for path in exported)
    assert any("Vox" in str(path.parent) for path in exported)
    assert result.copied_audio_files == 2


def test_build_protools_transfer_report_mentions_pro_tools_specifics(tmp_path):
    als_path = create_test_als(tmp_path)
    project = parse_ableton_project(als_path)

    report = build_protools_transfer_report(project)

    assert project.name in report
    assert f"AUDIO TRACKS FOUND ({len(project.audio_tracks)})" in report
    assert "Spot" in report and "Original Time Stamp" in report


def test_build_protools_transfer_report_duck_types_logic_project():
    project = LogicProject(
        name="Logic Report Demo",
        tempo=95.0,
        time_sig_numerator=3,
        time_sig_denominator=4,
        sample_rate=48000,
        audio_files=[],
        plugins=[],
        track_names=["Piano"],
        alternative=0,
    )

    report = build_protools_transfer_report(project)

    assert "Logic Report Demo" in report
    assert "AUDIO TRACKS FOUND (1)" in report
    assert "Sample Rate: 48000" in report


def test_generate_protools_transfer_manifest_targets_protools(tmp_path):
    als_path = create_test_als(tmp_path)
    project = parse_ableton_project(als_path)

    result = generate_protools_transfer(project, tmp_path / "output")
    manifest = json.loads((result.package_path / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["target"] == "protools"
    assert manifest["project_name"] == "Demo Set"
    assert len(manifest["tracks"]) == 2


def test_generate_protools_transfer_import_guide_notes_zero_session_start(tmp_path):
    als_path = create_test_als(tmp_path)
    project = parse_ableton_project(als_path)

    result = generate_protools_transfer(project, tmp_path / "output")
    guide_text = result.artifact_path.read_text(encoding="utf-8")

    assert "00:00:00:00" in guide_text
    assert "Spot" in guide_text


def test_generate_protools_transfer_copies_non_pcm_source_as_reference(tmp_path):
    source = tmp_path / "external" / "loop.mp3"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"ID3-fake-mp3-bytes")

    track = AbletonTrack(
        name="Loop Track",
        clips=[
            AbletonAudioClip(
                clip_name="External Loop",
                track_name="Loop Track",
                source_path=source,
                relative_source_path=None,
                start_beats=2.0,
                end_beats=6.0,
            )
        ],
    )
    project = AbletonProject(
        name="NonPCM Demo",
        tempo=120.0,
        time_sig_numerator=4,
        time_sig_denominator=4,
        audio_tracks=[track],
        locators=[],
    )

    result = generate_protools_transfer(project, tmp_path / "output")
    manifest = json.loads((result.package_path / "manifest.json").read_text(encoding="utf-8"))

    clip_row = manifest["tracks"][0]["clips"][0]
    assert clip_row["export_mode"] == "copied-source"
    exported = result.package_path / "Audio Files" / "01 - Loop Track" / clip_row["export_name"]
    assert exported.exists()
    assert exported.read_bytes() == b"ID3-fake-mp3-bytes"
    assert result.copied_audio_files == 1


def test_generate_protools_transfer_flags_missing_source_as_reference_only(tmp_path):
    track = AbletonTrack(
        name="Ghost Track",
        clips=[
            AbletonAudioClip(
                clip_name="Missing Clip",
                track_name="Ghost Track",
                source_path=tmp_path / "does-not-exist.wav",
                relative_source_path=None,
                start_beats=0.0,
                end_beats=2.0,
                source_issue="missing-file-reference",
            )
        ],
    )
    project = AbletonProject(
        name="Missing Demo",
        tempo=120.0,
        time_sig_numerator=4,
        time_sig_denominator=4,
        audio_tracks=[track],
        locators=[],
    )

    result = generate_protools_transfer(project, tmp_path / "output")
    manifest = json.loads((result.package_path / "manifest.json").read_text(encoding="utf-8"))

    clip_row = manifest["tracks"][0]["clips"][0]
    assert clip_row["export_mode"] == "reference-only"
    assert clip_row["source_issue"] == "missing-file-reference"
    assert result.copied_audio_files == 0
