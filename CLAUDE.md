# CLAUDE.md

## Project: Logic Pro to Ableton Live Converter

Converts Logic Pro `.logicx` projects into Ableton Live `.als` files, preserving audio stems, track structure, tempo, time signature, and providing plugin replacement suggestions.

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
| `logic_parser.py` | Parse `.logicx` bundles: plist metadata, binary ProjectData (EVAW/LFUA markers), audio file discovery |
| `ableton_generator.py` | Generate `.als` files using Ableton's `DefaultLiveSet.als` as template |
| `plugin_database.py` | AU component type/subtype/manufacturer mappings |
| `vst3_scanner.py` | Scan local VST3 plugins from `C:/Program Files/Common Files/VST3/` |
| `plugin_matcher.py` | Match Logic AU plugins to local VST3 alternatives by category |
| `report.py` | Generate human-readable conversion report |
| `cli.py` | CLI entry point |

## Key Technical Details

### Logic Pro `.logicx` Format
- macOS bundle with `Resources/ProjectInformation.plist`, `Alternatives/000/MetaData.plist`, `Alternatives/000/ProjectData` (binary), `Media/Audio Files/`
- MetaData.plist: tempo, time sig, sample rate, audio file list
- ProjectData binary: embedded plists for plugins, EVAW markers (audio file metadata), LFUA markers (audio filenames)
- EVAW offset +12 = file length in samples (NOT start position). LFUA[N] maps to EVAW[N] by positional index
- Audio filename patterns: `track#01.wav` (takes), `track_ Comp A.wav` (comps), `track_bip.wav` (bounce-in-place)

### Ableton `.als` Generation
- **Template-based**: loads `DefaultLiveSet.als` from Ableton Live 12 installation at `C:/ProgramData/Ableton/Live 12 Suite/Resources/Builtin/Templates/`
- Clones the template's AudioTrack for each Logic track, injects AudioClip elements into `MainSequencer > Sample > ArrangerAutomation > Events`
- **All IDs must be globally unique** for `AutomationTarget`, `ModulationTarget`, `Pointee` elements. Uses `_IdAllocator` starting from `NextPointeeId`
- **ReturnTracks must be preserved** from template (send bus routing). Removing them causes "more send knobs than sent to return" error
- **One clip per track**: multiple overlapping clips at Time=0 causes Ableton to silently drop clips. Use `_pick_best_clip()` (comp > bip > latest take)
- Logic Pro recordings start from bar 1 of the session, so all clips are placed at `Time=0`
- FileRef uses `RelativePathType=3` (project-relative) with `Samples/Imported/filename.wav` and absolute `Path`

### Common Errors When Generating .als
- "Slot count mismatch" = Scenes count must match ClipSlotList count per track
- "Unknown class 'Sample'" = wrong XML structure in FreezeSequencer; use template approach
- "more send knobs than sent to return" = ReturnTracks removed but Sends still reference them
- Clips invisible = overlapping clips at same Time position, or duplicate IDs

## Test Data
- `Might Last Forever.logicx`: 13 tracks, 38 audio files (WAV, 24-bit mono + 16-bit stereo), 120 BPM, 4/4, 44100 Hz
- 71 tests covering parser, generator, models, plugins, report, CLI

## Dependencies
- Python 3.11+ (no external packages)
- Ableton Live 12 installed (for DefaultLiveSet.als template)

## File Conventions
- Tests in `tests/test_*.py`, one per module
- Plans/design docs in `docs/plans/`
- Generated output in `output/` (gitignored)
