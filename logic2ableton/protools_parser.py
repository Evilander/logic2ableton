"""Parse Avid Pro Tools session files (.ptx / .pts / .ptf) into a session model.

The format is undocumented; this is a clean-room Python implementation built
from the byte-level facts documented by the ptformat project
(https://github.com/zamaudio/ptformat) and verified against real sessions.

Container layout:
- 20-byte plaintext header: 0x03, the ASCII bitcode "0010111100101011",
  an endianness flag at 0x11, XOR scheme id at 0x12, XOR seed at 0x13.
- The rest of the file is XOR-obfuscated with a 256-byte key stream
  ``key[i] = (i * delta) & 0xFF``; PT5-9 indexes the key by byte offset,
  PT10+ indexes it by 4 KiB page (``(offset >> 12) & 0xFF``).
- Content is a tree of blocks: 0x5A marker, u16 block type, u32 block size,
  u16 content type, then data (which may itself contain child blocks).

Audio timeline units are samples; MIDI units are Pro Tools ticks
(960,000 per quarter note) offset from the epoch 0xE8D4A51000.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

_BITCODE = b"0010111100101011"
_ZMARK = 0x5A

# Pro Tools MIDI tick resolution (ticks per quarter note) and the tick value
# that represents "position zero" in MIDI event/region records.
PT_TICKS_PER_QUARTER = 960_000
_PT_ZERO_TICKS = 0xE8D4A51000

_AUDIO_EXTENSIONS = (".wav", ".aif", ".aiff", ".mxf")
_WAV_TYPE_CODES = (b"WAVE", b"EVAW", b"AIFF", b"FFIA", b"fFXM", b"MXFf")


@dataclass
class ProToolsAudioFile:
    index: int
    filename: str
    length_frames: int = 0


@dataclass
class ProToolsRegion:
    """An audio clip: plays source[offset : offset+length] at timeline[start]."""

    name: str
    index: int
    start_samples: int
    offset_samples: int
    length_samples: int
    wav_index: int
    filename: str = ""


@dataclass
class ProToolsTrack:
    name: str
    index: int
    regions: list[ProToolsRegion] = field(default_factory=list)


@dataclass
class ProToolsMidiNote:
    pitch: int
    start_beats: float  # absolute arrangement position in quarter-note beats
    duration_beats: float
    velocity: int


@dataclass
class ProToolsMidiTrack:
    name: str
    notes: list[ProToolsMidiNote] = field(default_factory=list)

    @property
    def note_count(self) -> int:
        return len(self.notes)


@dataclass
class ProToolsSession:
    name: str
    path: Path
    version: int
    sample_rate: int
    audio_files: list[ProToolsAudioFile]
    tracks: list[ProToolsTrack]
    midi_tracks: list[ProToolsMidiTrack]
    compatibility_warnings: list[str] = field(default_factory=list)

    @property
    def total_regions(self) -> int:
        return sum(len(track.regions) for track in self.tracks)

    @property
    def total_midi_notes(self) -> int:
        return sum(track.note_count for track in self.midi_tracks)


@dataclass
class _Block:
    block_type: int
    content_type: int
    offset: int  # offset of the data area (marker position + 7)
    size: int
    children: list["_Block"] = field(default_factory=list)


class ProToolsParseError(ValueError):
    """Raised when a file is not a decodable Pro Tools session."""


def _xor_delta(seed: int, multiplier: int, negative: bool) -> int:
    for i in range(256):
        if (i * multiplier) & 0xFF == seed:
            return (-i) & 0xFF if negative else i
    return 0


def _deobfuscate(raw: bytes) -> bytes:
    """Reverse the session XOR obfuscation. The first 20 bytes are plaintext."""
    if len(raw) < 0x14:
        raise ProToolsParseError("File too small to be a Pro Tools session.")
    scheme = raw[0x12]
    seed = raw[0x13]
    if scheme == 0x01:  # Pro Tools 5-9
        delta = _xor_delta(seed, 53, negative=False)
        index_of = lambda i: i & 0xFF  # noqa: E731
    elif scheme == 0x05:  # Pro Tools 10+
        delta = _xor_delta(seed, 11, negative=True)
        index_of = lambda i: (i >> 12) & 0xFF  # noqa: E731
    else:
        raise ProToolsParseError(f"Unknown Pro Tools XOR scheme {scheme:#04x}.")

    key = bytes((i * delta) & 0xFF for i in range(256))
    out = bytearray(raw)
    for i in range(0x14, len(raw)):
        out[i] ^= key[index_of(i)]
    return bytes(out)


class _SessionReader:
    def __init__(self, data: bytes):
        self.data = data
        self.big_endian = bool(data[0x11])

    def u16(self, pos: int) -> int:
        return struct.unpack_from(">H" if self.big_endian else "<H", self.data, pos)[0]

    def u32(self, pos: int) -> int:
        return struct.unpack_from(">I" if self.big_endian else "<I", self.data, pos)[0]

    def u40(self, pos: int) -> int:
        chunk = self.data[pos:pos + 5]
        return int.from_bytes(chunk, "big" if self.big_endian else "little")

    def u64(self, pos: int) -> int:
        return struct.unpack_from(">Q" if self.big_endian else "<Q", self.data, pos)[0]

    def string(self, pos: int) -> str:
        length = self.u32(pos)
        if length > len(self.data) - pos - 4:
            return ""
        return self.data[pos + 4:pos + 4 + length].decode("utf-8", errors="replace")

    # -- block tree -------------------------------------------------------

    def parse_block(self, pos: int, limit: int) -> _Block | None:
        data = self.data
        if pos + 9 > limit or data[pos] != _ZMARK:
            return None
        block_type = self.u16(pos + 1)
        size = self.u32(pos + 3)
        content_type = self.u16(pos + 7)
        offset = pos + 7
        if size + offset > limit or block_type & 0xFF00:
            return None

        block = _Block(block_type=block_type, content_type=content_type, offset=offset, size=size)
        i = 1
        end = offset + size
        while pos + i < end:
            child = self.parse_block(pos + i, end)
            if child is not None:
                block.children.append(child)
                i += child.size + 7
            else:
                i += 1
        return block

    def parse_all_blocks(self) -> list[_Block]:
        blocks: list[_Block] = []
        i = 0x14
        n = len(self.data)
        while i < n:
            block = self.parse_block(i, n)
            if block is not None:
                blocks.append(block)
                i += block.size + 7
            else:
                i += 1
        return blocks


def _iter_blocks(blocks: list[_Block], content_type: int):
    for block in blocks:
        if block.content_type == content_type:
            yield block


def _three_point(reader: _SessionReader, j: int) -> tuple[int, int, int, int]:
    """Decode a variable-width (start, offset, length) triple.

    A descriptor at ``j`` declares each value's byte width in the high nibble
    of three consecutive bytes; the values follow, always little-endian.
    Returns (start, offset, length, bytes_consumed_after_j).
    """
    data = reader.data
    if reader.big_endian:
        offset_w = (data[j + 4] & 0xF0) >> 4
        length_w = (data[j + 3] & 0xF0) >> 4
        start_w = (data[j + 2] & 0xF0) >> 4
    else:
        offset_w = (data[j + 1] & 0xF0) >> 4
        length_w = (data[j + 2] & 0xF0) >> 4
        start_w = (data[j + 3] & 0xF0) >> 4

    def read_le(pos: int, width: int) -> int:
        if width < 1 or width > 5:
            return 0
        return int.from_bytes(data[pos:pos + width], "little")

    k = j
    offset = read_le(k + 5, offset_w)
    k += offset_w
    length = read_le(k + 5, length_w)
    k += length_w
    start = read_le(k + 5, start_w)
    k += start_w
    return start, offset, length, (k + 5) - j


def _parse_version(reader: _SessionReader) -> int:
    data = reader.data
    block = reader.parse_block(0x1F, len(data))
    if block is None:
        for pos in (0x40, 0x3D):
            if data[pos]:
                return data[pos]
        return data[0x3A] + 2
    if block.content_type == 0x0003:
        skip = len(reader.string(block.offset + 3)) + 8
        return reader.u32(block.offset + 3 + skip)
    if block.content_type == 0x2067:
        return 2 + reader.u32(block.offset + 20)
    return 0


def _parse_sample_rate(reader: _SessionReader, blocks: list[_Block]) -> int:
    for block in _iter_blocks(blocks, 0x1028):
        return reader.u32(block.offset + 4)
    return 0


def _parse_audio_files(reader: _SessionReader, blocks: list[_Block], version: int) -> list[ProToolsAudioFile]:
    files: list[ProToolsAudioFile] = []
    for parent in _iter_blocks(blocks, 0x1004):
        n_wavs = reader.u32(parent.offset + 2)
        for child in parent.children:
            if child.content_type != 0x103A:
                continue
            pos = child.offset + 11
            index = 0
            while pos < child.offset + child.size and index < n_wavs:
                name = reader.string(pos)
                pos += len(name.encode("utf-8", errors="replace")) + 4
                type_code = reader.data[pos:pos + 4]
                pos += 9
                if ".grp" in name or name in ("Audio Files", "Fade Files"):
                    continue
                if version >= 10 and type_code[:1] == b"\x00":
                    if not name.lower().endswith(_AUDIO_EXTENSIONS):
                        continue
                elif type_code not in _WAV_TYPE_CODES:
                    continue
                files.append(ProToolsAudioFile(index=index, filename=name))
                index += 1
        # Frame lengths live in nested WAV metadata blocks, in file order.
        lengths: list[int] = []
        for child in parent.children:
            if child.content_type != 0x1003:
                continue
            for grand in child.children:
                if grand.content_type == 0x1001:
                    lengths.append(reader.u64(grand.offset + 8))
        for wav, length in zip(files, lengths):
            wav.length_frames = length
    return files


def _parse_regions(reader: _SessionReader, blocks: list[_Block]) -> list[ProToolsRegion]:
    """Audio region definitions (PT10+ uses 0x262a/0x2629; PT5-9 0x100b/0x1008)."""
    regions: list[ProToolsRegion] = []
    index = 0
    for list_type, entry_type in ((0x262A, 0x2629), (0x100B, 0x1008)):
        for parent in _iter_blocks(blocks, list_type):
            for child in parent.children:
                if child.content_type != entry_type:
                    continue
                j = child.offset + 11
                name = reader.string(j)
                j += len(name.encode("utf-8", errors="replace")) + 4
                start, offset, length, _ = _three_point(reader, j)
                wav_index = -1
                if child.children:
                    inner = child.children[0]
                    wav_index = reader.u32(inner.offset + inner.size)
                regions.append(
                    ProToolsRegion(
                        name=name,
                        index=index,
                        start_samples=start,
                        offset_samples=offset,
                        length_samples=length,
                        wav_index=wav_index,
                    )
                )
                index += 1
        if regions:
            break
    return regions


def _parse_track_placements(
    reader: _SessionReader,
    blocks: list[_Block],
    regions: list[ProToolsRegion],
) -> list[ProToolsTrack]:
    """Region-onto-track placements (PT8+ map: 0x1054 > 0x1052 > 0x1050 > 0x104f)."""
    by_index = {region.index: region for region in regions}
    tracks: list[ProToolsTrack] = []
    for parent in _iter_blocks(blocks, 0x1054):
        for lane, child in enumerate(c for c in parent.children if c.content_type == 0x1052):
            name = reader.string(child.offset + 2)
            track = ProToolsTrack(name=name, index=lane)
            for entry in child.children:
                if entry.content_type != 0x1050:
                    continue
                if reader.data[entry.offset + 46] == 0x01:
                    continue  # crossfade render, not a user clip
                for placement in entry.children:
                    if placement.content_type != 0x104F:
                        continue
                    j = placement.offset + 4
                    region_index = reader.u32(j)
                    start = reader.u32(j + 5)
                    region = by_index.get(region_index)
                    if region is None:
                        continue
                    placed = ProToolsRegion(
                        name=region.name,
                        index=region.index,
                        start_samples=start,
                        offset_samples=region.offset_samples,
                        length_samples=region.length_samples,
                        wav_index=region.wav_index,
                        filename=region.filename,
                    )
                    track.regions.append(placed)
            tracks.append(track)
    return tracks


def _parse_midi(reader: _SessionReader, blocks: list[_Block]) -> list[ProToolsMidiTrack]:
    # 1) Raw event chunks anchored by the MdNLB marker inside 0x2000 blocks.
    chunks: list[list[tuple[int, int, int, int]]] = []  # (pos_ticks, note, len_ticks, velocity)
    data = reader.data
    for block in _iter_blocks(blocks, 0x2000):
        k = block.offset
        end = block.offset + block.size
        while k + 35 < end:
            found = data.find(b"MdNLB", k, len(data))
            if found < 0:
                break
            k = found + 11
            n_events = reader.u32(k)
            k += 4
            zero_ticks = reader.u40(k)
            events: list[tuple[int, int, int, int]] = []
            for _ in range(n_events):
                if k + 35 > len(data):
                    break
                pos = reader.u40(k) - zero_ticks
                note = data[k + 8]
                length = reader.u40(k + 9)
                velocity = data[k + 17]
                events.append((pos, note, length, velocity))
                k += 35
            chunks.append(events)

    if not chunks:
        return []

    # 2) MIDI region table maps region entries to chunk indices.
    midi_regions: dict[int, list[tuple[int, int, int, int]]] = {}
    region_number = 0
    for list_type, entry_type in ((0x2634, 0x2633), (0x2002, 0x2001)):
        for parent in _iter_blocks(blocks, list_type):
            for child in parent.children:
                if child.content_type != entry_type:
                    continue
                for entry in child.children:
                    if entry.content_type not in (0x1007, 0x2628):
                        continue
                    chunk_index = reader.u32(entry.offset + entry.size)
                    if 0 <= chunk_index < len(chunks):
                        midi_regions[region_number] = chunks[chunk_index]
                    region_number += 1

    # 3) MIDI track names (0x2519 > 0x251a).
    names: list[str] = []
    for parent in _iter_blocks(blocks, 0x2519):
        for child in parent.children:
            if child.content_type == 0x251A:
                names.append(reader.string(child.offset + 4))

    # 4) Region-onto-MIDI-track placements (0x1058 > 0x1057 > 0x1056 > 0x104f).
    tracks: list[ProToolsMidiTrack] = []
    for parent in _iter_blocks(blocks, 0x1058):
        for lane, child in enumerate(c for c in parent.children if c.content_type == 0x1057):
            name = names[lane] if lane < len(names) else f"MIDI {lane + 1}"
            track = ProToolsMidiTrack(name=name)
            for entry in child.children:
                if entry.content_type != 0x1056:
                    continue
                for placement in entry.children:
                    if placement.content_type != 0x104F:
                        continue
                    j = placement.offset + 4
                    region_index = reader.u32(j)
                    start_raw = reader.u40(j + 5)
                    region_start_ticks = abs(start_raw - _PT_ZERO_TICKS)
                    events = midi_regions.get(region_index)
                    if events is None:
                        continue
                    for pos, note, length, velocity in events:
                        if not (0 <= note <= 127) or length <= 0:
                            continue
                        track.notes.append(
                            ProToolsMidiNote(
                                pitch=note,
                                start_beats=(region_start_ticks + pos) / PT_TICKS_PER_QUARTER,
                                duration_beats=length / PT_TICKS_PER_QUARTER,
                                velocity=max(1, min(127, velocity)),
                            )
                        )
            track.notes.sort(key=lambda n: (n.start_beats, n.pitch))
            if track.notes:
                tracks.append(track)
    return tracks


def parse_protools_session(ptx_path: Path) -> ProToolsSession:
    """Parse a Pro Tools session file into a ProToolsSession model."""
    ptx_path = Path(ptx_path)
    raw = ptx_path.read_bytes()
    if not raw or raw[0] != 0x03 or raw[1:17] != _BITCODE:
        raise ProToolsParseError(
            f"'{ptx_path.name}' does not look like a Pro Tools session (bad signature)."
        )

    data = _deobfuscate(raw)
    reader = _SessionReader(data)
    version = _parse_version(reader)
    blocks = reader.parse_all_blocks()

    sample_rate = _parse_sample_rate(reader, blocks)
    warnings: list[str] = []
    if not (44100 <= sample_rate <= 192000):
        warnings.append(
            f"Session sample rate {sample_rate or 'unknown'} could not be validated; assuming 48000."
        )
        sample_rate = 48000

    audio_files = _parse_audio_files(reader, blocks, version)
    regions = _parse_regions(reader, blocks)
    files_by_index = {wav.index: wav for wav in audio_files}
    for region in regions:
        wav = files_by_index.get(region.wav_index)
        if wav is not None:
            region.filename = wav.filename
    tracks = _parse_track_placements(reader, blocks, regions)
    midi_tracks = _parse_midi(reader, blocks)

    placed = [region for track in tracks for region in track.regions]
    unnamed = [region for region in placed if not region.filename]
    if unnamed:
        warnings.append(
            f"{len(unnamed)} placed clip(s) could not be matched to a source audio file "
            "and will be skipped."
        )

    return ProToolsSession(
        name=ptx_path.stem,
        path=ptx_path,
        version=version,
        sample_rate=sample_rate,
        audio_files=audio_files,
        tracks=tracks,
        midi_tracks=midi_tracks,
        compatibility_warnings=warnings,
    )
