import json
import math
import plistlib
import re
import struct
from pathlib import Path

import bisect
from dataclasses import replace

from logic2ableton.audio import read_audio_info
from logic2ableton.logic_project_data import (
    PPQ,
    SEQUENCE_ORIGIN_TICKS,
    LogicArrangement,
    LogicChannelStrip,
    decode_project_data,
)
from logic2ableton.models import (
    AudioFileRef,
    HardwareOutput,
    LogicMidiNote,
    LogicMidiRegion,
    LogicMidiTrack,
    LogicProject,
    PluginInstance,
    TrackGroup,
    TrackMixerState,
    parse_audio_filename,
)
from logic2ableton.smf import MIDI_TICKS_PER_QUARTER
from logic2ableton.timeline import (
    MAX_TEMPO_BPM,
    MIN_TEMPO_BPM,
    TempoEvent,
    TempoMap,
    Timeline,
    TimelineMarker,
    beats_per_bar,
)


def parse_project_info(logicx_path: Path) -> dict:
    """Parse Resources/ProjectInformation.plist."""
    plist_path = logicx_path / "Resources" / "ProjectInformation.plist"
    with open(plist_path, "rb") as f:
        data = plistlib.load(f)
    return {
        "name": data.get("VariantNames", {}).get("0", logicx_path.stem),
        "last_saved_from": data.get("LastSavedFrom", ""),
        "variant_names": data.get("VariantNames", {}),
        "active_variant": data.get("ActiveVariant", 0),
        "bundle_version": data.get("BundleVersion", ""),
    }


def parse_metadata(logicx_path: Path, alternative: int = 0) -> dict:
    """Parse Alternatives/{N}/MetaData.plist."""
    plist_path = logicx_path / "Alternatives" / f"{alternative:03d}" / "MetaData.plist"
    with open(plist_path, "rb") as f:
        data = plistlib.load(f)
    # Software-instrument file references signal MIDI/instrument tracks; reverb
    # impulse responses are an audio-effect resource, not a MIDI-track indicator.
    instrument_keys = ("SamplerInstrumentsFiles", "QuicksamplerFiles", "AlchemyFiles", "UltrabeatFiles")
    software_instrument_files = sum(len(data.get(key, [])) for key in instrument_keys)
    return {
        "tempo": data.get("BeatsPerMinute", 120.0),
        "time_sig_numerator": data.get("SongSignatureNumerator", 4),
        "time_sig_denominator": data.get("SongSignatureDenominator", 4),
        "sample_rate": data.get("SampleRate", 44100),
        "num_tracks": data.get("NumberOfTracks", 0),
        "song_key": data.get("SongKey", ""),
        "song_gender_key": data.get("SongGenderKey", ""),
        "audio_files": [f.replace("Audio Files/", "") for f in data.get("AudioFiles", [])],
        "unused_audio_files": [f.replace("Audio Files/", "") for f in data.get("UnusedAudioFiles", [])],
        "has_audio_membership": "AudioFiles" in data,
        "software_instrument_files": software_instrument_files,
    }


def discover_alternatives(logicx_path: Path) -> list[int]:
    """Return sorted alternative indices that contain project data.

    Logic numbers alternative folders arbitrarily (a project's only
    alternative may be ``004``, not ``000``), so callers must discover what
    actually exists rather than assuming a fixed index.
    """
    alt_dir = logicx_path / "Alternatives"
    if not alt_dir.exists():
        return []
    found: list[int] = []
    for child in alt_dir.iterdir():
        if not child.is_dir() or not child.name.isdigit():
            continue
        if (child / "MetaData.plist").exists() or (child / "ProjectData").exists():
            found.append(int(child.name))
    return sorted(found)


def resolve_alternative(
    logicx_path: Path,
    requested: int | None = None,
    active_variant: int | None = None,
) -> int:
    """Pick which alternative folder to parse.

    ``requested`` (e.g. CLI ``--alternative``) wins when present. Otherwise we
    prefer the project's active variant, then fall back to the lowest-numbered
    alternative that exists on disk.
    """
    available = discover_alternatives(logicx_path)
    if not available:
        raise FileNotFoundError(
            f"No Logic alternatives found under {logicx_path / 'Alternatives'}. "
            "The project may be empty or saved in an unsupported format."
        )
    if requested is not None:
        if requested in available:
            return requested
        available_str = ", ".join(f"{n:03d}" for n in available)
        raise FileNotFoundError(
            f"Alternative {requested:03d} not found in {logicx_path.name}. "
            f"Available alternative(s): {available_str}"
        )
    if active_variant is not None and active_variant in available:
        return active_variant
    return available[0]


def _int_to_4cc(n: int) -> str:
    """Convert an integer to a 4-character code (FourCC)."""
    try:
        return struct.pack(">I", n).decode("ascii", errors="replace")
    except (struct.error, ValueError):
        return str(n)


def _read_project_data(logicx_path: Path, alternative: int = 0) -> bytes:
    """Read the ProjectData binary file. Returns empty bytes if missing."""
    project_data_path = logicx_path / "Alternatives" / f"{alternative:03d}" / "ProjectData"
    if not project_data_path.exists():
        return b""
    with open(project_data_path, "rb") as f:
        return f.read()


def extract_plugins(logicx_path: Path, alternative: int = 0, *, _data: bytes | None = None) -> list[PluginInstance]:
    """Extract plugin instances from embedded plists in ProjectData."""
    data = _data if _data is not None else _read_project_data(logicx_path, alternative)
    if not data:
        return []

    plugins = []
    for match in re.finditer(rb"<\?xml version", data):
        start = match.start()
        end_marker = data.find(b"</plist>", start)
        if end_marker == -1:
            continue
        end = end_marker + len(b"</plist>")
        try:
            parsed = plistlib.loads(data[start:end])
        except Exception:
            continue
        if not isinstance(parsed, dict) or "name" not in parsed:
            continue

        mfr_int = parsed.get("manufacturer", 0)
        subtype_int = parsed.get("subtype", 0)
        type_int = parsed.get("type", 0)

        plugins.append(PluginInstance(
            name=parsed["name"],
            au_type=_int_to_4cc(type_int) if isinstance(type_int, int) else str(type_int),
            au_subtype=_int_to_4cc(subtype_int) if isinstance(subtype_int, int) else str(subtype_int),
            au_manufacturer=_int_to_4cc(mfr_int) if isinstance(mfr_int, int) else str(mfr_int),
            is_waves="Waves_XPst" in parsed,
            raw_plist=parsed,
        ))
    return plugins


# Logic stores arrangement MIDI inside EvSq ("qSvE") chunks of ProjectData. Each
# note is a fixed record anchored by this 15-byte signature that follows the
# [velocity][pitch] bytes; the format was reverse-engineered against ground-truth
# projects (verified pitch, velocity, position, and duration).
_MIDI_NOTE_SIGNATURE = bytes([0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0x89, 0, 0, 0, 0])

# Bar 1 of the arrangement sits at tick 38400 (40 quarter notes at 960 PPQ) in
# every ground-truth project used to crack the note format. Positions at or
# after this anchor convert to absolute arrangement beats.
_LOGIC_BAR1_ORIGIN_TICKS = 38400


def extract_midi_notes(
    logicx_path: Path,
    alternative: int = 0,
    *,
    _data: bytes | None = None,
    warnings: list[str] | None = None,
) -> list[LogicMidiTrack]:
    """Extract MIDI note sequences from Logic's binary ProjectData.

    Notes are grouped by the EvSq sequence (region) they belong to. When every
    note sits at or after Logic's bar-1 tick anchor, notes are placed at their
    absolute arrangement positions; otherwise placement falls back to
    relative-to-earliest-note (and a warning is appended when a ``warnings``
    list is supplied). Returns one LogicMidiTrack per note-bearing sequence.
    """
    data = _data if _data is not None else _read_project_data(logicx_path, alternative)
    if not data:
        return []

    seq_offsets: list[int] = []
    pos = 0
    while (pos := data.find(b"qSvE", pos)) >= 0:
        seq_offsets.append(pos)
        pos += 4

    raw: dict[int, list[tuple[int, int, int, int]]] = {}  # seq_offset -> (pitch, vel, pos_ticks, dur_ticks)
    i = 0
    while (i := data.find(_MIDI_NOTE_SIGNATURE, i)) >= 0:
        i += 15
        if i < 24 or i + 4 > len(data):
            continue
        velocity = data[i - 17]
        pitch = data[i - 16]
        duration = struct.unpack_from("<I", data, i)[0]
        position = struct.unpack_from("<I", data, i - 24)[0]
        # Guard against false signature matches with out-of-range values.
        if not (0 <= pitch <= 127 and 1 <= velocity <= 127):
            continue
        if duration <= 0 or duration > 1_000_000_000 or position > 1_000_000_000:
            continue
        seq_index = bisect.bisect_right(seq_offsets, i) - 1
        seq_key = seq_offsets[seq_index] if seq_index >= 0 else -1
        raw.setdefault(seq_key, []).append((pitch, velocity, position, duration))

    if not raw:
        return []

    global_min = min(p for notes in raw.values() for (_, _, p, _) in notes)
    if global_min >= _LOGIC_BAR1_ORIGIN_TICKS:
        origin = _LOGIC_BAR1_ORIGIN_TICKS
    else:
        origin = global_min
        if warnings is not None:
            warnings.append(
                "MIDI notes were found before Logic's expected bar-1 anchor, so MIDI regions "
                "were placed relative to the earliest note instead of at absolute arrangement "
                "positions - verify their placement after import."
            )
    tracks: list[LogicMidiTrack] = []
    for index, seq_key in enumerate(sorted(raw), start=1):
        notes = []
        for pitch, velocity, position, duration in raw[seq_key]:
            notes.append(
                LogicMidiNote(
                    pitch=pitch,
                    start_beats=(position - origin) / MIDI_TICKS_PER_QUARTER,
                    duration_beats=duration / MIDI_TICKS_PER_QUARTER,
                    velocity=velocity,
                )
            )
        notes.sort(key=lambda note: (note.start_beats, note.pitch))
        tracks.append(LogicMidiTrack(name=f"MIDI {index}", notes=notes))
    return tracks


def _get_bwf_time_reference(file_path: Path) -> int | None:
    """Read BWF TimeReference from a WAV file's bext chunk.

    Returns the sample count (uint64) or None if no bext chunk exists.
    """
    try:
        with open(file_path, "rb") as f:
            riff = f.read(4)
            if riff != b"RIFF":
                return None
            file_size = struct.unpack("<I", f.read(4))[0]
            if f.read(4) != b"WAVE":
                return None
            file_end = min(file_size + 8, file_path.stat().st_size)
            while f.tell() + 8 <= file_end:
                chunk_id = f.read(4)
                if len(chunk_id) < 4:
                    break
                chunk_size = struct.unpack("<I", f.read(4))[0]
                if f.tell() + chunk_size > file_end:
                    return None
                if chunk_id == b"bext" and chunk_size >= 346:
                    chunk_data = f.read(346)
                    return struct.unpack_from("<Q", chunk_data, 338)[0]
                f.seek(chunk_size, 1)
                if chunk_size % 2 and f.tell() < file_end:
                    # Logic sometimes omits odd-byte padding before cue/bext.
                    # The same convention occurs in its AIFF recordings.
                    if f.read(1) != b"\x00":
                        f.seek(-1, 1)
    except Exception:
        pass
    return None


def _decode_ieee_extended_80(raw: bytes) -> int:
    """Decode AIFF 80-bit extended float (sample rate) into integer Hz."""
    if len(raw) != 10:
        return 44100
    exponent = ((raw[0] & 0x7F) << 8) | raw[1]
    mantissa = int.from_bytes(raw[2:10], "big")
    if exponent == 0 and mantissa == 0:
        return 0
    sample_rate = mantissa * (2.0 ** (exponent - 16383 - 63))
    return int(sample_rate)


def _get_audio_sample_rate(file_path: Path, default: int = 44100) -> int:
    """Read sample rate from WAV/AIFF headers."""
    ext = file_path.suffix.lower()
    try:
        with open(file_path, "rb") as f:
            if ext == ".wav":
                if f.read(4) != b"RIFF":
                    return default
                riff_size = struct.unpack("<I", f.read(4))[0]
                if f.read(4) != b"WAVE":
                    return default
                while f.tell() < riff_size + 8:
                    chunk_id = f.read(4)
                    if len(chunk_id) < 4:
                        break
                    chunk_size = struct.unpack("<I", f.read(4))[0]
                    if chunk_id == b"fmt " and chunk_size >= 8:
                        fmt = f.read(chunk_size)
                        return struct.unpack_from("<I", fmt, 4)[0]
                    f.seek(chunk_size + (chunk_size % 2), 1)
                return default

            if ext in (".aif", ".aiff"):
                if f.read(4) != b"FORM":
                    return default
                form_size = struct.unpack(">I", f.read(4))[0]
                if f.read(4) not in (b"AIFF", b"AIFC"):
                    return default

                file_end = form_size + 8
                while f.tell() < file_end:
                    chunk_id = f.read(4)
                    if len(chunk_id) < 4:
                        break
                    chunk_size = struct.unpack(">I", f.read(4))[0]
                    data_start = f.tell()
                    if chunk_id == b"COMM" and chunk_size >= 18:
                        data = f.read(chunk_size)
                        return _decode_ieee_extended_80(data[8:18])

                    f.seek(data_start + chunk_size)
                    if chunk_size % 2:
                        # Some Logic AIFF files omit odd-byte padding; only consume if present.
                        maybe_pad = f.read(1)
                        if maybe_pad != b"\x00":
                            f.seek(-1, 1)
                return default
    except Exception:
        return default
    return default


def _get_aiff_timestamp(file_path: Path) -> tuple[int | None, int]:
    """Read timeline position from an AIFF file's MARK chunk.

    Logic Pro embeds markers in AIFF recordings:
    - 'Timestamp: N' marker: absolute sample position (like BWF TimeReference)
    - 'Start' marker: content start offset within the file (after pre-roll)

    Returns (timestamp, start_offset) where timestamp is the absolute SMPTE
    position of frame 0, and start_offset is the content start within the file.
    Both in samples. Returns (None, 0) if no timestamp found.
    """
    timestamp = None
    start_offset = 0
    try:
        with open(file_path, "rb") as f:
            if f.read(4) != b"FORM":
                return None, 0
            file_size = struct.unpack(">I", f.read(4))[0]
            aiff_id = f.read(4)  # AIFF or AIFC
            if aiff_id not in (b"AIFF", b"AIFC"):
                return None, 0

            file_end = file_size + 8
            while f.tell() < file_end:
                chunk_id = f.read(4)
                if len(chunk_id) < 4:
                    break
                chunk_size = struct.unpack(">I", f.read(4))[0]
                data_start = f.tell()

                if chunk_id == b"MARK":
                    data = f.read(chunk_size)
                    if len(data) >= 2:
                        num_markers = struct.unpack(">H", data[0:2])[0]
                        offset = 2
                        for _ in range(num_markers):
                            if offset + 7 > len(data):
                                break
                            _marker_id = struct.unpack(">H", data[offset:offset+2])[0]
                            position = struct.unpack(">I", data[offset+2:offset+6])[0]
                            name_len = data[offset+6]
                            if offset + 7 + name_len > len(data):
                                break
                            name = data[offset+7:offset+7+name_len].decode("ascii", errors="replace")
                            # AIFF pstrings are padded to even length.
                            total_name_bytes = name_len + (1 if name_len % 2 == 0 else 0)
                            offset += 7 + total_name_bytes
                            if name.startswith("Timestamp: "):
                                try:
                                    timestamp = int(name.split(": ", 1)[1])
                                except (ValueError, IndexError):
                                    pass
                            elif name.strip() == "Start":
                                start_offset = position
                else:
                    f.seek(data_start + chunk_size)

                # Robust odd-byte alignment for inconsistent AIFF writers.
                # Some Logic files omit the expected pad byte after odd-sized chunks.
                next_pos = data_start + chunk_size
                if next_pos >= file_end:
                    break
                f.seek(next_pos)
                if chunk_size % 2:
                    maybe_pad = f.read(1)
                    if maybe_pad == b"\x00":
                        next_pos += 1
                f.seek(next_pos)
    except Exception:
        pass
    return timestamp, start_offset


def _get_audio_time_reference(file_path: Path) -> tuple[int, int] | None:
    """Read timeline position and content offset from any audio file (WAV or AIFF).

    For WAV: reads BWF bext chunk TimeReference; the content offset is
    always 0 (BWF has no separate pre-roll marker concept).
    For AIFF: reads the Timestamp marker (where frame 0 sits on the SMPTE
    timeline) plus the Start marker (where the recorded content begins
    within the file, i.e. Logic's punch-in/pre-roll offset).

    Returns (content_start_samples, content_offset_samples), or None if no
    position data found. content_start_samples is where the *content*
    (from Start onward) belongs on the SMPTE timeline - the caller must
    also trim playback by content_offset_samples so the pre-roll before
    Start is not replayed at the shifted position too (see F03, 2026-09-15
    review: previously only the position moved and the offset was dropped,
    so the content itself played a second time later than it should).
    """
    ext = file_path.suffix.lower()
    if ext == ".wav":
        time_ref = _get_bwf_time_reference(file_path)
        return (time_ref, 0) if time_ref is not None else None
    if ext in (".aif", ".aiff"):
        timestamp, start_offset = _get_aiff_timestamp(file_path)
        if timestamp is not None:
            return timestamp + start_offset, start_offset
    return None


_SMPTE_TIMECODE_RE = re.compile(r"^(\d+):([0-5]?\d):([0-5]?\d)(?:[:;](\d{1,2}))?$")


def parse_smpte_start(text: str, *, fps: float = 30.0) -> float | None:
    """Parse a --smpte-start value into seconds from SMPTE midnight.

    Accepts 'auto' (returns None, meaning "infer automatically"),
    'HH:MM:SS', 'HH:MM:SS:FF', 'HH:MM:SS;FF' (drop-frame separator, treated
    the same as ':'), or a bare number of seconds. Raises ValueError for
    anything else.
    """
    stripped = text.strip()
    if stripped.lower() == "auto":
        return None

    match = _SMPTE_TIMECODE_RE.match(stripped)
    if match:
        hours, minutes, seconds, frames = match.groups()
        total = int(hours) * 3600 + int(minutes) * 60 + int(seconds)
        if frames is not None:
            frame_count = int(frames)
            if frame_count >= fps:
                raise ValueError(f"Invalid SMPTE start time {text!r}: frame {frame_count} is not less than fps {fps}")
            total += frame_count / fps
        return float(total)

    try:
        value = float(stripped)
    except ValueError:
        raise ValueError(f"Invalid SMPTE start time: {text!r}") from None
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"Invalid SMPTE start time: {text!r}")
    return value


def format_smpte(seconds: float, *, fps: float = 30.0) -> str:
    """Format a seconds-from-SMPTE-midnight value as 'HH:MM:SS:FF'."""
    frames_per_second = round(fps)
    total_frames = round(seconds * fps)
    frame = total_frames % frames_per_second
    total_seconds = total_frames // frames_per_second
    secs = total_seconds % 60
    minutes = (total_seconds // 60) % 60
    hours = total_seconds // 3600
    return f"{hours:02d}:{minutes:02d}:{secs:02d}:{frame:02d}"


def resolve_audio_dir(logicx_path: Path) -> tuple[Path | None, str]:
    """Locate a project's bundled audio directory and identify its layout.

    Package-saved Logic projects store audio under
    ``<logicx>/Media/Audio Files``. Folder-saved projects (File > Project
    Management > Save As, "Include Assets" with a project folder) keep the
    .logicx package alongside a sibling ``Audio Files`` folder instead - or,
    less commonly, a sibling ``Media/Audio Files``. The package location
    wins when both exist.
    """
    package_dir = logicx_path / "Media" / "Audio Files"
    if package_dir.is_dir():
        return package_dir, "package"

    folder_dir = logicx_path.parent / "Audio Files"
    if folder_dir.is_dir():
        return folder_dir, "folder"

    folder_media_dir = logicx_path.parent / "Media" / "Audio Files"
    if folder_media_dir.is_dir():
        return folder_media_dir, "folder"

    return None, "missing"


_TIMESTAMPABLE_AUDIO_SUFFIXES = (".wav", ".aif", ".aiff")


def _iter_timestamped_audio_files(audio_dir: Path, *, default_sample_rate: int = 44100):
    """Yield (audio_file, time_reference_samples, content_offset_samples, sample_rate)
    for each timestamped file in a directory.

    Skips non-files, unsupported extensions, and files with no embedded timeline
    timestamp (see _get_audio_time_reference).
    """
    for audio_file in audio_dir.iterdir():
        if not audio_file.is_file() or audio_file.suffix.lower() not in _TIMESTAMPABLE_AUDIO_SUFFIXES:
            continue
        time_ref = _get_audio_time_reference(audio_file)
        if time_ref is None:
            continue
        content_start, content_offset = time_ref
        file_sample_rate = _get_audio_sample_rate(audio_file, default=default_sample_rate)
        yield audio_file, content_start, content_offset, file_sample_rate


def _resolve_timestamped_audio(
    audio_dir: Path | None,
    *,
    default_sample_rate: int = 44100,
    contained_filenames: set[str] | None = None,
) -> list[tuple[Path, int, int, int]]:
    """Resolve every timestamped audio file in a directory in a single pass.

    ``contained_filenames``, when given, restricts the result to the
    project's actual media references (the Logic alternative's AudioFiles
    membership, minus UnusedAudioFiles). Callers share this one resolution
    pass for both SMPTE-start inference and region placement so an
    alternative-excluded recording's timestamp can never influence either
    one (see F01, 2026-09-15 review).

    Returns a list of (audio_file, content_start_samples,
    content_offset_samples, sample_rate) - see _get_audio_time_reference for
    what content_start/content_offset mean.
    """
    if audio_dir is None or not audio_dir.is_dir():
        return []
    entries = []
    for entry in _iter_timestamped_audio_files(audio_dir, default_sample_rate=default_sample_rate):
        audio_file = entry[0]
        if contained_filenames is not None and audio_file.name not in contained_filenames:
            continue
        entries.append(entry)
    return entries


def _infer_smpte_start(entries: list[tuple[Path, int, int, int]]) -> float | None:
    """Infer a project's SMPTE start from the earliest timestamped audio file.

    ``entries`` must already be resolved (and, for a real project, filtered
    to its contained media references - see _resolve_timestamped_audio) so
    an excluded alternate take or unused recording can never skew the
    inferred hour.

    Touring convention places one SMPTE hour per song, so the inferred start
    is the whole hour at or below the earliest embedded timestamp. Returns
    None when no entry carries a usable timestamp.
    """
    earliest_seconds: float | None = None
    for _audio_file, content_start, _content_offset, file_sample_rate in entries:
        file_seconds = content_start / file_sample_rate
        if earliest_seconds is None or file_seconds < earliest_seconds:
            earliest_seconds = file_seconds

    if earliest_seconds is None:
        return None
    return math.floor(earliest_seconds / 3600) * 3600.0


def extract_regions(
    logicx_path: Path,
    alternative: int = 0,
    *,
    _data: bytes | None = None,
    smpte_start_seconds: float = 3600.0,
    clamped: list[str] | None = None,
    contained_filenames: set[str] | None = None,
    _entries: list[tuple[Path, int, int, int]] | None = None,
) -> dict[str, int]:
    """Extract audio region start positions from audio file timestamps.

    For WAV files: reads BWF bext chunk TimeReference.
    For AIFF files: reads Timestamp + Start markers from MARK chunk.

    All timestamps are relative to SMPTE midnight. ``smpte_start_seconds``
    (Logic's default is 01:00:00:00, i.e. 3600 seconds) is subtracted to get
    the position relative to bar 1. Files whose timestamp precedes the SMPTE
    start land at 0 and have their filename appended to ``clamped`` (when
    supplied) so callers can warn about a likely SMPTE-start mismatch.

    Imported files (MP3, non-timestamped audio) default to 0.

    ``contained_filenames``, when given, restricts placement (and the
    clamped-file warnings derived from it) to a project's actual media
    references, so an alternative-excluded recording's timestamp never
    determines another clip's placement (see F01, 2026-09-15 review).
    Omit it to resolve every timestamped file in the directory, as callers
    inspecting a bundle directly (outside a parsed project) expect.

    ``_entries`` lets a caller that already resolved timestamps (e.g.
    parse_logic_project, sharing one pass with SMPTE-start inference) reuse
    them here instead of re-scanning the directory and re-reading every
    file's header a second time.

    Returns:
        dict mapping filename -> start_position_samples (relative to bar 1)
    """
    del _data  # Kept for API compatibility with shared ProjectData pattern.

    if _entries is not None:
        entries = _entries
    else:
        audio_dir, _layout = resolve_audio_dir(logicx_path)
        if audio_dir is None:
            return {}

        # Fallback sample rate from project metadata.
        meta_path = logicx_path / "Alternatives" / f"{alternative:03d}" / "MetaData.plist"
        sample_rate = 44100
        try:
            with open(meta_path, "rb") as f:
                meta = plistlib.load(f)
            sample_rate = meta.get("SampleRate", 44100)
        except Exception:
            pass
        entries = _resolve_timestamped_audio(
            audio_dir, default_sample_rate=sample_rate, contained_filenames=contained_filenames,
        )

    regions: dict[str, int] = {}
    for audio_file, content_start, _content_offset, file_sample_rate in entries:
        smpte_offset = round(smpte_start_seconds * file_sample_rate)
        position = content_start - smpte_offset
        if position < 0:
            if clamped is not None:
                clamped.append(audio_file.name)
            position = 0
        regions[audio_file.name] = position

    return regions


# Matches the clamp TrackMixerState.volume_linear already applies
# (10**(-70/20) and 10**(6/20)); values outside this range are rejected here
# instead of a JSON typo silently landing on a fader nobody chose.
_MIXER_MIN_VOLUME_DB = -70.0
_MIXER_MAX_VOLUME_DB = 6.0
_MIXER_PAN_RANGE = (-1.0, 1.0)


def load_mixer_overrides(
    json_path: Path, *, base: dict[str, TrackMixerState] | None = None,
) -> dict[str, TrackMixerState]:
    """Load and strictly validate per-track mixer overrides from an explicit JSON file.

    ``base`` holds the values the tracks already have (read from the Logic
    project): a field an entry leaves out keeps the track's value from there
    instead of going back to 0 dB, centre, unmuted.

    An explicit --mixer file is a deliberate user override, so any problem
    with it - missing, malformed JSON, wrong shape, or an out-of-range/
    wrong-type field - is a real configuration error and raises ValueError
    with a clear message, rather than silently falling back to "no
    overrides" (which previously let a typo like is_muted: "false" mute a
    track, since bool("false") is True; see F12, 2026-09-15 review).
    """
    if not json_path.exists():
        raise ValueError(f"Mixer overrides file not found: {json_path}")

    try:
        raw_text = json_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"Could not read mixer overrides file {json_path}: {exc}") from exc

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Mixer overrides file {json_path} is not valid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError(
            f"Mixer overrides file {json_path} must contain a JSON object mapping track name to overrides"
        )

    overrides: dict[str, TrackMixerState] = {}
    for track_name, raw_state in data.items():
        if not isinstance(track_name, str):
            raise ValueError(f"Mixer overrides file {json_path}: track name must be a string, got {track_name!r}")
        if not isinstance(raw_state, dict):
            raise ValueError(f"Mixer overrides file {json_path}: overrides for {track_name!r} must be an object")

        start = (base or {}).get(track_name) or TrackMixerState()
        volume_db = raw_state.get("volume_db", start.volume_db)
        if isinstance(volume_db, bool) or not isinstance(volume_db, (int, float)) or not math.isfinite(volume_db):
            raise ValueError(
                f"Mixer overrides file {json_path}: {track_name!r} has an invalid 'volume_db' "
                f"(must be a finite number): {volume_db!r}"
            )
        if not _MIXER_MIN_VOLUME_DB <= volume_db <= _MIXER_MAX_VOLUME_DB:
            raise ValueError(
                f"Mixer overrides file {json_path}: {track_name!r} has a 'volume_db' outside "
                f"[{_MIXER_MIN_VOLUME_DB:g}, {_MIXER_MAX_VOLUME_DB:g}]: {volume_db!r}"
            )

        pan = raw_state.get("pan", start.pan)
        if isinstance(pan, bool) or not isinstance(pan, (int, float)) or not math.isfinite(pan):
            raise ValueError(
                f"Mixer overrides file {json_path}: {track_name!r} has an invalid 'pan' "
                f"(must be a finite number): {pan!r}"
            )
        if not _MIXER_PAN_RANGE[0] <= pan <= _MIXER_PAN_RANGE[1]:
            raise ValueError(
                f"Mixer overrides file {json_path}: {track_name!r} has a 'pan' outside "
                f"[{_MIXER_PAN_RANGE[0]:g}, {_MIXER_PAN_RANGE[1]:g}]: {pan!r}"
            )

        is_muted = raw_state.get("is_muted", start.is_muted)
        if not isinstance(is_muted, bool):
            raise ValueError(f"Mixer overrides file {json_path}: {track_name!r} has a non-boolean 'is_muted': {is_muted!r}")

        is_soloed = raw_state.get("is_soloed", start.is_soloed)
        if not isinstance(is_soloed, bool):
            raise ValueError(f"Mixer overrides file {json_path}: {track_name!r} has a non-boolean 'is_soloed': {is_soloed!r}")

        overrides[track_name] = TrackMixerState(
            volume_db=float(volume_db),
            pan=float(pan),
            is_muted=is_muted,
            is_soloed=is_soloed,
        )
    return overrides


def discover_audio_files(logicx_path: Path) -> list[AudioFileRef]:
    """Discover all audio files in the project's resolved audio directory.

    Package-saved projects keep audio under Media/Audio Files inside the
    .logicx; folder-saved projects keep a sibling Audio Files folder. See
    resolve_audio_dir().
    """
    audio_dir, layout = resolve_audio_dir(logicx_path)
    if audio_dir is None:
        return []

    # Containment is checked against the project root, not just the audio
    # directory itself: if audio_dir is a symlink/junction pointing outside
    # the project, resolving against it alone would follow the link and
    # accept whatever the attacker placed at the target.
    project_root = (logicx_path if layout == "package" else logicx_path.parent).resolve()
    resolved_audio_dir = audio_dir.resolve()
    refs = []
    for audio_file in sorted(audio_dir.iterdir()):
        if not audio_file.is_file():
            continue
        resolved_audio_file = audio_file.resolve()
        if not resolved_audio_file.is_relative_to(resolved_audio_dir):
            continue
        if not resolved_audio_file.is_relative_to(project_root):
            continue
        if audio_file.suffix.lower() not in (".wav", ".aif", ".aiff", ".mp3", ".m4a"):
            continue
        track_name, take_number, is_comp, comp_name = parse_audio_filename(audio_file.name)
        refs.append(AudioFileRef(
            filename=audio_file.name,
            track_name=track_name,
            take_number=take_number,
            is_comp=is_comp,
            comp_name=comp_name,
            file_path=resolved_audio_file,
        ))
    return refs


def _build_compatibility_warnings(
    meta: dict,
    audio_files: list[AudioFileRef],
    regions: dict[str, int],
    midi_tracks: list[LogicMidiTrack],
    *,
    clamped_files: list[str] | None = None,
    smpte_start_seconds: float = 3600.0,
    smpte_start_inferred: bool = False,
    track_count: int | None = None,
    on_disk_names: set[str] | None = None,
) -> list[str]:
    """Summarize bundle conditions that are likely to produce incomplete conversions.

    ``track_count`` overrides the audio-filename-derived track count when the
    arrangement itself named the tracks. ``on_disk_names`` are the audio files
    found in the project's audio folder; without it the converted clips stand
    in, which is only right when every file found becomes a clip.
    """
    warnings: list[str] = []

    discovered_names = {ref.filename for ref in audio_files} if on_disk_names is None else on_disk_names
    metadata_names = meta.get("audio_files", [])
    missing_bundle_files = [name for name in metadata_names if name not in discovered_names]
    if missing_bundle_files:
        examples = ", ".join(missing_bundle_files[:5])
        if len(missing_bundle_files) > 5:
            examples += ", ..."
        warnings.append(
            f"{len(missing_bundle_files)} audio file(s) listed by Logic were not found inside "
            f"Media/Audio Files and cannot be copied yet: {examples}"
        )

    unpositioned_files = [ref.filename for ref in audio_files if ref.filename not in regions]
    if unpositioned_files and track_count is None:
        examples = ", ".join(unpositioned_files[:5])
        if len(unpositioned_files) > 5:
            examples += ", ..."
        warnings.append(
            f"{len(unpositioned_files)} audio file(s) had no embedded timeline timestamp and "
            f"will default to bar 1: {examples}"
        )

    metadata_track_count = meta.get("num_tracks", 0)
    recovered_track_count = (
        track_count if track_count is not None else len({ref.track_name for ref in audio_files})
    )
    if metadata_track_count and track_count is None and recovered_track_count != metadata_track_count:
        warnings.append(
            f"Logic metadata reports {metadata_track_count} track(s), but only "
            f"{recovered_track_count} track(s) were recoverable from bundled audio filenames"
        )

    if not discovered_names:
        warnings.append(
            "No bundled audio files were discovered under Media/Audio Files (package-saved) or "
            "a sibling Audio Files folder next to the .logicx (folder-saved); this project may "
            "depend on external media, aliases, or unsupported content types"
        )

    if clamped_files:
        examples = ", ".join(clamped_files[:5])
        if len(clamped_files) > 5:
            examples += ", ..."
        warnings.append(
            f"{len(clamped_files)} audio file(s) start before the SMPTE start used "
            f"({format_smpte(smpte_start_seconds)}) and were placed at bar 1: {examples}"
        )
        if not smpte_start_inferred and len(clamped_files) == len(regions):
            warnings.append(
                f"Every timestamped audio file starts before the SMPTE start used "
                f"({format_smpte(smpte_start_seconds)}); this project probably uses a different "
                "SMPTE start (File > Project Settings > Synchronization) - pass --smpte-start "
                "(or auto) to fix it."
            )

    instrument_files = meta.get("software_instrument_files", 0)
    total_midi_notes = sum(track.note_count for track in midi_tracks)
    if instrument_files and total_midi_notes:
        warnings.append(
            f"This project references {instrument_files} software-instrument file(s). MIDI notes were "
            "transferred as native MIDI tracks (and exported to MIDI/), but the software instruments "
            "and their settings are not transferred — reload them manually in Ableton."
        )
    elif instrument_files:
        warnings.append(
            f"This project references {instrument_files} software-instrument file(s), but no MIDI "
            "notes could be decoded from its binary project data (likely an older Logic save "
            "format), so MIDI was not transferred."
        )

    return warnings


def _bar_label(beats: float, bar_beats: float) -> str:
    """'bar 14' or 'bar 3 beat 3.5' for a position in beats from bar 1."""
    bar_index = math.floor(beats / bar_beats + 1e-9)
    beat = beats - bar_index * bar_beats + 1
    return f"bar {bar_index + 1}" if abs(beat - 1) < 1e-6 else f"bar {bar_index + 1} beat {beat:.3g}"


def _decoded_tempo(
    arrangement: LogicArrangement, fallback_tempo: float, bar_beats: float, warnings: list[str],
) -> tuple[float, list[TempoEvent], list[tuple[float, float]]]:
    """The tempo at bar 1, the changes after it and the tempos before it, from Logic's tempo track.

    A tempo holds until the next event. Logic stores the time each event falls
    on, which lets the reading check itself: if stepping through the list does
    not land on those times, the project has a kind of tempo change this does
    not understand, and that is reported instead of converted on trust.

    The third value is the lead-in: (beat, bpm) for events before bar 1, at
    negative beats, and only when the tempo there differs from the tempo at
    bar 1. The Live set starts at bar 1, so the lead-in never reaches it; it
    is what audio starting before bar 1 is trimmed by.
    """
    events = arrangement.tempo_events
    if not events:
        return fallback_tempo, [], []

    if len({event.seconds for event in events}) > 1:
        for previous, event in zip(events, events[1:]):
            expected = previous.seconds + (event.tick - previous.tick) / PPQ * 60.0 / previous.bpm
            if abs(event.seconds - expected) > 0.02:
                where = _bar_label((event.tick - SEQUENCE_ORIGIN_TICKS) / PPQ, bar_beats)
                warnings.append(
                    f"Logic's tempo track does not add up as plain tempo steps around {where} (the project may "
                    "use a tempo curve there). The tempo map in the result may be off from that point; compare "
                    "it with Logic and please report this project."
                )
                break

    at_or_before_bar_one = [event for event in events if event.tick <= SEQUENCE_ORIGIN_TICKS]
    base = at_or_before_bar_one[-1].bpm if at_or_before_bar_one else events[0].bpm
    lead_in = [
        ((event.tick - SEQUENCE_ORIGIN_TICKS) / PPQ, event.bpm)
        for event in events if event.tick < SEQUENCE_ORIGIN_TICKS
    ]
    if not any(abs(bpm - base) >= 0.00005 for _, bpm in lead_in):
        lead_in = []

    changes: list[TempoEvent] = []
    current = base
    for event in events:
        if event.tick <= SEQUENCE_ORIGIN_TICKS or abs(event.bpm - current) < 0.00005:
            continue
        changes.append(TempoEvent(beat=(event.tick - SEQUENCE_ORIGIN_TICKS) / PPQ, bpm=event.bpm))
        current = event.bpm

    tempos = [base] + [event.bpm for event in changes]
    if min(tempos) < MIN_TEMPO_BPM or max(tempos) > MAX_TEMPO_BPM:
        warnings.append(
            f"Logic's tempo track goes outside the range Live accepts ({MIN_TEMPO_BPM:g} to {MAX_TEMPO_BPM:g} BPM); "
            "those tempos were limited to that range, so the affected passage will not line up with Logic."
        )
        base = min(MAX_TEMPO_BPM, max(MIN_TEMPO_BPM, base))
        limited: list[TempoEvent] = []
        for event in changes:
            bpm = min(MAX_TEMPO_BPM, max(MIN_TEMPO_BPM, event.bpm))
            if bpm != (limited[-1].bpm if limited else base):
                limited.append(TempoEvent(beat=event.beat, bpm=bpm))
        changes = limited
    lead_in = [(beat, min(MAX_TEMPO_BPM, max(MIN_TEMPO_BPM, bpm))) for beat, bpm in lead_in]
    return base, changes, lead_in


def _unapplied_tempo_warning(arrangement: LogicArrangement, bar_beats: float, project_tempo: float) -> str | None:
    """For projects whose arrangement could not be read: say that Logic's tempo changes are not applied."""
    events = arrangement.tempo_events
    changes = [(before, after) for before, after in zip(events, events[1:]) if abs(after.bpm - before.bpm) >= 0.0001]
    if not changes:
        return None
    before, after = changes[0]
    where = _bar_label((after.tick - SEQUENCE_ORIGIN_TICKS) / PPQ, bar_beats)
    return (
        f"Logic's tempo track changes tempo {len(changes)} time(s) in this project, first at {where} "
        f"({before.bpm:g} to {after.bpm:g} BPM). This project's arrangement could not be read, so the changes "
        f"are not applied: the Live set stays at {project_tempo:g} BPM. List them in a --timeline file."
    )


class _BeatClock:
    """Beats and samples of one audio file under the project's tempo map.

    Without tempo changes this is the plain samples-per-beat arithmetic, kept
    exactly as it was so that single-tempo projects convert bit for bit as
    before. With changes, every length is measured from where it sits.

    ``lead_in`` holds the tempos before bar 1 as (beat, bpm) at negative
    beats, for projects that start earlier at another tempo. The tempo map
    itself begins at bar 1, so time before it is worked out here.
    """

    def __init__(self, tempo_map: TempoMap, sample_rate: int, lead_in: list[tuple[float, float]] | None = None):
        self._map = tempo_map
        self._rate = sample_rate
        self._lead_in = lead_in or []
        self._flat = not tempo_map.events and not self._lead_in
        self.samples_per_beat = 60.0 / tempo_map.base_tempo * sample_rate

    def _seconds_at(self, beats: float) -> float:
        """Seconds from bar 1 to a position; negative before bar 1."""
        if beats >= 0 or not self._lead_in:
            return self._map.beats_to_seconds(beats)
        seconds = 0.0
        upper = 0.0
        for start, bpm in reversed(self._lead_in):
            lower = max(start, beats)
            if lower < upper:
                seconds -= (upper - lower) * 60.0 / bpm
                upper = lower
            if beats >= start:
                return seconds
        # earlier than the first tempo event: its tempo extends backward
        return seconds - (upper - beats) * 60.0 / self._lead_in[0][1]

    def _beats_at(self, seconds: float) -> float:
        if seconds >= 0 or not self._lead_in:
            return self._map.seconds_to_beats(seconds)
        remaining = -seconds
        upper = 0.0
        for start, bpm in reversed(self._lead_in):
            span_seconds = (upper - start) * 60.0 / bpm
            if remaining <= span_seconds:
                return upper - remaining * bpm / 60.0
            remaining -= span_seconds
            upper = start
        return upper - remaining * self._lead_in[0][1] / 60.0

    def position_samples(self, beats: float) -> float:
        """The time of an arrangement position, in samples from bar 1."""
        if self._flat:
            return beats * self.samples_per_beat
        return self._seconds_at(beats) * self._rate

    def samples_between(self, start_beats: float, end_beats: float) -> float:
        if self._flat:
            return (end_beats - start_beats) * self.samples_per_beat
        return (self._seconds_at(end_beats) - self._seconds_at(start_beats)) * self._rate

    def length_beats(self, start_beats: float, samples: float) -> float:
        """How many beats ``samples`` of audio cover when they start at ``start_beats``."""
        if self._flat:
            return samples / self.samples_per_beat
        return self._beats_at(self._seconds_at(start_beats) + samples / self._rate) - start_beats


def _unroll_regions(regions: list[LogicMidiRegion]) -> list[LogicMidiNote]:
    """Flatten regions to absolute notes, repeating looped content across its span."""
    notes: list[LogicMidiNote] = []
    for region in regions:
        end = region.end_beats
        repeats = 1
        if region.is_looping and region.length_beats > 0:
            repeats = math.ceil(region.loop_span_beats / region.length_beats)
        for index in range(repeats):
            base = region.start_beats + index * region.length_beats
            for note in region.notes:
                start = base + note.start_beats
                if start >= end or start < 0:
                    continue
                duration = min(note.duration_beats, end - start)
                if duration <= 0:
                    continue
                notes.append(LogicMidiNote(
                    pitch=note.pitch, start_beats=start, duration_beats=duration, velocity=note.velocity,
                ))
    notes.sort(key=lambda note: (note.start_beats, note.pitch))
    return notes


def _without_stacked_notes(notes: list[LogicMidiNote]) -> list[LogicMidiNote]:
    """Drop duplicate notes and end a note where the next one of its pitch starts."""
    by_pitch: dict[int, list[LogicMidiNote]] = {}
    for note in sorted(notes, key=lambda note: (note.start_beats, -note.duration_beats)):
        same_pitch = by_pitch.setdefault(note.pitch, [])
        if same_pitch:
            previous = same_pitch[-1]
            if note.start_beats - previous.start_beats < 1e-9:
                previous.velocity = max(previous.velocity, note.velocity)
                continue
            previous.duration_beats = min(previous.duration_beats, note.start_beats - previous.start_beats)
        same_pitch.append(replace(note))
    return sorted((note for group in by_pitch.values() for note in group), key=lambda note: (note.start_beats, note.pitch))


def _merge_overlapping_midi(regions: list[LogicMidiRegion]) -> tuple[list[LogicMidiRegion], list[str]]:
    """Join MIDI regions that overlap on one track into a single region.

    Logic plays overlapping MIDI regions together. A Live track plays one clip
    at a time, so each overlapping group becomes one clip with every note the
    group plays (loops unrolled). Returns the regions and the merged groups' names.
    """
    half_tick = 0.5 / PPQ
    groups: list[list[LogicMidiRegion]] = []
    group_end = 0.0
    for region in sorted(regions, key=lambda region: region.start_beats):
        if groups and region.start_beats < group_end - half_tick:
            groups[-1].append(region)
            group_end = max(group_end, region.end_beats)
        else:
            groups.append([region])
            group_end = region.end_beats

    out: list[LogicMidiRegion] = []
    merged_names: list[str] = []
    for group in groups:
        if len(group) == 1:
            out.append(group[0])
            continue
        start = group[0].start_beats
        end = max(region.end_beats for region in group)
        notes = _without_stacked_notes([
            replace(note, start_beats=note.start_beats - start) for note in _unroll_regions(group)
        ])
        out.append(LogicMidiRegion(name=group[0].name, start_beats=start, length_beats=end - start, notes=notes))
        merged_names.append(" + ".join(region.name for region in group))
    return out, merged_names


# Adjacent regions can overlap by a fraction of a tick, because positions are
# stored in ticks and lengths in samples. Overlaps this short are trimmed
# without a report line.
_OVERLAP_REPORT_BEATS = 0.01


def _cut_overlapping_audio(
    refs: list[AudioFileRef], *, tempo_map: TempoMap, warnings: list[str],
) -> list[AudioFileRef]:
    """Cut audio regions that overlap on one track so no two clips overlap.

    A Logic audio track plays one region at a time, and where two overlap the
    one that starts later plays. A Live track holds one clip at a time, so the
    earlier region is cut where the later one starts. If the earlier region
    runs past the end of the later one, that remainder is kept as its own
    clip: whether Logic plays it has not been checked against a real project,
    and a clip that can be deleted is easier to deal with than missing audio.
    Every cut is reported.
    """
    spans: list[tuple[float, float, _BeatClock]] = []
    by_track: dict[str, list[int]] = {}
    for index, ref in enumerate(refs):
        clock = _BeatClock(tempo_map, _get_audio_sample_rate(ref.file_path))
        length = ref.content_duration_samples
        if length is None:
            try:
                length = max(0, read_audio_info(ref.file_path).frame_count - ref.content_offset_samples)
            except (OSError, ValueError):
                length = 0
        spans.append((ref.start_beats, ref.start_beats + clock.length_beats(ref.start_beats, length), clock))
        by_track.setdefault(ref.track_name, []).append(index)

    pieces_by_index: dict[int, list[tuple[float, float]]] = {}
    cut: list[str] = []
    for indices in by_track.values():
        ordered = sorted(indices, key=lambda index: (spans[index][0], index))
        for position, index in enumerate(ordered):
            start, end, _ = spans[index]
            pieces = [(start, end)]
            first_later: int | None = None
            for later in ordered[position + 1:]:
                later_start, later_end, _ = spans[later]
                if later_end <= later_start or later_start >= end - 1e-9:
                    continue
                first_later = later if first_later is None else first_later
                remaining: list[tuple[float, float]] = []
                for piece_start, piece_end in pieces:
                    if later_end <= piece_start or later_start >= piece_end:
                        remaining.append((piece_start, piece_end))
                        continue
                    if later_start > piece_start:
                        remaining.append((piece_start, later_start))
                    if later_end < piece_end:
                        remaining.append((later_end, piece_end))
                pieces = remaining
            if pieces == [(start, end)]:
                continue
            pieces_by_index[index] = pieces
            if (end - start) - sum(piece_end - piece_start for piece_start, piece_end in pieces) >= _OVERLAP_REPORT_BEATS:
                ref, later_ref = refs[index], refs[first_later]
                cut.append(
                    f"{ref.track_name}: {ref.clip_name or ref.filename} under {later_ref.clip_name or later_ref.filename}"
                )

    if not pieces_by_index:
        return refs
    out: list[AudioFileRef] = []
    for index, ref in enumerate(refs):
        if index not in pieces_by_index:
            out.append(ref)
            continue
        start, _, clock = spans[index]
        for piece_start, piece_end in pieces_by_index[index]:
            if piece_end - piece_start < _OVERLAP_REPORT_BEATS:
                continue
            out.append(replace(
                ref,
                start_position_samples=max(0, round(clock.position_samples(piece_start))),
                content_offset_samples=ref.content_offset_samples + round(clock.samples_between(start, piece_start)),
                content_duration_samples=int(clock.samples_between(piece_start, piece_end) + 1e-6),
                start_beats=piece_start,
            ))
    if cut:
        examples = ", ".join(cut[:5]) + (", ..." if len(cut) > 5 else "")
        warnings.append(
            f"{len(cut)} audio region(s) overlap a later region on the same track. A track plays one clip at a "
            f"time, so the later region was kept whole and the earlier one cut around it; compare these with "
            f"Logic: {examples}"
        )
    return out


def _unique_track_names(arrangement: LogicArrangement, warnings: list[str]) -> dict[int, str]:
    """Display name per Logic track id, numbering tracks that share a name.

    Outputs group clips by track name, so two Logic tracks both called
    "Guitar" would otherwise merge into one track. The first track (by lane,
    then position) keeps the plain name; later ones become "Guitar (2)", ...
    """
    first_seen: dict[int, tuple[int, int]] = {}
    for placement in arrangement.regions:
        # Only tracks that produce output compete for a name.
        if placement.muted:
            continue
        if placement.kind == "midi":
            sequence = arrangement.sequences.get(placement.sequence_id)
            if sequence is None or not sequence.notes:
                continue
        elif placement.audio_file_id not in arrangement.audio_files:
            continue
        key = (placement.lane, placement.tick)
        if placement.track_id not in first_seen or key < first_seen[placement.track_id]:
            first_seen[placement.track_id] = key
    names: dict[int, str] = {}
    used: set[str] = set()
    renamed: list[str] = []
    for track_id in sorted(first_seen, key=lambda tid: (first_seen[tid], tid)):
        base = arrangement.track_name(track_id)
        name = base
        number = 2
        while name.casefold() in used:
            name = f"{base} ({number})"
            number += 1
        used.add(name.casefold())
        names[track_id] = name
        if name != base:
            renamed.append(name)
    if renamed:
        examples = ", ".join(renamed[:5]) + (", ..." if len(renamed) > 5 else "")
        warnings.append(
            f"{len(renamed)} Logic track(s) share a name with another track and were numbered "
            f"to keep them separate: {examples}"
        )
    return names


def _strip_mixer_state(strip: LogicChannelStrip) -> TrackMixerState:
    return TrackMixerState(
        volume_db=max(_MIXER_MIN_VOLUME_DB, min(_MIXER_MAX_VOLUME_DB, strip.volume_db)),
        pan=strip.pan_position,
        is_muted=strip.muted,
    )


def _hardware_output(destination: tuple[str, int] | None) -> HardwareOutput | None:
    """The interface output a signal ends at, unless it is the main one (Output 1-2)."""
    if destination is None:
        return None
    kind, number = destination
    if kind == "output" and number > 0:
        return HardwareOutput(first_channel=2 * number + 1)
    if kind == "mono output":
        return HardwareOutput(first_channel=number, stereo=False)
    return None


def _arrangement_mixer(
    arrangement: LogicArrangement,
    track_names_by_id: dict[int, str],
    warnings: list[str],
) -> tuple[dict[str, TrackMixerState], dict[str, TrackGroup], dict[str, str], dict[str, HardwareOutput]]:
    """Fader, pan and mute of every output track, and the buses the tracks play through.

    A Logic track whose output is a bus is heard through the aux channel that
    listens on that bus, at that channel's level and only while it is unmuted.
    Live's counterpart is a group track, so each such aux becomes a group with
    the aux's fader, pan and mute, holding the tracks that play into it; an
    aux that itself feeds a bus becomes a group inside a group. Without the
    groups the track faders alone would give a different balance than Logic.

    Returns the mixer state per track name, the groups by name, each grouped
    track's group, and the interface output of each track that does not end
    at the main output.
    """
    mixer = arrangement.mixer
    states: dict[str, TrackMixerState] = {}
    groups: dict[str, TrackGroup] = {}
    membership: dict[str, str] = {}
    outputs: dict[str, HardwareOutput] = {}
    group_names: dict[int, str] = {}    # aux strip index -> group name
    used_names: set[str] = set()
    stopped: dict[tuple[str, str], list[str]] = {}  # (track or group, why the path stops) -> names

    def route(strip: LogicChannelStrip, trail: frozenset[int]) -> tuple[str | None, HardwareOutput | None, str | None]:
        """The group a strip plays into, else its interface output, else what stops the path."""
        destination = mixer.destination(strip)
        if destination is None or destination[0] != "bus":
            return None, _hardware_output(destination), None
        listeners = mixer.bus_listeners(destination[1])
        if not listeners:
            return None, None, "unheard"
        if len(listeners) > 1:
            return None, None, "shared"
        if listeners[0].index in trail:
            return None, None, "loop"
        aux = listeners[0]
        name = group_names.get(aux.index)
        if name is None:
            base = aux.name or aux.label
            name = base
            number = 2
            while name.casefold() in used_names:
                name = f"{base} ({number})"
                number += 1
            used_names.add(name.casefold())
            group_names[aux.index] = name
            group = TrackGroup(name=name, mixer=_strip_mixer_state(aux))
            groups[name] = group
            group.parent, group.output, problem = route(aux, trail | {aux.index})
            if problem:
                stopped.setdefault(("group", problem), []).append(name)
        return name, None, None

    for track_id, track_name in track_names_by_id.items():
        strip = mixer.strip_for_track(track_id)
        if strip is None:
            continue
        states[track_name] = _strip_mixer_state(strip)
        group, output, problem = route(strip, frozenset({strip.index}))
        if group is not None:
            membership[track_name] = group
        elif output is not None:
            outputs[track_name] = output
        elif problem:
            stopped.setdefault(("track", problem), []).append(track_name)

    silenced = [
        name for track_id, name in track_names_by_id.items()
        if (strip := mixer.strip_for_track(track_id)) is not None and strip.solo_silenced and not strip.muted
    ]
    if silenced:
        shown = dict(group_names)
        for track_id, name in track_names_by_id.items():
            if (strip := mixer.strip_for_track(track_id)) is not None:
                shown.setdefault(strip.index, name)
        soloed = [
            shown.get(strip.index) or strip.name or strip.label
            for strip in sorted(mixer.strips.values(), key=lambda strip: strip.index)
            if strip.soloed and strip.track_id is not None
        ]
        where = f" for {', '.join(soloed[:5])}{', ...' if len(soloed) > 5 else ''}" if soloed else ""
        examples = ", ".join(silenced[:5]) + (", ..." if len(silenced) > 5 else "")
        warnings.append(
            f"Solo was on{where} when the Logic project was saved and silenced {len(silenced)} track(s) that are "
            f"not muted. Solo is not carried over, so they play here: {examples}"
        )

    reasons = {
        "unheard": "with no aux channel listening on it",
        "shared": "that more than one aux channel listens on, which a Live group cannot mirror",
        "loop": "that leads back to the same channel",
    }
    outcomes = {
        "track": "so they are not grouped and keep their own level",
        "group": "so they play to the main output",
    }
    for (kind, problem), names in stopped.items():
        examples = ", ".join(names[:5]) + (", ..." if len(names) > 5 else "")
        warnings.append(
            f"{len(names)} {kind}(s) play into a Logic bus {reasons[problem]}, {outcomes[kind]}: {examples}"
        )
    return states, groups, membership, outputs


def _arrangement_midi_tracks(
    arrangement: LogicArrangement,
    *,
    bar_beats: float,
    track_names_by_id: dict[int, str],
    warnings: list[str],
) -> list[LogicMidiTrack]:
    """One LogicMidiTrack per Logic track that has audible MIDI regions."""
    by_track: dict[int, list] = {}
    for placement in arrangement.regions:
        if placement.kind == "midi":
            by_track.setdefault(placement.track_id, []).append(placement)

    tracks: list[LogicMidiTrack] = []
    muted: list[str] = []
    merged: list[str] = []
    for track_id, placements in sorted(
        by_track.items(), key=lambda item: (min(p.lane for p in item[1]), min(p.tick for p in item[1]))
    ):
        track_name = track_names_by_id.get(track_id) or arrangement.track_name(track_id)
        regions: list[LogicMidiRegion] = []
        for placement in sorted(placements, key=lambda p: p.tick):
            sequence = arrangement.sequences.get(placement.sequence_id)
            if sequence is None or sequence.content_length <= 0:
                continue
            window_start = sequence.content_start
            window_end = window_start + sequence.content_length
            notes = []
            for note in sequence.notes:
                relative = note.tick - SEQUENCE_ORIGIN_TICKS
                if not (window_start <= relative < window_end):
                    continue
                notes.append(LogicMidiNote(
                    pitch=note.pitch,
                    start_beats=(relative - window_start) / PPQ,
                    duration_beats=note.duration / PPQ,
                    velocity=note.velocity,
                ))
            if not notes:
                continue
            if placement.muted:
                muted.append(f"{track_name}: {sequence.name or 'region'}")
                continue
            notes.sort(key=lambda note: (note.start_beats, note.pitch))
            start_beats = arrangement.region_beats(placement.tick)
            if start_beats < 0:
                warnings.append(
                    f"MIDI region '{sequence.name or track_name}' on '{track_name}' starts before bar 1 "
                    "and was moved to bar 1; Live's arrangement cannot start earlier."
                )
                start_beats = 0.0
            regions.append(LogicMidiRegion(
                name=sequence.name or track_name,
                start_beats=start_beats,
                length_beats=sequence.content_length / PPQ,
                notes=notes,
                loop_span_beats=placement.loop_span / PPQ if placement.looped else None,
            ))
        if regions:
            regions, merged_names = _merge_overlapping_midi(regions)
            merged.extend(f"{track_name}: {names}" for names in merged_names)
            tracks.append(LogicMidiTrack(name=track_name, notes=_unroll_regions(regions), regions=regions))
    if merged:
        examples = ", ".join(merged[:5]) + (", ..." if len(merged) > 5 else "")
        warnings.append(
            f"{len(merged)} group(s) of overlapping MIDI regions were each written as one clip holding all "
            f"their notes, because a Live track plays one clip at a time: {examples}"
        )
    if muted:
        examples = ", ".join(muted[:5]) + (", ..." if len(muted) > 5 else "")
        warnings.append(f"Skipped {len(muted)} muted MIDI region(s) as Logic would not play them: {examples}")
    return tracks


def _within_window(
    passes: list[tuple[float, int, int | None]], start: float, end: float, clock: "_BeatClock",
) -> list[tuple[float, int, int | None]]:
    """Cut each (start beats, source offset, source length) pass to the beats from ``start`` to ``end``."""
    kept = []
    for pass_start, offset, length in passes:
        if pass_start < start:
            trimmed = round(clock.samples_between(pass_start, start))
            offset += trimmed
            if length is not None:
                length -= trimmed
            pass_start = start
        if pass_start >= end - 0.5 / PPQ:
            continue
        room = round(clock.samples_between(pass_start, end))
        length = room if length is None else min(length, room)
        if length > 0:
            kept.append((pass_start, offset, length))
    return kept


def _arrangement_audio_refs(
    arrangement: LogicArrangement,
    discovered: list[AudioFileRef],
    *,
    tempo_map: TempoMap,
    bar_beats: float,
    track_names_by_id: dict[int, str],
    warnings: list[str],
    listed_as_used: bool = False,
    lead_in: list[tuple[float, float]] | None = None,
) -> tuple[list[AudioFileRef], list[str]]:
    """Audio clips as Logic placed them: one ref per region, one per repetition when looped.

    Regions sit at musical positions and play their audio at its own speed,
    so how many beats a region covers depends on the tempo where it sits:
    lengths, loop passes and cuts are all measured through ``tempo_map``, and
    through ``lead_in`` for the part of the song before bar 1.

    ``listed_as_used`` says that ``discovered`` holds exactly the files Logic's
    own metadata lists as used by this alternative. Each of them must then
    have a region; one that has none means part of the arrangement was not
    read, and that is reported instead of passing as a clean result.
    """
    by_name = {ref.filename.casefold(): ref for ref in discovered}
    refs: list[AudioFileRef] = []
    lanes: dict[str, int] = {}
    missing: list[str] = []
    muted: list[str] = []
    looped: list[str] = []
    placed: set[str] = set()
    undescribed = 0
    empty: list[str] = []
    for placement in sorted(arrangement.regions, key=lambda p: (p.tick, p.lane)):
        if placement.kind != "audio":
            continue
        filename = arrangement.audio_files.get(placement.audio_file_id)
        if not filename:
            undescribed += 1
            continue
        source = by_name.get(filename.casefold())
        track_name = track_names_by_id.get(placement.track_id) or arrangement.track_name(placement.track_id)
        if source is None:
            missing.append(filename)
            continue
        placed.add(filename.casefold())
        region = arrangement.audio_regions.get((placement.audio_file_id, placement.audio_region_index))
        region_name = region.name if region and region.name else None
        label = region_name or filename
        if placement.muted:
            muted.append(f"{track_name}: {label}")
            continue
        clock = _BeatClock(tempo_map, _get_audio_sample_rate(source.file_path), lead_in)
        samples_per_beat = clock.samples_per_beat
        start_beats = arrangement.region_beats(placement.tick)
        content_offset = region.content_offset if region else 0
        content_length = region.content_length if region else None

        # (start beats, source offset, source length) for each pass the region plays.
        passes: list[tuple[float, int, int | None]] = [(start_beats, content_offset, content_length)]
        if placement.looped and content_length:
            # Logic repeats the region end to end across the loop span and cuts
            # the last pass at the span's end. Spans are in ticks and lengths in
            # samples: a remainder under half a tick is rounding between the
            # two, not another pass.
            span_beats = placement.loop_span / PPQ
            length_beats = clock.length_beats(start_beats, content_length)
            half_tick = 0.5 / PPQ
            if span_beats > length_beats * (1 + 1e-9) and not tempo_map.events and not lead_in:
                passes = []
                repetition = 0
                while repetition * length_beats < span_beats - half_tick:
                    remaining_beats = span_beats - repetition * length_beats
                    length = (
                        content_length if remaining_beats >= length_beats
                        else round(remaining_beats * samples_per_beat)
                    )
                    if length <= 0:
                        break
                    passes.append((start_beats + repetition * length_beats, content_offset, length))
                    repetition += 1
            elif span_beats > length_beats + half_tick:
                # Under tempo changes each pass covers a different number of
                # beats, so every pass starts where the one before it ended.
                passes = []
                span_end = start_beats + span_beats
                position = start_beats
                while position < span_end - half_tick:
                    pass_beats = clock.length_beats(position, content_length)
                    length = (
                        content_length if position + pass_beats <= span_end + half_tick
                        else round(clock.samples_between(position, span_end))
                    )
                    if length <= 0:
                        break
                    passes.append((position, content_offset, length))
                    position += pass_beats
            if len(passes) > 1:
                looped.append(f"{track_name}: {label} x{len(passes)}")
        if placement.window is not None:
            # A take folder plays its contents only within its own length.
            passes = _within_window(
                passes, arrangement.region_beats(placement.window[0]), arrangement.region_beats(placement.window[1]), clock,
            )

        for pass_start, pass_offset, pass_length in passes:
            if pass_start < 0:
                # The Live arrangement starts at bar 1: keep the audio in sync
                # by trimming the part that would sit before it.
                trimmed = round(clock.samples_between(pass_start, 0.0))
                pass_offset += trimmed
                if pass_length is not None:
                    pass_length -= trimmed
                    if pass_length <= 0:
                        warnings.append(
                            f"Audio region '{label}' on '{track_name}' ends before bar 1 and was skipped."
                        )
                        continue
                pass_start = 0.0
            if pass_length is not None and pass_length <= 0:
                empty.append(f"{track_name}: {label}")
                continue
            refs.append(replace(
                source,
                track_name=track_name,
                start_position_samples=max(0, round(clock.position_samples(pass_start))),
                content_offset_samples=pass_offset,
                content_duration_samples=pass_length,
                clip_name=region_name,
                start_beats=pass_start,
            ))
            lanes.setdefault(track_name, placement.lane)
    refs = _cut_overlapping_audio(refs, tempo_map=tempo_map, warnings=warnings)
    if looped:
        examples = ", ".join(looped[:5]) + (", ..." if len(looped) > 5 else "")
        warnings.append(f"{len(looped)} looped audio region(s) were written as repeated clips: {examples}")
    if missing:
        unique = sorted(set(missing))
        examples = ", ".join(unique[:5]) + (", ..." if len(unique) > 5 else "")
        warnings.append(
            f"{len(unique)} audio file(s) referenced by the arrangement are not in the audio folder "
            f"and were skipped: {examples}"
        )
    if muted:
        examples = ", ".join(muted[:5]) + (", ..." if len(muted) > 5 else "")
        warnings.append(f"Skipped {len(muted)} muted audio region(s) as Logic would not play them: {examples}")
    if empty:
        examples = ", ".join(empty[:5]) + (", ..." if len(empty) > 5 else "")
        warnings.append(f"{len(empty)} audio region(s) have no length in the project data and were left out: {examples}")
    if undescribed:
        warnings.append(
            f"{undescribed} audio region(s) point at an audio file the project data does not describe "
            "and were left out. Please report this project."
        )
    unplaced = [ref.filename for ref in discovered if ref.filename.casefold() not in placed] if listed_as_used else []
    if unplaced:
        examples = ", ".join(unplaced[:5]) + (", ..." if len(unplaced) > 5 else "")
        warnings.append(
            f"{len(unplaced)} audio file(s) are used in the Logic project, but the converter found no region "
            f"for them, so they are MISSING from the result: {examples}. The files are fine; part of this "
            "project could not be read. Please report it."
        )
    track_names = sorted(lanes, key=lambda name: lanes[name])
    return refs, track_names


def _project_timeline(
    arrangement: LogicArrangement | None, tempo_changes: list[TempoEvent], supplied: Timeline | None,
) -> Timeline | None:
    """Tempo changes and markers for the result: the project's own, unless a
    supplied timeline lists that kind itself."""
    markers = [
        TimelineMarker(beat=arrangement.marker_beats(marker.tick), name=marker.name)
        for marker in (arrangement.markers if arrangement is not None else [])
        if arrangement.marker_beats(marker.tick) >= 0
    ]
    if supplied is None:
        if not markers and not tempo_changes:
            return None
        return Timeline(
            tempo_events=tempo_changes,
            markers=markers,
            source_path="Logic project",
            tempo_from_project=bool(tempo_changes),
            markers_from_project=bool(markers),
        )
    return Timeline(
        tempo_events=supplied.tempo_events or tempo_changes,
        markers=supplied.markers or markers,
        source_path=supplied.source_path,
        tempo_from_project=not supplied.tempo_events and bool(tempo_changes),
        markers_from_project=not supplied.markers and bool(markers),
    )


def read_time_base(logicx_path: Path, alternative: int | None = None) -> tuple[str, int, int, float]:
    """Name, time signature and tempo of a Logic project, from its metadata alone.

    This is what a --timeline file's bar positions are resolved against, so
    the file can be loaded and checked before the project itself is decoded.
    """
    logicx_path = Path(logicx_path)
    info = parse_project_info(logicx_path)
    alternative = resolve_alternative(logicx_path, alternative, info.get("active_variant"))
    meta = parse_metadata(logicx_path, alternative=alternative)
    name = info["variant_names"].get(str(alternative), logicx_path.stem)
    return name, meta["time_sig_numerator"], meta["time_sig_denominator"], meta["tempo"]


def parse_logic_project(
    logicx_path: Path,
    alternative: int | None = None,
    *,
    smpte_start_seconds: float | None = 3600.0,
    timeline: Timeline | None = None,
) -> LogicProject:
    """Parse a complete Logic Pro project into a LogicProject dataclass.

    ``alternative`` defaults to ``None``, which auto-detects the active
    alternative. Pass an explicit index to force a specific one.

    ``smpte_start_seconds`` defaults to Logic's own default (3600, i.e.
    01:00:00:00). Pass ``None`` to infer it automatically from the earliest
    timestamped audio file instead (see ``_infer_smpte_start``).

    ``timeline`` is a tempo map and markers supplied by the user (--timeline).
    Tempo entries in it replace the tempo changes read from the project, and
    its markers replace the project's markers; whatever it leaves out comes
    from the project.
    """
    logicx_path = Path(logicx_path)
    info = parse_project_info(logicx_path)
    alternative = resolve_alternative(logicx_path, alternative, info.get("active_variant"))
    meta = parse_metadata(logicx_path, alternative=alternative)
    audio_dir, audio_layout = resolve_audio_dir(logicx_path)
    audio_files = discover_audio_files(logicx_path)
    on_disk_names = {ref.filename for ref in audio_files}
    active_names = set(meta["audio_files"])
    unused_names = set(meta["unused_audio_files"])
    audio_files = [
        ref for ref in audio_files
        if (ref.filename in active_names if meta["has_audio_membership"] else ref.filename not in unused_names)
    ]
    excluded_count = len(on_disk_names) - len({ref.filename for ref in audio_files})

    # One shared timestamp-resolution pass, filtered to this alternative's
    # contained media references, reused below for SMPTE-start inference,
    # region placement, and the clamped-file warnings derived from it - an
    # excluded take's timestamp can influence none of the three (F01).
    contained_filenames = {ref.filename for ref in audio_files}
    timestamped_entries = _resolve_timestamped_audio(
        audio_dir, default_sample_rate=meta["sample_rate"], contained_filenames=contained_filenames,
    )

    smpte_start_inferred = smpte_start_seconds is None
    if smpte_start_inferred:
        inferred = _infer_smpte_start(timestamped_entries)
        effective_smpte_start = inferred if inferred is not None else 3600.0
    else:
        effective_smpte_start = smpte_start_seconds

    # Read ProjectData once, share across extractors.
    project_data = _read_project_data(logicx_path, alternative)
    plugins = extract_plugins(logicx_path, alternative, _data=project_data)
    midi_warnings: list[str] = []
    clamped_files: list[str] = []
    bar_beats = beats_per_bar(meta["time_sig_numerator"], meta["time_sig_denominator"])
    arrangement = decode_project_data(project_data, beats_per_bar=bar_beats)
    arrangement_decoded = bool(arrangement.regions)
    supplied_tempo = list(timeline.tempo_events) if timeline is not None else []
    project_tempo = meta["tempo"]
    tempo_changes: list[TempoEvent] = []
    track_count = None
    mixer_state: dict[str, TrackMixerState] = {}
    track_groups: dict[str, TrackGroup] = {}
    track_group: dict[str, str] = {}
    track_outputs: dict[str, HardwareOutput] = {}
    if arrangement_decoded:
        # The project's own arrangement says where every region sits, how it
        # loops and which track owns it, so audio timestamps and the SMPTE
        # start are not needed for placement.
        midi_warnings.extend(arrangement.warnings)
        tempo_warnings: list[str] = []
        project_tempo, tempo_changes, lead_in = _decoded_tempo(arrangement, meta["tempo"], bar_beats, tempo_warnings)
        if not supplied_tempo:
            midi_warnings.extend(tempo_warnings)
        tempo_map = TempoMap(project_tempo, supplied_tempo or tempo_changes)
        track_names_by_id = _unique_track_names(arrangement, midi_warnings)
        midi_tracks = _arrangement_midi_tracks(
            arrangement, bar_beats=bar_beats, track_names_by_id=track_names_by_id, warnings=midi_warnings,
        )
        audio_files, track_names = _arrangement_audio_refs(
            arrangement,
            audio_files,
            tempo_map=tempo_map,
            bar_beats=bar_beats,
            track_names_by_id=track_names_by_id,
            warnings=midi_warnings,
            listed_as_used=meta["has_audio_membership"],
            lead_in=lead_in,
        )
        regions = {ref.filename: ref.start_position_samples for ref in audio_files}
        track_count = len(set(track_names) | {track.name for track in midi_tracks})
        output_names = set(track_names) | {track.name for track in midi_tracks if track.note_count > 0}
        mixer_state, track_groups, track_group, track_outputs = _arrangement_mixer(
            arrangement,
            {track_id: name for track_id, name in track_names_by_id.items() if name in output_names},
            midi_warnings,
        )
    else:
        midi_tracks = extract_midi_notes(logicx_path, alternative, _data=project_data, warnings=midi_warnings)
        regions = extract_regions(
            logicx_path,
            alternative,
            _data=project_data,
            smpte_start_seconds=effective_smpte_start,
            clamped=clamped_files,
            _entries=timestamped_entries,
        )
        # AIFF recordings with a Start marker: place the clip at the marker's own
        # arrangement position (already reflected in regions/start_position, via
        # _get_audio_time_reference folding Start into the timeline position) AND
        # trim playback to start at that same marker, so the pre-roll before it
        # is skipped rather than replayed a second time later (F03).
        content_offsets = {
            audio_file.name: content_offset
            for audio_file, _content_start, content_offset, _rate in timestamped_entries
        }
        for ref in audio_files:
            ref.start_position_samples = regions.get(ref.filename, 0)
            ref.content_offset_samples = content_offsets.get(ref.filename, 0)

        seen = set()
        track_names = []
        for ref in audio_files:
            if ref.track_name not in seen:
                seen.add(ref.track_name)
                track_names.append(ref.track_name)

    compatibility_warnings = _build_compatibility_warnings(
        meta,
        audio_files,
        regions,
        midi_tracks,
        clamped_files=clamped_files,
        smpte_start_seconds=effective_smpte_start,
        smpte_start_inferred=smpte_start_inferred,
        track_count=track_count,
        on_disk_names=on_disk_names,
    )
    if excluded_count:
        compatibility_warnings.append(
            f"Excluded {excluded_count} unused or unreferenced audio file(s) from the selected Logic alternative."
        )
    if not arrangement_decoded and not supplied_tempo:
        unapplied = _unapplied_tempo_warning(arrangement, bar_beats, project_tempo)
        if unapplied:
            compatibility_warnings.insert(0, unapplied)
    compatibility_warnings.extend(midi_warnings)
    project_timeline = _project_timeline(arrangement if arrangement_decoded else None, tempo_changes, timeline)

    return LogicProject(
        name=info["variant_names"].get(str(alternative), logicx_path.stem),
        tempo=project_tempo,
        time_sig_numerator=meta["time_sig_numerator"],
        time_sig_denominator=meta["time_sig_denominator"],
        sample_rate=meta["sample_rate"],
        audio_files=audio_files,
        plugins=plugins,
        track_names=track_names,
        alternative=alternative,
        metadata_track_count=meta["num_tracks"],
        metadata_audio_files=meta["audio_files"],
        software_instrument_files=meta["software_instrument_files"],
        midi_tracks=midi_tracks,
        compatibility_warnings=compatibility_warnings,
        smpte_start_seconds=effective_smpte_start,
        smpte_start_inferred=smpte_start_inferred,
        audio_dir=audio_dir,
        audio_layout=audio_layout,
        timeline=project_timeline,
        arrangement_decoded=arrangement_decoded,
        tempo_track_decoded=arrangement_decoded and bool(arrangement.tempo_events),
        project_start_bar=arrangement.project_start_bar if arrangement_decoded else None,
        project_start_beats=(
            arrangement.project_start_ticks / PPQ
            if arrangement_decoded and arrangement.project_start_ticks is not None else None
        ),
        mixer_state=mixer_state or None,
        mixer_from_project=bool(mixer_state),
        track_groups=track_groups,
        track_group=track_group,
        track_outputs=track_outputs,
    )
