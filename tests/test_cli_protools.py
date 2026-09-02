"""CLI integration tests for the Pro Tools lanes."""

import json
from pathlib import Path

from scripts.fixture_builders import (
    build_logic_project_data,
    build_synthetic_logicx,
    build_synthetic_ptx,
)

from logic2ableton.cli import (
    ABLETON2PT_MODE,
    LOGIC2PT_MODE,
    PT2ABLETON_MODE,
    PT2LOGIC_MODE,
    _detect_mode,
    main,
)

from conftest import create_test_als, write_test_wav


def _json_lines(capsys):
    out = capsys.readouterr().out
    return [json.loads(line) for line in out.strip().splitlines() if line.strip()]


def test_detect_mode_defaults_ptx_to_ableton():
    assert _detect_mode("some_python", ["session.ptx"]) == PT2ABLETON_MODE
    assert _detect_mode("some_python", ["Session.PTS"]) == PT2ABLETON_MODE
    assert _detect_mode("protools2logic", ["session.ptx"]) == PT2LOGIC_MODE
    assert _detect_mode("ableton2protools", ["set.als"]) == ABLETON2PT_MODE
    assert _detect_mode("logic2protools", ["proj.logicx"]) == LOGIC2PT_MODE
    # legacy behavior untouched
    assert _detect_mode("some_python", ["set.als"]) == "ableton2logic"
    assert _detect_mode("some_python", ["proj.logicx"]) == "logic2ableton"


def test_protools2ableton_full_conversion(tmp_path, capsys):
    ptx = build_synthetic_ptx(tmp_path)
    write_test_wav(ptx.parent / "Audio Files" / "Guitar.wav", frames=118050, sample_rate=48000)
    out_dir = tmp_path / "out"

    rc = main([str(ptx), "--output", str(out_dir), "--json-progress"])
    assert rc == 0

    lines = _json_lines(capsys)
    assert lines[-1]["stage"] == "complete"
    assert lines[-1]["direction"] == PT2ABLETON_MODE
    als_path = Path(lines[-1]["als_path"])
    assert als_path.exists()
    assert lines[-1]["tracks"] == 1
    assert lines[-1]["midi_tracks"] == 1
    assert lines[-1]["midi_notes"] == 2
    assert any("tempo" in w.lower() for w in lines[-1]["compatibility_warnings"])
    assert Path(lines[-1]["report_path"]).exists()

    # Audio was copied into the Ableton project folder
    assert (als_path.parent / "Samples" / "Imported" / "Guitar.wav").exists()
    # MIDI .mid exports were written next to the set
    assert list((als_path.parent / "MIDI").glob("*.mid"))


def test_protools2logic_creates_transfer_package(tmp_path, capsys):
    ptx = build_synthetic_ptx(tmp_path)
    write_test_wav(ptx.parent / "Audio Files" / "Guitar.wav", frames=118050, sample_rate=48000)
    out_dir = tmp_path / "out"

    rc = main(["protools2logic", str(ptx), "--output", str(out_dir), "--json-progress"])
    assert rc == 0

    lines = _json_lines(capsys)
    assert lines[-1]["stage"] == "complete"
    assert lines[-1]["direction"] == PT2LOGIC_MODE
    assert Path(lines[-1]["package_path"]).is_dir()
    assert Path(lines[-1]["artifact_path"]).exists()
    assert lines[-1]["midi_notes"] == 2


def test_ableton2protools_creates_transfer_package(tmp_path, capsys):
    als = create_test_als(tmp_path)
    out_dir = tmp_path / "out"

    rc = main(["ableton2protools", str(als), "--output", str(out_dir), "--json-progress"])
    assert rc == 0

    lines = _json_lines(capsys)
    payload = lines[-1]
    assert payload["stage"] == "complete"
    assert payload["direction"] == ABLETON2PT_MODE
    package = Path(payload["package_path"])
    assert package.is_dir()
    assert (package / "manifest.json").exists()
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["target"] == "protools"
    assert (package / "IMPORT GUIDE.txt").exists()
    assert payload["tracks"] == 2


def test_logic2protools_report_only(tmp_path, capsys, monkeypatch):
    blob = build_logic_project_data([[(60, 100, 38400, 960)]])
    logicx = build_synthetic_logicx(tmp_path, project_data=blob)
    out_dir = tmp_path / "out"

    rc = main(["logic2protools", str(logicx), "--output", str(out_dir), "--report-only", "--json-progress"])
    assert rc == 0

    lines = _json_lines(capsys)
    payload = lines[-1]
    assert payload["stage"] == "complete"
    assert payload["direction"] == LOGIC2PT_MODE
    assert "Pro Tools Transfer Report" in payload["report"]
    assert payload["midi_notes"] == 1
    assert Path(payload["report_path"]).exists()


def test_protools_lane_rejects_wrong_extension(tmp_path, capsys):
    not_ptx = tmp_path / "set.als"
    not_ptx.write_bytes(b"x")
    rc = main(["protools2ableton", str(not_ptx), "--output", str(tmp_path / "out"), "--json-progress"])
    assert rc == 1
    lines = _json_lines(capsys)
    assert lines[-1]["stage"] == "error"
    assert "Pro Tools session" in lines[-1]["message"]


def test_protools_lane_reports_parse_failure(tmp_path, capsys):
    bogus = tmp_path / "broken.ptx"
    bogus.write_bytes(b"\x00" * 64)
    rc = main([str(bogus), "--output", str(tmp_path / "out"), "--json-progress"])
    assert rc == 1
    lines = _json_lines(capsys)
    assert lines[-1]["stage"] == "error"
    assert Path(lines[-1]["report_path"]).exists()
