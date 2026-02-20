# Roadmap

Last updated: 2026-02-19

## Priorities

1. Don't generate broken `.als` files. Ever.
2. Same input = same output. No random IDs, no timestamp jitter.
3. Audio fidelity first, then mix state, then MIDI/plugins.
4. Every feature gets tests. No exceptions.

## What Works Today

| Feature | Status | Notes |
|---------|--------|-------|
| Audio track + clip transfer | Done | Arrangement view, correct timeline positions |
| WAV timestamp extraction | Done | BWF bext chunk TimeReference |
| AIFF timestamp extraction | Done | MARK chunk Timestamp + Start markers |
| Overlap resolution | Done | comp > bounce-in-place > latest take per time range |
| Tempo + time signature | Done | Both MainTrack and Transport elements |
| Plugin identification | Done | 51 AU plugins in database (Waves, Apple built-ins) |
| VST3 suggestion matching | Done | Category-based, scans local VST3 directory |
| Mixer state (JSON overrides) | Done | `--mixer` flag, `--generate-mixer-template` |
| Conversion report | Done | Tracks, plugins, mixer state, suggestions |
| Cross-platform support | Done | macOS + Windows template/VST3 paths, --template flag |
| Plugin database (252 AU) | Done | Waves, Apple, iZotope, Soundtoys, Arturia, NI, Brainworx, +more |
| Name-similarity matching | Done | Token overlap scoring when category match is too broad |
| Test suite | Done | 107 tests across all modules, 5 real projects |

## Phase 1: Native Mixer Extraction

**Why first:** The JSON mixer override workflow works but requires manual effort. Extracting mixer values directly from Logic's binary `ProjectData` would give users a "just works" experience for volume, pan, mute, and solo.

**What to do:**
- Reverse-engineer the `ProjectData` binary format for track-level mixer values. The file contains embedded plists (already parsed for plugins) but the mixer state is likely in the proprietary binary sections between plists
- Binary exploration approach: dump hex regions near LFUA (filename) markers, look for IEEE 754 float patterns that correspond to known fader positions from test projects
- Map extracted mixer values to track names using the existing LFUA filename → track name pipeline
- Merge strategy: auto-extracted values as defaults, JSON overrides take precedence
- Validation: compare extracted values against manually-verified mixer positions from the 5 test projects

**Files to modify:**
- `logic_parser.py` — new `_extract_mixer_from_binary()` function
- `models.py` — no changes needed, `TrackMixerState` already exists
- `cli.py` — auto-extraction on by default, `--no-mixer-extract` to disable

**Done when:**
- At least volume and pan extract correctly from "Might Last Forever" (WAV, 120 BPM)
- JSON overrides still work and take priority over extracted values

## ~~Phase 2: Cross-Platform Support~~ (DONE)

**Why early:** Logic Pro users are on macOS. A Windows-only converter is backwards — the people who need this tool are transferring projects FROM Macs. Supporting macOS means the converter can run on the same machine as the Logic project.

**What to do:**
- Replace hardcoded `C:/ProgramData/Ableton/` template paths with platform-aware discovery:
  - macOS: `/Applications/Ableton Live 12 Suite.app/Contents/App-Resources/Builtin/Templates/`
  - Windows: current `C:/ProgramData/Ableton/` paths
  - Fallback: `--template` CLI flag for custom path
- Replace Windows-specific VST3 path (`C:/Program Files/Common Files/VST3`) with:
  - macOS: `/Library/Audio/Plug-Ins/VST3/` + `~/Library/Audio/Plug-Ins/VST3/`
  - Windows: current default
- Fix any `Path` separator assumptions (already mostly clean, but audit)
- Test on macOS (at minimum: template loading, file ref generation, path handling)

**Files to modify:**
- `ableton_generator.py` — `_find_template()` and `_TEMPLATE_PATHS`
- `vst3_scanner.py` — default scan paths
- `cli.py` — add `--template` flag

**Done when:**
- `python -m logic2ableton project.logicx -o ./out` works on macOS with Ableton 12 installed
- CI runs tests on both platforms (or at least template discovery is testable without Ableton)

## ~~Phase 3: Plugin Database Expansion~~ (DONE)

**Why now:** The current plugin database has 51 AU mappings, heavily weighted toward Waves (17) and Apple built-ins (11). Missing entire vendors: FabFilter, Universal Audio, iZotope, Soundtoys, SSL, Slate Digital, Plugin Alliance, Arturia. Better coverage means more useful conversion reports.

**What to do:**
- Expand `plugin_database.py` AU_PLUGINS dict with major vendors:
  - FabFilter (Pro-Q, Pro-C, Pro-L, Pro-R, Pro-DS, Pro-MB, Saturn, Timeless, Volcano)
  - Universal Audio (UA 1176, LA-2A, Neve, Pultec, SSL, Studer, API, Fairchild)
  - iZotope (Ozone, Neutron, RX, Nectar, Trash)
  - Soundtoys (Decapitator, EchoBoy, PrimalTap, PanMan, MicroShift)
  - SSL (Channel Strip, Bus Compressor)
  - Native Instruments (Guitar Rig, Massive, Kontakt)
  - Arturia (analog synth emulations)
- Add name-similarity matching in `plugin_matcher.py` as a fallback when category matching returns too many candidates
- Source AU component type/subtype/manufacturer codes from Apple's `auval` output or community databases

**Files to modify:**
- `plugin_database.py` — expand AU_PLUGINS
- `plugin_matcher.py` — add name-similarity scoring
- `vst3_scanner.py` — expand KNOWN_PLUGINS for new VST3 counterparts

**Done when:**
- 150+ AU plugin mappings
- Name-similarity fallback reduces "no match found" rate on test projects

## Phase 4: Automation Transfer

**Why before routing:** Automation (especially volume and pan) directly affects how a mix sounds. Routing is structural but automation is sonic — a volume ride on a vocal is more impactful than getting sends wired up correctly.

**What to do:**
- Identify automation data format in Logic `ProjectData` binary. Automation is likely stored as timestamped breakpoint envelopes per parameter per track
- Start with volume and pan automation only — these map cleanly to Ableton's `AutomationEnvelope` XML structure
- Ableton automation lives under `AutomationEnvelopes > Envelopes > AutomationLane` within each track, with `Events > AutomationEvent` containing `Time` and `Value`
- Timebase conversion: Logic uses samples or ticks, Ableton uses beats. `samples_to_beats()` already exists
- Interpolation: match Logic's curve type (linear, curved) to Ableton's closest equivalent
- Report unsupported automation lanes (plugin parameters, sends) with clear messaging

**Files to modify:**
- `logic_parser.py` — new `_extract_automation()` function
- `models.py` — new `AutomationLane` dataclass (parameter_id, breakpoints list)
- `ableton_generator.py` — inject `AutomationEnvelope` elements into tracks
- `report.py` — list transferred vs. skipped automation lanes

**Done when:**
- Volume automation from "Might Last Forever" renders correctly in Ableton
- No schema errors on import (verify with Ableton MCP `open_set`)

## Phase 5: Bus/Send Routing

**What to do:**
- Detect aux/bus tracks in Logic's binary data. Logic uses Bus 1-64 as internal routing; these map to Ableton's Return Tracks with Sends
- Create additional Return Tracks beyond the template defaults (A/B)
- Set Send knob values on audio tracks to route to the correct returns
- Preserve send levels where extractable
- Report unresolved routing (sidechain, complex routing chains) with "recreate manually" guidance

**Files to modify:**
- `logic_parser.py` — extract bus/send topology
- `models.py` — new `SendRoute` dataclass
- `ableton_generator.py` — create Return Tracks, set Send amounts
- `report.py` — routing summary section

**Gotchas:**
- Ableton requires `Sends` count to match `ReturnTracks` count — currently enforced by template preservation
- Adding return tracks means updating ClipSlotList Scenes count on every track

**Done when:**
- A project with aux sends converts with return tracks wired up
- No "more send knobs than sent to return" errors

## Phase 6: MIDI Transfer

**What to do:**
- Extract MIDI note data from Logic's `ProjectData` binary
- Create MIDI tracks in Ableton (use template's built-in MIDI track structure, similar to AudioTrack cloning)
- Generate `MidiClip` elements with `Notes > KeyTracks > MidiKey > MidiNoteEvent` structure
- Handle timing: Logic MIDI is likely in ticks (960 PPQN typical); convert to Ableton's beat-relative positioning
- Instrument tracks: create MIDI tracks with instrument slots empty, report which Logic instruments were present so users can manually load equivalents
- Tempo map interactions: if project has tempo changes, MIDI timing must account for them (Ableton warping)

**Files to modify:**
- `logic_parser.py` — new `_extract_midi_events()` function
- `models.py` — new `MidiNoteEvent` and `MidiClip` dataclasses
- `ableton_generator.py` — MIDI track creation and clip injection
- `report.py` — MIDI transfer summary

**Done when:**
- MIDI notes from a test project appear at correct positions in Ableton
- Both audio and MIDI tracks coexist in the same .als without errors

## Phase 7: Packaging and Distribution

**What to do:**
- Add `pyproject.toml` with proper metadata, entry point (`logic2ableton` CLI command)
- Publish to PyPI: `pip install logic2ableton`
- GitHub Actions CI: test on Python 3.11-3.13, Windows + macOS
- Versioned releases with changelog
- Consider a `--template` auto-download for users without Ableton installed (ship a minimal template?)

**Done when:**
- `pip install logic2ableton && logic2ableton project.logicx -o ./out` works
- CI blocks merges on test failures

## Future Ideas

These aren't planned but would be valuable if the core is solid:

- **Batch conversion** — `logic2ableton convert *.logicx --output ./batch/` for studio archives
- **GUI wrapper** — Electron or web-based drag-and-drop interface
- **Track colors** — Logic Pro assigns track colors; map to Ableton's 70-color palette
- **Clip gain** — per-clip volume adjustments (Logic "Region Gain")
- **Track grouping** — Logic folder stacks → Ableton Group Tracks
- **DAW-neutral intermediate format** — parse Logic once into a universal model, export to Ableton/Reaper/Pro Tools/etc.
- **Conversion diff tool** — compare two .als outputs to verify changes between converter versions
- **Ableton → Logic** — reverse direction (much harder, Ableton XML → Logic binary)

## Rules

- Every new transfer feature needs: parser tests, generator tests, at least one CLI integration test
- Existing CLI flags don't break. New flags are additive
- Breaking changes get documented in README with migration notes
- Test against all 5 reference projects before merging features that touch clip placement or track structure
