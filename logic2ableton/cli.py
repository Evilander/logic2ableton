"""CLI entry point for the Logic Pro to Ableton Live converter."""

import argparse
import json
import sys
from pathlib import Path

from logic2ableton.logic_parser import load_mixer_overrides, parse_logic_project
from logic2ableton.plugin_matcher import match_plugins
from logic2ableton.ableton_generator import generate_als
from logic2ableton.report import generate_report


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
        "--vst3-path",
        default="C:/Program Files/Common Files/VST3",
        help="VST3 directory",
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

    args = parser.parse_args(argv)
    logicx_path = Path(args.input)

    if not logicx_path.exists():
        print(f"Error: {logicx_path} not found", file=sys.stderr)
        return 1

    print(f"Parsing {logicx_path.name}...")
    project = parse_logic_project(logicx_path, alternative=args.alternative)

    if args.mixer:
        project.mixer_state = load_mixer_overrides(Path(args.mixer))
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
        print(f"  Mixer template: {mixer_path}")

    print(
        f"  Found {len(project.track_names)} tracks, "
        f"{len(project.audio_files)} audio files, "
        f"{len(project.plugins)} plugins"
    )

    plugin_matches = match_plugins(project.plugins, Path(args.vst3_path))
    report = generate_report(project, plugin_matches)
    print(report)

    if args.report_only:
        return 0

    output_dir = Path(args.output)
    print(f"\nGenerating Ableton project in {output_dir}...")
    als_path = generate_als(project, output_dir, copy_audio=not args.no_copy)
    print(f"  Created: {als_path}")

    report_path = output_dir / f"{project.name}_conversion_report.txt"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    print(f"  Report: {report_path}")
    print("\nDone!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
