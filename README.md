# logic2ableton

Convert Logic Pro `.logicx` projects into Ableton Live `.als` sets with preserved audio arrangement timing, track structure, tempo/time signature, and plugin replacement suggestions.

## Status

This project is functional for audio-centric Logic sessions and currently focuses on:

- Audio track and clip transfer into Ableton Arrangement View
- Timeline placement from embedded WAV/AIFF timestamps
- Basic mixer transfer (volume, pan, mute, solo) via JSON overrides
- Plugin discovery and VST3 suggestion reporting

It does not yet transfer automation, MIDI, or full routing topology.

## What It Transfers

- Project name
- Tempo and time signature
- Audio file inventory from `Media/Audio Files`
- Track names inferred from Logic audio naming conventions
- Clip timeline positions from embedded audio metadata:
  - WAV: BWF `bext` TimeReference
  - AIFF: `MARK` chunk `Timestamp` + `Start` markers
- Clip overlap selection per track:
  - comp > bounce-in-place (`_bip`) > latest take
- Ableton mixer values when provided through a mixer JSON file:
  - volume (dB -> linear)
  - pan
  - mute
  - solo
- Plugin report with category-based VST3 alternatives

## What It Does Not Transfer Yet

- Plugin parameter state
- Automation lanes/curves
- Bus/send routing graph reconstruction
- MIDI notes and instrument tracks
- Folder/grouping semantics

## Requirements

- Windows or macOS
- Ableton Live 12 installed locally (for `DefaultLiveSet.als` template)
- Optional: VST3 plugins in `C:/Program Files/Common Files/VST3`

## Installation

### Standalone executable (no Python required)

Download the latest release from [GitHub Releases](https://github.com/Evilander/logic2ableton/releases):

- **Windows**: `logic2ableton-windows-x64.exe`
- **macOS**: `logic2ableton-macos-x64`

### Via pip

```bash
pip install logic2ableton
```

### From source

```bash
git clone https://github.com/Evilander/logic2ableton.git
cd logic2ableton
pip install -e .
```

No third-party Python dependencies are required.

## Quick Start

### Convert a project

```bash
logic2ableton "path/to/MySong.logicx" --output ./output
```

### Generate report only

```bash
logic2ableton "path/to/MySong.logicx" --report-only
```

### Convert without copying audio

```bash
logic2ableton "path/to/MySong.logicx" --output ./output --no-copy
```

## CLI Reference

```text
logic2ableton <input.logicx> [options]
```

Options:

- `--version`, `-V`: show version
- `--output`, `-o`: output directory (default `.`)
- `--alternative`, `-a`: Logic alternative index (default `0`)
- `--no-copy`: do not copy audio into Ableton project
- `--report-only`: print conversion report only
- `--template`: path to Ableton `DefaultLiveSet.als` template (auto-detected if omitted)
- `--vst3-path`: VST3 directory for suggestion scanning
- `--mixer`: path to `mixer_overrides.json`
- `--generate-mixer-template`: emit template mixer JSON for all discovered tracks
- `--json-progress`: output machine-readable JSON progress lines (for GUI integration)

## Mixer Override Workflow

1. Generate a template:

```bash
python -m logic2ableton "A:\path\to\MySong.logicx" --output .\output --generate-mixer-template --report-only
```

2. Edit `output\mixer_overrides.json`:

```json
{
  "KICK IN": {
    "volume_db": -3.0,
    "pan": 0.0,
    "is_muted": false,
    "is_soloed": false
  },
  "SNARE": {
    "volume_db": -6.0,
    "pan": 0.2,
    "is_muted": false,
    "is_soloed": false
  }
}
```

3. Apply during conversion:

```bash
python -m logic2ableton "A:\path\to\MySong.logicx" --output .\output --mixer .\output\mixer_overrides.json
```

## Output Structure

For project `MySong.logicx` and output directory `output`:

```text
output/
  MySong Project/
    MySong.als
    Samples/
      Imported/
        *.wav / *.aif / *.aiff / ...
  MySong_conversion_report.txt
```

## Architecture

- `logic2ableton/models.py`: core dataclasses and filename parsing
- `logic2ableton/logic_parser.py`: Logic metadata parsing, plugin extraction, region timing extraction
- `logic2ableton/ableton_generator.py`: template-driven Ableton set generation and clip injection
- `logic2ableton/plugin_database.py`: AU plugin lookup table
- `logic2ableton/vst3_scanner.py`: local VST3 discovery and heuristic categorization
- `logic2ableton/plugin_matcher.py`: Logic AU -> VST3 candidate matching
- `logic2ableton/report.py`: conversion report generation
- `logic2ableton/cli.py`: command-line interface

## Testing

Run all tests:

```bash
python -m pytest tests -q
```

The suite currently validates parser behavior, clip placement, mixer application, CLI flows, reporting, and plugin matching/scanning.

## Troubleshooting

### Ableton template not found

If you see a `DefaultLiveSet.als` template error, ensure Ableton Live 12 is installed in one of:

- `C:/ProgramData/Ableton/Live 12 Suite/Resources/Builtin/Templates/`
- `C:/ProgramData/Ableton/Live 12 Standard/Resources/Builtin/Templates/`
- `C:/ProgramData/Ableton/Live 12 Intro/Resources/Builtin/Templates/`
- `C:/ProgramData/Ableton/Live 12 Trial/Resources/Builtin/Templates/`

### Clips start at 0 unexpectedly

Some imported/reference files do not include timestamp metadata. These will default to start at 0 unless timeline reconstruction from Logic `ProjectData` is added in a future release.

### Plugin suggestions are empty

Set `--vst3-path` to your VST3 install location, or ensure plugins are available in the default path.

## Contributing

1. Create a branch from `master`
2. Keep changes focused and covered by tests
3. Run `python -m pytest tests -q`
4. Open a pull request with a clear before/after summary

## Roadmap

See `ROADMAP.md` for milestone-level planning and priorities.
