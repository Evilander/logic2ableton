# Handoff: deficiencies and improvement plan

State as of **v2.1.0** (2026-07-10). This document is the honest inventory of what
is weak, unverified, or deferred — with evidence pointers and acceptance criteria
so any thread can be picked up cold. Feature phases live in [ROADMAP.md](ROADMAP.md);
this file is about quality debts and how to retire them.

## Verify the current state first

```bash
python -m pytest tests/ -q          # 153 passed, 46 skipped (fixture-gated) expected
ruff check logic2ableton tests      # 8 pre-existing errors expected (see P0-2)
cd app && npm run build             # electron-vite build must pass clean
```

Fixture-gated tests light up when local sessions exist: a reference `.logicx`
next to the repo root (`needs_test_project`), a VST3 directory (`needs_vst3`),
and a Pro Tools session via `L2A_PTX_FIXTURE` (`needs_ptx_fixture`).

---

## P0 — Correctness and honesty gaps

### P0-1: Pro Tools session tempo is not decoded

**Deficiency.** All four PT lanes assume a tempo (`--tempo`, default 120). Audio
placement stays sample-exact regardless, but beat positions in generated `.als`
sets only line up when the destination tempo matches the flag.

**Where.** `logic2ableton/protools_parser.py` (nothing parses tempo),
`protools_import.py::_tempo_warning`, CLI `--tempo` plumbing in `cli.py`.

**Leads.** The reference reverse-engineering (ptformat) never solved this either.
Unparsed content types observed in a real PT2023 v12 session that are candidates:
`0x2511` ("Snaps"), `0x2038` (×13), `0x230b` (×26), `0x2589`, `0x258e`. Method
that works (proven on the Logic format): save sessions with *known distinct
tempos* differing by nothing else, diff the decoded block streams, locate the
float/int that moves. Requires access to Pro Tools or donated fixture pairs.

**Acceptance.** Tempo parsed correctly on ≥2 real sessions of different PT
versions; `--tempo` demoted to an override; the tempo warning only appears when
decoding fails. Add the found layout to `docs/reverse-engineering-pro-tools-sessions.md`.

### P0-2: CI has no lint or typecheck step (and 8 ruff errors live on master)

**Deficiency.** `.github/workflows/release.yml` runs pytest + builds only. Ruff
is used locally but not enforced; 8 pre-existing errors (3 auto-fixable: unused
imports, `E741` ambiguous names) sit on master. The Electron app's TypeScript is
only checked implicitly via `electron-vite build`.

**Fix.** One commit: `ruff check --fix` the trivial ones, hand-fix the rest,
then add a `lint` job (ruff + `npm run build` or `tsc --noEmit` for `app/`) to
the workflow and make `test` need it.

**Acceptance.** `ruff check logic2ableton tests` clean; CI fails on new lint errors.

### P0-3: The critical ID-uniqueness invariant is not tested in CI

**Deficiency.** `test_generate_als_unique_critical_ids` (AutomationTarget /
ModulationTarget / Pointee must be globally unique or Ableton corrupts sets) is
gated on a private reference `.logicx` that CI does not have. The single most
dangerous generator invariant currently relies on local runs.

**Fix.** Add a synthetic variant: build a `LogicProject` in-test (audio refs via
`tests/conftest.py::write_test_wav` + MIDI via `tests/test_native_midi.py`
helpers), generate with the bundled template, assert uniqueness across those
three tags. No private fixture needed.

**Acceptance.** The invariant test runs green in CI on every push.

### P0-4: Pro Tools MIDI is validated only synthetically

**Deficiency.** The `.ptx` MIDI decode path (MdNLB event records, region maps,
track placement — `protools_parser.py::_parse_midi`) passes synthetic fixtures
built from the documented layout, but no *real* PT session with MIDI has been
through it. The one real fixture used for v2.1.0 validation is audio-only.
Related known approximation: MIDI regions anchor to their first note — a
region's leading silence is dropped (`_parse_midi`, and the three-point
`region_pos` in `0x2633` entries is parsed but unused).

**Fix.** Obtain 1–2 real PT sessions containing MIDI (any collaborator with Pro
Tools can export one in minutes: a few named MIDI tracks, known note content,
known positions). Verify pitch/velocity/position/duration against ground truth;
then wire `region_pos` through so leading-silence offsets survive.

**Acceptance.** A fixture-gated test asserting exact known notes from a real
session; region offset preserved; docs updated.

### P0-5: Older Logic saves' MIDI is detected but not decoded

**Deficiency.** Projects from older Logic versions use a different note-record
variant (flag byte `0x69`/`0x88` where current saves have `0x89`). We now report
this honestly ("no MIDI notes could be decoded... older Logic save format") but
transfer nothing. Sequence counts suggest real content exists in such projects.

**Leads.** `logic2ableton/logic_parser.py::_MIDI_NOTE_SIGNATURE`; ROADMAP Phase 6
notes. The blocker is ground truth: reverse-engineering the variant from a
project whose true note content is unknown violates the repo's #1 rule. The
clean path is a donor project saved by both an old and a current Logic version
with identical content, then diff.

**Acceptance.** Variant decoded and verified against a ground-truth pair, or
explicitly closed as won't-fix with the detection warning kept.

---

## P1 — Validation depth, release ops

### P1-1: v2.1.0 is not on PyPI (publish is manual)

CI builds installers but has no `twine` step. Either publish manually
(`python -m build && twine upload dist/*`) or add a `pypi` job on `v*` tags
using a `PYPI_API_TOKEN` secret + `pypa/gh-action-pypi-publish`. The README's
PyPI badges currently advertise an older version.

**Acceptance.** `pip install logic2ableton==2.1.0` works, or CI publishes on the
next tag.

### P1-2: Packaged-binary smoke only covers one of six lanes

The CI desktop-smoke runs the PyInstaller exe against `ableton2logic` only.
The forward lane and all four PT lanes ship as packaged binaries without a
packaged smoke. Both are now cheap to add:

- PT lanes: `tests/test_protools_parser.py::build_synthetic_ptx` can synthesize
  a session in the smoke script (or lift it into `scripts/`), then run
  `--mode protools2ableton` with the bundled template and assert the `.als` and
  report exist.
- Forward lane: `tests/test_forward_midi.py::_make_logicx` + a `write_test_wav`
  audio file synthesize a minimal `.logicx`.

**Acceptance.** Desktop-smoke exercises at least `logic2ableton`,
`ableton2logic`, `protools2ableton`, and one `*2protools` lane as a packaged exe.

### P1-3: Personal fixture path is hardcoded as a default in tests

`tests/conftest.py` and `tests/test_protools_parser.py` default
`L2A_PTX_FIXTURE` to a personal local path (committed publicly in v2.1.0).
Harmless functionally, but it doesn't belong in the repo. Switch to env-var-only
(skip when unset) and document the variable in the README's development section.

### P1-4: Loose repo artifacts

- `smoke/demo.als` is committed but stale — CI regenerates it every run. Delete.
- `build/`, `dist/`, `logic2ableton.egg-info/` exist locally as ignored dirs (fine),
  but confirm no release tarball picks them up.

---

## P2 — Structural debt (refactor before it drifts)

### P2-1: Four hand-built report builders

`report.py::generate_report` (forward), `logic_transfer.py::build_logic_transfer_report`,
`protools_transfer.py::build_protools_transfer_report`, and
`protools_import.py::build_protools_import_report` are independent
string-assembly functions with similar-but-not-identical sections. This is the
classic place where reports silently stop matching what a lane actually did.

**Fix.** Extract a small shared builder (sections: header, tracks, MIDI,
warnings, not-transferred) that each lane parameterizes. Keep the plain-text
output byte-identical where tests assert content.

### P2-2: No DAW-neutral intermediate model

Three model families now exist (`LogicProject`, `AbletonProject`,
`ProToolsSession`) plus pairwise mappers (`protools_import.py`). Adding a fourth
DAW (Reaper and Studio One both have text/XML project formats — far easier than
`.ptx`) would multiply mappers. ROADMAP already lists the neutral model under
Future Ideas; the PT mappers are the template for what it must carry: tracks,
clips with source trims, absolute-position MIDI, sample rate, tempo(s), warnings.

**Trigger.** Do this *when* a fourth format lands or when a third mapper pair is
needed — not speculatively.

### P2-3: Electron type declarations still partially duplicated

The renderer consolidated into `app/src/renderer/src/conversion.ts`, but
`ConversionDirection`/`ProgressEvent`/record shapes are still independently
declared in `app/src/main/converter.ts`, `app/src/preload/index.ts`, and
`env.d.ts`. One shared module imported by all three contexts would prevent a
seventh-lane drift. Also consider debouncing the tempo input's preview re-run
(currently race-safe via request tokens, but each change spawns a subprocess).

### P2-4: stdlib XML parsing stance

The project parses gzipped XML with `xml.etree` everywhere. External entities
are not resolved by stdlib ElementTree (no XXE), but entity-expansion DoS is
technically possible with a hostile `.als`. Zero runtime dependencies is a
deliberate virtue here; recommended stance is to document the accepted risk for
this local desktop tool and optionally add a decompressed-size guard, rather
than adding `defusedxml`.

### P2-5: Duplicated audio-header readers

`ableton_generator.py::_get_audio_info` and `logic_parser.py`'s WAV/AIFF readers
overlap; `logic_transfer.py` has its own decode stack; `protools_transfer.py`
imports private helpers from `logic_transfer.py`. A `bwf.py`/`audioinfo.py`
consolidation was consciously deferred (see the PT export lane's design notes) —
do it as its own change with the full suite as the safety net.

---

## P3 — Feature depth (user-visible improvements)

| Item | Where | Notes |
| --- | --- | --- |
| Native mixer extraction from Logic binary (volume/pan) | ROADMAP Phase 1 | Oldest open phase; IEEE-754 float hunting near LFUA markers |
| Automation transfer (volume/pan first) | ROADMAP Phase 4 | Sonic impact > routing |
| Bus/send routing → Return tracks | ROADMAP Phase 5 | Scene-count invariant applies when adding Returns |
| Logic MIDI track names | ROADMAP Phase 6 remaining | `MSeq`/`qeSM` chunk pairs 1:1 with `EvSq`, u16-length name string observed — unverified |
| PT clip gain + fade shapes | PT lanes | Fades currently skipped by design |
| Ableton looping MIDI clips: unroll repeats | `ableton_parser.py` warning path | Currently only first pass exports; bounded unroll to clip end would be strictly better |
| One Ableton `MidiClip` per PT region (not per track) | `protools_import.py` | Region boundaries are parsed; mapping currently flattens per track |
| Type-1 multi-track SMF export option | `smf.py` | Writer is Type-0 only; single-file import convenience |
| Batch conversion (`*.logicx` globs) | CLI | ROADMAP future idea |
| Product naming | app + repo | "logic2ableton" / "Logic Ableton Transfer" undersell a three-DAW tool; renaming is a marketing/breaking decision — flag only |

---

## Distribution debts

- **macOS notarization** absent (documented `xattr` workaround in README). Needs
  an Apple Developer ID; removes the scariest first-run friction.
- **Windows code signing** absent (SmartScreen warnings on the installer).
- **Intel macOS** builds absent (arm64 only).

## Working agreements worth keeping

1. Never ship reverse-engineering speculation — ground truth first (repo rule #1).
   P0-1/P0-4/P0-5 all block on fixtures for exactly this reason; acquiring donor
   sessions is the highest-leverage unblocking act on this list.
2. Reports must say what actually happened, including what *didn't* transfer.
   Any new lane/feature adds its limits to the report and README the same day.
3. Every transfer feature lands with parser tests, generator tests, and a CLI
   integration test (repo rule; the six-lane matrix in `tests/test_cli_protools.py`
   is the pattern).
4. When mirroring a DAW's file format, copy what the DAW itself writes (the
   Live 12 `MidiClip` schema and the PT block layouts were both captured from
   real files, not documentation — there is no documentation).
