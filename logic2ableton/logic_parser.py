import plistlib
import re
import struct
from pathlib import Path

from logic2ableton.models import AudioFileRef, LogicProject, PluginInstance, parse_audio_filename


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
                else:
                    f.seek(chunk_size + (chunk_size % 2), 1)
    except Exception:
        pass
    return None


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
            form = f.read(4)
            if form != b"FORM":
                return None, 0
            file_size = struct.unpack(">I", f.read(4))[0]
            aiff_id = f.read(4)  # AIFF or AIFC
            if aiff_id not in (b"AIFF", b"AIFC"):
                return None, 0
            while f.tell() < file_size + 8:
                chunk_id = f.read(4)
                if len(chunk_id) < 4:
                    break
                chunk_size = struct.unpack(">I", f.read(4))[0]
                if chunk_id == b"MARK":
                    data = f.read(chunk_size)
                    if chunk_size % 2:
                        f.read(1)  # pad byte
                    if len(data) < 2:
                        continue
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
                        # Advance past name + pad byte (AIFF pstrings are even-padded)
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
                    skip = chunk_size + (chunk_size % 2)
                    f.seek(skip, 1)
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
    elif ext in (".aif", ".aiff"):
        timestamp, start_offset = _get_aiff_timestamp(file_path)
        if timestamp is not None:
            # Timestamp = where frame 0 of the file is on SMPTE timeline
            # start_offset = where content begins within the file (after pre-roll)
            # Content position on timeline = Timestamp + start_offset
            return timestamp + start_offset
        return None
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
    audio_dir = logicx_path / "Media" / "Audio Files"
    if not audio_dir.exists():
        return {}

    # Detect sample rate from metadata for SMPTE offset calculation
    meta_path = logicx_path / "Alternatives" / f"{alternative:03d}" / "MetaData.plist"
    sample_rate = 44100
    try:
        with open(meta_path, "rb") as f:
            meta = plistlib.load(f)
        sample_rate = meta.get("SampleRate", 44100)
    except Exception:
        pass

    smpte_offset = 3600 * sample_rate  # 1 hour in samples

    regions: dict[str, int] = {}
    for audio_file in audio_dir.iterdir():
        if not audio_file.is_file() or audio_file.suffix.lower() not in (".wav", ".aif", ".aiff"):
            continue
        time_ref = _get_audio_time_reference(audio_file)
        if time_ref is not None:
            start_samples = max(0, time_ref - smpte_offset)
            regions[audio_file.name] = start_samples

    return regions


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

    # Read ProjectData once, share across extractors
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
