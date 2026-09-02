"""Synthetic DAW fixtures shared by tests and packaged-binary smoke checks."""

from __future__ import annotations

import gzip
import plistlib
import struct
import wave
import xml.etree.ElementTree as ET
from pathlib import Path

from logic2ableton.logic_parser import _MIDI_NOTE_SIGNATURE
from logic2ableton.protools_parser import (
    _PT_ZERO_TICKS,
    PT_TICKS_PER_QUARTER,
    _deobfuscate,
)


def write_test_wav(
    path: Path,
    *,
    frames: int = 44_100,
    sample_rate: int = 44_100,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(b"\x00\x00" * frames)
    return path


def create_sample_als(als_path: Path) -> Path:
    """Create a tiny Ableton set with one unwarped audio clip."""
    project_dir = als_path.parent
    samples_dir = project_dir / "Samples" / "Imported"
    sample_path = write_test_wav(samples_dir / "kick.wav")

    root = ET.Element("Ableton")
    live_set = ET.SubElement(root, "LiveSet")
    transport = ET.SubElement(live_set, "Transport")
    tempo = ET.SubElement(ET.SubElement(transport, "Tempo"), "Manual")
    tempo.set("Value", "128")
    time_signatures = ET.SubElement(transport, "TimeSignatures")
    time_signature = ET.SubElement(time_signatures, "RemoteableTimeSignature")
    ET.SubElement(time_signature, "Numerator").set("Value", "4")
    ET.SubElement(time_signature, "Denominator").set("Value", "4")

    locators = ET.SubElement(ET.SubElement(live_set, "Locators"), "Locators")
    locator = ET.SubElement(locators, "Locator")
    ET.SubElement(locator, "Name").set("Value", "Verse")
    ET.SubElement(locator, "Time").set("Value", "17")

    tracks = ET.SubElement(live_set, "Tracks")
    drums = ET.SubElement(tracks, "AudioTrack")
    ET.SubElement(ET.SubElement(drums, "Name"), "EffectiveName").set("Value", "Drums")
    device_chain = ET.SubElement(drums, "DeviceChain")
    main_sequencer = ET.SubElement(device_chain, "MainSequencer")
    sample = ET.SubElement(main_sequencer, "Sample")
    arranger = ET.SubElement(sample, "ArrangerAutomation")
    events = ET.SubElement(arranger, "Events")
    clip = ET.SubElement(events, "AudioClip")
    clip.set("Time", "1")
    ET.SubElement(clip, "CurrentStart").set("Value", "1")
    ET.SubElement(clip, "CurrentEnd").set("Value", "5")
    ET.SubElement(clip, "Name").set("Value", "Kick Loop")
    loop = ET.SubElement(clip, "Loop")
    ET.SubElement(loop, "StartRelative").set("Value", "0")
    ET.SubElement(clip, "IsWarped").set("Value", "false")
    ET.SubElement(clip, "Disabled").set("Value", "false")
    sample_ref = ET.SubElement(clip, "SampleRef")
    file_ref = ET.SubElement(sample_ref, "FileRef")
    ET.SubElement(file_ref, "Path").set("Value", str(sample_path.resolve()))
    ET.SubElement(file_ref, "RelativePath").set("Value", "Samples/Imported/kick.wav")
    ET.SubElement(sample_ref, "DefaultDuration").set("Value", "44100")
    ET.SubElement(sample_ref, "DefaultSampleRate").set("Value", "44100")

    als_path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(als_path, "wb") as handle:
        handle.write(ET.tostring(root, encoding="utf-8", xml_declaration=True))
    return als_path


def _logic_note_record(
    pitch: int,
    velocity: int,
    position_ticks: int,
    duration_ticks: int,
) -> bytes:
    return (
        struct.pack("<I", position_ticks)
        + b"\x00\x00\x00"
        + bytes([velocity, pitch])
        + _MIDI_NOTE_SIGNATURE
        + struct.pack("<I", duration_ticks)
    )


def build_logic_project_data(
    sequences: list[list[tuple[int, int, int, int]]],
) -> bytes:
    blob = b"HEADERPAD" * 4
    for sequence in sequences:
        blob += b"qSvE" + b"\x00" * 8
        for pitch, velocity, position, duration in sequence:
            blob += _logic_note_record(pitch, velocity, position, duration)
        blob += b"\xf1\x00\x00\x00" + b"\x00" * 8
    return blob


def build_synthetic_logicx(
    output_dir: Path,
    *,
    project_data: bytes,
    sampler_files: list[str] | None = None,
) -> Path:
    """Create a minimal Logic bundle containing supplied ProjectData."""
    logicx_path = output_dir / "Synth.logicx"
    resources = logicx_path / "Resources"
    alternative = logicx_path / "Alternatives" / "000"
    resources.mkdir(parents=True)
    alternative.mkdir(parents=True)
    with open(resources / "ProjectInformation.plist", "wb") as handle:
        plistlib.dump({"VariantNames": {"0": "Synth"}, "ActiveVariant": 0}, handle)
    metadata = {
        "BeatsPerMinute": 120.0,
        "SampleRate": 44_100,
        "NumberOfTracks": 0,
        "SamplerInstrumentsFiles": sampler_files or [],
    }
    with open(alternative / "MetaData.plist", "wb") as handle:
        plistlib.dump(metadata, handle)
    (alternative / "ProjectData").write_bytes(project_data)
    return logicx_path


def _pt_block(block_type: int, content_type: int, payload: bytes) -> bytes:
    return (
        b"\x5a"
        + struct.pack("<H", block_type)
        + struct.pack("<I", len(payload) + 2)
        + struct.pack("<H", content_type)
        + payload
    )


def _pt_string(value: str) -> bytes:
    encoded = value.encode("utf-8")
    return struct.pack("<I", len(encoded)) + encoded


def _pt_three_point(start: int, offset: int, length: int) -> bytes:
    return bytes([0, 0x40, 0x40, 0x40, 0]) + struct.pack("<III", offset, length, start)


def _pt_midi_event(
    position_ticks: int,
    note: int,
    length_ticks: int,
    velocity: int,
) -> bytes:
    event = bytearray(35)
    event[0:5] = position_ticks.to_bytes(5, "little")
    event[8] = note
    event[9:14] = length_ticks.to_bytes(5, "little")
    event[17] = velocity
    return bytes(event)


def build_synthetic_ptx(
    output_dir: Path,
    *,
    sample_rate: int = 48_000,
    wav_name: str = "Guitar.wav",
    wav_frames: int = 44_100,
    region: tuple[int, int, int] = (96_000, 1_000, 22_050),
    track_name: str = "Guitar",
    midi: bool = True,
) -> Path:
    """Create a small obfuscated PTX session from the documented block layout."""
    start, offset, length = region

    header = bytes([0x03]) + b"0010111100101011" + bytes([0x00, 0x05, 77])
    first = _pt_block(1, 0x2206, b"\x00\x00")
    version = _pt_block(1, 0x2067, b"\x00" * 18 + struct.pack("<I", 10))
    rate = _pt_block(2, 0x1028, b"\x00\x00" + struct.pack("<I", sample_rate))

    wav_entry = _pt_string(wav_name) + b"WAVE" + b"\x00" * 5
    wav_names = _pt_block(2, 0x103A, b"\x00" * 9 + wav_entry)
    wav_meta = _pt_block(
        2,
        0x1003,
        _pt_block(2, 0x1001, b"\x00" * 6 + struct.pack("<Q", wav_frames)),
    )
    wav_list = _pt_block(1, 0x1004, struct.pack("<I", 1) + wav_names + wav_meta)

    inner = _pt_block(2, 0x2628, b"\x00\x00")
    region_entry = _pt_block(
        2,
        0x2629,
        b"\x00" * 9
        + _pt_string("Guitar-01")
        + _pt_three_point(start, offset, length)
        + inner
        + struct.pack("<I", 0),
    )
    region_list = _pt_block(1, 0x262A, struct.pack("<I", 1) + region_entry)

    placement = _pt_block(
        2,
        0x104F,
        b"\x00\x00" + struct.pack("<I", 0) + b"\x00" + struct.pack("<I", start),
    )
    lane_entry_payload = bytearray(placement)
    lane_entry_payload += b"\x00" * (45 - len(lane_entry_payload))
    lane_entry = _pt_block(2, 0x1050, bytes(lane_entry_payload))
    lane = _pt_block(2, 0x1052, _pt_string(track_name) + lane_entry)
    track_map = _pt_block(1, 0x1054, b"\x00\x00" + lane)
    parts = [rate, wav_list, region_list, track_map]

    if midi:
        note_region_ticks = 4 * PT_TICKS_PER_QUARTER
        zero = 500_000_000
        events = _pt_midi_event(
            zero,
            60,
            2 * PT_TICKS_PER_QUARTER,
            100,
        ) + _pt_midi_event(
            zero + 2 * PT_TICKS_PER_QUARTER,
            64,
            PT_TICKS_PER_QUARTER,
            90,
        )
        midi_block = _pt_block(
            1,
            0x2000,
            b"MdNLB" + b"\x00" * 6 + struct.pack("<I", 2) + events,
        )
        midi_names = _pt_block(
            1,
            0x2519,
            _pt_block(2, 0x251A, b"\x00\x00" + _pt_string("Synth")),
        )
        midi_region_entry = _pt_block(2, 0x2628, b"\x00\x00")
        midi_regions = _pt_block(
            1,
            0x2634,
            _pt_block(2, 0x2633, midi_region_entry + struct.pack("<I", 0)),
        )
        midi_placement = _pt_block(
            2,
            0x104F,
            b"\x00\x00"
            + struct.pack("<I", 0)
            + b"\x00"
            + (_PT_ZERO_TICKS + note_region_ticks).to_bytes(5, "little"),
        )
        midi_lane = _pt_block(2, 0x1057, _pt_block(2, 0x1056, midi_placement))
        midi_map = _pt_block(1, 0x1058, b"\x00\x00" + midi_lane)
        parts += [midi_block, midi_names, midi_regions, midi_map]

    plaintext = header + first + version
    plaintext += b"\x00" * (4096 - len(plaintext))
    plaintext += b"".join(parts)
    obfuscated = _deobfuscate(plaintext)
    assert obfuscated[0x1000:] != plaintext[0x1000:]

    ptx_path = output_dir / "Synthetic Session.ptx"
    ptx_path.write_bytes(obfuscated)
    return ptx_path
