# CLAUDE.md

## Project: Logic Pro to Ableton Live Converter

Converts Logic Pro `.logicx` projects into Ableton Live `.als` files, preserving audio stems, track structure, clip timeline positions, tempo, time signature, and providing plugin replacement suggestions.

**GitHub**: https://github.com/Evilander/logic2ableton

## Quick Start

```bash
# Run converter
python -m logic2ableton "path/to/project.logicx" --output ./output

# Run tests
python -m pytest tests/ -v
```

## Architecture

| Module | Purpose |
|--------|---------|
| `models.py` | Data classes: `AudioFileRef`, `PluginInstance`, `LogicProject`, `samples_to_beats()` |
| `logic_parser.py` | Parse `.logicx` bundles: plist metadata, binary ProjectData, BWF/AIFF timestamps, audio discovery |
| `ableton_generator.py` | Generate `.als` files using Ableton's `DefaultLiveSet.als` as template |
| `plugin_database.py` | AU component type/subtype/manufacturer mappings |
| `vst3_scanner.py` | Scan local VST3 plugins from `C:/Program Files/Common Files/VST3/` |
| `plugin_matcher.py` | Match Logic AU plugins to local VST3 alternatives by category |
| `report.py` | Generate human-readable conversion report |
| `cli.py` | CLI entry point |

## Key Technical Details

### Logic Pro `.logicx` Format
- macOS bundle: `Resources/ProjectInformation.plist`, `Alternatives/000/MetaData.plist`, `Alternatives/000/ProjectData` (binary), `Media/Audio Files/`
- MetaData.plist: tempo, time sig, sample rate, audio file list
- ProjectData binary: embedded plists for plugins, EVAW markers (file length, NOT position), LFUA markers (filenames)
- Audio filename patterns: `track#01.wav` (takes), `track_ Comp A.wav` (comps), `track_bip.wav` (bounce-in-place)

### Audio Timeline Positioning
- **WAV files**: BWF bext chunk at offset 338 contains TimeReference (uint64, samples from SMPTE midnight)
- **AIFF files**: MARK chunk contains `Timestamp: N` marker (SMPTE position) + `Start` marker (content offset after pre-roll)
- Logic default SMPTE offset: `3600 * sample_rate` (1 hour). Subtract to get position relative to bar 1
- AIFF content position = `Timestamp + Start_marker - SMPTE_offset`
- Python's `wave` module cannot read AIFF — COMM chunk parsed manually for nframes/sample_rate
- Imported files (MP3, non-timestamped) have no position data; default to 0

### Ableton `.als` Generation
- **Template-based**: loads `DefaultLiveSet.als` from `C:/ProgramData/Ableton/Live 12 Suite/Resources/Builtin/Templates/`
- Clones template AudioTrack per Logic track, injects AudioClip elements into `MainSequencer > Sample > ArrangerAutomation > Events`
- **All IDs must be globally unique** for `AutomationTarget`, `ModulationTarget`, `Pointee`. Uses `_IdAllocator` from `NextPointeeId`
- **ReturnTracks must be preserved** from template
- **CurrentStart/CurrentEnd are ABSOLUTE timeline positions**: `CurrentStart=Time`, `CurrentEnd=Time+Duration`
- **FileRef**: `Type=1` for audio files, `RelativePathType=1`, `LastModDate` in SampleRef (not FileRef)
- Overlap resolution: comp > bounce-in-place > latest take; non-overlapping clips all kept

### Common Errors When Generating .als
- "Slot count mismatch" = Scenes count must match ClipSlotList count per track
- "more send knobs than sent to return" = ReturnTracks removed but Sends still reference them
- Clips invisible = overlapping clips at same Time position, or duplicate IDs
- Audio offline = wrong FileRef Type/RelativePathType, or missing LastModDate

## Test Data
- `Might Last Forever.logicx`: 13 tracks, 36 audio files (WAV), 120 BPM
- Additional test projects: Rip It Up (AIFF, 140 BPM), one and only lover (AIFF, 95 BPM), Raincoat (mixed, 135 BPM), almost a ghost (mixed, 130 BPM)
- 72 tests covering parser, generator, models, plugins, report, CLI

## What's Transferred
- Audio stems with correct timeline positions
- Track names and structure
- Tempo and time signature
- Plugin identification + VST3 match suggestions

## What's NOT Transferred (Yet)
- Mixer state (volume, pan, mute/solo) — **next priority**
- Automation curves
- Bus/send routing
- MIDI data
- Track grouping/folders
- Plugin parameters (not compatible across DAWs)

## Dependencies
- Python 3.11+ (no external packages)
- Ableton Live 12 installed (for DefaultLiveSet.als template)
- Currently Windows-only (hardcoded template paths)

## File Conventions
- Tests in `tests/test_*.py`, one per module
- Plans/design docs in `docs/plans/`
- Generated output in `output/` (gitignored)
