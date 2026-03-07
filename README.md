# logic2ableton

Convert Logic Pro `.logicx` projects into Ableton Live `.als` sets.

The converter is aimed at audio-first Logic sessions: recorded stems, bounced comps, clip placement, project tempo, time signature, and plugin identification. It ships as a desktop app for Windows and macOS, and it can also be run from source as a CLI.

**[Download the latest release](https://github.com/Evilander/logic2ableton/releases/latest)**

## What Works Well

- Audio tracks into Ableton Arrangement View
- Timeline placement from embedded WAV BWF timestamps
- Timeline placement from Logic AIFF `MARK` chunks
- Overlap resolution: comp > bounce-in-place > latest take
- Tempo and time signature transfer
- Optional mixer overrides from JSON
- Plugin identification with VST3 suggestions

## Current Limits

- MIDI and software instrument tracks are not transferred
- Bus/send routing is not recreated
- Automation is not recreated
- Plugin parameters are not recreated
- Imported audio without embedded timestamps defaults to bar 1
- External or aliased media that is not inside `Media/Audio Files` cannot be copied yet

That last pair is the main reason some projects convert well and others do not. As of `v1.0.2`, the conversion report explicitly calls these cases out instead of looking like a clean success.

## Desktop App

The desktop app supports drag-and-drop project selection, project previews, real-time conversion progress, and a conversion history sidebar.

## CLI

Install from source:

```bash
git clone https://github.com/Evilander/logic2ableton.git
cd logic2ableton
pip install -e .
```

Convert a project:

```bash
logic2ableton "path/to/MySong.logicx" --output ./output
```

Generate only the text report:

```bash
logic2ableton "path/to/MySong.logicx" --report-only
```

Generate a mixer template you can edit:

```bash
logic2ableton "path/to/MySong.logicx" --output ./output --generate-mixer-template --report-only
```

Apply mixer overrides during conversion:

```bash
logic2ableton "path/to/MySong.logicx" --output ./output --mixer ./output/mixer_overrides.json
```

### CLI Options

| Option | Description |
|--------|-------------|
| `--version`, `-V` | Show version |
| `--output`, `-o` | Output directory |
| `--alternative`, `-a` | Logic alternative index |
| `--report-only` | Print the conversion report without writing `.als` |
| `--no-copy` | Do not copy audio files into the Ableton project |
| `--template` | Use a specific `DefaultLiveSet.als` |
| `--vst3-path` | Override VST3 scan directory |
| `--mixer` | Apply mixer overrides from JSON |
| `--generate-mixer-template` | Write a starter `mixer_overrides.json` |
| `--json-progress` | Emit JSON progress lines for GUI integration |

## Output

```text
output/
  MySong Project/
    MySong.als
    Samples/
      Imported/
        *.wav / *.aif / *.aiff / *.mp3 / *.m4a
  MySong_conversion_report.txt
```

If Ableton generation fails after parsing, the converter still writes the text report so you have something actionable to inspect or attach to an issue.

## Reading The Report

The report now includes a `COMPATIBILITY WARNINGS` section. Pay attention to it when a session does not line up in Ableton.

Typical warnings:

- Audio listed by Logic but missing from the bundle
- Audio files with no embedded timeline timestamp
- A track-count mismatch between Logic metadata and recoverable bundled audio

These warnings usually mean the session needs either a parser improvement or a different export path for part of the project.

## Building And Testing

Run the Python tests:

```bash
python -m pytest tests -q
```

Run the desktop app in development:

```bash
cd app
npm install
npm run dev
```

## Bug Reports

Useful bug reports include:

- The Logic version used to save the project
- The report text, especially `COMPATIBILITY WARNINGS`
- Whether the project uses imported loops, external media, aliases, or lots of AIFF recordings
- A minimal failing project if you can share one

## License

MIT
