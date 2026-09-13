"""Exercise representative conversion lanes through the standalone CLI."""

from __future__ import annotations

import argparse
import gzip
import json
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from scripts.fixture_builders import (
    build_logic_project_data,
    build_synthetic_logicx,
    build_synthetic_ptx,
    create_sample_als,
    write_test_wav,
)


def _run_command(command: list[str], *, label: str) -> None:
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        details = "\n".join(part for part in (result.stdout, result.stderr) if part)
        raise RuntimeError(f"{label} failed with exit code {result.returncode}\n{details}")


def _run_converter(
    converter: list[str],
    *,
    mode: str,
    source: Path,
    output_dir: Path,
    extra_args: list[str] | None = None,
) -> None:
    command = [
        *converter,
        "--mode",
        mode,
        str(source),
        "--output",
        str(output_dir),
        "--json-progress",
        *(extra_args or []),
    ]
    _run_command(command, label=f"{mode} smoke")


def _run_converter_batch(
    converter: list[str],
    *,
    mode: str,
    sources: list[Path],
    output_dir: Path,
) -> None:
    command = [
        *converter,
        "--mode",
        mode,
        *[str(source) for source in sources],
        "--output",
        str(output_dir),
        "--json-progress",
    ]
    _run_command(command, label=f"{mode} batch smoke")


def _require(output_dir: Path, pattern: str) -> None:
    if not any(output_dir.glob(pattern)):
        raise AssertionError(f"Missing {pattern!r} artifact under {output_dir}")


def run_smoke(converter: list[str]) -> None:
    with tempfile.TemporaryDirectory(prefix="logic2ableton-smoke-") as temp_dir:
        root = Path(temp_dir)
        fixtures = root / "fixtures"
        fixtures.mkdir()

        ableton_set = create_sample_als(fixtures / "demo.als")

        project_data = build_logic_project_data([[(60, 100, 38_400, 960)]])
        logic_project = build_synthetic_logicx(fixtures, project_data=project_data)
        write_test_wav(logic_project / "Media" / "Audio Files" / "Guitar.wav")

        protools_session = build_synthetic_ptx(fixtures, wav_frames=118_050)
        write_test_wav(
            fixtures / "Audio Files" / "Guitar.wav",
            frames=118_050,
            sample_rate=48_000,
        )

        reverse_output = root / "ableton2logic"
        _run_converter(
            converter,
            mode="ableton2logic",
            source=ableton_set,
            output_dir=reverse_output,
        )
        _require(reverse_output, "**/IMPORT_TO_LOGIC.md")
        _require(reverse_output, "**/Track Stems/*.wav")
        _require(reverse_output, "**/Logic Timeline/*.mid")

        forward_output = root / "logic2ableton"
        _run_converter(
            converter,
            mode="logic2ableton",
            source=logic_project,
            output_dir=forward_output,
        )
        _require(forward_output, "**/*.als")
        _require(forward_output, "*_conversion_report.txt")
        _require(forward_output, "**/Samples/Imported/*.wav")
        _require(forward_output, "**/MIDI/*.mid")

        timeline_path = fixtures / "timeline.json"
        timeline_path.write_text(json.dumps({
            "tempo": [{"bar": 2, "bpm": 100}, {"bar": 4, "bpm": 140}],
            "markers": [{"bar": 1, "name": "Intro"}, {"bar": 4, "name": "Chorus"}],
        }))
        timeline_output = root / "logic2ableton-timeline"
        _run_converter(
            converter,
            mode="logic2ableton",
            source=logic_project,
            output_dir=timeline_output,
            extra_args=[
                "--smpte-start", "auto",
                "--keep-unwarped", "Guitar*",
                "--timeline", str(timeline_path),
            ],
        )
        als_path = next(timeline_output.glob("**/*.als"))
        timeline_root = ET.fromstring(gzip.decompress(als_path.read_bytes()))
        locators = timeline_root.findall(".//Locators/Locators/Locator")
        if len(locators) != 2:
            raise AssertionError(f"Expected 2 Locators in the timeline .als, found {len(locators)}")
        float_events = timeline_root.findall(".//FloatEvent")
        if len(float_events) <= 1:
            raise AssertionError("Expected more than one tempo automation FloatEvent")
        guitar_clip = timeline_root.find(".//AudioClip")
        if guitar_clip.find("IsWarped").get("Value") != "false":
            raise AssertionError("Expected the Guitar clip to be unwarped")

        batch_output = root / "logic2ableton-batch"
        second_project = build_synthetic_logicx(fixtures / "second", project_data=project_data)
        write_test_wav(second_project / "Media" / "Audio Files" / "Guitar.wav")
        _run_converter_batch(
            converter,
            mode="logic2ableton",
            sources=[logic_project, second_project],
            output_dir=batch_output,
        )
        batch_reports = list(batch_output.glob("*_conversion_report.txt"))
        if len(batch_reports) != 2:
            raise AssertionError(f"Expected 2 batch reports, found {len(batch_reports)}")

        protools_output = root / "protools2ableton"
        _run_converter(
            converter,
            mode="protools2ableton",
            source=protools_session,
            output_dir=protools_output,
        )
        _require(protools_output, "**/*.als")
        _require(protools_output, "*_conversion_report.txt")
        _require(protools_output, "**/Samples/Imported/*.wav")
        _require(protools_output, "**/MIDI/*.mid")

        protools_transfer_output = root / "ableton2protools"
        _run_converter(
            converter,
            mode="ableton2protools",
            source=ableton_set,
            output_dir=protools_transfer_output,
        )
        _require(protools_transfer_output, "**/manifest.json")
        _require(protools_transfer_output, "**/IMPORT GUIDE.txt")
        _require(protools_transfer_output, "**/*_protools_transfer_report.txt")

        for mode, source, manifest, guide in (
            ("protools2logic", protools_session, "timeline_manifest.json", "IMPORT_TO_LOGIC.md"),
            ("logic2protools", logic_project, "manifest.json", "IMPORT GUIDE.txt"),
        ):
            output = root / mode
            _run_converter(converter, mode=mode, source=source, output_dir=output)
            _require(output, f"**/{manifest}")
            _require(output, f"**/{guide}")
            _require(output, "**/Audio Files/**/*.wav")
            _require(output, "**/*.mid")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", nargs="?", help="Path to the standalone converter")
    parser.add_argument(
        "--source",
        action="store_true",
        help="Run the source module instead of a packaged binary",
    )
    args = parser.parse_args(argv)

    if args.source:
        converter = [sys.executable, "-m", "logic2ableton.cli"]
    elif args.binary:
        binary = Path(args.binary).resolve()
        if not binary.is_file():
            parser.error(f"standalone converter not found: {binary}")
        converter = [str(binary)]
    else:
        parser.error("provide a standalone converter path or use --source")

    run_smoke(converter)
    print("Standalone smoke passed: all six conversion lanes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
