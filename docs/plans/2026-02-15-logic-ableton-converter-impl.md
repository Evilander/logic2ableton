# Logic Pro to Ableton Live Converter — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a Python CLI tool that converts Logic Pro `.logicx` projects into Ableton Live `.als` files, transferring audio stems, track structure, and providing plugin replacement suggestions.

**Architecture:** Six Python modules — a Logic parser (plist + binary extraction), plugin database (AU component ID mappings), VST3 scanner (local inventory), plugin matcher (category-based matching), Ableton generator (gzipped XML output), and a CLI entry point. No external dependencies beyond Python stdlib.

**Tech Stack:** Python 3.10+ (stdlib only: `plistlib`, `gzip`, `struct`, `re`, `xml.etree.ElementTree`, `pathlib`, `shutil`, `argparse`, `dataclasses`)

**Test data:** `Might Last Forever.logicx` in project root — 12 tracks, 38 audio files, 24 Waves/Logic plugins, 120 BPM, 4/4, 44100 Hz.

---

### Task 1: Data Models

**Files:**
- Create: `logic2ableton/models.py`
- Test: `tests/test_models.py`

**Step 1: Write the failing test**

```python
# tests/test_models.py
from pathlib import Path
from logic2ableton.models import AudioFileRef, PluginInstance, LogicProject

def test_audio_file_ref_creation():
    ref = AudioFileRef(
        filename="KICK IN#01.wav",
        track_name="KICK IN",
        take_number=1,
        is_comp=False,
        comp_name="",
        file_path=Path("/fake/path/KICK IN#01.wav"),
    )
    assert ref.track_name == "KICK IN"
    assert ref.take_number == 1
    assert ref.is_comp is False

def test_plugin_instance_creation():
    plugin = PluginInstance(
        name="Big Guitar 2",
        au_type="aufx",
        au_subtype="TG5M",
        au_manufacturer="ksWV",
        is_waves=True,
        raw_plist={"name": "Big Guitar 2"},
    )
    assert plugin.au_subtype == "TG5M"
    assert plugin.is_waves is True

def test_logic_project_creation():
    project = LogicProject(
        name="Might Last Forever",
        tempo=120.0,
        time_sig_numerator=4,
        time_sig_denominator=4,
        sample_rate=44100,
        audio_files=[],
        plugins=[],
        track_names=["KICK IN", "Tyler Amp"],
        alternative=0,
    )
    assert project.tempo == 120.0
    assert len(project.track_names) == 2
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'logic2ableton'`

**Step 3: Write minimal implementation**

```python
# logic2ableton/__init__.py
# (empty, makes this a package)

# logic2ableton/models.py
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class AudioFileRef:
    filename: str
    track_name: str
    take_number: int
    is_comp: bool
    comp_name: str
    file_path: Path


@dataclass
class PluginInstance:
    name: str
    au_type: str
    au_subtype: str
    au_manufacturer: str
    is_waves: bool
    raw_plist: dict


@dataclass
class LogicProject:
    name: str
    tempo: float
    time_sig_numerator: int
    time_sig_denominator: int
    sample_rate: int
    audio_files: list[AudioFileRef]
    plugins: list[PluginInstance]
    track_names: list[str]
    alternative: int
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_models.py -v`
Expected: PASS (3 tests)

**Step 5: Commit**

```bash
git add logic2ableton/__init__.py logic2ableton/models.py tests/test_models.py
git commit -m "feat: add data models for Logic project, audio refs, and plugin instances"
```

---

### Task 2: Audio Filename Parser

**Files:**
- Modify: `logic2ableton/models.py` (add `parse_audio_filename` function)
- Test: `tests/test_models.py` (add tests)

This function takes a filename like `"KICK IN#02.wav"` or `"scratch vox 2_ Comp A.wav"` and returns track_name, take_number, is_comp, comp_name.

**Step 1: Write the failing tests**

```python
# Add to tests/test_models.py
from logic2ableton.models import parse_audio_filename

def test_parse_take_filename():
    result = parse_audio_filename("KICK IN#01.wav")
    assert result == ("KICK IN", 1, False, "")

def test_parse_take_filename_high_number():
    result = parse_audio_filename("Tyler Amp#03.wav")
    assert result == ("Tyler Amp", 3, False, "")

def test_parse_comp_filename():
    result = parse_audio_filename("scratch vox 2_ Comp A.wav")
    assert result == ("scratch vox 2", 0, True, "Comp A")

def test_parse_simple_filename():
    result = parse_audio_filename("scratch vox 1.wav")
    assert result == ("scratch vox 1", 0, False, "")

def test_parse_bip_filename():
    result = parse_audio_filename("BASS GUITAR_bip.wav")
    assert result == ("BASS GUITAR", 0, False, "")

def test_parse_reference_track():
    result = parse_audio_filename("01 One Headlight.wav")
    assert result == ("01 One Headlight", 0, False, "")

def test_parse_keys_filename():
    result = parse_audio_filename("keys#01.wav")
    assert result == ("keys", 1, False, "")

def test_parse_kick_drum_sc():
    result = parse_audio_filename("Kick Drum (SC)#01.wav")
    assert result == ("Kick Drum (SC)", 1, False, "")
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_models.py -v -k parse`
Expected: FAIL with `ImportError: cannot import name 'parse_audio_filename'`

**Step 3: Write minimal implementation**

```python
# Add to logic2ableton/models.py
import re

def parse_audio_filename(filename: str) -> tuple[str, int, bool, str]:
    """Parse a Logic Pro audio filename into (track_name, take_number, is_comp, comp_name).

    Patterns:
      "KICK IN#01.wav"              -> ("KICK IN", 1, False, "")
      "scratch vox 2_ Comp A.wav"   -> ("scratch vox 2", 0, True, "Comp A")
      "BASS GUITAR_bip.wav"         -> ("BASS GUITAR", 0, False, "")
      "scratch vox 1.wav"           -> ("scratch vox 1", 0, False, "")
    """
    stem = filename.rsplit(".", 1)[0]  # Remove .wav/.aif extension

    # Check for comp pattern: "name_ Comp X" or "name/ Comp X"
    comp_match = re.match(r"^(.+?)[_/]\s*Comp\s+(.+)$", stem)
    if comp_match:
        return (comp_match.group(1).strip(), 0, True, f"Comp {comp_match.group(2).strip()}")

    # Check for take pattern: "name#NN"
    take_match = re.match(r"^(.+?)#(\d+)$", stem)
    if take_match:
        return (take_match.group(1), int(take_match.group(2)), False, "")

    # Check for _bip suffix (bounce-in-place)
    bip_match = re.match(r"^(.+?)_bip$", stem)
    if bip_match:
        return (bip_match.group(1), 0, False, "")

    # Plain filename
    return (stem, 0, False, "")
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_models.py -v -k parse`
Expected: PASS (8 tests)

**Step 5: Commit**

```bash
git add logic2ableton/models.py tests/test_models.py
git commit -m "feat: add audio filename parser for takes, comps, and bip files"
```

---

### Task 3: Logic Pro Metadata Parser (plist files)

**Files:**
- Create: `logic2ableton/logic_parser.py`
- Test: `tests/test_logic_parser.py`

Parses `MetaData.plist` and `ProjectInformation.plist` from a `.logicx` package.

**Step 1: Write the failing tests**

```python
# tests/test_logic_parser.py
from pathlib import Path
from logic2ableton.logic_parser import parse_metadata, parse_project_info

TEST_PROJECT = Path("Might Last Forever.logicx")

def test_parse_project_info():
    info = parse_project_info(TEST_PROJECT)
    assert info["name"] == "Might Last Forever"
    assert "Logic Pro" in info["last_saved_from"]
    assert info["variant_names"]["0"] == "Might Last Forever"

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
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_logic_parser.py -v`
Expected: FAIL with `ModuleNotFoundError`

**Step 3: Write minimal implementation**

```python
# logic2ableton/logic_parser.py
import plistlib
from pathlib import Path


def parse_project_info(logicx_path: Path) -> dict:
    """Parse Resources/ProjectInformation.plist from a .logicx package."""
    plist_path = logicx_path / "Resources" / "ProjectInformation.plist"
    with open(plist_path, "rb") as f:
        data = plistlib.load(f)
    return {
        "name": data.get("VariantNames", {}).get("0", logicx_path.stem),
        "last_saved_from": data.get("LastSavedFrom", ""),
        "variant_names": data.get("VariantNames", {}),
        "active_variant": data.get("ActiveVariant", 0),
        "bundle_version": data.get("BundleVersion", ""),
    }


def parse_metadata(logicx_path: Path, alternative: int = 0) -> dict:
    """Parse Alternatives/{N}/MetaData.plist from a .logicx package."""
    plist_path = logicx_path / "Alternatives" / f"{alternative:03d}" / "MetaData.plist"
    with open(plist_path, "rb") as f:
        data = plistlib.load(f)
    return {
        "tempo": data.get("BeatsPerMinute", 120.0),
        "time_sig_numerator": data.get("SongSignatureNumerator", 4),
        "time_sig_denominator": data.get("SongSignatureDenominator", 4),
        "sample_rate": data.get("SampleRate", 44100),
        "num_tracks": data.get("NumberOfTracks", 0),
        "song_key": data.get("SongKey", ""),
        "song_gender_key": data.get("SongGenderKey", ""),
        "audio_files": [
            f.replace("Audio Files/", "") for f in data.get("AudioFiles", [])
        ],
        "unused_audio_files": [
            f.replace("Audio Files/", "") for f in data.get("UnusedAudioFiles", [])
        ],
    }
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_logic_parser.py -v`
Expected: PASS (2 tests)

**Step 5: Commit**

```bash
git add logic2ableton/logic_parser.py tests/test_logic_parser.py
git commit -m "feat: add Logic Pro plist metadata parser"
```

---

### Task 4: Logic Pro Binary Plugin Extractor

**Files:**
- Modify: `logic2ableton/logic_parser.py` (add `extract_plugins`)
- Test: `tests/test_logic_parser.py` (add tests)

Extracts plugin instances from embedded plists inside ProjectData.

**Step 1: Write the failing tests**

```python
# Add to tests/test_logic_parser.py
from logic2ableton.logic_parser import extract_plugins

def test_extract_plugins_from_project_data():
    plugins = extract_plugins(TEST_PROJECT, alternative=0)
    assert len(plugins) == 24  # 24 embedded plists found in exploration

    # Check that known plugins are found
    subtypes = [p.au_subtype for p in plugins]
    assert "TG5M" in subtypes  # Abbey Road TG Mastering Chain
    assert "76CM" in subtypes  # CLA-76
    assert "L1CM" in subtypes  # L1 Limiter mono

    # Check a specific plugin
    big_guitar = [p for p in plugins if p.name == "Big Guitar 2"]
    assert len(big_guitar) == 1
    assert big_guitar[0].au_manufacturer == "ksWV"
    assert big_guitar[0].is_waves is True

def test_extract_plugins_vintage_vocal():
    plugins = extract_plugins(TEST_PROJECT, alternative=0)
    vintage = [p for p in plugins if p.name == "Vintage Vocal"]
    assert len(vintage) == 1
    assert vintage[0].au_subtype == "TG5M"
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_logic_parser.py -v -k plugin`
Expected: FAIL with `ImportError: cannot import name 'extract_plugins'`

**Step 3: Write minimal implementation**

```python
# Add to logic2ableton/logic_parser.py
import re
import struct
from logic2ableton.models import PluginInstance


def _int_to_4cc(n: int) -> str:
    """Convert a 32-bit integer to a 4-character code (big-endian)."""
    try:
        return struct.pack(">I", n).decode("ascii", errors="replace")
    except (struct.error, ValueError):
        return str(n)


def extract_plugins(logicx_path: Path, alternative: int = 0) -> list[PluginInstance]:
    """Extract plugin instances from embedded plists in ProjectData binary."""
    project_data_path = (
        logicx_path / "Alternatives" / f"{alternative:03d}" / "ProjectData"
    )
    with open(project_data_path, "rb") as f:
        data = f.read()

    plugins = []
    for match in re.finditer(rb"<\?xml version", data):
        start = match.start()
        end_marker = data.find(b"</plist>", start)
        if end_marker == -1:
            continue
        end = end_marker + len(b"</plist>")
        plist_bytes = data[start:end]
        try:
            parsed = plistlib.loads(plist_bytes)
        except Exception:
            continue
        if not isinstance(parsed, dict) or "name" not in parsed:
            continue

        mfr_int = parsed.get("manufacturer", 0)
        subtype_int = parsed.get("subtype", 0)
        type_int = parsed.get("type", 0)

        plugins.append(
            PluginInstance(
                name=parsed["name"],
                au_type=_int_to_4cc(type_int) if isinstance(type_int, int) else str(type_int),
                au_subtype=_int_to_4cc(subtype_int) if isinstance(subtype_int, int) else str(subtype_int),
                au_manufacturer=_int_to_4cc(mfr_int) if isinstance(mfr_int, int) else str(mfr_int),
                is_waves="Waves_XPst" in parsed,
                raw_plist=parsed,
            )
        )
    return plugins
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_logic_parser.py -v -k plugin`
Expected: PASS (2 tests)

**Step 5: Commit**

```bash
git add logic2ableton/logic_parser.py tests/test_logic_parser.py
git commit -m "feat: extract plugin instances from ProjectData embedded plists"
```

---

### Task 5: Logic Pro Audio File Discovery

**Files:**
- Modify: `logic2ableton/logic_parser.py` (add `discover_audio_files`)
- Test: `tests/test_logic_parser.py` (add tests)

Scans `Media/Audio Files/` and parses each filename into an `AudioFileRef`.

**Step 1: Write the failing tests**

```python
# Add to tests/test_logic_parser.py
from logic2ableton.logic_parser import discover_audio_files

def test_discover_audio_files():
    refs = discover_audio_files(TEST_PROJECT)
    assert len(refs) == 38  # 38 audio files in Media/Audio Files/

    # Check grouping by track name
    track_names = sorted(set(r.track_name for r in refs))
    assert "KICK IN" in track_names
    assert "Tyler Amp" in track_names
    assert "scratch vox 2" in track_names
    assert "keys" in track_names

def test_discover_audio_files_takes():
    refs = discover_audio_files(TEST_PROJECT)
    kick_refs = [r for r in refs if r.track_name == "KICK IN"]
    assert len(kick_refs) == 3  # #01, #02, #03
    take_numbers = sorted(r.take_number for r in kick_refs)
    assert take_numbers == [1, 2, 3]

def test_discover_audio_files_comps():
    refs = discover_audio_files(TEST_PROJECT)
    comp_refs = [r for r in refs if r.is_comp]
    assert len(comp_refs) == 1  # scratch vox 2_ Comp A.wav
    assert comp_refs[0].comp_name == "Comp A"
    assert comp_refs[0].track_name == "scratch vox 2"
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_logic_parser.py -v -k discover`
Expected: FAIL with `ImportError`

**Step 3: Write minimal implementation**

```python
# Add to logic2ableton/logic_parser.py
from logic2ableton.models import AudioFileRef, parse_audio_filename


def discover_audio_files(logicx_path: Path) -> list[AudioFileRef]:
    """Discover and parse all audio files in Media/Audio Files/."""
    audio_dir = logicx_path / "Media" / "Audio Files"
    if not audio_dir.exists():
        return []

    refs = []
    for audio_file in sorted(audio_dir.iterdir()):
        if not audio_file.is_file():
            continue
        if not audio_file.suffix.lower() in (".wav", ".aif", ".aiff", ".mp3", ".m4a"):
            continue

        track_name, take_number, is_comp, comp_name = parse_audio_filename(
            audio_file.name
        )
        refs.append(
            AudioFileRef(
                filename=audio_file.name,
                track_name=track_name,
                take_number=take_number,
                is_comp=is_comp,
                comp_name=comp_name,
                file_path=audio_file.resolve(),
            )
        )
    return refs
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_logic_parser.py -v -k discover`
Expected: PASS (3 tests)

**Step 5: Commit**

```bash
git add logic2ableton/logic_parser.py tests/test_logic_parser.py
git commit -m "feat: discover and parse audio files from Logic Pro media folder"
```

---

### Task 6: Full Logic Project Parser

**Files:**
- Modify: `logic2ableton/logic_parser.py` (add `parse_logic_project`)
- Test: `tests/test_logic_parser.py` (add tests)

Combines all parsing functions into a single `parse_logic_project` that returns a `LogicProject`.

**Step 1: Write the failing tests**

```python
# Add to tests/test_logic_parser.py
from logic2ableton.logic_parser import parse_logic_project

def test_parse_logic_project():
    project = parse_logic_project(TEST_PROJECT, alternative=0)
    assert project.name == "Might Last Forever"
    assert project.tempo == 120.0
    assert project.time_sig_numerator == 4
    assert project.time_sig_denominator == 4
    assert project.sample_rate == 44100
    assert len(project.audio_files) == 38
    assert len(project.plugins) == 24
    assert project.alternative == 0

def test_parse_logic_project_track_names():
    project = parse_logic_project(TEST_PROJECT, alternative=0)
    # Track names are derived from unique audio file track names
    assert "KICK IN" in project.track_names
    assert "Tyler Amp" in project.track_names
    assert "SNARE" in project.track_names
    assert "Everett Amp" in project.track_names
    assert "OH L" in project.track_names
    assert "OH R" in project.track_names
    assert "BASS GUITAR" in project.track_names
    assert "keys" in project.track_names
    assert "scratch vox 1" in project.track_names
    assert "scratch vox 2" in project.track_names
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_logic_parser.py -v -k "parse_logic_project"`
Expected: FAIL with `ImportError`

**Step 3: Write minimal implementation**

```python
# Add to logic2ableton/logic_parser.py
from logic2ableton.models import LogicProject


def parse_logic_project(logicx_path: Path, alternative: int = 0) -> LogicProject:
    """Parse a complete .logicx project into a LogicProject dataclass."""
    logicx_path = Path(logicx_path)

    info = parse_project_info(logicx_path)
    meta = parse_metadata(logicx_path, alternative=alternative)
    audio_files = discover_audio_files(logicx_path)
    plugins = extract_plugins(logicx_path, alternative=alternative)

    # Derive unique track names from audio files, preserving discovery order
    seen = set()
    track_names = []
    for ref in audio_files:
        if ref.track_name not in seen:
            seen.add(ref.track_name)
            track_names.append(ref.track_name)

    return LogicProject(
        name=info["name"],
        tempo=meta["tempo"],
        time_sig_numerator=meta["time_sig_numerator"],
        time_sig_denominator=meta["time_sig_denominator"],
        sample_rate=meta["sample_rate"],
        audio_files=audio_files,
        plugins=plugins,
        track_names=track_names,
        alternative=alternative,
    )
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_logic_parser.py -v -k "parse_logic_project"`
Expected: PASS (2 tests)

**Step 5: Commit**

```bash
git add logic2ableton/logic_parser.py tests/test_logic_parser.py
git commit -m "feat: add full Logic project parser combining plist, binary, and audio discovery"
```

---

### Task 7: Plugin Database

**Files:**
- Create: `logic2ableton/plugin_database.py`
- Test: `tests/test_plugin_database.py`

Hardcoded mappings of AU component IDs to human-readable plugin info.

**Step 1: Write the failing tests**

```python
# tests/test_plugin_database.py
from logic2ableton.plugin_database import lookup_au_plugin, PluginInfo

def test_lookup_waves_cla76():
    info = lookup_au_plugin("ksWV", "76CM")
    assert info is not None
    assert info.name == "Waves CLA-76"
    assert info.category == "compressor"

def test_lookup_waves_l1_mono():
    info = lookup_au_plugin("ksWV", "L1CM")
    assert info is not None
    assert "L1" in info.name
    assert info.category == "limiter"

def test_lookup_waves_tg_mastering():
    info = lookup_au_plugin("ksWV", "TG5M")
    assert info is not None
    assert "Abbey Road" in info.name or "TG" in info.name

def test_lookup_unknown_plugin():
    info = lookup_au_plugin("XXXX", "YYYY")
    assert info is None

def test_lookup_logic_channel_eq():
    info = lookup_au_plugin("appl", "bceq")
    assert info is not None
    assert info.category == "eq"
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_plugin_database.py -v`
Expected: FAIL with `ModuleNotFoundError`

**Step 3: Write minimal implementation**

```python
# logic2ableton/plugin_database.py
from dataclasses import dataclass


@dataclass
class PluginInfo:
    name: str
    category: str       # eq, compressor, limiter, de_esser, reverb, delay, etc.
    character: str       # description of the sonic character


# Key: (manufacturer_4cc, subtype_4cc)
AU_PLUGINS: dict[tuple[str, str], PluginInfo] = {
    # === Waves plugins (manufacturer: ksWV) ===
    ("ksWV", "76CM"): PluginInfo("Waves CLA-76", "compressor", "FET compressor"),
    ("ksWV", "76CS"): PluginInfo("Waves CLA-76", "compressor", "FET compressor stereo"),
    ("ksWV", "TG5M"): PluginInfo("Waves Abbey Road TG Mastering Chain", "channel_strip", "vintage console channel strip"),
    ("ksWV", "TG5S"): PluginInfo("Waves Abbey Road TG Mastering Chain", "channel_strip", "vintage console channel strip stereo"),
    ("ksWV", "DSAM"): PluginInfo("Waves De-Esser", "de_esser", "sibilance de-esser"),
    ("ksWV", "DSAS"): PluginInfo("Waves De-Esser", "de_esser", "sibilance de-esser stereo"),
    ("ksWV", "LA2M"): PluginInfo("Waves CLA-2A", "compressor", "opto compressor"),
    ("ksWV", "LA2S"): PluginInfo("Waves CLA-2A", "compressor", "opto compressor stereo"),
    ("ksWV", "LA3M"): PluginInfo("Waves CLA-3A", "compressor", "opto compressor"),
    ("ksWV", "LA3S"): PluginInfo("Waves CLA-3A", "compressor", "opto compressor stereo"),
    ("ksWV", "L1CM"): PluginInfo("Waves L1 Limiter", "limiter", "brickwall peak limiter"),
    ("ksWV", "L1CS"): PluginInfo("Waves L1 Limiter", "limiter", "brickwall peak limiter stereo"),
    ("ksWV", "BSLM"): PluginInfo("Waves Bass Rider", "utility", "bass level rider"),
    ("ksWV", "BSLS"): PluginInfo("Waves Bass Rider", "utility", "bass level rider stereo"),
    ("ksWV", "T37M"): PluginInfo("Waves J37 Tape", "saturation", "tape saturation"),
    ("ksWV", "T37S"): PluginInfo("Waves J37 Tape", "saturation", "tape saturation stereo"),
    ("ksWV", "NIDM"): PluginInfo("Waves Renaissance De-Esser", "de_esser", "renaissance de-esser"),
    ("ksWV", "NIDS"): PluginInfo("Waves Renaissance De-Esser", "de_esser", "renaissance de-esser stereo"),
    ("ksWV", "APCM"): PluginInfo("Waves API 2500", "compressor", "bus compressor"),
    ("ksWV", "APCS"): PluginInfo("Waves API 2500", "compressor", "bus compressor stereo"),
    ("ksWV", "TAPM"): PluginInfo("Waves J37 Tape", "saturation", "tape emulation"),
    ("ksWV", "TAPS"): PluginInfo("Waves J37 Tape", "saturation", "tape emulation stereo"),
    ("ksWV", "PLTM"): PluginInfo("Waves PuigTec EQ", "eq", "tube equalizer"),
    ("ksWV", "PLTS"): PluginInfo("Waves PuigTec EQ", "eq", "tube equalizer stereo"),
    ("ksWV", "RDRM"): PluginInfo("Waves Renaissance De-Esser", "de_esser", "de-esser"),
    ("ksWV", "RDRS"): PluginInfo("Waves Renaissance De-Esser", "de_esser", "de-esser stereo"),

    # === Apple built-in plugins (manufacturer: appl) ===
    ("appl", "bceq"): PluginInfo("Channel EQ", "eq", "parametric EQ"),
    ("appl", "chor"): PluginInfo("Chorus", "modulation", "chorus effect"),
    ("appl", "pdlb"): PluginInfo("Pedalboard", "multi_fx", "guitar effect pedalboard"),
    ("appl", "lmtr"): PluginInfo("Limiter", "limiter", "brickwall limiter"),
    ("appl", "mcmp"): PluginInfo("Multipressor", "compressor", "multiband compressor"),
    ("appl", "cmpr"): PluginInfo("Compressor", "compressor", "general compressor"),
    ("appl", "spdz"): PluginInfo("Space Designer", "reverb", "convolution reverb"),
    ("appl", "chrm"): PluginInfo("ChromaVerb", "reverb", "algorithmic reverb"),
    ("appl", "tdly"): PluginInfo("Tape Delay", "delay", "tape delay"),
    ("appl", "sdly"): PluginInfo("Stereo Delay", "delay", "stereo delay"),
    ("appl", "ngat"): PluginInfo("Noise Gate", "gate", "noise gate"),
    ("appl", "dees"): PluginInfo("DeEsser 2", "de_esser", "de-esser"),
    ("appl", "aufx"): PluginInfo("AUFilter", "eq", "AU filter"),
    ("appl", "flng"): PluginInfo("Flanger", "modulation", "flanger"),
    ("appl", "phsr"): PluginInfo("Phaser", "modulation", "phaser"),
    ("appl", "tmlo"): PluginInfo("Tremolo", "modulation", "tremolo"),
}


def lookup_au_plugin(manufacturer: str, subtype: str) -> PluginInfo | None:
    """Look up a plugin by its AU manufacturer and subtype 4CC codes."""
    return AU_PLUGINS.get((manufacturer, subtype))
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_plugin_database.py -v`
Expected: PASS (5 tests)

**Step 5: Commit**

```bash
git add logic2ableton/plugin_database.py tests/test_plugin_database.py
git commit -m "feat: add AU plugin database with Waves and Apple built-in mappings"
```

---

### Task 8: VST3 Scanner

**Files:**
- Create: `logic2ableton/vst3_scanner.py`
- Test: `tests/test_vst3_scanner.py`

Scans the local VST3 directory and categorizes plugins.

**Step 1: Write the failing tests**

```python
# tests/test_vst3_scanner.py
from pathlib import Path
from logic2ableton.vst3_scanner import scan_vst3_plugins, VST3Plugin

# Use real VST3 path on this machine
VST3_PATH = Path("C:/Program Files/Common Files/VST3")

def test_scan_finds_plugins():
    plugins = scan_vst3_plugins(VST3_PATH)
    assert len(plugins) > 50  # We know there are 159 entries

def test_scan_categorizes_fabfilter():
    plugins = scan_vst3_plugins(VST3_PATH)
    fabfilter = [p for p in plugins if "FabFilter" in p.name]
    # FabFilter exists as a directory in VST3
    assert len(fabfilter) >= 1

def test_scan_categorizes_valhalla():
    plugins = scan_vst3_plugins(VST3_PATH)
    valhalla = [p for p in plugins if "Valhalla" in p.name or "ValhallaDSP" in p.name]
    assert len(valhalla) >= 1

def test_scan_empty_dir(tmp_path):
    plugins = scan_vst3_plugins(tmp_path)
    assert plugins == []
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_vst3_scanner.py -v`
Expected: FAIL with `ModuleNotFoundError`

**Step 3: Write minimal implementation**

```python
# logic2ableton/vst3_scanner.py
from dataclasses import dataclass
from pathlib import Path


@dataclass
class VST3Plugin:
    name: str
    path: Path
    category: str  # eq, compressor, limiter, reverb, delay, synth, etc.


# Category detection patterns (name substring -> category)
CATEGORY_PATTERNS: list[tuple[str, str]] = [
    # EQ
    ("Pro-Q", "eq"), ("EQ", "eq"), ("equaliz", "eq"),
    # Compressors
    ("Pro-C", "compressor"), ("Compressor", "compressor"), ("Comp", "compressor"),
    ("FET", "compressor"), ("LA-2A", "compressor"), ("1176", "compressor"),
    # Limiters
    ("Pro-L", "limiter"), ("Limiter", "limiter"), ("L1", "limiter"),
    ("L2", "limiter"), ("Maximizer", "limiter"),
    # De-essers
    ("Pro-DS", "de_esser"), ("De-Ess", "de_esser"), ("DeEss", "de_esser"),
    ("Sibilance", "de_esser"),
    # Reverb
    ("Reverb", "reverb"), ("Verb", "reverb"), ("Room", "reverb"),
    ("Hall", "reverb"), ("Plate", "reverb"), ("Valhalla", "reverb"),
    # Delay
    ("Delay", "delay"), ("Echo", "delay"),
    # Saturation / Distortion
    ("Drive", "saturation"), ("Tape", "saturation"), ("Saturat", "saturation"),
    ("Distort", "saturation"), ("Fuzz", "saturation"), ("Overdrive", "saturation"),
    # Modulation
    ("Chorus", "modulation"), ("Flanger", "modulation"), ("Phaser", "modulation"),
    # Channel strips / multi-fx
    ("Channel", "channel_strip"), ("Strip", "channel_strip"),
    # Synths / Instruments
    ("Synth", "synth"), ("Piano", "synth"), ("Keys", "synth"),
    ("Organ", "synth"), ("Serum", "synth"),
    # Utility
    ("Meter", "utility"), ("Analyzer", "utility"), ("Monitor", "utility"),
    ("Gain", "utility"), ("Rider", "utility"),
    # Noise reduction
    ("Denoise", "noise_reduction"), ("DeNoise", "noise_reduction"),
    ("Noise", "noise_reduction"),
]


def _categorize_plugin(name: str) -> str:
    """Guess a plugin's category from its name."""
    for pattern, category in CATEGORY_PATTERNS:
        if pattern.lower() in name.lower():
            return category
    return "unknown"


def scan_vst3_plugins(vst3_path: Path) -> list[VST3Plugin]:
    """Scan a VST3 directory and return categorized plugin list."""
    if not vst3_path.exists():
        return []

    plugins = []
    for entry in sorted(vst3_path.iterdir()):
        name = entry.stem  # Strip .vst3 extension if present
        plugins.append(
            VST3Plugin(
                name=name,
                path=entry,
                category=_categorize_plugin(name),
            )
        )
    return plugins
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_vst3_scanner.py -v`
Expected: PASS (4 tests)

**Step 5: Commit**

```bash
git add logic2ableton/vst3_scanner.py tests/test_vst3_scanner.py
git commit -m "feat: add VST3 directory scanner with category detection"
```

---

### Task 9: Plugin Matcher

**Files:**
- Create: `logic2ableton/plugin_matcher.py`
- Test: `tests/test_plugin_matcher.py`

Matches Logic Pro plugins to available VST3 replacements.

**Step 1: Write the failing tests**

```python
# tests/test_plugin_matcher.py
from pathlib import Path
from logic2ableton.plugin_matcher import match_plugins, PluginMatch
from logic2ableton.models import PluginInstance

VST3_PATH = Path("C:/Program Files/Common Files/VST3")

def test_match_known_compressor():
    plugins = [
        PluginInstance(
            name="Guitar",
            au_type="aufx",
            au_subtype="76CM",
            au_manufacturer="ksWV",
            is_waves=True,
            raw_plist={},
        )
    ]
    matches = match_plugins(plugins, VST3_PATH)
    assert len(matches) == 1
    assert matches[0].logic_plugin_name == "Waves CLA-76"
    assert matches[0].preset_name == "Guitar"
    assert matches[0].category == "compressor"
    assert len(matches[0].suggested_vst3s) > 0

def test_match_unknown_plugin():
    plugins = [
        PluginInstance(
            name="Mystery",
            au_type="aufx",
            au_subtype="XXXX",
            au_manufacturer="YYYY",
            is_waves=False,
            raw_plist={},
        )
    ]
    matches = match_plugins(plugins, VST3_PATH)
    assert len(matches) == 1
    assert matches[0].logic_plugin_name == "Unknown (YYYY/XXXX)"
    assert matches[0].category == "unknown"

def test_match_returns_all_plugins():
    plugins = [
        PluginInstance("Big Guitar", "aufx", "TG5M", "ksWV", True, {}),
        PluginInstance("Untitled", "aufx", "L1CM", "ksWV", True, {}),
    ]
    matches = match_plugins(plugins, VST3_PATH)
    assert len(matches) == 2
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_plugin_matcher.py -v`
Expected: FAIL with `ModuleNotFoundError`

**Step 3: Write minimal implementation**

```python
# logic2ableton/plugin_matcher.py
from dataclasses import dataclass, field
from pathlib import Path
from logic2ableton.models import PluginInstance
from logic2ableton.plugin_database import lookup_au_plugin
from logic2ableton.vst3_scanner import scan_vst3_plugins, VST3Plugin


@dataclass
class PluginMatch:
    logic_plugin_name: str
    preset_name: str
    category: str
    character: str
    suggested_vst3s: list[str] = field(default_factory=list)


def match_plugins(
    plugins: list[PluginInstance], vst3_path: Path
) -> list[PluginMatch]:
    """Match Logic Pro plugins to suggested VST3 replacements."""
    vst3_plugins = scan_vst3_plugins(vst3_path)
    # Group VST3s by category
    vst3_by_category: dict[str, list[str]] = {}
    for vst3 in vst3_plugins:
        vst3_by_category.setdefault(vst3.category, []).append(vst3.name)

    matches = []
    for plugin in plugins:
        info = lookup_au_plugin(plugin.au_manufacturer, plugin.au_subtype)
        if info:
            logic_name = info.name
            category = info.category
            character = info.character
        else:
            logic_name = f"Unknown ({plugin.au_manufacturer}/{plugin.au_subtype})"
            category = "unknown"
            character = ""

        suggested = vst3_by_category.get(category, [])[:5]

        matches.append(
            PluginMatch(
                logic_plugin_name=logic_name,
                preset_name=plugin.name,
                category=category,
                character=character,
                suggested_vst3s=suggested,
            )
        )
    return matches
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_plugin_matcher.py -v`
Expected: PASS (3 tests)

**Step 5: Commit**

```bash
git add logic2ableton/plugin_matcher.py tests/test_plugin_matcher.py
git commit -m "feat: add plugin matcher with category-based VST3 suggestions"
```

---

### Task 10: Ableton Live Set Generator

**Files:**
- Create: `logic2ableton/ableton_generator.py`
- Test: `tests/test_ableton_generator.py`

Generates a valid Ableton Live 12 `.als` file from a `LogicProject`.

**Step 1: Write the failing tests**

```python
# tests/test_ableton_generator.py
import gzip
import xml.etree.ElementTree as ET
from pathlib import Path
from logic2ableton.ableton_generator import generate_als
from logic2ableton.logic_parser import parse_logic_project

TEST_PROJECT = Path("Might Last Forever.logicx")

def test_generate_als_creates_file(tmp_path):
    project = parse_logic_project(TEST_PROJECT)
    output_dir = tmp_path / "output"
    als_path = generate_als(project, output_dir, copy_audio=False)

    assert als_path.exists()
    assert als_path.suffix == ".als"
    assert als_path.name == "Might Last Forever.als"

def test_generate_als_is_valid_gzipped_xml(tmp_path):
    project = parse_logic_project(TEST_PROJECT)
    als_path = generate_als(project, tmp_path / "output", copy_audio=False)

    with gzip.open(als_path, "rb") as f:
        xml_content = f.read().decode("utf-8")

    # Should parse as valid XML
    root = ET.fromstring(xml_content)
    assert root.tag == "Ableton"

def test_generate_als_has_correct_tracks(tmp_path):
    project = parse_logic_project(TEST_PROJECT)
    als_path = generate_als(project, tmp_path / "output", copy_audio=False)

    with gzip.open(als_path, "rb") as f:
        root = ET.fromstring(f.read())

    tracks = root.find(".//Tracks")
    audio_tracks = tracks.findall("AudioTrack")
    # Should have one track per unique track name
    assert len(audio_tracks) == len(project.track_names)

    # Check track names
    names = []
    for track in audio_tracks:
        name_elem = track.find(".//Name/EffectiveName")
        names.append(name_elem.get("Value"))
    for tn in project.track_names:
        assert tn in names

def test_generate_als_has_correct_tempo(tmp_path):
    project = parse_logic_project(TEST_PROJECT)
    als_path = generate_als(project, tmp_path / "output", copy_audio=False)

    with gzip.open(als_path, "rb") as f:
        root = ET.fromstring(f.read())

    tempo = root.find(".//Tempo/Manual")
    assert tempo.get("Value") == "120"

def test_generate_als_copies_audio(tmp_path):
    project = parse_logic_project(TEST_PROJECT)
    output_dir = tmp_path / "output"
    als_path = generate_als(project, output_dir, copy_audio=True)

    samples_dir = output_dir / "Might Last Forever Project" / "Samples" / "Imported"
    assert samples_dir.exists()
    wav_files = list(samples_dir.glob("*.wav"))
    assert len(wav_files) == len(project.audio_files)
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_ableton_generator.py -v`
Expected: FAIL with `ModuleNotFoundError`

**Step 3: Write minimal implementation**

```python
# logic2ableton/ableton_generator.py
import gzip
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path
from logic2ableton.models import LogicProject


def _make_audio_track_xml(track_id: int, name: str, color: int) -> ET.Element:
    """Create a minimal AudioTrack XML element."""
    track = ET.SubElement(ET.Element("dummy"), "AudioTrack")
    track.set("Id", str(track_id))

    _val(track, "LomId", "0")
    _val(track, "LomIdView", "0")
    _val(track, "IsContentSelectedInDocument", "false")
    _val(track, "PreferredContentViewMode", "0")

    delay = ET.SubElement(track, "TrackDelay")
    _val(delay, "Value", "0")
    _val(delay, "IsValueSampleBased", "false")

    name_elem = ET.SubElement(track, "Name")
    _val(name_elem, "EffectiveName", name)
    _val(name_elem, "UserName", name)
    _val(name_elem, "Annotation", "")
    _val(name_elem, "MemorizedFirstClipName", "")

    _val(track, "Color", str(color))

    envelopes = ET.SubElement(track, "AutomationEnvelopes")
    ET.SubElement(envelopes, "Envelopes")

    _val(track, "TrackGroupId", "-1")
    _val(track, "TrackUnfolded", "true")
    ET.SubElement(track, "DevicesListWrapper").set("LomId", "0")
    ET.SubElement(track, "ClipSlotsListWrapper").set("LomId", "0")
    ET.SubElement(track, "ArrangementClipsListWrapper").set("LomId", "0")
    ET.SubElement(track, "TakeLanesListWrapper").set("LomId", "0")
    _val(track, "ViewData", "{}")

    take_lanes_wrapper = ET.SubElement(track, "TakeLanes")
    ET.SubElement(take_lanes_wrapper, "TakeLanes")
    _val(take_lanes_wrapper, "AreTakeLanesFolded", "true")

    _val(track, "LinkedTrackGroupId", "-1")
    _val(track, "SavedPlayingSlot", "-1")
    _val(track, "SavedPlayingOffset", "0")
    _val(track, "Freeze", "false")
    _val(track, "NeedArrangerRefreeze", "true")
    _val(track, "PostProcessFreezeClips", "0")

    # DeviceChain with routing
    device_chain = ET.SubElement(track, "DeviceChain")

    auto_lanes = ET.SubElement(device_chain, "AutomationLanes")
    auto_lanes_inner = ET.SubElement(auto_lanes, "AutomationLanes")
    lane = ET.SubElement(auto_lanes_inner, "AutomationLane")
    lane.set("Id", "0")
    _val(lane, "SelectedDevice", "0")
    _val(lane, "SelectedEnvelope", "0")
    _val(lane, "IsContentSelectedInDocument", "false")
    _val(lane, "LaneHeight", "68")
    _val(auto_lanes, "AreAdditionalAutomationLanesFolded", "false")

    clip_env = ET.SubElement(device_chain, "ClipEnvelopeChooserViewState")
    _val(clip_env, "SelectedDevice", "0")
    _val(clip_env, "SelectedEnvelope", "0")
    _val(clip_env, "PreferModulationVisible", "false")

    _routing(device_chain, "AudioInputRouting", "AudioIn/External/S0", "Ext. In", "1/2")
    _routing(device_chain, "MidiInputRouting", "MidiIn/External.All/-1", "Ext: All Ins", "")
    _routing(device_chain, "AudioOutputRouting", "AudioOut/Main", "Master", "")
    _routing(device_chain, "MidiOutputRouting", "MidiOut/None", "None", "")

    # Mixer
    mixer = ET.SubElement(device_chain, "Mixer")
    _val(mixer, "LomId", "0")
    _val(mixer, "LomIdView", "0")
    _val(mixer, "IsExpanded", "true")

    # Volume
    volume = ET.SubElement(mixer, "Volume")
    _val(volume, "LomId", "0")
    _val(volume, "Manual", "1")

    # Pan
    pan = ET.SubElement(mixer, "Pan")
    _val(pan, "LomId", "0")
    _val(pan, "Manual", "0")

    # Sends list (empty)
    ET.SubElement(mixer, "Sends")

    # Speaker (on/off)
    speaker = ET.SubElement(mixer, "Speaker")
    _val(speaker, "LomId", "0")
    _val(speaker, "Manual", "true")

    # Solo/Mute
    _val(mixer, "SoloSink", "false")

    # MainSink (device chain output)
    main_sink = ET.SubElement(device_chain, "MainSequencer")
    clip_slot_list = ET.SubElement(main_sink, "ClipSlotList")

    # Arrangement clip slots (empty — v1 places no clips programmatically)
    # Users will drag audio in from the Samples folder

    return track


def _val(parent: ET.Element, tag: str, value: str) -> ET.Element:
    """Create a <Tag Value="value"/> element."""
    elem = ET.SubElement(parent, tag)
    elem.set("Value", value)
    return elem


def _routing(parent: ET.Element, tag: str, target: str, upper: str, lower: str):
    """Create a routing element."""
    elem = ET.SubElement(parent, tag)
    _val(elem, "Target", target)
    _val(elem, "UpperDisplayString", upper)
    _val(elem, "LowerDisplayString", lower)
    mpe = ET.SubElement(elem, "MpeSettings")
    _val(mpe, "ZoneType", "0")
    _val(mpe, "FirstNoteChannel", "1")
    _val(mpe, "LastNoteChannel", "15")


# Track color palette (Ableton color indices)
TRACK_COLORS = [0, 1, 2, 3, 5, 6, 7, 9, 10, 11, 13, 14, 15, 17, 18, 19, 20, 22, 25, 26]


def generate_als(
    project: LogicProject,
    output_dir: Path,
    copy_audio: bool = True,
) -> Path:
    """Generate an Ableton Live .als file from a LogicProject.

    Returns the path to the created .als file.
    """
    project_folder = output_dir / f"{project.name} Project"
    project_folder.mkdir(parents=True, exist_ok=True)

    # Copy audio files
    if copy_audio:
        samples_dir = project_folder / "Samples" / "Imported"
        samples_dir.mkdir(parents=True, exist_ok=True)
        for ref in project.audio_files:
            dest = samples_dir / ref.filename
            if not dest.exists() and ref.file_path.exists():
                shutil.copy2(ref.file_path, dest)

    # Build XML
    root = ET.Element("Ableton")
    root.set("MajorVersion", "5")
    root.set("MinorVersion", "12.0_12300")
    root.set("SchemaChangeCount", "1")
    root.set("Creator", "logic2ableton converter")
    root.set("Revision", "")

    live_set = ET.SubElement(root, "LiveSet")
    _val(live_set, "NextPointeeId", "10000")
    _val(live_set, "OverwriteProtectionNumber", "2819")
    _val(live_set, "LomId", "0")
    _val(live_set, "LomIdView", "0")

    # Tracks
    tracks_elem = ET.SubElement(live_set, "Tracks")
    for i, track_name in enumerate(project.track_names):
        track = _make_audio_track_xml(
            track_id=i + 100,
            name=track_name,
            color=TRACK_COLORS[i % len(TRACK_COLORS)],
        )
        tracks_elem.append(track)

    # Master Track (minimal)
    main_track = ET.SubElement(live_set, "MainTrack")
    _val(main_track, "LomId", "0")

    # Transport
    transport = ET.SubElement(live_set, "Transport")
    tempo_elem = ET.SubElement(transport, "Tempo")
    _val(tempo_elem, "LomId", "0")
    _val(tempo_elem, "Manual", str(int(project.tempo)))
    min_max = ET.SubElement(tempo_elem, "MidiControllerRange")
    _val(min_max, "Min", "60")
    _val(min_max, "Max", "200")

    time_sigs = ET.SubElement(transport, "TimeSignatures")
    ts = ET.SubElement(time_sigs, "RemoteableTimeSignature")
    ts.set("Id", "0")
    _val(ts, "Numerator", str(project.time_sig_numerator))
    _val(ts, "Denominator", str(project.time_sig_denominator))
    _val(ts, "Time", "0")

    # Write gzipped XML
    als_path = project_folder / f"{project.name}.als"
    xml_bytes = ET.tostring(root, encoding="unicode", xml_declaration=True).encode(
        "utf-8"
    )
    with gzip.open(als_path, "wb") as f:
        f.write(xml_bytes)

    return als_path
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_ableton_generator.py -v`
Expected: PASS (5 tests)

**Step 5: Commit**

```bash
git add logic2ableton/ableton_generator.py tests/test_ableton_generator.py
git commit -m "feat: add Ableton .als generator with track structure and audio copying"
```

---

### Task 11: Conversion Report Generator

**Files:**
- Create: `logic2ableton/report.py`
- Test: `tests/test_report.py`

Generates the human-readable conversion report.

**Step 1: Write the failing test**

```python
# tests/test_report.py
from pathlib import Path
from logic2ableton.report import generate_report
from logic2ableton.logic_parser import parse_logic_project
from logic2ableton.plugin_matcher import match_plugins

TEST_PROJECT = Path("Might Last Forever.logicx")
VST3_PATH = Path("C:/Program Files/Common Files/VST3")

def test_generate_report():
    project = parse_logic_project(TEST_PROJECT)
    matches = match_plugins(project.plugins, VST3_PATH)
    report = generate_report(project, matches)

    assert "Might Last Forever" in report
    assert "120" in report  # tempo
    assert "KICK IN" in report
    assert "Tyler Amp" in report
    assert "PLUGINS FOUND" in report
    assert "NOT TRANSFERRED" in report

def test_report_contains_track_count():
    project = parse_logic_project(TEST_PROJECT)
    matches = match_plugins(project.plugins, VST3_PATH)
    report = generate_report(project, matches)
    assert "TRACKS TRANSFERRED" in report
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_report.py -v`
Expected: FAIL with `ModuleNotFoundError`

**Step 3: Write minimal implementation**

```python
# logic2ableton/report.py
from logic2ableton.models import LogicProject
from logic2ableton.plugin_matcher import PluginMatch


def generate_report(project: LogicProject, plugin_matches: list[PluginMatch]) -> str:
    """Generate a human-readable conversion report."""
    lines = []
    lines.append("=" * 60)
    lines.append("  Logic Pro to Ableton Conversion Report")
    lines.append("=" * 60)
    lines.append(f"Project: {project.name}")
    lines.append(
        f"Tempo: {project.tempo} BPM | "
        f"Time Sig: {project.time_sig_numerator}/{project.time_sig_denominator} | "
        f"Sample Rate: {project.sample_rate}"
    )
    lines.append("")

    # Tracks
    lines.append(f"TRACKS TRANSFERRED ({len(project.track_names)}):")
    for i, track_name in enumerate(project.track_names, 1):
        # Count takes for this track
        takes = [r for r in project.audio_files if r.track_name == track_name and r.take_number > 0]
        comps = [r for r in project.audio_files if r.track_name == track_name and r.is_comp]
        plain = [r for r in project.audio_files if r.track_name == track_name and r.take_number == 0 and not r.is_comp]

        parts = []
        if takes:
            parts.append(f"{len(takes)} takes")
        if comps:
            comp_names = ", ".join(c.comp_name for c in comps)
            parts.append(f"comp: {comp_names}")
        if plain and not takes:
            parts.append(f"{len(plain)} file(s)")

        detail = " — " + ", ".join(parts) if parts else ""
        lines.append(f"  {i}. {track_name}{detail}")

    lines.append("")

    # Plugins
    lines.append(f"PLUGINS FOUND ({len(plugin_matches)}):")
    for match in plugin_matches:
        preset = f' "{match.preset_name}"' if match.preset_name and match.preset_name != "#default" else ""
        suggestions = ", ".join(match.suggested_vst3s[:3]) if match.suggested_vst3s else "(no match found)"
        lines.append(f"  {match.logic_plugin_name}{preset}")
        lines.append(f"    -> Suggested: {suggestions}")

    lines.append("")

    # Not transferred
    lines.append("NOT TRANSFERRED:")
    lines.append("  - Plugin settings/parameters (not compatible across DAWs)")
    lines.append("  - Automation data (requires deeper binary parsing)")
    lines.append("  - Exact region positions (clips placed at bar 1; manual arrangement needed)")
    lines.append("  - Bus/send routing (recreate manually in Ableton)")
    lines.append("")
    lines.append("=" * 60)

    return "\n".join(lines)
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_report.py -v`
Expected: PASS (2 tests)

**Step 5: Commit**

```bash
git add logic2ableton/report.py tests/test_report.py
git commit -m "feat: add conversion report generator"
```

---

### Task 12: CLI Entry Point

**Files:**
- Create: `logic2ableton/cli.py`
- Test: `tests/test_cli.py`

The `argparse`-based CLI that ties everything together.

**Step 1: Write the failing tests**

```python
# tests/test_cli.py
import subprocess
import sys
from pathlib import Path

TEST_PROJECT = Path("Might Last Forever.logicx")

def test_cli_report_only(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "logic2ableton.cli", str(TEST_PROJECT), "--report-only"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "Might Last Forever" in result.stdout
    assert "TRACKS TRANSFERRED" in result.stdout
    assert "PLUGINS FOUND" in result.stdout

def test_cli_full_conversion(tmp_path):
    output_dir = str(tmp_path / "output")
    result = subprocess.run(
        [sys.executable, "-m", "logic2ableton.cli", str(TEST_PROJECT), "--output", output_dir],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    als_path = tmp_path / "output" / "Might Last Forever Project" / "Might Last Forever.als"
    assert als_path.exists()
    report_path = tmp_path / "output" / "Might Last Forever_conversion_report.txt"
    assert report_path.exists()

def test_cli_no_args():
    result = subprocess.run(
        [sys.executable, "-m", "logic2ableton.cli"],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_cli.py -v`
Expected: FAIL with module not found

**Step 3: Write minimal implementation**

```python
# logic2ableton/cli.py
"""Logic Pro to Ableton Live converter CLI."""
import argparse
import sys
from pathlib import Path

from logic2ableton.logic_parser import parse_logic_project
from logic2ableton.plugin_matcher import match_plugins
from logic2ableton.ableton_generator import generate_als
from logic2ableton.report import generate_report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Convert Logic Pro .logicx projects to Ableton Live .als files"
    )
    parser.add_argument("input", help="Path to .logicx project")
    parser.add_argument("--output", "-o", default=".", help="Output directory (default: current dir)")
    parser.add_argument("--alternative", "-a", type=int, default=0, help="Logic alternative to convert (default: 0)")
    parser.add_argument("--no-copy", action="store_true", help="Don't copy audio files")
    parser.add_argument("--report-only", action="store_true", help="Only print report, don't generate .als")
    parser.add_argument(
        "--vst3-path",
        default="C:/Program Files/Common Files/VST3",
        help="VST3 plugin directory",
    )

    args = parser.parse_args(argv)
    logicx_path = Path(args.input)

    if not logicx_path.exists():
        print(f"Error: {logicx_path} not found", file=sys.stderr)
        return 1
    if not logicx_path.is_dir():
        print(f"Error: {logicx_path} is not a .logicx package", file=sys.stderr)
        return 1

    # Parse
    print(f"Parsing {logicx_path.name}...")
    project = parse_logic_project(logicx_path, alternative=args.alternative)
    print(f"  Found {len(project.track_names)} tracks, {len(project.audio_files)} audio files, {len(project.plugins)} plugins")

    # Match plugins
    vst3_path = Path(args.vst3_path)
    plugin_matches = match_plugins(project.plugins, vst3_path)

    # Report
    report = generate_report(project, plugin_matches)
    print(report)

    if args.report_only:
        return 0

    # Generate
    output_dir = Path(args.output)
    print(f"\nGenerating Ableton project in {output_dir}...")
    als_path = generate_als(project, output_dir, copy_audio=not args.no_copy)
    print(f"  Created: {als_path}")

    # Save report
    report_path = output_dir / f"{project.name}_conversion_report.txt"
    report_path.write_text(report, encoding="utf-8")
    print(f"  Report: {report_path}")

    if not args.no_copy:
        samples_dir = output_dir / f"{project.name} Project" / "Samples" / "Imported"
        print(f"  Samples: {samples_dir}")

    print("\nDone!")
    return 0


if __name__ == "__main__":
    sys.exit(main())


# logic2ableton/__main__.py  (allows `python -m logic2ableton`)
# NOTE: Also create logic2ableton/__main__.py with:
#   from logic2ableton.cli import main
#   import sys
#   sys.exit(main())
```

Also create:

```python
# logic2ableton/__main__.py
from logic2ableton.cli import main
import sys

sys.exit(main())
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_cli.py -v`
Expected: PASS (3 tests)

**Step 5: Commit**

```bash
git add logic2ableton/cli.py logic2ableton/__main__.py tests/test_cli.py
git commit -m "feat: add CLI entry point tying all modules together"
```

---

### Task 13: Integration Test — End-to-End Conversion

**Files:**
- Create: `tests/test_integration.py`

Full end-to-end test with the real project.

**Step 1: Write the integration test**

```python
# tests/test_integration.py
import gzip
import xml.etree.ElementTree as ET
from pathlib import Path
from logic2ableton.logic_parser import parse_logic_project
from logic2ableton.plugin_matcher import match_plugins
from logic2ableton.ableton_generator import generate_als
from logic2ableton.report import generate_report

TEST_PROJECT = Path("Might Last Forever.logicx")
VST3_PATH = Path("C:/Program Files/Common Files/VST3")

def test_end_to_end_conversion(tmp_path):
    """Full pipeline: parse Logic -> match plugins -> generate Ableton -> verify."""
    # Parse
    project = parse_logic_project(TEST_PROJECT)
    assert project.name == "Might Last Forever"
    assert project.tempo == 120.0

    # Match plugins
    matches = match_plugins(project.plugins, VST3_PATH)
    assert len(matches) == len(project.plugins)

    # Generate .als with audio copy
    output_dir = tmp_path / "output"
    als_path = generate_als(project, output_dir, copy_audio=True)
    assert als_path.exists()

    # Verify .als structure
    with gzip.open(als_path, "rb") as f:
        root = ET.fromstring(f.read())
    assert root.tag == "Ableton"
    tracks = root.findall(".//Tracks/AudioTrack")
    assert len(tracks) == len(project.track_names)

    # Verify audio files copied
    samples_dir = output_dir / "Might Last Forever Project" / "Samples" / "Imported"
    assert samples_dir.exists()
    copied_files = list(samples_dir.glob("*.wav"))
    assert len(copied_files) == len(project.audio_files)

    # Generate and verify report
    report = generate_report(project, matches)
    assert "Might Last Forever" in report
    assert "KICK IN" in report
    assert "PLUGINS FOUND" in report
    assert len(report) > 500  # Report should be substantial
```

**Step 2: Run test**

Run: `python -m pytest tests/test_integration.py -v`
Expected: PASS

**Step 3: Commit**

```bash
git add tests/test_integration.py
git commit -m "test: add end-to-end integration test with real Logic project"
```

---

### Task 14: Manual Verification

**Step 1: Run the full CLI conversion**

```bash
python -m logic2ableton.cli "Might Last Forever.logicx" --output ./test_output
```

Expected: Creates `test_output/Might Last Forever Project/Might Last Forever.als` and copies all 38 audio files.

**Step 2: Inspect the generated .als**

```bash
python -c "
import gzip
with gzip.open('test_output/Might Last Forever Project/Might Last Forever.als', 'rb') as f:
    print(f.read().decode('utf-8')[:3000])
"
```

Verify the XML looks correct with proper tracks.

**Step 3: Run report-only mode**

```bash
python -m logic2ableton.cli "Might Last Forever.logicx" --report-only
```

Expected: Prints the conversion report to stdout without creating files.

**Step 4: Run all tests**

```bash
python -m pytest tests/ -v
```

Expected: All tests pass.

**Step 5: Final commit**

```bash
git add -A
git commit -m "feat: Logic Pro to Ableton converter v1 — complete"
```

---

### Summary

| Task | Module | What it does |
|------|--------|-------------|
| 1 | `models.py` | Data classes: AudioFileRef, PluginInstance, LogicProject |
| 2 | `models.py` | Audio filename parser (takes, comps, bip) |
| 3 | `logic_parser.py` | Plist metadata parser (tempo, time sig, sample rate) |
| 4 | `logic_parser.py` | Binary plugin extractor (embedded plists in ProjectData) |
| 5 | `logic_parser.py` | Audio file discovery from Media folder |
| 6 | `logic_parser.py` | Full project parser combining all sources |
| 7 | `plugin_database.py` | AU component ID → plugin name/category mappings |
| 8 | `vst3_scanner.py` | Local VST3 directory scanner with categorization |
| 9 | `plugin_matcher.py` | Category-based plugin matching engine |
| 10 | `ableton_generator.py` | Ableton .als XML generator with track structure |
| 11 | `report.py` | Human-readable conversion report |
| 12 | `cli.py` | CLI entry point with argparse |
| 13 | Integration test | End-to-end pipeline test |
| 14 | Manual verification | CLI smoke test with real project |
