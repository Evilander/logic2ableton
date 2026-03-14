"""Generate a Logic-ready transfer package from an Ableton Live Set."""

from __future__ import annotations

import csv
import json
import re
import shutil
import wave
from dataclasses import dataclass
from pathlib import Path

from logic2ableton.models import AbletonAudioClip, AbletonProject


@dataclass
class LogicTransferArtifact:
    package_path: Path
    artifact_path: Path
    report_path: Path
    copied_audio_files: int


def _safe_name(value: str, fallback: str) -> str:
    collapsed = re.sub(r"[^\w.\- ]+", "_", value, flags=re.ASCII).strip()
    collapsed = re.sub(r"\s+", " ", collapsed)
    return collapsed or fallback


def _clip_export_name(index: int, clip: AbletonAudioClip) -> str:
    stem = _safe_name(clip.clip_name, f"clip_{index:03d}")
    extension = clip.source_path.suffix if clip.source_path is not None else ".wav"
    return f"{index:03d} - {stem} - {clip.start_beats:09.3f} beats{extension}"


def _clip_rows(project: AbletonProject) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for track_index, track in enumerate(project.audio_tracks, start=1):
        for clip_index, clip in enumerate(track.clips, start=1):
            rows.append(
                {
                    "track_index": track_index,
                    "track_name": track.name,
                    "clip_index": clip_index,
                    "clip_name": clip.clip_name,
                    "start_beats": round(clip.start_beats, 6),
                    "end_beats": round(clip.end_beats, 6),
                    "duration_beats": round(clip.duration_beats, 6),
                    "source_in_beats": round(clip.source_in_beats, 6),
                    "is_warped": clip.is_warped,
                    "source_issue": clip.source_issue or "",
                    "relative_source_path": clip.relative_source_path or "",
                }
            )
    return rows


def _trim_wav_clip(clip: AbletonAudioClip, destination: Path, tempo: float) -> bool:
    if clip.source_path is None or clip.source_path.suffix.lower() != ".wav":
        return False

    seconds_per_beat = 60.0 / tempo
    start_seconds = max(0.0, clip.source_in_beats * seconds_per_beat)
    clip_seconds = max(0.0, clip.duration_beats * seconds_per_beat)

    with wave.open(str(clip.source_path), "rb") as source:
        params = source.getparams()
        frame_rate = source.getframerate()
        start_frame = min(source.getnframes(), int(start_seconds * frame_rate))
        frame_count = max(0, int(clip_seconds * frame_rate))
        source.setpos(start_frame)
        frames = source.readframes(frame_count)

    with wave.open(str(destination), "wb") as target:
        target.setparams(params)
        target.writeframes(frames)

    return True


def build_logic_transfer_report(project: AbletonProject) -> str:
    lines = []
    lines.append("=" * 60)
    lines.append("  Ableton Live to Logic Transfer Report")
    lines.append("=" * 60)
    lines.append(f"Project: {project.name}")
    lines.append(
        f"Tempo: {project.tempo} BPM | Time Sig: "
        f"{project.time_sig_numerator}/{project.time_sig_denominator}"
    )
    lines.append("")

    lines.append(f"AUDIO TRACKS FOUND ({len(project.audio_tracks)}):")
    for index, track in enumerate(project.audio_tracks, start=1):
        warped = sum(1 for clip in track.clips if clip.is_warped)
        clip_summary = f"{len(track.clips)} clip(s)"
        if warped:
            clip_summary += f", {warped} warped"
        lines.append(f"  {index}. {track.name} - {clip_summary}")
    lines.append("")

    lines.append(f"LOCATORS FOUND ({len(project.locators)}):")
    if project.locators:
        for locator in project.locators[:20]:
            lines.append(f"  - {locator.name} @ beat {locator.time_beats:.3f}")
        if len(project.locators) > 20:
            lines.append(f"  - ... {len(project.locators) - 20} more")
    else:
        lines.append("  - No arrangement locators were found.")
    lines.append("")

    lines.append("TRANSFER PACKAGE CONTENTS:")
    lines.append("  - Audio Files/: clips grouped by Ableton track with beat-prefixed filenames")
    lines.append("  - timeline_manifest.json + timeline_manifest.csv")
    lines.append("  - locators.csv")
    lines.append("  - IMPORT_TO_LOGIC.md")
    lines.append("")

    lines.append("COMPATIBILITY WARNINGS:")
    if project.compatibility_warnings:
        for warning in project.compatibility_warnings:
            lines.append(f"  - {warning}")
    else:
        lines.append("  - No obvious compatibility issues detected from the Live Set.")
    lines.append("")

    lines.append("NOT TRANSFERRED:")
    lines.append("  - Ableton devices, racks, and plugin state")
    lines.append("  - Real-time warp rendering and clip envelope rendering")
    lines.append("  - Return-track routing and master-bus processing")
    lines.append("  - Automation beyond what is visible in the manifest")
    lines.append("  - Trimmed export is only rendered for WAV sources; other formats are copied as-is and called out in the manifest")
    lines.append("")
    lines.append("=" * 60)
    return "\n".join(lines)


def build_logic_import_guide(project: AbletonProject) -> str:
    return "\n".join(
        [
            f"# Import {project.name} into Logic Pro",
            "",
            "1. Create a new empty Logic Pro project.",
            f"2. Set the project tempo to {project.tempo:.3f} BPM and the time signature to "
            f"{project.time_sig_numerator}/{project.time_sig_denominator}.",
            "3. Open the `Audio Files` folder in this package.",
            "4. Import one track folder at a time so the track order stays readable.",
            "5. Use `timeline_manifest.csv` to place clips at the listed beat positions.",
            "6. Recreate any warped clips, device chains, sends, or automation noted in the report.",
            "",
            "Tip: every exported filename starts with the clip order and its Ableton beat position.",
        ]
    )


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def generate_logic_transfer(
    project: AbletonProject,
    output_dir: Path,
    copy_audio: bool = True,
) -> LogicTransferArtifact:
    """Create a Logic-ready import package from an Ableton project."""
    output_dir = Path(output_dir)
    package_path = output_dir / f"{project.name} Logic Transfer"
    audio_root = package_path / "Audio Files"
    package_path.mkdir(parents=True, exist_ok=True)
    audio_root.mkdir(parents=True, exist_ok=True)

    copied_audio_files = 0
    manifest_tracks: list[dict[str, object]] = []

    for track_index, track in enumerate(project.audio_tracks, start=1):
        track_dir = audio_root / f"{track_index:02d} - {_safe_name(track.name, f'track_{track_index:02d}')}"
        track_dir.mkdir(parents=True, exist_ok=True)
        manifest_clips: list[dict[str, object]] = []

        for clip_index, clip in enumerate(track.clips, start=1):
            export_name = _clip_export_name(clip_index, clip)
            exported_path = track_dir / export_name
            export_mode = "reference-only"
            if copy_audio and clip.source_path is not None and clip.source_path.exists():
                if not _trim_wav_clip(clip, exported_path, project.tempo):
                    shutil.copy2(clip.source_path, exported_path)
                    export_mode = "copied-source"
                else:
                    export_mode = "trimmed-wav"
                copied_audio_files += 1

            manifest_clips.append(
                {
                    "clip_index": clip_index,
                    "clip_name": clip.clip_name,
                    "export_name": export_name,
                    "start_beats": round(clip.start_beats, 6),
                    "end_beats": round(clip.end_beats, 6),
                    "duration_beats": round(clip.duration_beats, 6),
                    "source_in_beats": round(clip.source_in_beats, 6),
                    "is_warped": clip.is_warped,
                    "export_mode": export_mode,
                    "source_issue": clip.source_issue or "",
                    "relative_source_path": clip.relative_source_path or "",
                }
            )

        manifest_tracks.append(
            {
                "track_index": track_index,
                "track_name": track.name,
                "clips": manifest_clips,
            }
        )

    manifest_path = package_path / "timeline_manifest.json"
    manifest_rows = _clip_rows(project)
    _write_json(
        manifest_path,
        {
            "format": "ableton2logic.transfer/v1",
            "project_name": project.name,
            "tempo": project.tempo,
            "time_signature": f"{project.time_sig_numerator}/{project.time_sig_denominator}",
            "compatibility_warnings": project.compatibility_warnings,
            "locators": [
                {"name": locator.name, "time_beats": round(locator.time_beats, 6)}
                for locator in project.locators
            ],
            "tracks": manifest_tracks,
        },
    )
    _write_csv(package_path / "timeline_manifest.csv", manifest_rows)
    _write_csv(
        package_path / "locators.csv",
        [{"name": locator.name, "time_beats": round(locator.time_beats, 6)} for locator in project.locators],
    )

    guide_path = package_path / "IMPORT_TO_LOGIC.md"
    guide_path.write_text(build_logic_import_guide(project), encoding="utf-8")

    report_path = package_path / f"{_safe_name(project.name, 'project')}_logic_transfer_report.txt"
    report_path.write_text(build_logic_transfer_report(project), encoding="utf-8")

    return LogicTransferArtifact(
        package_path=package_path,
        artifact_path=guide_path,
        report_path=report_path,
        copied_audio_files=copied_audio_files,
    )
