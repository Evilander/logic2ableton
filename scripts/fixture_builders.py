"""Synthetic DAW fixtures shared by tests and packaged-binary smoke checks."""

from __future__ import annotations

import gzip
import plistlib
import struct
import wave
import xml.etree.ElementTree as ET
from pathlib import Path

from logic2ableton.audio import write_pcm_wav
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
    mixer = ET.SubElement(ET.SubElement(ET.SubElement(live_set, "MainTrack"), "DeviceChain"), "Mixer")
    ET.SubElement(ET.SubElement(mixer, "Tempo"), "Manual").set("Value", "128")
    ET.SubElement(ET.SubElement(mixer, "TimeSignature"), "Manual").set("Value", "201")

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


def build_folder_style_logicx(
    output_dir: Path,
    *,
    name: str = "FolderProj",
    project_data: bytes = b"",
    sampler_files: list[str] | None = None,
) -> Path:
    """Create a folder-saved Logic project: .logicx package plus a sibling Audio Files folder.

    Mirrors Apple's "Save As" project-folder layout: the project folder holds
    the .logicx (Resources/, Alternatives/) and a sibling 'Audio Files'
    folder, rather than 'Media/Audio Files' nested inside the package.
    """
    project_folder = output_dir / name
    logicx_path = project_folder / f"{name}.logicx"
    resources = logicx_path / "Resources"
    alternative = logicx_path / "Alternatives" / "000"
    resources.mkdir(parents=True)
    alternative.mkdir(parents=True)
    with open(resources / "ProjectInformation.plist", "wb") as handle:
        plistlib.dump(
            {"VariantNames": {"0": name}, "ActiveVariant": 0, "HasProjectFolder": True},
            handle,
        )
    metadata = {
        "BeatsPerMinute": 120.0,
        "SampleRate": 44_100,
        "NumberOfTracks": 0,
        "SamplerInstrumentsFiles": sampler_files or [],
    }
    with open(alternative / "MetaData.plist", "wb") as handle:
        plistlib.dump(metadata, handle)
    (alternative / "ProjectData").write_bytes(project_data)
    (project_folder / "Audio Files").mkdir(parents=True)
    return logicx_path


_LOGIC_SEQUENCE_ORIGIN = 38400
_LOGIC_PROJECT_START = 34560
_LOGIC_TICKS_PER_BAR = 3840


def _logic_object(
    tag: str,
    payload: bytes,
    *,
    kind: int = 1,
    class_id: int = 0x17,
    id1: int = 0,
    id2: int | None = None,
    version: int = 1,
    wide_sentinel: bool = False,
) -> bytes:
    """Serialise one Logic object: reversed tag, ids, sentinel, version, size, payload."""
    if wide_sentinel:
        ids = struct.pack("<I", id1) + b"\xff\xff\xff\xff\xff\xff\xff\x7f"
    elif id2 is None:
        ids = struct.pack("<I", id1) + b"\xff\xff\xff\xff\xff\xff\xff\xff"
    else:
        ids = struct.pack("<II", id1, id2) + b"\xff\xff\xff\xff"
    header = tag.encode("ascii")[::-1] + struct.pack("<HI", kind, class_id) + ids
    assert len(header) == 22
    return header + b"\x02\x00\x00\x00" + bytes([version, 0]) + struct.pack("<I", len(payload)) + payload


def _logic_event_sequence(records: bytes, *, id1: int, id2: int, version: int = 1) -> bytes:
    payload = b"\x00\x00\x00\x00" + records + b"\xf1\x00\x00\x00\xff\xff\xff\x3f\x00\x00\x00\x00"
    return _logic_object("EvSq", payload, kind=1, id1=id1, id2=id2, version=version)


def logic_note_record(
    rel_tick: int,
    pitch: int,
    velocity: int,
    duration: int,
    *,
    flag: int = 0x01,
    variant: int = 0x40,
    nudge: int = 0,
    extension: bool = False,
) -> bytes:
    """A 32-byte note event (status 0x90); ``extension`` appends the trailing
    data record Logic writes for some notes (status bit 0x4000)."""
    status = 0x90 | (0x4000 if extension else 0)
    record = (
        struct.pack("<II", status, _LOGIC_SEQUENCE_ORIGIN + rel_tick)
        + b"\x00\x80\x24"
        + bytes([velocity, pitch, 0, 0, flag, variant, 0, 0, 0])
        + struct.pack("<h", nudge)
        + b"\x00\x89\x00\x00\x00\x00"
        + struct.pack("<I", duration)
    )
    assert len(record) == 32
    if extension:
        record += b"\xe4\xd9\x01\x00" + b"\x00" * 28
    return record


def _logic_marker_record(tick: int, marker_id: int, length: int) -> bytes:
    return (
        struct.pack("<II", 0x12, tick)
        + b"\x00\x00\x00\x00\x00\x00\x00\x01"
        + struct.pack("<I", marker_id)
        + b"\x00\x00\x00\x88\x00\x00\x00\x00"
        + struct.pack("<I", length)
        + b"\x00\x00\x00\x00\x00\x00\x00\x88"
        + b"\x00" * 8
    )


def _logic_placement_record(
    *,
    audio: bool,
    tick: int,
    track: int,
    lane: int,
    flags: int,
    loop_span: int | None,
    sequence: int = 0,
    audio_index: int = 0,
    audio_file: int = 0,
) -> bytes:
    span = 0x3FFFFFFF if loop_span is None else loop_span
    record = (
        struct.pack("<II", 0x24 if audio else 0x20, tick)
        + struct.pack("<I", 0 if audio else sequence)
        + struct.pack("<I", flags)
        + struct.pack("<I", track)
        + bytes([lane, 0, 0, 0x89])
        + b"\x00\x00\x00\x00"
        + struct.pack("<I", span)
        + (b"\xff\xff\xff\xff" if audio else struct.pack("<I", sequence))
        + (b"\x00\x00\x00\xbc" if audio else b"\x00\x00\x00\x88")
        + struct.pack("<II", audio_index if audio else 0, audio_file if audio else 0)
        + b"\x00" * 32
    )
    assert len(record) == 80
    return record


def _logic_rtf(text: str) -> bytes:
    body = text.encode("cp1252", errors="replace")
    escaped = "".join(f"\\'{b:02x}" if b >= 0x80 or chr(b) in "{}\\" else chr(b) for b in body)
    return (
        "{\\rtf1\\ansi\\ansicpg1252\\cocoartf2513\n{\\fonttbl\\f0\\fswiss\\fcharset0 Helvetica;}\n"
        "{\\colortbl;\\red255\\green255\\blue255;}\n\\pard\\tx560\\pardirnatural\\partightenfactor0\n\n"
        f"\\f0\\fs24 \\cf2 {escaped}}}"
    ).encode("latin-1")


def build_logic_arrangement_project_data(
    *,
    tracks: dict[int, str],
    sequences: list[dict],
    midi_regions: list[dict],
    audio_files: dict[int, str] | None = None,
    audio_regions: list[dict] | None = None,
    audio_placements: list[dict] | None = None,
    markers: list[tuple[int, int, str]] | None = None,
    project_start_bar: int | None = 1,
    tempo: float = 120.0,
    version: int = 1,
) -> bytes:
    """ProjectData in the object layout logic_project_data decodes.

    ``sequences``: {"id", "name", "notes": [(rel_tick, pitch, velocity, duration)] or
    prebuilt record bytes, "start": content start ticks, "length": content ticks}.
    ``midi_regions``: {"bar", "track", "sequence", "loop_bars", "muted", "lane"}.
    ``audio_regions``: {"file", "index", "name", "offset", "length"}.
    ``audio_placements``: {"bar", "track", "file", "index", "muted", "lane", "loop_beats"}.
    ``markers``: (bar, marker_id, name). Bars are 1-based arrangement bars.
    """
    start_bar = 1 if project_start_bar is None else project_start_bar

    def region_tick(bar: float) -> int:
        return _LOGIC_PROJECT_START + int(round((bar - start_bar) * _LOGIC_TICKS_PER_BAR))

    header = bytearray(400)
    struct.pack_into("<II", header, 170, int(round(tempo * 10_000)), int(round(tempo * 10_000)))
    if project_start_bar is not None:
        struct.pack_into("<I", header, 364, _LOGIC_SEQUENCE_ORIGIN + (project_start_bar - 1) * _LOGIC_TICKS_PER_BAR)
    blob = bytes(header)

    for track_id, name in tracks.items():
        encoded = name.encode("utf-8")
        payload = b"\x00" * 162 + struct.pack("<H", len(encoded)) + encoded + b"\x00" * 16
        blob += _logic_object("Envi", payload, kind=5, class_id=0x14, id1=track_id, version=version)

    for spec in sequences:
        encoded = spec["name"].encode("utf-8")
        trailer = bytearray(80)
        struct.pack_into("<I", trailer, 4, spec.get("start", 0))
        struct.pack_into("<I", trailer, 60, spec["length"])
        payload = (
            b"\x00" * 4 + b"\x2e\x03\x01\x00" + b"\x00" * 12
            + struct.pack("<H", len(encoded)) + encoded + (b"\x00" if len(encoded) & 1 else b"")
            + bytes(trailer)
        )
        blob += _logic_object("MSeq", payload, kind=2, id1=spec["id"], version=version)
        blob += _logic_object("Trak", b"\x00" * 4, kind=4, id1=spec["id"], version=version, wide_sentinel=True)
        records = b""
        for note in spec.get("notes", []):
            records += note if isinstance(note, bytes) else logic_note_record(*note)
        blob += _logic_event_sequence(records, id1=spec["id"], id2=spec["id"] + 1000, version=version)

    placements = b""
    for spec in sorted(midi_regions, key=lambda r: r["bar"]):
        flags = 0x400 | (0x1000 if spec.get("loop_bars") else 0) | (0x1 if spec.get("muted") else 0)
        placements += _logic_placement_record(
            audio=False,
            tick=region_tick(spec["bar"]),
            track=spec["track"],
            lane=spec.get("lane", 1),
            flags=flags,
            loop_span=int(spec["loop_bars"] * _LOGIC_TICKS_PER_BAR) if spec.get("loop_bars") else None,
            sequence=spec["sequence"],
        )
    for spec in sorted(audio_placements or [], key=lambda r: r["bar"]):
        placements += _logic_placement_record(
            audio=True,
            tick=region_tick(spec["bar"]),
            track=spec["track"],
            lane=spec.get("lane", 1),
            flags=(0x1000 if spec.get("loop_beats") else 0) | (0x1 if spec.get("muted") else 0),
            loop_span=int(spec["loop_beats"] * 960) if spec.get("loop_beats") else None,
            audio_index=spec.get("index", 0),
            audio_file=spec["file"],
        )
    blob += _logic_object("MSeq", b"\x00" * 20 + b"\x00\x00" + b"\x00" * 80, kind=2, id1=4, version=version)
    blob += _logic_event_sequence(placements, id1=4, id2=9004, version=version)

    marker_records = b""
    for bar, marker_id, name in sorted(markers or []):
        tick = _LOGIC_SEQUENCE_ORIGIN + int(round((bar - 1) * _LOGIC_TICKS_PER_BAR))
        marker_records += _logic_marker_record(tick, marker_id, _LOGIC_TICKS_PER_BAR)
        blob += _logic_object("TxSq", b"\x00" * 100 + _logic_rtf(name), kind=1, class_id=0x20, id1=marker_id, version=version)
    if marker_records:
        blob += _logic_event_sequence(marker_records, id1=8, id2=9008, version=version)

    for file_id, filename in (audio_files or {}).items():
        encoded = filename.encode("utf-16-le")
        payload = b"\x00" * 12 + struct.pack("<H", len(filename)) + encoded + b"\x00" * 8
        blob += _logic_object("AuFl", payload, kind=1, class_id=0x0B, id1=file_id, version=version)
    for spec in audio_regions or []:
        encoded = spec["name"].encode("utf-8")
        payload = bytearray(78)  # name length lands at object offset 110
        struct.pack_into("<Q", payload, 10, spec.get("offset", 0))
        struct.pack_into("<Q", payload, 26, spec["length"])
        payload += struct.pack("<H", len(encoded)) + encoded + b"\x00" * 8
        blob += _logic_object(
            "AuRg", bytes(payload), kind=1, class_id=0x0B, id1=spec["file"], id2=spec.get("index", 0), version=version,
        )
    return blob


def write_smpte_stamped_wav(
    path: Path,
    *,
    smpte_seconds: float,
    sample_rate: int = 44_100,
    channels: int = 1,
    sample_width: int = 2,
    frames: int = 44_100,
) -> Path:
    """Write a BWF-stamped test WAV with its bext TimeReference at a chosen SMPTE time."""
    path.parent.mkdir(parents=True, exist_ok=True)
    time_reference_samples = int(smpte_seconds * sample_rate)
    write_pcm_wav(
        path,
        sample_rate=sample_rate,
        channels=channels,
        sample_width=sample_width,
        frames=b"\x00" * (frames * channels * sample_width),
        time_reference_samples=time_reference_samples,
    )
    return path


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
