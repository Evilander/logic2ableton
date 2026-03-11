# logic2ableton

Convert Logic Pro `.logicx` projects into Ableton Live `.als` sessions with a desktop app or CLI.

`logic2ableton` is built for audio-first projects: bounced stems, recorded takes, arrangement timing, tempo, time signature, mixer-state overrides, and plugin identification. The goal is simple: get a working Ableton arrangement fast, then use the compatibility report to see exactly what still needs manual cleanup.

**Latest release:** https://github.com/Evilander/logic2ableton/releases/latest

## Why People Use It

- Move recorded Logic sessions into Ableton without rebuilding the entire timeline by hand.
- Recover placement from embedded WAV BWF timestamps and Logic AIFF `MARK` chunks.
- See conversion limits immediately through a saved compatibility report instead of guessing what failed.
- Hand off projects between collaborators who prefer different DAWs.

## What The Converter Transfers Well

- Audio tracks into Ableton Arrangement View
- Timeline placement from bundled audio timestamps
- Tempo and time signature
- Overlap resolution for comp-style takes
- Optional mixer overrides from JSON
- Plugin identification with VST3 suggestions in the report

## Current Limits

- MIDI and software instrument tracks are not recreated
- Automation is not recreated
- Bus and send routing are not recreated
- Plugin parameters are not recreated
- Imported audio without embedded timestamps defaults to bar 1
- Media outside `Media/Audio Files` is not copied automatically

If a project lands imperfectly, the first thing to inspect is the generated conversion report. It is the primary support artifact for this project.

## Install

### Desktop App

Download the latest installer or portable build from GitHub Releases:

- Windows: NSIS installer and portable `.exe`
- macOS: Apple Silicon `.dmg`

Notes:

- macOS builds are unsigned. Gatekeeper may require opening them manually the first time.
- Intel macOS users currently need to run from source or use a self-hosted packaging flow. GitHub-hosted Intel runner support for this repo's release lane is not stable enough to publish as an official artifact.
- The desktop app bundles the converter binary, so end users do not need Python installed.

### CLI From Source

```bash
git clone https://github.com/Evilander/logic2ableton.git
cd logic2ableton
pip install -e .
```

Show the installed version:

```bash
logic2ableton --version
```

## Quick Start

### Desktop Workflow

1. Drop a `.logicx` project into the app.
2. Review the preview summary.
3. Pick an output directory.
4. Convert and inspect the report if anything looks off.

The app keeps a local history of recent conversions so failed runs are easier to revisit.

### CLI Workflow

Convert a project:

```bash
logic2ableton "/path/to/MySong.logicx" --output ./output
```

Generate only the report:

```bash
logic2ableton "/path/to/MySong.logicx" --report-only
```

Generate a mixer template:

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
```

## CLI Options

| Option | Description |
| --- | --- |
| `--version`, `-V` | Show version |
| `--output`, `-o` | Output directory |
| `--alternative`, `-a` | Logic alternative index |
| `--report-only` | Write the conversion report without generating `.als` |
| `--no-copy` | Do not copy audio files into the Ableton project |
| `--template` | Use a specific `DefaultLiveSet.als` |
| `--vst3-path` | Override the VST3 scan directory |
| `--mixer` | Apply mixer overrides from JSON |
| `--generate-mixer-template` | Write a starter `mixer_overrides.json` |
| `--json-progress` | Emit JSON progress lines for GUI integration |

## Output Layout

```text
output/
  MySong Project/
    MySong.als
    Samples/
      Imported/
        *.wav / *.aif / *.aiff / *.mp3 / *.m4a
  MySong_conversion_report.txt
```

The report is written for successful conversions, `--report-only` runs, and controlled failure paths so troubleshooting does not depend on terminal scrollback.

## Reading The Report

Pay close attention to `COMPATIBILITY WARNINGS`.

Typical warnings:

- Audio listed by Logic but missing from the bundle
- Audio files with no embedded timeline timestamp
- Track-count mismatches between Logic metadata and recoverable audio

These warnings usually mean one of two things:

- the project needs a parser improvement in `logic2ableton`
- part of the source session needs a cleaner export path before conversion

## Development

Run tests:

```bash
python -m pytest tests -q
```

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

- The Logic Pro version used to save the project
- The exact conversion report
- Whether the project uses imported loops, aliases, or external media
- A minimal failing project if one can be shared

Open issues here: https://github.com/Evilander/logic2ableton/issues

## License

MIT
