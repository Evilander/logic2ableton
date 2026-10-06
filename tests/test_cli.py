import argparse
import gzip
import subprocess
import sys
import json
import os
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from logic2ableton.cli import _tempo_argument, main
from logic2ableton import __version__
from logic2ableton.models import LogicProject
from logic2ableton.timeline import MAX_TEMPO_BPM, MIN_TEMPO_BPM

from scripts.fixture_builders import (
    build_logic_arrangement_project_data,
    build_logic_project_data,
    build_synthetic_logicx,
    write_smpte_stamped_wav,
)

from conftest import TEST_PROJECT, TEST_PROJECT_NAME


@pytest.mark.parametrize("bpm", [MIN_TEMPO_BPM, MAX_TEMPO_BPM, 128.5])
def test_tempo_argument_accepts_live_range(bpm):
    assert _tempo_argument(str(bpm)) == bpm


@pytest.mark.parametrize("bpm", [0.0000001, MIN_TEMPO_BPM - 0.001, MAX_TEMPO_BPM + 0.001, 5000])
def test_tempo_argument_rejects_outside_live_range(bpm):
    """Shares its bound with the --timeline loader (F13, 2026-09-15 review)."""
    with pytest.raises(argparse.ArgumentTypeError):
        _tempo_argument(str(bpm))


@pytest.mark.needs_test_project
def test_cli_report_only():
    result = subprocess.run(
        [sys.executable, "-m", "logic2ableton.cli", str(TEST_PROJECT), "--report-only"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert TEST_PROJECT_NAME in result.stdout
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
    als_path = tmp_path / "output" / f"{TEST_PROJECT_NAME} Project" / f"{TEST_PROJECT_NAME}.als"
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


def test_cli_report_only_reuses_report_path_across_repeat_runs(tmp_path, monkeypatch, capsys):
    """A single-input --report-only run (the Electron preview flow) must overwrite the
    same report file on repeat calls instead of numbering a new one each time."""
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

    for _ in range(3):
        exit_code = main([str(project_path), "--output", str(output_dir), "--report-only"])
        assert exit_code == 0

    reports = list(output_dir.glob("*_conversion_report.txt"))
    assert [p.name for p in reports] == ["Preview Project_conversion_report.txt"]


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
    als_path = tmp_path / "output" / f"{TEST_PROJECT_NAME} Project" / f"{TEST_PROJECT_NAME}.als"
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


def _minimal_project(**overrides):
    fields = dict(
        name="SMPTE Project",
        tempo=120.0,
        time_sig_numerator=4,
        time_sig_denominator=4,
        sample_rate=44100,
        audio_files=[],
        plugins=[],
        track_names=[],
        alternative=0,
        compatibility_warnings=[],
    )
    fields.update(overrides)
    return LogicProject(**fields)


@pytest.mark.parametrize("value,expected", [
    ("01:30:00:00", 5400.0),
    ("120", 120.0),
    ("auto", None),
])
def test_cli_smpte_start_reaches_parse_logic_project(tmp_path, monkeypatch, value, expected):
    project_path = tmp_path / "project.logicx"
    project_path.mkdir()
    (project_path / "Alternatives").mkdir()
    output_dir = tmp_path / "output"

    captured = {}

    def fake_parse(*_args, **kwargs):
        captured.update(kwargs)
        return _minimal_project()

    monkeypatch.setattr("logic2ableton.cli.parse_logic_project", fake_parse)
    monkeypatch.setattr("logic2ableton.cli.match_plugins", lambda *_args, **_kwargs: [])

    exit_code = main([str(project_path), "--output", str(output_dir), "--report-only", "--smpte-start", value])

    assert exit_code == 0
    assert captured["smpte_start_seconds"] == expected


def test_cli_smpte_start_invalid_value_exits_2():
    with pytest.raises(SystemExit) as exc:
        main(["--smpte-start", "not-a-time", "project.logicx"])
    assert exc.value.code == 2


def test_cli_keep_unwarped_reaches_generate_als(tmp_path, monkeypatch):
    project_path = tmp_path / "project.logicx"
    project_path.mkdir()
    (project_path / "Alternatives").mkdir()
    output_dir = tmp_path / "output"

    monkeypatch.setattr(
        "logic2ableton.cli.parse_logic_project",
        lambda *_args, **_kwargs: _minimal_project(name="Unwarp Project", track_names=["Guitar"]),
    )
    monkeypatch.setattr("logic2ableton.cli.match_plugins", lambda *_args, **_kwargs: [])

    captured = {}

    def fake_generate_als(project, output_dir, copy_audio=True, template_path=None, *, keep_unwarped=None, keep_outputs=False):
        captured["keep_unwarped"] = keep_unwarped
        als_path = output_dir / f"{project.name} Project" / f"{project.name}.als"
        als_path.parent.mkdir(parents=True, exist_ok=True)
        root = ET.Element("Ableton")
        ET.SubElement(root, "LiveSet")
        with gzip.open(als_path, "wb") as handle:
            handle.write(ET.tostring(root))
        return als_path

    monkeypatch.setattr("logic2ableton.cli.generate_als", fake_generate_als)

    exit_code = main([
        str(project_path), "--output", str(output_dir),
        "--keep-unwarped", "Guitar*", "--keep-unwarped", "Drums",
    ])

    assert exit_code == 0
    assert captured["keep_unwarped"] == ["Guitar*", "Drums"]


def _decoded_logicx(tmp_path, **overrides):
    """A synthetic project whose arrangement decodes: one click region, a "Count" marker at bar 3."""
    spec = dict(
        tracks={1: "Click"},
        sequences=[{"id": 44, "name": "Click", "length": 3840, "notes": [(0, 60, 100, 240)]}],
        midi_regions=[{"bar": 1, "track": 1, "sequence": 44, "lane": 1}],
        markers=[(3, 12, "Count")],
        tempo=120.0,
    )
    spec.update(overrides)
    return build_synthetic_logicx(tmp_path / "project", project_data=build_logic_arrangement_project_data(**spec))


def _report_only(tmp_path, capsys, logicx, timeline_spec=None):
    argv = [str(logicx), "--output", str(tmp_path / "output"), "--report-only"]
    if timeline_spec is not None:
        timeline_path = tmp_path / "timeline.json"
        timeline_path.write_text(json.dumps(timeline_spec))
        argv += ["--timeline", str(timeline_path)]
    exit_code = main(argv)
    return exit_code, capsys.readouterr().out


def test_cli_timeline_loaded_and_attached(tmp_path, capsys):
    exit_code, out = _report_only(tmp_path, capsys, _decoded_logicx(tmp_path), {
        "tempo": [{"bar": 3, "bpm": 90}],
        "markers": [{"bar": 5, "name": "Bridge"}],
    })

    assert exit_code == 0
    assert "TIMELINE:" in out
    assert "90 BPM" in out
    assert "Bridge" in out
    assert "Live tempo automation and locators from --timeline are written into the .als" in out


@pytest.mark.parametrize("json_markers", [False, True])
def test_cli_timeline_keeps_decoded_markers_unless_it_lists_its_own(tmp_path, capsys, json_markers):
    spec = {"tempo": [{"bar": 3, "bpm": 90}]}
    if json_markers:
        spec["markers"] = [{"bar": 5, "name": "Bridge"}]
    exit_code, out = _report_only(tmp_path, capsys, _decoded_logicx(tmp_path), spec)

    assert exit_code == 0
    assert "90 BPM" in out
    assert ("Count" in out) is not json_markers
    assert ("Markers (read from the Logic project):" in out) is not json_markers
    assert ("Bridge" in out) is json_markers


@pytest.mark.parametrize("timeline_spec", [None, {"markers": [{"bar": 5, "name": "Bridge"}]}, {"tempo": [{"bar": 9, "bpm": 90}]}])
def test_cli_tempo_changes_come_from_the_project_unless_the_timeline_lists_tempo(tmp_path, capsys, timeline_spec):
    logicx = _decoded_logicx(tmp_path, tempo=81.5, tempo_changes=[(17, 83.0), (25, 81.5)])
    exit_code, out = _report_only(tmp_path, capsys, logicx, timeline_spec)

    assert exit_code == 0
    from_file = bool(timeline_spec and "tempo" in timeline_spec)
    assert ("Tempo changes (read from the Logic project):" in out) is not from_file
    assert ("Bar 17 beat 1: 83 BPM" in out) is not from_file
    assert ("Bar 25 beat 1: 81.5 BPM" in out) is not from_file
    assert ("Bar 9 beat 1: 90 BPM" in out) is from_file
    assert "Tempo: 81.5 BPM at bar 1" in out
    # nothing left to say about tempo not being transferred
    assert "Logic tempo" not in out and "tempo track changes" not in out


@pytest.mark.parametrize("write_timeline", [None, "missing", "invalid", "tiny_bpm", "huge_bpm"])
def test_cli_timeline_missing_or_invalid_file_exits_1(tmp_path, write_timeline):
    project_path = _decoded_logicx(tmp_path)
    output_dir = tmp_path / "output"

    if write_timeline == "invalid":
        timeline_path = tmp_path / "timeline.json"
        timeline_path.write_text(json.dumps({"tempo": [{"bar": 1}]}))  # missing 'bpm'
    elif write_timeline == "tiny_bpm":
        # 0.0000001 BPM would serialize as Tempo Manual "0" - reject before
        # any output is created rather than writing an unplayable tempo
        # (F13, 2026-09-15 review).
        timeline_path = tmp_path / "timeline.json"
        timeline_path.write_text(json.dumps({"tempo": [{"bar": 1, "bpm": 0.0000001}]}))
    elif write_timeline == "huge_bpm":
        timeline_path = tmp_path / "timeline.json"
        timeline_path.write_text(json.dumps({"tempo": [{"bar": 1, "bpm": 5000}]}))
    else:
        timeline_path = tmp_path / "missing.json"

    exit_code = main([
        str(project_path), "--output", str(output_dir), "--timeline", str(timeline_path),
    ])

    assert exit_code == 1
    report_path = output_dir / "Synth_conversion_report.txt"
    assert report_path.exists()
    assert "Stage: timeline" in report_path.read_text(encoding="utf-8")
    # Timeline validation failed before generation ever ran: no .als output.
    assert not list(output_dir.rglob("*.als"))


def test_cli_good_timeline_with_an_unreadable_project_fails_at_parsing(tmp_path):
    project_path = tmp_path / "broken.logicx"
    (project_path / "Alternatives").mkdir(parents=True)
    timeline_path = tmp_path / "timeline.json"
    timeline_path.write_text(json.dumps({"tempo": [{"bar": 3, "bpm": 90}]}))
    output_dir = tmp_path / "output"

    exit_code = main([str(project_path), "--output", str(output_dir), "--timeline", str(timeline_path)])

    assert exit_code == 1
    reports = list(output_dir.glob("*_conversion_report.txt"))
    assert len(reports) == 1 and "Stage: parsing" in reports[0].read_text(encoding="utf-8")


def test_cli_batch_two_inputs_produce_two_reports_and_json_lines(tmp_path, capsys):
    blob = build_logic_project_data([])
    first = build_synthetic_logicx(tmp_path / "one", project_data=blob)
    second = build_synthetic_logicx(tmp_path / "two", project_data=blob)
    output_dir = tmp_path / "output"

    exit_code = main([
        "logic2ableton", str(first), str(second),
        "--output", str(output_dir), "--report-only", "--json-progress",
    ])
    lines = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines()]
    completes = [line for line in lines if line["stage"] == "complete"]

    assert exit_code == 0
    assert len(completes) == 2
    assert {line["input"] for line in completes} == {str(first), str(second)}
    assert len(list(output_dir.glob("*_conversion_report.txt"))) == 2


def test_cli_batch_exit_code_is_1_when_one_input_fails(tmp_path, capsys):
    blob = build_logic_project_data([])
    good = build_synthetic_logicx(tmp_path / "one", project_data=blob)
    bad = tmp_path / "broken.logicx"
    bad.mkdir()
    output_dir = tmp_path / "output"

    exit_code = main([
        "logic2ableton", str(good), str(bad),
        "--output", str(output_dir), "--report-only", "--json-progress",
    ])

    assert exit_code == 1
    assert (output_dir / "Synth_conversion_report.txt").exists()
    assert (output_dir / "broken_conversion_report.txt").exists()

    lines = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines()]
    errors = [line for line in lines if line["stage"] == "error"]
    assert len(errors) == 1
    assert errors[0]["input"] == str(bad)


def test_cli_batch_generate_mixer_template_does_not_overwrite_across_inputs(tmp_path):
    blob = build_logic_project_data([])
    first = build_synthetic_logicx(tmp_path / "one", project_data=blob)
    second = build_synthetic_logicx(tmp_path / "two", project_data=blob)
    output_dir = tmp_path / "output"

    exit_code = main([
        "logic2ableton", str(first), str(second),
        "--output", str(output_dir), "--report-only", "--generate-mixer-template",
    ])

    assert exit_code == 0
    templates = sorted(output_dir.glob("mixer_overrides*.json"))
    assert len(templates) == 2


def test_cli_smpte_start_auto_infers_hour_floor_end_to_end(tmp_path, capsys):
    logicx = build_synthetic_logicx(tmp_path, project_data=b"")
    audio_dir = logicx / "Media" / "Audio Files"
    write_smpte_stamped_wav(audio_dir / "Early.wav", smpte_seconds=7210.0, sample_rate=44_100)
    write_smpte_stamped_wav(audio_dir / "Later.wav", smpte_seconds=7300.0, sample_rate=44_100)
    output_dir = tmp_path / "output"

    exit_code = main([str(logicx), "--output", str(output_dir), "--report-only", "--smpte-start", "auto"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "SMPTE start: 02:00:00:00 (inferred from the earliest recording)" in captured.out


def test_cli_single_run_mixer_template_overwrites_on_repeat(tmp_path):
    blob = build_logic_project_data([])
    logicx = build_synthetic_logicx(tmp_path, project_data=blob)
    output_dir = tmp_path / "output"

    for _ in range(3):
        exit_code = main([
            str(logicx), "--output", str(output_dir), "--report-only", "--generate-mixer-template",
        ])
        assert exit_code == 0

    assert sorted(p.name for p in output_dir.glob("mixer_overrides*.json")) == ["mixer_overrides.json"]


def test_cli_folder_style_project_converts_and_copies_audio(tmp_path, capsys):
    from scripts.fixture_builders import build_folder_style_logicx

    logicx = build_folder_style_logicx(tmp_path, name="Folder Song")
    write_smpte_stamped_wav(logicx.parent / "Audio Files" / "LTC#01.wav", smpte_seconds=3600.0, sample_rate=44_100)
    write_smpte_stamped_wav(logicx.parent / "Audio Files" / "Guitar#01.wav", smpte_seconds=3602.0, sample_rate=44_100)
    output_dir = tmp_path / "output"

    exit_code = main([str(logicx), "--output", str(output_dir), "--keep-unwarped", "ltc*"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Audio files: folder-style project" in captured.out
    assert "Unwarped (won't stretch with tempo changes): LTC" in captured.out
    project_dir = output_dir / "Folder Song Project"
    assert (project_dir / "Folder Song.als").exists()
    copied = sorted(p.name for p in (project_dir / "Samples" / "Imported").glob("*.wav"))
    assert copied == ["Guitar#01.wav", "LTC#01.wav"]


def test_cli_report_only_warns_about_unmatched_keep_unwarped_pattern(tmp_path, capsys):
    logicx = build_synthetic_logicx(tmp_path, project_data=b"")
    write_smpte_stamped_wav(logicx / "Media" / "Audio Files" / "Guitar#01.wav", smpte_seconds=3600.0, sample_rate=44_100)
    output_dir = tmp_path / "output"

    exit_code = main([
        str(logicx), "--output", str(output_dir), "--report-only",
        "--keep-unwarped", "Guitar*", "--keep-unwarped", "Pilot*",
    ])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "--keep-unwarped pattern 'Pilot*' did not match any track name." in captured.out
    assert "'Guitar*' did not match" not in captured.out


def test_cli_report_only_includes_midi_totals_for_a_midi_only_project(tmp_path, capsys):
    """A recoverable MIDI sequence with no audio must still surface in the preview."""
    blob = build_logic_project_data([[(60, 100, 38400, 960)]])
    logicx = build_synthetic_logicx(tmp_path, project_data=blob)
    output_dir = tmp_path / "output"

    exit_code = main([
        str(logicx), "--output", str(output_dir), "--report-only", "--json-progress",
    ])
    lines = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines()]
    complete = [line for line in lines if line["stage"] == "complete"][0]

    assert exit_code == 0
    assert complete["tracks"] == 0
    assert complete["audio_files"] == 0
    assert complete["midi_tracks"] == 1
    assert complete["midi_notes"] == 1


def test_cli_smpte_start_qualifier_tells_explicit_from_default(tmp_path, capsys):
    logicx = build_synthetic_logicx(tmp_path, project_data=b"")
    write_smpte_stamped_wav(logicx / "Media" / "Audio Files" / "Guitar#01.wav", smpte_seconds=3600.0, sample_rate=44_100)
    output_dir = tmp_path / "output"

    assert main([str(logicx), "--output", str(output_dir), "--report-only"]) == 0
    assert "SMPTE start: 01:00:00:00 (default)" in capsys.readouterr().out

    assert main([str(logicx), "--output", str(output_dir), "--report-only", "--smpte-start", "01:00:00:00"]) == 0
    assert "SMPTE start: 01:00:00:00 (from --smpte-start)" in capsys.readouterr().out


def test_cli_finishes_on_a_console_that_cannot_show_a_name(tmp_path):
    """macOS stores "ö" as "o" plus a combining mark, which a Windows code page cannot print; the
    report used to crash the run there, before the Live set was written."""
    blob = build_logic_arrangement_project_data(
        tracks={1: "Cue Eichhörnchen"},
        sequences=[{"id": 44, "name": "Click", "length": 3840, "notes": [(0, 60, 100, 240)]}],
        midi_regions=[{"bar": 1, "track": 1, "sequence": 44, "lane": 1}],
    )
    source = build_synthetic_logicx(tmp_path / "Eichhörnchentag", project_data=blob)
    env = {**os.environ, "PYTHONIOENCODING": "cp1252"}
    result = subprocess.run(
        [sys.executable, "-m", "logic2ableton.cli", str(source), "--output", str(tmp_path / "out")],
        capture_output=True, env=env, cwd=str(Path(__file__).resolve().parents[1]),
    )
    assert result.returncode == 0, result.stderr.decode("cp1252", errors="replace")[-600:]
    assert list((tmp_path / "out").rglob("*.als"))
