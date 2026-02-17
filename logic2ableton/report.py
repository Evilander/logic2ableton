"""Generate a human-readable conversion report."""

from logic2ableton.models import LogicProject
from logic2ableton.plugin_matcher import PluginMatch


def generate_report(project: LogicProject, plugin_matches: list[PluginMatch]) -> str:
    """Generate a text report summarizing the Logic-to-Ableton conversion.

    Args:
        project: Parsed Logic Pro project.
        plugin_matches: Plugin match results from match_plugins().

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

    lines.append("NOT TRANSFERRED:")
    lines.append("  - Plugin settings/parameters (not compatible across DAWs)")
    lines.append("  - Automation data (requires deeper binary parsing)")
    lines.append("  - Bus/send routing (recreate manually in Ableton)")
    lines.append("")
    lines.append("=" * 60)
    return "\n".join(lines)
