"""Structured decoder for the object graph inside Logic Pro's ProjectData.

Logic serialises a song as a flat run of tagged objects. Every object starts
with a 4-byte tag stored byte-reversed on disk ("qSvE" is EvSq), a u16 kind,
a u32 class id, two u32 object ids, a 4- or 8-byte sentinel, the bytes
``02 00 00 00``, a u16 format version (1 for Logic 10.x saves, 2 for Logic 11
and later) and a u32 payload size. The objects read here:

* ``EvSq`` (event sequence): events starting 36 bytes into the object, each
  a chain of 16-byte units. The first unit begins with a u32 status and a
  u32 tick, and the low status byte is the event type. Every further unit
  of the same event has the high bit set in its eighth byte (a class byte
  such as 0x88, 0x89, 0x8A, 0xAA, 0xBC), which a first unit never has
  because that byte is the top of the tick. An event's size is therefore
  read off the data, not a table, and whatever Logic attaches to an event
  (the extra units of some notes, the fades of an audio region) travels
  with it. A status of 0xF1 terminates the list.
    - 0x90 note (2 units, more with attached data): velocity at +11, pitch
      at +12, int16 nudge at +20, class 0x89 at +23, duration in ticks at
      +28.
    - 0x12 marker (3 units): marker id at +16, class 0x88 at +23, distance
      to the next marker at +28.
    - 0x20 / 0x24 MIDI / audio region placement (5 units, more when the
      region has fades): flags at +12 (bit 0 mute, bit 12 loop), track id
      at +16, lane index at +20, class 0x89 at +23, loop span in ticks at
      +28 (0x3FFFFFFF when the region does not loop), MSeq id at +32 for
      MIDI regions, class 0x88 (MIDI) or 0xBC (audio) at +39, audio region
      index at +40 and audio file id at +44 for audio regions.
      Per-track automation containers are stored as MIDI placements of an
      MSeq named ``*Automation``.
    - 0x60 tempo (2 units): class 0x88 at +23, beats per minute x 10000 at
      +16, and at +24 the time the event falls on in 1/2000 s (one hour is
      added, the default SMPTE start). A tempo holds until the next event:
      integrating the list that way reproduces every stored time to the
      millisecond. The list is read into ``tempo_events``.
* ``MSeq`` (a MIDI region's content): u16-prefixed UTF-8 name at +52; from
  the even-aligned end of that name, the content start offset at +4 and the
  content length at +60, both in ticks. The EvSq with the notes follows the
  next ``Trak`` object.
* ``Envi`` (environment object): u16-prefixed name at +194. Region placements
  reference tracks by this object's id. Byte +117 is the object type, 0x11
  for a channel strip; such an object names its strip in the u16 after the
  even-aligned name: the strip's index in the pool below, plus one.
* ``AuCO`` (channel strip): the mixer. One ``AuCn`` object owns the whole
  pool, every strip Logic could show, used or not, and the strip's second
  id is its index in the pool. Logic's own label at +96 ("Audio 3",
  "Inst 1", "Aux 2", "Bus 5", "Input 1-2", "Output 3-4"; the outputs the
  audio device has carry a bullet in front), bit 2 of +114 set
  for a stereo strip, fader 0-127 at +121 (the same value with a 24-bit
  fraction as a u32 at +152), pan 0-127 at +125 (64 is centre), mute at
  +126, output at +128 and input at +130 (u16).
    - The fader law is dB = 40 x log10(value / 90): 90 is 0 dB, 127 is
      +5.98 dB (Logic shows +6.0), and a level typed into Logic as -10 dB
      is stored as 50.6107.
    - The output is an index into the list [stereo outputs, buses, mono
      outputs], whose section sizes are the numbers of "Output a-b" and
      "Bus n" strips in the pool, so the same bus has a different number on
      a different audio interface. 0xFFFF is no output.
    - An aux strip's input is an index into [stereo inputs, buses] when the
      strip is stereo and [mono inputs, buses] when it is mono.
* ``TxSq`` (text): an RTF blob holding a marker's name; its object id equals
  the marker id.
* ``AuFl`` (audio file): u16-prefixed UTF-16LE file name at +44.
* ``AuRg`` (audio region): u64 content offset at +42 and u64 content length
  at +58 (samples in the file's own clock), u16-prefixed name at +110. Its
  first id is the AuFl id; the second id tells apart regions cut from one
  file.

The file opens with a ``Song`` object (its payload holds the header fields
read below). A project saved with undo history has further ``Song`` objects
after the current state, one per history step, each followed by old copies of
the objects that step changed; decoding stops at the second ``Song``.

Ticks are 960 per quarter note and three tick frames coexist: note ticks are
region-relative with the content origin at 38400; marker ticks count from
38400 = bar 1; region placements count from 34560 = the project start, whose
bar number version-1 saves keep in the song header (u32 at file offset 364,
in marker ticks). Everything above was reverse-engineered from Logic 10.6.2
and Logic 11 saves and checked against Logic's own MIDI exports, arrangement
screenshots and audio file lengths; the mixer against the mute buttons, dB
readouts, pan values, bus names and track stacks in the picture of its main
window that Logic saves with each project.
"""

from __future__ import annotations

import math
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
_UNIT = 16                  # events are chains of 16-byte units
_NOTE_SIZE = 32
_MARKER_SIZE = 48
_PLACEMENT_SIZE = 80
_NOTE_STATUS = 0x90
_MARKER_STATUS = 0x12
_TEMPO_STATUS = 0x60
_TEMPO_SIZE = 32
_TEMPO_CLASS = 0x88
_MIDI_REGION_STATUS = 0x20
_AUDIO_REGION_STATUS = 0x24
_NOTE_CLASS = 0x89
_MARKER_CLASS = 0x88
_PLACEMENT_CLASS = 0x89
_MIDI_REGION_CLASS = 0x88
_AUDIO_REGION_CLASS = 0xBC
_FLAG_MUTE = 0x1
_FLAG_LOOP = 0x1000
_PROJECT_START_OFFSET = 364
_STRIP_OBJECT_TYPE = 0x11       # Envi +117: the object is a channel strip
_STRIP_MIN_SIZE = 124           # payload bytes a channel strip needs for the fields read here
_STRIP_STEREO = 0x04            # AuCO +114
_NO_ROUTING = 0xFFFF
_UNITY_FADER = 90.0             # the fader value Logic shows as 0 dB
_PAN_CENTRE = 64
_STEREO_OUTPUT_LABEL = re.compile(r"Output \d+-\d+$")
_STEREO_INPUT_LABEL = re.compile(r"Input \d+-\d+$")
_MONO_INPUT_LABEL = re.compile(r"Input \d+$")
_MONO_OUTPUT_LABEL = re.compile(r"Output \d+$")
_LABEL_MARK = re.compile(r"^[^0-9A-Za-z]+")  # the bullet in front of a device's own outputs
_BUS_LABEL = re.compile(r"Bus \d+$")
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
class LogicTempoEvent:
    tick: int       # marker frame: 38400 is bar 1
    bpm: float      # in effect until the next event
    seconds: float = 0.0  # the time Logic stored for the event (one hour is bar 1 by default)


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
class LogicChannelStrip:
    index: int              # position in the project's channel strip pool
    label: str              # Logic's own label: "Audio 3", "Inst 1", "Aux 2", "Output 1-2"
    stereo: bool
    muted: bool
    fader: float            # 0-127; 90 is 0 dB
    pan: int                # 0-127; 64 is centre
    output: int             # index into [stereo outputs, buses, mono outputs]
    input: int
    track_id: int | None = None     # the track object that plays through this strip
    name: str | None = None         # that object's name, i.e. the name on the mixer

    @property
    def is_aux(self) -> bool:
        return self.label.startswith("Aux ")

    @property
    def volume_db(self) -> float:
        if self.fader <= 0:
            return -math.inf
        return 40.0 * math.log10(self.fader / _UNITY_FADER)

    @property
    def pan_position(self) -> float:
        """-1.0 (hard left) to 1.0 (hard right)."""
        offset = self.pan - _PAN_CENTRE
        return max(-1.0, min(1.0, offset / (_PAN_CENTRE if offset < 0 else _PAN_CENTRE - 1)))


@dataclass
class LogicMixer:
    strips: dict[int, LogicChannelStrip] = field(default_factory=dict)  # by pool index
    track_strips: dict[int, int] = field(default_factory=dict)          # track id -> pool index
    stereo_outputs: int = 0
    mono_outputs: int = 0
    stereo_inputs: int = 0
    mono_inputs: int = 0
    buses: int = 0

    def strip_for_track(self, track_id: int) -> LogicChannelStrip | None:
        return self.strips.get(self.track_strips.get(track_id, -1))

    def destination(self, strip: LogicChannelStrip) -> tuple[str, int] | None:
        """Where a strip plays to: ("output", pair), ("bus", number) or ("mono output", number).

        ``pair`` counts stereo outputs from 0 (0 is Output 1-2); bus and mono
        output numbers are the ones Logic shows.
        """
        code = strip.output
        if code == _NO_ROUTING or not self.stereo_outputs:
            return None
        if code < self.stereo_outputs:
            return "output", code
        if code < self.stereo_outputs + self.buses:
            return "bus", code - self.stereo_outputs + 1
        number = code - self.stereo_outputs - self.buses + 1
        return ("mono output", number) if number <= self.mono_outputs else None

    def source_bus(self, strip: LogicChannelStrip) -> int | None:
        """The bus an aux strip takes its input from, if it is a bus."""
        first_bus = self.stereo_inputs if strip.stereo else self.mono_inputs
        if not self.buses or not first_bus <= strip.input < first_bus + self.buses:
            return None
        return strip.input - first_bus + 1

    def bus_listeners(self, bus: int) -> list[LogicChannelStrip]:
        """The aux channels on the mixer that take their input from a bus.

        The pool also holds every aux Logic could create, each preset to a
        bus of its own; only a strip with a track object is on the mixer.
        """
        return [
            strip for strip in self.strips.values()
            if strip.is_aux and strip.track_id is not None and self.source_bus(strip) == bus
        ]


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
    history_steps: int = 0  # undo-history snapshots found after the current state and ignored
    tempo_events: list[LogicTempoEvent] = field(default_factory=list)
    mixer: LogicMixer = field(default_factory=LogicMixer)

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
    """Yield (status, tick, record_offset, size) for the events of an EvSq.

    The size comes from the data: the first unit of an event is followed by
    its continuation units, recognisable by the high bit of their eighth
    byte. Unknown event types and data attached to known ones are stepped
    over at their real size, so they never hide the events after them.
    """
    pos = seq.offset + 36
    end = min(seq.offset + 32 + seq.size, len(data))
    while pos + _UNIT <= end:
        status = _u32(data, pos)
        tick = _u32(data, pos + 4)
        if status == _TERMINATOR or (status == 0 and tick == _NO_LOOP):
            return
        size = _UNIT
        while pos + size + _UNIT <= end and data[pos + size + 7] & 0x80:
            size += _UNIT
        if not data[pos + 7] & 0x80:  # a stray continuation unit is not an event
            yield status & 0xFF, tick, pos, size
        pos += size


def _notes(data: bytes, seq: ObjectHeader) -> list[LogicNote]:
    notes = []
    for status, tick, pos, size in iter_records(data, seq):
        if status != _NOTE_STATUS or size < _NOTE_SIZE or data[pos + 23] != _NOTE_CLASS:
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


def _mixer(data: bytes, objects: list[ObjectHeader]) -> LogicMixer:
    """Read the channel strip pool and which track object plays through which strip."""
    mixer = LogicMixer()
    candidates = [
        obj for obj in objects
        if obj.tag == "AuCO" and obj.size >= _STRIP_MIN_SIZE and obj.offset + 32 + obj.size <= len(data)
    ]
    if not candidates:
        return mixer
    # Every AuCn owns a small AuCO object; one of them also owns the strips.
    owners: dict[int, int] = {}
    for obj in candidates:
        owners[obj.id1] = owners.get(obj.id1, 0) + 1
    pool = max(owners, key=lambda owner: (owners[owner], -owner))
    for obj in candidates:
        if obj.id1 != pool or obj.id2 in mixer.strips:
            continue
        base = obj.offset
        label = _LABEL_MARK.sub("", data[base + 96:base + 112].split(b"\x00")[0].decode("mac_roman")).strip()
        mixer.strips[obj.id2] = LogicChannelStrip(
            index=obj.id2,
            label=label,
            stereo=bool(data[base + 114] & _STRIP_STEREO),
            muted=bool(data[base + 126] & 1),
            fader=_u32(data, base + 152) / 0x1000000,
            pan=data[base + 125],
            output=_u16(data, base + 128),
            input=_u16(data, base + 130),
        )
    labels = [strip.label for strip in mixer.strips.values()]
    mixer.stereo_outputs = sum(1 for label in labels if _STEREO_OUTPUT_LABEL.match(label))
    mixer.mono_outputs = sum(1 for label in labels if _MONO_OUTPUT_LABEL.match(label))
    mixer.stereo_inputs = sum(1 for label in labels if _STEREO_INPUT_LABEL.match(label))
    mixer.mono_inputs = sum(1 for label in labels if _MONO_INPUT_LABEL.match(label))
    mixer.buses = sum(1 for label in labels if _BUS_LABEL.match(label))

    for obj in objects:
        if obj.tag != "Envi" or obj.offset + 196 > len(data) or data[obj.offset + 117] != _STRIP_OBJECT_TYPE:
            continue
        name = _prefixed_text(data, obj.offset + 194, limit=63)
        if not name:
            continue
        name_len = _u16(data, obj.offset + 194)
        field_offset = obj.offset + 196 + name_len + (name_len & 1)
        if field_offset + 2 > min(obj.offset + 32 + obj.size, len(data)):
            continue
        strip = mixer.strips.get(_u16(data, field_offset) - 1)
        if strip is None or obj.id1 in mixer.track_strips:
            continue
        mixer.track_strips[obj.id1] = strip.index
        if strip.track_id is None:
            strip.track_id = obj.id1
            strip.name = name
    return mixer


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
        for status, tick, pos, size in iter_records(data, obj):
            if status != _MARKER_STATUS or size < _MARKER_SIZE or data[pos + 23] != _MARKER_CLASS:
                continue
            marker_id = _u32(data, pos + 16)
            markers.append(LogicMarker(tick=tick, id=marker_id, name=names.get(marker_id, f"Marker {marker_id}")))
    markers.sort(key=lambda marker: (marker.tick, marker.id))
    return markers


def _tempo_events(data: bytes, objects: list[ObjectHeader]) -> list[LogicTempoEvent]:
    """Logic's tempo track: the first event sequence that holds tempo events."""
    for obj in objects:
        if obj.tag != "EvSq":
            continue
        events = []
        for status, tick, pos, size in iter_records(data, obj):
            if status != _TEMPO_STATUS or size < _TEMPO_SIZE or data[pos + 23] != _TEMPO_CLASS:
                continue
            bpm = _u32(data, pos + 16) / 10_000
            if 1.0 <= bpm <= 1000.0:
                # signed: an event before bar 1 can fall before the SMPTE start
                stamp = struct.unpack_from("<i", data, pos + 24)[0]
                events.append(LogicTempoEvent(tick=tick, bpm=bpm, seconds=stamp / 2000))
        if events:
            return sorted(events, key=lambda event: event.tick)
    return []


def _placements(data: bytes, objects: list[ObjectHeader]) -> list[LogicPlacement]:
    placements = []
    for obj in objects:
        if obj.tag != "EvSq":
            continue
        for status, tick, pos, size in iter_records(data, obj):
            if status not in (_MIDI_REGION_STATUS, _AUDIO_REGION_STATUS) or size < _PLACEMENT_SIZE:
                continue
            is_midi = status == _MIDI_REGION_STATUS
            region_class = _MIDI_REGION_CLASS if is_midi else _AUDIO_REGION_CLASS
            if data[pos + 23] != _PLACEMENT_CLASS or data[pos + 39] != region_class:
                continue
            loop_span = _u32(data, pos + 28)
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
    # A project saved with undo history carries one extra Song object per
    # history step, each followed by the old copies of whatever that step
    # changed (arrangement sequences, regions, tracks, audio regions). Only
    # the objects before the second Song are the project's current state;
    # reading the rest would stack every past arrangement on top of it and let
    # stale names and trims overwrite current ones.
    song_offsets = [obj.offset for obj in objects if obj.tag == "Song"]
    if len(song_offsets) > 1:
        arrangement.history_steps = len(song_offsets) - 1
        objects = [obj for obj in objects if obj.offset < song_offsets[1]]
    arrangement.format_version = max(set(obj.version for obj in objects), key=[obj.version for obj in objects].count)
    arrangement.sequences = _sequences(data, objects)
    arrangement.track_names = _track_names(data, objects)
    arrangement.markers = _markers(data, objects)
    arrangement.placements = _placements(data, objects)
    arrangement.tempo_events = _tempo_events(data, objects)
    arrangement.audio_files = _audio_files(data, objects)
    arrangement.audio_regions = _audio_regions(data, objects)
    arrangement.mixer = _mixer(data, objects)
    arrangement.project_start_bar = _project_start_bar(
        data, arrangement.format_version, int(round(beats_per_bar * PPQ)) or PPQ * 4,
    )
    if len(data) >= 178 and _u32(data, 170) == _u32(data, 174) and 0 < _u32(data, 170) < 10_000_000:
        arrangement.tempo_bpm = _u32(data, 170) / 10_000
    return arrangement
