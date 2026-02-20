# logic2ableton

Convert Logic Pro `.logicx` projects into Ableton Live `.als` sets — preserving audio arrangement, timeline positions, track structure, tempo, and time signature. Includes a desktop GUI and command-line interface.

**[Download the latest release](https://github.com/Evilander/logic2ableton/releases/latest)** — available for Windows and macOS. No Python or Ableton installation required.

> This is a v1.0 release. If you try it out, please [open an issue](https://github.com/Evilander/logic2ableton/issues) with feedback, bug reports, or feature requests. Especially interested in hearing from people converting real-world sessions.

## Download

| Platform | File | Description |
|----------|------|-------------|
| Windows | [Installer](https://github.com/Evilander/logic2ableton/releases/latest) | Standard Windows installer |
| Windows | [Portable](https://github.com/Evilander/logic2ableton/releases/latest) | Single exe, no install needed |
| macOS (Apple Silicon) | [DMG (arm64)](https://github.com/Evilander/logic2ableton/releases/latest) | For M1/M2/M3/M4 Macs |
| macOS (Intel) | [DMG (x64)](https://github.com/Evilander/logic2ableton/releases/latest) | For Intel Macs |

### Desktop App

The desktop app provides drag-and-drop project selection, a project preview with track/clip/plugin counts, real-time conversion progress, and a history sidebar. Just drop your `.logicx` folder and convert.

### CLI

Also available as a command-line tool when installed from source:

```bash
git clone https://github.com/Evilander/logic2ableton.git
cd logic2ableton
pip install -e .
logic2ableton "path/to/MySong.logicx" --output ./output
```

## What It Transfers

- Audio tracks and clips into Ableton Arrangement View
- Timeline positions from embedded WAV (BWF bext) and AIFF (MARK chunk) timestamps
- Clip overlap resolution: comp > bounce-in-place > latest take
- Mixer state (volume, pan, mute, solo) via JSON overrides
- Tempo and time signature
- Plugin identification with VST3 match suggestions

## What It Does Not Transfer Yet

- Automation curves
- MIDI notes and instrument tracks
- Bus/send routing
- Plugin parameters
- Folder/grouping

## CLI Reference

```text
logic2ableton <input.logicx> [options]
```

| Option | Description |
|--------|-------------|
| `--version`, `-V` | Show version |
| `--output`, `-o` | Output directory (default `.`) |
| `--report-only` | Print conversion report only |
| `--no-copy` | Don't copy audio files |
| `--template` | Path to Ableton `DefaultLiveSet.als` template (bundled by default) |
| `--vst3-path` | VST3 directory for suggestion scanning |
| `--mixer` | Path to `mixer_overrides.json` |
| `--generate-mixer-template` | Generate editable mixer JSON for all tracks |
| `--json-progress` | Machine-readable JSON progress output |

## Mixer Override Workflow

1. Generate a template:
```bash
logic2ableton "MySong.logicx" -o ./output --generate-mixer-template --report-only
```

2. Edit `output/mixer_overrides.json` with your volume/pan/mute/solo values.

3. Apply during conversion:
```bash
logic2ableton "MySong.logicx" -o ./output --mixer ./output/mixer_overrides.json
```

## Output Structure

```text
output/
  MySong Project/
    MySong.als
    Samples/
      Imported/
        *.wav / *.aif / *.aiff
  MySong_conversion_report.txt
```

## Building from Source

```bash
git clone https://github.com/Evilander/logic2ableton.git
cd logic2ableton
pip install -e .
python -m pytest tests -q
```

No third-party Python dependencies. The Ableton template is bundled — no Ableton installation needed.

### Desktop App Development

```bash
cd app
npm install
npm run dev
```

## Contributing

1. Fork and create a branch from `master`
2. Make changes with test coverage
3. Run `python -m pytest tests -q`
4. Open a pull request

## License

MIT
