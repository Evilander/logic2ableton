"""Generate a Pro Tools-ready transfer package from an Ableton Live Set or a parsed Logic project."""

from __future__ import annotations

import io
import json
import shutil
import struct
import wave
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from logic2ableton.logic_transfer import (
    DecodedAudio,
    _beats_to_frames,
    _clip_export_name,
    _midi_track_name,
    _read_decoded_audio,
    _render_clip_pcm,
    _safe_name,
    _supports_pcm_render,
)
from logic2ableton.models import (
    AbletonAudioClip,
    AbletonProject,
    AudioFileRef,
    LogicProject,
    samples_to_beats,
)
from logic2ableton.smf import build_midi_note_file

TransferProject = AbletonProject | LogicProject


@dataclass
class ProToolsTransferResult:
    package_path: Path
    report_path: Path
    artifact_path: Path
    copied_audio_files: int
    rendered_midi_files: int = 0
    transferred_midi_notes: int = 0


def _build_pt_bext_chunk(time_reference_samples: int) -> bytes:
    """Build a bext (Broadcast Wave) chunk stamped for a Pro Tools session.

    TimeReference is a pure from-zero sample count with no SMPTE offset,
    because a fresh Pro Tools session starts at 00:00:00:00 - unlike Logic's
    default 01:00:00:00 session start, which the Logic-bound lane accounts
    for separately when it parses Logic's own audio files.
    """
    description = b"Pro Tools Transfer timestamp".ljust(256, b"\x00")
    originator = b"logic2ableton".ljust(32, b"\x00")
    originator_reference = b"protools_transfer".ljust(32, b"\x00")
    origination_date = b"2026-07-10"
    origination_time = b"00:00:00"
    payload = bytearray(346)
    payload[0:256] = description
    payload[256:288] = originator
    payload[288:320] = originator_reference
    payload[320:330] = origination_date
    payload[330:338] = origination_time
    struct.pack_into("<Q", payload, 338, max(0, time_reference_samples))
    return bytes(payload)


def _write_pt_wav_with_bext(
    destination: Path,
    *,
    sample_rate: int,
    channels: int,
    sample_width: int,
    frames: bytes,
    time_reference_samples: int,
) -> None:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(sample_width)
        handle.setframerate(sample_rate)
        handle.writeframes(frames)

    base = buffer.getvalue()
    bext_payload = _build_pt_bext_chunk(time_reference_samples)
    bext_chunk = b"bext" + struct.pack("<I", len(bext_payload)) + bext_payload
    riff_size = len(base) - 8 + len(bext_chunk)
    rebuilt = b"RIFF" + struct.pack("<I", riff_size) + b"WAVE" + bext_chunk + base[12:]
    destination.write_bytes(rebuilt)


def _representative_sample_rate(rates: list[int], default: int = 44100) -> int:
    if not rates:
        return default
    return Counter(rates).most_common(1)[0][0]


def _render_protools_clip_export(
    clip: AbletonAudioClip,
    destination: Path,
    *,
    tempo: float,
    cache: dict[Path, DecodedAudio | None],
) -> tuple[str, int | None]:
    """Render one Ableton clip as a Pro Tools-ready timestamped WAV.

    Mirrors logic_transfer's PCM re-render and non-PCM copy-as-reference
    fallback, but always stamps a pure from-zero TimeReference (no offset).
    """
    if clip.source_path is None or not clip.source_path.exists():
        return "reference-only", None

    decoded = _read_decoded_audio(clip.source_path, cache)
    if decoded is None:
        shutil.copy2(clip.source_path, destination)
        return "copied-source", None

    rendered = _render_clip_pcm(
        clip,
        tempo=tempo,
        out_rate=decoded.frame_rate,
        out_channels=decoded.channels,
        out_width=decoded.sample_width,
        cache=cache,
    )
    if rendered is None:
        shutil.copy2(clip.source_path, destination)
        return "copied-source", None

    time_reference = _beats_to_frames(clip.start_beats, tempo, decoded.frame_rate)
    _write_pt_wav_with_bext(
        destination,
        sample_rate=decoded.frame_rate,
        channels=decoded.channels,
        sample_width=decoded.sample_width,
        frames=rendered,
        time_reference_samples=time_reference,
    )
    return ("timestamped-warp-approximation" if clip.is_warped else "timestamped-wav"), time_reference


def _logic_audio_export_name(index: int, ref: AudioFileRef, *, tempo: float, sample_rate: int) -> str:
    stem = _safe_name(Path(ref.filename).stem, f"clip_{index:03d}")
    beats = samples_to_beats(ref.start_position_samples, tempo, sample_rate)
    extension = ".wav" if _supports_pcm_render(ref.file_path) else (Path(ref.filename).suffix or ".wav")
    return f"{index:03d} - {stem} - {beats:09.3f} beats{extension}"


def _render_protools_logic_audio_export(
    ref: AudioFileRef,
    destination: Path,
    *,
    cache: dict[Path, DecodedAudio | None],
) -> tuple[str, int | None]:
    """Render one Logic-sourced audio file as a Pro Tools-ready timestamped WAV.

    Logic's own audio files already represent a single recorded region, so no
    clip-level slicing is needed here - only re-stamping the bext
    TimeReference to the file's start position, which the Logic parser has
    already normalized to be relative to bar 1 (no further offset applied).
    """
    if not ref.file_path.exists():
        return "reference-only", None

    decoded = _read_decoded_audio(ref.file_path, cache)
    if decoded is None:
        shutil.copy2(ref.file_path, destination)
        return "copied-source", None

    _write_pt_wav_with_bext(
        destination,
        sample_rate=decoded.frame_rate,
        channels=decoded.channels,
        sample_width=decoded.sample_width,
        frames=decoded.frames,
        time_reference_samples=ref.start_position_samples,
    )
    return "timestamped-wav", ref.start_position_samples


def _export_ableton_audio(
    project: AbletonProject,
    audio_root: Path,
    *,
    copy_audio: bool,
) -> tuple[int, list[dict[str, object]], list[int]]:
    """Render every Ableton clip into its Pro Tools track folder.

    Returns (copied_audio_files, manifest_tracks, decoded_sample_rates).
    """
    cache: dict[Path, DecodedAudio | None] = {}
    copied_audio_files = 0
    manifest_tracks: list[dict[str, object]] = []
    decoded_rates: list[int] = []

    for track_index, track in enumerate(project.audio_tracks, start=1):
        track_dir = audio_root / f"{track_index:02d} - {_safe_name(track.name, f'track_{track_index:02d}')}"
        track_dir.mkdir(parents=True, exist_ok=True)
        manifest_clips: list[dict[str, object]] = []

        for clip_index, clip in enumerate(track.clips, start=1):
            export_name = _clip_export_name(clip_index, clip)
            destination = track_dir / export_name
            export_mode = "reference-only"
            time_reference_samples: int | None = None

            if copy_audio and clip.source_path is not None and clip.source_path.exists():
                export_mode, time_reference_samples = _render_protools_clip_export(
                    clip, destination, tempo=project.tempo, cache=cache,
                )
                if export_mode != "reference-only":
                    copied_audio_files += 1
                    decoded = cache.get(clip.source_path)
                    if decoded is not None:
                        decoded_rates.append(decoded.frame_rate)

            manifest_clips.append(
                {
                    "clip_index": clip_index,
                    "clip_name": clip.clip_name,
                    "export_name": export_name,
                    "start_beats": round(clip.start_beats, 6),
                    "duration_beats": round(clip.duration_beats, 6),
                    "is_warped": clip.is_warped,
                    "export_mode": export_mode,
                    "time_reference_samples": time_reference_samples,
                    "source_issue": clip.source_issue or "",
                }
            )

        manifest_tracks.append(
            {
                "track_index": track_index,
                "track_name": track.name,
                "clips": manifest_clips,
            }
        )

    return copied_audio_files, manifest_tracks, decoded_rates


def _export_logic_audio(
    project: LogicProject,
    audio_root: Path,
    *,
    copy_audio: bool,
) -> tuple[int, list[dict[str, object]]]:
    """Render every Logic-sourced audio file into its Pro Tools track folder."""
    cache: dict[Path, DecodedAudio | None] = {}
    copied_audio_files = 0
    manifest_tracks: list[dict[str, object]] = []

    for track_index, track_name in enumerate(project.track_names, start=1):
        track_dir = audio_root / f"{track_index:02d} - {_safe_name(track_name, f'track_{track_index:02d}')}"
        track_dir.mkdir(parents=True, exist_ok=True)
        refs = [ref for ref in project.audio_files if ref.track_name == track_name]
        manifest_files: list[dict[str, object]] = []

        for file_index, ref in enumerate(refs, start=1):
            export_name = _logic_audio_export_name(
                file_index, ref, tempo=project.tempo, sample_rate=project.sample_rate
            )
            destination = track_dir / export_name
            export_mode = "reference-only"
            time_reference_samples: int | None = None

            if copy_audio and ref.file_path.exists():
                export_mode, time_reference_samples = _render_protools_logic_audio_export(
                    ref, destination, cache=cache
                )
                if export_mode != "reference-only":
                    copied_audio_files += 1

            manifest_files.append(
                {
                    "file_index": file_index,
                    "filename": ref.filename,
                    "export_name": export_name,
                    "start_position_samples": ref.start_position_samples,
                    "take_number": ref.take_number,
                    "is_comp": ref.is_comp,
                    "comp_name": ref.comp_name,
                    "export_mode": export_mode,
                    "time_reference_samples": time_reference_samples,
                }
            )

        manifest_tracks.append(
            {
                "track_index": track_index,
                "track_name": track_name,
                "clips": manifest_files,
            }
        )

    return copied_audio_files, manifest_tracks


def _export_midi_tracks(
    midi_tracks,
    midi_root: Path,
    *,
    tempo: float,
    numerator: int,
    denominator: int,
) -> tuple[int, int, list[dict[str, object]]]:
    """Write one Standard MIDI file per note-bearing track (AbletonMidiTrack or LogicMidiTrack)."""
    tracks_with_notes = [track for track in midi_tracks if track.note_count > 0]
    if not tracks_with_notes:
        return 0, 0, []

    midi_root.mkdir(parents=True, exist_ok=True)
    rendered_midi_files = 0
    transferred_midi_notes = 0
    manifest_midi_tracks: list[dict[str, object]] = []

    for index, track in enumerate(tracks_with_notes, start=1):
        midi_name = _midi_track_name(index, track.name)
        (midi_root / midi_name).write_bytes(
            build_midi_note_file(track, tempo=tempo, numerator=numerator, denominator=denominator)
        )
        rendered_midi_files += 1
        transferred_midi_notes += track.note_count
        manifest_midi_tracks.append(
            {
                "track_index": index,
                "track_name": track.name,
                "midi_export_name": midi_name,
                "note_count": track.note_count,
            }
        )

    return rendered_midi_files, transferred_midi_notes, manifest_midi_tracks


def build_protools_transfer_report(project: TransferProject) -> str:
    is_ableton = hasattr(project, "audio_tracks")
    lines = []
    lines.append("=" * 60)
    lines.append("  Pro Tools Transfer Report")
    lines.append("=" * 60)
    lines.append(f"Project: {project.name}")

    sample_rate = getattr(project, "sample_rate", None)
    sample_rate_part = f" | Sample Rate: {sample_rate}" if sample_rate else ""
    lines.append(
        f"Tempo: {project.tempo} BPM | Time Sig: "
        f"{project.time_sig_numerator}/{project.time_sig_denominator}{sample_rate_part}"
    )
    lines.append("")

    if is_ableton:
        lines.append(f"AUDIO TRACKS FOUND ({len(project.audio_tracks)}):")
        for index, track in enumerate(project.audio_tracks, start=1):
            warped = sum(1 for clip in track.clips if clip.is_warped)
            clip_summary = f"{len(track.clips)} clip(s)"
            if warped:
                clip_summary += f", {warped} warped"
            lines.append(f"  {index}. {track.name} - {clip_summary}")
    else:
        lines.append(f"AUDIO TRACKS FOUND ({len(project.track_names)}):")
        for index, name in enumerate(project.track_names, start=1):
            file_count = sum(1 for ref in project.audio_files if ref.track_name == name)
            lines.append(f"  {index}. {name} - {file_count} file(s)")
    lines.append("")

    midi_tracks_with_notes = [track for track in project.midi_tracks if track.note_count > 0]
    lines.append(f"MIDI TRACKS FOUND ({len(midi_tracks_with_notes)}):")
    if midi_tracks_with_notes:
        for index, track in enumerate(midi_tracks_with_notes, start=1):
            lines.append(f"  {index}. {track.name} - {track.note_count} note(s)")
    else:
        lines.append("  - No MIDI tracks with notes were found.")
    lines.append("")

    lines.append("TRANSFER PACKAGE CONTENTS:")
    lines.append("  - Audio Files/: timestamped WAV clip exports grouped by track")
    if midi_tracks_with_notes:
        lines.append("  - MIDI/: one Standard MIDI file per track, with notes at their arrangement positions")
    lines.append("  - manifest.json + IMPORT GUIDE.txt")
    lines.append("")

    lines.append("PRO TOOLS IMPORT:")
    lines.append("  - Create a new session at this package's sample rate with a 00:00:00:00 session start")
    lines.append("  - Import the Audio Files, select every clip in the Clips list, then Spot > Original Time Stamp")
    lines.append("  - Import > MIDI for the MIDI folder, then set session tempo/meter to match this project")
    lines.append("")

    lines.append("COMPATIBILITY WARNINGS:")
    if project.compatibility_warnings:
        for warning in project.compatibility_warnings:
            lines.append(f"  - {warning}")
    else:
        lines.append("  - No obvious compatibility issues detected.")
    lines.append("")

    lines.append("NOT TRANSFERRED:")
    lines.append("  - Plugin/instrument state, automation, and bus/send routing")
    lines.append("  - Warp rendering is approximated only when reconstructing timestamped audio from warped clips")
    lines.append("  - Non-PCM sources that cannot be re-rendered in-process are copied as references")
    lines.append("")
    lines.append("=" * 60)
    return "\n".join(lines)


def build_protools_import_guide(project: TransferProject, *, sample_rate: int | None = None) -> str:
    has_midi = any(track.note_count > 0 for track in project.midi_tracks)
    rate = sample_rate if sample_rate is not None else getattr(project, "sample_rate", 44100)

    lines = [
        f"Pro Tools Import Guide - {project.name}",
        "",
        "1. Create a new Pro Tools session:",
        f"   - Sample rate: {rate} Hz",
        "   - Session start / Main Counter: 00:00:00:00 (not Logic's default 01:00:00:00)",
        "",
        "2. Import audio:",
        "   - File > Import > Audio (or drag the folders under 'Audio Files' into the session)",
        "   - Select every imported clip in the Clips list",
        "   - Right-click > Spot > Original Time Stamp to snap each clip to its exact position",
        "",
    ]
    step = 3
    if has_midi:
        lines += [
            f"{step}. Import MIDI:",
            "   - File > Import > MIDI, choose the files under the 'MIDI' folder",
            "   - Assign an instrument/synth to each imported MIDI track (instruments are not transferred)",
            "",
        ]
        step += 1
    lines += [
        f"{step}. Set session tempo and meter:",
        f"   - Tempo: {project.tempo:.3f} BPM",
        f"   - Meter: {project.time_sig_numerator}/{project.time_sig_denominator}",
        "",
        "Notes:",
        "- Clips are stamped relative to session start (sample 0), not Logic's SMPTE 1-hour offset.",
        "- Warped/approximated clips are best-effort and should be checked against the original.",
        "- Non-PCM sources that could not be re-rendered are copied as references and flagged in manifest.json.",
    ]
    return "\n".join(lines)


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_package_artifacts(
    package_path: Path,
    *,
    project_name: str,
    manifest_payload: dict[str, object],
    guide_text: str,
    report_text: str,
) -> tuple[Path, Path]:
    _write_json(package_path / "manifest.json", manifest_payload)

    guide_path = package_path / "IMPORT GUIDE.txt"
    guide_path.write_text(guide_text, encoding="utf-8")

    report_path = package_path / f"{_safe_name(project_name, 'project')}_protools_transfer_report.txt"
    report_path.write_text(report_text, encoding="utf-8")

    return guide_path, report_path


def generate_protools_transfer(
    project: AbletonProject,
    output_dir: Path,
    copy_audio: bool = True,
) -> ProToolsTransferResult:
    """Create a Pro Tools-ready import package from a parsed Ableton project."""
    output_dir = Path(output_dir)
    package_path = output_dir / f"{project.name} Pro Tools Transfer"
    audio_root = package_path / "Audio Files"
    package_path.mkdir(parents=True, exist_ok=True)
    audio_root.mkdir(parents=True, exist_ok=True)

    copied_audio_files, manifest_tracks, decoded_rates = _export_ableton_audio(
        project, audio_root, copy_audio=copy_audio
    )
    sample_rate = _representative_sample_rate(decoded_rates)

    rendered_midi_files, transferred_midi_notes, manifest_midi_tracks = _export_midi_tracks(
        project.midi_tracks,
        package_path / "MIDI",
        tempo=project.tempo,
        numerator=project.time_sig_numerator,
        denominator=project.time_sig_denominator,
    )

    manifest_payload = {
        "format": "logic2ableton.protools_transfer/v1",
        "target": "protools",
        "project_name": project.name,
        "tempo": project.tempo,
        "time_signature": f"{project.time_sig_numerator}/{project.time_sig_denominator}",
        "sample_rate": sample_rate,
        "session_start": "00:00:00:00",
        "compatibility_warnings": project.compatibility_warnings,
        "tracks": manifest_tracks,
        "midi_tracks": manifest_midi_tracks,
    }
    guide_path, report_path = _write_package_artifacts(
        package_path,
        project_name=project.name,
        manifest_payload=manifest_payload,
        guide_text=build_protools_import_guide(project, sample_rate=sample_rate),
        report_text=build_protools_transfer_report(project),
    )

    return ProToolsTransferResult(
        package_path=package_path,
        report_path=report_path,
        artifact_path=guide_path,
        copied_audio_files=copied_audio_files,
        rendered_midi_files=rendered_midi_files,
        transferred_midi_notes=transferred_midi_notes,
    )


def generate_protools_transfer_from_logic(
    project: LogicProject,
    output_dir: Path,
    copy_audio: bool = True,
) -> ProToolsTransferResult:
    """Create a Pro Tools-ready import package from a parsed Logic project."""
    output_dir = Path(output_dir)
    package_path = output_dir / f"{project.name} Pro Tools Transfer"
    audio_root = package_path / "Audio Files"
    package_path.mkdir(parents=True, exist_ok=True)
    audio_root.mkdir(parents=True, exist_ok=True)

    copied_audio_files, manifest_tracks = _export_logic_audio(project, audio_root, copy_audio=copy_audio)

    rendered_midi_files, transferred_midi_notes, manifest_midi_tracks = _export_midi_tracks(
        project.midi_tracks,
        package_path / "MIDI",
        tempo=project.tempo,
        numerator=project.time_sig_numerator,
        denominator=project.time_sig_denominator,
    )

    manifest_payload = {
        "format": "logic2ableton.protools_transfer/v1",
        "target": "protools",
        "project_name": project.name,
        "tempo": project.tempo,
        "time_signature": f"{project.time_sig_numerator}/{project.time_sig_denominator}",
        "sample_rate": project.sample_rate,
        "session_start": "00:00:00:00",
        "compatibility_warnings": project.compatibility_warnings,
        "tracks": manifest_tracks,
        "midi_tracks": manifest_midi_tracks,
    }
    guide_path, report_path = _write_package_artifacts(
        package_path,
        project_name=project.name,
        manifest_payload=manifest_payload,
        guide_text=build_protools_import_guide(project, sample_rate=project.sample_rate),
        report_text=build_protools_transfer_report(project),
    )

    return ProToolsTransferResult(
        package_path=package_path,
        report_path=report_path,
        artifact_path=guide_path,
        copied_audio_files=copied_audio_files,
        rendered_midi_files=rendered_midi_files,
        transferred_midi_notes=transferred_midi_notes,
    )
