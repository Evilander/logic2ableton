import subprocess
import sys
import json
from pathlib import Path

import pytest

from logic2ableton.cli import main
from logic2ableton import __version__
from logic2ableton.models import LogicProject

TEST_PROJECT = Path("Might Last Forever.logicx")


@pytest.mark.needs_test_project
def test_cli_report_only():
    result = subprocess.run(
        [sys.executable, "-m", "logic2ableton.cli", str(TEST_PROJECT), "--report-only"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "Might Last Forever" in result.stdout
    assert "TRACKS TRANSFERRED" in result.stdout


@pytest.mark.needs_test_project
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


def test_cli_version():
    result = subprocess.run(
        [sys.executable, "-m", "logic2ableton.cli", "--version"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert __version__ in result.stdout


def test_cli_report_only_writes_report(tmp_path, monkeypatch, capsys):
    project_path = tmp_path / "project.logicx"
    project_path.mkdir()
    (project_path / "Alternatives").mkdir()
    output_dir = tmp_path / "output"

    monkeypatch.setattr(
        "logic2ableton.cli.parse_logic_project",
        lambda *_args, **_kwargs: LogicProject(
            name="Preview Project",
            tempo=120.0,
            time_sig_numerator=4,
            time_sig_denominator=4,
            sample_rate=44100,
            audio_files=[],
            plugins=[],
            track_names=["Track 1"],
            alternative=0,
            compatibility_warnings=[],
        ),
    )
    monkeypatch.setattr("logic2ableton.cli.match_plugins", lambda *_args, **_kwargs: [])

    exit_code = main([str(project_path), "--output", str(output_dir), "--report-only"])
    captured = capsys.readouterr()

    assert exit_code == 0
    report_path = output_dir / "Preview Project_conversion_report.txt"
    assert report_path.exists()
    assert str(report_path) in captured.out


def test_cli_writes_report_when_parse_fails(tmp_path, monkeypatch, capsys):
    project_path = tmp_path / "broken.logicx"
    project_path.mkdir()
    (project_path / "Alternatives").mkdir()
    output_dir = tmp_path / "output"

    monkeypatch.setattr(
        "logic2ableton.cli.parse_logic_project",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("project package is unreadable")),
    )

    exit_code = main([str(project_path), "--output", str(output_dir)])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "project package is unreadable" in captured.err
    report_path = output_dir / "broken_conversion_report.txt"
    assert report_path.exists()
    assert "Stage: parsing" in report_path.read_text(encoding="utf-8")


def test_cli_writes_report_when_generation_fails(tmp_path, monkeypatch, capsys):
    project_path = tmp_path / "project.logicx"
    project_path.mkdir()
    (project_path / "Alternatives").mkdir()
    output_dir = tmp_path / "output"

    monkeypatch.setattr(
        "logic2ableton.cli.parse_logic_project",
        lambda *_args, **_kwargs: LogicProject(
            name="Broken Project",
            tempo=120.0,
            time_sig_numerator=4,
            time_sig_denominator=4,
            sample_rate=44100,
            audio_files=[],
            plugins=[],
            track_names=[],
            alternative=0,
            compatibility_warnings=["No bundled audio files were discovered"],
        ),
    )
    monkeypatch.setattr("logic2ableton.cli.match_plugins", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("logic2ableton.cli.generate_als", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("template missing")))

    exit_code = main([str(project_path), "--output", str(output_dir)])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "template missing" in captured.err
    report_path = output_dir / "Broken Project_conversion_report.txt"
    assert report_path.exists()


def test_cli_writes_report_when_mixer_overrides_fail(tmp_path, monkeypatch, capsys):
    project_path = tmp_path / "project.logicx"
    project_path.mkdir()
    (project_path / "Alternatives").mkdir()
    output_dir = tmp_path / "output"

    monkeypatch.setattr(
        "logic2ableton.cli.parse_logic_project",
        lambda *_args, **_kwargs: LogicProject(
            name="Mixer Project",
            tempo=120.0,
            time_sig_numerator=4,
            time_sig_denominator=4,
            sample_rate=44100,
            audio_files=[],
            plugins=[],
            track_names=["Track 1"],
            alternative=0,
            compatibility_warnings=[],
        ),
    )
    monkeypatch.setattr(
        "logic2ableton.cli.load_mixer_overrides",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("invalid mixer payload")),
    )

    exit_code = main([str(project_path), "--output", str(output_dir), "--mixer", "bad.json"])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "invalid mixer payload" in captured.err
    report_path = output_dir / "Mixer Project_conversion_report.txt"
    assert report_path.exists()
    assert "Stage: mixer" in report_path.read_text(encoding="utf-8")


def test_cli_writes_report_when_plugin_matching_fails(tmp_path, monkeypatch, capsys):
    project_path = tmp_path / "project.logicx"
    project_path.mkdir()
    (project_path / "Alternatives").mkdir()
    output_dir = tmp_path / "output"

    monkeypatch.setattr(
        "logic2ableton.cli.parse_logic_project",
        lambda *_args, **_kwargs: LogicProject(
            name="Plugin Project",
            tempo=120.0,
            time_sig_numerator=4,
            time_sig_denominator=4,
            sample_rate=44100,
            audio_files=[],
            plugins=[],
            track_names=["Track 1"],
            alternative=0,
            compatibility_warnings=["Plugin scan failed"],
        ),
    )
    monkeypatch.setattr(
        "logic2ableton.cli.match_plugins",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("vst scan crashed")),
    )

    exit_code = main([str(project_path), "--output", str(output_dir)])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "vst scan crashed" in captured.err
    report_path = output_dir / "Plugin Project_conversion_report.txt"
    assert report_path.exists()
    assert "Stage: plugins" in report_path.read_text(encoding="utf-8")


def test_cli_report_only_fails_cleanly_when_report_write_fails(tmp_path, monkeypatch, capsys):
    project_path = tmp_path / "project.logicx"
    project_path.mkdir()
    (project_path / "Alternatives").mkdir()
    output_dir = tmp_path / "output"

    monkeypatch.setattr(
        "logic2ableton.cli.parse_logic_project",
        lambda *_args, **_kwargs: LogicProject(
            name="Report Project",
            tempo=120.0,
            time_sig_numerator=4,
            time_sig_denominator=4,
            sample_rate=44100,
            audio_files=[],
            plugins=[],
            track_names=["Track 1"],
            alternative=0,
            compatibility_warnings=[],
        ),
    )
    monkeypatch.setattr("logic2ableton.cli.match_plugins", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        "logic2ableton.cli._write_report",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )

    exit_code = main([str(project_path), "--output", str(output_dir), "--report-only"])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "disk full" in captured.err


def test_cli_forward_rejects_als_input(tmp_path, capsys):
    als_path = tmp_path / "set.als"
    als_path.write_bytes(b"not really")
    output_dir = tmp_path / "output"

    exit_code = main(["logic2ableton", str(als_path), "--output", str(output_dir)])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "Expected a Logic Pro .logicx project" in captured.err
    report_path = output_dir / "set_conversion_report.txt"
    assert report_path.exists()
    assert "Stage: validation" in report_path.read_text(encoding="utf-8")


def test_cli_forward_explains_logic9_project(tmp_path, capsys):
    project_path = tmp_path / "Old Song.logic"
    (project_path / "Audio Files").mkdir(parents=True)
    output_dir = tmp_path / "output"

    exit_code = main([str(project_path), "--output", str(output_dir)])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "'Old Song.logic' is a Logic Pro 8 or 9 project" in captured.err
    assert "Save As" in captured.err
    report_path = output_dir / "Old Song_conversion_report.txt"
    assert "Stage: validation" in report_path.read_text(encoding="utf-8")


def test_cli_forward_rejects_file_inside_logicx(tmp_path, capsys):
    project_data = tmp_path / "Song.logicx" / "Alternatives" / "000" / "ProjectData"
    project_data.parent.mkdir(parents=True)
    project_data.write_bytes(b"\x00")
    output_dir = tmp_path / "output"

    exit_code = main([str(project_data), "--output", str(output_dir)])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "Pass the .logicx package itself" in captured.err


def test_cli_forward_rejects_unstructured_logicx(tmp_path, capsys):
    project_path = tmp_path / "empty.logicx"
    project_path.mkdir()
    output_dir = tmp_path / "output"

    exit_code = main([str(project_path), "--output", str(output_dir)])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "does not look like a Logic project" in captured.err


def test_cli_reverse_rejects_logicx_input(tmp_path, capsys):
    project_path = tmp_path / "song.logicx"
    project_path.mkdir()
    output_dir = tmp_path / "output"

    exit_code = main(["ableton2logic", str(project_path), "--output", str(output_dir)])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "Expected an Ableton .als Live Set" in captured.err


@pytest.mark.needs_test_project
def test_cli_template_flag(tmp_path):
    """--template flag should be accepted."""
    from logic2ableton.ableton_generator import _find_template
    real_template = _find_template()
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
            "--template",
            str(real_template),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    als_path = tmp_path / "output" / "Might Last Forever Project" / "Might Last Forever.als"
    assert als_path.exists()


@pytest.mark.needs_test_project
def test_cli_generate_mixer_template(tmp_path):
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
            "--generate-mixer-template",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0

    json_path = tmp_path / "output" / "mixer_overrides.json"
    assert json_path.exists()
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert "KICK IN" in data
    assert data["KICK IN"]["volume_db"] == 0.0
    assert data["KICK IN"]["pan"] == 0.0


@pytest.mark.needs_test_project
def test_cli_json_progress(tmp_path):
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
            "--json-progress",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    lines = [line for line in result.stdout.strip().split("\n") if line.strip()]
    parsed = [json.loads(line) for line in lines]
    stages = [p["stage"] for p in parsed]
    assert "parsing" in stages
    assert "complete" in stages
    complete = [p for p in parsed if p["stage"] == "complete"][0]
    assert "als_path" in complete
    assert "report" in complete
    assert complete["tracks"] > 0


@pytest.mark.needs_test_project
def test_cli_json_progress_report_only():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "logic2ableton.cli",
            str(TEST_PROJECT),
            "--report-only",
            "--json-progress",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    lines = [line for line in result.stdout.strip().split("\n") if line.strip()]
    parsed = [json.loads(line) for line in lines]
    stages = [p["stage"] for p in parsed]
    assert "complete" in stages
    complete = [p for p in parsed if p["stage"] == "complete"][0]
    assert "report" in complete
    assert complete["tracks"] > 0
