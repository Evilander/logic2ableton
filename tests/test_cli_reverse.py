import json
import subprocess
import sys

from conftest import create_test_als

from logic2ableton.cli import main


def test_cli_reverse_report_only_infers_mode_from_als_extension(tmp_path, capsys):
    als_path = create_test_als(tmp_path)
    output_dir = tmp_path / "output"

    exit_code = main([str(als_path), "--output", str(output_dir), "--report-only"])
    captured = capsys.readouterr()

    assert exit_code == 0
    report_path = output_dir / "Demo Set_logic_transfer_report.txt"
    assert report_path.exists()
    assert str(report_path) in captured.out


def test_cli_reverse_creates_transfer_package_with_explicit_mode(tmp_path):
    als_path = create_test_als(tmp_path)
    output_dir = tmp_path / "output"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "logic2ableton.cli",
            "ableton2logic",
            str(als_path),
            "--output",
            str(output_dir),
            "--json-progress",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    payloads = [json.loads(line) for line in lines]
    complete = [payload for payload in payloads if payload["stage"] == "complete"][0]
    assert complete["direction"] == "ableton2logic"
    assert complete["artifact_path"].endswith("IMPORT_TO_LOGIC.md")
    assert complete["locators"] == 1


def test_cli_reverse_supports_mode_flag(tmp_path):
    als_path = create_test_als(tmp_path)
    output_dir = tmp_path / "output"

    exit_code = main(["--mode", "ableton2logic", str(als_path), "--output", str(output_dir)])

    assert exit_code == 0
    assert (output_dir / "Demo Set Logic Transfer" / "IMPORT_TO_LOGIC.md").exists()
