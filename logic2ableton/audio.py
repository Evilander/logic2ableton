"""Read uncompressed audio metadata without decoding entire source files."""

from __future__ import annotations

import math
import struct
from array import array
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


AUDIO_SUFFIXES = {".wav", ".aif", ".aiff", ".mp3", ".m4a", ".flac", ".ogg"}
BLOCK_FRAMES = 65536


@dataclass(frozen=True)
class AudioInfo:
    frame_count: int
    sample_rate: int
    channels: int
    sample_width: int
    data_offset: int
    encoding: str = "pcm"
    little_endian: bool = True

    @property
    def frame_width(self) -> int:
        return self.channels * self.sample_width


def extended_float80(data: bytes) -> float:
    if len(data) != 10:
        raise ValueError("Invalid AIFF sample rate")
    exponent = int.from_bytes(data[:2], "big")
    sign = -1 if exponent & 0x8000 else 1
    exponent &= 0x7fff
    if exponent == 0x7fff:
        raise ValueError("Invalid AIFF sample rate")
    try:
        return sign * math.ldexp(int.from_bytes(data[2:], "big"), exponent - 16383 - 63)
    except OverflowError as exc:
        raise ValueError("Invalid AIFF sample rate") from exc


def read_audio_info(path: Path) -> AudioInfo:
    """Support PCM/IEEE-float RIFF WAV and uncompressed AIFF/AIFC.

    Unsupported or malformed files raise instead of inventing a duration.
    Chunk payloads are skipped, keeping header inspection constant in memory.
    """
    with Path(path).open("rb") as handle:
        size = Path(path).stat().st_size
        header = handle.read(12)
        wav = header[:4] == b"RIFF" and header[8:] == b"WAVE"
        aiff = header[:4] == b"FORM" and header[8:] in (b"AIFF", b"AIFC")
        if not (wav or aiff):
            raise ValueError("Unsupported audio container (expected WAV or AIFF)")
        endian = "little" if wav else "big"
        boundary = min(size, int.from_bytes(header[4:8], endian) + 8)
        channels = rate = width = count = 0
        data_offset = data_size = 0
        encoding = "pcm" if wav else "signed-pcm"
        little_endian = wav
        while handle.tell() + 8 <= boundary:
            chunk = handle.read(8)
            length = int.from_bytes(chunk[4:], endian)
            start = handle.tell()
            if start + length > boundary:
                raise ValueError("Truncated audio chunk")
            if wav and chunk[:4] == b"fmt ":
                fmt = handle.read(min(length, 40))
                if len(fmt) < 16:
                    raise ValueError("Truncated WAV format")
                format_tag, channels, rate, _, alignment, bits = struct.unpack_from("<HHIIHH", fmt)
                if format_tag == 0xfffe and len(fmt) >= 40:
                    if fmt[26:40] != bytes.fromhex("000000001000800000aa00389b71"):
                        raise ValueError("Unsupported WAV subformat")
                    format_tag = int.from_bytes(fmt[24:26], "little")
                if format_tag not in (1, 3):
                    raise ValueError("Compressed WAV is not supported")
                encoding = "float" if format_tag == 3 else "pcm"
                width = bits // 8
                if bits % 8 or alignment != channels * width:
                    raise ValueError("Invalid WAV sample alignment")
            elif wav and chunk[:4] == b"data":
                data_offset, data_size = start, length
            elif aiff and chunk[:4] == b"COMM":
                comm = handle.read(min(length, 22))
                if len(comm) < 18:
                    raise ValueError("Truncated AIFF format")
                channels, count, bits = struct.unpack_from(">HIH", comm)
                rate = round(extended_float80(comm[8:18]))
                width = bits // 8
                if bits % 8:
                    raise ValueError("Unsupported AIFF sample width")
                if header[8:] == b"AIFC":
                    compression = comm[18:22]
                    if compression == b"sowt":
                        little_endian = True
                    elif compression in (b"fl32", b"FL32", b"fl64", b"FL64"):
                        encoding = "float"
                    elif compression not in (b"NONE", b"twos"):
                        raise ValueError("Compressed AIFF is not supported")
            elif aiff and chunk[:4] == b"SSND":
                if length < 8:
                    raise ValueError("Truncated AIFF sound chunk")
                offset = int.from_bytes(handle.read(4), "big")
                data_offset, data_size = start + 8 + offset, length - 8 - offset
            # Audio metadata is complete once both the format and samples are
            # found. Some Logic WAVs omit padding before their trailing cue/
            # bext chunks; those ancillary chunks do not affect PCM duration.
            if channels and rate and width and data_offset:
                break
            handle.seek(start + length + length % 2)
        if channels <= 0 or rate <= 0 or width not in ((4, 8) if encoding == "float" else (1, 2, 3, 4)):
            raise ValueError("Invalid or unsupported audio format")
        if not data_offset or data_size < 0:
            raise ValueError("Missing audio frames")
        # Like wave.Wave_read, count complete frames only. Logic comp bounces
        # can include a trailing pad byte in the declared data-chunk size.
        available = data_size // (channels * width)
        if aiff and count > available:
            raise ValueError("Truncated AIFF samples")
        return AudioInfo(available if wav else count, rate, channels, width, data_offset, encoding, little_endian)


@dataclass(frozen=True)
class DecodedAudio:
    """A seekable PCM view. Only requested source frames are loaded into memory."""

    path: Path
    info: AudioInfo

    @property
    def frame_rate(self) -> int:
        return self.info.sample_rate

    @property
    def channels(self) -> int:
        return self.info.channels

    @property
    def sample_width(self) -> int:
        return self.info.sample_width

    @property
    def encoding(self) -> str:
        return "float" if self.info.encoding == "float" else "pcm"

    @property
    def frame_width(self) -> int:
        return self.channels * self.sample_width

    @property
    def frame_count(self) -> int:
        return self.info.frame_count

    @property
    def frames(self) -> bytes:
        return self.read_frames(0, self.frame_count)

    def iter_frames(self) -> Iterator[bytes]:
        for start in range(0, self.frame_count, BLOCK_FRAMES):
            yield self.read_frames(start, min(BLOCK_FRAMES, self.frame_count - start))

    def read_frames(self, start: int, count: int) -> bytes:
        start = max(0, min(self.frame_count, start))
        count = max(0, min(count, self.frame_count - start))
        with self.path.open("rb") as handle:
            handle.seek(self.info.data_offset + start * self.info.frame_width)
            frames = handle.read(count * self.info.frame_width)
        if len(frames) != count * self.info.frame_width:
            raise ValueError(f"Source audio was truncated: {self.path.name}")
        if self.sample_width == 1 and self.info.encoding == "signed-pcm":
            return bytes(value ^ 0x80 for value in frames)
        if self.info.little_endian:
            return frames
        if self.sample_width in (2, 4, 8):
            samples = array({2: "h", 4: "i", 8: "q"}[self.sample_width])
            samples.frombytes(frames)
            samples.byteswap()
            return samples.tobytes()
        normalized = bytearray(len(frames))
        for offset in range(0, len(frames), 3):
            normalized[offset:offset + 3] = frames[offset:offset + 3][::-1]
        return bytes(normalized)


# Float audio is written as 32-bit: Logic and Pro Tools document 32-bit float
# files and neither documents 64-bit. It holds 24-bit PCM exactly.
FLOAT_EXPORT_WIDTH = 4
_FLOAT32_MAX = 3.4028234663852886e38


def export_format(audio: DecodedAudio) -> tuple[int, str]:
    """Sample width and encoding of a WAV written from ``audio``."""
    return (FLOAT_EXPORT_WIDTH, "float") if audio.encoding == "float" else (audio.sample_width, "pcm")


def pack_float(values: Iterable[float], width: int) -> bytes:
    """Pack little-endian floats; finite values past the 32-bit range are clamped to it."""
    if width != 4:
        values = list(values)
        return struct.pack(f"<{len(values)}d", *values)
    values = [max(-_FLOAT32_MAX, min(_FLOAT32_MAX, value)) if math.isfinite(value) else value for value in values]
    return struct.pack(f"<{len(values)}f", *values)


def samples_as_float(frames: bytes, sample_width: int, encoding: str, out_width: int) -> bytes:
    """Promote little-endian samples to float for mixing, without clipping them."""
    count = len(frames) // sample_width
    if encoding == "float":
        if sample_width == out_width:
            return frames
        values = struct.unpack(f"<{count}{'f' if sample_width == 4 else 'd'}", frames)
    elif sample_width == 1:
        values = [(value - 128) / 128 for value in frames]
    elif sample_width in (2, 4):
        scale = 1 << (sample_width * 8 - 1)
        values = [value / scale for value in struct.unpack(f"<{count}{'h' if sample_width == 2 else 'i'}", frames)]
    else:
        scale = 1 << (sample_width * 8 - 1)
        values = [
            int.from_bytes(frames[offset:offset + sample_width], "little", signed=True) / scale
            for offset in range(0, len(frames), sample_width)
        ]
    return pack_float(values, out_width)


def place_mono_channel(frames: bytes, sample_width: int, channel: int) -> bytes:
    """Put a mono source in one stereo channel, leaving the other silent."""
    if channel not in (0, 1):
        raise ValueError("Stereo channel must be 0 (left) or 1 (right)")
    silence = b"\x80" if sample_width == 1 else b"\x00" * sample_width
    result = bytearray(len(frames) * 2)
    for offset in range(0, len(frames), sample_width):
        sample = frames[offset:offset + sample_width]
        result[offset * 2:(offset + sample_width) * 2] = sample + silence if channel == 0 else silence + sample
    return bytes(result)


def build_bext_chunk(time_reference_samples: int, originator_reference: str = "logic2ableton") -> bytes:
    # BWF v0: description, originator, reference, date/time, sample timestamp,
    # version and 254 reserved bytes. The fixed section is 602 bytes.
    now = datetime.now(UTC)
    payload = bytearray(602)
    payload[:256] = b"Session transfer timestamp".ljust(256, b"\x00")
    payload[256:288] = b"logic2ableton".ljust(32, b"\x00")
    payload[288:320] = originator_reference.encode("ascii")[:32].ljust(32, b"\x00")
    payload[320:330] = now.strftime("%Y-%m-%d").encode("ascii")
    payload[330:338] = now.strftime("%H:%M:%S").encode("ascii")
    struct.pack_into("<Q", payload, 338, max(0, time_reference_samples))
    return bytes(payload)


def write_pcm_wav(
    destination: Path, *, sample_rate: int, channels: int, sample_width: int,
    frames: bytes | Iterable[bytes], time_reference_samples: int,
    originator_reference: str = "logic2ableton",
    encoding: str = "pcm",
) -> None:
    """Stream a PCM or IEEE-float Broadcast WAV without changing sample values."""
    if encoding not in ("pcm", "float") or sample_width not in ((4, 8) if encoding == "float" else (1, 2, 3, 4)):
        raise ValueError("Unsupported WAV output sample format")
    bext = build_bext_chunk(time_reference_samples, originator_reference)
    alignment = channels * sample_width
    fmt = struct.pack("<HHIIHH", 3 if encoding == "float" else 1, channels,
                      sample_rate, sample_rate * alignment, alignment, sample_width * 8)
    if encoding == "float":
        fmt += b"\x00\x00"  # WAVEFORMATEX: no extra format bytes
    blocks = (frames,) if isinstance(frames, bytes) else frames
    with destination.open("wb") as handle:
        handle.write(b"RIFF\0\0\0\0WAVEfmt " + struct.pack("<I", len(fmt)) + fmt)
        fact_offset = None
        if encoding == "float":
            handle.write(b"fact" + struct.pack("<I", 4))
            fact_offset = handle.tell()
            handle.write(b"\0\0\0\0")
        handle.write(b"bext" + struct.pack("<I", len(bext)) + bext + b"data\0\0\0\0")
        data_start = handle.tell()
        count = 0
        for block in blocks:
            if len(block) % alignment:
                raise ValueError("Unaligned PCM output")
            count += len(block)
            if data_start + count > 0xffffffff:
                raise ValueError("Rendered WAV exceeds the 4 GiB RIFF limit; split this session before exporting")
            handle.write(block)
        if count % 2:
            handle.write(b"\0")
        riff_size = handle.tell() - 8
        handle.seek(4)
        handle.write(struct.pack("<I", riff_size))
        handle.seek(data_start - 4)
        handle.write(struct.pack("<I", count))
        if fact_offset is not None:
            handle.seek(fact_offset)
            handle.write(struct.pack("<I", count // alignment))
