# Logic Pro to Ableton Live Converter — Design Document

**Date**: 2026-02-15
**Status**: Approved
**Language**: Python (CLI)
**Approach**: Audio-First Hybrid (reliable extraction now, incremental binary parsing later)

---

## Problem

Logic Pro and Ableton Live use incompatible project formats. There is no tool that converts a Logic Pro `.logicx` project into an Ableton `.als` Live Set while preserving audio stems, track structure, and providing plugin replacement suggestions.

## Key Technical Findings

### Logic Pro `.logicx` Format
- **Package structure**: macOS bundle containing `Alternatives/NNN/ProjectData` (proprietary binary), `MetaData.plist`, `Media/Audio Files/`
- **ProjectData binary**: Undocumented format with a `gnoS` (Song) header tag. Contains:
  - Audio file references encoded in UTF-16LE, marked with `AUFL` tags
  - Embedded Apple plist XML sections for each plugin instance (fully parseable)
  - Track names as ASCII strings in a track definition region
  - Takes identified by `#01`, `#02`, `#03` filename suffixes
  - Comps identified by "Comp A/B/C" naming patterns
- **MetaData.plist**: Binary plist with tempo, time signature, sample rate, audio file inventory, track count

### Plugin Data (from embedded plists)
Each plugin instance contains:
- `name`: Preset name (e.g., "Big Guitar 2", "Vintage Vocal")
- `type`: AU component type (`aufx` = effect, `aumu` = instrument, `aumf` = MIDI effect)
- `subtype`: 4-char plugin identifier (e.g., `76CM` = Waves CLA-76)
- `manufacturer`: 4-char code (e.g., `ksWV` = Waves)
- `Waves_XPst`: Waves-specific preset state data (binary)
- `data`: Generic AU state data (binary)

### Ableton `.als` Format
- Gzipped XML file using a well-documented schema
- Tracks: `<AudioTrack>`, `<MidiTrack>`, `<ReturnTrack>` within `<Tracks>`
- Audio clips reference files via `<SampleRef>` with relative paths
- Take lanes supported in Ableton 12+ via `<TakeLanes>`
- Multiple open-source parsers/generators available

### User's VST3 Library
159 plugins in `C:\Program Files\Common Files\VST3\` including FabFilter, Waves, Valhalla, iZotope, Native Instruments, Soundtheory, Arturia, and many others.

---

## Architecture

```
logic2ableton/
├── logic2ableton.py       # CLI entry point
├── logic_parser.py        # Logic Pro .logicx parser
├── plugin_matcher.py      # Plugin identification + VST3 matching
├── ableton_generator.py   # .als XML generator
├── plugin_database.py     # Known AU component type/subtype mappings
└── vst3_scanner.py        # Local VST3 inventory scanner
```

## Module Design

### 1. Logic Parser (`logic_parser.py`)

**Input**: Path to `.logicx` package
**Output**: `LogicProject` dataclass containing:

```python
@dataclass
class AudioFileRef:
    filename: str           # e.g., "KICK IN#01.wav"
    track_name: str         # e.g., "KICK IN" (stripped of take suffix)
    take_number: int        # e.g., 1 (from #01)
    is_comp: bool           # True if this is a comp bounce
    comp_name: str          # e.g., "Comp A"
    file_path: Path         # Absolute path to the audio file in Media/

@dataclass
class PluginInstance:
    name: str               # Preset name
    au_type: str            # 4CC type (aufx, aumu, etc.)
    au_subtype: str         # 4CC subtype (76CM, TG5M, etc.)
    au_manufacturer: str    # 4CC manufacturer (ksWV, appl, etc.)
    is_waves: bool          # Has Waves_XPst data
    raw_plist: dict         # Full parsed plist for future use

@dataclass
class LogicProject:
    name: str
    tempo: float
    time_sig_numerator: int
    time_sig_denominator: int
    sample_rate: int
    audio_files: list[AudioFileRef]
    plugins: list[PluginInstance]
    track_names: list[str]  # Ordered list of unique track names
    alternative: int        # Which alternative was parsed
```

**Parsing strategy**:
1. Parse `Resources/ProjectInformation.plist` for project name and version
2. Parse `Alternatives/{N}/MetaData.plist` for tempo, time sig, sample rate, audio inventory
3. Scan `Media/Audio Files/` for actual audio files present
4. Scan `ProjectData` binary:
   - Find all `<?xml version` → `</plist>` sections, parse with `plistlib`
   - Find audio file references via UTF-16LE filename + AUFL marker pattern
   - Extract track names from binary string regions
5. Group audio files by track name, detect takes and comps

### 2. Plugin Matcher (`plugin_matcher.py` + `plugin_database.py`)

**Plugin Database** — Hardcoded dictionary of known AU plugins:

```python
AU_PLUGINS = {
    # Waves plugins (manufacturer: ksWV)
    ("ksWV", "76CM"): PluginInfo("Waves CLA-76", "compressor", "FET compressor"),
    ("ksWV", "TG5M"): PluginInfo("Waves Abbey Road TG Mastering Chain", "channel_strip", "vintage console"),
    ("ksWV", "DSAM"): PluginInfo("Waves De-Esser", "de_esser", "de-esser"),
    ("ksWV", "LA2M"): PluginInfo("Waves CLA-2A", "compressor", "opto compressor"),
    ("ksWV", "L1CM"): PluginInfo("Waves L1 Limiter", "limiter", "brickwall limiter"),
    ("ksWV", "BSLM"): PluginInfo("Waves Bass Rider", "utility", "bass level rider"),
    # ... extensible
    # Apple built-in (manufacturer: appl)
    ("appl", "bceq"): PluginInfo("Channel EQ", "eq", "parametric EQ"),
    ("appl", "chor"): PluginInfo("Chorus", "modulation", "chorus"),
    ("appl", "pdlb"): PluginInfo("Pedalboard", "multi_fx", "guitar effects"),
}
```

**VST3 Scanner** (`vst3_scanner.py`):
- Reads directory listing from VST3 path
- Categorizes by name patterns and known plugin databases:
  - FabFilter Pro-Q → EQ
  - FabFilter Pro-C → Compressor
  - FabFilter Pro-L → Limiter
  - Valhalla → Reverb/Delay
  - etc.

**Matching**: For each Logic plugin, find VST3s in the same category, rank by character similarity.

### 3. Ableton Generator (`ableton_generator.py`)

**Input**: `LogicProject` + plugin match results
**Output**: `.als` file + `Samples/` directory

**Strategy**:
- Use a minimal Ableton 12 XML template as the base structure
- One `<AudioTrack>` per unique track name
- Active comp or first take placed as the arrangement clip
- Additional takes available as take lanes (Ableton 12 feature)
- Audio files copied to `Samples/Imported/` with `<SampleRef>` relative paths
- Clips placed at arrangement position 0 (bar 1 beat 1) with warp disabled
- Tempo and time signature set from Logic metadata
- Empty `<DeviceChain>` on each track (user adds their own plugins guided by the report)

### 4. CLI Interface (`logic2ableton.py`)

```
Usage: python logic2ableton.py <input.logicx> [options]

Options:
  --output DIR         Output directory (default: current directory)
  --alternative N      Which Logic alternative to convert (default: 0)
  --no-copy            Reference audio in-place instead of copying
  --report-only        Print plugin/track report without generating .als
  --vst3-path PATH     Custom VST3 directory (default: C:\Program Files\Common Files\VST3)
```

**Output files**:
1. `<Name> Project/<Name>.als` — The Ableton Live Set
2. `<Name> Project/Samples/Imported/` — Copied audio files
3. `<Name>_conversion_report.txt` — Detailed conversion report

**Report format**:
```
=== Logic Pro to Ableton Conversion Report ===
Project: Might Last Forever
Tempo: 120 BPM | Time Sig: 4/4 | Sample Rate: 44100

TRACKS TRANSFERRED (10):
  1. KICK IN — 3 takes, comp: none
  2. Tyler Amp — 3 takes, comp: none
  3. SNARE — 3 takes, comp: none
  ...

PLUGINS FOUND → SUGGESTED REPLACEMENTS:
  Waves CLA-76 (mono) "Guitar" → FabFilter Pro-C 2, Arturia Comp FET-76
  Waves L1 Limiter → FabFilter Pro-L 2
  Channel EQ (Logic) → FabFilter Pro-Q 3, Dragon EQ
  ...

NOT TRANSFERRED:
  - Plugin settings/parameters (not compatible across DAWs)
  - Automation data (requires deeper binary parsing)
  - Exact region positions (clips placed at bar 1; manual arrangement needed)
  - Bus/send routing (recreate manually in Ableton)
```

---

## Limitations (v1)

1. **No exact region timing** — Audio clips start at bar 1. User must manually arrange.
2. **No automation** — Automation data is in the binary but not parsed yet.
3. **No MIDI transfer** — MIDI data embedded in binary, not extracted in v1.
4. **No bus routing** — Send/return topology not reconstructed.
5. **Plugin suggestions only** — No automatic parameter mapping between plugins.
6. **Tested on Logic 10.3.x** — Newer versions may have different binary layout.

## Future Enhancements

- Parse region start/end positions from binary (incremental reverse-engineering)
- Extract MIDI note data
- Reconstruct bus/send routing
- Support Logic alternatives as Ableton scenes
- GUI wrapper with drag-and-drop
- Batch conversion of multiple projects
