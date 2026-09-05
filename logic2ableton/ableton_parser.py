"""Parse Ableton Live `.als` files into an audio-first project model."""

from __future__ import annotations

import gzip
import math
import xml.etree.ElementTree as ET
from pathlib import Path

from logic2ableton.audio import AUDIO_SUFFIXES
from logic2ableton.ableton_metadata import decode_meter, has_global_changes, read_global_parameter

from logic2ableton.models import (
    AbletonAudioClip,
    AbletonLocator,
    AbletonMidiClip,
    AbletonMidiNote,
    AbletonMidiTrack,
    AbletonProject,
    AbletonTrack,
)


def _read_set_root(als_path: Path) -> ET.Element:
    try:
        with gzip.open(als_path, "rb") as handle:
            return ET.fromstring(handle.read())
    except OSError:
        return ET.parse(als_path).getroot()


def _value(element: ET.Element | None, default: str = "") -> str:
    if element is None:
        return default
    if "Value" in element.attrib:
        return element.get("Value", default)
    if element.text:
        return element.text
    return default


def _float_value(element: ET.Element | None, default: float = 0.0) -> float:
    try:
        value = float(_value(element, str(default)))
    except (TypeError, ValueError):
        return default
    if not math.isfinite(value):
        raise ValueError("Non-finite number in Live Set")
    return value


def _float_attr(element: ET.Element | None, name: str, default: float = 0.0) -> float:
    if element is None:
        return default
    try:
        value = float(element.get(name, str(default)))
    except (TypeError, ValueError):
        return default
    if not math.isfinite(value):
        raise ValueError("Non-finite number in Live Set")
    return value


def _bool_value(element: ET.Element | None, default: bool = False) -> bool:
    raw = _value(element, "true" if default else "false").strip().lower()
    if raw in {"true", "1"}:
        return True
    if raw in {"false", "0"}:
        return False
    return default


def _live_set(root: ET.Element) -> ET.Element:
    live_set = root.find("LiveSet")
    return live_set if live_set is not None else root


def _project_name(als_path: Path, live_set: ET.Element) -> str:
    candidates = [
        live_set.find("Name"),
        live_set.find(".//MetaData/Name"),
        live_set.find(".//ProjectName"),
    ]
    for candidate in candidates:
        value = _value(candidate)
        if value:
            return value
    return als_path.stem


def _is_within_project(project_root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(project_root)
        return True
    except ValueError:
        return False


def _resolve_source_path(als_path: Path, file_ref: ET.Element | None) -> tuple[Path | None, str | None, str | None]:
    if file_ref is None:
        return None, None, "missing-file-reference"

    absolute_path = _value(file_ref.find("Path"))
    relative_path = _value(file_ref.find("RelativePath"))
    project_root = als_path.parent.resolve()
    absolute_candidate: Path | None = None
    relative_candidate: Path | None = None
    saw_blocked_candidate = False

    if absolute_path:
        absolute_candidate = Path(absolute_path).expanduser().resolve()
        if not _is_within_project(project_root, absolute_candidate):
            saw_blocked_candidate = True
            absolute_candidate = None

    if relative_path:
        normalized = relative_path.replace("\\", "/")
        if normalized.startswith("./"):
            normalized = normalized[2:]
        relative_candidate = (als_path.parent / normalized).resolve()
        if not _is_within_project(project_root, relative_candidate):
            saw_blocked_candidate = True
            relative_candidate = None

    for candidate in (relative_candidate, absolute_candidate):
        if candidate is not None and candidate.is_file():
            if candidate.suffix.lower() not in AUDIO_SUFFIXES:
                return None, relative_path or None, "unsupported-media-type"
            return candidate, relative_path or None, None

    for candidate in (relative_candidate, absolute_candidate):
        if candidate is not None:
            return candidate, relative_path or None, "missing-file-reference"

    if saw_blocked_candidate:
        return None, relative_path or None, "external-media-blocked"
    return None, relative_path or None, "missing-file-reference"


def _parse_locators(live_set: ET.Element) -> list[AbletonLocator]:
    locators: list[AbletonLocator] = []
    locator_groups = [
        live_set.find("Locators/Locators"),
        live_set.find("Locators"),
    ]
    seen: set[tuple[str, float]] = set()
    for group in locator_groups:
        if group is None:
            continue
        for locator in group.findall("Locator"):
            name = _value(locator.find("Name"), "Locator")
            time_beats = _float_value(locator.find("Time"), 0.0)
            key = (name, time_beats)
            if key in seen:
                continue
            seen.add(key)
            locators.append(AbletonLocator(name=name, time_beats=time_beats))
    return locators


def _parse_clip(
    clip: ET.Element, als_path: Path, track_name: str, tempo: float,
    source_cache: dict[tuple[str, str], tuple[Path | None, str | None, str | None]] | None = None,
) -> AbletonAudioClip | None:
    sample_ref = clip.find("SampleRef")
    file_ref = sample_ref.find("FileRef") if sample_ref is not None else None
    source_key = (_value(file_ref.find("Path")), _value(file_ref.find("RelativePath"))) if file_ref is not None else ("", "")
    if source_cache is None:
        source_path, relative_source_path, source_issue = _resolve_source_path(als_path, file_ref)
    else:
        if source_key not in source_cache:
            source_cache[source_key] = _resolve_source_path(als_path, file_ref)
        source_path, relative_source_path, source_issue = source_cache[source_key]

    start_beats = _float_value(clip.find("CurrentStart"), _float_attr(clip, "Time", 0.0))
    end_beats = _float_value(clip.find("CurrentEnd"), start_beats)
    if end_beats <= start_beats:
        duration_samples = _float_value(sample_ref.find("DefaultDuration") if sample_ref is not None else None, 0.0)
        sample_rate = _float_value(sample_ref.find("DefaultSampleRate") if sample_ref is not None else None, 0.0)
        if duration_samples > 0 and sample_rate > 0:
            duration_seconds = duration_samples / sample_rate
            end_beats = start_beats + (duration_seconds / 60.0) * tempo

    clip_name = _value(clip.find("Name"))
    if not clip_name and source_path is not None:
        clip_name = source_path.stem
    if not clip_name:
        clip_name = f"{track_name} clip"

    is_warped = _bool_value(clip.find("IsWarped"))
    source_start = _float_value(clip.find("Loop/LoopStart")) + _float_value(clip.find("Loop/StartRelative"))
    source_in_seconds = source_start
    if is_warped:
        # Warp markers map content beats to source-file seconds. Interpolate
        # the start marker even when subsequent time stretching is approximate.
        markers = sorted({
            (_float_attr(marker, "BeatTime"), _float_attr(marker, "SecTime"))
            for marker in clip.findall("WarpMarkers/WarpMarker")
        })
        source_in_seconds = source_start * 60 / tempo
        if markers:
            source_in_seconds = markers[0][1] + (source_start - markers[0][0]) * 60 / tempo
        for left, right in zip(markers, markers[1:]):
            if right[0] <= left[0]:
                continue
            source_in_seconds = left[1] + (source_start - left[0]) * (right[1] - left[1]) / (right[0] - left[0])
            if source_start <= right[0]:
                break
    source_in_beats = source_start if is_warped else source_in_seconds * tempo / 60

    return AbletonAudioClip(
        clip_name=clip_name,
        track_name=track_name,
        source_path=source_path,
        relative_source_path=relative_source_path,
        start_beats=start_beats,
        end_beats=end_beats,
        source_in_beats=source_in_beats,
        source_in_seconds=max(0.0, source_in_seconds),
        is_warped=is_warped,
        is_disabled=_bool_value(clip.find("Disabled")),
        source_issue=source_issue,
    )


def _parse_audio_tracks(live_set: ET.Element, als_path: Path, tempo: float) -> list[AbletonTrack]:
    tracks: list[AbletonTrack] = []
    source_cache: dict[tuple[str, str], tuple[Path | None, str | None, str | None]] = {}
    for track in live_set.findall(".//Tracks/AudioTrack"):
        track_name = _value(track.find("Name/EffectiveName"), "Audio Track")
        clips: list[AbletonAudioClip] = []
        for clip in track.findall(".//MainSequencer/Sample/ArrangerAutomation/Events/AudioClip"):
            parsed = _parse_clip(clip, als_path, track_name, tempo, source_cache)
            if parsed is not None and not parsed.is_disabled:
                clips.append(parsed)
        tracks.append(AbletonTrack(name=track_name, clips=clips))
    return tracks


_BEAT_EPSILON = 1e-9


def _render_clip_notes(
    events: list[tuple[int, float, float, int]],
    *,
    clip_start: float,
    clip_end: float,
    loop_start: float,
    loop_end: float,
    start_relative: float,
    loop_on: bool,
) -> list[AbletonMidiNote]:
    """Place clip-content notes on the arrangement the way Live plays them.

    Verified against Live 12.4.3 by consolidating clips with known notes:
    playback starts at loop_start + start_relative and runs to loop_end, then
    wraps to loop_start until the clip's arrangement length is used up (Live
    wraps even when the loop switch is off). Notes are cut at the loop end and
    at the clip end. With the loop switch on, notes that start outside the loop
    brace never sound; otherwise a note already sounding at the start marker
    plays from the clip start for its remaining length.
    """
    clip_length = clip_end - clip_start
    loop_length = loop_end - loop_start
    if clip_length <= _BEAT_EPSILON or loop_length <= _BEAT_EPSILON:
        return []
    play_start = loop_start + start_relative
    if play_start < loop_start or play_start >= loop_end:
        play_start = loop_start
    if loop_on:
        events = [event for event in events if loop_start <= event[1] < loop_end]

    notes: list[AbletonMidiNote] = []
    elapsed = 0.0
    window_start = play_start
    first_pass = True
    while elapsed < clip_length - _BEAT_EPSILON:
        window_length = min(loop_end - window_start, clip_length - elapsed)
        window_end = window_start + window_length
        for pitch, time, duration, velocity in events:
            note_end = time + duration
            if first_pass and time < window_start < note_end:
                onset = window_start
            elif window_start <= time < window_end - _BEAT_EPSILON:
                onset = time
            else:
                continue
            played = min(note_end, window_end) - onset
            if played <= _BEAT_EPSILON:
                continue
            notes.append(
                AbletonMidiNote(
                    pitch=pitch,
                    start_beats=clip_start + elapsed + (onset - window_start),
                    duration_beats=played,
                    velocity=velocity,
                )
            )
        elapsed += window_length
        first_pass = False
        window_start = loop_start
    return notes


def _parse_midi_clip(clip: ET.Element, track_name: str, tempo: float) -> AbletonMidiClip | None:
    start_beats = _float_value(clip.find("CurrentStart"), _float_attr(clip, "Time", 0.0))
    end_beats = _float_value(clip.find("CurrentEnd"), start_beats)

    clip_name = _value(clip.find("Name")) or f"{track_name} clip"
    is_disabled = _bool_value(clip.find("Disabled"))
    is_looping = _bool_value(clip.find("Loop/LoopOn"))
    # Notes are timed relative to the clip's content origin (1.1.1 == 0 beats);
    # the loop brace and start marker decide which of them play and where.
    events: list[tuple[int, float, float, int]] = []
    for key_track in clip.findall(".//KeyTrack"):
        pitch = int(_float_value(key_track.find("MidiKey"), -1.0))
        if pitch < 0 or pitch > 127:
            continue
        for event in key_track.findall(".//MidiNoteEvent"):
            if event.get("IsEnabled", "true").strip().lower() in {"false", "0"}:
                continue
            note_time = _float_attr(event, "Time", 0.0)
            duration = _float_attr(event, "Duration", 0.0)
            velocity = int(round(_float_attr(event, "Velocity", 100.0)))
            if duration <= 0:
                continue
            events.append((pitch, note_time, duration, max(1, min(127, velocity))))

    notes = _render_clip_notes(
        events,
        clip_start=start_beats,
        clip_end=max(end_beats, start_beats),
        loop_start=_float_value(clip.find("Loop/LoopStart"), 0.0),
        loop_end=_float_value(clip.find("Loop/LoopEnd"), max(end_beats, start_beats) - start_beats),
        start_relative=_float_value(clip.find("Loop/StartRelative"), 0.0),
        loop_on=is_looping,
    )
    notes = [note for note in notes if note.start_beats >= 0]
    notes.sort(key=lambda note: (note.start_beats, note.pitch))
    return AbletonMidiClip(
        clip_name=clip_name,
        track_name=track_name,
        start_beats=start_beats,
        end_beats=end_beats if end_beats > start_beats else start_beats,
        notes=notes,
        is_disabled=is_disabled,
        is_looping=is_looping,
    )


def _parse_midi_tracks(live_set: ET.Element, tempo: float) -> list[AbletonMidiTrack]:
    tracks: list[AbletonMidiTrack] = []
    for track in live_set.findall(".//Tracks/MidiTrack"):
        track_name = _value(track.find("Name/EffectiveName"), "MIDI Track")
        clips: list[AbletonMidiClip] = []
        for clip in track.findall(".//ArrangerAutomation/Events/MidiClip"):
            parsed = _parse_midi_clip(clip, track_name, tempo)
            if parsed is not None and not parsed.is_disabled:
                clips.append(parsed)
        tracks.append(AbletonMidiTrack(name=track_name, clips=clips))
    return tracks


def _build_compatibility_warnings(project: AbletonProject) -> list[str]:
    warnings: list[str] = []
    clips = project.clips
    if not project.audio_tracks and not project.midi_tracks:
        warnings.append("No Ableton audio or MIDI tracks were found in the Live Set.")
        return warnings

    missing_files = [clip for clip in clips if clip.source_issue == "missing-file-reference"]
    if missing_files:
        examples = ", ".join(clip.clip_name for clip in missing_files[:5])
        if len(missing_files) > 5:
            examples += ", ..."
        warnings.append(
            f"{len(missing_files)} clip(s) referenced audio that could not be resolved from the Live Set: {examples}"
        )

    blocked_external = [clip for clip in clips if clip.source_issue == "external-media-blocked"]
    if blocked_external:
        examples = ", ".join(clip.clip_name for clip in blocked_external[:5])
        if len(blocked_external) > 5:
            examples += ", ..."
        warnings.append(
            f"{len(blocked_external)} clip(s) referenced audio outside the Ableton project folder and were blocked for safety: {examples}"
        )

    unsupported = [clip for clip in clips if clip.source_issue == "unsupported-media-type"]
    if unsupported:
        warnings.append(f"{len(unsupported)} clip(s) referenced unsupported media types and were not copied.")

    warped = [clip for clip in clips if clip.is_warped]
    if warped:
        examples = ", ".join(clip.clip_name for clip in warped[:5])
        if len(warped) > 5:
            examples += ", ..."
        warnings.append(
            f"{len(warped)} clip(s) use Ableton warping; source start offsets are preserved, but time stretching and loop playback require review in the destination DAW: {examples}"
        )

    if not clips and not project.midi_tracks:
        warnings.append("No arrangement audio clips were found in the Live Set.")

    return warnings


def parse_ableton_project(als_path: Path) -> AbletonProject:
    """Parse an Ableton Live Set into a transfer-friendly project model."""
    als_path = Path(als_path)
    root = _read_set_root(als_path)
    live_set = _live_set(root)

    tempo = read_global_parameter(live_set, "Tempo", 120.0)
    if not math.isfinite(tempo) or tempo <= 0:
        raise ValueError("The Live Set has an invalid tempo")
    time_sig = live_set.find(".//Transport//TimeSignatures/RemoteableTimeSignature")
    numerator, denominator = decode_meter(int(read_global_parameter(live_set, "TimeSignature", 201)))
    if time_sig is not None and live_set.find("MainTrack") is None and live_set.find("MasterTrack") is None:
        numerator = int(_float_value(time_sig.find("Numerator"), 4))
        denominator = int(_float_value(time_sig.find("Denominator"), 4))
    project = AbletonProject(
        name=_project_name(als_path, live_set),
        tempo=tempo,
        time_sig_numerator=numerator,
        time_sig_denominator=denominator,
        audio_tracks=_parse_audio_tracks(live_set, als_path, tempo),
        locators=_parse_locators(live_set),
        midi_tracks=_parse_midi_tracks(live_set, tempo),
    )
    project.compatibility_warnings = _build_compatibility_warnings(project)
    for parameter in ("Tempo", "TimeSignature"):
        if has_global_changes(live_set, parameter):
            project.compatibility_warnings.append(
                f"Arrangement {parameter} changes are not transferred; only the value at project start is used."
            )
    return project
