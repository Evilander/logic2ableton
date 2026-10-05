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
    used_audio_files: list[str] | None = None,
) -> Path:
    """Create a minimal Logic bundle containing supplied ProjectData.

    ``used_audio_files`` writes Logic's own list of the audio files this
    alternative uses (MetaData ``AudioFiles``); without it the bundle carries
    no such list, like saves from older Logic versions.
    """
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
    if used_audio_files is not None:
        metadata["AudioFiles"] = [f"Audio Files/{name}" for name in used_audio_files]
        metadata["UnusedAudioFiles"] = []
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
    """A 32-byte note event (status 0x90); ``extension`` appends the two extra
    units Logic attaches to some notes (status bit 0x4000, classes 0xA3 and 0xA7)."""
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
        record += b"\xe4\xd9\x01\x00\x00\x00\x00\xa3" + b"\x00" * 8
        record += b"\x00" * 7 + b"\xa7" + b"\x00" * 8
    return record


def _logic_tempo_record(tick: int, bpm: float, seconds: float) -> bytes:
    """A tempo event (status 0x60): the tempo x 10000 and the time it falls on,
    in 1/2000 s counted from one hour."""
    return (
        struct.pack("<II", 0x60, tick)
        + b"\x00\x00\x00\x00\x7f\x00\x00\x01"
        + struct.pack("<I", int(round(bpm * 10_000)))
        + b"\x00\x00\x40\x88"
        + struct.pack("<I", int(round((3600 + seconds) * 2000)))
        + b"\x00\x00\x00\x00"
    )


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
    fades: bool = False,
) -> bytes:
    """An 80-byte region placement: five 16-byte units, each continuation unit
    marked by a class byte with the high bit set. ``fades`` appends the ten
    units Logic attaches to an audio region that has fades."""
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
        + b"\x00\x06\x00\x00\x00\x00\x06\x8a" + b"\x00" * 8
        + b"\x00" * 7 + (b"\x89" if audio else b"\x88") + b"\x00" * 8
    )
    assert len(record) == 80
    if fades:
        group = (
            b"\x00\x00\x00\x00\x00\x00\x0b\xaa" + b"\x00" * 8
            + (b"\x00" * 7 + b"\x88" + b"\x00" * 8) * 4
        )
        record += group * 2
    return record


def _logic_rtf(text: str) -> bytes:
    body = text.encode("cp1252", errors="replace")
    escaped = "".join(f"\\'{b:02x}" if b >= 0x80 or chr(b) in "{}\\" else chr(b) for b in body)
    return (
        "{\\rtf1\\ansi\\ansicpg1252\\cocoartf2513\n{\\fonttbl\\f0\\fswiss\\fcharset0 Helvetica;}\n"
        "{\\colortbl;\\red255\\green255\\blue255;}\n\\pard\\tx560\\pardirnatural\\partightenfactor0\n\n"
        f"\\f0\\fs24 \\cf2 {escaped}}}"
    ).encode("latin-1")


_LOGIC_STRIP_POOL = 36  # the AuCn object that owns the channel strips in real projects
# Strip types in the order Logic numbers channels: (label, stereo) -> type code.
_LOGIC_STRIP_TYPES = {
    ("Audio", None): 0x40, ("Input", False): 0x41, ("Aux", None): 0x42, ("Inst", None): 0x43,
    ("Output", False): 0x44, ("Bus", None): 0x45, ("Master", None): 0x46, ("Sub", None): 0x46,
    ("Input", True): 0x49, ("Output", True): 0x4C,
}


def _logic_strip_identity(label: str) -> tuple[int, int]:
    """A strip's type code and its number within the type, as Logic stores them."""
    text = label.lstrip("\u2022").strip()
    kind, _, numbers = text.partition(" ")
    pair = "-" in numbers
    first = int(numbers.split("-")[0]) if numbers else 1
    if kind in ("Input", "Output"):
        return _LOGIC_STRIP_TYPES[(kind, pair)], (first - 1) // 2 if pair else first - 1
    if kind == "Master":
        return 0x46, 0
    if kind == "Sub":
        return 0x46, first  # VCA strips follow the master in its type
    return _LOGIC_STRIP_TYPES[(kind, None)], first - 1


def _logic_channel_strip(
    position: int,
    label: str,
    *,
    stereo: bool = False,
    fader: float = 90.0,
    pan: int = 64,
    muted: bool = False,
    soloed: bool = False,
    solo_silenced: bool = False,
    output: int = 0xFFFF,
    input_code: int = 0,
    stereo_input: bool | None = None,
    version: int = 1,
) -> bytes:
    """One AuCO object: type and number at +40, label at +96, stereo bit at +114, fader, stereo
    input at +122, solo button at +124, pan, mute and silenced-by-a-solo at +126, output and
    input. Its second id is its place in the file."""
    payload = bytearray(132)
    struct.pack_into("<HH", payload, 8, *_logic_strip_identity(label))
    encoded = (label if label.startswith("\u2022") else " " + label).encode("mac_roman")
    payload[64:64 + len(encoded)] = encoded
    payload[80:84] = bytes([0xAB, 0xF7, 0xD7 if stereo else 0xD3, 0xCF])
    payload[89] = int(fader)
    payload[90] = 1 if (stereo if stereo_input is None else stereo_input) else 0
    payload[92] = 1 if soloed else 0
    payload[93] = pan
    payload[94] = (1 if muted else 0) | (2 if solo_silenced else 0)
    struct.pack_into("<HH", payload, 96, output, input_code)
    struct.pack_into("<I", payload, 120, int(round(fader * 0x1000000)))
    return _logic_object("AuCO", bytes(payload), kind=1, id1=_LOGIC_STRIP_POOL, id2=position, version=version)


def _logic_mixer(mixer: dict, *, version: int = 1) -> tuple[bytes, dict[int, int], dict[int, int]]:
    """The channel strip pool of a project, and the channel number of each track's strip.

    ``mixer``: {"stereo_outputs", "mono_outputs", "stereo_inputs", "mono_inputs",
    "buses": how many of each the pool holds (an audio interface with other
    channel counts numbers the same bus differently), "spare_aux": also write
    Logic's unused aux strips, one preset to each bus, "device_outputs": how
    many outputs get the bullet Logic puts in front of the ones the audio
    device has, "decoys": {track id: channel number} for objects that are not
    channel strips but hold that number where a strip object holds its
    strip, "strips": [...], "late": [...] strips created after the project
    was, which Logic 10 writes at the end of the pool, "missing": labels of
    strips Logic counts but whose objects are left out of the file,
    "owner_list": False to leave out the owner's list of type sizes}.
    A strip: {"label": "Audio 1", "track": the id of the track object that
    plays through it, or a list of ids (omit for a strip that is not on the
    mixer), "stereo", "fader" (0-127, 90 is 0 dB), "pan" (0-127), "muted",
    "soloed", "solo_silenced" (by a solo elsewhere), "stereo_input" when it
    differs from "stereo" (a mono strip fed in stereo), and where it plays to:
    "output" (stereo output pair, 0 is Output 1-2; the default), "bus" (bus
    number), "mono_output" (output number), "no_output" or "output_code"
    (the raw number); an aux adds "from_bus", the bus it listens on}.

    The strips are written in the order given, then the inputs, outputs and
    buses, then the spare aux strips, then the late ones: not the order Logic
    numbers channels in, which goes by type.
    """
    stereo_outputs = mixer.get("stereo_outputs", 2)
    mono_outputs = mixer.get("mono_outputs", 2 * stereo_outputs)
    stereo_inputs = mixer.get("stereo_inputs", 2)
    mono_inputs = mixer.get("mono_inputs", 2 * stereo_inputs)
    buses = mixer.get("buses", 8)

    def output_code(spec: dict) -> int:
        if "output_code" in spec:
            return spec["output_code"]
        if spec.get("no_output"):
            return 0xFFFF
        if "bus" in spec:
            return stereo_outputs + spec["bus"] - 1
        if "mono_output" in spec:
            return stereo_outputs + buses + spec["mono_output"] - 1
        return spec.get("output", 0)

    def input_code(spec: dict) -> int:
        if "from_bus" not in spec:
            return spec.get("input", 0)
        fed_in_stereo = spec.get("stereo_input", spec.get("stereo"))
        return (stereo_inputs if fed_in_stereo else mono_inputs) + spec["from_bus"] - 1

    marked = mixer.get("device_outputs", 0)

    def output_label(text: str, number: int) -> str:
        return ("\u2022" if number < marked else "") + text

    declared = list(mixer.get("strips", []))
    late = list(mixer.get("late", []))
    taken = {_logic_strip_identity(spec["label"]) for spec in declared + late}
    furniture = [{"label": f"Input {n + 1}"} for n in range(mono_inputs)]
    furniture += [{"label": f"Input {2 * n + 1}-{2 * n + 2}", "stereo": True} for n in range(stereo_inputs)]
    furniture += [{"label": output_label(f"Output {n + 1}", n)} for n in range(mono_outputs)]
    furniture += [{"label": output_label(f"Output {2 * n + 1}-{2 * n + 2}", 2 * n), "stereo": True} for n in range(stereo_outputs)]
    furniture += [{"label": f"Bus {n + 1}", "stereo": True} for n in range(buses)]
    furniture = [dict(spec, no_output=True) for spec in furniture if _logic_strip_identity(spec["label"]) not in taken]
    spare = []
    if mixer.get("spare_aux"):
        used = [_logic_strip_identity(spec["label"])[1] for spec in declared + late if spec["label"].startswith("Aux ")]
        first_free = max(used, default=-1) + 2
        spare = [
            {"label": f"Aux {first_free + bus - 1}", "stereo": True, "from_bus": bus} for bus in range(1, buses + 1)
        ]

    written = declared + furniture + spare + late
    sizes: dict[int, int] = {}
    for spec in written:
        kind, number = _logic_strip_identity(spec["label"])
        sizes[kind] = max(sizes.get(kind, 0), number + 1)
    first_number, total = {}, 0
    for kind in sorted(sizes):
        first_number[kind] = total
        total += sizes[kind]

    blob = b""
    if mixer.get("owner_list", True):
        # The owner: how many strips in all at +62, then of each type from 0x40 at +64.
        owner = bytearray(64)
        struct.pack_into("<H", owner, 30, total)
        for kind, size in sizes.items():
            struct.pack_into("<H", owner, 32 + 2 * (kind - 0x40), size)
        blob += _logic_object("AuCn", bytes(owner), kind=1, id1=_LOGIC_STRIP_POOL, id2=0, version=version)
    missing = set(mixer.get("missing", ()))
    track_strips: dict[int, int] = {}
    for position, spec in enumerate(written):
        kind, number = _logic_strip_identity(spec["label"])
        owners = spec.get("track")
        for track_id in owners if isinstance(owners, list) else [owners]:
            if track_id is not None:
                track_strips[track_id] = first_number[kind] + number
        if spec["label"] in missing:
            continue
        blob += _logic_channel_strip(
            position,
            spec["label"],
            stereo=bool(spec.get("stereo")),
            fader=spec.get("fader", 90.0),
            pan=spec.get("pan", 64),
            muted=bool(spec.get("muted")),
            soloed=spec.get("soloed", False),
            solo_silenced=spec.get("solo_silenced", False),
            output=output_code(spec),
            input_code=input_code(spec),
            stereo_input=spec.get("stereo_input"),
            version=version,
        )
    # Every AuCn also owns one small AuCO that is not a strip.
    blob += _logic_object("AuCO", b"\x00" * 14, kind=1, id1=32, id2=0, version=version)
    blob += _logic_object("AuCO", b"\x00" * 14, kind=1, id1=_LOGIC_STRIP_POOL, id2=len(written), version=version)
    return blob, track_strips, dict(mixer.get("decoys", {}))


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
    history: list[dict] | None = None,
    tempo_changes: list[tuple[float, float]] | None = None,
    mixer: dict | None = None,
) -> bytes:
    """ProjectData in the object layout logic_project_data decodes.

    ``mixer``: the channel strips, see ``_logic_mixer``. Without it the
    tracks are written as objects that are not channel strips and the
    project has no mixer to read.

    ``tempo_changes``: (bar, bpm) steps of the tempo track after bar 1, where
    ``tempo`` starts. A third element overrides the time stamp Logic would
    store for that event (seconds from bar 1).

    ``history``: undo-history steps appended after the current state, as Logic
    saves them: each dict takes the same keys as this function (tracks,
    sequences, midi_regions, audio_*, markers) and is written as a further
    Song object followed by those old object copies.

    ``sequences``: {"id", "name", "notes": [(rel_tick, pitch, velocity, duration)] or
    prebuilt record bytes, "start": content start ticks, "length": content ticks}.
    ``midi_regions``: {"bar", "track", "sequence", "loop_bars", "muted", "lane"}.
    ``audio_regions``: {"file", "index", "name", "offset", "length"}.
    ``audio_placements``: {"bar", "track", "file", "index", "muted", "lane", "loop_beats", "fades"}.
    ``markers``: (bar, marker_id, name). Bars are 1-based arrangement bars.
    """
    start_bar = 1 if project_start_bar is None else project_start_bar

    def region_tick(bar: float) -> int:
        return _LOGIC_PROJECT_START + int(round((bar - start_bar) * _LOGIC_TICKS_PER_BAR))

    # The file opens with 24 bytes and then the Song object, whose payload
    # holds the header fields at fixed file offsets (tempo 170/174, project
    # start 364).
    song_payload = bytearray(376)
    struct.pack_into("<II", song_payload, 170 - 56, int(round(tempo * 10_000)), int(round(tempo * 10_000)))
    if project_start_bar is not None:
        struct.pack_into(
            "<I", song_payload, 364 - 56,
            _LOGIC_SEQUENCE_ORIGIN + (project_start_bar - 1) * _LOGIC_TICKS_PER_BAR,
        )
    blob = b"\x00" * 24 + _logic_object(
        "Song", bytes(song_payload), kind=3, class_id=0xFFFFFFFF, id1=0xFFFFFFFF, version=version,
    )

    # The tempo track: one event at bar 1 and one per change, each stamped
    # with the time it falls on.
    tempo_records = _logic_tempo_record(_LOGIC_SEQUENCE_ORIGIN, tempo, 0.0)
    current_bar, current_bpm, seconds = 1.0, tempo, 0.0
    for bar, bpm, *stamp in sorted(tempo_changes or []):
        seconds += (bar - current_bar) * (_LOGIC_TICKS_PER_BAR / 960) * 60.0 / current_bpm
        tick = _LOGIC_SEQUENCE_ORIGIN + int(round((bar - 1) * _LOGIC_TICKS_PER_BAR))
        tempo_records += _logic_tempo_record(tick, bpm, stamp[0] if stamp else seconds)
        current_bar, current_bpm = bar, bpm
    blob += _logic_event_sequence(tempo_records, id1=0, id2=9000, version=version)

    strips_blob, track_strips, decoys = _logic_mixer(mixer, version=version) if mixer else (b"", {}, {})
    for track_id, name in tracks.items():
        encoded = name.encode("utf-8")
        head = bytearray(162)
        tail = b"\x00" * 16
        if track_id in track_strips or track_id in decoys:
            # A channel strip object: type 0x11 at +117, and after the
            # even-aligned name the channel number of its strip, plus one.
            # Other objects (a MIDI click is type 9) keep unrelated data there.
            head[117 - 32] = 0x11 if track_id in track_strips else 0x09
            strip_index = track_strips.get(track_id, decoys.get(track_id))
            tail = (b"\x00" if len(encoded) & 1 else b"") + struct.pack("<H", strip_index + 1) + tail
        payload = bytes(head) + struct.pack("<H", len(encoded)) + encoded + tail
        blob += _logic_object("Envi", payload, kind=5, class_id=0x14, id1=track_id, version=version)
    blob += strips_blob

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
            fades=bool(spec.get("fades")),
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
    for step in history or []:
        snapshot = build_logic_arrangement_project_data(
            tracks=step.get("tracks", {}),
            sequences=step.get("sequences", []),
            midi_regions=step.get("midi_regions", []),
            audio_files=step.get("audio_files"),
            audio_regions=step.get("audio_regions"),
            audio_placements=step.get("audio_placements"),
            markers=step.get("markers"),
            project_start_bar=project_start_bar,
            tempo=step.get("tempo", tempo),
            version=version,
            tempo_changes=step.get("tempo_changes"),
            mixer=step.get("mixer"),
        )
        blob += snapshot[24:]  # its Song object and the old object copies
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
