"""CLI entry point for bidirectional Logic and Ableton transfer workflows."""

from __future__ import annotations

import argparse
import json
import gzip
import math
import xml.etree.ElementTree as ET
import sys
from datetime import UTC, datetime
from pathlib import Path

from logic2ableton.paths import output_path, safe_name

from logic2ableton import __version__
from logic2ableton.ableton_generator import generate_als
from logic2ableton.ableton_parser import parse_ableton_project
from logic2ableton.logic_parser import load_mixer_overrides, parse_logic_project
from logic2ableton.logic_transfer import build_logic_transfer_report, generate_logic_transfer
from logic2ableton.plugin_matcher import match_plugins
from logic2ableton.protools_import import (
    DEFAULT_PROTOOLS_TEMPO,
    build_protools_import_report,
    protools_to_ableton_project,
    protools_to_logic_project,
)
from logic2ableton.protools_parser import parse_protools_session
from logic2ableton.protools_transfer import (
    build_protools_transfer_report,
    generate_protools_transfer,
    generate_protools_transfer_from_logic,
)
from logic2ableton.report import generate_report
from logic2ableton.smf import build_midi_note_file
from logic2ableton.vst3_scanner import default_vst3_path

FORWARD_MODE = "logic2ableton"
REVERSE_MODE = "ableton2logic"
PT2ABLETON_MODE = "protools2ableton"
PT2LOGIC_MODE = "protools2logic"
ABLETON2PT_MODE = "ableton2protools"
LOGIC2PT_MODE = "logic2protools"
SUPPORTED_MODES = {
    FORWARD_MODE,
    REVERSE_MODE,
    PT2ABLETON_MODE,
    PT2LOGIC_MODE,
    ABLETON2PT_MODE,
    LOGIC2PT_MODE,
}

_PROTOOLS_SUFFIXES = (".ptx", ".pts", ".ptf")

_FAILURE_TITLES = {
    FORWARD_MODE: "LOGIC2ABLETON CONVERSION FAILED",
    REVERSE_MODE: "ABLETON2LOGIC TRANSFER FAILED",
    PT2ABLETON_MODE: "PROTOOLS2ABLETON CONVERSION FAILED",
    PT2LOGIC_MODE: "PROTOOLS2LOGIC TRANSFER FAILED",
    ABLETON2PT_MODE: "ABLETON2PROTOOLS TRANSFER FAILED",
    LOGIC2PT_MODE: "LOGIC2PROTOOLS TRANSFER FAILED",
}


def _emit(stage: str, progress: float, message: str, **extra) -> None:
    """Output a JSON progress line to stdout."""
    print(json.dumps({"stage": stage, "progress": progress, "message": message, **extra}), flush=True)


def _write_report(report_path: Path, report: str) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")


def _report_path(
    output_dir: Path,
    input_path: Path,
    *,
    project_name: str | None = None,
    suffix: str = "_conversion_report.txt",
) -> Path:
    report_name = safe_name(project_name or input_path.stem or "project")
    return output_path(output_dir, f"{report_name}{suffix}")


def _build_failure_report(mode: str, input_path: Path, stage: str, error: str) -> str:
    title = _FAILURE_TITLES.get(mode, "CONVERSION FAILED")
    return "\n".join(
        [
            title,
            f"Timestamp (UTC): {datetime.now(UTC).isoformat()}",
            f"Input: {input_path}",
            f"Stage: {stage}",
            "",
            "ERROR",
            error,
        ]
    )


def _export_logic_midi(project, project_folder: Path) -> int:
    """Write extracted Logic MIDI tracks as importable .mid files. Returns file count."""
    tracks = [t for t in project.midi_tracks if t.note_count > 0]
    if not tracks:
        return 0
    midi_dir = project_folder / "MIDI"
    try:
        midi_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        project.compatibility_warnings.append(f"MIDI files could not be exported: {exc}")
        return 0
    written = 0
    for index, track in enumerate(tracks, start=1):
        safe = safe_name(track.name, f"midi_{index:02d}")
        data = build_midi_note_file(
            track,
            tempo=project.tempo,
            numerator=project.time_sig_numerator,
            denominator=project.time_sig_denominator,
        )
        try:
            (midi_dir / f"{index:02d} - {safe}.mid").write_bytes(data)
            written += 1
        except OSError as exc:
            project.compatibility_warnings.append(f"MIDI export failed for {track.name}: {exc}")
    return written


_LEGACY_LOGIC_FORMATS = {
    ".logic": "a Logic Pro 8 or 9 project",
    ".lso": "a Logic 7 song file",
}


def _validate_logic_input(path: Path) -> str | None:
    """Return a human-readable reason the path is not a usable Logic project, or None."""
    suffix = path.suffix.lower()
    if suffix in _LEGACY_LOGIC_FORMATS:
        return (
            f"'{path.name}' is {_LEGACY_LOGIC_FORMATS[suffix]}. logic2ableton reads .logicx "
            "packages, which Logic Pro X (10.0) and later save. Open the project in a current "
            "Logic Pro, choose File > Save As, and convert the .logicx it writes."
        )
    if suffix != ".logicx":
        if any(part.lower().endswith(".logicx") for part in path.parts[:-1]):
            return f"'{path.name}' is inside a Logic project package. Pass the .logicx package itself."
        return (
            f"Expected a Logic Pro .logicx project but got '{path.name}'. "
            "Use ableton2logic for Ableton .als Live Sets."
        )
    if not path.is_dir():
        return f"'{path.name}' is not a readable Logic project package."
    has_alternatives = (path / "Alternatives").is_dir()
    has_info = (path / "Resources" / "ProjectInformation.plist").is_file()
    if not has_alternatives and not has_info:
        return (
            f"'{path.name}' does not look like a Logic project "
            "(missing Alternatives/ and Resources/ProjectInformation.plist)."
        )
    return None


def _validate_ableton_input(path: Path) -> str | None:
    """Return a human-readable reason the path is not a usable Live Set, or None."""
    if path.suffix.lower() != ".als":
        return (
            f"Expected an Ableton .als Live Set but got '{path.name}'. "
            "Use logic2ableton for Logic .logicx projects."
        )
    if not path.is_file():
        return f"'{path.name}' is not a readable .als file."
    return None


def _validate_protools_input(path: Path) -> str | None:
    """Return a human-readable reason the path is not a usable Pro Tools session, or None."""
    if path.suffix.lower() not in _PROTOOLS_SUFFIXES:
        return (
            f"Expected a Pro Tools session (.ptx/.pts/.ptf) but got '{path.name}'."
        )
    if not path.is_file():
        return f"'{path.name}' is not a readable Pro Tools session file."
    return None


def _progress_for_stage(stage: str) -> float:
    return {
        "validation": 0.05,
        "parsing": 0.1,
        "mixer": 0.3,
        "plugins": 0.4,
        "report": 0.45,
        "generating": 0.55,
        "copying": 0.8,
        "report-write": 1.0,
    }.get(stage, 0.0)


def _persist_report_with_note(report_path: Path, report: str) -> tuple[bool, str]:
    try:
        _write_report(report_path, report)
        return True, f" Report saved to {report_path}"
    except OSError as exc:
        return False, f" Could not save report to {report_path}: {exc}"


def _finalize_report(report_path: Path, report: str, warnings: list[str]) -> tuple[str, bool, str | None]:
    extra = [warning for warning in warnings if warning not in report]
    if extra:
        report += "\n\nCONVERSION NOTES:\n" + "\n".join(f"  - {warning}" for warning in extra)
    saved, note = _persist_report_with_note(report_path, report)
    warning = None
    if not saved:
        warning = f"Conversion completed, but the report could not be written.{note}"
        warnings.append(warning)
        report += "\n\n" + warning
    return report, saved, warning


def _als_audio_counts(path: Path) -> tuple[int, int]:
    with gzip.open(path) as handle:
        root = ET.parse(handle).getroot()
    clips = root.findall(".//AudioClip")
    sources = {clip.find("SampleRef/FileRef/Path").get("Value") for clip in clips}
    return len(clips), len(sources)


def _tempo_argument(value: str) -> float:
    tempo = float(value)
    if not math.isfinite(tempo) or not 20 <= tempo <= 999:
        raise argparse.ArgumentTypeError("Tempo must be a number between 20 and 999 BPM")
    return tempo


def _emit_failure(
    *,
    mode: str,
    output_dir: Path,
    input_path: Path,
    stage: str,
    error: str,
    jp: bool,
    project_name: str | None = None,
    report: str | None = None,
    compatibility_warnings: list[str] | None = None,
    report_suffix: str = "_conversion_report.txt",
) -> int:
    report_path = _report_path(output_dir, input_path, project_name=project_name, suffix=report_suffix)
    report_text = report or _build_failure_report(mode, input_path, stage, error)
    _, report_note = _persist_report_with_note(report_path, report_text)
    message = f"Failed during {stage}: {error}.{report_note}"
    payload: dict[str, object] = {
        "direction": mode,
        "report": report_text,
        "report_path": str(report_path),
        "artifact_path": str(report_path),
    }
    if compatibility_warnings is not None:
        payload["compatibility_warnings"] = compatibility_warnings
    if jp:
        _emit("error", _progress_for_stage(stage), message, **payload)
    else:
        print(f"Error: {message}", file=sys.stderr)
    return 1


def _build_forward_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert Logic Pro .logicx projects to Ableton Live .als files",
    )
    parser.add_argument("input", help="Path to .logicx project")
    parser.add_argument("--output", "-o", default=".", help="Output directory")
    parser.add_argument(
        "--alternative",
        "-a",
        type=int,
        default=None,
        help="Logic alternative index to convert (auto-detects the active alternative if omitted)",
    )
    parser.add_argument("--no-copy", action="store_true", help="Do not copy audio files into the output package")
    parser.add_argument("--report-only", action="store_true", help="Write the conversion report without generating output files")
    parser.add_argument(
        "--template",
        default=None,
        help="Path to Ableton DefaultLiveSet.als template (auto-detected if omitted)",
    )
    parser.add_argument("--vst3-path", default=None, help="VST3 directory (auto-detected per platform if omitted)")
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
    parser.add_argument("--json-progress", action="store_true", help="Output machine-readable JSON progress lines")
    return parser


def _build_reverse_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert Ableton Live .als projects into a Logic-ready transfer package",
    )
    parser.add_argument("input", help="Path to .als Live Set")
    parser.add_argument("--output", "-o", default=".", help="Output directory")
    parser.add_argument("--no-copy", action="store_true", help="Do not copy audio files into the transfer package")
    parser.add_argument("--report-only", action="store_true", help="Write only the transfer report")
    parser.add_argument("--json-progress", action="store_true", help="Output machine-readable JSON progress lines")
    return parser


def _build_protools_import_parser(mode: str) -> argparse.ArgumentParser:
    destination = "an Ableton Live .als set" if mode == PT2ABLETON_MODE else "a Logic-ready transfer package"
    parser = argparse.ArgumentParser(
        description=f"Convert a Pro Tools session (.ptx) into {destination}",
    )
    parser.add_argument("input", help="Path to .ptx/.pts Pro Tools session")
    parser.add_argument("--output", "-o", default=".", help="Output directory")
    parser.add_argument(
        "--tempo",
        type=_tempo_argument,
        default=None,
        help=(
            "Tempo (BPM) used to convert sample positions to beats; the session "
            f"tempo is not recoverable from .ptx yet (default {DEFAULT_PROTOOLS_TEMPO:g})"
        ),
    )
    parser.add_argument("--no-copy", action="store_true", help="Do not copy audio files into the output")
    parser.add_argument("--report-only", action="store_true", help="Write the conversion report without generating output files")
    if mode == PT2ABLETON_MODE:
        parser.add_argument(
            "--template",
            default=None,
            help="Path to Ableton DefaultLiveSet.als template (auto-detected if omitted)",
        )
    parser.add_argument("--json-progress", action="store_true", help="Output machine-readable JSON progress lines")
    return parser


def _build_protools_export_parser(mode: str) -> argparse.ArgumentParser:
    source = "an Ableton Live .als set" if mode == ABLETON2PT_MODE else "a Logic Pro .logicx project"
    parser = argparse.ArgumentParser(
        description=f"Convert {source} into a Pro Tools-ready transfer package",
    )
    parser.add_argument("input", help="Path to the source project")
    parser.add_argument("--output", "-o", default=".", help="Output directory")
    if mode == LOGIC2PT_MODE:
        parser.add_argument(
            "--alternative",
            "-a",
            type=int,
            default=None,
            help="Logic alternative index to convert (auto-detects the active alternative if omitted)",
        )
    parser.add_argument("--no-copy", action="store_true", help="Do not copy audio files into the transfer package")
    parser.add_argument("--report-only", action="store_true", help="Write only the transfer report")
    parser.add_argument("--json-progress", action="store_true", help="Output machine-readable JSON progress lines")
    return parser


def _detect_mode(program_name: str, remaining_args: list[str]) -> str:
    stem = Path(program_name).stem.lower()
    if stem in SUPPORTED_MODES and stem != FORWARD_MODE:
        return stem
    skip_value = False
    for token in remaining_args:
        if skip_value:
            skip_value = False
            continue
        if token in {"--output", "-o", "--alternative", "-a", "--tempo", "--template", "--vst3-path", "--mixer"}:
            skip_value = True
            continue
        if token.startswith("-"):
            continue
        lowered = token.lower()
        if lowered.endswith(".als"):
            return REVERSE_MODE
        if lowered.endswith(_PROTOOLS_SUFFIXES):
            return PT2ABLETON_MODE
        break
    return FORWARD_MODE


def _resolve_mode(argv: list[str]) -> tuple[str, list[str]] | None:
    raw_args = list(argv)
    explicit_mode = None
    if raw_args and raw_args[0] in SUPPORTED_MODES:
        explicit_mode = raw_args.pop(0)

    bootstrap = argparse.ArgumentParser(add_help=False)
    bootstrap.add_argument("--mode", choices=sorted(SUPPORTED_MODES))
    bootstrap.add_argument("--version", "-V", action="store_true")
    known, remaining = bootstrap.parse_known_args(raw_args)

    if known.version:
        print(f"logic2ableton {__version__}")
        return None

    mode = explicit_mode or known.mode or _detect_mode(sys.argv[0], remaining)
    return mode, remaining


def _run_forward(args: argparse.Namespace) -> int:
    logicx_path = Path(args.input)
    output_dir = Path(args.output)
    jp = args.json_progress

    if not logicx_path.exists():
        message = f"{logicx_path} not found"
        if jp:
            _emit("error", 0, message, direction=FORWARD_MODE)
            return 1
        print(f"Error: {message}", file=sys.stderr)
        return 1

    validation_error = _validate_logic_input(logicx_path)
    if validation_error:
        return _emit_failure(
            mode=FORWARD_MODE,
            output_dir=output_dir,
            input_path=logicx_path,
            stage="validation",
            error=validation_error,
            jp=jp,
        )

    if jp:
        _emit("parsing", 0.1, f"Parsing {logicx_path.name}...", direction=FORWARD_MODE)
    else:
        print(f"Parsing {logicx_path.name}...")

    try:
        project = parse_logic_project(logicx_path, alternative=args.alternative)
    except Exception as exc:
        return _emit_failure(
            mode=FORWARD_MODE,
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
                mode=FORWARD_MODE,
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
        mixer_path = output_path(output_dir, "mixer_overrides.json")
        mixer_path.parent.mkdir(parents=True, exist_ok=True)
        mixer_path.write_text(json.dumps(template, indent=2), encoding="utf-8")
        if not jp:
            print(f"  Mixer template: {mixer_path}")

    if jp:
        _emit(
            "parsing",
            0.3,
            f"Found {len(project.track_names)} tracks, {len(project.audio_files)} audio files, {len(project.plugins)} plugins",
            direction=FORWARD_MODE,
        )
    else:
        print(
            f"  Found {len(project.track_names)} tracks, "
            f"{len(project.audio_files)} audio files, "
            f"{len(project.plugins)} plugins"
        )

    vst3_path = Path(args.vst3_path) if args.vst3_path else default_vst3_path()

    if jp:
        _emit("plugins", 0.4, "Matching plugins...", direction=FORWARD_MODE)

    try:
        plugin_matches = match_plugins(project.plugins, vst3_path)
    except Exception as exc:
        return _emit_failure(
            mode=FORWARD_MODE,
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
            mode=FORWARD_MODE,
            output_dir=output_dir,
            input_path=logicx_path,
            stage="report",
            error=str(exc),
            jp=jp,
            project_name=project.name,
            compatibility_warnings=project.compatibility_warnings,
        )

    report_path = _report_path(output_dir, logicx_path, project_name=project.name)

    if not jp:
        print(report)

    if args.report_only:
        try:
            _write_report(report_path, report)
        except OSError as exc:
            return _emit_failure(
                mode=FORWARD_MODE,
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
            _emit(
                "complete",
                1.0,
                "Report generated",
                direction=FORWARD_MODE,
                artifact_path=str(report_path),
                report=report,
                report_path=str(report_path),
                tracks=len(project.track_names),
                audio_files=len(project.audio_files),
                plugins=len(project.plugins),
                compatibility_warnings=project.compatibility_warnings,
            )
        else:
            print(f"\nReport: {report_path}")
        return 0

    template_path = Path(args.template) if args.template else None

    if jp:
        _emit("generating", 0.55, "Generating Ableton session...", direction=FORWARD_MODE)
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
            mode=FORWARD_MODE,
            output_dir=output_dir,
            input_path=logicx_path,
            stage="generating",
            error=str(exc),
            jp=jp,
            project_name=project.name,
            report=report,
            compatibility_warnings=project.compatibility_warnings,
        )

    midi_files = _export_logic_midi(project, als_path.parent)
    report = generate_report(project, plugin_matches)
    report, saved, warning = _finalize_report(report_path, report, project.compatibility_warnings)
    clip_count, audio_count = _als_audio_counts(als_path)

    if jp:
        _emit(
            "complete",
            1.0,
            "Conversion complete",
            direction=FORWARD_MODE,
            als_path=str(als_path),
            artifact_path=str(als_path),
            report=report,
            report_path=str(report_path),
            tracks=len(project.track_names),
            clips=clip_count,
            audio_files=audio_count,
            midi_tracks=midi_files,
            midi_notes=project.total_midi_notes,
            compatibility_warnings=project.compatibility_warnings,
            **({"warning": warning} if warning else {}),
        )
    else:
        print(f"  Created: {als_path}")
        if midi_files:
            print(
                f"  MIDI: {midi_files} native MIDI track(s) ({project.total_midi_notes} notes) "
                f"created in the set + .mid exports in {als_path.parent / 'MIDI'}"
            )

    if warning and not jp:
        print(f"Warning: {warning}", file=sys.stderr)

    if not jp:
        if saved:
            print(f"  Report: {report_path}")
        print("\nDone!")

    return 0


def _run_reverse(args: argparse.Namespace) -> int:
    als_path = Path(args.input)
    output_dir = Path(args.output)
    jp = args.json_progress

    if not als_path.exists():
        message = f"{als_path} not found"
        if jp:
            _emit("error", 0, message, direction=REVERSE_MODE)
            return 1
        print(f"Error: {message}", file=sys.stderr)
        return 1

    validation_error = _validate_ableton_input(als_path)
    if validation_error:
        return _emit_failure(
            mode=REVERSE_MODE,
            output_dir=output_dir,
            input_path=als_path,
            stage="validation",
            error=validation_error,
            jp=jp,
            report_suffix="_logic_transfer_report.txt",
        )

    if jp:
        _emit("parsing", 0.1, f"Parsing {als_path.name}...", direction=REVERSE_MODE)
    else:
        print(f"Parsing {als_path.name}...")

    try:
        project = parse_ableton_project(als_path)
    except Exception as exc:
        return _emit_failure(
            mode=REVERSE_MODE,
            output_dir=output_dir,
            input_path=als_path,
            stage="parsing",
            error=f"Failed to parse {als_path.name}: {exc}",
            jp=jp,
            report_suffix="_logic_transfer_report.txt",
        )

    midi_track_count = sum(1 for track in project.midi_tracks if track.note_count > 0)
    if jp:
        _emit(
            "parsing",
            0.3,
            f"Found {len(project.audio_tracks)} audio tracks, {len(project.clips)} clips, "
            f"{midi_track_count} MIDI tracks ({project.total_midi_notes} notes), {len(project.locators)} locators",
            direction=REVERSE_MODE,
            midi_tracks=midi_track_count,
            midi_notes=project.total_midi_notes,
        )
    else:
        print(
            f"  Found {len(project.audio_tracks)} audio tracks, "
            f"{len(project.clips)} clips, "
            f"{midi_track_count} MIDI tracks ({project.total_midi_notes} notes), "
            f"{len(project.locators)} locators"
        )

    try:
        report = build_logic_transfer_report(project)
    except Exception as exc:
        return _emit_failure(
            mode=REVERSE_MODE,
            output_dir=output_dir,
            input_path=als_path,
            stage="report",
            error=str(exc),
            jp=jp,
            project_name=project.name,
            compatibility_warnings=project.compatibility_warnings,
            report_suffix="_logic_transfer_report.txt",
        )

    report_path = _report_path(
        output_dir,
        als_path,
        project_name=project.name,
        suffix="_logic_transfer_report.txt",
    )

    if not jp:
        print(report)

    if args.report_only:
        try:
            _write_report(report_path, report)
        except OSError as exc:
            return _emit_failure(
                mode=REVERSE_MODE,
                output_dir=output_dir,
                input_path=als_path,
                stage="report-write",
                error=str(exc),
                jp=jp,
                project_name=project.name,
                report=report,
                compatibility_warnings=project.compatibility_warnings,
                report_suffix="_logic_transfer_report.txt",
            )
        if jp:
            _emit(
                "complete",
                1.0,
                "Transfer report generated",
                direction=REVERSE_MODE,
                artifact_path=str(report_path),
                report=report,
                report_path=str(report_path),
                tracks=len(project.audio_tracks),
                clips=len(project.clips),
                audio_files=len(project.clips),
                locators=len(project.locators),
                midi_tracks=midi_track_count,
                midi_notes=project.total_midi_notes,
                compatibility_warnings=project.compatibility_warnings,
            )
        else:
            print(f"\nReport: {report_path}")
        return 0

    if jp:
        _emit("generating", 0.55, "Generating Logic transfer package...", direction=REVERSE_MODE)
    else:
        print(f"\nGenerating Logic transfer package in {output_dir}...")

    try:
        transfer = generate_logic_transfer(project, output_dir, copy_audio=not args.no_copy)
    except Exception as exc:
        return _emit_failure(
            mode=REVERSE_MODE,
            output_dir=output_dir,
            input_path=als_path,
            stage="generating",
            error=str(exc),
            jp=jp,
            project_name=project.name,
            report=report,
            compatibility_warnings=project.compatibility_warnings,
            report_suffix="_logic_transfer_report.txt",
        )

    report = build_logic_transfer_report(project)
    if jp:
        _emit(
            "complete",
            1.0,
            "Logic transfer package created",
            direction=REVERSE_MODE,
            artifact_path=str(transfer.artifact_path),
            package_path=str(transfer.package_path),
            report=report,
            report_path=str(transfer.report_path),
            tracks=len(project.audio_tracks),
            clips=len(project.clips),
            audio_files=transfer.copied_audio_files,
            locators=len(project.locators),
            midi_tracks=transfer.rendered_midi_files,
            midi_notes=transfer.transferred_midi_notes,
            compatibility_warnings=project.compatibility_warnings,
        )
    else:
        print(f"  Created: {transfer.package_path}")
        print(f"  Import guide: {transfer.artifact_path}")
        if transfer.rendered_midi_files:
            print(f"  MIDI tracks: {transfer.rendered_midi_files} ({transfer.transferred_midi_notes} notes)")
        print(f"  Report: {transfer.report_path}")
        print("\nDone!")

    return 0


def _run_protools_import(args: argparse.Namespace, mode: str) -> int:
    """Convert a Pro Tools session into an Ableton set or a Logic transfer package."""
    ptx_path = Path(args.input)
    output_dir = Path(args.output)
    jp = args.json_progress
    to_ableton = mode == PT2ABLETON_MODE
    report_suffix = "_conversion_report.txt" if to_ableton else "_logic_transfer_report.txt"

    if not ptx_path.exists():
        message = f"{ptx_path} not found"
        if jp:
            _emit("error", 0, message, direction=mode)
            return 1
        print(f"Error: {message}", file=sys.stderr)
        return 1

    validation_error = _validate_protools_input(ptx_path)
    if validation_error:
        return _emit_failure(
            mode=mode,
            output_dir=output_dir,
            input_path=ptx_path,
            stage="validation",
            error=validation_error,
            jp=jp,
            report_suffix=report_suffix,
        )

    if jp:
        _emit("parsing", 0.1, f"Parsing {ptx_path.name}...", direction=mode)
    else:
        print(f"Parsing {ptx_path.name}...")

    try:
        session = parse_protools_session(ptx_path)
    except Exception as exc:
        return _emit_failure(
            mode=mode,
            output_dir=output_dir,
            input_path=ptx_path,
            stage="parsing",
            error=f"Failed to parse {ptx_path.name}: {exc}",
            jp=jp,
            report_suffix=report_suffix,
        )

    tempo = args.tempo or DEFAULT_PROTOOLS_TEMPO
    destination = "Ableton Live" if to_ableton else "Logic Pro"

    if jp:
        _emit(
            "parsing",
            0.3,
            f"Found {len(session.tracks)} track lane(s), {session.total_regions} clip(s), "
            f"{len(session.midi_tracks)} MIDI track(s) ({session.total_midi_notes} notes)",
            direction=mode,
            midi_tracks=len(session.midi_tracks),
            midi_notes=session.total_midi_notes,
        )
    else:
        print(
            f"  Found {len(session.tracks)} track lane(s), {session.total_regions} clip(s), "
            f"{len(session.midi_tracks)} MIDI track(s) ({session.total_midi_notes} notes)"
        )

    try:
        report = build_protools_import_report(session, destination=destination, tempo=tempo)
    except Exception as exc:
        return _emit_failure(
            mode=mode,
            output_dir=output_dir,
            input_path=ptx_path,
            stage="report",
            error=str(exc),
            jp=jp,
            project_name=session.name,
            report_suffix=report_suffix,
        )

    report_path = _report_path(output_dir, ptx_path, project_name=session.name, suffix=report_suffix)

    if not jp:
        print(report)

    if args.report_only:
        try:
            _write_report(report_path, report)
        except OSError as exc:
            return _emit_failure(
                mode=mode,
                output_dir=output_dir,
                input_path=ptx_path,
                stage="report-write",
                error=str(exc),
                jp=jp,
                project_name=session.name,
                report=report,
                report_suffix=report_suffix,
            )
        if jp:
            _emit(
                "complete",
                1.0,
                "Report generated",
                direction=mode,
                artifact_path=str(report_path),
                report=report,
                report_path=str(report_path),
                tracks=len(session.tracks),
                clips=session.total_regions,
                audio_files=len(session.audio_files),
                midi_tracks=len(session.midi_tracks),
                midi_notes=session.total_midi_notes,
                compatibility_warnings=session.compatibility_warnings,
            )
        else:
            print(f"\nReport: {report_path}")
        return 0

    if jp:
        _emit("generating", 0.55, f"Generating {destination} output...", direction=mode)
    else:
        print(f"\nGenerating {destination} output in {output_dir}...")

    if to_ableton:
        project = protools_to_logic_project(session, tempo=args.tempo)
        template_path = Path(args.template) if args.template else None
        try:
            als_path = generate_als(
                project,
                output_dir,
                copy_audio=not args.no_copy,
                template_path=template_path,
            )
        except Exception as exc:
            return _emit_failure(
                mode=mode,
                output_dir=output_dir,
                input_path=ptx_path,
                stage="generating",
                error=str(exc),
                jp=jp,
                project_name=session.name,
                report=report,
                compatibility_warnings=project.compatibility_warnings,
                report_suffix=report_suffix,
            )

        midi_files = _export_logic_midi(project, als_path.parent)
        report, saved, warning = _finalize_report(report_path, report, project.compatibility_warnings)
        clip_count, audio_count = _als_audio_counts(als_path)
        if warning and not jp:
            print(f"Warning: {warning}", file=sys.stderr)
        if jp:
            _emit(
                "complete",
                1.0,
                "Conversion complete",
                direction=mode,
                als_path=str(als_path),
                artifact_path=str(als_path),
                report=report,
                report_path=str(report_path),
                tracks=len(project.track_names),
                clips=clip_count,
                audio_files=audio_count,
                midi_tracks=midi_files,
                midi_notes=project.total_midi_notes,
                compatibility_warnings=project.compatibility_warnings,
                **({"warning": warning} if warning else {}),
            )
        else:
            print(f"  Created: {als_path}")
            if midi_files:
                print(
                    f"  MIDI: {midi_files} native MIDI track(s) ({project.total_midi_notes} notes) "
                    f"created in the set + .mid exports in {als_path.parent / 'MIDI'}"
                )
            if saved:
                print(f"  Report: {report_path}")
            print("\nDone!")
        return 0

    project = protools_to_ableton_project(session, tempo=args.tempo)
    try:
        transfer = generate_logic_transfer(project, output_dir, copy_audio=not args.no_copy)
    except Exception as exc:
        return _emit_failure(
            mode=mode,
            output_dir=output_dir,
            input_path=ptx_path,
            stage="generating",
            error=str(exc),
            jp=jp,
            project_name=session.name,
            report=report,
            compatibility_warnings=project.compatibility_warnings,
            report_suffix=report_suffix,
        )

    report, _, outer_warning = _finalize_report(report_path, report, project.compatibility_warnings)
    report, _, package_warning = _finalize_report(transfer.report_path, report, project.compatibility_warnings)
    warning = outer_warning or package_warning
    if warning and not jp:
        print(f"Warning: {warning}", file=sys.stderr)
    if jp:
        _emit(
            "complete",
            1.0,
            "Logic transfer package created",
            direction=mode,
            artifact_path=str(transfer.artifact_path),
            package_path=str(transfer.package_path),
            report=report,
            report_path=str(transfer.report_path),
            tracks=len(project.audio_tracks),
            clips=len(project.clips),
            audio_files=transfer.copied_audio_files,
            midi_tracks=transfer.rendered_midi_files,
            midi_notes=transfer.transferred_midi_notes,
            compatibility_warnings=project.compatibility_warnings,
        )
    else:
        print(f"  Created: {transfer.package_path}")
        print(f"  Import guide: {transfer.artifact_path}")
        print(f"  Report: {transfer.report_path}")
        print("\nDone!")
    return 0


def _run_protools_export(args: argparse.Namespace, mode: str) -> int:
    """Convert an Ableton set or Logic project into a Pro Tools transfer package."""
    input_path = Path(args.input)
    output_dir = Path(args.output)
    jp = args.json_progress
    from_ableton = mode == ABLETON2PT_MODE
    report_suffix = "_protools_transfer_report.txt"

    if not input_path.exists():
        message = f"{input_path} not found"
        if jp:
            _emit("error", 0, message, direction=mode)
            return 1
        print(f"Error: {message}", file=sys.stderr)
        return 1

    validation_error = (
        _validate_ableton_input(input_path) if from_ableton else _validate_logic_input(input_path)
    )
    if validation_error:
        return _emit_failure(
            mode=mode,
            output_dir=output_dir,
            input_path=input_path,
            stage="validation",
            error=validation_error,
            jp=jp,
            report_suffix=report_suffix,
        )

    if jp:
        _emit("parsing", 0.1, f"Parsing {input_path.name}...", direction=mode)
    else:
        print(f"Parsing {input_path.name}...")

    try:
        if from_ableton:
            project = parse_ableton_project(input_path)
        else:
            project = parse_logic_project(input_path, alternative=args.alternative)
    except Exception as exc:
        return _emit_failure(
            mode=mode,
            output_dir=output_dir,
            input_path=input_path,
            stage="parsing",
            error=f"Failed to parse {input_path.name}: {exc}",
            jp=jp,
            report_suffix=report_suffix,
        )

    if from_ableton:
        track_count = len(project.audio_tracks)
        clip_count = len(project.clips)
    else:
        track_count = len(project.track_names)
        clip_count = len(project.audio_files)
    midi_track_count = sum(1 for track in project.midi_tracks if track.note_count > 0)

    if jp:
        _emit(
            "parsing",
            0.3,
            f"Found {track_count} audio tracks, {clip_count} clips, "
            f"{midi_track_count} MIDI tracks ({project.total_midi_notes} notes)",
            direction=mode,
            midi_tracks=midi_track_count,
            midi_notes=project.total_midi_notes,
        )
    else:
        print(
            f"  Found {track_count} audio tracks, {clip_count} clips, "
            f"{midi_track_count} MIDI tracks ({project.total_midi_notes} notes)"
        )

    try:
        report = build_protools_transfer_report(project)
    except Exception as exc:
        return _emit_failure(
            mode=mode,
            output_dir=output_dir,
            input_path=input_path,
            stage="report",
            error=str(exc),
            jp=jp,
            project_name=project.name,
            compatibility_warnings=project.compatibility_warnings,
            report_suffix=report_suffix,
        )

    report_path = _report_path(output_dir, input_path, project_name=project.name, suffix=report_suffix)

    if not jp:
        print(report)

    if args.report_only:
        try:
            _write_report(report_path, report)
        except OSError as exc:
            return _emit_failure(
                mode=mode,
                output_dir=output_dir,
                input_path=input_path,
                stage="report-write",
                error=str(exc),
                jp=jp,
                project_name=project.name,
                report=report,
                compatibility_warnings=project.compatibility_warnings,
                report_suffix=report_suffix,
            )
        if jp:
            _emit(
                "complete",
                1.0,
                "Transfer report generated",
                direction=mode,
                artifact_path=str(report_path),
                report=report,
                report_path=str(report_path),
                tracks=track_count,
                clips=clip_count,
                audio_files=clip_count,
                midi_tracks=midi_track_count,
                midi_notes=project.total_midi_notes,
                compatibility_warnings=project.compatibility_warnings,
            )
        else:
            print(f"\nReport: {report_path}")
        return 0

    if jp:
        _emit("generating", 0.55, "Generating Pro Tools transfer package...", direction=mode)
    else:
        print(f"\nGenerating Pro Tools transfer package in {output_dir}...")

    try:
        if from_ableton:
            transfer = generate_protools_transfer(project, output_dir, copy_audio=not args.no_copy)
        else:
            transfer = generate_protools_transfer_from_logic(project, output_dir, copy_audio=not args.no_copy)
    except Exception as exc:
        return _emit_failure(
            mode=mode,
            output_dir=output_dir,
            input_path=input_path,
            stage="generating",
            error=str(exc),
            jp=jp,
            project_name=project.name,
            report=report,
            compatibility_warnings=project.compatibility_warnings,
            report_suffix=report_suffix,
        )

    report = build_protools_transfer_report(project)
    if jp:
        _emit(
            "complete",
            1.0,
            "Pro Tools transfer package created",
            direction=mode,
            artifact_path=str(transfer.artifact_path),
            package_path=str(transfer.package_path),
            report=report,
            report_path=str(transfer.report_path),
            tracks=track_count,
            clips=clip_count,
            audio_files=transfer.copied_audio_files,
            midi_tracks=transfer.rendered_midi_files,
            midi_notes=transfer.transferred_midi_notes,
            compatibility_warnings=project.compatibility_warnings,
        )
    else:
        print(f"  Created: {transfer.package_path}")
        print(f"  Import guide: {transfer.artifact_path}")
        if transfer.rendered_midi_files:
            print(f"  MIDI tracks: {transfer.rendered_midi_files} ({transfer.transferred_midi_notes} notes)")
        print(f"  Report: {transfer.report_path}")
        print("\nDone!")

    return 0


def main(argv: list[str] | None = None) -> int:
    resolved = _resolve_mode(list(argv if argv is not None else sys.argv[1:]))
    if resolved is None:
        return 0

    mode, remaining = resolved
    if mode == REVERSE_MODE:
        args = _build_reverse_parser().parse_args(remaining)
        return _run_reverse(args)
    if mode in (PT2ABLETON_MODE, PT2LOGIC_MODE):
        args = _build_protools_import_parser(mode).parse_args(remaining)
        return _run_protools_import(args, mode)
    if mode in (ABLETON2PT_MODE, LOGIC2PT_MODE):
        args = _build_protools_export_parser(mode).parse_args(remaining)
        return _run_protools_export(args, mode)
    args = _build_forward_parser().parse_args(remaining)
    return _run_forward(args)


if __name__ == "__main__":
    sys.exit(main())
