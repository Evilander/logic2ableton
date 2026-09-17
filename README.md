# logic2ableton

[![PyPI version](https://img.shields.io/pypi/v/logic2ableton)](https://pypi.org/project/logic2ableton/)
[![Python 3.11+](https://img.shields.io/pypi/pyversions/logic2ableton)](https://pypi.org/project/logic2ableton/)
[![License: MIT](https://img.shields.io/pypi/l/logic2ableton)](https://github.com/Evilander/logic2ableton/blob/master/LICENSE)

**Move a project between Logic Pro, Ableton Live, and Pro Tools.** logic2ableton reads the DAWs' proprietary, undocumented session formats and rebuilds your project on the other side: audio placed on the timeline with clip trims intact, tempo, time signature, per-track colors, and note-accurate MIDI that lands as real MIDI tracks.

> **Wait — how is that possible?** Logic stores its MIDI in an undocumented binary blob, and Pro Tools sessions are an obfuscated binary container with no public spec. So they got reverse-engineered — Logic's format verified against Apple's own shipping demo songs, and Pro Tools' against real studio sessions.
>
> 📖 **[Deep-dive: reverse-engineering Logic's binary format →](docs/reverse-engineering-logic-pro-midi.md)**
> 📖 **[Deep-dive: inside the Pro Tools session container →](docs/reverse-engineering-pro-tools-sessions.md)**

It ships six production workflows in one repo, one desktop app, and one release train:

- `logic2ableton` — convert Logic Pro projects into Ableton Live sets
- `ableton2logic` — turn Ableton Live sets into Logic-ready transfer packages
- `protools2ableton` — convert Pro Tools sessions into Ableton Live sets
- `protools2logic` — turn Pro Tools sessions into Logic-ready transfer packages
- `ableton2protools` — turn Ableton Live sets into Pro Tools-ready transfer packages
- `logic2protools` — turn Logic Pro projects into Pro Tools-ready transfer packages

The product goal is **speed with evidence**: every run emits a report showing exactly what transferred cleanly, what needs manual cleanup, and where the source project exceeds what any cross-DAW workflow can preserve.

## Why Use It

- One maintained project instead of separate one-off scripts for each DAW direction
- Desktop app for non-technical users, CLI for power users and automation
- Output built for real sessions: copied audio, timeline metadata, and explicit compatibility reporting
- Reports are first-class artifacts, not an afterthought

## Workflow Matrix

| Workflow | Input | Output | Best For |
| --- | --- | --- | --- |
| `logic2ableton` | Logic Pro `.logicx` | Ableton Live `.als` + native MIDI tracks + copied media + conversion report | Moving Logic sessions into Ableton Arrangement View |
| `ableton2logic` | Ableton Live `.als` | Logic import package with track stems, timestamped clip WAVs, Logic timeline MIDI, and transfer report | Rebuilding Ableton sessions inside Logic with much cleaner layout recovery |
| `protools2ableton` | Pro Tools `.ptx` | Ableton Live `.als` with trimmed clips at session positions + native MIDI tracks + conversion report | Opening Pro Tools sessions directly in Ableton |
| `protools2logic` | Pro Tools `.ptx` | Logic import package with timestamped clip WAVs and MIDI + transfer report | Rebuilding Pro Tools sessions inside Logic |
| `ableton2protools` | Ableton Live `.als` | Pro Tools import package with spot-to-timestamp WAVs, MIDI files, manifest, and import guide | Handing an Ableton session to a Pro Tools studio |
| `logic2protools` | Logic Pro `.logicx` | Pro Tools import package with spot-to-timestamp WAVs, MIDI files, manifest, and import guide | Handing a Logic session to a Pro Tools studio |

## What Works Well

### Logic to Ableton

- Audio tracks into Ableton Arrangement View
- Region placement read from the Logic project itself: audio regions land where Logic had them, trimmed to the same slice of their file, on tracks named after the Logic tracks; older saves the decoder cannot read fall back to WAV BWF timestamps and Logic AIFF `MARK` chunks
- Tempo and time signature
- Overlap resolution for takes and comp bounces
- Audio membership follows the selected Logic alternative; unused and unreferenced takes are excluded
- Distinct per-track colors, with arrangement clips matching their track color
- MIDI regions decoded from Logic's binary project data land as **native Ableton MIDI tracks** inside the `.als` (and as Standard MIDI file exports): one clip per region at its arrangement position, and looped Logic regions become looping Live clips
- Logic markers become Live locators; muted regions are left out, as Logic would not play them
- `--smpte-start` sets the SMPTE time bar 1 plays at (or infers it with `auto`), for projects whose placement comes from audio timestamps
- `--timeline` supplies a tempo map (and, optionally, markers that replace the decoded ones); it becomes Live tempo automation, locators, and warp markers that follow the map (verified by opening the generated set in Live 12.4.3 and reading back its values)
- `--keep-unwarped` keeps matching tracks (sync tone, pilot tracks, timecode) as unwarped Live clips so they never stretch when the tempo changes
- Folder-saved Logic projects (a `.logicx` package next to a sibling `Audio Files` folder) are read the same as package-saved ones
- Multiple `.logicx` inputs in one run, converted and reported one after another
- Optional mixer overrides from JSON
- Plugin identification with VST3 suggestions in the report

### Ableton to Logic

- Audio-track and clip discovery from `.als`
- MIDI-track and arrangement note extraction, exported as importable Standard MIDI files
- Tempo, locators, and a documented base time signature
- Logic-ready transfer package with:
  - `Track Stems/` for the fastest arrangement-faithful import
  - `Logic Timeline/Logic Timeline.mid` for tempo and locator import
  - `MIDI Tracks/` with one Standard MIDI file per Ableton MIDI track, notes placed at their arrangement positions
  - `Audio Files/` grouped by Ableton track with timestamped WAV clip exports where supported
  - `timeline_manifest.json`
  - `timeline_manifest.csv`
  - `locators.csv`
  - `IMPORT_TO_LOGIC.md`
  - a saved transfer report

### Pro Tools to Ableton / Logic

- Reads `.ptx` sessions directly (Pro Tools 10 through current `.ptx` saves, plus legacy `.pts`), including the XOR-obfuscated container
- Audio clips with their exact source trims (a clip that plays 8.6 s from the middle of a take stays that clip), placed at their session positions
- Stereo tracks reassembled from Pro Tools' per-channel lanes
- MIDI notes decoded from the session and created as native Ableton MIDI tracks or Logic-importable MIDI files
- Session sample rate and format version detection
- Crossfade render files are recognized and skipped so they don't appear as phantom clips

### Ableton / Logic to Pro Tools

- Pro Tools import package with per-track folders of timestamped WAV clip exports
- BWF `TimeReference` stamped for Pro Tools' **Spot > Original Time Stamp** workflow (session start `00:00:00:00`, no SMPTE hour offset)
- One Standard MIDI file per MIDI track
- `manifest.json`, a transfer report, and a step-by-step `IMPORT GUIDE.txt`

## Current Limits

### Logic to Ableton

- MIDI notes transfer as native MIDI tracks (and `.mid` exports), but the software instruments, their settings, and MIDI effects are not recreated — reload instruments in Ableton
- Region placement, loops, track names and markers come from the project's own arrangement data. That decoding was worked out on Logic 10.6 and Logic 11 saves; the report says "Region positions: read from the Logic arrangement" when it applies. Saves it cannot read fall back to audio timestamps, `MIDI 1`, `MIDI 2`, ... track names, and a warning
- Regions that start before bar 1 are moved to bar 1 (audio is trimmed by the same amount) because a Live arrangement cannot start earlier
- Looped audio regions are written as repeated clips, spaced at the project tempo. If the tempo changes under a looped audio region, check the repeats; the report lists every region it unrolled
- Logic tracks that share a name are kept apart by numbering the later ones (`Guitar`, `Guitar (2)`), and the report says which were renamed
- Logic's tempo track is not decoded (only the project tempo is); supply tempo changes with `--timeline`
- `--smpte-start` only matters for the timestamp fallback; it must then match the project's own synchronization setting (default `01:00:00:00`), and the report lists any files placed at bar 1 because their timestamp precedes it
- Automation is not recreated
- Bus and send routing are not recreated
- Plugin parameters are not recreated
- In the timestamp fallback, imported audio without embedded timestamps defaults to bar 1
- Media outside `Media/Audio Files` is not copied automatically
- WAV duration detection includes IEEE float recordings. Sources whose duration cannot be read are skipped with a report warning

### Ableton to Logic

- The reverse lane does not synthesize a native `.logicx` package
- MIDI note data transfers, but instruments, devices, racks, MIDI effects, and plugin state do not — reload those in Logic
- Looping MIDI clips are unrolled to their arrangement length, honoring the loop brace and start marker the way Live plays them (verified against Live 12.4's own Consolidate output); notes cut at a loop or clip boundary are shortened, not extended
- Ableton devices, racks, plugin state, and return-bus processing are not transferred
- Warped clips are exported with best-effort timing, but they still need review inside Logic before delivery
- The base tempo, meter, and markers are exported into the Logic Timeline MIDI file. Later tempo and meter changes are reported but are not reconstructed
- Non-PCM sources that cannot be rendered to timestamped WAV in-process are copied as references and flagged in the report/manifest
- Media references outside the Ableton project folder are blocked. Use Live's **Collect All and Save** before transferring a set that relies on external files
- PCM and float audio are rendered in chunks. Individual rendered WAVs are limited to the RIFF format's 4 GiB size limit
- The transfer package covers audio and MIDI; use the stems and MIDI files first, then clip exports and the manifest if you need finer reconstruction

### Pro Tools lanes

- **Session tempo is not recoverable from `.ptx` yet.** Audio positions are sample-exact regardless, but beat positions are computed at an assumed tempo (default 120 BPM, override with `--tempo`). Keep the destination set at that tempo, or pass the real session BPM
- Plugins, inserts, sends, automation, clip gain, and fades are not transferred; crossfade renders are skipped
- Elastic Audio state is not reconstructed; clips reference their source audio directly
- The source session's `Audio Files/` folder must sit next to the `.ptx` for media to be copied
- MIDI regions anchor to their first note (a leading-silence offset inside a region is not preserved)

If a project lands imperfectly, the first thing to inspect is the generated report. It is the primary support artifact for this project.

## Reverse Import Strategy

For `ableton2logic`, the cleanest path is:

1. Import `Logic Timeline/Logic Timeline.mid` into a new empty Logic project at the project start.
2. Drag every file from `Track Stems/` into Logic starting at bar 1.
3. If you need clip-level editing, import `Audio Files/` and use Logic's `Edit > Move > To Recorded Position` command on timestamped WAV clips.
4. Use the transfer report and `timeline_manifest.csv` to review warped clips, copied-source files, and any manual cleanup.

## Install

| Method | Command / Link | Description |
|--------|----------------|-------------|
| **PyPI** | `pip install logic2ableton` | CLI tool, any platform with Python 3.11+ |
| Windows | [Installer](https://github.com/Evilander/logic2ableton/releases/latest) | Desktop app, standard Windows installer |
| Windows | [Portable](https://github.com/Evilander/logic2ableton/releases/latest) | Desktop app, single exe, no install needed |
| macOS (Apple Silicon) | [DMG (arm64)](https://github.com/Evilander/logic2ableton/releases/latest) | Desktop app for M1/M2/M3/M4 Macs on macOS 12 or newer |
| macOS (Apple Silicon, Big Sur) | [DMG (arm64, macOS 11)](https://github.com/Evilander/logic2ableton/releases/latest) | Desktop app for M1/M2/M3/M4 Macs still on macOS 11 |

### Desktop App

Download the latest installer or portable build from GitHub Releases:

- Windows: NSIS installer and portable `.exe`
- macOS: Apple Silicon `.dmg`, plus a second Apple Silicon `.dmg` (filename ending `-arm64-macos11.dmg`) for macOS 11 Big Sur

Notes:

- macOS builds are ad-hoc signed but not notarized, so Gatekeeper quarantines them on first download. If macOS says the app "is damaged and can't be opened," clear the quarantine flag once after copying it to Applications:

  ```bash
  xattr -dr com.apple.quarantine "/Applications/Logic Ableton Transfer.app"
  ```

  Alternatively, right-click the app and choose **Open**, then confirm in the dialog.
- Intel macOS users currently need a self-hosted packaging flow or a local source build (release DMGs are Apple Silicon only).
- The regular macOS DMG needs macOS 12 or newer. The macOS 11 DMG is built against Electron 37.10.3 instead of the current Electron release, because current Electron requires macOS 12+. Electron 37 has been end-of-life since 2026-01-13 and gets no further security updates, so use the regular DMG unless you're actually still on Big Sur.
- The desktop app bundles the converter binary, so end users do not need Python installed.

### Install from PyPI

```bash
pip install logic2ableton
```

If you prefer an isolated global CLI install:

```bash
pipx install logic2ableton
```

Then run from anywhere:

```bash
logic2ableton "path/to/MySong.logicx" --output ./output
```

Show the installed version:

```bash
logic2ableton --version
```

## Quick Start

### Desktop Workflow

1. Launch the app.
2. Drop any session file — `.logicx`, `.als`, or `.ptx` — into the window; the app detects the source DAW.
3. Pick the destination DAW and review the preview.
4. Select an output directory, run the transfer, and inspect the report if anything looks off.

### CLI Workflow

Choose the command that matches the route:

Logic to Ableton:

```bash
logic2ableton "/path/to/MySong.logicx" --output ./output
```

Logic to Ableton for a SMPTE-synced live show: infer the timecode hour, keep the sync and pilot tracks from stretching, and apply a tempo map with markers:

```bash
logic2ableton "Song.logicx" --output ./out --smpte-start auto --keep-unwarped "LTC*" --keep-unwarped "Pilot*" --timeline timeline.json
```

Batch convert several Logic projects in one run:

```bash
logic2ableton "Song1.logicx" "Song2.logicx" "Song3.logicx" --output ./output
```

Ableton to Logic:

```bash
ableton2logic "/path/to/MySet.als" --output ./output
```

Pro Tools to Ableton (pass the session tempo so beat positions line up):

```bash
protools2ableton "/path/to/MySession.ptx" --output ./output --tempo 128
```

Pro Tools to Logic:

```bash
protools2logic "/path/to/MySession.ptx" --output ./output --tempo 128
```

Ableton or Logic to Pro Tools:

```bash
ableton2protools "/path/to/MySet.als" --output ./output
logic2protools "/path/to/MySong.logicx" --output ./output
```

Fastest Logic import after the package is created:

1. Open `IMPORT_TO_LOGIC.md`.
2. Import `Logic Timeline/Logic Timeline.mid` into an empty Logic project at the timeline start.
3. Drag `Track Stems/*.wav` into Logic starting at bar 1.
4. Use `Audio Files/` only when you want clip-level reconstruction instead of full-track stems.

The original `logic2ableton` command also auto-detects `.als` and `.ptx` input:

```bash
logic2ableton "/path/to/MySet.als" --output ./output      # runs ableton2logic
logic2ableton "/path/to/MySession.ptx" --output ./output  # runs protools2ableton
```

Preview-only / report-only:

```bash
logic2ableton "/path/to/MySong.logicx" --report-only
ableton2logic "/path/to/MySet.als" --report-only
```

Generate a Logic mixer template:

```bash
logic2ableton "/path/to/MySong.logicx" --output ./output --generate-mixer-template --report-only
```

Apply mixer overrides:

```bash
logic2ableton "/path/to/MySong.logicx" --output ./output --mixer ./output/mixer_overrides.json
```

Emit JSON progress for app or automation integration:

```bash
logic2ableton "/path/to/MySong.logicx" --output ./output --json-progress
ableton2logic "/path/to/MySet.als" --output ./output --json-progress
```

Progress events report audio counts (`tracks`, `clips`, `audio_files`) separately
from `midi_tracks` and `midi_notes`. Previews include recovered MIDI content.
For Ableton output, the completion event's `midi_tracks` counts native tracks in
the set; `midi_files` separately counts exported `.mid` sidecars.

A failed run ends with an `error` event whose `failure_stage` names the step that
failed (for example `timeline`, `parsing` or `generating`) and whose `error`
holds the reason. The saved report keeps any analysis already done and ends
with the same stage and reason.

Pro Tools previews check referenced audio on disk. The report lists found,
missing, and skipped sources, and `compatibility_warnings` names missing media
before conversion begins. These notes also appear in the desktop preview.

## CLI Options

Every lane's input argument accepts one or more paths. Pass several to convert
each in turn; the CLI prints a per-file progress line and a converted/failed
summary at the end.

### Shared

| Option | Description |
| --- | --- |
| `--version`, `-V` | Show version |
| `--mode` | Force any of the six lane names (`logic2ableton`, `ableton2logic`, `protools2ableton`, `protools2logic`, `ableton2protools`, `logic2protools`) |
| `--output`, `-o` | Output directory |
| `--report-only` | Write the transfer report without generating output files |
| `--no-copy` | Do not copy audio. Generated Live sets reference the original files; transfer packages contain metadata and MIDI only |
| `--json-progress` | Emit JSON progress lines for GUI or automation use |

### Logic to Ableton Only

| Option | Description |
| --- | --- |
| `--alternative`, `-a` | Logic alternative index (also on `logic2protools`) |
| `--template` | Use a specific `DefaultLiveSet.als` (also on `protools2ableton`) |
| `--vst3-path` | Override the VST3 scan directory |
| `--mixer` | Apply mixer overrides from JSON |
| `--generate-mixer-template` | Write a starter `mixer_overrides.json` |
| `--smpte-start` | SMPTE time bar 1 plays at (default `01:00:00:00`); pass `auto` to infer one whole SMPTE hour from the earliest recording (also on `logic2protools`) |
| `--keep-unwarped` | Track-name glob, case-insensitive and repeatable; matching tracks are written as unwarped Live clips so they never stretch when the tempo changes |
| `--timeline` | JSON file with a tempo map and markers applied on top of the Logic project (see [Timeline JSON](#timeline-json) below) |

### Timeline JSON

`--timeline` supplies a tempo map, which the parser does not read from Logic's tempo track, and optionally markers. Markers decoded from the project are kept unless the file lists its own, which then replace them:

```json
{
  "tempo": [{"bar": 9, "bpm": 132}],
  "markers": [{"bar": 9, "name": "Chorus"}]
}
```

Each `tempo` or `markers` entry needs a position: either a 1-based `bar` with
an optional 1-based `beat` (defaults to 1), or an absolute `beats` value
counted in quarter notes from bar 1 (so bar 1 is `0`). Give one or the other,
not both. Bar and beat positions are resolved against the Logic project's own
base time signature. Tempo changes are steps, not ramps: each breakpoint holds
its BPM until the next one, and the report's `TIMELINE` section, the Live
tempo automation, and the clip warp markers all follow that same map.

### Pro Tools Imports Only

| Option | Description |
| --- | --- |
| `--tempo` | Tempo (BPM) used to convert sample positions to beats; `.ptx` does not expose its tempo yet (default 120) |

## Output Layout

Each conversion creates a fresh project or transfer folder. If the name already
exists, the new folder gets a suffix such as `(2)`, preserving the earlier export.
Project and track names are made safe for filenames on Windows and macOS.

### Logic (or Pro Tools) to Ableton

```text
output/
  MySong Project/
    MySong.als            <- audio tracks + native MIDI tracks
    Samples/
      Imported/
        *.wav / *.aif / *.aiff / *.mp3 / *.m4a
    MIDI/
      01 - MIDI 1.mid
  MySong_conversion_report.txt
```

### Ableton to Logic

```text
output/
  MySet Logic Transfer/
    Track Stems/
      01 - Drums.wav
      02 - Vocals.wav
    Logic Timeline/
      Logic Timeline.mid
    MIDI Tracks/
      01 - Bass.mid
      02 - Lead.mid
    Audio Files/
      01 - Drums/
      02 - Vocals/
    timeline_manifest.json
    timeline_manifest.csv
    locators.csv
    IMPORT_TO_LOGIC.md
    MySet_logic_transfer_report.txt
```

### Ableton (or Logic) to Pro Tools

```text
output/
  MySet Pro Tools Transfer/
    Audio Files/
      01 - Drums/
        001 - Kick Loop - *.wav   <- BWF TimeReference stamped for Spot > Original Time Stamp
      02 - Vocals/
    MIDI/
      01 - Bass.mid
    manifest.json
    IMPORT GUIDE.txt
    MySet_protools_transfer_report.txt
  MySet_protools_transfer_report.txt
```

## What "Production Ready" Means Here

- Repeated validation across parser tests, package builds, standalone converter builds, and desktop packaging
- Windows desktop smoke coverage in CI before tagged release packaging
- Reports emitted on both success and failure paths so support starts with evidence instead of guesswork
- Desktop app safety rails around approved files, active jobs, and artifact opening
- Ableton to Logic now ships multiple reconstruction layers instead of a single manifest-only package
- The Pro Tools parser is validated against a real Pro Tools 2023 studio session (96 kHz, comped vocals, stereo lanes) and a synthetic obfuscated fixture that runs in CI
- All six conversion lanes ship from the same repo and version together

## Reading The Reports

Every Logic to Ableton report opens with the SMPTE start it used (the
default `01:00:00:00`, a value passed with `--smpte-start`, or one inferred
from the earliest recording) and whether it found the project's audio in a
package or a folder-style layout.

Pay close attention to `COMPATIBILITY WARNINGS`.

Typical warnings include:

- Audio referenced by the source project but missing on disk
- Logic audio with no embedded timeline timestamp
- Audio files that start before the SMPTE start value; these land at bar 1 instead of their real position, and the report names each one
- Ableton clips that rely on warping or other live processing that cannot be rendered faithfully by this project
- Reverse-lane sources that were copied as references instead of rendered into timestamped WAV files

Warnings generally mean one of two things:

- the converter needs a parser/generator improvement
- the source session needs manual cleanup or a more deliberate export/import path

## Development

The reverse-engineering write-ups in [docs/](docs/) explain how the Logic
MIDI and Pro Tools session formats were decoded and what is still open.

Run tests:

```bash
python -m pytest tests -q
ruff check logic2ableton tests scripts
```

Tests against real sessions are optional and skipped by default. To run them,
point `L2A_LOGIC_FIXTURE` at a local `.logicx` package and `L2A_PTX_FIXTURE`
at a local `.ptx` file before invoking pytest.

Run the six-lane smoke check, which synthesizes a Logic project, a Live set,
and a Pro Tools session and converts each through the CLI:

```bash
python -m scripts.smoke_standalone --source
python -m scripts.smoke_standalone dist/logic2ableton.exe   # against the packaged binary
```

No third-party Python dependencies. The Ableton template is bundled, so no Ableton installation is needed to generate Live sets.

Build the Python package:

```bash
python -m build
```

Build the standalone converter:

```bash
pyinstaller logic2ableton.spec
dist/logic2ableton.exe --version
```

Run the desktop app in development:

```bash
cd app
npm ci
npm run dev
```

Build the desktop app:

```bash
cd app
npm ci
npm run build
npm run typecheck
npm test
```

The desktop bundles Geist Sans directly; its font license is included in
`app/src/renderer/public/Geist-LICENSE.txt` and copied into the built app.

Build the Windows release artifacts locally:

```bash
pyinstaller logic2ableton.spec
copy dist\logic2ableton.exe app\resources\logic2ableton.exe
cd app
npm ci
npm run dist:win
```

## Release Process

GitHub Actions validates:

- Ruff lint and the desktop app build
- Python tests on Windows and macOS
- Python package builds
- A packaged-binary smoke run through all six conversion lanes
- Tagged release packaging for Windows and macOS

Publishing a release is done by pushing a `v*` tag. The workflow uploads the
generated installers to GitHub Releases automatically. The same workflow
publishes the Python package to PyPI through a trusted publisher (environment
`pypi`) once the repository variable `PYPI_PUBLISH` is set to `true`.

The macOS build matrix produces two Apple Silicon DMGs: the regular one
(macOS 12+) and a second built against Electron 37.10.3 for macOS 11 Big
Sur. The installer job can also be run on demand with `workflow_dispatch`,
which builds and uploads the same artifacts without cutting a GitHub
Release.

## Bug Reports

Useful issues include:

- The Logic Pro or Ableton Live version used to save the project
- The exact generated report
- A minimal failing project if one can be shared
- Whether the issue is in the desktop app, CLI, or packaging

Open issues here: https://github.com/Evilander/logic2ableton/issues

## License

MIT
