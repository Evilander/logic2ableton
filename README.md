# logic2ableton

[![PyPI version](https://img.shields.io/pypi/v/logic2ableton)](https://pypi.org/project/logic2ableton/)
[![Python 3.11+](https://img.shields.io/pypi/pyversions/logic2ableton)](https://pypi.org/project/logic2ableton/)
[![License: MIT](https://img.shields.io/pypi/l/logic2ableton)](https://github.com/Evilander/logic2ableton/blob/master/LICENSE)
Bidirectional, audio-first transfer between Logic Pro `.logicx` projects and Ableton Live `.als` sessions.

Version 2.0 ships two production workflows in the same project:

- `logic2ableton`: convert Logic Pro projects into Ableton Live sets
- `ableton2logic`: turn Ableton Live sets into Logic-ready transfer packages

The product goal is speed with evidence. Every run produces a report so users can see exactly what transferred cleanly, what needs manual cleanup, and where the source project exceeds what a cross-DAW workflow can preserve.

## What Works Well

### Logic to Ableton

- Audio tracks into Ableton Arrangement View
- Timeline placement from bundled WAV BWF timestamps and Logic AIFF `MARK` chunks
- Tempo and time signature
- Overlap resolution for takes and comp bounces
- Optional mixer overrides from JSON
- Plugin identification with VST3 suggestions in the report

### Ableton to Logic

- Audio-track and clip discovery from `.als`
- Tempo, time signature, and locators
- Logic-ready transfer package with:
  - `Audio Files/` grouped by Ableton track
  - `timeline_manifest.json`
  - `timeline_manifest.csv`
  - `locators.csv`
  - `IMPORT_TO_LOGIC.md`
  - a saved transfer report

## Current Limits

### Logic to Ableton

- MIDI and software instrument tracks are not recreated
- Automation is not recreated
- Bus and send routing are not recreated
- Plugin parameters are not recreated
- Imported audio without embedded timestamps defaults to bar 1
- Media outside `Media/Audio Files` is not copied automatically

### Ableton to Logic

- The reverse lane does not synthesize a native `.logicx` package
- Ableton devices, racks, plugin state, and return-bus processing are not transferred
- Warped clips are exported as source references and called out in the report, but warp rendering must be recreated manually in Logic
- The transfer package is audio-first; use the manifest and import guide to rebuild the arrangement inside Logic

If a project lands imperfectly, the first thing to inspect is the generated report. It is the primary support artifact for this project.

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

- macOS builds are unsigned. Gatekeeper may require opening them manually the first time.
- Intel macOS users currently need a self-hosted packaging flow or a local source build.
- The desktop app bundles the converter binary, so end users do not need Python installed.

### Install from PyPI

```bash
pip install logic2ableton
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
2. Choose `Logic to Ableton` or `Ableton to Logic`.
3. Drop a `.logicx` or `.als` file into the window.
4. Review the preview and select an output directory.
5. Run the transfer and inspect the report if anything looks off.

### CLI Workflow

Logic to Ableton:

```bash
logic2ableton "/path/to/MySong.logicx" --output ./output
```

Ableton to Logic:

```bash
ableton2logic "/path/to/MySet.als" --output ./output
```

The original `logic2ableton` command also auto-detects `.als` input:

```bash
logic2ableton "/path/to/MySet.als" --output ./output
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
| `--mode` | Force `logic2ableton` or `ableton2logic` |
| `--output`, `-o` | Output directory |
| `--report-only` | Write the transfer report without generating output files |
| `--no-copy` | Do not copy audio files into the generated project/package |
| `--json-progress` | Emit JSON progress lines for GUI or automation use |

### Logic to Ableton Only

| Option | Description |
| --- | --- |
| `--alternative`, `-a` | Logic alternative index |
| `--template` | Use a specific `DefaultLiveSet.als` |
| `--vst3-path` | Override the VST3 scan directory |
| `--mixer` | Apply mixer overrides from JSON |
| `--generate-mixer-template` | Write a starter `mixer_overrides.json` |

## Output Layout

### Logic to Ableton

```text
output/
  MySong Project/
    MySong.als
    Samples/
      Imported/
        *.wav / *.aif / *.aiff / *.mp3 / *.m4a
  MySong_conversion_report.txt
```

### Ableton to Logic

```text
output/
  MySet Logic Transfer/
    Audio Files/
      01 - Drums/
      02 - Vocals/
    timeline_manifest.json
    timeline_manifest.csv
    locators.csv
    IMPORT_TO_LOGIC.md
    MySet_logic_transfer_report.txt
```

## Reading The Reports

Pay close attention to `COMPATIBILITY WARNINGS`.

Typical warnings include:

- Audio referenced by the source project but missing on disk
- Logic audio with no embedded timeline timestamp
- Ableton clips that rely on warping or other live processing that cannot be rendered faithfully by this project

Warnings generally mean one of two things:

- the converter needs a parser/generator improvement
- the source session needs manual cleanup or a more deliberate export/import path

## Development

Run tests:

```bash
python -m pytest tests -q
```

No third-party Python dependencies. The Ableton template is bundled — no Ableton installation needed.

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
