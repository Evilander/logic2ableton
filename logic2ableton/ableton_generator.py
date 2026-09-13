"""Generate a valid gzipped XML Ableton Live Set (.als) file from a LogicProject.

Strategy: Load the real DefaultLiveSet.als template from Ableton's installation,
strip its default tracks, inject our audio tracks with clips, and set tempo/time sig.
This guarantees structural correctness since we start from a valid file.

Critical: All Id attributes in the XML must be globally unique. When cloning
template tracks, every Id is reassigned using a global counter. Locators are
the one exception: Live scopes their Ids from 0 in their own namespace (see
_write_locators), confirmed against Live 12.4.3's own saved sets.

Unwarped-clip units: Loop/LoopStart, Loop/LoopEnd, and Loop/StartRelative are
expressed in plain seconds when IsWarped is false (see
_unwarped_clip_extent), while CurrentStart/CurrentEnd stay in arrangement
beats. Verified against Live 12.4.3's own save: a 10 s file Live imported and
unwarped at 90 BPM was written with LoopEnd 10 and CurrentEnd - CurrentStart
15, and Live normalizes the WarpMarkers of an unwarped clip itself.
"""

import copy
import fnmatch
import gzip
import io
import math
import re
import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from dataclasses import replace
from typing import TYPE_CHECKING

from logic2ableton.audio import read_audio_info
from logic2ableton.ableton_metadata import encode_meter, set_global_parameter, set_tempo_automation
from logic2ableton.paths import create_output_directory, safe_name
from logic2ableton.timeline import TempoMap, beats_per_bar

from logic2ableton.models import AudioFileRef, LogicMidiTrack, LogicProject, TrackMixerState, samples_to_beats

if TYPE_CHECKING:
    from logic2ableton.timeline import TimelineMarker


_EDITIONS = ["Suite", "Trial", "Standard", "Intro", "Lite"]

_WIN_TEMPLATE_PATHS = [
    Path(f"C:/ProgramData/Ableton/Live 12 {ed}/Resources/Builtin/Templates/DefaultLiveSet.als")
    for ed in _EDITIONS
]

_MAC_TEMPLATE_PATHS = [
    Path(f"/Applications/Ableton Live 12 {ed}.app/Contents/App-Resources/Builtin/Templates/DefaultLiveSet.als")
    for ed in _EDITIONS
]


_BUNDLED_TEMPLATE = Path(__file__).parent / "data" / "DefaultLiveSet.als"


def _find_template(custom_path: Path | None = None) -> Path | None:
    """Find the Ableton default template on disk.

    Args:
        custom_path: Explicit template path (--template CLI flag). Checked first.
    """
    if custom_path is not None:
        if custom_path.exists():
            return custom_path
        return None

    paths = _MAC_TEMPLATE_PATHS if sys.platform == "darwin" else _WIN_TEMPLATE_PATHS
    for p in paths:
        if p.exists():
            return p

    if _BUNDLED_TEMPLATE.exists():
        return _BUNDLED_TEMPLATE
    return None


_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")


def _val(parent: ET.Element, tag: str, value) -> ET.Element:
    """Create a <Tag Value="value"/> child element.

    Strips control characters (illegal in XML 1.0) so a tainted string —
    e.g. a --timeline marker name — can never produce a malformed .als.
    """
    elem = ET.SubElement(parent, tag)
    elem.set("Value", _CONTROL_CHAR_RE.sub("", str(value)))
    return elem


def _format_ableton_number(value: float) -> str:
    """Format numbers without dropping meaningful fractional precision."""
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _get_audio_info(file_path: Path) -> tuple[int, int]:
    """Return source frames and rate, or zeros when the header is unsupported."""
    try:
        info = read_audio_info(file_path)
        return info.frame_count, info.sample_rate
    except (OSError, ValueError):
        return 0, 0


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
    _reassign_ids(track, allocator)

    name_elem = track.find("Name")
    if name_elem is not None:
        eff = name_elem.find("EffectiveName")
        if eff is not None:
            eff.set("Value", name)
        user = name_elem.find("UserName")
        if user is not None:
            user.set("Value", name)

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


def _unwarped_clip_extent(duration_secs: float, offset_secs: float) -> dict:
    """Loop bounds for an unwarped clip, expressed in plain seconds.

    Warped clips address Loop/WarpMarker bounds in beats, on the file's own
    warp grid. An unwarped clip plays back at its raw sample rate with no
    warp grid, so Live addresses Loop/LoopStart, Loop/LoopEnd, and
    Loop/StartRelative in seconds instead — see the module docstring.
    """
    return {
        "LoopStart": offset_secs,
        "LoopEnd": offset_secs + duration_secs,
        "StartRelative": 0.0,
    }


def _make_audio_clip_xml(
    allocator: _IdAllocator,
    ref: AudioFileRef,
    tempo_map: TempoMap,
    sample_rate: int,
    time_sig_numerator: int,
    time_sig_denominator: int,
    project_folder: Path | None = None,
    color: int = 0,
    is_warped: bool = True,
) -> ET.Element:
    """Create an AudioClip element for arrangement view.

    Each clip is placed at its BWF-derived timeline position (start_position_samples).
    Logic Pro embeds BWF timestamps in recordings; the position is extracted
    during parsing and stored in AudioFileRef.start_position_samples.
    """
    file_duration_samples, file_sample_rate = _get_audio_info(ref.file_path)
    timeline_sample_rate = ref.timeline_sample_rate or file_sample_rate or sample_rate
    source_sample_rate = file_sample_rate or sample_rate

    # Pro Tools-style regions play a slice of the source file; Logic clips
    # play the whole file (offset 0, duration None).
    offset_samples = max(0, ref.content_offset_samples)
    content_samples = ref.content_duration_samples
    if content_samples is None:
        content_samples = max(0, file_duration_samples - offset_samples)

    duration_samples = file_duration_samples
    if content_samples <= 0:
        raise ValueError(f"Cannot determine audio duration: {ref.filename}")
    duration_secs = (duration_samples / source_sample_rate if duration_samples > 0
                     else (offset_samples + content_samples) / timeline_sample_rate)
    # The content offset is a slice position within the source file, not an
    # arrangement position — it must not be run through the tempo map's
    # absolute-zero-anchored curve (that would treat it as if it sat on the
    # song timeline). Always use the flat single-tempo formula, the same one
    # TempoMap.samples_to_beats falls back to when there is no tempo map.
    offset_beats = samples_to_beats(offset_samples, tempo_map.base_tempo, timeline_sample_rate)
    offset_secs = offset_samples / timeline_sample_rate
    content_secs = content_samples / timeline_sample_rate

    # Calculate timeline position from BWF timestamp
    start_beats = tempo_map.samples_to_beats(ref.start_position_samples, timeline_sample_rate)
    start_seconds = tempo_map.beats_to_seconds(start_beats)
    # How many beats the content spans depends on where it sits once the
    # tempo changes, so measure from the clip's own position. Without a
    # timeline this is the flat single-tempo formula, kept as is so the
    # output does not drift by float rounding.
    if tempo_map.events:
        duration_beats = tempo_map.seconds_to_beats(start_seconds + content_secs) - start_beats
    else:
        duration_beats = tempo_map.samples_to_beats(content_samples, timeline_sample_rate)

    clip = ET.Element("AudioClip")
    clip.set("Id", str(allocator.next()))
    clip.set("Time", str(start_beats))

    _val(clip, "LomId", "0")
    if is_warped:
        current_end = start_beats + duration_beats
    else:
        # Anchor the elapsed-beats calculation at the clip's own arrangement
        # position so a tempo breakpoint under the clip is reflected exactly,
        # rather than assuming a flat tempo across the whole clip.
        current_end = start_beats + (
            tempo_map.seconds_to_beats(start_seconds + content_secs)
            - tempo_map.seconds_to_beats(start_seconds)
        )
    # CurrentStart/CurrentEnd are ABSOLUTE timeline positions
    _val(clip, "CurrentStart", str(start_beats))
    _val(clip, "CurrentEnd", str(current_end))

    # Loop — relative to audio content (not timeline). StartRelative selects
    # where in the source the clip starts playing.
    loop = ET.SubElement(clip, "Loop")
    if is_warped:
        _val(loop, "LoopStart", _format_ableton_number(offset_beats))
        _val(loop, "LoopEnd", _format_ableton_number(offset_beats + duration_beats))
        _val(loop, "StartRelative", "0")
    else:
        extent = _unwarped_clip_extent(content_secs, offset_secs)
        _val(loop, "LoopStart", _format_ableton_number(extent["LoopStart"]))
        _val(loop, "LoopEnd", _format_ableton_number(extent["LoopEnd"]))
        _val(loop, "StartRelative", _format_ableton_number(extent["StartRelative"]))
    _val(loop, "LoopOn", "false")

    clip_name = ref.clip_name or ref.filename.rsplit(".", 1)[0]
    _val(clip, "Name", clip_name)
    # Match the clip color to its track so the arrangement reads cleanly.
    _val(clip, "Color", str(color))
    _val(clip, "Disabled", "false")
    _val(clip, "IsWarped", "true" if is_warped else "false")

    fades = ET.SubElement(clip, "Fades")
    _val(fades, "FadeInLength", "0")
    _val(fades, "FadeOutLength", "0")
    _val(fades, "IsDefaultFadeIn", "true")
    _val(fades, "IsDefaultFadeOut", "true")

    ts_outer = ET.SubElement(clip, "TimeSignature")
    ts_list = ET.SubElement(ts_outer, "TimeSignatures")
    ts_remote = ET.SubElement(ts_list, "RemoteableTimeSignature")
    ts_remote.set("Id", str(allocator.next()))
    _val(ts_remote, "Numerator", str(time_sig_numerator))
    _val(ts_remote, "Denominator", str(time_sig_denominator))
    _val(ts_remote, "Time", "0")

    # WarpMarkers — start and end always; warped clips that cross a tempo
    # breakpoint get an extra marker at each one so Live's playback speed
    # tracks the song's actual tempo curve instead of one flat ratio for
    # the whole file. With no timeline, this reduces to the plain
    # single-tempo formula (no map lookups) so output is unchanged.
    if tempo_map.events:
        end_abs_beat = tempo_map.seconds_to_beats(start_seconds + duration_secs)
        warp_end_beat_time = end_abs_beat - start_beats
        breakpoints = tempo_map.breakpoints_between(start_beats, end_abs_beat) if is_warped else []
    else:
        warp_end_beat_time = duration_secs * tempo_map.base_tempo / 60
        breakpoints = []

    warp_markers = ET.SubElement(clip, "WarpMarkers")
    wm_start = ET.SubElement(warp_markers, "WarpMarker")
    wm_start.set("Id", str(allocator.next()))
    wm_start.set("SecTime", "0")
    wm_start.set("BeatTime", "0")
    for event in breakpoints:
        wm_mid = ET.SubElement(warp_markers, "WarpMarker")
        wm_mid.set("Id", str(allocator.next()))
        wm_mid.set("SecTime", str(tempo_map.beats_to_seconds(event.beat) - start_seconds))
        wm_mid.set("BeatTime", str(event.beat - start_beats))
    wm_end = ET.SubElement(warp_markers, "WarpMarker")
    wm_end.set("Id", str(allocator.next()))
    wm_end.set("SecTime", str(duration_secs))
    wm_end.set("BeatTime", str(warp_end_beat_time))

    # WarpMode: 0 = Beats (default for arrangement clips)
    _val(clip, "WarpMode", "0")

    # SampleRef — use absolute path so Ableton can find the audio
    sample_ref = ET.SubElement(clip, "SampleRef")
    file_ref = ET.SubElement(sample_ref, "FileRef")
    _val(file_ref, "RelativePathType", "1" if project_folder else "0")
    _val(file_ref, "RelativePath", f"Samples/Imported/{ref.filename}" if project_folder else "")
    abs_path = ((project_folder / "Samples" / "Imported" / ref.filename)
                if project_folder else ref.file_path).resolve()
    _val(file_ref, "Path", str(abs_path).replace("\\", "/"))
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
    _val(sample_ref, "DefaultSampleRate", str(source_sample_rate))

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

    comps = [c for c in clips if c.is_comp]
    if comps:
        return comps[0]

    bips = [c for c in clips if "_bip" in c.filename]
    if bips:
        return bips[0]

    return max(clips, key=lambda c: c.take_number)


def _get_clip_end_samples(ref: AudioFileRef, sample_rate: int) -> int:
    """Get the end position of a clip in samples (honoring source trims)."""
    if ref.content_duration_samples is not None:
        return ref.start_position_samples + ref.content_duration_samples
    duration_samples, _ = _get_audio_info(ref.file_path)
    return ref.start_position_samples + duration_samples


def _resolve_overlaps(clips: list[AudioFileRef], sample_rate: int) -> list[AudioFileRef]:
    """Resolve overlapping clips, keeping the best one per overlapping group.

    Clips at different timeline positions are all kept. When clips overlap
    in their actual time ranges (one starts before the other ends),
    _pick_best_clip selects the best: comp > bounce-in-place > latest take.
    """
    if not clips:
        return []
    if len(clips) == 1:
        return clips

    # Explicit arrangement regions are edits, not alternate Logic takes.
    placed = [clip for clip in clips if clip.content_duration_samples is not None]
    if placed:
        takes = [clip for clip in clips if clip.content_duration_samples is None]
        return sorted(placed + _resolve_overlaps(takes, sample_rate), key=lambda clip: clip.start_position_samples)

    sorted_clips = sorted(clips, key=lambda c: c.start_position_samples)

    # Group clips that overlap in time range using a sweep-line approach
    groups: list[list[AudioFileRef]] = []
    current_group: list[AudioFileRef] = [sorted_clips[0]]
    group_end = _get_clip_end_samples(sorted_clips[0], sample_rate)

    for clip in sorted_clips[1:]:
        if clip.start_position_samples < group_end:
            current_group.append(clip)
            clip_end = _get_clip_end_samples(clip, sample_rate)
            group_end = max(group_end, clip_end)
        else:
            groups.append(current_group)
            current_group = [clip]
            group_end = _get_clip_end_samples(clip, sample_rate)
    groups.append(current_group)

    resolved = []
    for group in groups:
        best = _pick_best_clip(group)
        if best is not None:
            resolved.append(best)
    return resolved


def _inject_clips_into_track(
    track: ET.Element,
    clips: list[AudioFileRef],
    allocator: _IdAllocator,
    tempo_map: TempoMap,
    sample_rate: int,
    time_sig_numerator: int,
    time_sig_denominator: int,
    project_folder: Path | None = None,
    color: int = 0,
    is_warped: bool = True,
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

    for existing in list(events):
        events.remove(existing)

    selected = _resolve_overlaps(clips, sample_rate)

    for ref in selected:
        clip_elem = _make_audio_clip_xml(
            allocator=allocator,
            ref=ref,
            tempo_map=tempo_map,
            sample_rate=sample_rate,
            time_sig_numerator=time_sig_numerator,
            time_sig_denominator=time_sig_denominator,
            project_folder=project_folder,
            color=color,
            is_warped=is_warped,
        )
        events.append(clip_elem)


def _matching_unwarp_patterns(track_name: str, keep_unwarped: list[str] | None) -> list[str]:
    """--keep-unwarped patterns (fnmatch, case-insensitive via casefold) matching a track name."""
    if not keep_unwarped:
        return []
    folded = track_name.casefold()
    return [pattern for pattern in keep_unwarped if fnmatch.fnmatchcase(folded, pattern.casefold())]


def unmatched_keep_unwarped_warnings(track_names: list[str], keep_unwarped: list[str] | None) -> list[str]:
    """Compatibility warnings for --keep-unwarped patterns that matched no track name.

    Track-name matching is plain string logic with no dependency on
    generate_als actually running, so this is shared by generate_als and any
    report-only caller that wants the same "pattern didn't match anything"
    diagnostic without a full conversion.
    """
    if not keep_unwarped:
        return []
    matched: set[str] = set()
    for name in track_names:
        matched.update(_matching_unwarp_patterns(name, keep_unwarped))
    return [
        f"--keep-unwarped pattern {pattern!r} did not match any track name."
        for pattern in keep_unwarped
        if pattern not in matched
    ]


def _write_locators(live_set: ET.Element, markers: list["TimelineMarker"]) -> None:
    """Write TimelineMarkers into LiveSet/Locators/Locators.

    Schema confirmed against Live 12.4.3's own saved sets and
    ableton_parser._parse_locators: <Locator Id="N"><LomId Value="0"/>
    <Time Value="beats"/><Name Value="..."/><Annotation Value=""/>
    <IsSongStart Value="false"/></Locator>, Ids sequential from 0 in their
    own namespace (not the global allocator — see the module docstring).
    """
    locators_outer = live_set.find("Locators")
    if locators_outer is None:
        return
    locators = locators_outer.find("Locators")
    if locators is None:
        locators = ET.SubElement(locators_outer, "Locators")
    for existing in list(locators):
        locators.remove(existing)

    for index, marker in enumerate(sorted(markers, key=lambda m: m.beat)):
        locator = ET.SubElement(locators, "Locator")
        locator.set("Id", str(index))
        _val(locator, "LomId", "0")
        _val(locator, "Time", _format_ableton_number(marker.beat))
        _val(locator, "Name", marker.name)
        _val(locator, "Annotation", "")
        _val(locator, "IsSongStart", "false")


def _make_midi_clip_xml(
    allocator: _IdAllocator,
    midi_track: LogicMidiTrack,
    time_sig_numerator: int,
    time_sig_denominator: int,
    color: int = 0,
) -> ET.Element:
    """Create a MidiClip element for arrangement view.

    The element shape mirrors what Ableton Live 12.2/12.3 writes for its own
    MIDI clips (captured from real Live-saved sets): notes live under
    Notes > KeyTracks > KeyTrack (one per pitch) > Notes > MidiNoteEvent with
    clip-local NoteIds, and NoteIdGenerator/NextId holds the next free id.
    Note times are relative to the clip content origin, which aligns with
    CurrentStart when StartRelative is 0.
    """
    bar_length_beats = beats_per_bar(time_sig_numerator, time_sig_denominator)
    starts = [note.start_beats for note in midi_track.notes]
    ends = [note.start_beats + note.duration_beats for note in midi_track.notes]
    clip_start = math.floor(min(starts) / bar_length_beats) * bar_length_beats
    clip_end = max(math.ceil(max(ends) / bar_length_beats) * bar_length_beats, clip_start + bar_length_beats)
    length = clip_end - clip_start

    clip = ET.Element("MidiClip")
    clip.set("Id", str(allocator.next()))
    clip.set("Time", _format_ableton_number(clip_start))

    _val(clip, "LomId", "0")
    _val(clip, "LomIdView", "0")
    _val(clip, "CurrentStart", _format_ableton_number(clip_start))
    _val(clip, "CurrentEnd", _format_ableton_number(clip_end))

    loop = ET.SubElement(clip, "Loop")
    _val(loop, "LoopStart", "0")
    _val(loop, "LoopEnd", _format_ableton_number(length))
    _val(loop, "StartRelative", "0")
    _val(loop, "LoopOn", "false")
    _val(loop, "OutMarker", _format_ableton_number(length))
    _val(loop, "HiddenLoopStart", "0")
    _val(loop, "HiddenLoopEnd", _format_ableton_number(length))

    _val(clip, "Name", midi_track.name)
    _val(clip, "Annotation", "")
    _val(clip, "Color", str(color))
    _val(clip, "LaunchMode", "0")
    _val(clip, "LaunchQuantisation", "0")

    ts_outer = ET.SubElement(clip, "TimeSignature")
    ts_list = ET.SubElement(ts_outer, "TimeSignatures")
    ts_remote = ET.SubElement(ts_list, "RemoteableTimeSignature")
    ts_remote.set("Id", str(allocator.next()))
    _val(ts_remote, "Numerator", str(time_sig_numerator))
    _val(ts_remote, "Denominator", str(time_sig_denominator))
    _val(ts_remote, "Time", "0")

    envelopes = ET.SubElement(clip, "Envelopes")
    ET.SubElement(envelopes, "Envelopes")

    scroller = ET.SubElement(clip, "ScrollerTimePreserver")
    _val(scroller, "LeftTime", "0")
    _val(scroller, "RightTime", _format_ableton_number(length))

    time_selection = ET.SubElement(clip, "TimeSelection")
    _val(time_selection, "AnchorTime", "0")
    _val(time_selection, "OtherTime", "0")

    _val(clip, "Legato", "false")
    _val(clip, "Ram", "false")

    groove = ET.SubElement(clip, "GrooveSettings")
    _val(groove, "GrooveId", "-1")

    _val(clip, "Disabled", "false")
    _val(clip, "VelocityAmount", "0")

    follow = ET.SubElement(clip, "FollowAction")
    _val(follow, "FollowTime", "4")
    _val(follow, "IsLinked", "true")
    _val(follow, "LoopIterations", "1")
    _val(follow, "FollowActionA", "4")
    _val(follow, "FollowActionB", "0")
    _val(follow, "FollowChanceA", "100")
    _val(follow, "FollowChanceB", "0")
    _val(follow, "JumpIndexA", "1")
    _val(follow, "JumpIndexB", "1")
    _val(follow, "FollowActionEnabled", "false")

    grid = ET.SubElement(clip, "Grid")
    _val(grid, "FixedNumerator", "1")
    _val(grid, "FixedDenominator", "16")
    _val(grid, "GridIntervalPixel", "20")
    _val(grid, "Ntoles", "2")
    _val(grid, "SnapToGrid", "true")
    _val(grid, "Fixed", "false")

    _val(clip, "FreezeStart", "0")
    _val(clip, "FreezeEnd", "0")
    _val(clip, "IsWarped", "true")
    _val(clip, "TakeId", "1")

    notes_elem = ET.SubElement(clip, "Notes")
    key_tracks = ET.SubElement(notes_elem, "KeyTracks")

    by_pitch: dict[int, list] = {}
    for note in midi_track.notes:
        by_pitch.setdefault(note.pitch, []).append(note)

    note_id = 1
    for key_index, pitch in enumerate(sorted(by_pitch)):
        key_track = ET.SubElement(key_tracks, "KeyTrack")
        key_track.set("Id", str(key_index))
        kt_notes = ET.SubElement(key_track, "Notes")
        for note in sorted(by_pitch[pitch], key=lambda n: n.start_beats):
            event = ET.SubElement(kt_notes, "MidiNoteEvent")
            event.set("Time", _format_ableton_number(note.start_beats - clip_start))
            event.set("Duration", _format_ableton_number(note.duration_beats))
            event.set("Velocity", str(max(1, min(127, note.velocity))))
            event.set("OffVelocity", "64")
            event.set("NoteId", str(note_id))
            note_id += 1
        _val(key_track, "MidiKey", str(pitch))

    per_note = ET.SubElement(notes_elem, "PerNoteEventStore")
    ET.SubElement(per_note, "EventLists")
    ET.SubElement(notes_elem, "NoteProbabilityGroups")
    group_gen = ET.SubElement(notes_elem, "ProbabilityGroupIdGenerator")
    _val(group_gen, "NextId", "1")
    note_gen = ET.SubElement(notes_elem, "NoteIdGenerator")
    _val(note_gen, "NextId", str(note_id))

    _val(clip, "BankSelectCoarse", "-1")
    _val(clip, "BankSelectFine", "-1")
    _val(clip, "ProgramChange", "-1")

    expression_grid = ET.SubElement(clip, "ExpressionGrid")
    _val(expression_grid, "FixedNumerator", "1")
    _val(expression_grid, "FixedDenominator", "16")
    _val(expression_grid, "GridIntervalPixel", "20")
    _val(expression_grid, "Ntoles", "2")
    _val(expression_grid, "SnapToGrid", "false")
    _val(expression_grid, "Fixed", "false")

    return clip


def _inject_midi_clip_into_track(
    track: ET.Element,
    midi_track: LogicMidiTrack,
    allocator: _IdAllocator,
    time_sig_numerator: int,
    time_sig_denominator: int,
    color: int = 0,
) -> None:
    """Inject one MidiClip with the track's notes into a cloned MidiTrack.

    MIDI arrangement clips live under MainSequencer > ClipTimeable >
    ArrangerAutomation > Events (audio clips use Sample instead of
    ClipTimeable).
    """
    if not midi_track.notes:
        return

    main_seq = track.find(".//MainSequencer")
    if main_seq is None:
        return
    clip_timeable = main_seq.find("ClipTimeable")
    if clip_timeable is None:
        return
    arranger = clip_timeable.find("ArrangerAutomation")
    if arranger is None:
        return
    events = arranger.find("Events")
    if events is None:
        events = ET.SubElement(arranger, "Events")

    for existing in list(events):
        events.remove(existing)

    events.append(
        _make_midi_clip_xml(
            allocator=allocator,
            midi_track=midi_track,
            time_sig_numerator=time_sig_numerator,
            time_sig_denominator=time_sig_denominator,
            color=color,
        )
    )


def generate_als(
    project: LogicProject,
    output_dir: Path,
    copy_audio: bool = True,
    template_path: Path | None = None,
    *,
    keep_unwarped: list[str] | None = None,
) -> Path:
    """Generate a gzipped XML Ableton Live Set (.als) file.

    Uses the real Ableton DefaultLiveSet.als as a structural template,
    then injects our tracks, clips, tempo, and time signature.

    Args:
        project: Parsed Logic Pro project.
        output_dir: Directory to write the Ableton project into.
        copy_audio: If True, copy audio files to the project's Samples/Imported folder.
        template_path: Explicit path to DefaultLiveSet.als. Auto-detected if None.
        keep_unwarped: fnmatch patterns, matched case-insensitively against
            the Logic track name, selecting tracks whose audio clips should
            be written unwarped (IsWarped=false, Loop bounds in seconds).

    Returns:
        Path to the created .als file.
    """
    output_dir = Path(output_dir)
    if not math.isfinite(project.tempo) or project.tempo <= 0:
        raise ValueError("Project tempo must be finite and positive")
    meter = encode_meter(project.time_sig_numerator, project.time_sig_denominator)

    timeline = getattr(project, "timeline", None)
    tempo_map = TempoMap(project.tempo, timeline.tempo_events if timeline is not None else [])

    resolved_template = _find_template(template_path)
    if resolved_template is None:
        raise FileNotFoundError(
            "Ableton Live 12 DefaultLiveSet.als template not found. "
            "Ensure Ableton Live 12 is installed."
        )

    project_folder = create_output_directory(output_dir, f"{project.name} Project")
    with gzip.open(resolved_template) as template:
        tree = ET.parse(template)
    root = tree.getroot()
    live_set = root.find("LiveSet")

    # Find the template AudioTrack and MidiTrack to use as structural bases
    tracks_elem = live_set.find("Tracks")
    template_audio_track = None
    template_midi_track = None
    for track in list(tracks_elem):
        if track.tag == "AudioTrack" and template_audio_track is None:
            template_audio_track = track
        elif track.tag == "MidiTrack" and template_midi_track is None:
            template_midi_track = track

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
    export_refs: list[AudioFileRef] = []
    source_names: dict[Path, str] = {}
    used_names: set[str] = set()
    for ref in project.audio_files:
        frames, _ = _get_audio_info(ref.file_path)
        content = ref.content_duration_samples
        if content is None:
            content = frames - max(0, ref.content_offset_samples)
        if content <= 0:
            warning = f"Skipped {ref.filename}: audio duration could not be read (missing or unsupported source)."
            if warning not in project.compatibility_warnings:
                project.compatibility_warnings.append(warning)
            continue
        source = ref.file_path.resolve()
        if source not in source_names:
            base = safe_name(Path(ref.filename.replace("\\", "/")).stem) + ref.file_path.suffix.lower()
            name = base
            number = 2
            while name.casefold() in used_names:
                name = f"{Path(base).stem} ({number}){Path(base).suffix}"
                number += 1
            source_names[source] = name
            used_names.add(name.casefold())
        exported = replace(ref, filename=source_names[source])
        export_refs.append(exported)
        clips_by_track.setdefault(ref.track_name, []).append(exported)

    for i, track_name in enumerate(project.track_names):
        color = i % 16
        track = _clone_track(template_audio_track, allocator, track_name, color)

        unwarp_patterns = _matching_unwarp_patterns(track_name, keep_unwarped)

        track_clips = clips_by_track.get(track_name, [])
        _inject_clips_into_track(
            track,
            track_clips,
            allocator,
            tempo_map,
            project.sample_rate,
            project.time_sig_numerator,
            project.time_sig_denominator,
            project_folder if copy_audio else None,
            color=color,
            is_warped=not unwarp_patterns,
        )

        if project.mixer_state:
            _set_mixer_state(track, project.mixer_state.get(track_name))

        tracks_elem.append(track)

    for warning in unmatched_keep_unwarped_warnings(project.track_names, keep_unwarped):
        if warning not in project.compatibility_warnings:
            project.compatibility_warnings.append(warning)

    # Create native MIDI tracks from extracted Logic MIDI sequences
    native_midi_tracks = [t for t in project.midi_tracks if t.note_count > 0]
    if native_midi_tracks:
        if template_midi_track is None:
            project.compatibility_warnings.append(
                "The Ableton template has no MIDI track to clone, so MIDI tracks were not "
                "created inside the .als; import the files from MIDI/ manually."
            )
        else:
            for j, midi_track in enumerate(native_midi_tracks):
                color = (len(project.track_names) + j) % 16
                track = _clone_track(template_midi_track, allocator, midi_track.name, color)
                _inject_midi_clip_into_track(
                    track,
                    midi_track,
                    allocator,
                    project.time_sig_numerator,
                    project.time_sig_denominator,
                    color=color,
                )
                tracks_elem.append(track)

    # Re-add return tracks (must come after audio tracks)
    for rt in return_tracks:
        tracks_elem.append(rt)

    set_tempo_automation(live_set, tempo_map, allocator)
    set_global_parameter(live_set, "TimeSignature", str(meter))

    if timeline is not None and timeline.markers:
        _write_locators(live_set, timeline.markers)

    # Update NextPointeeId to be above all allocated IDs. Must come after
    # set_tempo_automation, which can allocate new FloatEvent Ids.
    if next_id_elem is not None:
        next_id_elem.set("Value", str(allocator.current))

    root.set("Creator", "logic2ableton converter")

    # Write gzipped XML
    buffer = io.BytesIO()
    tree.write(buffer, encoding="UTF-8", xml_declaration=True)
    xml_bytes = buffer.getvalue()

    als_path = project_folder / f"{safe_name(project.name)}.als"
    with gzip.open(als_path, "wb") as f:
        f.write(xml_bytes)

    if copy_audio:
        samples_dir = project_folder / "Samples" / "Imported"
        samples_dir.mkdir(parents=True, exist_ok=True)
        for audio_ref in export_refs:
            dest = samples_dir / audio_ref.filename
            if audio_ref.file_path.exists():
                shutil.copy2(audio_ref.file_path, dest)

    return als_path
