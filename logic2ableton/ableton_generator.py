"""Generate a valid gzipped XML Ableton Live Set (.als) file from a LogicProject.

Strategy: Load the real DefaultLiveSet.als template from Ableton's installation,
strip its default tracks, inject our audio tracks with clips, and set tempo/time sig.
This guarantees structural correctness since we start from a valid file.

Critical: All Id attributes in the XML must be globally unique. When cloning
template tracks, every Id is reassigned using a global counter.
"""

import copy
import gzip
import io
import shutil
import struct
import wave
import xml.etree.ElementTree as ET
from pathlib import Path

from logic2ableton.models import AudioFileRef, LogicProject, TrackMixerState


# Paths to the Ableton default template (installed with Live 12)
_TEMPLATE_PATHS = [
    Path("C:/ProgramData/Ableton/Live 12 Suite/Resources/Builtin/Templates/DefaultLiveSet.als"),
    Path("C:/ProgramData/Ableton/Live 12 Trial/Resources/Builtin/Templates/DefaultLiveSet.als"),
    Path("C:/ProgramData/Ableton/Live 12 Standard/Resources/Builtin/Templates/DefaultLiveSet.als"),
    Path("C:/ProgramData/Ableton/Live 12 Intro/Resources/Builtin/Templates/DefaultLiveSet.als"),
]


def _find_template() -> Path | None:
    """Find the Ableton default template on disk."""
    for p in _TEMPLATE_PATHS:
        if p.exists():
            return p
    return None


def _val(parent: ET.Element, tag: str, value) -> ET.Element:
    """Create a <Tag Value="value"/> child element."""
    elem = ET.SubElement(parent, tag)
    elem.set("Value", str(value))
    return elem


def _get_audio_info(file_path: Path) -> tuple[int, int]:
    """Read audio file header. Returns (nframes, sample_rate).

    Supports both WAV (RIFF) and AIFF (FORM/AIFF) formats.
    """
    ext = file_path.suffix.lower()

    # Try WAV first
    if ext == ".wav":
        try:
            with wave.open(str(file_path), "rb") as w:
                return w.getnframes(), w.getframerate()
        except Exception:
            return 0, 44100

    # AIFF: parse COMM chunk for nframes and sample rate
    if ext in (".aif", ".aiff"):
        try:
            with open(file_path, "rb") as f:
                form = f.read(4)
                if form != b"FORM":
                    return 0, 44100
                file_size = struct.unpack(">I", f.read(4))[0]
                f.read(4)  # AIFF or AIFC
                while f.tell() < file_size + 8:
                    chunk_id = f.read(4)
                    if len(chunk_id) < 4:
                        break
                    chunk_size = struct.unpack(">I", f.read(4))[0]
                    if chunk_id == b"COMM":
                        data = f.read(chunk_size)
                        nframes = struct.unpack(">I", data[2:6])[0]
                        # Decode 80-bit IEEE 754 extended float for sample rate
                        sr_bytes = data[8:18]
                        exponent = ((sr_bytes[0] & 0x7F) << 8) | sr_bytes[1]
                        mantissa = int.from_bytes(sr_bytes[2:10], "big")
                        sample_rate = mantissa * (2.0 ** (exponent - 16383 - 63))
                        return nframes, int(sample_rate)
                    else:
                        f.seek(chunk_size + (chunk_size % 2), 1)
        except Exception:
            pass
        return 0, 44100

    return 0, 44100


class _IdAllocator:
    """Allocates globally unique integer IDs for Ableton XML elements."""

    def __init__(self, start: int):
        self._next = start

    def next(self) -> int:
        val = self._next
        self._next += 1
        return val

    @property
    def current(self) -> int:
        return self._next


def _reassign_ids(element: ET.Element, allocator: _IdAllocator) -> None:
    """Recursively reassign all Id attributes in an element tree to unique values."""
    if "Id" in element.attrib:
        element.set("Id", str(allocator.next()))
    for child in element:
        _reassign_ids(child, allocator)


def _clone_track(template_track: ET.Element, allocator: _IdAllocator, name: str, color: int) -> ET.Element:
    """Clone a template AudioTrack with unique IDs and custom name/color."""
    track = copy.deepcopy(template_track)

    # Reassign ALL Id attributes to globally unique values
    _reassign_ids(track, allocator)

    # Set name
    name_elem = track.find("Name")
    if name_elem is not None:
        eff = name_elem.find("EffectiveName")
        if eff is not None:
            eff.set("Value", name)
        user = name_elem.find("UserName")
        if user is not None:
            user.set("Value", name)

    # Set color
    color_elem = track.find("Color")
    if color_elem is not None:
        color_elem.set("Value", str(color))

    return track


def _set_mixer_state(track: ET.Element, mixer_state: TrackMixerState | None) -> None:
    """Set volume, pan, mute, and solo on an AudioTrack mixer."""
    if mixer_state is None:
        return

    mixer = track.find(".//DeviceChain/Mixer")
    if mixer is None:
        return

    vol_elem = mixer.find("Volume/Manual")
    if vol_elem is not None:
        vol_elem.set("Value", str(mixer_state.volume_linear))

    pan_elem = mixer.find("Pan/Manual")
    if pan_elem is not None:
        pan_elem.set("Value", str(max(-1.0, min(1.0, mixer_state.pan))))

    # Speaker/Manual is true when unmuted.
    speaker_elem = mixer.find("Speaker/Manual")
    if speaker_elem is not None:
        speaker_elem.set("Value", "false" if mixer_state.is_muted else "true")

    solo_elem = mixer.find("SoloSink")
    if solo_elem is not None:
        solo_elem.set("Value", "true" if mixer_state.is_soloed else "false")


def _make_audio_clip_xml(
    allocator: _IdAllocator,
    ref: AudioFileRef,
    tempo: float,
    sample_rate: int,
    project_folder: Path | None = None,
) -> ET.Element:
    """Create an AudioClip element for arrangement view.

    Each clip is placed at its BWF-derived timeline position (start_position_samples).
    Logic Pro embeds BWF timestamps in recordings; the position is extracted
    during parsing and stored in AudioFileRef.start_position_samples.
    """
    # Get duration and sample rate from WAV header
    duration_samples, file_sample_rate = _get_audio_info(ref.file_path)
    timeline_sample_rate = file_sample_rate if file_sample_rate > 0 else sample_rate
    duration_beats = (duration_samples * tempo / (timeline_sample_rate * 60)) if duration_samples > 0 else 4.0
    duration_secs = duration_samples / file_sample_rate if duration_samples > 0 else 2.0

    # Calculate timeline position from BWF timestamp
    start_beats = ref.start_position_samples * tempo / (timeline_sample_rate * 60)

    clip = ET.Element("AudioClip")
    clip.set("Id", str(allocator.next()))
    clip.set("Time", str(start_beats))

    _val(clip, "LomId", "0")
    # CurrentStart/CurrentEnd are ABSOLUTE timeline positions
    _val(clip, "CurrentStart", str(start_beats))
    _val(clip, "CurrentEnd", str(start_beats + duration_beats))

    # Loop — relative to audio content (not timeline)
    loop = ET.SubElement(clip, "Loop")
    _val(loop, "LoopStart", "0")
    _val(loop, "LoopEnd", str(duration_beats))
    _val(loop, "StartRelative", "0")
    _val(loop, "LoopOn", "false")

    # Name (stem without extension)
    clip_name = ref.filename.rsplit(".", 1)[0]
    _val(clip, "Name", clip_name)
    _val(clip, "Color", "0")
    _val(clip, "Disabled", "false")
    _val(clip, "IsWarped", "true")

    # Fades
    fades = ET.SubElement(clip, "Fades")
    _val(fades, "FadeInLength", "0")
    _val(fades, "FadeOutLength", "0")
    _val(fades, "IsDefaultFadeIn", "true")
    _val(fades, "IsDefaultFadeOut", "true")

    # TimeSignature
    ts_outer = ET.SubElement(clip, "TimeSignature")
    ts_list = ET.SubElement(ts_outer, "TimeSignatures")
    ts_remote = ET.SubElement(ts_list, "RemoteableTimeSignature")
    ts_remote.set("Id", str(allocator.next()))
    _val(ts_remote, "Numerator", "4")
    _val(ts_remote, "Denominator", "4")
    _val(ts_remote, "Time", "0")

    # WarpMarkers — two markers: start and end
    warp_markers = ET.SubElement(clip, "WarpMarkers")
    wm_start = ET.SubElement(warp_markers, "WarpMarker")
    wm_start.set("Id", str(allocator.next()))
    wm_start.set("SecTime", "0")
    wm_start.set("BeatTime", "0")
    wm_end = ET.SubElement(warp_markers, "WarpMarker")
    wm_end.set("Id", str(allocator.next()))
    wm_end.set("SecTime", str(duration_secs))
    wm_end.set("BeatTime", str(duration_beats))

    # WarpMode: 0 = Beats (default for arrangement clips)
    _val(clip, "WarpMode", "0")

    # SampleRef — use absolute path so Ableton can find the audio
    sample_ref = ET.SubElement(clip, "SampleRef")
    file_ref = ET.SubElement(sample_ref, "FileRef")
    _val(file_ref, "RelativePathType", "1")
    _val(file_ref, "RelativePath", f"Samples/Imported/{ref.filename}")
    if project_folder:
        abs_path = (project_folder / "Samples" / "Imported" / ref.filename).resolve()
        _val(file_ref, "Path", str(abs_path).replace("\\", "/"))
    else:
        _val(file_ref, "Path", "")
    _val(file_ref, "Type", "1")
    _val(file_ref, "LivePackName", "")
    _val(file_ref, "LivePackId", "")
    try:
        _val(file_ref, "OriginalFileSize", str(ref.file_path.stat().st_size))
    except Exception:
        _val(file_ref, "OriginalFileSize", "0")
    _val(file_ref, "OriginalCrc", "0")
    # LastModDate — file modification time as Unix timestamp (in SampleRef, not FileRef)
    try:
        _val(sample_ref, "LastModDate", str(int(ref.file_path.stat().st_mtime)))
    except Exception:
        pass
    ET.SubElement(sample_ref, "SourceContext")
    _val(sample_ref, "SampleUsageHint", "0")
    _val(sample_ref, "DefaultDuration", str(duration_samples))
    _val(sample_ref, "DefaultSampleRate", str(file_sample_rate))

    # Envelopes
    envelopes = ET.SubElement(clip, "Envelopes")
    ET.SubElement(envelopes, "Envelopes")

    return clip


def _pick_best_clip(clips: list[AudioFileRef]) -> AudioFileRef | None:
    """Pick the best clip for a track from multiple overlapping takes.

    Priority: comp file > bounce-in-place > latest take (highest take number).
    """
    if not clips:
        return None
    if len(clips) == 1:
        return clips[0]

    # Prefer comp files
    comps = [c for c in clips if c.is_comp]
    if comps:
        return comps[0]

    # Prefer bounce-in-place files
    bips = [c for c in clips if "_bip" in c.filename]
    if bips:
        return bips[0]

    # Fall back to latest take (highest take number)
    return max(clips, key=lambda c: c.take_number)


def _get_clip_end_samples(ref: AudioFileRef, sample_rate: int) -> int:
    """Get the end position of a clip in samples."""
    duration_samples, _ = _get_audio_info(ref.file_path)
    return ref.start_position_samples + duration_samples


def _resolve_overlaps(clips: list[AudioFileRef], tempo: float, sample_rate: int) -> list[AudioFileRef]:
    """Resolve overlapping clips, keeping the best one per overlapping group.

    Clips at different timeline positions are all kept. When clips overlap
    in their actual time ranges (one starts before the other ends),
    _pick_best_clip selects the best: comp > bounce-in-place > latest take.
    """
    if not clips:
        return []
    if len(clips) == 1:
        return clips

    # Sort by start position
    sorted_clips = sorted(clips, key=lambda c: c.start_position_samples)

    # Group clips that overlap in time range using a sweep-line approach
    groups: list[list[AudioFileRef]] = []
    current_group: list[AudioFileRef] = [sorted_clips[0]]
    group_end = _get_clip_end_samples(sorted_clips[0], sample_rate)

    for clip in sorted_clips[1:]:
        if clip.start_position_samples < group_end:
            # Overlaps with current group
            current_group.append(clip)
            clip_end = _get_clip_end_samples(clip, sample_rate)
            group_end = max(group_end, clip_end)
        else:
            groups.append(current_group)
            current_group = [clip]
            group_end = _get_clip_end_samples(clip, sample_rate)
    groups.append(current_group)

    # Pick best clip from each group
    result = []
    for group in groups:
        best = _pick_best_clip(group)
        if best is not None:
            result.append(best)
    return result


def _inject_clips_into_track(
    track: ET.Element,
    clips: list[AudioFileRef],
    allocator: _IdAllocator,
    tempo: float,
    sample_rate: int,
    project_folder: Path | None = None,
) -> None:
    """Inject AudioClip elements into a track's arrangement view.

    Clips are placed at their BWF-derived timeline positions. Overlapping clips
    at the same position are resolved (best clip selected); clips at different
    positions are all included.
    """
    if not clips:
        return

    # Find the Sample > ArrangerAutomation > Events path in MainSequencer
    main_seq = track.find(".//MainSequencer")
    if main_seq is None:
        return

    sample = main_seq.find("Sample")
    if sample is None:
        return

    arranger = sample.find("ArrangerAutomation")
    if arranger is None:
        return

    events = arranger.find("Events")
    if events is None:
        events = ET.SubElement(arranger, "Events")

    # Clear any existing clips
    for existing in list(events):
        events.remove(existing)

    # Resolve overlapping clips, keep all non-overlapping
    selected = _resolve_overlaps(clips, tempo, sample_rate)

    for ref in selected:
        clip_elem = _make_audio_clip_xml(
            allocator=allocator,
            ref=ref,
            tempo=tempo,
            sample_rate=sample_rate,
            project_folder=project_folder,
        )
        events.append(clip_elem)


def generate_als(
    project: LogicProject,
    output_dir: Path,
    copy_audio: bool = True,
) -> Path:
    """Generate a gzipped XML Ableton Live Set (.als) file.

    Uses the real Ableton DefaultLiveSet.als as a structural template,
    then injects our tracks, clips, tempo, and time signature.

    Args:
        project: Parsed Logic Pro project.
        output_dir: Directory to write the Ableton project into.
        copy_audio: If True, copy audio files to the project's Samples/Imported folder.

    Returns:
        Path to the created .als file.
    """
    output_dir = Path(output_dir)
    project_folder = output_dir / f"{project.name} Project"
    project_folder.mkdir(parents=True, exist_ok=True)

    # Load the real Ableton template
    template_path = _find_template()
    if template_path is None:
        raise FileNotFoundError(
            "Ableton Live 12 DefaultLiveSet.als template not found. "
            "Ensure Ableton Live 12 is installed."
        )

    tree = ET.parse(gzip.open(template_path))
    root = tree.getroot()
    live_set = root.find("LiveSet")

    # Find the template AudioTrack to use as a structural base
    tracks_elem = live_set.find("Tracks")
    template_audio_track = None
    for track in list(tracks_elem):
        if track.tag == "AudioTrack":
            template_audio_track = track
            break

    if template_audio_track is None:
        raise RuntimeError("No AudioTrack found in Ableton template")

    # Remove Audio and MIDI tracks but keep ReturnTracks (send bus)
    return_tracks = []
    for track in list(tracks_elem):
        if track.tag == "ReturnTrack":
            return_tracks.append(track)
        tracks_elem.remove(track)

    # Initialize ID allocator starting after all existing IDs in the template
    next_id_elem = live_set.find("NextPointeeId")
    start_id = int(next_id_elem.get("Value")) if next_id_elem is not None else 30000
    allocator = _IdAllocator(start_id)

    # Group audio files by track name
    clips_by_track: dict[str, list[AudioFileRef]] = {}
    for ref in project.audio_files:
        clips_by_track.setdefault(ref.track_name, []).append(ref)

    # Create one audio track per Logic track
    for i, track_name in enumerate(project.track_names):
        color = i % 16
        track = _clone_track(template_audio_track, allocator, track_name, color)

        # Inject clips into the track's arrangement view
        track_clips = clips_by_track.get(track_name, [])
        _inject_clips_into_track(
            track, track_clips, allocator, project.tempo, project.sample_rate, project_folder
        )

        # Apply mixer values when present.
        if project.mixer_state:
            _set_mixer_state(track, project.mixer_state.get(track_name))

        tracks_elem.append(track)

    # Re-add return tracks (must come after audio tracks)
    for rt in return_tracks:
        tracks_elem.append(rt)

    # Update NextPointeeId to be above all allocated IDs
    if next_id_elem is not None:
        next_id_elem.set("Value", str(allocator.current))

    # Set tempo on MainTrack mixer
    main_track = live_set.find("MainTrack")
    if main_track is not None:
        tempo_elem = main_track.find(".//Tempo/Manual")
        if tempo_elem is not None:
            tempo_elem.set("Value", str(int(project.tempo)))

    # Set tempo in Transport
    transport = live_set.find("Transport")
    if transport is not None:
        tempo_manual = transport.find(".//Tempo/Manual")
        if tempo_manual is not None:
            tempo_manual.set("Value", str(int(project.tempo)))

        # Set time signature
        ts = transport.find(".//TimeSignatures/RemoteableTimeSignature")
        if ts is not None:
            num = ts.find("Numerator")
            if num is not None:
                num.set("Value", str(project.time_sig_numerator))
            den = ts.find("Denominator")
            if den is not None:
                den.set("Value", str(project.time_sig_denominator))

    # Update the Creator attribute
    root.set("Creator", "logic2ableton converter")

    # Write gzipped XML
    buffer = io.BytesIO()
    tree.write(buffer, encoding="UTF-8", xml_declaration=True)
    xml_bytes = buffer.getvalue()

    als_path = project_folder / f"{project.name}.als"
    with gzip.open(als_path, "wb") as f:
        f.write(xml_bytes)

    # Copy audio files if requested
    if copy_audio:
        samples_dir = project_folder / "Samples" / "Imported"
        samples_dir.mkdir(parents=True, exist_ok=True)
        for audio_ref in project.audio_files:
            dest = samples_dir / audio_ref.filename
            if audio_ref.file_path.exists():
                shutil.copy2(audio_ref.file_path, dest)

    return als_path
