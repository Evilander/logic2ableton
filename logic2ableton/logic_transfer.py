"""Generate a Logic-ready transfer package from an Ableton Live Set."""

from __future__ import annotations

import csv
import io
import json
import re
import shutil
import struct
import wave
from dataclasses import dataclass
from pathlib import Path

try:
    import aifc
except ImportError:  # pragma: no cover - Python versions that drop aifc entirely.
    aifc = None

from logic2ableton.models import AbletonAudioClip, AbletonProject, AbletonTrack

SUPPORTED_PCM_SUFFIXES = {".wav", ".aif", ".aiff"}
MIDI_TICKS_PER_QUARTER = 960


@dataclass
class LogicTransferArtifact:
    package_path: Path
    artifact_path: Path
    report_path: Path
    copied_audio_files: int
    rendered_stem_files: int
    timeline_path: Path | None


@dataclass
class DecodedAudio:
    frame_rate: int
    channels: int
    sample_width: int
    frames: bytes

    @property
    def frame_count(self) -> int:
        frame_width = self.channels * self.sample_width
        if frame_width <= 0:
            return 0
        return len(self.frames) // frame_width

    @property
    def frame_width(self) -> int:
        return self.channels * self.sample_width


def _safe_name(value: str, fallback: str) -> str:
    collapsed = re.sub(r"[^\w.\- ]+", "_", value, flags=re.ASCII).strip()
    collapsed = re.sub(r"\s+", " ", collapsed)
    return collapsed or fallback


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


def _read_wave_source(path: Path) -> DecodedAudio:
    with wave.open(str(path), "rb") as handle:
        return DecodedAudio(
            frame_rate=handle.getframerate(),
            channels=handle.getnchannels(),
            sample_width=handle.getsampwidth(),
            frames=handle.readframes(handle.getnframes()),
        )


def _read_aiff_source(path: Path) -> DecodedAudio | None:
    if aifc is None:
        return None
    with aifc.open(str(path), "rb") as handle:
        if handle.getcomptype() not in {"NONE", "not compressed"}:
            return None
        return DecodedAudio(
            frame_rate=handle.getframerate(),
            channels=handle.getnchannels(),
            sample_width=handle.getsampwidth(),
            frames=handle.readframes(handle.getnframes()),
        )


def _read_decoded_audio(path: Path, cache: dict[Path, DecodedAudio | None]) -> DecodedAudio | None:
    if path in cache:
        return cache[path]

    suffix = path.suffix.lower()
    decoded: DecodedAudio | None = None
    try:
        if suffix == ".wav":
            decoded = _read_wave_source(path)
        elif suffix in {".aif", ".aiff"}:
            decoded = _read_aiff_source(path)
    except Exception:
        decoded = None

    if decoded is not None:
        if decoded.channels not in {1, 2} or decoded.sample_width not in {1, 2, 3, 4}:
            decoded = None

    cache[path] = decoded
    return decoded


def _slice_frames(audio: DecodedAudio, start_frame: int, frame_count: int) -> bytes:
    start_frame = max(0, min(audio.frame_count, start_frame))
    frame_count = max(0, frame_count)
    start_byte = start_frame * audio.frame_width
    end_byte = min(len(audio.frames), start_byte + frame_count * audio.frame_width)
    return audio.frames[start_byte:end_byte]


def _fit_to_frame_count(frames: bytes, sample_width: int, channels: int, target_frame_count: int) -> bytes:
    target_bytes = max(0, target_frame_count) * sample_width * channels
    if len(frames) == target_bytes:
        return frames
    if len(frames) > target_bytes:
        return frames[:target_bytes]
    return frames + (b"\x00" * (target_bytes - len(frames)))


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
) -> bytes | None:
    if clip.source_path is None or not clip.source_path.exists():
        return None

    decoded = _read_decoded_audio(clip.source_path, cache)
    if decoded is None:
        return None

    source_start_frame = _beats_to_frames(clip.source_in_beats, tempo, decoded.frame_rate)
    target_frame_count = max(1, _beats_to_frames(clip.duration_beats, tempo, decoded.frame_rate))
    if (decoded.frame_rate, decoded.channels, decoded.sample_width) != (out_rate, out_channels, out_width):
        return None

    raw_frames = _slice_frames(decoded, source_start_frame, target_frame_count)
    return _fit_to_frame_count(raw_frames, out_width, out_channels, target_frame_count)


def _build_bext_chunk(time_reference_samples: int) -> bytes:
    description = b"Logic Ableton Transfer timestamp".ljust(256, b"\x00")
    originator = b"logic2ableton".ljust(32, b"\x00")
    originator_reference = b"ableton2logic".ljust(32, b"\x00")
    origination_date = b"2026-03-14"
    origination_time = b"00:00:00"
    payload = bytearray(346)
    payload[0:256] = description
    payload[256:288] = originator
    payload[288:320] = originator_reference
    payload[320:330] = origination_date
    payload[330:338] = origination_time
    struct.pack_into("<Q", payload, 338, max(0, time_reference_samples))
    return bytes(payload)


def _write_wav_with_bext(
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
    bext_payload = _build_bext_chunk(time_reference_samples)
    bext_chunk = b"bext" + struct.pack("<I", len(bext_payload)) + bext_payload
    riff_size = len(base) - 8 + len(bext_chunk)
    rebuilt = b"RIFF" + struct.pack("<I", riff_size) + b"WAVE" + bext_chunk + base[12:]
    destination.write_bytes(rebuilt)


def _write_var_len(value: int) -> bytes:
    buffer = value & 0x7F
    encoded = bytearray([buffer])
    value >>= 7
    while value:
        buffer = (value & 0x7F) | 0x80
        encoded.insert(0, buffer)
        value >>= 7
    return bytes(encoded)


def _build_logic_timeline_midi(project: AbletonProject) -> bytes:
    track_data = bytearray()
    track_name = f"{project.name} Timeline".encode("utf-8")
    track_data.extend(b"\x00\xff\x03" + _write_var_len(len(track_name)) + track_name)

    mpqn = int(round(60_000_000 / project.tempo))
    track_data.extend(b"\x00\xff\x51\x03" + mpqn.to_bytes(3, "big"))

    denominator_power = 0
    denominator = max(1, project.time_sig_denominator)
    while (1 << denominator_power) < denominator and denominator_power < 7:
        denominator_power += 1
    track_data.extend(
        b"\x00\xff\x58\x04"
        + bytes([project.time_sig_numerator, denominator_power, 24, 8])
    )

    previous_tick = 0
    for locator in sorted(project.locators, key=lambda item: item.time_beats):
        tick = max(0, int(round(locator.time_beats * MIDI_TICKS_PER_QUARTER)))
        delta = tick - previous_tick
        previous_tick = tick
        marker_name = locator.name.encode("utf-8")
        track_data.extend(_write_var_len(delta))
        track_data.extend(b"\xff\x06" + _write_var_len(len(marker_name)) + marker_name)

    track_data.extend(b"\x00\xff\x2f\x00")
    track = b"MTrk" + struct.pack(">I", len(track_data)) + bytes(track_data)
    header = b"MThd" + struct.pack(">IHHH", 6, 0, 1, MIDI_TICKS_PER_QUARTER)
    return header + track


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
    mix_buffer = bytearray(total_frames * frame_width)
    rendered_clips = 0
    approximated_warp = False

    for clip in track.clips:
        rendered = _render_clip_pcm(
            clip,
            tempo=tempo,
            out_rate=sample_rate,
            out_channels=channels,
            out_width=sample_width,
            cache=cache,
        )
        if rendered is None:
            continue

        start_frame = _beats_to_frames(clip.start_beats, tempo, sample_rate)
        start_byte = max(0, start_frame * frame_width)
        end_byte = min(len(mix_buffer), start_byte + len(rendered))
        if end_byte <= start_byte:
            continue

        clipped = rendered[: end_byte - start_byte]
        existing = bytes(mix_buffer[start_byte:end_byte])
        mix_buffer[start_byte:end_byte] = _mix_pcm_frames(existing, clipped, sample_width)
        rendered_clips += 1
        approximated_warp = approximated_warp or clip.is_warped

    if rendered_clips == 0:
        return "reference-only", 0

    _write_wav_with_bext(
        destination,
        sample_rate=sample_rate,
        channels=channels,
        sample_width=sample_width,
        frames=bytes(mix_buffer),
        time_reference_samples=0,
    )
    return ("approximate-warp" if approximated_warp else "timeline-stem"), rendered_clips


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
    return "\n".join(
        [
            f"# Import {project.name} into Logic Pro",
            "",
            "## Fastest path (closest to the Ableton arrangement)",
            "1. Create a new empty Logic Pro project.",
            "2. Import `Logic Timeline/Logic Timeline.mid` to bring in the project tempo, time signature, and locators.",
            "3. Drag every file from `Track Stems` into Logic starting at project bar 1.",
            "4. Keep one Logic track per stem to preserve the Ableton track order and layout.",
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
    )
