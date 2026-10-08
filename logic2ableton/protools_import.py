"""Map parsed Pro Tools sessions onto the transfer models both lanes consume.

Pro Tools stores stereo tracks as per-channel lanes that share one track name.
Lanes referencing an interleaved source are merged; split-mono sources remain
separate left/right tracks with explicit channel placement.

Audio timeline positions are samples, so the sample->beat conversion needs a
tempo. Session tempo is not recoverable from .ptx yet; callers pass one (CLI
--tempo) or the default applies. Audio placement survives a wrong tempo in
the Logic lane (beats round-trip back to the same samples), but in Ableton
the set's tempo must match the conversion tempo for clips to sit at the
correct real-time positions - the warning spells that out.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from logic2ableton.audio import AUDIO_SUFFIXES, read_audio_info
from logic2ableton.paths import contained_source_path

from logic2ableton.models import (
    AbletonAudioClip,
    AbletonMidiClip,
    AbletonMidiNote,
    AbletonMidiTrack,
    AbletonProject,
    AbletonTrack,
    AudioFileRef,
    LogicMidiNote,
    LogicMidiTrack,
    LogicProject,
    TrackMixerState,
)
from logic2ableton.protools_parser import ProToolsRegion, ProToolsSession, ProToolsTrack

DEFAULT_PROTOOLS_TEMPO = 120.0

_CHANNEL_SUFFIX = re.compile(r"\.(L|R|C|Ls|Rs|Lss|Rss|LFE)$")


def _strip_channel_suffix(name: str) -> str:
    return _CHANNEL_SUFFIX.sub("", name)


def _merge_stereo_lanes(
    tracks: list[ProToolsTrack], *, source_channels: dict[str, int] | None = None,
    output_channels: dict[str, int] | None = None,
    reserved_names: set[str] | None = None,
) -> list[ProToolsTrack]:
    """Merge interleaved lanes and retain the channel of split-mono sources.

    Regions are deduplicated by placement (start/offset/length/source) with
    channel suffixes stripped from their names.
    """
    merged: dict[tuple[str, int | None], ProToolsTrack] = {}
    order: list[tuple[str, int | None]] = []
    source_channels = source_channels or {}
    used_names: set[str] = set()
    reserved_names = {track.name for track in tracks} | (reserved_names or set())
    file_sides: dict[tuple[str, str], set[str]] = {}
    track_sides: dict[str, set[str]] = {}
    for track in tracks:
        for region in track.regions:
            suffix = _CHANNEL_SUFFIX.search(region.name) or _CHANNEL_SUFFIX.search(Path(region.filename).stem)
            if suffix is not None:
                file_sides.setdefault((track.name, region.filename), set()).add(suffix.group(1))
                track_sides.setdefault(track.name, set()).add(suffix.group(1))
    seen_by_track: dict[tuple[str, int | None], set[tuple]] = {}
    for track in tracks:
        for region in track.regions:
            suffix = _CHANNEL_SUFFIX.search(region.name) or _CHANNEL_SUFFIX.search(Path(region.filename).stem)
            side = suffix.group(1) if suffix is not None else None
            channels = source_channels.get(region.filename)
            mono = channels == 1 or (channels is None and len(file_sides.get((track.name, region.filename), ())) == 1)
            # A mono track that plays one half of a split pair (Vox.L.wav) is
            # still a centred mono track; only a track whose lanes play both
            # halves is a split stereo track.
            paired = {"L", "R"} <= track_sides.get(track.name, set())
            channel = (0 if side == "L" else 1) if side in ("L", "R") and mono and paired else None
            track_key = (track.name, channel)
            if track_key not in merged:
                name = track.name if channel is None else f"{track.name}.{('L', 'R')[channel]}"
                base = name
                number = 2
                while name in used_names or (channel is not None and name in reserved_names):
                    name = f"{base} ({number})"
                    number += 1
                used_names.add(name)
                merged[track_key] = ProToolsTrack(name=name, index=len(order))
                order.append(track_key)
                if channel is not None and output_channels is not None:
                    output_channels[name] = channel
            target = merged[track_key]
            seen = seen_by_track.setdefault(track_key, set())
            key = (region.start_samples, region.offset_samples, region.length_samples, region.filename)
            if key in seen:
                continue
            seen.add(key)
            target.regions.append(
                ProToolsRegion(
                    name=_strip_channel_suffix(region.name),
                    index=region.index,
                    start_samples=region.start_samples,
                    offset_samples=region.offset_samples,
                    length_samples=region.length_samples,
                    wav_index=region.wav_index,
                    filename=region.filename,
                )
            )
    return [merged[key] for key in order]


def _resolve_audio_dir(session: ProToolsSession) -> Path:
    return session.path.parent / "Audio Files"


def _source_audio_path(directory: Path, filename: str, warnings: list[str]) -> Path | None:
    candidate = contained_source_path(directory, filename)
    if candidate is None:
        warning = f"Blocked source audio reference outside the session folder: {filename}"
    elif candidate.suffix.lower() not in AUDIO_SUFFIXES:
        warning = f"Unsupported source audio format '{candidate.suffix or '(none)'}', clip skipped: {filename}"
    else:
        return candidate
    if warning not in warnings:
        warnings.append(warning)
    return None


def _tempo_warning(tempo: float) -> str:
    return (
        f"Pro Tools session tempo is not recoverable from .ptx yet; positions were "
        f"converted at {tempo:g} BPM. Keep the destination set at {tempo:g} BPM (or "
        f"re-run with --tempo) so clips sit at the correct real-time positions."
    )


@dataclass
class ProToolsMediaPreflight:
    """Resolved audio-media state for a Pro Tools session.

    Built once from the session's regions so the report-only preview and the
    real conversion agree on which referenced files are missing, instead of
    the preview trusting declared filenames and the conversion checking disk.
    """
    referenced_files: list[str] = field(default_factory=list)
    resolved_files: dict[str, Path] = field(default_factory=dict)
    missing_files: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    tracks: list[ProToolsTrack] = field(default_factory=list)
    output_channels: dict[str, int] = field(default_factory=dict)

    @property
    def found_files(self) -> list[str]:
        missing = set(self.missing_files)
        return sorted(name for name in self.resolved_files if name not in missing)


def resolve_protools_media(session: ProToolsSession) -> ProToolsMediaPreflight:
    """Resolve every region's source audio against disk once.

    Both ``protools_to_logic_project`` and ``protools_to_ableton_project`` accept
    the result so they don't repeat this check, and the CLI's report-only branch
    uses it directly so a missing file is named in the preview, not just at
    conversion time.
    """
    audio_dir = _resolve_audio_dir(session)
    warnings: list[str] = []
    referenced: set[str] = set()
    resolved: dict[str, Path] = {}
    missing: list[str] = []
    source_channels: dict[str, int] = {}
    for track in session.tracks:
        for region in track.regions:
            if not region.filename or region.filename in referenced:
                continue
            referenced.add(region.filename)
            file_path = _source_audio_path(audio_dir, region.filename, warnings)
            if file_path is None:
                continue
            resolved[region.filename] = file_path
            if not file_path.is_file():
                missing.append(region.filename)
            else:
                try:
                    source_channels[region.filename] = read_audio_info(file_path).channels
                except (OSError, ValueError):
                    pass

    output_channels: dict[str, int] = {}
    tracks = _merge_stereo_lanes(
        session.tracks, source_channels=source_channels, output_channels=output_channels,
        reserved_names={track.name for track in session.midi_tracks},
    )
    if output_channels:
        warnings.append(
            "Split-mono sources are preserved on separate left/right tracks: "
            "Live tracks are hard-panned; rendered WAVs place each source in its original stereo channel."
        )

    unique_missing = sorted(set(missing))
    if unique_missing:
        examples = ", ".join(unique_missing[:5]) + (", ..." if len(unique_missing) > 5 else "")
        warnings.append(
            f"{len(unique_missing)} source audio file(s) were not found in {audio_dir.name}/ "
            f"next to the session; their clips reference missing media: {examples}"
        )

    return ProToolsMediaPreflight(
        referenced_files=sorted(referenced),
        resolved_files=resolved,
        missing_files=unique_missing,
        warnings=warnings,
        tracks=tracks,
        output_channels=output_channels,
    )


def protools_to_logic_project(
    session: ProToolsSession,
    *,
    tempo: float | None = None,
    preflight: ProToolsMediaPreflight | None = None,
) -> LogicProject:
    """Build a LogicProject view of a Pro Tools session for the Ableton generator."""
    tempo_value = tempo or DEFAULT_PROTOOLS_TEMPO
    media = preflight if preflight is not None else resolve_protools_media(session)
    warnings = list(session.compatibility_warnings)
    warnings.append(_tempo_warning(tempo_value))

    tracks = media.tracks
    audio_refs: list[AudioFileRef] = []
    track_names: list[str] = []
    for track in tracks:
        if not track.regions:
            continue
        track_names.append(track.name)
        for region in track.regions:
            if not region.filename:
                continue
            file_path = media.resolved_files.get(region.filename)
            if file_path is None:
                continue
            audio_refs.append(
                AudioFileRef(
                    filename=region.filename,
                    track_name=track.name,
                    take_number=0,
                    is_comp=False,
                    comp_name="",
                    file_path=file_path,
                    start_position_samples=region.start_samples,
                    content_offset_samples=region.offset_samples,
                    content_duration_samples=region.length_samples,
                    clip_name=region.name,
                    timeline_sample_rate=session.sample_rate,
                )
            )

    warnings.extend(media.warnings)

    midi_tracks = [
        LogicMidiTrack(
            name=track.name,
            notes=[
                LogicMidiNote(
                    pitch=note.pitch,
                    start_beats=note.start_beats,
                    duration_beats=note.duration_beats,
                    velocity=note.velocity,
                )
                for note in track.notes
            ],
        )
        for track in session.midi_tracks
        if track.notes
    ]

    return LogicProject(
        name=session.name,
        tempo=tempo_value,
        time_sig_numerator=4,
        time_sig_denominator=4,
        sample_rate=session.sample_rate,
        audio_files=audio_refs,
        plugins=[],
        track_names=track_names,
        alternative=0,
        midi_tracks=midi_tracks,
        mixer_state={name: TrackMixerState(pan=-1.0 if channel == 0 else 1.0)
                     for name, channel in media.output_channels.items()} or None,
        compatibility_warnings=warnings,
    )


def protools_to_ableton_project(
    session: ProToolsSession,
    *,
    tempo: float | None = None,
    preflight: ProToolsMediaPreflight | None = None,
) -> AbletonProject:
    """Build an AbletonProject view of a Pro Tools session for the Logic package lane.

    Beat positions produced here round-trip back to the same sample positions
    inside the Logic transfer renderer regardless of the tempo chosen.
    """
    tempo_value = tempo or DEFAULT_PROTOOLS_TEMPO
    media = preflight if preflight is not None else resolve_protools_media(session)
    missing_files = set(media.missing_files)
    warnings = list(session.compatibility_warnings)
    warnings.append(_tempo_warning(tempo_value))

    def to_beats(samples: int) -> float:
        return samples * tempo_value / (session.sample_rate * 60)

    tracks = media.tracks
    audio_tracks: list[AbletonTrack] = []
    for track in tracks:
        clips: list[AbletonAudioClip] = []
        for region in track.regions:
            if not region.filename:
                continue
            file_path = media.resolved_files.get(region.filename)
            if file_path is None:
                continue
            source_issue = "missing-file-reference" if region.filename in missing_files else None
            start_beats = to_beats(region.start_samples)
            clips.append(
                AbletonAudioClip(
                    clip_name=region.name,
                    track_name=track.name,
                    source_path=file_path,
                    relative_source_path=f"Audio Files/{region.filename}",
                    start_beats=start_beats,
                    end_beats=start_beats + to_beats(region.length_samples),
                    source_in_beats=to_beats(region.offset_samples),
                    is_warped=False,
                    source_issue=source_issue,
                    output_channel=media.output_channels.get(track.name),
                )
            )
        if clips:
            audio_tracks.append(AbletonTrack(name=track.name, clips=clips))

    warnings.extend(media.warnings)

    midi_tracks: list[AbletonMidiTrack] = []
    for track in session.midi_tracks:
        if not track.notes:
            continue
        first = min(note.start_beats for note in track.notes)
        last = max(note.start_beats + note.duration_beats for note in track.notes)
        clip = AbletonMidiClip(
            clip_name=track.name,
            track_name=track.name,
            start_beats=0.0,
            end_beats=max(last, first),
            notes=[
                AbletonMidiNote(
                    pitch=note.pitch,
                    start_beats=note.start_beats,
                    duration_beats=note.duration_beats,
                    velocity=note.velocity,
                )
                for note in track.notes
            ],
        )
        midi_tracks.append(AbletonMidiTrack(name=track.name, clips=[clip]))

    return AbletonProject(
        name=session.name,
        tempo=tempo_value,
        time_sig_numerator=4,
        time_sig_denominator=4,
        audio_tracks=audio_tracks,
        locators=[],
        midi_tracks=midi_tracks,
        compatibility_warnings=warnings,
    )


def build_protools_import_report(
    session: ProToolsSession, *, destination: str, tempo: float,
    preflight: ProToolsMediaPreflight | None = None,
) -> str:
    """Human-readable report for a Pro Tools import conversion."""
    media = preflight if preflight is not None else resolve_protools_media(session)
    lines = []
    lines.append("=" * 60)
    lines.append(f"  Pro Tools to {destination} Conversion Report")
    lines.append("=" * 60)
    lines.append(f"Session: {session.name}")
    lines.append(
        f"Format version: {session.version} | Sample Rate: {session.sample_rate} | "
        f"Assumed Tempo: {tempo:g} BPM"
    )
    lines.append("")

    merged = [t for t in media.tracks if t.regions]
    lines.append(f"AUDIO TRACKS ({len(merged)}):")
    for i, track in enumerate(merged, 1):
        lines.append(f"  {i}. {track.name} - {len(track.regions)} clip(s)")
    lines.append("")

    midi_tracks = [track for track in session.midi_tracks if track.note_count > 0]
    if midi_tracks:
        total_notes = session.total_midi_notes
        lines.append(f"MIDI TRACKS ({len(midi_tracks)}, {total_notes} notes):")
        for i, track in enumerate(midi_tracks, 1):
            lines.append(f"  {i}. {track.name} - {track.note_count} note(s)")
        lines.append("")

    skipped = len(media.referenced_files) - len(media.resolved_files)
    lines.append(
        f"SOURCE AUDIO FILES ({len(media.referenced_files)} referenced, "
        f"{len(media.found_files)} found, {len(media.missing_files)} missing, {skipped} skipped):"
    )
    missing = set(media.missing_files)
    for filename in media.referenced_files:
        status = "missing" if filename in missing else "found" if filename in media.resolved_files else "skipped"
        lines.append(f"  - {filename} [{status}]")
    lines.append("")

    lines.append("COMPATIBILITY NOTES:")
    for warning in [*session.compatibility_warnings, _tempo_warning(tempo), *media.warnings]:
        lines.append(f"  - {warning}")
    lines.append("")

    lines.append("NOT TRANSFERRED:")
    lines.append("  - Plugins, inserts, and sends (not compatible across DAWs)")
    lines.append("  - Automation and clip gain")
    lines.append("  - Session tempo/meter map (set manually; see tempo warning)")
    lines.append("  - Fades (crossfade render files are skipped; recreate fades manually)")
    lines.append("")
    lines.append("=" * 60)
    return "\n".join(lines)
