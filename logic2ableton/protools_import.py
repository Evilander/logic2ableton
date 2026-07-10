"""Map parsed Pro Tools sessions onto the transfer models both lanes consume.

Pro Tools stores stereo tracks as per-channel lanes that share one track name
and reference the same interleaved source file (region names carry .L/.R
suffixes). The mappers merge those lanes back into single tracks.

Audio timeline positions are samples, so the sample->beat conversion needs a
tempo. Session tempo is not recoverable from .ptx yet; callers pass one (CLI
--tempo) or the default applies. Audio placement survives a wrong tempo in
the Logic lane (beats round-trip back to the same samples), but in Ableton
the set's tempo must match the conversion tempo for clips to sit at the
correct real-time positions - the warning spells that out.
"""

from __future__ import annotations

import re
from pathlib import Path

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
)
from logic2ableton.protools_parser import ProToolsRegion, ProToolsSession, ProToolsTrack

DEFAULT_PROTOOLS_TEMPO = 120.0

_CHANNEL_SUFFIX = re.compile(r"\.(L|R|C|Ls|Rs|Lss|Rss|LFE)$")


def _strip_channel_suffix(name: str) -> str:
    return _CHANNEL_SUFFIX.sub("", name)


def _merge_stereo_lanes(tracks: list[ProToolsTrack]) -> list[ProToolsTrack]:
    """Collapse per-channel lanes that share a track name into one track.

    Regions are deduplicated by placement (start/offset/length/source) with
    channel suffixes stripped from their names.
    """
    merged: dict[str, ProToolsTrack] = {}
    order: list[str] = []
    for track in tracks:
        if track.name not in merged:
            merged[track.name] = ProToolsTrack(name=track.name, index=len(order))
            order.append(track.name)
        target = merged[track.name]
        seen = {
            (r.start_samples, r.offset_samples, r.length_samples, r.filename)
            for r in target.regions
        }
        for region in track.regions:
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
    return [merged[name] for name in order]


def _resolve_audio_dir(session: ProToolsSession) -> Path:
    return session.path.parent / "Audio Files"


def _tempo_warning(tempo: float) -> str:
    return (
        f"Pro Tools session tempo is not recoverable from .ptx yet; positions were "
        f"converted at {tempo:g} BPM. Keep the destination set at {tempo:g} BPM (or "
        f"re-run with --tempo) so clips sit at the correct real-time positions."
    )


def protools_to_logic_project(
    session: ProToolsSession,
    *,
    tempo: float | None = None,
) -> LogicProject:
    """Build a LogicProject view of a Pro Tools session for the Ableton generator."""
    tempo_value = tempo or DEFAULT_PROTOOLS_TEMPO
    audio_dir = _resolve_audio_dir(session)
    warnings = list(session.compatibility_warnings)
    warnings.append(_tempo_warning(tempo_value))

    tracks = _merge_stereo_lanes(session.tracks)
    audio_refs: list[AudioFileRef] = []
    track_names: list[str] = []
    missing: list[str] = []
    for track in tracks:
        if not track.regions:
            continue
        track_names.append(track.name)
        for region in track.regions:
            if not region.filename:
                continue
            file_path = audio_dir / region.filename
            if not file_path.exists():
                missing.append(region.filename)
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
                )
            )

    if missing:
        unique = sorted(set(missing))
        examples = ", ".join(unique[:5]) + (", ..." if len(unique) > 5 else "")
        warnings.append(
            f"{len(unique)} source audio file(s) were not found in {audio_dir.name}/ "
            f"next to the session; their clips reference missing media: {examples}"
        )

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
        compatibility_warnings=warnings,
    )


def protools_to_ableton_project(
    session: ProToolsSession,
    *,
    tempo: float | None = None,
) -> AbletonProject:
    """Build an AbletonProject view of a Pro Tools session for the Logic package lane.

    Beat positions produced here round-trip back to the same sample positions
    inside the Logic transfer renderer regardless of the tempo chosen.
    """
    tempo_value = tempo or DEFAULT_PROTOOLS_TEMPO
    audio_dir = _resolve_audio_dir(session)
    warnings = list(session.compatibility_warnings)
    warnings.append(_tempo_warning(tempo_value))

    def to_beats(samples: int) -> float:
        return samples * tempo_value / (session.sample_rate * 60)

    tracks = _merge_stereo_lanes(session.tracks)
    audio_tracks: list[AbletonTrack] = []
    missing: list[str] = []
    for track in tracks:
        clips: list[AbletonAudioClip] = []
        for region in track.regions:
            if not region.filename:
                continue
            file_path = audio_dir / region.filename
            source_issue = None
            if not file_path.exists():
                missing.append(region.filename)
                source_issue = "missing-file-reference"
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
                )
            )
        if clips:
            audio_tracks.append(AbletonTrack(name=track.name, clips=clips))

    if missing:
        unique = sorted(set(missing))
        examples = ", ".join(unique[:5]) + (", ..." if len(unique) > 5 else "")
        warnings.append(
            f"{len(unique)} source audio file(s) were not found in {audio_dir.name}/ "
            f"next to the session; their clips reference missing media: {examples}"
        )

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


def build_protools_import_report(session: ProToolsSession, *, destination: str, tempo: float) -> str:
    """Human-readable report for a Pro Tools import conversion."""
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

    merged = [t for t in _merge_stereo_lanes(session.tracks) if t.regions]
    lines.append(f"AUDIO TRACKS ({len(merged)}):")
    for i, track in enumerate(merged, 1):
        lines.append(f"  {i}. {track.name} - {len(track.regions)} clip(s)")
    lines.append("")

    if session.midi_tracks:
        total_notes = session.total_midi_notes
        lines.append(f"MIDI TRACKS ({len(session.midi_tracks)}, {total_notes} notes):")
        for i, track in enumerate(session.midi_tracks, 1):
            lines.append(f"  {i}. {track.name} - {track.note_count} note(s)")
        lines.append("")

    lines.append(f"SOURCE AUDIO FILES ({len(session.audio_files)}):")
    for wav in session.audio_files:
        lines.append(f"  - {wav.filename}")
    lines.append("")

    lines.append("NOT TRANSFERRED:")
    lines.append("  - Plugins, inserts, and sends (not compatible across DAWs)")
    lines.append("  - Automation and clip gain")
    lines.append("  - Session tempo/meter map (set manually; see tempo warning)")
    lines.append("  - Fades (crossfade render files are skipped; recreate fades manually)")
    lines.append("")
    lines.append("=" * 60)
    return "\n".join(lines)
