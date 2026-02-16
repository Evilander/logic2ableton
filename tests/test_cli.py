import subprocess
import sys
from pathlib import Path

TEST_PROJECT = Path("Might Last Forever.logicx")


def test_cli_report_only():
    result = subprocess.run(
        [sys.executable, "-m", "logic2ableton.cli", str(TEST_PROJECT), "--report-only"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "Might Last Forever" in result.stdout
    assert "TRACKS TRANSFERRED" in result.stdout


def test_cli_full_conversion(tmp_path):
    output_dir = str(tmp_path / "output")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "logic2ableton.cli",
            str(TEST_PROJECT),
            "--output",
            output_dir,
            "--no-copy",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    als_path = tmp_path / "output" / "Might Last Forever Project" / "Might Last Forever.als"
    assert als_path.exists()


def test_cli_no_args():
    result = subprocess.run(
        [sys.executable, "-m", "logic2ableton.cli"],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
