import json
import math
import plistlib
import re
import struct
from pathlib import Path

import bisect

from logic2ableton.models import (
    AudioFileRef,
    LogicMidiNote,
    LogicMidiTrack,
    LogicProject,
    PluginInstance,
    TrackMixerState,
    parse_audio_filename,
)
from logic2ableton.smf import MIDI_TICKS_PER_QUARTER


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


def _get_audio_time_reference(file_path: Path) -> int | None:
    """Read timeline position from any audio file (WAV or AIFF).

    For WAV: reads BWF bext chunk TimeReference.
    For AIFF: reads Timestamp marker + Start marker offset.

    Returns the content start position in samples (SMPTE-absolute),
    or None if no position data found.
    """
    ext = file_path.suffix.lower()
    if ext == ".wav":
        return _get_bwf_time_reference(file_path)
    if ext in (".aif", ".aiff"):
        timestamp, start_offset = _get_aiff_timestamp(file_path)
        if timestamp is not None:
            # Timestamp = where frame 0 of the file is on SMPTE timeline.
            # Start marker = where content begins within the file.
            return timestamp + start_offset
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
    """Yield (audio_file, time_reference_samples, sample_rate) for each timestamped file in a directory.

    Skips non-files, unsupported extensions, and files with no embedded timeline
    timestamp (see _get_audio_time_reference).
    """
    for audio_file in audio_dir.iterdir():
        if not audio_file.is_file() or audio_file.suffix.lower() not in _TIMESTAMPABLE_AUDIO_SUFFIXES:
            continue
        time_ref = _get_audio_time_reference(audio_file)
        if time_ref is None:
            continue
        file_sample_rate = _get_audio_sample_rate(audio_file, default=default_sample_rate)
        yield audio_file, time_ref, file_sample_rate


def _infer_smpte_start(audio_dir: Path | None) -> float | None:
    """Infer a project's SMPTE start from its earliest timestamped audio file.

    Touring convention places one SMPTE hour per song, so the inferred start
    is the whole hour at or below the earliest embedded timestamp. Returns
    None when no bundled audio file carries a usable timestamp.
    """
    if audio_dir is None or not audio_dir.is_dir():
        return None

    earliest_seconds: float | None = None
    for _audio_file, time_ref, file_sample_rate in _iter_timestamped_audio_files(audio_dir):
        file_seconds = time_ref / file_sample_rate
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

    Returns:
        dict mapping filename -> start_position_samples (relative to bar 1)
    """
    del _data  # Kept for API compatibility with shared ProjectData pattern.

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

    regions: dict[str, int] = {}
    for audio_file, time_ref, file_sample_rate in _iter_timestamped_audio_files(audio_dir, default_sample_rate=sample_rate):
        smpte_offset = round(smpte_start_seconds * file_sample_rate)
        position = time_ref - smpte_offset
        if position < 0:
            if clamped is not None:
                clamped.append(audio_file.name)
            position = 0
        regions[audio_file.name] = position

    return regions


def load_mixer_overrides(json_path: Path) -> dict[str, TrackMixerState]:
    """Load per-track mixer overrides from JSON."""
    if not json_path.exists():
        return {}

    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception:
        return {}

    if not isinstance(data, dict):
        return {}

    overrides: dict[str, TrackMixerState] = {}
    for track_name, raw_state in data.items():
        if not isinstance(track_name, str) or not isinstance(raw_state, dict):
            continue
        overrides[track_name] = TrackMixerState(
            volume_db=float(raw_state.get("volume_db", 0.0)),
            pan=float(raw_state.get("pan", 0.0)),
            is_muted=bool(raw_state.get("is_muted", False)),
            is_soloed=bool(raw_state.get("is_soloed", False)),
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
) -> list[str]:
    """Summarize bundle conditions that are likely to produce incomplete conversions."""
    warnings: list[str] = []

    discovered_names = {ref.filename for ref in audio_files}
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
    if unpositioned_files:
        examples = ", ".join(unpositioned_files[:5])
        if len(unpositioned_files) > 5:
            examples += ", ..."
        warnings.append(
            f"{len(unpositioned_files)} audio file(s) had no embedded timeline timestamp and "
            f"will default to bar 1: {examples}"
        )

    metadata_track_count = meta.get("num_tracks", 0)
    recovered_track_count = len({ref.track_name for ref in audio_files})
    if metadata_track_count and recovered_track_count != metadata_track_count:
        warnings.append(
            f"Logic metadata reports {metadata_track_count} track(s), but only "
            f"{recovered_track_count} track(s) were recoverable from bundled audio filenames"
        )

    if not audio_files:
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


def parse_logic_project(
    logicx_path: Path,
    alternative: int | None = None,
    *,
    smpte_start_seconds: float | None = 3600.0,
) -> LogicProject:
    """Parse a complete Logic Pro project into a LogicProject dataclass.

    ``alternative`` defaults to ``None``, which auto-detects the active
    alternative. Pass an explicit index to force a specific one.

    ``smpte_start_seconds`` defaults to Logic's own default (3600, i.e.
    01:00:00:00). Pass ``None`` to infer it automatically from the earliest
    timestamped audio file instead (see ``_infer_smpte_start``).
    """
    logicx_path = Path(logicx_path)
    info = parse_project_info(logicx_path)
    alternative = resolve_alternative(logicx_path, alternative, info.get("active_variant"))
    meta = parse_metadata(logicx_path, alternative=alternative)
    audio_dir, audio_layout = resolve_audio_dir(logicx_path)
    audio_files = discover_audio_files(logicx_path)
    discovered_count = len(audio_files)
    active_names = set(meta["audio_files"])
    unused_names = set(meta["unused_audio_files"])
    audio_files = [
        ref for ref in audio_files
        if (ref.filename in active_names if meta["has_audio_membership"] else ref.filename not in unused_names)
    ]

    smpte_start_inferred = smpte_start_seconds is None
    if smpte_start_inferred:
        inferred = _infer_smpte_start(audio_dir)
        effective_smpte_start = inferred if inferred is not None else 3600.0
    else:
        effective_smpte_start = smpte_start_seconds

    # Read ProjectData once, share across extractors.
    project_data = _read_project_data(logicx_path, alternative)
    plugins = extract_plugins(logicx_path, alternative, _data=project_data)
    midi_warnings: list[str] = []
    midi_tracks = extract_midi_notes(logicx_path, alternative, _data=project_data, warnings=midi_warnings)
    clamped_files: list[str] = []
    regions = extract_regions(
        logicx_path,
        alternative,
        _data=project_data,
        smpte_start_seconds=effective_smpte_start,
        clamped=clamped_files,
    )
    for ref in audio_files:
        ref.start_position_samples = regions.get(ref.filename, 0)

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
    )
    excluded_count = discovered_count - len(audio_files)
    if excluded_count:
        compatibility_warnings.append(
            f"Excluded {excluded_count} unused or unreferenced audio file(s) from the selected Logic alternative."
        )
    compatibility_warnings.extend(midi_warnings)

    return LogicProject(
        name=info["variant_names"].get(str(alternative), logicx_path.stem),
        tempo=meta["tempo"],
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
    )
