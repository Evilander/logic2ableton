import json
import plistlib
import re
import struct
from pathlib import Path

from logic2ableton.models import AudioFileRef, LogicProject, PluginInstance, TrackMixerState, parse_audio_filename


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
    }


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
            f.read(4)  # WAVE
            while f.tell() < file_size + 8:
                chunk_id = f.read(4)
                if len(chunk_id) < 4:
                    break
                chunk_size = struct.unpack("<I", f.read(4))[0]
                if chunk_id == b"bext" and chunk_size >= 346:
                    chunk_data = f.read(chunk_size)
                    return struct.unpack_from("<Q", chunk_data, 338)[0]
                f.seek(chunk_size + (chunk_size % 2), 1)
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


def extract_regions(logicx_path: Path, alternative: int = 0, *, _data: bytes | None = None) -> dict[str, int]:
    """Extract audio region start positions from audio file timestamps.

    For WAV files: reads BWF bext chunk TimeReference.
    For AIFF files: reads Timestamp + Start markers from MARK chunk.

    All timestamps are relative to SMPTE midnight. Logic's default SMPTE
    start is 01:00:00:00 (1 hour), so we subtract 3600 * sample_rate
    to get the position relative to bar 1.

    Imported files (MP3, non-timestamped audio) default to 0.

    Returns:
        dict mapping filename -> start_position_samples (relative to bar 1)
    """
    del _data  # Kept for API compatibility with shared ProjectData pattern.

    audio_dir = logicx_path / "Media" / "Audio Files"
    if not audio_dir.exists():
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
    for audio_file in audio_dir.iterdir():
        if not audio_file.is_file() or audio_file.suffix.lower() not in (".wav", ".aif", ".aiff"):
            continue
        time_ref = _get_audio_time_reference(audio_file)
        if time_ref is None:
            continue
        file_sample_rate = _get_audio_sample_rate(audio_file, default=sample_rate)
        smpte_offset = 3600 * file_sample_rate  # 1 hour at the file's own sample rate
        regions[audio_file.name] = max(0, time_ref - smpte_offset)

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

    result: dict[str, TrackMixerState] = {}
    for track_name, values in data.items():
        if not isinstance(track_name, str) or not isinstance(values, dict):
            continue
        result[track_name] = TrackMixerState(
            volume_db=float(values.get("volume_db", 0.0)),
            pan=float(values.get("pan", 0.0)),
            is_muted=bool(values.get("is_muted", False)),
            is_soloed=bool(values.get("is_soloed", False)),
        )
    return result


def discover_audio_files(logicx_path: Path) -> list[AudioFileRef]:
    """Discover all audio files in Media/Audio Files/."""
    audio_dir = logicx_path / "Media" / "Audio Files"
    if not audio_dir.exists():
        return []

    refs = []
    for audio_file in sorted(audio_dir.iterdir()):
        if not audio_file.is_file():
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
            file_path=audio_file.resolve(),
        ))
    return refs


def parse_logic_project(logicx_path: Path, alternative: int = 0) -> LogicProject:
    """Parse a complete Logic Pro project into a LogicProject dataclass."""
    logicx_path = Path(logicx_path)
    info = parse_project_info(logicx_path)
    meta = parse_metadata(logicx_path, alternative=alternative)
    audio_files = discover_audio_files(logicx_path)

    # Read ProjectData once, share across extractors.
    project_data = _read_project_data(logicx_path, alternative)
    plugins = extract_plugins(logicx_path, alternative, _data=project_data)
    regions = extract_regions(logicx_path, alternative, _data=project_data)
    for ref in audio_files:
        ref.start_position_samples = regions.get(ref.filename, 0)

    seen = set()
    track_names = []
    for ref in audio_files:
        if ref.track_name not in seen:
            seen.add(ref.track_name)
            track_names.append(ref.track_name)

    return LogicProject(
        name=info["name"],
        tempo=meta["tempo"],
        time_sig_numerator=meta["time_sig_numerator"],
        time_sig_denominator=meta["time_sig_denominator"],
        sample_rate=meta["sample_rate"],
        audio_files=audio_files,
        plugins=plugins,
        track_names=track_names,
        alternative=alternative,
    )
