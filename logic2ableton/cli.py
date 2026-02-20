"""CLI entry point for the Logic Pro to Ableton Live converter."""

import argparse
import json
import sys
from pathlib import Path

from logic2ableton import __version__
from logic2ableton.logic_parser import load_mixer_overrides, parse_logic_project
from logic2ableton.plugin_matcher import match_plugins
from logic2ableton.ableton_generator import generate_als
from logic2ableton.report import generate_report
from logic2ableton.vst3_scanner import default_vst3_path


def _emit(stage: str, progress: float, message: str, **extra):
    """Output a JSON progress line to stdout."""
    print(json.dumps({"stage": stage, "progress": progress, "message": message, **extra}), flush=True)


def main(argv: list[str] | None = None) -> int:
    """Main CLI entry point.

    Args:
        argv: Command-line arguments (defaults to sys.argv[1:]).

    Returns:
        Exit code (0 for success, 1 for error).
    """
    parser = argparse.ArgumentParser(
        description="Convert Logic Pro .logicx projects to Ableton Live .als files"
    )
    parser.add_argument("--version", "-V", action="version", version=f"logic2ableton {__version__}")
    parser.add_argument("input", help="Path to .logicx project")
    parser.add_argument("--output", "-o", default=".", help="Output directory")
    parser.add_argument(
        "--alternative",
        "-a",
        type=int,
        default=0,
        help="Logic alternative to convert",
    )
    parser.add_argument(
        "--no-copy", action="store_true", help="Don't copy audio files"
    )
    parser.add_argument(
        "--report-only", action="store_true", help="Only print report"
    )
    parser.add_argument(
        "--template",
        default=None,
        help="Path to Ableton DefaultLiveSet.als template (auto-detected if omitted)",
    )
    parser.add_argument(
        "--vst3-path",
        default=None,
        help="VST3 directory (auto-detected per platform if omitted)",
    )
    parser.add_argument(
        "--mixer",
        default=None,
        help="Path to mixer_overrides.json with per-track volume/pan/mute/solo values",
    )
    parser.add_argument(
        "--generate-mixer-template",
        action="store_true",
        help="Generate a mixer_overrides.json template with all track names",
    )
    parser.add_argument(
        "--json-progress",
        action="store_true",
        help="Output machine-readable JSON progress lines (for GUI integration)",
    )

    args = parser.parse_args(argv)
    logicx_path = Path(args.input)
    jp = args.json_progress

    if not logicx_path.exists():
        if jp:
            _emit("error", 0, f"{logicx_path} not found")
            return 1
        print(f"Error: {logicx_path} not found", file=sys.stderr)
        return 1

    if jp:
        _emit("parsing", 0.1, f"Parsing {logicx_path.name}...")
    else:
        print(f"Parsing {logicx_path.name}...")

    project = parse_logic_project(logicx_path, alternative=args.alternative)

    if args.mixer:
        project.mixer_state = load_mixer_overrides(Path(args.mixer))
        if not jp:
            print(f"  Loaded mixer overrides for {len(project.mixer_state)} track(s)")

    if args.generate_mixer_template:
        template = {
            track_name: {
                "volume_db": 0.0,
                "pan": 0.0,
                "is_muted": False,
                "is_soloed": False,
            }
            for track_name in project.track_names
        }
        mixer_path = Path(args.output) / "mixer_overrides.json"
        mixer_path.parent.mkdir(parents=True, exist_ok=True)
        mixer_path.write_text(json.dumps(template, indent=2), encoding="utf-8")
        if not jp:
            print(f"  Mixer template: {mixer_path}")

    if jp:
        _emit("parsing", 0.3, f"Found {len(project.track_names)} tracks, {len(project.audio_files)} audio files, {len(project.plugins)} plugins")
    else:
        print(
            f"  Found {len(project.track_names)} tracks, "
            f"{len(project.audio_files)} audio files, "
            f"{len(project.plugins)} plugins"
        )

    vst3_path = Path(args.vst3_path) if args.vst3_path else default_vst3_path()

    if jp:
        _emit("plugins", 0.4, "Matching plugins...")
    plugin_matches = match_plugins(project.plugins, vst3_path)
    report = generate_report(project, plugin_matches)

    if not jp:
        print(report)

    if args.report_only:
        if jp:
            _emit("complete", 1.0, "Report generated",
                  report=report,
                  tracks=len(project.track_names),
                  audio_files=len(project.audio_files),
                  plugins=len(project.plugins))
        return 0

    output_dir = Path(args.output)
    template_path = Path(args.template) if args.template else None

    if jp:
        _emit("generating", 0.5, "Generating Ableton session...")
    else:
        print(f"\nGenerating Ableton project in {output_dir}...")

    als_path = generate_als(project, output_dir, copy_audio=not args.no_copy, template_path=template_path)

    if jp:
        _emit("complete", 1.0, "Conversion complete",
              als_path=str(als_path),
              report=report,
              tracks=len(project.track_names),
              clips=len(project.audio_files),
              audio_files=len(project.audio_files))
    else:
        print(f"  Created: {als_path}")

    report_path = output_dir / f"{project.name}_conversion_report.txt"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")

    if not jp:
        print(f"  Report: {report_path}")
        print("\nDone!")

    return 0


if __name__ == "__main__":
    sys.exit(main())
