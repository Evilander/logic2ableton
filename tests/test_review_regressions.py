"""Conversion/content and filesystem regressions from the full repository review."""

import gzip
import json
import plistlib
import struct
import wave
import xml.etree.ElementTree as ET
from dataclasses import replace
from pathlib import Path

import pytest

from logic2ableton.ableton_generator import _BUNDLED_TEMPLATE, generate_als
from logic2ableton.ableton_parser import parse_ableton_project
from logic2ableton.audio import BLOCK_FRAMES, DecodedAudio, read_audio_info
from logic2ableton.cli import _detect_mode, _report_path
from logic2ableton.logic_parser import _MIDI_NOTE_SIGNATURE, extract_midi_notes, parse_logic_project
from logic2ableton.logic_transfer import generate_logic_transfer
from logic2ableton.models import AbletonAudioClip, AbletonProject, AbletonTrack, AudioFileRef, LogicProject
from logic2ableton.protools_transfer import generate_protools_transfer, generate_protools_transfer_from_logic
from logic2ableton.vst3_scanner import scan_vst3_plugins
from scripts.fixture_builders import build_logic_project_data, build_synthetic_logicx, build_synthetic_ptx


def pcm(path, samples, *, rate=100, width=2):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setparams((1, width, rate, 0, "NONE", "not compressed"))
        handle.writeframes(bytes(samples) if width == 1 else struct.pack("<" + "h" * len(samples), *samples))
    return path


def logic(source=None, **kwargs):
    refs = [] if source is None else [AudioFileRef(source.name, "Audio", 0, False, "", source)]
    return LogicProject(**dict(dict(name="Session", tempo=120, time_sig_numerator=4,
        time_sig_denominator=4, sample_rate=100, audio_files=refs, plugins=[],
        track_names=["Audio"] if refs else [], alternative=0), **kwargs))


def live(source=None, **kwargs):
    tracks = [] if source is None else [AbletonTrack("Audio", [AbletonAudioClip(
        "Clip", "Audio", source, None, 0, 2)])]
    return AbletonProject(**dict(dict(name="Session", tempo=120, time_sig_numerator=4,
        time_sig_denominator=4, audio_tracks=tracks, locators=[]), **kwargs))


def xml(path):
    return ET.fromstring(gzip.decompress(path.read_bytes()))


def save_xml(path, root):
    path.write_bytes(gzip.compress(ET.tostring(root)))


@pytest.mark.parametrize("name", ["../escaped", "/absolute", "C:\\outside\\project", "..", "CON"])
def test_all_exports_and_reports_stay_in_selected_output(tmp_path, name):
    out = tmp_path / "chosen"
    out.mkdir()
    outputs = [generate_als(logic(name=name), out),
               generate_logic_transfer(live(name=name), out).package_path,
               generate_protools_transfer(live(name=name), out).package_path,
               generate_protools_transfer_from_logic(logic(name=name), out).package_path,
               _report_path(out, Path("source.als"), project_name=name)]
    assert all(path.resolve().is_relative_to(out.resolve()) for path in outputs)
    assert sorted(path.name for path in tmp_path.iterdir()) == ["chosen"]


def test_report_symlink_cannot_redirect_write(tmp_path):
    out = tmp_path / "output"
    out.mkdir()
    outside = tmp_path / "unrelated.txt"
    outside.write_text("keep")
    try:
        (out / "Session_conversion_report.txt").symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(ValueError, match="escapes"):
        _report_path(out, Path("source.als"), project_name="Session")
    assert outside.read_text() == "keep"


@pytest.mark.parametrize("track_tag", ["MainTrack", "MasterTrack"])
def test_real_template_roundtrip_preserves_global_tempo_and_meter(tmp_path, track_tag):
    root = xml(_BUNDLED_TEMPLATE)
    root.find("LiveSet/MainTrack").tag = track_tag
    template = tmp_path / "template.als"
    save_xml(template, root)
    source = pcm(tmp_path / "source.wav", [1000] * 100)
    project = logic(source, tempo=90, time_sig_numerator=7, time_sig_denominator=8)
    project.audio_files[0].start_position_samples = 1000
    result = generate_als(project, tmp_path / "out", template_path=template)
    rendered = xml(result)
    main = rendered.find(f"LiveSet/{track_tag}")
    assert main.find("DeviceChain/Mixer/TimeSignature/Manual").get("Value") == "303"
    assert main.find(".//EnumEvent").get("Value") == "303"
    assert main.find(".//FloatEvent").get("Value") == "90"
    parsed = parse_ableton_project(result)
    assert (parsed.tempo, parsed.time_sig_numerator, parsed.time_sig_denominator) == (90, 7, 8)
    assert parsed.clips[0].start_beats * 60 / parsed.tempo == 10


@pytest.mark.parametrize("absolute", [True, False])
def test_external_references_are_blocked_before_audio_copy(tmp_path, absolute):
    source = pcm(tmp_path / "source.wav", [1000] * 100)
    result = generate_als(logic(source), tmp_path / "out")
    root = xml(result)
    ref = root.find(".//AudioClip/SampleRef/FileRef")
    external = tmp_path / "private.txt"
    external.write_text("do not export this")
    ref.find("Path").set("Value", str(external) if absolute else "")
    ref.find("RelativePath").set("Value", "" if absolute else "../../private.txt")
    save_xml(result, root)
    parsed = parse_ableton_project(result)
    assert parsed.clips[0].source_path is None
    assert parsed.clips[0].source_issue == "external-media-blocked"
    for transfer in (generate_logic_transfer, generate_protools_transfer):
        output = transfer(parsed, tmp_path / "transfers")
        assert output.copied_audio_files == 0
        assert not list((output.package_path / "Audio Files").rglob("*.txt"))


def test_unwarped_trim_exports_the_selected_samples(tmp_path):
    source = pcm(tmp_path / "source.wav", [1000] * 100 + [2000] * 100)
    result = generate_als(logic(source), tmp_path / "out")
    root = xml(result)
    clip = root.find(".//AudioClip")
    for tag, value in [("IsWarped", "false"), ("CurrentEnd", "2"),
                       ("Loop/LoopStart", "1"), ("Loop/LoopEnd", "2"), ("Loop/StartRelative", "0")]:
        clip.find(tag).set("Value", value)
    save_xml(result, root)
    for generate in (generate_logic_transfer, generate_protools_transfer):
        transfer = generate(parse_ableton_project(result), tmp_path / "transfers")
        exported = next((transfer.package_path / "Audio Files").rglob("*.wav"))
        with wave.open(str(exported)) as handle:
            assert handle.readframes(100) == struct.pack("<h", 2000) * 100


def test_trimmed_regions_preserve_source_warp_map_and_offset(tmp_path):
    source = pcm(tmp_path / "source.wav", [1000] * 200 + [2000] * 200 + [3000] * 600)
    project = logic(source)
    project.audio_files[0] = replace(project.audio_files[0], start_position_samples=1000,
        content_offset_samples=200, content_duration_samples=200, timeline_sample_rate=100)
    result = generate_als(project, tmp_path / "out")
    clip = xml(result).find(".//AudioClip")
    assert clip.find("Loop/LoopStart").get("Value") == "4"
    assert clip.find("Loop/StartRelative").get("Value") == "0"
    marker = clip.findall("WarpMarkers/WarpMarker")[-1]
    assert (float(marker.get("SecTime")), float(marker.get("BeatTime"))) == (10, 20)
    parsed = parse_ableton_project(result)
    assert parsed.clips[0].source_in_seconds == 2
    transfer = generate_logic_transfer(parsed, tmp_path / "transfers")
    exported = next((transfer.package_path / "Audio Files").rglob("*.wav"))
    with wave.open(str(exported)) as handle:
        assert handle.readframes(200) == struct.pack("<h", 2000) * 200


def test_overlapping_placed_regions_keep_their_nonoverlapping_tails(tmp_path):
    source = pcm(tmp_path / "source.wav", [1000] * 500)
    project = logic(source)
    project.audio_files = [replace(project.audio_files[0], start_position_samples=start,
        content_duration_samples=200, clip_name=f"edit {index}", timeline_sample_rate=100)
        for index, start in enumerate((0, 190, 380))]
    clips = xml(generate_als(project, tmp_path / "out")).findall(".//AudioClip")
    assert [float(clip.find("CurrentStart").get("Value")) for clip in clips] == [0, 3.8, 7.6]
    assert max(float(clip.find("CurrentEnd").get("Value")) for clip in clips) == 11.6


def test_float_wav_duration_and_rendered_content(tmp_path):
    rate = 48000
    samples = struct.pack("<f", 0.25) * (rate * 10)
    fmt = struct.pack("<HHIIHH", 3, 1, rate, rate * 4, 4, 32)
    payload = b"WAVEfmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(samples)) + samples
    source = tmp_path / "float.wav"
    source.write_bytes(b"RIFF" + struct.pack("<I", len(payload)) + payload)
    result = generate_als(logic(source), tmp_path / "out")
    clip = xml(result).find(".//AudioClip")
    assert float(clip.find("CurrentEnd").get("Value")) == 20
    assert int(clip.find("SampleRef/DefaultSampleRate").get("Value")) == rate
    transfer = generate_protools_transfer(parse_ableton_project(result), tmp_path / "transfers")
    with wave.open(str(next((transfer.package_path / "Audio Files").rglob("*.wav")))) as handle:
        assert handle.getnframes() == rate * 10
        assert handle.getsampwidth() == 4
        assert handle.readframes(1) == struct.pack("<i", 536870912)


def test_unknown_duration_is_reported_instead_of_four_beat_clip(tmp_path):
    source = tmp_path / "broken.mp3"
    source.write_bytes(b"unreadable audio")
    project = logic(source)
    assert not xml(generate_als(project, tmp_path / "out")).findall(".//AudioClip")
    assert any("Skipped broken.mp3" in warning for warning in project.compatibility_warnings)


def test_no_copy_references_existing_original_media(tmp_path):
    source = pcm(tmp_path / "source.wav", [1000] * 100)
    result = generate_als(logic(source), tmp_path / "out", copy_audio=False)
    ref = xml(result).find(".//AudioClip/SampleRef/FileRef")
    assert Path(ref.find("Path").get("Value")) == source.resolve()
    assert ref.find("RelativePath").get("Value") == ""
    assert not (result.parent / "Samples").exists()


@pytest.mark.parametrize("generate", [generate_logic_transfer, generate_protools_transfer])
def test_repeat_exports_get_fresh_packages(tmp_path, generate):
    source = pcm(tmp_path / "source.wav", [1000] * 100)
    first = generate(live(source), tmp_path / "out")
    second = generate(live(), tmp_path / "out")
    assert first.package_path != second.package_path
    assert list((first.package_path / "Audio Files").rglob("*.wav"))
    assert not list((second.package_path / "Audio Files").rglob("*.wav"))


def test_unsigned_silence_and_padding_remain_silent(tmp_path):
    source = pcm(tmp_path / "source.wav", [128] * 50, width=1)
    project = live(source)
    project.clips[0].start_beats = 1
    project.clips[0].end_beats = 3
    transfer = generate_logic_transfer(project, tmp_path / "out")
    for exported in transfer.package_path.rglob("*.wav"):
        with wave.open(str(exported)) as handle:
            assert set(handle.readframes(handle.getnframes())) == {128}


def test_render_reads_bounded_blocks_and_preserves_boundary_samples(tmp_path, monkeypatch):
    source = pcm(tmp_path / "source.wav", [1200] * (BLOCK_FRAMES * 3 + 10), rate=48000)
    project = live(source)
    project.clips[0].end_beats = read_audio_info(source).frame_count / 48000 * 2
    requested = []
    original = DecodedAudio.read_frames

    def read(self, start, count):
        requested.append(count)
        return original(self, start, count)

    monkeypatch.setattr(DecodedAudio, "read_frames", read)
    transfer = generate_logic_transfer(project, tmp_path / "out")
    assert max(requested) <= BLOCK_FRAMES
    with wave.open(str(next((transfer.package_path / "Track Stems").glob("*.wav")))) as handle:
        handle.setpos(BLOCK_FRAMES - 1)
        assert handle.readframes(3) == struct.pack("<h", 1200) * 3


@pytest.mark.parametrize("membership, expected", [(True, ["Vocal#01.wav"]), (False, ["Vocal#01.wav"])])
def test_logic_uses_selected_alternative_audio_membership(tmp_path, membership, expected):
    bundle = tmp_path / "Session.logicx"
    (bundle / "Resources").mkdir(parents=True)
    (bundle / "Resources/ProjectInformation.plist").write_bytes(plistlib.dumps({
        "VariantNames": {"0": "old", "2": "active"}, "ActiveVariant": 2}))
    alternative = bundle / "Alternatives/002"
    alternative.mkdir(parents=True)
    metadata = {"UnusedAudioFiles": ["Audio Files/Vocal#02.wav"]}
    if membership:
        metadata["AudioFiles"] = ["Audio Files/Vocal#01.wav"]
    (alternative / "MetaData.plist").write_bytes(plistlib.dumps(metadata))
    for name in ("Vocal#01.wav", "Vocal#02.wav"):
        pcm(bundle / "Media/Audio Files" / name, [1000] * 100)
    project = parse_logic_project(bundle)
    assert [ref.filename for ref in project.audio_files] == expected
    assert project.name == "active"
    clips = xml(generate_als(project, tmp_path / "out")).findall(".//AudioClip")
    assert [clip.find("Name").get("Value") for clip in clips] == ["Vocal#01"]


def test_truncated_logic_midi_record_is_skipped(tmp_path):
    assert extract_midi_notes(tmp_path, _data=b"x" * 32 + _MIDI_NOTE_SIGNATURE + b"\0\0") == []


@pytest.mark.parametrize("program,args,expected", [
    ("logic2ableton", ["demo.als"], "ableton2logic"),
    ("logic2ableton.exe", ["--output", "folder", "demo.ptx"], "protools2ableton"),
    ("logic2ableton", ["--output=folder", "demo.logicx"], "logic2ableton"),
    ("ableton2protools", ["demo.als"], "ableton2protools"),
])
def test_installed_launcher_autodetects_input(program, args, expected):
    assert _detect_mode(program, args) == expected


def test_vst3_scan_descends_vendor_folders_and_stops_at_bundles(tmp_path):
    (tmp_path / "readme.txt").write_text("not a plugin")
    bundle = tmp_path / "Vendor/Test EQ.vst3"
    (bundle / "Contents").mkdir(parents=True)
    (bundle / "Contents/Private.vst3").touch()
    (tmp_path / "Vendor/Synth.VST3").touch()
    assert {plugin.name for plugin in scan_vst3_plugins(tmp_path)} == {"Test EQ", "Synth"}


@pytest.mark.parametrize("mode", ["logic2ableton", "protools2ableton", "protools2logic"])
def test_completion_reports_failed_report_write_once(tmp_path, monkeypatch, capsys, mode):
    from logic2ableton import cli

    source = (build_synthetic_logicx(tmp_path, project_data=build_logic_project_data([[(60, 100, 38400, 960)]]))
              if mode == "logic2ableton" else build_synthetic_ptx(tmp_path))

    def fail_write(*args):
        raise OSError("read-only report destination")

    monkeypatch.setattr(cli, "_write_report", fail_write)
    assert cli.main([mode, str(source), "--output", str(tmp_path / "out"), "--json-progress"]) == 0
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    completed = [event for event in events if event["stage"] == "complete"]
    assert len(completed) == 1
    assert "read-only report destination" in completed[0]["report"]
    assert any("report could not be written" in warning for warning in completed[0]["compatibility_warnings"])


def test_final_report_includes_generation_time_warnings_and_actual_clip_count(tmp_path, capsys):
    from logic2ableton.cli import main

    bundle = build_synthetic_logicx(tmp_path, project_data=b"")
    source = bundle / "Media/Audio Files/unsupported.mp3"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"unknown duration")
    assert main(["logic2ableton", str(bundle), "--output", str(tmp_path / "out"), "--json-progress"]) == 0
    payload = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert payload["clips"] == 0
    assert payload["audio_files"] == 0
    assert "Skipped unsupported.mp3" in payload["report"]
    assert payload["report"] == Path(payload["report_path"]).read_text()


def test_midi_export_failure_preserves_successful_files_and_warns(tmp_path, monkeypatch, capsys):
    from logic2ableton.cli import main

    bundle = build_synthetic_logicx(tmp_path, project_data=build_logic_project_data([
        [(60, 100, 38400, 960)], [(72, 100, 38400, 960)]]))
    write_bytes = Path.write_bytes

    def write(path, data):
        if path.suffix == ".mid" and path.name.startswith("01"):
            raise OSError("simulated MIDI write failure")
        return write_bytes(path, data)

    monkeypatch.setattr(Path, "write_bytes", write)
    assert main(["logic2ableton", str(bundle), "--output", str(tmp_path / "out"), "--json-progress"]) == 0
    payload = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert payload["midi_tracks"] == 1
    assert "simulated MIDI write failure" in payload["report"]
    assert len(list((Path(payload["als_path"]).parent / "MIDI").glob("*.mid"))) == 1


@pytest.mark.parametrize("tempo", ["", "nan", "inf", "0", "-1"])
def test_cli_rejects_invalid_tempo(tmp_path, tempo):
    from logic2ableton.cli import main

    with pytest.raises(SystemExit) as exc:
        main(["protools2ableton", "session.ptx", "--tempo", tempo])
    assert exc.value.code == 2


def test_truncated_ptx_returns_a_parse_error(tmp_path):
    from logic2ableton.protools_parser import ProToolsParseError, parse_protools_session

    session = build_synthetic_ptx(tmp_path)
    session.write_bytes(session.read_bytes()[:30])
    with pytest.raises(ProToolsParseError):
        parse_protools_session(session)


def test_protools_external_media_cannot_enter_transfer_models(tmp_path):
    from logic2ableton.protools_import import protools_to_ableton_project, protools_to_logic_project
    from logic2ableton.protools_parser import ProToolsRegion, ProToolsSession, ProToolsTrack

    outside = pcm(tmp_path / "outside.wav", [1000] * 100)
    session = ProToolsSession("Session", tmp_path / "Session.ptx", 12, 48000, [], [
        ProToolsTrack("Audio", 0, [ProToolsRegion("Clip", 0, 0, 0, 100, 0, str(outside))])], [])
    assert not protools_to_logic_project(session).audio_files
    assert not protools_to_ableton_project(session).clips


def test_logic_wav_without_odd_byte_padding_keeps_duration_and_timestamp(tmp_path):
    from logic2ableton.audio import build_bext_chunk
    from logic2ableton.logic_parser import _get_bwf_time_reference

    source = tmp_path / "logic.wav"
    fmt = struct.pack("<HHIIHH", 1, 1, 48000, 48000 * 3, 3, 24)
    timestamp = 48000 * 3600 + 321
    bext = build_bext_chunk(timestamp)
    # Matches the unpadded odd-sized data chunk in the local Logic recordings.
    payload = (b"WAVEfmt " + struct.pack("<I", len(fmt)) + fmt
               + b"data" + struct.pack("<I", 303) + b"\x01\0\0" * 101
               + b"bext" + struct.pack("<I", len(bext)) + bext)
    source.write_bytes(b"RIFF" + struct.pack("<I", len(payload)) + payload)
    assert read_audio_info(source).frame_count == 101
    assert _get_bwf_time_reference(source) == timestamp


@pytest.mark.parametrize("offset,duration", [(100, None), (0, 0), (0, -1)])
def test_empty_audio_regions_do_not_abort_other_tracks(tmp_path, offset, duration):
    source = pcm(tmp_path / "source.wav", [1000] * 100)
    project = logic(source)
    project.audio_files.append(replace(project.audio_files[0], content_offset_samples=offset,
        content_duration_samples=duration, clip_name="empty"))
    result = generate_als(project, tmp_path / "out")
    assert len(xml(result).findall(".//AudioClip")) == 1
    assert any("Skipped" in warning for warning in project.compatibility_warnings)


def test_single_warp_marker_anchors_the_source_trim(tmp_path):
    source = pcm(tmp_path / "source.wav", [1000] * 1000)
    result = generate_als(logic(source), tmp_path / "out")
    root = xml(result)
    clip = root.find(".//AudioClip")
    markers = clip.find("WarpMarkers")
    markers.remove(markers[-1])
    markers[0].set("SecTime", "3")
    markers[0].set("BeatTime", "2")
    clip.find("Loop/LoopStart").set("Value", "4")
    save_xml(result, root)
    assert parse_ableton_project(result).clips[0].source_in_seconds == 4


def test_logic_comp_wav_with_counted_pad_byte_preserves_all_complete_frames(tmp_path):
    source = tmp_path / "comp.wav"
    fmt = struct.pack("<HHIIHH", 1, 1, 48000, 48000 * 3, 3, 24)
    payload = (b"WAVEfmt " + struct.pack("<I", len(fmt)) + fmt
               + b"data" + struct.pack("<I", 304) + b"\x01\0\0" * 101 + b"\0")
    source.write_bytes(b"RIFF" + struct.pack("<I", len(payload)) + payload)
    info = read_audio_info(source)
    assert info.frame_count == 101
    assert DecodedAudio(source, info).frames == b"\x01\0\0" * 101


def test_float64_audio_saturates_without_arithmetic_overflow(tmp_path):
    source = tmp_path / "float64.wav"
    fmt = struct.pack("<HHIIHH", 3, 1, 48000, 48000 * 8, 8, 64)
    samples = struct.pack("<ddd", 1e300, -1e300, 0.25)
    payload = b"WAVEfmt " + struct.pack("<I", 16) + fmt + b"data" + struct.pack("<I", len(samples)) + samples
    source.write_bytes(b"RIFF" + struct.pack("<I", len(payload)) + payload)
    decoded = DecodedAudio(source, read_audio_info(source))
    assert struct.unpack("<iii", decoded.frames) == (2147483647, -2147483648, 536870912)
