"""Generate a Logic-ready transfer package from an Ableton Live Set."""

from __future__ import annotations

import csv
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Iterable, Iterator

from logic2ableton.audio import BLOCK_FRAMES, DecodedAudio, read_audio_info, write_pcm_wav

from logic2ableton.models import AbletonAudioClip, AbletonMidiTrack, AbletonProject, AbletonTrack
from logic2ableton.paths import create_output_directory, safe_name as _safe_name
from logic2ableton.smf import (
    beats_to_ticks,
    build_midi_note_file,
    tempo_meta,
    time_signature_meta,
    wrap_midi_track,
    write_var_len,
)

SUPPORTED_PCM_SUFFIXES = {".wav", ".aif", ".aiff"}


@dataclass
class LogicTransferArtifact:
    package_path: Path
    artifact_path: Path
    report_path: Path
    copied_audio_files: int
    rendered_stem_files: int
    timeline_path: Path | None
    rendered_midi_files: int = 0
    transferred_midi_notes: int = 0


def _beats_to_seconds(beats: float, tempo: float) -> float:
    return max(0.0, beats) * 60.0 / tempo


def _beats_to_frames(beats: float, tempo: float, sample_rate: int) -> int:
    return int(round(_beats_to_seconds(beats, tempo) * sample_rate))


def _project_length_beats(project: AbletonProject) -> float:
    clip_end = max((clip.end_beats for clip in project.clips), default=0.0)
    locator_end = max((locator.time_beats for locator in project.locators), default=0.0)
    return max(clip_end, locator_end)


def _supports_pcm_render(path: Path | None) -> bool:
    return path is not None and path.exists() and path.suffix.lower() in SUPPORTED_PCM_SUFFIXES


def _clip_export_name(index: int, clip: AbletonAudioClip) -> str:
    stem = _safe_name(clip.clip_name, f"clip_{index:03d}")
    extension = ".wav" if _supports_pcm_render(clip.source_path) else (clip.source_path.suffix if clip.source_path else ".wav")
    return f"{index:03d} - {stem} - {clip.start_beats:09.3f} beats{extension}"


def _track_stem_name(index: int, track_name: str) -> str:
    return f"{index:02d} - {_safe_name(track_name, f'track_{index:02d}')}.wav"


def _midi_track_name(index: int, track_name: str) -> str:
    return f"{index:02d} - {_safe_name(track_name, f'midi_{index:02d}')}.mid"


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
                    "source_in_seconds": clip.source_in_seconds,
                    "is_warped": clip.is_warped,
                    "source_issue": clip.source_issue or "",
                    "relative_source_path": clip.relative_source_path or "",
                }
            )
    return rows


def _read_decoded_audio(path: Path, cache: dict[Path, DecodedAudio | None]) -> DecodedAudio | None:
    if path not in cache:
        try:
            info = read_audio_info(path)
            cache[path] = DecodedAudio(path, info) if info.channels in (1, 2) else None
        except (OSError, ValueError):
            cache[path] = None
    return cache[path]


def _slice_frames(audio: DecodedAudio, start_frame: int, frame_count: int) -> bytes:
    return audio.read_frames(start_frame, frame_count)


def _fit_to_frame_count(frames: bytes, sample_width: int, channels: int, target_frame_count: int) -> bytes:
    target_bytes = max(0, target_frame_count) * sample_width * channels
    if len(frames) == target_bytes:
        return frames
    if len(frames) > target_bytes:
        return frames[:target_bytes]
    silence = b"\x80" if sample_width == 1 else b"\x00"
    return frames + (silence * (target_bytes - len(frames)))


def _sample_limits(sample_width: int) -> tuple[int, int]:
    if sample_width == 1:
        return -128, 127
    max_value = (1 << (sample_width * 8 - 1)) - 1
    min_value = -(1 << (sample_width * 8 - 1))
    return min_value, max_value


def _decode_sample(chunk: bytes, sample_width: int) -> int:
    if sample_width == 1:
        return chunk[0] - 128
    if sample_width == 3:
        sign = b"\xff" if chunk[2] & 0x80 else b"\x00"
        return int.from_bytes(chunk + sign, "little", signed=True)
    return int.from_bytes(chunk, "little", signed=True)


def _encode_sample(value: int, sample_width: int) -> bytes:
    if sample_width == 1:
        return bytes([value + 128])
    if sample_width == 3:
        return int(value).to_bytes(4, "little", signed=True)[:3]
    return int(value).to_bytes(sample_width, "little", signed=True)


def _mix_pcm_frames(base: bytes, overlay: bytes, sample_width: int) -> bytes:
    if len(base) != len(overlay):
        raise ValueError("PCM mixes must have equal byte lengths")

    minimum, maximum = _sample_limits(sample_width)
    mixed = bytearray(len(base))
    for offset in range(0, len(base), sample_width):
        base_sample = _decode_sample(base[offset : offset + sample_width], sample_width)
        overlay_sample = _decode_sample(overlay[offset : offset + sample_width], sample_width)
        sample = max(minimum, min(maximum, base_sample + overlay_sample))
        mixed[offset : offset + sample_width] = _encode_sample(sample, sample_width)
    return bytes(mixed)


def _render_clip_pcm(
    clip: AbletonAudioClip,
    *,
    tempo: float,
    out_rate: int,
    out_channels: int,
    out_width: int,
    cache: dict[Path, DecodedAudio | None],
    start_frame: int = 0,
    frame_count: int | None = None,
) -> bytes | None:
    if clip.source_path is None or not clip.source_path.exists():
        return None

    decoded = _read_decoded_audio(clip.source_path, cache)
    if decoded is None:
        return None

    source_start_frame = (
        round(clip.source_in_seconds * decoded.frame_rate)
        if clip.source_in_seconds is not None
        else _beats_to_frames(clip.source_in_beats, tempo, decoded.frame_rate)
    )
    target_frame_count = max(1, _beats_to_frames(clip.duration_beats, tempo, decoded.frame_rate))
    if (decoded.frame_rate, decoded.channels, decoded.sample_width) != (out_rate, out_channels, out_width):
        return None

    target_frame_count = max(0, target_frame_count - start_frame)
    if frame_count is not None:
        target_frame_count = min(target_frame_count, frame_count)
    raw_frames = _slice_frames(decoded, source_start_frame + start_frame, target_frame_count)
    return _fit_to_frame_count(raw_frames, out_width, out_channels, target_frame_count)


def _iter_clip_pcm(
    clip: AbletonAudioClip, decoded: DecodedAudio, *, tempo: float,
    cache: dict[Path, DecodedAudio | None],
) -> Iterator[bytes]:
    count = max(1, _beats_to_frames(clip.duration_beats, tempo, decoded.frame_rate))
    for start in range(0, count, BLOCK_FRAMES):
        block = _render_clip_pcm(
            clip, tempo=tempo, out_rate=decoded.frame_rate, out_channels=decoded.channels,
            out_width=decoded.sample_width, cache=cache, start_frame=start,
            frame_count=min(BLOCK_FRAMES, count - start),
        )
        if block is None:
            raise ValueError(f"Source audio became unavailable: {clip.clip_name}")
        yield block


def _write_wav_with_bext(
    destination: Path, *, sample_rate: int, channels: int, sample_width: int,
    frames: bytes | Iterable[bytes], time_reference_samples: int,
) -> None:
    write_pcm_wav(
        destination, sample_rate=sample_rate, channels=channels, sample_width=sample_width,
        frames=frames, time_reference_samples=time_reference_samples, originator_reference="ableton2logic",
    )


def _build_logic_timeline_midi(project: AbletonProject) -> bytes:
    track_data = bytearray()
    track_name = f"{project.name} Timeline".encode("utf-8")
    track_data.extend(b"\x00\xff\x03" + write_var_len(len(track_name)) + track_name)
    track_data.extend(tempo_meta(project.tempo))
    track_data.extend(time_signature_meta(project.time_sig_numerator, project.time_sig_denominator))

    previous_tick = 0
    for locator in sorted(project.locators, key=lambda item: item.time_beats):
        tick = beats_to_ticks(locator.time_beats)
        delta = tick - previous_tick
        previous_tick = tick
        marker_name = locator.name.encode("utf-8")
        track_data.extend(write_var_len(delta))
        track_data.extend(b"\xff\x06" + write_var_len(len(marker_name)) + marker_name)

    track_data.extend(b"\x00\xff\x2f\x00")
    return wrap_midi_track(bytes(track_data))


def _build_midi_note_file(track: AbletonMidiTrack, *, tempo: float, numerator: int, denominator: int) -> bytes:
    return build_midi_note_file(track, tempo=tempo, numerator=numerator, denominator=denominator)


def _track_render_format(track: AbletonTrack, cache: dict[Path, DecodedAudio | None]) -> tuple[int, int, int] | None:
    decodable = [
        _read_decoded_audio(clip.source_path, cache)
        for clip in track.clips
        if clip.source_path is not None and clip.source_path.exists()
    ]
    available = [audio for audio in decodable if audio is not None]
    if not available:
        return None

    formats = {(audio.frame_rate, audio.channels, audio.sample_width) for audio in available}
    if len(formats) != 1:
        return None
    return next(iter(formats))


def _render_track_stem(
    track: AbletonTrack,
    destination: Path,
    *,
    tempo: float,
    project_length_beats: float,
    cache: dict[Path, DecodedAudio | None],
) -> tuple[str, int]:
    format_info = _track_render_format(track, cache)
    if format_info is None:
        return "reference-only", 0

    sample_rate, channels, sample_width = format_info
    total_frames = max(1, _beats_to_frames(project_length_beats, tempo, sample_rate))
    frame_width = channels * sample_width
    available = [clip for clip in track.clips if clip.source_path is not None
                 and _read_decoded_audio(clip.source_path, cache) is not None]
    if not available:
        return "reference-only", 0
    placements = [(clip, _beats_to_frames(clip.start_beats, tempo, sample_rate),
                   _beats_to_frames(clip.end_beats, tempo, sample_rate)) for clip in available]
    placements = [(clip, start, end) for clip, start, end in placements if start < total_frames and end > start]
    if not placements:
        return "reference-only", 0

    def blocks() -> Iterator[bytes]:
        silence = b"\x80" if sample_width == 1 else b"\x00"
        for block_start in range(0, total_frames, BLOCK_FRAMES):
            block_end = min(total_frames, block_start + BLOCK_FRAMES)
            mixed = bytearray(silence * ((block_end - block_start) * frame_width))
            occupied: list[tuple[int, int]] = []
            for clip, clip_start, clip_end in placements:
                start, end = max(block_start, clip_start), min(block_end, clip_end)
                if end <= start:
                    continue
                rendered = _render_clip_pcm(
                    clip, tempo=tempo, out_rate=sample_rate, out_channels=channels,
                    out_width=sample_width, cache=cache,
                    start_frame=start - clip_start, frame_count=end - start,
                )
                if rendered is None:
                    raise ValueError(f"Source audio became unavailable: {clip.clip_name}")
                left, right = (start - block_start) * frame_width, (end - block_start) * frame_width
                rendered = _fit_to_frame_count(rendered, sample_width, channels, end - start)
                if any(start < previous_end and end > previous_start for previous_start, previous_end in occupied):
                    mixed[left:right] = _mix_pcm_frames(bytes(mixed[left:right]), rendered, sample_width)
                else:
                    mixed[left:right] = rendered
                occupied.append((start, end))
            yield bytes(mixed)

    _write_wav_with_bext(
        destination, sample_rate=sample_rate, channels=channels, sample_width=sample_width,
        frames=blocks(), time_reference_samples=0,
    )
    mode = "approximate-warp" if any(clip.is_warped for clip, _, _ in placements) else "timeline-stem"
    return mode, len(placements)


def _render_clip_export(
    clip: AbletonAudioClip,
    destination: Path,
    *,
    tempo: float,
    cache: dict[Path, DecodedAudio | None],
) -> tuple[str, int | None]:
    if clip.source_path is None or not clip.source_path.exists():
        return "reference-only", None

    decoded = _read_decoded_audio(clip.source_path, cache)
    if decoded is None:
        shutil.copy2(clip.source_path, destination)
        return "copied-source", None

    rendered = _iter_clip_pcm(clip, decoded, tempo=tempo, cache=cache)

    time_reference = _beats_to_frames(clip.start_beats, tempo, decoded.frame_rate)
    _write_wav_with_bext(
        destination,
        sample_rate=decoded.frame_rate,
        channels=decoded.channels,
        sample_width=decoded.sample_width,
        frames=rendered,
        time_reference_samples=time_reference,
    )
    return ("timestamped-warp-approximation" if clip.is_warped else "timestamped-wav"), time_reference


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

    midi_tracks_with_notes = [track for track in project.midi_tracks if track.note_count > 0]
    lines.append(f"MIDI TRACKS FOUND ({len(midi_tracks_with_notes)}):")
    if midi_tracks_with_notes:
        for index, track in enumerate(midi_tracks_with_notes, start=1):
            lines.append(
                f"  {index}. {track.name} - {len(track.clips)} clip(s), {track.note_count} note(s)"
            )
    else:
        lines.append("  - No MIDI tracks with notes were found in the arrangement.")
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
    lines.append("  - Track Stems/: full-length WAV stems that line up from project start")
    lines.append("  - Logic Timeline/: Standard MIDI file with tempo, time signature, and locators")
    if midi_tracks_with_notes:
        lines.append("  - MIDI Tracks/: one Standard MIDI file per Ableton MIDI track, with notes at their arrangement positions")
    lines.append("  - Audio Files/: timestamped WAV clip exports or copied source files grouped by track")
    lines.append("  - timeline_manifest.json + timeline_manifest.csv")
    lines.append("  - locators.csv")
    lines.append("  - IMPORT_TO_LOGIC.md")
    lines.append("")

    lines.append("FASTEST IMPORT PATH:")
    lines.append("  - Import the Logic Timeline MIDI file into a new empty Logic project")
    lines.append("  - Drag Track Stems into Logic starting at project bar 1")
    lines.append("  - Use clip exports only when you need edit-level reconstruction")
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
    lines.append("  - Return-track routing and master-bus processing")
    lines.append("  - Automation beyond what is visible in the manifest")
    lines.append("  - Warp rendering is approximated only when reconstructing timestamped audio or stems from source clips")
    lines.append("  - Non-PCM sources that cannot be converted in-process are copied as references and may need manual placement")
    lines.append("")
    lines.append("=" * 60)
    return "\n".join(lines)


def build_logic_import_guide(project: AbletonProject) -> str:
    has_midi = any(track.note_count > 0 for track in project.midi_tracks)
    lines = [
        f"# Import {project.name} into Logic Pro",
        "",
        "## Fastest path (closest to the Ableton arrangement)",
        "1. Create a new empty Logic Pro project.",
        "2. Import `Logic Timeline/Logic Timeline.mid` to bring in the project tempo, time signature, and locators.",
        "3. Drag every file from `Track Stems` into Logic starting at project bar 1.",
        "4. Keep one Logic track per stem to preserve the Ableton track order and layout.",
    ]
    if has_midi:
        lines += [
            "",
            "## MIDI tracks",
            "1. Open the `MIDI Tracks` folder in this package.",
            "2. Drag each `.mid` file into Logic at bar 1; the notes already carry their arrangement positions.",
            "3. Assign a software instrument to each imported MIDI region (the Ableton instrument is not transferred).",
            "4. Each file embeds the project tempo and time signature, so it lines up with the timeline import.",
        ]
    lines += [
        "",
        "## Clip-level reconstruction",
        "1. Open the `Audio Files` folder in this package.",
        "2. Import one track folder at a time so the track order stays readable.",
        "3. For timestamped WAV exports, use Logic's `Edit > Move > To Recorded Position` command after import.",
        "4. Use `timeline_manifest.csv` if you want to place or verify clips by beat number manually.",
        "",
        "## Notes",
        f"- The intended project tempo is {project.tempo:.3f} BPM and the base time signature is "
        f"{project.time_sig_numerator}/{project.time_sig_denominator}.",
        "- Warped clips are exported with best-effort timing, but they should be reviewed in Logic before delivery.",
        "- Copied source files that are not rendered as timestamped WAVs are called out in the report and manifest.",
    ]
    if has_midi:
        lines.append("- MIDI note data transfers, but instruments, devices, and MIDI effects do not — reload those in Logic.")
    return "\n".join(lines)


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
    package_path = create_output_directory(output_dir, f"{project.name} Logic Transfer")
    clip_root = package_path / "Audio Files"
    stem_root = package_path / "Track Stems"
    timeline_root = package_path / "Logic Timeline"
    package_path.mkdir(parents=True, exist_ok=True)
    clip_root.mkdir(parents=True, exist_ok=True)
    stem_root.mkdir(parents=True, exist_ok=True)
    timeline_root.mkdir(parents=True, exist_ok=True)

    decode_cache: dict[Path, DecodedAudio | None] = {}
    copied_audio_files = 0
    rendered_stem_files = 0
    project_length_beats = _project_length_beats(project)
    manifest_tracks: list[dict[str, object]] = []

    for track_index, track in enumerate(project.audio_tracks, start=1):
        track_dir = clip_root / f"{track_index:02d} - {_safe_name(track.name, f'track_{track_index:02d}')}"
        track_dir.mkdir(parents=True, exist_ok=True)
        manifest_clips: list[dict[str, object]] = []

        stem_name = _track_stem_name(track_index, track.name)
        stem_path = stem_root / stem_name
        stem_mode = "reference-only"
        stem_clip_count = 0
        if copy_audio:
            stem_mode, stem_clip_count = _render_track_stem(
                track,
                stem_path,
                tempo=project.tempo,
                project_length_beats=project_length_beats,
                cache=decode_cache,
            )
            if stem_clip_count > 0:
                rendered_stem_files += 1
            if track.clips and stem_clip_count < len(track.clips):
                project.compatibility_warnings.append(
                    f"Track '{track.name}': {stem_clip_count} of {len(track.clips)} clip(s) rendered into its stem. "
                    "Unavailable media, incompatible formats, or clips shorter than one sample may require using the individual clip exports."
                )

        for clip_index, clip in enumerate(track.clips, start=1):
            export_name = _clip_export_name(clip_index, clip)
            exported_path = track_dir / export_name
            export_mode = "reference-only"
            time_reference_samples: int | None = None
            if copy_audio and clip.source_path is not None and clip.source_path.exists():
                export_mode, time_reference_samples = _render_clip_export(
                    clip,
                    exported_path,
                    tempo=project.tempo,
                    cache=decode_cache,
                )
                if export_mode != "reference-only":
                    copied_audio_files += 1
                if export_mode == "copied-source":
                    project.compatibility_warnings.append(
                        f"Clip '{clip.clip_name}' was copied without PCM rendering; recreate its trim and placement manually."
                    )

            manifest_clips.append(
                {
                    "clip_index": clip_index,
                    "clip_name": clip.clip_name,
                    "export_name": export_name,
                    "start_beats": round(clip.start_beats, 6),
                    "end_beats": round(clip.end_beats, 6),
                    "duration_beats": round(clip.duration_beats, 6),
                    "source_in_beats": round(clip.source_in_beats, 6),
                    "source_in_seconds": clip.source_in_seconds,
                    "is_warped": clip.is_warped,
                    "export_mode": export_mode,
                    "time_reference_samples": time_reference_samples,
                    "source_issue": clip.source_issue or "",
                    "relative_source_path": clip.relative_source_path or "",
                }
            )

        manifest_tracks.append(
            {
                "track_index": track_index,
                "track_name": track.name,
                "stem_export_name": stem_name if stem_clip_count > 0 else "",
                "stem_mode": stem_mode,
                "stem_clip_count": stem_clip_count,
                "clips": manifest_clips,
            }
        )

    timeline_path = timeline_root / "Logic Timeline.mid"
    timeline_path.write_bytes(_build_logic_timeline_midi(project))

    rendered_midi_files = 0
    transferred_midi_notes = 0
    manifest_midi_tracks: list[dict[str, object]] = []
    midi_tracks_with_notes = [track for track in project.midi_tracks if track.note_count > 0]
    if midi_tracks_with_notes:
        midi_root = package_path / "MIDI Tracks"
        midi_root.mkdir(parents=True, exist_ok=True)
        for midi_index, track in enumerate(midi_tracks_with_notes, start=1):
            midi_name = _midi_track_name(midi_index, track.name)
            (midi_root / midi_name).write_bytes(
                _build_midi_note_file(
                    track,
                    tempo=project.tempo,
                    numerator=project.time_sig_numerator,
                    denominator=project.time_sig_denominator,
                )
            )
            rendered_midi_files += 1
            transferred_midi_notes += track.note_count
            manifest_midi_tracks.append(
                {
                    "track_index": midi_index,
                    "track_name": track.name,
                    "midi_export_name": midi_name,
                    "clip_count": len(track.clips),
                    "note_count": track.note_count,
                }
            )

    manifest_path = package_path / "timeline_manifest.json"
    manifest_rows = _clip_rows(project)
    _write_json(
        manifest_path,
        {
            "format": "ableton2logic.transfer/v2",
            "project_name": project.name,
            "tempo": project.tempo,
            "time_signature": f"{project.time_sig_numerator}/{project.time_sig_denominator}",
            "compatibility_warnings": project.compatibility_warnings,
            "project_length_beats": round(project_length_beats, 6),
            "logic_timeline_midi": str(timeline_path.relative_to(package_path)).replace("\\", "/"),
            "locators": [
                {"name": locator.name, "time_beats": round(locator.time_beats, 6)}
                for locator in project.locators
            ],
            "tracks": manifest_tracks,
            "midi_tracks": manifest_midi_tracks,
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
        rendered_stem_files=rendered_stem_files,
        timeline_path=timeline_path,
        rendered_midi_files=rendered_midi_files,
        transferred_midi_notes=transferred_midi_notes,
    )
