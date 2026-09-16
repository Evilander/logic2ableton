"""CLI integration tests for the Pro Tools lanes."""

import json
from copy import deepcopy
from pathlib import Path

import pytest

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


@pytest.mark.parametrize("mode", ["protools2ableton", "protools2logic"])
def test_protools_report_only_names_missing_media(tmp_path, capsys, mode):
    """Preview and conversion must both report missing source media."""
    ptx = build_synthetic_ptx(tmp_path)
    # Guitar.wav is referenced by the session but deliberately never written.
    out_dir = tmp_path / "out"

    preview_rc = main([mode, str(ptx), "--output", str(out_dir), "--report-only", "--json-progress"])
    preview_lines = _json_lines(capsys)
    preview_complete = [line for line in preview_lines if line["stage"] == "complete"][0]

    assert preview_rc == 0
    assert any("Guitar.wav" in w for w in preview_complete["compatibility_warnings"])
    assert "Guitar.wav [missing]" in preview_complete["report"]
    assert "1 referenced, 0 found, 1 missing, 0 skipped" in preview_complete["report"]
    assert Path(preview_complete["report_path"]).read_text(encoding="utf-8") == preview_complete["report"]

    conversion_rc = main([mode, str(ptx), "--output", str(out_dir), "--json-progress"])
    conversion_lines = _json_lines(capsys)
    conversion_complete = [line for line in conversion_lines if line["stage"] == "complete"][0]

    assert conversion_rc == 0
    assert any("Guitar.wav" in w for w in conversion_complete["compatibility_warnings"])
    assert set(preview_complete["compatibility_warnings"]) <= set(conversion_complete["compatibility_warnings"])


@pytest.mark.parametrize("mode", ["protools2ableton", "protools2logic"])
def test_protools_preflight_failure_returns_a_structured_error(tmp_path, capsys, monkeypatch, mode):
    source = build_synthetic_ptx(tmp_path)

    def fail_resolution(session):
        raise PermissionError("source directory is unreadable")

    monkeypatch.setattr("logic2ableton.cli.resolve_protools_media", fail_resolution)
    assert main([mode, str(source), "--output", str(tmp_path / "out"), "--report-only", "--json-progress"]) == 1
    complete = _json_lines(capsys)[-1]
    assert complete["stage"] == "error"
    assert "source directory is unreadable" in complete["message"]


@pytest.mark.parametrize("mode", ["protools2ableton", "protools2logic"])
def test_protools_preview_counts_merged_tracks_and_nonempty_midi(tmp_path, capsys, monkeypatch, mode):
    from logic2ableton.protools_parser import ProToolsMidiTrack, parse_protools_session

    ptx = build_synthetic_ptx(tmp_path)
    write_test_wav(ptx.parent / "Audio Files" / "Guitar.wav", frames=118050, sample_rate=48000)
    session = parse_protools_session(ptx)
    session.tracks.append(deepcopy(session.tracks[0]))
    session.midi_tracks.append(ProToolsMidiTrack(name="Empty", notes=[]))
    monkeypatch.setattr("logic2ableton.cli.parse_protools_session", lambda _: session)
    args = [mode, str(ptx), "--output", str(tmp_path / "out"), "--json-progress"]

    assert main([*args, "--report-only"]) == 0
    preview = _json_lines(capsys)[-1]
    assert main(args) == 0
    complete = _json_lines(capsys)[-1]

    for event in (preview, complete):
        assert event["tracks"] == 1
        assert event["clips"] == 1
        assert event["midi_tracks"] == 1
        assert event["midi_notes"] == 2


@pytest.mark.parametrize("mode", [
    "logic2ableton", "logic2protools", "ableton2logic", "ableton2protools",
    "protools2ableton", "protools2logic",
])
def test_mixed_preview_and_conversion_use_the_same_track_counts(tmp_path, capsys, mode):
    from logic2ableton.ableton_generator import generate_als
    from logic2ableton.logic_parser import parse_logic_project

    if mode.startswith("protools"):
        source = build_synthetic_ptx(tmp_path)
        write_test_wav(source.parent / "Audio Files" / "Guitar.wav", frames=118050, sample_rate=48000)
        notes = 2
    else:
        blob = build_logic_project_data([[(60, 100, 38400, 960)]])
        source = build_synthetic_logicx(tmp_path, project_data=blob)
        write_test_wav(source / "Media" / "Audio Files" / "Guitar.wav")
        if mode.startswith("ableton"):
            source = generate_als(parse_logic_project(source), tmp_path / "live")
        notes = 1
    args = [mode, str(source), "--output", str(tmp_path / "out"), "--json-progress"]
    assert main([*args, "--report-only"]) == 0
    preview = _json_lines(capsys)[-1]
    assert main(args) == 0
    complete = _json_lines(capsys)[-1]

    for event in (preview, complete):
        assert event["tracks"] == 1
        assert event["midi_tracks"] == 1
        assert event["midi_notes"] == notes


@pytest.mark.parametrize("mode", ["logic2ableton", "protools2ableton"])
def test_native_midi_count_survives_a_failed_sidecar_export(tmp_path, capsys, monkeypatch, mode):
    import gzip
    import xml.etree.ElementTree as ET

    if mode == "protools2ableton":
        source = build_synthetic_ptx(tmp_path)
    else:
        blob = build_logic_project_data([[(60, 100, 38400, 960)]])
        source = build_synthetic_logicx(tmp_path, project_data=blob)
    original_write = Path.write_bytes

    def fail_midi_write(path, data):
        if path.suffix == ".mid":
            raise PermissionError("MIDI sidecar is read-only")
        return original_write(path, data)

    monkeypatch.setattr(Path, "write_bytes", fail_midi_write)
    assert main([mode, str(source), "--output", str(tmp_path / "out"), "--json-progress"]) == 0
    complete = _json_lines(capsys)[-1]
    with gzip.open(complete["als_path"]) as handle:
        root = ET.parse(handle).getroot()
    assert len(root.findall(".//MidiTrack")) == 1
    assert complete["midi_tracks"] == 1
    assert complete["midi_files"] == 0
    assert any("MIDI export failed" in warning for warning in complete["compatibility_warnings"])


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


def test_logic2protools_accepts_smpte_start(tmp_path, capsys, monkeypatch):
    blob = build_logic_project_data([[(60, 100, 38400, 960)]])
    logicx = build_synthetic_logicx(tmp_path, project_data=blob)
    out_dir = tmp_path / "out"

    captured = {}
    from logic2ableton import cli

    original_parse = cli.parse_logic_project

    def spy_parse(*args, **kwargs):
        captured.update(kwargs)
        return original_parse(*args, **kwargs)

    monkeypatch.setattr(cli, "parse_logic_project", spy_parse)

    rc = main([
        "logic2protools", str(logicx), "--output", str(out_dir), "--report-only",
        "--json-progress", "--smpte-start", "02:00:00:00",
    ])
    assert rc == 0
    assert captured["smpte_start_seconds"] == 7200.0


def test_protools_lane_rejects_wrong_extension(tmp_path, capsys):
    not_ptx = tmp_path / "set.als"
    not_ptx.write_bytes(b"x")
    rc = main(["protools2ableton", str(not_ptx), "--output", str(tmp_path / "out"), "--json-progress"])
    assert rc == 1
    lines = _json_lines(capsys)
    assert lines[-1]["stage"] == "error"
    assert "Pro Tools session" in lines[-1]["message"]


def test_protools2ableton_batch_rejects_wrong_lane_but_completes_good_input(tmp_path, capsys):
    ptx = build_synthetic_ptx(tmp_path)
    write_test_wav(ptx.parent / "Audio Files" / "Guitar.wav", frames=118050, sample_rate=48000)
    wrong_lane = tmp_path / "set.als"
    wrong_lane.write_bytes(b"x")
    out_dir = tmp_path / "out"

    rc = main([
        "protools2ableton", str(ptx), str(wrong_lane),
        "--output", str(out_dir), "--json-progress",
    ])
    assert rc == 1

    lines = _json_lines(capsys)
    completes = [line for line in lines if line["stage"] == "complete"]
    errors = [line for line in lines if line["stage"] == "error"]
    assert len(completes) == 1
    assert completes[0]["input"] == str(ptx)
    assert len(errors) == 1
    assert errors[0]["input"] == str(wrong_lane)
    assert "Pro Tools session" in errors[0]["message"]
    assert len(list(out_dir.glob("*_conversion_report.txt"))) == 2


def test_protools_lane_reports_parse_failure(tmp_path, capsys):
    bogus = tmp_path / "broken.ptx"
    bogus.write_bytes(b"\x00" * 64)
    rc = main([str(bogus), "--output", str(tmp_path / "out"), "--json-progress"])
    assert rc == 1
    lines = _json_lines(capsys)
    assert lines[-1]["stage"] == "error"
    assert Path(lines[-1]["report_path"]).exists()
