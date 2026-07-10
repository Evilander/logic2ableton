# Inside the Pro Tools session container

Pro Tools session files (`.ptx`, and the older `.pts`/`.ptf`) have no public
specification. This project ships a clean-room Python parser for them
(`logic2ableton/protools_parser.py`). This document describes the format as
implemented and how the implementation was verified.

**Credit where it belongs:** the byte-level facts below were originally
established by Damien Zammit's [ptformat](https://github.com/zamaudio/ptformat)
project, which reverse-engineered the container for Ardour's session importer.
Our parser is an independent MIT-licensed implementation built from those
documented facts — no code was copied — and re-verified against real sessions.

## The container in one picture

```text
offset 0x00  03                          file signature
offset 0x01  "0010111100101011"          16-char ASCII bitcode magic
offset 0x11  endianness flag             0 = little-endian (all modern saves)
offset 0x12  XOR scheme id               0x01 = PT 5-9, 0x05 = PT 10+
offset 0x13  XOR seed byte
offset 0x14  obfuscated block stream ...
```

## The XOR obfuscation

Everything after the 20-byte header is XORed with a 256-byte key stream:

```text
key[i] = (i * delta) & 0xFF
```

`delta` is derived from the seed byte: it is the value `i` (negated modulo 256
for PT 10+) that satisfies `(i * multiplier) & 0xFF == seed`, where the
multiplier is 53 for the PT 5-9 scheme and 11 for PT 10+.

The schemes differ in how the key is indexed:

- **PT 5-9:** by byte offset — `key[offset & 0xFF]`
- **PT 10+:** by 4 KiB page — `key[(offset >> 12) & 0xFF]`

A fun consequence of the page-indexed scheme: `key[0]` is always zero, so the
first 4 KiB of every `.ptx` is effectively plaintext, and depending on the
seed the key stream can be periodic — the real session we tested against had
`delta = 0x60`, which makes every eighth page plaintext too. If you ever
hex-dump a `.ptx` and see readable strings early on followed by apparent
garbage, this is why.

## The block tree

The decoded content is a stream of nestable blocks:

```text
+0  0x5A                 marker ("Z")
+1  u16  block type      (high byte always 0 — used as a validity check)
+3  u32  block size      (counts from the content-type field)
+7  u16  content type    (what the block means)
+9  data ...             (may itself contain child blocks)
```

Walking is straightforward: scan from offset 20, and inside every block's data
region, scan for child blocks recursively. The content types this project
consumes:

| Content type | Meaning |
| --- | --- |
| `0x2067` | Session path info; also encodes the format version |
| `0x1028` | Session sample rate |
| `0x1004` > `0x103a` | Audio file (wav) name list |
| `0x1004` > `0x1003` > `0x1001` | Per-file frame lengths |
| `0x262a` > `0x2629` | Audio region definitions (PT 10+; `0x100b`/`0x1008` before) |
| `0x1054` > `0x1052` > `0x1050` > `0x104f` | Region-onto-track placements per lane |
| `0x2000` | Raw MIDI event chunks (`MdNLB` marker) |
| `0x2634` > `0x2633` | MIDI region map (PT 10+) |
| `0x2519` > `0x251a` | MIDI track names |
| `0x1058` > `0x1057` > `0x1056` > `0x104f` | MIDI region-onto-track placements |

## The "three-point" region encoding

Audio regions are `(start, source offset, length)` triples in samples, but the
values are stored with variable widths. A five-byte descriptor declares each
value's byte count in the high nibbles of bytes 1–3, and the values follow
back-to-back, always little-endian. A region that plays 8.6 seconds from the
middle of a 3-minute take costs only as many bytes as its numbers need.

Placements (`0x104f`) re-anchor a region on a specific track lane with a fresh
start position, which is what actually matters for the timeline. A flag byte
at offset 46 of the `0x1050` wrapper marks crossfade render files — those are
skipped so fades don't appear as phantom clips.

## Stereo tracks are two lanes

Pro Tools stores a stereo audio track as two mono lanes that share the track
name and reference the same interleaved source file, with `.L`/`.R` suffixes
on the region names. The importer merges those lanes back into single tracks
and strips the channel suffixes.

## MIDI

MIDI note events live in fixed 35-byte records after an `MdNLB` marker:
position (u40 ticks), pitch (byte 8), length (u40 at byte 9), velocity
(byte 17). Positions are relative to the first event of the chunk, and region
placements are u40 tick values offset from the epoch `0xE8D4A51000` (10^12).
Pro Tools ticks are **960,000 per quarter note** — a thousand times finer than
a typical MIDI file — which conveniently means note timing converts to beats
with no tempo knowledge at all.

## What is not decoded (yet)

- **Session tempo and meter map.** Nothing in the block types above carries
  it; conversions assume a tempo (CLI `--tempo`) and say so in the report.
  Audio placement is sample-exact regardless.
- Plugins, inserts, sends, automation, clip gain, fade shapes.
- Elastic Audio state.

## How it was verified

1. **A real session:** a Pro Tools 2023 (`format version 12`) 96 kHz studio
   session with comped vocals, stereo keys lanes, and full-length prints.
   Every parsed value was checked for internal coherence — file frame lengths
   against the session length, mirrored L/R lanes, sequential comp segments,
   trim offsets versus source lengths — and the converted Ableton set
   round-trips to sample-exact positions.
2. **A synthetic fixture built in CI:** the test suite assembles a `.ptx`
   byte-by-byte from the layout above (including XOR obfuscation with content
   pushed past the plaintext first page) and asserts the parser recovers
   exactly what was encoded — audio, trims, placements, and MIDI.

Fixture-gated tests run automatically when a local `.ptx` is available
(`L2A_PTX_FIXTURE` environment variable).
