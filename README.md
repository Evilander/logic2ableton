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
- Timeline placement from bundled WAV BWF timestamps and Logic AIFF `MARK` chunks
- Tempo and time signature
- Overlap resolution for takes and comp bounces
- Distinct per-track colors, with arrangement clips matching their track color
- MIDI notes decoded from Logic's binary project data land as **native Ableton MIDI tracks** inside the `.als` (and as Standard MIDI file exports), placed at their absolute arrangement positions
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
- MIDI tracks are named `MIDI 1`, `MIDI 2`, ... (binding Logic's track names to its binary note sequences is still being reverse-engineered)
- Notes placed before Logic's bar-1 anchor fall back to relative placement, with a warning in the report
- Older Logic save formats store notes in a binary variant this project cannot decode yet; the report says so explicitly instead of pretending
- Automation is not recreated
- Bus and send routing are not recreated
- Plugin parameters are not recreated
- Imported audio without embedded timestamps defaults to bar 1
- Media outside `Media/Audio Files` is not copied automatically

### Ableton to Logic

- The reverse lane does not synthesize a native `.logicx` package
- MIDI note data transfers, but instruments, devices, racks, MIDI effects, and plugin state do not — reload those in Logic
- Looping MIDI clips export only their first pass of notes; repeat them manually in Logic if needed
- Ableton devices, racks, plugin state, and return-bus processing are not transferred
- Warped clips are exported with best-effort timing, but they still need review inside Logic before delivery
- Tempo and markers are exported into the Logic Timeline MIDI file; do not assume time-signature changes are fully reconstructed unless you verify them in Logic
- Non-PCM sources that cannot be rendered to timestamped WAV in-process are copied as references and flagged in the report/manifest
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
| macOS (Apple Silicon) | [DMG (arm64)](https://github.com/Evilander/logic2ableton/releases/latest) | Desktop app for M1/M2/M3/M4 Macs |

### Desktop App

Download the latest installer or portable build from GitHub Releases:

- Windows: NSIS installer and portable `.exe`
- macOS: Apple Silicon `.dmg`

Notes:

- macOS builds are ad-hoc signed but not notarized, so Gatekeeper quarantines them on first download. If macOS says the app "is damaged and can't be opened," clear the quarantine flag once after copying it to Applications:

  ```bash
  xattr -dr com.apple.quarantine "/Applications/Logic Ableton Transfer.app"
  ```

  Alternatively, right-click the app and choose **Open**, then confirm in the dialog.
- Intel macOS users currently need a self-hosted packaging flow or a local source build (release DMGs are Apple Silicon only).
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

## CLI Options

### Shared

| Option | Description |
| --- | --- |
| `--version`, `-V` | Show version |
| `--mode` | Force any of the six lane names (`logic2ableton`, `ableton2logic`, `protools2ableton`, `protools2logic`, `ableton2protools`, `logic2protools`) |
| `--output`, `-o` | Output directory |
| `--report-only` | Write the transfer report without generating output files |
| `--no-copy` | Do not copy audio files into the generated project/package |
| `--json-progress` | Emit JSON progress lines for GUI or automation use |

### Logic to Ableton Only

| Option | Description |
| --- | --- |
| `--alternative`, `-a` | Logic alternative index (also on `logic2protools`) |
| `--template` | Use a specific `DefaultLiveSet.als` (also on `protools2ableton`) |
| `--vst3-path` | Override the VST3 scan directory |
| `--mixer` | Apply mixer overrides from JSON |
| `--generate-mixer-template` | Write a starter `mixer_overrides.json` |

### Pro Tools Imports Only

| Option | Description |
| --- | --- |
| `--tempo` | Tempo (BPM) used to convert sample positions to beats; `.ptx` does not expose its tempo yet (default 120) |

## Output Layout

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

Pay close attention to `COMPATIBILITY WARNINGS`.

Typical warnings include:

- Audio referenced by the source project but missing on disk
- Logic audio with no embedded timeline timestamp
- Ableton clips that rely on warping or other live processing that cannot be rendered faithfully by this project
- Reverse-lane sources that were copied as references instead of rendered into timestamped WAV files

Warnings generally mean one of two things:

- the converter needs a parser/generator improvement
- the source session needs manual cleanup or a more deliberate export/import path

## Development

Run tests:

```bash
python -m pytest tests -q
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
```

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

- Python tests on Windows and macOS
- Python package builds
- Windows desktop smoke builds before release tags
- Tagged release packaging for Windows and macOS

Publishing a release is done by pushing a `v*` tag. The workflow uploads the generated installers to GitHub Releases automatically.

## Bug Reports

Useful issues include:

- The Logic Pro or Ableton Live version used to save the project
- The exact generated report
- A minimal failing project if one can be shared
- Whether the issue is in the desktop app, CLI, or packaging

Open issues here: https://github.com/Evilander/logic2ableton/issues

## License

MIT
