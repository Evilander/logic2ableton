"""CLI entry point for the Logic Pro to Ableton Live converter."""

import argparse
import json
import sys
from datetime import datetime, UTC
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


def _write_report(report_path: Path, report: str) -> None:
    """Persist the human-readable conversion report."""
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")


def _report_path(output_dir: Path, input_path: Path, project_name: str | None = None) -> Path:
    """Build the report filename for a conversion attempt."""
    report_name = project_name or input_path.stem or "logic_project"
    return output_dir / f"{report_name}_conversion_report.txt"


def _build_failure_report(input_path: Path, stage: str, error: str) -> str:
    """Capture actionable details when conversion fails before a full report exists."""
    return "\n".join(
        [
            "LOGIC2ABLETON CONVERSION FAILED",
            f"Timestamp (UTC): {datetime.now(UTC).isoformat()}",
            f"Input: {input_path}",
            f"Stage: {stage}",
            "",
            "ERROR",
            error,
        ]
    )


def _progress_for_stage(stage: str) -> float:
    return {
        "parsing": 0.0,
        "mixer": 0.3,
        "plugins": 0.4,
        "report": 0.45,
        "generating": 0.5,
        "report-write": 1.0,
    }.get(stage, 0.0)


def _persist_report_with_note(report_path: Path, report: str) -> tuple[bool, str]:
    try:
        _write_report(report_path, report)
        return True, f" Report saved to {report_path}"
    except OSError as exc:
        return False, f" Could not save report to {report_path}: {exc}"


def _emit_failure(
    *,
    output_dir: Path,
    input_path: Path,
    stage: str,
    error: str,
    jp: bool,
    project_name: str | None = None,
    report: str | None = None,
    compatibility_warnings: list[str] | None = None,
) -> int:
    report_path = _report_path(output_dir, input_path, project_name)
    report_text = report or _build_failure_report(input_path, stage, error)
    _, report_note = _persist_report_with_note(report_path, report_text)
    message = f"Failed during {stage}: {error}.{report_note}"
    payload: dict[str, object] = {
        "report": report_text,
        "report_path": str(report_path),
    }
    if compatibility_warnings is not None:
        payload["compatibility_warnings"] = compatibility_warnings
    if jp:
        _emit("error", _progress_for_stage(stage), message, **payload)
    else:
        print(f"Error: {message}", file=sys.stderr)
    return 1


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
    output_dir = Path(args.output)
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

    try:
        project = parse_logic_project(logicx_path, alternative=args.alternative)
    except Exception as exc:
        return _emit_failure(
            output_dir=output_dir,
            input_path=logicx_path,
            stage="parsing",
            error=f"Failed to parse {logicx_path.name}: {exc}",
            jp=jp,
        )

    if args.mixer:
        try:
            project.mixer_state = load_mixer_overrides(Path(args.mixer))
        except Exception as exc:
            return _emit_failure(
                output_dir=output_dir,
                input_path=logicx_path,
                stage="mixer",
                error=str(exc),
                jp=jp,
                project_name=project.name,
            )
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
    try:
        plugin_matches = match_plugins(project.plugins, vst3_path)
    except Exception as exc:
        return _emit_failure(
            output_dir=output_dir,
            input_path=logicx_path,
            stage="plugins",
            error=str(exc),
            jp=jp,
            project_name=project.name,
            compatibility_warnings=project.compatibility_warnings,
        )

    try:
        report = generate_report(project, plugin_matches)
    except Exception as exc:
        return _emit_failure(
            output_dir=output_dir,
            input_path=logicx_path,
            stage="report",
            error=str(exc),
            jp=jp,
            project_name=project.name,
            compatibility_warnings=project.compatibility_warnings,
        )
    report_path = _report_path(output_dir, logicx_path, project.name)

    if not jp:
        print(report)

    if args.report_only:
        try:
            _write_report(report_path, report)
        except OSError as exc:
            return _emit_failure(
                output_dir=output_dir,
                input_path=logicx_path,
                stage="report-write",
                error=str(exc),
                jp=jp,
                project_name=project.name,
                report=report,
                compatibility_warnings=project.compatibility_warnings,
            )
        if jp:
            _emit("complete", 1.0, "Report generated",
                  report=report,
                  report_path=str(report_path),
                  tracks=len(project.track_names),
                  audio_files=len(project.audio_files),
                  plugins=len(project.plugins),
                  compatibility_warnings=project.compatibility_warnings)
        else:
            print(f"\nReport: {report_path}")
        return 0

    template_path = Path(args.template) if args.template else None

    if jp:
        _emit("generating", 0.5, "Generating Ableton session...")
    else:
        print(f"\nGenerating Ableton project in {output_dir}...")

    try:
        als_path = generate_als(
            project,
            output_dir,
            copy_audio=not args.no_copy,
            template_path=template_path,
        )
    except Exception as exc:
        return _emit_failure(
            output_dir=output_dir,
            input_path=logicx_path,
            stage="generating",
            error=str(exc),
            jp=jp,
            project_name=project.name,
            report=report,
            compatibility_warnings=project.compatibility_warnings,
        )

    if jp:
        _emit("complete", 1.0, "Conversion complete",
              als_path=str(als_path),
              report=report,
              report_path=str(report_path),
              tracks=len(project.track_names),
              clips=len(project.audio_files),
              audio_files=len(project.audio_files),
              compatibility_warnings=project.compatibility_warnings)
    else:
        print(f"  Created: {als_path}")

    saved, report_note = _persist_report_with_note(report_path, report)
    if not saved:
        warning = f"Conversion completed, but the report could not be written.{report_note}"
        if jp:
            _emit(
                "complete",
                1.0,
                warning,
                als_path=str(als_path),
                report=report,
                report_path=str(report_path),
                tracks=len(project.track_names),
                clips=len(project.audio_files),
                audio_files=len(project.audio_files),
                compatibility_warnings=project.compatibility_warnings,
                warning=warning,
            )
        else:
            print(f"Warning: {warning}", file=sys.stderr)

    if not jp:
        if saved:
            print(f"  Report: {report_path}")
        print("\nDone!")

    return 0


if __name__ == "__main__":
    sys.exit(main())
