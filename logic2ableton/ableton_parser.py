"""Parse Ableton Live `.als` files into an audio-first project model."""

from __future__ import annotations

import gzip
import xml.etree.ElementTree as ET
from pathlib import Path

from logic2ableton.models import (
    AbletonAudioClip,
    AbletonLocator,
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
        return float(_value(element, str(default)))
    except (TypeError, ValueError):
        return default


def _float_attr(element: ET.Element | None, name: str, default: float = 0.0) -> float:
    if element is None:
        return default
    try:
        return float(element.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _bool_value(element: ET.Element | None, default: bool = False) -> bool:
    raw = _value(element, "true" if default else "false").strip().lower()
    if raw in {"true", "1"}:
        return True
    if raw in {"false", "0"}:
        return False
    return default


def _live_set(root: ET.Element) -> ET.Element:
    return root.find("LiveSet") or root


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
        candidate.resolve().relative_to(project_root.resolve())
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

    if relative_path:
        normalized = relative_path.replace("\\", "/")
        if normalized.startswith("./"):
            normalized = normalized[2:]
        relative_candidate = (als_path.parent / normalized).resolve()
        if not _is_within_project(project_root, relative_candidate):
            saw_blocked_candidate = True
            relative_candidate = None

    for candidate in (relative_candidate, absolute_candidate):
        if candidate is not None and candidate.exists():
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


def _parse_clip(clip: ET.Element, als_path: Path, track_name: str, tempo: float) -> AbletonAudioClip | None:
    sample_ref = clip.find("SampleRef")
    file_ref = sample_ref.find("FileRef") if sample_ref is not None else None
    source_path, relative_source_path, source_issue = _resolve_source_path(als_path, file_ref)

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

    source_in_beats = _float_value(clip.find("Loop/StartRelative"), 0.0)

    return AbletonAudioClip(
        clip_name=clip_name,
        track_name=track_name,
        source_path=source_path,
        relative_source_path=relative_source_path,
        start_beats=start_beats,
        end_beats=end_beats,
        source_in_beats=source_in_beats,
        is_warped=_bool_value(clip.find("IsWarped")),
        is_disabled=_bool_value(clip.find("Disabled")),
        source_issue=source_issue,
    )


def _parse_audio_tracks(live_set: ET.Element, als_path: Path, tempo: float) -> list[AbletonTrack]:
    tracks: list[AbletonTrack] = []
    for track in live_set.findall(".//Tracks/AudioTrack"):
        track_name = _value(track.find("Name/EffectiveName"), "Audio Track")
        clips: list[AbletonAudioClip] = []
        for clip in track.findall(".//MainSequencer/Sample/ArrangerAutomation/Events/AudioClip"):
            parsed = _parse_clip(clip, als_path, track_name, tempo)
            if parsed is not None and not parsed.is_disabled:
                clips.append(parsed)
        tracks.append(AbletonTrack(name=track_name, clips=clips))
    return tracks


def _build_compatibility_warnings(project: AbletonProject) -> list[str]:
    warnings: list[str] = []
    clips = project.clips
    if not project.audio_tracks:
        warnings.append("No Ableton audio tracks were found in the Live Set.")
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

    warped = [clip for clip in clips if clip.is_warped]
    if warped:
        examples = ", ".join(clip.clip_name for clip in warped[:5])
        if len(warped) > 5:
            examples += ", ..."
        warnings.append(
            f"{len(warped)} clip(s) use Ableton warping; source files will be copied as references, but warp rendering must be recreated manually in Logic: {examples}"
        )

    if not clips:
        warnings.append("No arrangement audio clips were found in the Live Set.")

    return warnings


def parse_ableton_project(als_path: Path) -> AbletonProject:
    """Parse an Ableton Live Set into a transfer-friendly project model."""
    als_path = Path(als_path)
    root = _read_set_root(als_path)
    live_set = _live_set(root)

    tempo = _float_value(live_set.find(".//Transport//Tempo/Manual"), 120.0)
    time_sig = live_set.find(".//Transport//TimeSignatures/RemoteableTimeSignature")
    project = AbletonProject(
        name=_project_name(als_path, live_set),
        tempo=tempo,
        time_sig_numerator=int(_float_value(time_sig.find("Numerator") if time_sig is not None else None, 4.0)),
        time_sig_denominator=int(_float_value(time_sig.find("Denominator") if time_sig is not None else None, 4.0)),
        audio_tracks=_parse_audio_tracks(live_set, als_path, tempo),
        locators=_parse_locators(live_set),
    )
    project.compatibility_warnings = _build_compatibility_warnings(project)
    return project
