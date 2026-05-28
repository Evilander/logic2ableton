import json
import struct
from pathlib import Path

import pytest

from logic2ableton.models import AudioFileRef
from logic2ableton.logic_parser import (
    _build_compatibility_warnings,
    _get_aiff_timestamp,
    discover_audio_files,
    extract_plugins,
    extract_regions,
    load_mixer_overrides,
    parse_logic_project,
    parse_metadata,
    parse_project_info,
)

TEST_PROJECT = Path("Might Last Forever.logicx")


# Task 3 tests
@pytest.mark.needs_test_project
def test_parse_project_info():
    info = parse_project_info(TEST_PROJECT)
    assert info["name"] == "Might Last Forever"
    assert "Logic Pro" in info["last_saved_from"]
    assert info["variant_names"]["0"] == "Might Last Forever"


@pytest.mark.needs_test_project
def test_parse_metadata():
    meta = parse_metadata(TEST_PROJECT, alternative=0)
    assert meta["tempo"] == 120.0
    assert meta["time_sig_numerator"] == 4
    assert meta["time_sig_denominator"] == 4
    assert meta["sample_rate"] == 44100
    assert meta["num_tracks"] == 12
    assert meta["song_key"] == "C"
    assert meta["song_gender_key"] == "major"
    assert len(meta["audio_files"]) == 28
    assert len(meta["unused_audio_files"]) == 7


# Task 4 tests
@pytest.mark.needs_test_project
def test_extract_plugins_from_project_data():
    plugins = extract_plugins(TEST_PROJECT, alternative=0)
    assert len(plugins) == 24
    subtypes = [p.au_subtype for p in plugins]
    assert "TG5M" in subtypes
    assert "76CM" in subtypes
    assert "L1CM" in subtypes


@pytest.mark.needs_test_project
def test_extract_plugins_vintage_vocal():
    plugins = extract_plugins(TEST_PROJECT, alternative=0)
    vintage = [p for p in plugins if p.name == "Vintage Vocal"]
    assert len(vintage) == 1
    assert vintage[0].au_subtype == "TG5M"


# Task 5 tests
@pytest.mark.needs_test_project
def test_discover_audio_files():
    refs = discover_audio_files(TEST_PROJECT)
    assert len(refs) == 38
    track_names = sorted(set(r.track_name for r in refs))
    assert "KICK IN" in track_names
    assert "Tyler Amp" in track_names
    assert "scratch vox 2" in track_names


@pytest.mark.needs_test_project
def test_discover_audio_files_takes():
    refs = discover_audio_files(TEST_PROJECT)
    kick_refs = [r for r in refs if r.track_name == "KICK IN"]
    assert len(kick_refs) == 3
    take_numbers = sorted(r.take_number for r in kick_refs)
    assert take_numbers == [1, 2, 3]


@pytest.mark.needs_test_project
def test_discover_audio_files_comps():
    refs = discover_audio_files(TEST_PROJECT)
    comp_refs = [r for r in refs if r.is_comp]
    assert len(comp_refs) == 1
    assert comp_refs[0].comp_name == "Comp A"


# Task 6 tests
@pytest.mark.needs_test_project
def test_parse_logic_project():
    project = parse_logic_project(TEST_PROJECT, alternative=0)
    assert project.name == "Might Last Forever"
    assert project.tempo == 120.0
    assert project.time_sig_numerator == 4
    assert project.sample_rate == 44100
    assert len(project.audio_files) == 38
    assert len(project.plugins) == 24
    assert project.alternative == 0


@pytest.mark.needs_test_project
def test_parse_logic_project_track_names():
    project = parse_logic_project(TEST_PROJECT, alternative=0)
    assert "KICK IN" in project.track_names
    assert "Tyler Amp" in project.track_names
    assert "SNARE" in project.track_names
    assert "BASS GUITAR" in project.track_names
    assert "keys" in project.track_names
    assert "scratch vox 1" in project.track_names
    assert "scratch vox 2" in project.track_names


# Phase 2: Region timing tests

@pytest.mark.needs_test_project
def test_extract_regions_count():
    regions = extract_regions(TEST_PROJECT, alternative=0)
    # BWF timestamps exist for all recorded WAV files (not imported MP3s etc.)
    assert len(regions) >= 35


@pytest.mark.needs_test_project
def test_extract_regions_kick_in_01():
    """KICK IN#01 starts at bar 2 (beat 4) = 88,200 samples after SMPTE offset."""
    regions = extract_regions(TEST_PROJECT, alternative=0)
    assert regions["KICK IN#01.wav"] == 88_200


@pytest.mark.needs_test_project
def test_extract_regions_kick_in_02():
    """KICK IN#02 starts at bar 320 (beat 1276) = 28,135,800 samples after SMPTE offset."""
    regions = extract_regions(TEST_PROJECT, alternative=0)
    assert regions["KICK IN#02.wav"] == 28_135_800


def test_extract_regions_missing_project():
    regions = extract_regions(Path("/nonexistent/project.logicx"), alternative=0)
    assert regions == {}


@pytest.mark.needs_test_project
def test_parse_logic_project_has_start_positions():
    project = parse_logic_project(TEST_PROJECT, alternative=0)
    kick_01 = [r for r in project.audio_files if r.filename == "KICK IN#01.wav"]
    assert len(kick_01) == 1
    assert kick_01[0].start_position_samples == 88_200  # bar 2

    kick_02 = [r for r in project.audio_files if r.filename == "KICK IN#02.wav"]
    assert len(kick_02) == 1
    assert kick_02[0].start_position_samples == 28_135_800  # bar 320


def test_get_aiff_timestamp_handles_odd_chunk_without_pad(tmp_path):
    def marker_bytes(marker_id: int, position: int, name: str) -> bytes:
        raw_name = name.encode("ascii")
        payload = struct.pack(">HI", marker_id, position) + bytes([len(raw_name)]) + raw_name
        if len(raw_name) % 2 == 0:
            payload += b"\x00"
        return payload

    ssnd_chunk = b"SSND" + struct.pack(">I", 1) + b"\x00"  # odd-sized chunk with no pad byte
    mark_payload = (
        struct.pack(">H", 2)
        + marker_bytes(1, 400, "Start")
        + marker_bytes(2, 0, "Timestamp: 158760500")
    )
    mark_chunk = b"MARK" + struct.pack(">I", len(mark_payload)) + mark_payload
    body = b"AIFF" + ssnd_chunk + mark_chunk
    form = b"FORM" + struct.pack(">I", len(body)) + body

    aiff_path = tmp_path / "odd_mark.aif"
    aiff_path.write_bytes(form)

    timestamp, start_offset = _get_aiff_timestamp(aiff_path)
    assert timestamp == 158_760_500
    assert start_offset == 400


def test_load_mixer_overrides(tmp_path):
    data = {
        "KICK IN": {"volume_db": -3.0, "pan": 0.0},
        "SNARE": {"volume_db": -6.0, "pan": 0.2, "is_muted": True},
    }
    json_path = tmp_path / "mixer_overrides.json"
    json_path.write_text(json.dumps(data), encoding="utf-8")

    result = load_mixer_overrides(json_path)
    assert "KICK IN" in result
    assert result["KICK IN"].volume_db == -3.0
    assert result["SNARE"].is_muted is True
    assert result["SNARE"].pan == 0.2


def test_load_mixer_overrides_missing_file():
    result = load_mixer_overrides(Path("nonexistent.json"))
    assert result == {}


def test_build_compatibility_warnings_for_missing_and_unpositioned_audio(tmp_path):
    audio_file = tmp_path / "imported.wav"
    audio_file.write_bytes(b"")
    refs = [
        AudioFileRef(
            filename="imported.wav",
            track_name="Imported",
            take_number=0,
            is_comp=False,
            comp_name="",
            file_path=audio_file,
        )
    ]

    warnings = _build_compatibility_warnings(
        {
            "audio_files": ["imported.wav", "external.wav"],
            "num_tracks": 3,
        },
        refs,
        regions={},
    )

    assert any("external.wav" in warning for warning in warnings)
    assert any("default to bar 1" in warning for warning in warnings)
    assert any("3 track(s)" in warning for warning in warnings)


# Alternative discovery tests
import plistlib

from logic2ableton.logic_parser import discover_alternatives, resolve_alternative


def _make_logicx(tmp_path: Path, *, alternatives: list[int], active_variant: int) -> Path:
    logicx = tmp_path / "Sample.logicx"
    resources = logicx / "Resources"
    resources.mkdir(parents=True)
    with open(resources / "ProjectInformation.plist", "wb") as f:
        plistlib.dump({"ActiveVariant": active_variant, "VariantNames": {str(active_variant): "Sample"}}, f)
    for index in alternatives:
        alt = logicx / "Alternatives" / f"{index:03d}"
        alt.mkdir(parents=True)
        with open(alt / "MetaData.plist", "wb") as f:
            plistlib.dump(
                {
                    "BeatsPerMinute": 120.0,
                    "SongSignatureNumerator": 4,
                    "SongSignatureDenominator": 4,
                    "SampleRate": 44100,
                    "NumberOfTracks": 0,
                    "AudioFiles": [],
                },
                f,
            )
        (alt / "ProjectData").write_bytes(b"")
    return logicx


def test_discover_alternatives_finds_nonzero_index(tmp_path):
    logicx = _make_logicx(tmp_path, alternatives=[4], active_variant=4)
    assert discover_alternatives(logicx) == [4]


def test_resolve_alternative_prefers_active_variant(tmp_path):
    logicx = _make_logicx(tmp_path, alternatives=[0, 2, 4], active_variant=4)
    assert resolve_alternative(logicx, None, 4) == 4


def test_resolve_alternative_falls_back_to_lowest(tmp_path):
    logicx = _make_logicx(tmp_path, alternatives=[2, 5], active_variant=9)
    assert resolve_alternative(logicx, None, 9) == 2


def test_resolve_alternative_honors_explicit_request(tmp_path):
    logicx = _make_logicx(tmp_path, alternatives=[0, 4], active_variant=4)
    assert resolve_alternative(logicx, 0, 4) == 0


def test_resolve_alternative_rejects_missing_request(tmp_path):
    logicx = _make_logicx(tmp_path, alternatives=[4], active_variant=4)
    with pytest.raises(FileNotFoundError, match="Available alternative"):
        resolve_alternative(logicx, 0, 4)


def test_resolve_alternative_errors_when_none_exist(tmp_path):
    logicx = tmp_path / "Empty.logicx"
    (logicx / "Alternatives").mkdir(parents=True)
    with pytest.raises(FileNotFoundError, match="No Logic alternatives"):
        resolve_alternative(logicx, None, None)


def test_parse_logic_project_auto_detects_alternative(tmp_path):
    logicx = _make_logicx(tmp_path, alternatives=[4], active_variant=4)
    project = parse_logic_project(logicx)
    assert project.alternative == 4
    assert project.tempo == 120.0


# Synthetic plugin extraction (no real .logicx fixture required)
def _fourcc(code: str) -> int:
    return struct.unpack(">I", code.encode("ascii"))[0]


def test_extract_plugins_from_synthetic_project_data():
    plist_a = plistlib.dumps(
        {"name": "Test EQ", "type": _fourcc("aufx"), "subtype": _fourcc("TST1"), "manufacturer": _fourcc("Acme")}
    )
    plist_b = plistlib.dumps(
        {"name": "Test Comp", "type": _fourcc("aufx"), "subtype": _fourcc("TST2"), "manufacturer": _fourcc("Acme")}
    )
    blob = b"\x00\x01binary-noise" + plist_a + b"\xff\xff padding" + plist_b + b"\x00trailer"

    plugins = extract_plugins(Path("/does-not-exist.logicx"), _data=blob)

    assert [p.name for p in plugins] == ["Test EQ", "Test Comp"]
    assert plugins[0].au_subtype == "TST1"
    assert plugins[0].au_type == "aufx"
    assert plugins[0].au_manufacturer == "Acme"
    assert plugins[1].au_subtype == "TST2"


def test_extract_plugins_ignores_non_plugin_plists():
    # A plist without a "name" key must be skipped, not crash.
    junk = plistlib.dumps({"unrelated": "data"})
    plugin = plistlib.dumps({"name": "Real", "type": _fourcc("aumf"), "subtype": _fourcc("RL01"), "manufacturer": 0})
    plugins = extract_plugins(Path("/x.logicx"), _data=junk + plugin)
    assert [p.name for p in plugins] == ["Real"]


def test_extract_plugins_empty_data_returns_empty():
    assert extract_plugins(Path("/x.logicx"), _data=b"") == []


# Forward-lane MIDI/instrument detection
def _make_logicx_with_instruments(tmp_path: Path, *, ultrabeat=0, alchemy=0) -> Path:
    logicx = tmp_path / "Inst.logicx"
    (logicx / "Resources").mkdir(parents=True)
    with open(logicx / "Resources" / "ProjectInformation.plist", "wb") as f:
        plistlib.dump({"ActiveVariant": 0, "VariantNames": {"0": "Inst"}}, f)
    alt = logicx / "Alternatives" / "000"
    alt.mkdir(parents=True)
    with open(alt / "MetaData.plist", "wb") as f:
        plistlib.dump(
            {
                "BeatsPerMinute": 120.0,
                "SongSignatureNumerator": 4,
                "SongSignatureDenominator": 4,
                "SampleRate": 44100,
                "NumberOfTracks": 2,
                "AudioFiles": [],
                "UltrabeatFiles": [f"Ultrabeat/{i}.ub" for i in range(ultrabeat)],
                "AlchemyFiles": [f"Alchemy/{i}.aaz" for i in range(alchemy)],
            },
            f,
        )
    (alt / "ProjectData").write_bytes(b"")
    return logicx


def test_software_instrument_files_counted():
    meta = {
        "SamplerInstrumentsFiles": ["a", "b"],
        "QuicksamplerFiles": ["c"],
        "AlchemyFiles": ["d"],
        "UltrabeatFiles": [],
        "ImpulsResponsesFiles": ["ignored1", "ignored2"],  # reverb IR, not a MIDI signal
    }
    # Mirror parse_metadata's instrument tally without needing a real bundle.
    keys = ("SamplerInstrumentsFiles", "QuicksamplerFiles", "AlchemyFiles", "UltrabeatFiles")
    assert sum(len(meta.get(k, [])) for k in keys) == 4


def test_parse_logic_project_flags_instrument_content(tmp_path):
    logicx = _make_logicx_with_instruments(tmp_path, ultrabeat=3, alchemy=1)
    project = parse_logic_project(logicx)
    assert project.software_instrument_files == 4
    assert any("software-instrument" in w and "audio only" in w for w in project.compatibility_warnings)


def test_parse_logic_project_no_instrument_warning_when_absent(tmp_path):
    logicx = _make_logicx_with_instruments(tmp_path, ultrabeat=0, alchemy=0)
    project = parse_logic_project(logicx)
    assert project.software_instrument_files == 0
    assert not any("software-instrument" in w for w in project.compatibility_warnings)
