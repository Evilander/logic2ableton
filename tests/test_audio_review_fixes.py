"""Sample-level checks for float exports and Pro Tools split-mono channels."""

import gzip
import json
import math
import struct
import wave
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from logic2ableton.ableton_generator import generate_als
from logic2ableton.audio import DecodedAudio, read_audio_info
from logic2ableton.logic_transfer import _mix_pcm_frames, _track_render_format, generate_logic_transfer
from logic2ableton.models import AbletonAudioClip, AbletonProject, AbletonTrack, AudioFileRef, LogicProject
from logic2ableton.protools_import import protools_to_ableton_project, protools_to_logic_project, resolve_protools_media
from logic2ableton.protools_parser import ProToolsMidiNote, ProToolsMidiTrack, _deobfuscate, parse_protools_session
from logic2ableton.protools_transfer import generate_protools_transfer, generate_protools_transfer_from_logic
from scripts.fixture_builders import _pt_block, _pt_string, _pt_three_point


def _float_source(path, values, width, *, aifc=False, rate=48000):
    code = "f" if width == 4 else "d"
    frames = struct.pack((">" if aifc else "<") + code * len(values), *values)
    if aifc:
        fraction, exponent = math.frexp(rate)
        extended = struct.pack(">HQ", exponent + 16382, int(fraction * (1 << 64)))
        comm = struct.pack(">HIH", 1, len(values), width * 8) + extended + (b"fl32" if width == 4 else b"fl64")
        payload = b"AIFCCOMM" + struct.pack(">I", len(comm)) + comm
        payload += b"SSND" + struct.pack(">I", len(frames) + 8) + b"\0" * 8 + frames
        path.write_bytes(b"FORM" + struct.pack(">I", len(payload)) + payload)
    else:
        fmt = struct.pack("<HHIIHH", 3, 1, rate, rate * width, width, width * 8)
        payload = b"WAVEfmt " + struct.pack("<I", len(fmt)) + fmt
        payload += b"data" + struct.pack("<I", len(frames)) + frames
        path.write_bytes(b"RIFF" + struct.pack("<I", len(payload)) + payload)
    return path


def _read_wav(path):
    """Read output independently of the converter's header/sample decoder."""
    data = path.read_bytes()
    assert data[:4] == b"RIFF" and data[8:12] == b"WAVE"
    assert int.from_bytes(data[4:8], "little") == len(data) - 8
    chunks = {}
    at = 12
    while at + 8 <= len(data):
        length = int.from_bytes(data[at + 4:at + 8], "little")
        chunks[data[at:at + 4]] = data[at + 8:at + 8 + length]
        at += 8 + length + length % 2
    tag, channels, rate, _, _, bits = struct.unpack_from("<HHIIHH", chunks[b"fmt "])
    if tag == 3:
        code = "f" if bits == 32 else "d"
        values = [value for (value,) in struct.iter_unpack("<" + code, chunks[b"data"])]
        assert struct.unpack("<I", chunks[b"fact"])[0] == len(values) // channels
    else:
        width = bits // 8
        values = [int.from_bytes(chunks[b"data"][i:i + width], "little", signed=True)
                  for i in range(0, len(chunks[b"data"]), width)]
    timestamp = struct.unpack_from("<Q", chunks[b"bext"], 338)[0]
    return tag, channels, rate, bits, values, timestamp


def _project(clips):
    return AbletonProject("Audio", 120, 4, 4, [AbletonTrack("Track", clips)], [])


def _clip(path, *, start=0.0, frames=2, offset=0.0):
    return AbletonAudioClip(path.stem, "Track", path, None, start, start + frames * 2 / 48000,
                            source_in_seconds=offset)


@pytest.mark.parametrize("width", [4, 8])
@pytest.mark.parametrize("aifc", [False, True])
@pytest.mark.parametrize("destination", ["logic", "protools"])
def test_float_clip_exports_preserve_headroom_precision_trims_and_timestamp(tmp_path, width, aifc, destination):
    # 64-bit sources are written as 32-bit float, which Logic and Pro Tools document.
    values = [0.25, 1.25, -1.5, 0.5] if width == 4 else [0.25, 2.0**100, -(2.0**100), 0.5]
    source = _float_source(tmp_path / ("source.aif" if aifc else "source.wav"), values, width, aifc=aifc)
    project = _project([_clip(source, start=10 / 48000, offset=1 / 48000)])
    generate = generate_logic_transfer if destination == "logic" else generate_protools_transfer
    result = generate(project, tmp_path / "out")
    output = next((result.package_path / "Audio Files").rglob("*.wav"))
    tag, channels, rate, bits, actual, stamp = _read_wav(output)
    assert (tag, channels, rate, bits, actual, stamp) == (3, 1, 48000, 32, values[1:3], 5)
    if destination == "logic":
        stem = next((result.package_path / "Track Stems").glob("*.wav"))
        assert _read_wav(stem)[4] == [0.0] * 5 + values[1:3]


def test_logic_to_protools_writes_float64_sources_as_32_bit_float(tmp_path):
    source = _float_source(tmp_path / "source.wav", [1e300, -1e300, 0.25], 8)
    ref = AudioFileRef(source.name, "Track", 0, False, "", source,
                       start_position_samples=123, content_offset_samples=1, content_duration_samples=2)
    project = LogicProject("Float", 120, 4, 4, 48000, [ref], [], ["Track"], 0)
    result = generate_protools_transfer_from_logic(project, tmp_path / "out")
    output = next((result.package_path / "Audio Files").rglob("*.wav"))
    # Past the 32-bit range a sample is held at its limit instead of failing the export.
    assert _read_wav(output) == (3, 1, 48000, 32, [-3.4028234663852886e38, 0.25], 123)


def test_a_float_mix_past_the_32_bit_range_is_held_at_its_limit():
    loud = struct.pack("<ff", 3.4028234663852886e38, -3.4028234663852886e38)
    assert struct.unpack("<ff", _mix_pcm_frames(loud, loud, 4, "float")) == (3.4028234663852886e38, -3.4028234663852886e38)


def test_butted_clips_that_overlap_only_by_rounding_keep_their_integer_format(tmp_path):
    pcm = tmp_path / "integer.wav"
    with wave.open(str(pcm), "wb") as handle:
        handle.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
        handle.writeframes(struct.pack("<h", 1000) * 480)
    first = _clip(pcm, frames=480)
    # The next clip starts where the first ends, one rounding step earlier in beats.
    second = _clip(pcm, start=math.nextafter(first.end_beats, 0.0), frames=480)
    assert second.start_beats < first.end_beats
    track = AbletonTrack("Track", [first, second])
    assert _track_render_format(track, {}, tempo=120) == (48000, 1, 2, "pcm")
    really_overlapping = _clip(pcm, start=first.end_beats - 4 / 48000, frames=480)
    assert _track_render_format(AbletonTrack("Track", [first, really_overlapping]), {}, tempo=120) == (48000, 1, 4, "float")


def test_overlapping_float_stem_retains_sum_above_unity(tmp_path):
    first = _float_source(tmp_path / "first.wav", [1.25, -1.25], 4)
    second = _float_source(tmp_path / "second.wav", [1.5, -1.5], 4)
    result = generate_logic_transfer(_project([_clip(first), _clip(second)]), tmp_path / "out")
    stem = next((result.package_path / "Track Stems").glob("*.wav"))
    assert _read_wav(stem)[:5] == (3, 1, 48000, 32, [2.75, -2.75])


def test_mixed_pcm32_and_float32_stem_uses_correct_sample_encoding(tmp_path):
    pcm = tmp_path / "integer.wav"
    with wave.open(str(pcm), "wb") as handle:
        handle.setparams((1, 4, 48000, 0, "NONE", "not compressed"))
        handle.writeframes(struct.pack("<ii", 2147483647, -2147483648))
    floating = _float_source(tmp_path / "float.wav", [1.25, -1.25], 4)
    result = generate_logic_transfer(_project([_clip(pcm), _clip(floating)]), tmp_path / "out")
    stem = next((result.package_path / "Track Stems").glob("*.wav"))
    # Full-scale PCM32 is 1.0 in a 32-bit float; misread as float bits it would not be.
    assert _read_wav(stem)[:5] == (3, 1, 48000, 32, [2.25, -2.25])


def test_overlapping_pcm_stem_retains_headroom(tmp_path):
    pcm = tmp_path / "integer.wav"
    with wave.open(str(pcm), "wb") as handle:
        handle.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
        handle.writeframes(struct.pack("<hh", 24576, -24576))
    result = generate_logic_transfer(_project([_clip(pcm), _clip(pcm)]), tmp_path / "out")
    stem = next((result.package_path / "Track Stems").glob("*.wav"))
    assert _read_wav(stem)[:5] == (3, 1, 48000, 32, [1.5, -1.5])


def _split_mono_ptx(directory, *, floating=False):
    media = directory / "Audio Files"
    media.mkdir()
    names, metadata, regions, lanes = [], [], [], []
    for index, (side, value) in enumerate((("L", 16000), ("R", -16000))):
        filename = f"Keys.{side}.wav"
        if floating:
            _float_source(media / filename, [value / 8000] * 480, 4)
        else:
            with wave.open(str(media / filename), "wb") as handle:
                handle.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
                handle.writeframes(struct.pack("<h", value) * 480)
        names.append(_pt_string(filename) + b"WAVE" + b"\0" * 5)
        metadata.append(_pt_block(2, 0x1001, b"\0" * 6 + struct.pack("<Q", 480)))
        inner = _pt_block(2, 0x2628, b"\0\0")
        regions.append(_pt_block(2, 0x2629, b"\0" * 9 + _pt_string(f"Keys-01.{side}")
                                 + _pt_three_point(480, 10, 100) + inner + struct.pack("<I", index)))
        placement = _pt_block(2, 0x104F, b"\0\0" + struct.pack("<I", index) + b"\0" + struct.pack("<I", 480))
        entry = _pt_block(2, 0x1050, placement + b"\0" * (45 - len(placement)))
        lanes.append(_pt_block(2, 0x1052, _pt_string("Keys") + entry))
    header = bytes([3]) + b"0010111100101011" + bytes([0, 5, 77])
    plain = header + _pt_block(1, 0x2206, b"\0\0") + _pt_block(1, 0x2067, b"\0" * 18 + struct.pack("<I", 10))
    plain += b"\0" * (4096 - len(plain))
    plain += _pt_block(2, 0x1028, b"\0\0" + struct.pack("<I", 48000))
    plain += _pt_block(1, 0x1004, struct.pack("<I", 2) + _pt_block(2, 0x103A, b"\0" * 9 + b"".join(names))
                       + _pt_block(2, 0x1003, b"".join(metadata)))
    plain += _pt_block(1, 0x262A, struct.pack("<I", 2) + b"".join(regions))
    plain += _pt_block(1, 0x1054, b"\0\0" + b"".join(lanes))
    path = directory / "Split Stereo.ptx"
    path.write_bytes(_deobfuscate(plain))
    return parse_protools_session(path)


@pytest.mark.parametrize("floating", [False, True])
def test_split_mono_logic_exports_retain_stereo_polarity_and_trims(tmp_path, floating):
    session = _split_mono_ptx(tmp_path, floating=floating)
    project = protools_to_ableton_project(session)
    assert [track.name for track in project.audio_tracks] == ["Keys.L", "Keys.R"]
    result = generate_logic_transfer(project, tmp_path / "out")
    for index, stem in enumerate(sorted((result.package_path / "Track Stems").glob("*.wav"))):
        tag, channels, _, _, samples, stamp = _read_wav(stem)
        value = (2.0 if floating else 16000) * (1 if index == 0 else -1)
        pair = [value, 0] if index == 0 else [0, value]
        assert tag == (3 if floating else 1)
        assert channels == 2 and stamp == 0
        assert samples == [0] * 960 + pair * 100
    for index, clip in enumerate(sorted((result.package_path / "Audio Files").rglob("*.wav"))):
        values = _read_wav(clip)
        value = (2.0 if floating else 16000) * (1 if index == 0 else -1)
        assert values[1] == 2 and values[5] == 480
        assert values[4] == ([value, 0] if index == 0 else [0, value]) * 100


@pytest.mark.parametrize("copy_audio", [False, True])
def test_split_mono_live_tracks_are_separate_and_hard_panned(tmp_path, copy_audio):
    session = _split_mono_ptx(tmp_path)
    media = resolve_protools_media(session)
    project = protools_to_logic_project(session, preflight=media)
    als = generate_als(project, tmp_path / "out", copy_audio=copy_audio)
    root = ET.fromstring(gzip.decompress(als.read_bytes()))
    tracks = root.findall(".//Tracks/AudioTrack")
    assert len(tracks) == 2
    for channel, track in enumerate(tracks):
        assert float(track.find("DeviceChain/Mixer/Pan/Manual").get("Value")) == (-1, 1)[channel]
        clips = track.findall(".//AudioClip")
        assert len(clips) == 1
        clip = clips[0]
        source = Path(clip.find("SampleRef/FileRef/Path").get("Value"))
        assert source.is_file()
        audio = DecodedAudio(source, read_audio_info(source))
        assert struct.unpack("<h", audio.read_frames(0, 1))[0] == (16000, -16000)[channel]
        assert float(clip.find("CurrentStart").get("Value")) == 0.02


@pytest.mark.parametrize("channels", [1, 2])
def test_same_source_channel_lanes_merge_only_for_interleaved_audio(tmp_path, channels):
    session = _split_mono_ptx(tmp_path)
    filename = session.tracks[0].regions[0].filename
    session.tracks[1].regions[0].filename = filename
    with wave.open(str(tmp_path / "Audio Files" / filename), "wb") as handle:
        handle.setparams((channels, 2, 48000, 0, "NONE", "not compressed"))
        handle.writeframes(struct.pack("<h", 16000) * 480 * channels)
    project = protools_to_logic_project(session)
    assert project.track_names == (["Keys.L", "Keys.R"] if channels == 1 else ["Keys"])
    assert len(project.audio_files) == (2 if channels == 1 else 1)
    assert bool(project.mixer_state) == (channels == 1)


def test_a_mono_track_playing_one_half_of_a_split_pair_stays_centred(tmp_path):
    session = _split_mono_ptx(tmp_path)
    session.tracks = session.tracks[:1]
    project = protools_to_logic_project(session)
    assert project.track_names == ["Keys"]
    assert not project.mixer_state
    assert [clip.output_channel for track in protools_to_ableton_project(session).audio_tracks
            for clip in track.clips] == [None]


@pytest.mark.parametrize("midi_name", ["Keys.L", "Keys.R"])
def test_split_mono_names_do_not_apply_audio_pan_to_existing_midi_track(tmp_path, midi_name):
    session = _split_mono_ptx(tmp_path)
    session.midi_tracks = [ProToolsMidiTrack(midi_name, [ProToolsMidiNote(60, 0, 1, 100)])]
    project = protools_to_logic_project(session)
    als = generate_als(project, tmp_path / "out")
    root = ET.fromstring(gzip.decompress(als.read_bytes()))
    audio_tracks = root.findall(".//Tracks/AudioTrack")
    assert len(audio_tracks) == 2
    assert [float(track.find("DeviceChain/Mixer/Pan/Manual").get("Value"))
            for track in audio_tracks] == [-1.0, 1.0]
    assert midi_name not in [track.find("Name/EffectiveName").get("Value") for track in audio_tracks]
    midi_track = root.find(".//Tracks/MidiTrack")
    assert midi_track.find("Name/EffectiveName").get("Value") == midi_name
    assert float(midi_track.find("DeviceChain/Mixer/Pan/Manual").get("Value")) == 0.0


@pytest.mark.parametrize("destination", ["logic", "protools"])
@pytest.mark.parametrize("copy_audio", [False, True])
def test_split_mono_reference_exports_keep_channel_recovery_metadata(tmp_path, destination, copy_audio):
    session = _split_mono_ptx(tmp_path)
    for filename in ("Keys.L.wav", "Keys.R.wav"):
        (tmp_path / "Audio Files" / filename).write_bytes(b"not decodable")
    project = protools_to_ableton_project(session)
    generate = generate_logic_transfer if destination == "logic" else generate_protools_transfer
    result = generate(project, tmp_path / "out", copy_audio=copy_audio)
    manifest_name = "timeline_manifest.json" if destination == "logic" else "manifest.json"
    manifest = json.loads((result.package_path / manifest_name).read_text(encoding="utf-8"))
    clips = [track["clips"][0] for track in manifest["tracks"]]
    assert [clip["output_channel"] for clip in clips] == [0, 1]
    assert {clip["export_mode"] for clip in clips} == {"copied-source" if copy_audio else "reference-only"}
    if copy_audio:
        warnings = manifest["compatibility_warnings"]
        assert any("Pan this mono source fully left." in warning for warning in warnings)
        assert any("Pan this mono source fully right." in warning for warning in warnings)
