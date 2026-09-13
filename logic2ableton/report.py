"""Generate a human-readable conversion report."""

import fnmatch

from logic2ableton.logic_parser import format_smpte
from logic2ableton.models import LogicProject
from logic2ableton.plugin_matcher import PluginMatch
from logic2ableton.timeline import beats_per_bar


def _smpte_start_line(project: LogicProject) -> str:
    formatted = format_smpte(project.smpte_start_seconds)
    if project.smpte_start_inferred:
        qualifier = "inferred from the earliest recording"
    elif project.smpte_start_seconds == 3600.0:
        qualifier = "default"
    else:
        qualifier = "from --smpte-start"
    return f"SMPTE start: {formatted} ({qualifier})"


def _audio_files_line(project: LogicProject) -> str:
    if project.audio_layout == "package":
        return f"Audio files: package ({project.audio_dir})"
    if project.audio_layout == "folder":
        return f"Audio files: folder-style project ({project.audio_dir})"
    return "Audio files: not found (no Media/Audio Files or sibling Audio Files folder)"


def _matched_unwarped_tracks(track_names: list[str], keep_unwarped: list[str] | None) -> list[str]:
    if not keep_unwarped:
        return []
    patterns = [pattern.casefold() for pattern in keep_unwarped]
    return [
        name for name in track_names
        if any(fnmatch.fnmatchcase(name.casefold(), pattern) for pattern in patterns)
    ]


def _bar_beat(beat: float, numerator: int, denominator: int) -> tuple[int, float]:
    """Resolve an absolute beat offset back to a 1-based (bar, beat-in-bar) pair."""
    bar_length_beats = beats_per_bar(numerator, denominator)
    bar = int(beat // bar_length_beats) + 1
    beat_in_bar = (beat % bar_length_beats) + 1
    return bar, beat_in_bar


def generate_report(
    project: LogicProject,
    plugin_matches: list[PluginMatch],
    *,
    keep_unwarped: list[str] | None = None,
) -> str:
    """Generate a text report summarizing the Logic-to-Ableton conversion.

    Args:
        project: Parsed Logic Pro project.
        plugin_matches: Plugin match results from match_plugins().
        keep_unwarped: --keep-unwarped patterns, if any, used to name the
            tracks generate_als wrote as unwarped clips.

    Returns:
        Multi-line string report.
    """
    lines = []
    lines.append("=" * 60)
    lines.append("  Logic Pro to Ableton Conversion Report")
    lines.append("=" * 60)
    lines.append(f"Project: {project.name}")
    lines.append(
        f"Tempo: {project.tempo} BPM | Time Sig: "
        f"{project.time_sig_numerator}/{project.time_sig_denominator} | "
        f"Sample Rate: {project.sample_rate}"
    )
    lines.append(_smpte_start_line(project))
    lines.append(_audio_files_line(project))
    unwarped_tracks = _matched_unwarped_tracks(project.track_names, keep_unwarped)
    if unwarped_tracks:
        lines.append(f"Unwarped (won't stretch with tempo changes): {', '.join(unwarped_tracks)}")
    lines.append("")

    lines.append(f"TRACKS TRANSFERRED ({len(project.track_names)}):")
    for i, track_name in enumerate(project.track_names, 1):
        takes = [
            r
            for r in project.audio_files
            if r.track_name == track_name and r.take_number > 0
        ]
        comps = [
            r
            for r in project.audio_files
            if r.track_name == track_name and r.is_comp
        ]
        plain = [
            r
            for r in project.audio_files
            if r.track_name == track_name and r.take_number == 0 and not r.is_comp
        ]
        parts = []
        if takes:
            parts.append(f"{len(takes)} takes")
        if comps:
            comp_names = ", ".join(c.comp_name for c in comps)
            parts.append(f"comp: {comp_names}")
        if plain and not takes:
            parts.append(f"{len(plain)} file(s)")
        detail = " - " + ", ".join(parts) if parts else ""
        lines.append(f"  {i}. {track_name}{detail}")
    lines.append("")

    if project.mixer_state:
        lines.append(f"MIXER STATE APPLIED ({len(project.mixer_state)} tracks):")
        for name, state in project.mixer_state.items():
            parts = [f"{state.volume_db:+.1f} dB"]
            if state.pan != 0:
                direction = "L" if state.pan < 0 else "R"
                parts.append(f"pan {abs(state.pan):.0%}{direction}")
            if state.is_muted:
                parts.append("MUTED")
            if state.is_soloed:
                parts.append("SOLO")
            lines.append(f"  {name} - {', '.join(parts)}")
    else:
        lines.append("MIXER STATE: defaults (0 dB, center pan)")
        lines.append("  Tip: use --mixer mixer_overrides.json to set per-track levels")
    lines.append("")

    lines.append(f"PLUGINS FOUND ({len(plugin_matches)}):")
    for match in plugin_matches:
        preset = (
            f' "{match.preset_name}"'
            if match.preset_name and match.preset_name != "#default"
            else ""
        )
        suggestions = (
            ", ".join(match.suggested_vst3s[:3])
            if match.suggested_vst3s
            else "(no match found)"
        )
        lines.append(f"  {match.logic_plugin_name}{preset}")
        lines.append(f"    -> Suggested: {suggestions}")
    lines.append("")

    midi_tracks = [t for t in project.midi_tracks if t.note_count > 0]
    if midi_tracks:
        lines.append(
            f"MIDI TRACKS TRANSFERRED ({len(midi_tracks)}, {project.total_midi_notes} notes):"
        )
        for i, track in enumerate(midi_tracks, 1):
            lines.append(f"  {i}. {track.name} - {track.note_count} note(s)")
        lines.append(
            "  Created as native MIDI tracks inside the .als (load instruments manually), "
            "and exported as Standard MIDI files in the MIDI/ folder."
        )
        lines.append("")

    if project.timeline is not None:
        lines.append("TIMELINE:")
        if project.timeline.tempo_events:
            lines.append("  Tempo changes:")
            for event in project.timeline.tempo_events:
                bar, beat = _bar_beat(event.beat, project.time_sig_numerator, project.time_sig_denominator)
                lines.append(f"    Bar {bar} beat {beat:g}: {event.bpm:g} BPM")
        if project.timeline.markers:
            lines.append("  Markers:")
            for marker in project.timeline.markers:
                bar, beat = _bar_beat(marker.beat, project.time_sig_numerator, project.time_sig_denominator)
                lines.append(f"    Bar {bar} beat {beat:g}: {marker.name}")
        lines.append("  Live tempo automation and locators from --timeline are written into the .als (skipped when --report-only is used).")
        lines.append("")

    lines.append("COMPATIBILITY WARNINGS:")
    if project.compatibility_warnings:
        for warning in project.compatibility_warnings:
            lines.append(f"  - {warning}")
    else:
        lines.append("  - No obvious compatibility issues detected from project bundle metadata.")
    lines.append("")

    lines.append("NOT TRANSFERRED:")
    lines.append("  - Software instruments and MIDI effects (notes become native MIDI tracks, but reload the instruments in Ableton)")
    lines.append("  - Plugin settings/parameters (not compatible across DAWs)")
    lines.append("  - Automation data (requires deeper binary parsing)")
    lines.append("  - Bus/send routing (recreate manually in Ableton)")
    if project.timeline is None:
        lines.append("  - Logic tempo track and markers (not decoded yet; supply --timeline to reproduce them)")
    lines.append("")
    lines.append("=" * 60)
    return "\n".join(lines)
