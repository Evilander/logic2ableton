# Roadmap

This roadmap tracks the next major phases for `logic2ableton`, with emphasis on reliable audio transfer first and deeper DAW-state parity second.

## Guiding Priorities

1. Correctness first: do not generate malformed `.als`.
2. Deterministic transfer: same input should produce stable output.
3. Audio parity before MIDI/plugin parity.
4. Keep a strong test baseline for every feature expansion.

## Current Baseline (Completed)

- Template-based Ableton set generation from `DefaultLiveSet.als`
- Track creation and clip injection into Arrangement View
- Timeline placement from WAV/AIFF embedded timestamps
- Overlap resolution (comp > bounce-in-place > latest take)
- Tempo and time signature transfer
- Plugin extraction and VST3 suggestion report
- Mixer override support via JSON (`volume_db`, `pan`, `is_muted`, `is_soloed`)
- CLI template generation for mixer override files
- Automated test suite (parser, generator, CLI, report, plugin pipeline)

## Phase 1: Timeline and Session Fidelity Hardening

Goal: reduce remaining clip-placement edge cases and improve confidence on large projects.

Scope:

- Add optional debug exports for region/timestamp diagnostics
- Expand handling for files lacking embedded timestamp metadata
- Validate clip placement against additional Logic project variants
- Add regression tests from real-world problematic sessions

Exit criteria:

- Repeatable placement accuracy across reference test projects
- New placement regressions blocked by dedicated tests

## Phase 2: Native Mixer Extraction (Beyond JSON Overrides)

Goal: parse mixer values directly from Logic `ProjectData` when possible.

Scope:

- Reverse-engineer track-level level/pan/mute/solo structures in binary payload
- Map extracted mixer entities to discovered track names robustly
- Merge strategy between parsed mixer state and user-provided JSON overrides
- Validation harness comparing extracted values with known reference sessions

Exit criteria:

- Best-effort automatic mixer extraction enabled by default
- JSON overrides remain available as explicit override layer

## Phase 3: Routing and Bus Topology Transfer

Goal: preserve more of the production mix graph.

Scope:

- Detect aux/return buses in Logic structures
- Reconstruct sends and return routing in Ableton-compatible form
- Preserve send amounts where extractable
- Report unresolved routing with explicit fallback guidance

Exit criteria:

- Send/return network generated for supported project patterns
- Conversion report clearly lists preserved vs. unresolved routing

## Phase 4: Automation Transfer

Goal: transfer practical automation lanes with acceptable fidelity.

Scope:

- Identify and decode volume/pan automation from Logic data
- Map automation events to Ableton XML envelope structures
- Implement interpolation strategy and timebase conversion
- Add per-lane fallback reporting where mapping is unsupported

Exit criteria:

- Volume and pan automation transfers for supported projects
- Stable import in Ableton without schema errors

## Phase 5: MIDI and Instrument Track Transfer

Goal: move beyond audio-only conversions.

Scope:

- Extract MIDI note events and timing from Logic data
- Create corresponding MIDI clips in Ableton tracks
- Handle quantization/timebase and tempo map interactions
- Optional instrument placeholder strategy with report-driven replacement hints

Exit criteria:

- MIDI clips present with correct note timing in supported sessions
- Failure modes are explicit and non-destructive

## Phase 6: Cross-Platform and Packaging

Goal: improve usability and portability.

Scope:

- Decouple template path discovery from Windows-only defaults
- Add configuration for custom Ableton template paths
- Create distributable package and versioned release process
- Add CI pipeline with lint/test matrix

Exit criteria:

- Documented setup on Windows/macOS
- Reproducible release artifacts and CI-gated merges

## Stretch Goals

- Batch conversion for multiple Logic projects
- Optional GUI wrapper
- DAW-neutral intermediate model for future exporters
- Round-trip diagnostics package (input fingerprints + output assertions)

## Maintenance Rules

- Every new transfer feature must include:
  - parser tests
  - generator tests
  - at least one end-to-end CLI test
- Backward compatibility is required for existing CLI flags.
- Breaking changes must be documented in `README.md` with migration notes.
