"""Structured decoder for the object graph inside Logic Pro's ProjectData.

Logic serialises a song as a flat run of tagged objects. Every object starts
with a 4-byte tag stored byte-reversed on disk ("qSvE" is EvSq), a u16 kind,
a u32 class id, two u32 object ids, a 4- or 8-byte sentinel, the bytes
``02 00 00 00``, a u16 format version (1 for Logic 10.x saves, 2 for Logic 11
and later) and a u32 payload size. The objects read here:

* ``EvSq`` (event sequence): fixed-size records starting 36 bytes into the
  object. Each record begins with a u32 status and a u32 tick; the low
  status byte selects the record type and size, status bit 0x4000 means a
  32-byte extension record trails the event, and a status of 0xF1
  terminates the list.
    - 0x90 note (32 bytes): velocity at +11, pitch at +12, int16 nudge at
      +20, event class 0x89 at +23, duration in ticks at +28.
    - 0x12 marker (48 bytes): marker id at +16, event class 0x88 at +23,
      distance to the next marker at +28.
    - 0x20 / 0x24 MIDI / audio region placement (80 bytes): flags at +12
      (bit 0 mute, bit 12 loop), track id at +16, lane index at +20, loop
      span in ticks at +28 (0x3FFFFFFF when the region does not loop), MSeq
      id at +32 for MIDI regions, audio region index at +40 and audio file
      id at +44 for audio regions. Per-track automation containers are
      stored as MIDI placements of an MSeq named ``*Automation``.
* ``MSeq`` (a MIDI region's content): u16-prefixed UTF-8 name at +52; from
  the even-aligned end of that name, the content start offset at +4 and the
  content length at +60, both in ticks. The EvSq with the notes follows the
  next ``Trak`` object.
* ``Envi`` (environment object, i.e. a channel strip): u16-prefixed name at
  +194. Region placements reference tracks by this object's id.
* ``TxSq`` (text): an RTF blob holding a marker's name; its object id equals
  the marker id.
* ``AuFl`` (audio file): u16-prefixed UTF-16LE file name at +44.
* ``AuRg`` (audio region): u64 content offset at +42 and u64 content length
  at +58 (samples in the file's own clock), u16-prefixed name at +110. Its
  first id is the AuFl id; the second id tells apart regions cut from one
  file.

Ticks are 960 per quarter note and three tick frames coexist: note ticks are
region-relative with the content origin at 38400; marker ticks count from
38400 = bar 1; region placements count from 34560 = the project start, whose
bar number version-1 saves keep in the song header (u32 at file offset 364,
in marker ticks). Everything above was reverse-engineered from Logic 10.6.2
and Logic 11 saves and checked against Logic's own MIDI exports, arrangement
screenshots and audio file lengths.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field

PPQ = 960
SEQUENCE_ORIGIN_TICKS = 38400   # bar 1 for markers; content origin for notes
PROJECT_START_TICKS = 34560     # region placements count from here
_NO_LOOP = 0x3FFFFFFF
_TERMINATOR = 0xF1

_TAGS = (
    b"qSvE", b"karT", b"qeSM", b"qSxT", b"gRuA", b"lFuA", b"ivnE", b"OCuA", b"UCuA", b"nCuA", b"lytS",
    b"tSxT", b"rpyH", b"OgnS", b"MroC", b"vEuA", b"gnoS", b"tSnI", b"snrT", b"ryaL", b"tScS", b"ediV",
)
_TAG_RE = re.compile(b"|".join(re.escape(tag) for tag in _TAGS))
_SENTINELS = (b"\xff\xff\xff\xff", b"\xff\xff\xff\x7f")
_RECORD_SIZES = {0x90: 32, 0x12: 48, 0x20: 80, 0x24: 80, 0x11: 64, 0x30: 80, 0x60: 32, 0xC0: 16, 0x50: 16}
_NOTE_STATUS = 0x90
_MARKER_STATUS = 0x12
_MIDI_REGION_STATUS = 0x20
_AUDIO_REGION_STATUS = 0x24
_NOTE_CLASS = 0x89
_MARKER_CLASS = 0x88
_FLAG_MUTE = 0x1
_FLAG_LOOP = 0x1000
_EXTENSION_FLAG = 0x4000    # status bit: a 32-byte extension record follows the event
_EXTENSION_SIZE = 32
_PROJECT_START_OFFSET = 364
_RTF_START = bytes([0x7B, 0x5C]) + b"rtf1"


def _u16(data: bytes, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def _u64(data: bytes, offset: int) -> int:
    return struct.unpack_from("<Q", data, offset)[0]


@dataclass
class ObjectHeader:
    tag: str
    offset: int
    kind: int
    class_id: int
    id1: int
    id2: int
    version: int
    size: int


@dataclass
class LogicNote:
    tick: int           # region-relative, content origin at SEQUENCE_ORIGIN_TICKS
    pitch: int
    velocity: int
    duration: int       # ticks
    nudge: int = 0      # sub-tick offset for unquantized notes


@dataclass
class LogicSequence:
    """An MSeq object: the content of one MIDI region."""
    id: int
    name: str
    content_start: int  # ticks into the sequence where the region window begins
    content_length: int  # ticks
    notes: list[LogicNote] = field(default_factory=list)

    @property
    def is_container(self) -> bool:
        """Automation containers and other internal sequences carry a leading '*'."""
        return self.name.startswith("*")


@dataclass
class LogicMarker:
    tick: int           # SEQUENCE_ORIGIN_TICKS = bar 1
    id: int
    name: str


@dataclass
class LogicPlacement:
    """One region on the arrangement."""
    kind: str           # "midi" or "audio"
    tick: int           # PROJECT_START_TICKS = project start
    track_id: int
    lane: int
    flags: int
    loop_span: int | None       # ticks the region repeats across; None when not looping
    sequence_id: int | None     # MSeq id (MIDI)
    audio_file_id: int | None   # AuFl id (audio)
    audio_region_index: int | None

    @property
    def muted(self) -> bool:
        return bool(self.flags & _FLAG_MUTE)

    @property
    def looped(self) -> bool:
        return bool(self.flags & _FLAG_LOOP) and self.loop_span is not None


@dataclass
class LogicAudioRegion:
    file_id: int
    index: int
    name: str
    content_offset: int     # samples into the file
    content_length: int     # samples


@dataclass
class LogicArrangement:
    format_version: int = 0
    project_start_bar: int | None = None
    tempo_bpm: float | None = None
    track_names: dict[int, str] = field(default_factory=dict)
    sequences: dict[int, LogicSequence] = field(default_factory=dict)
    markers: list[LogicMarker] = field(default_factory=list)
    placements: list[LogicPlacement] = field(default_factory=list)
    audio_files: dict[int, str] = field(default_factory=dict)
    audio_regions: dict[tuple[int, int], LogicAudioRegion] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def regions(self) -> list[LogicPlacement]:
        """Placements that are user regions (automation containers excluded)."""
        out = []
        for placement in self.placements:
            if placement.kind == "midi":
                sequence = self.sequences.get(placement.sequence_id)
                if sequence is None or sequence.is_container:
                    continue
            out.append(placement)
        return out

    def track_name(self, track_id: int) -> str:
        return self.track_names.get(track_id) or f"Track {track_id}"

    def region_beats(self, tick: int, beats_per_bar: float) -> float:
        """Arrangement position in beats from bar 1 for a placement tick."""
        start_bar = self.project_start_bar if self.project_start_bar is not None else 1
        return (tick - PROJECT_START_TICKS) / PPQ + (start_bar - 1) * beats_per_bar

    @staticmethod
    def marker_beats(tick: int) -> float:
        return (tick - SEQUENCE_ORIGIN_TICKS) / PPQ


def scan_objects(data: bytes) -> list[ObjectHeader]:
    """Locate every tagged object whose header matches Logic's layout."""
    objects: list[ObjectHeader] = []
    for match in _TAG_RE.finditer(data):
        offset = match.start()
        if offset + 32 > len(data):
            continue
        if data[offset + 18:offset + 22] not in _SENTINELS:
            continue
        if data[offset + 22:offset + 26] != b"\x02\x00\x00\x00" or data[offset + 27] != 0:
            continue
        version = data[offset + 26]
        if version not in (1, 2):
            continue
        objects.append(ObjectHeader(
            tag=match.group()[::-1].decode("ascii"),
            offset=offset,
            kind=_u16(data, offset + 4),
            class_id=_u32(data, offset + 6),
            id1=_u32(data, offset + 10),
            id2=_u32(data, offset + 14),
            version=version,
            size=_u32(data, offset + 28),
        ))
    return objects


def iter_records(data: bytes, seq: ObjectHeader):
    """Yield (status, tick, record_offset) for the records of an EvSq."""
    pos = seq.offset + 36
    end = min(seq.offset + 32 + seq.size, len(data))
    while pos + 8 <= end:
        status = _u32(data, pos)
        if status == _TERMINATOR:
            return
        low = status & 0xFF
        size = _RECORD_SIZES.get(low)
        if size is None or pos + size > end:
            return  # unknown record type: stop rather than desync
        yield low, _u32(data, pos + 4), pos
        pos += size
        if status & _EXTENSION_FLAG:
            pos += _EXTENSION_SIZE  # an extra data record trails this event


def _notes(data: bytes, seq: ObjectHeader) -> list[LogicNote]:
    notes = []
    for status, tick, pos in iter_records(data, seq):
        if status != _NOTE_STATUS or data[pos + 23] != _NOTE_CLASS:
            continue
        velocity, pitch = data[pos + 11], data[pos + 12]
        duration = _u32(data, pos + 28)
        if not (1 <= velocity <= 127 and pitch <= 127) or duration == 0:
            continue
        notes.append(LogicNote(
            tick=tick, pitch=pitch, velocity=velocity, duration=duration,
            nudge=struct.unpack_from("<h", data, pos + 20)[0],
        ))
    return notes


def _prefixed_text(data: bytes, offset: int, *, limit: int = 255, utf16: bool = False) -> str | None:
    if offset + 2 > len(data):
        return None
    length = _u16(data, offset)
    if length == 0 or length > limit:
        return None
    raw_len = length * 2 if utf16 else length
    raw = data[offset + 2:offset + 2 + raw_len]
    if len(raw) != raw_len:
        return None
    try:
        return raw.decode("utf-16-le" if utf16 else "utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1") if not utf16 else None


def _sequences(data: bytes, objects: list[ObjectHeader]) -> dict[int, LogicSequence]:
    chain = [obj for obj in objects if obj.tag in ("MSeq", "Trak", "EvSq")]
    sequences: dict[int, LogicSequence] = {}
    for index, obj in enumerate(chain):
        if obj.tag != "MSeq":
            continue
        name = _prefixed_text(data, obj.offset + 52) or ""
        name_len = _u16(data, obj.offset + 52) if obj.offset + 54 <= len(data) else 0
        base = obj.offset + 54 + name_len + (name_len & 1)
        if base + 64 > len(data):
            continue
        content_start = _u32(data, base + 4)
        content_length = _u32(data, base + 60)
        notes: list[LogicNote] = []
        for follower in chain[index + 1:index + 3]:
            if follower.tag == "MSeq":
                break
            if follower.tag == "EvSq":
                notes = _notes(data, follower)
                break
        sequences[obj.id1] = LogicSequence(
            id=obj.id1, name=name, content_start=content_start, content_length=content_length, notes=notes,
        )
    return sequences


def _track_names(data: bytes, objects: list[ObjectHeader]) -> dict[int, str]:
    names = {}
    for obj in objects:
        if obj.tag != "Envi":
            continue
        name = _prefixed_text(data, obj.offset + 194, limit=63)
        if name:
            names[obj.id1] = name
    return names


def _rtf_plain_text(blob: bytes) -> str:
    """Extract the visible text of a one-paragraph RTF blob (marker names)."""
    text = blob.decode("latin-1", errors="replace")
    body = text[text.rfind("\\cf") if "\\cf" in text else 0:]
    body = re.sub(r"\\'([0-9a-fA-F]{2})", lambda m: bytes([int(m.group(1), 16)]).decode("cp1252", "replace"), body)
    body = re.sub(r"\\u(-?\d+)\??", lambda m: chr(int(m.group(1)) % 0x10000), body)
    body = re.sub(r"\\[a-zA-Z]+-?\d* ?", "", body)
    body = body.replace("\\{", "{").replace("\\}", "}").replace("\\\\", "\\")
    body = body.replace("{", "").replace("}", "")
    return " ".join(body.split())


def _marker_names(data: bytes, objects: list[ObjectHeader]) -> dict[int, str]:
    names = {}
    for index, obj in enumerate(objects):
        if obj.tag != "TxSq":
            continue
        # The RTF text can run past the declared payload size, so read up to
        # the next object instead of trusting the size field.
        bound = objects[index + 1].offset if index + 1 < len(objects) else len(data)
        chunk = data[obj.offset:max(bound, obj.offset + 32 + obj.size)]
        start = chunk.find(_RTF_START)
        if start < 0:
            continue
        end = chunk.rfind(b"}")
        if end <= start:
            continue
        name = _rtf_plain_text(chunk[start:end + 1])
        if name:
            names[obj.id1] = name
    return names


def _markers(data: bytes, objects: list[ObjectHeader]) -> list[LogicMarker]:
    names = _marker_names(data, objects)
    markers = []
    for obj in objects:
        if obj.tag != "EvSq":
            continue
        for status, tick, pos in iter_records(data, obj):
            if status != _MARKER_STATUS or data[pos + 23] != _MARKER_CLASS:
                continue
            marker_id = _u32(data, pos + 16)
            markers.append(LogicMarker(tick=tick, id=marker_id, name=names.get(marker_id, f"Marker {marker_id}")))
    markers.sort(key=lambda marker: (marker.tick, marker.id))
    return markers


def _placements(data: bytes, objects: list[ObjectHeader]) -> list[LogicPlacement]:
    placements = []
    for obj in objects:
        if obj.tag != "EvSq":
            continue
        for status, tick, pos in iter_records(data, obj):
            if status not in (_MIDI_REGION_STATUS, _AUDIO_REGION_STATUS):
                continue
            loop_span = _u32(data, pos + 28)
            is_midi = status == _MIDI_REGION_STATUS
            placements.append(LogicPlacement(
                kind="midi" if is_midi else "audio",
                tick=tick,
                track_id=_u32(data, pos + 16),
                lane=data[pos + 20],
                flags=_u32(data, pos + 12),
                loop_span=None if loop_span == _NO_LOOP else loop_span,
                sequence_id=_u32(data, pos + 32) if is_midi else None,
                audio_file_id=_u32(data, pos + 44) if not is_midi else None,
                audio_region_index=_u32(data, pos + 40) if not is_midi else None,
            ))
    return placements


def _audio_files(data: bytes, objects: list[ObjectHeader]) -> dict[int, str]:
    files = {}
    for obj in objects:
        if obj.tag != "AuFl":
            continue
        name = _prefixed_text(data, obj.offset + 44, limit=1024, utf16=True)
        if name:
            files[obj.id1] = name
    return files


def _audio_regions(data: bytes, objects: list[ObjectHeader]) -> dict[tuple[int, int], LogicAudioRegion]:
    regions = {}
    for obj in objects:
        if obj.tag != "AuRg" or obj.offset + 112 > len(data):
            continue
        regions[(obj.id1, obj.id2)] = LogicAudioRegion(
            file_id=obj.id1,
            index=obj.id2,
            name=_prefixed_text(data, obj.offset + 110) or "",
            content_offset=_u64(data, obj.offset + 42),
            content_length=_u64(data, obj.offset + 58),
        )
    return regions


def _project_start_bar(data: bytes, version: int, beats_per_bar_ticks: int) -> int | None:
    """Version-1 saves keep the project start (in marker ticks) in the song header."""
    if version != 1 or len(data) < _PROJECT_START_OFFSET + 4:
        return None
    value = _u32(data, _PROJECT_START_OFFSET)
    delta = value - SEQUENCE_ORIGIN_TICKS
    if delta % beats_per_bar_ticks:
        return None
    bar = 1 + delta // beats_per_bar_ticks
    if not -256 <= bar <= 256:
        return None
    return bar


def decode_project_data(data: bytes, *, beats_per_bar: float = 4.0) -> LogicArrangement:
    """Decode tracks, regions, notes, markers and audio regions from ProjectData."""
    arrangement = LogicArrangement()
    if not data:
        return arrangement
    objects = scan_objects(data)
    if not objects:
        return arrangement
    arrangement.format_version = max(set(obj.version for obj in objects), key=[obj.version for obj in objects].count)
    arrangement.sequences = _sequences(data, objects)
    arrangement.track_names = _track_names(data, objects)
    arrangement.markers = _markers(data, objects)
    arrangement.placements = _placements(data, objects)
    arrangement.audio_files = _audio_files(data, objects)
    arrangement.audio_regions = _audio_regions(data, objects)
    arrangement.project_start_bar = _project_start_bar(
        data, arrangement.format_version, int(round(beats_per_bar * PPQ)) or PPQ * 4,
    )
    if len(data) >= 174 and _u32(data, 170) == _u32(data, 174) and 0 < _u32(data, 170) < 10_000_000:
        arrangement.tempo_bpm = _u32(data, 170) / 10_000
    return arrangement
