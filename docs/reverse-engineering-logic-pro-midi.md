# Reverse-engineering Logic Pro's binary project format to extract MIDI

*How I pulled note-accurate MIDI out of an undocumented, proprietary binary file — and verified it against Apple's own shipping demo songs.*

---

## The problem nobody had solved

I write music in Logic Pro and collaborate with people who live in Ableton Live. Moving a session between the two is miserable: there is no shared project format, no exporter, and no third-party tool that does it well. So I built one — [logic2ableton](https://github.com/Evilander/logic2ableton).

Audio was the easy half. Logic stamps recorded audio with timeline positions (BWF `bext` time references in WAV, `MARK` chunks in AIFF), so I could reconstruct an arrangement from timestamps alone. The hard half was MIDI.

A Logic project's MIDI doesn't live in a sidecar file. It lives inside `ProjectData` — a single, undocumented, proprietary binary blob. There is no public spec. Apple has never published one. As far as I can tell, no other tool extracts note data from it. This is the story of how I cracked it, and how I proved I got it right.

## Anatomy of a `.logicx` bundle

A Logic project is a macOS package — a directory that Finder presents as a single file. Inside:

```
MySong.logicx/
  Resources/
    ProjectInformation.plist     # which "alternative" is active
  Alternatives/
    000/
      MetaData.plist             # tempo, time signature, sample rate, track count
      ProjectData                # <-- the binary blob. everything lives here.
    004/                         # alternatives are numbered arbitrarily, not 0..N
```

The first surprise: **alternatives are not numbered sequentially from zero.** A project whose only alternative is `004` is completely normal. My first parser hardcoded `Alternatives/000` and silently failed on real projects. The fix was to read `ActiveVariant` from `ProjectInformation.plist`, then fall back to scanning for whatever folders actually contain a `ProjectData`.

The `.plist` files are standard Apple property lists — `plistlib` reads them in three lines. Tempo, time signature, sample rate, and the software-instrument file references (which tell you a project *has* MIDI tracks before you ever open the binary) all come from `MetaData.plist`. Easy.

`ProjectData` is where it gets interesting.

## Free wins hiding in the binary

Before the hard part, a useful discovery: Logic embeds **plugin configurations as literal XML plists** inside the binary stream. You can find them by scanning for `<?xml version` and reading to the matching `</plist>`:

```python
for match in re.finditer(rb"<\?xml version", data):
    start = match.start()
    end = data.find(b"</plist>", start) + len(b"</plist>")
    parsed = plistlib.loads(data[start:end])   # name, manufacturer, type, subtype
```

That gave me every plugin instance — name and Audio Unit four-character codes — for free, which the converter turns into VST3 suggestions in its report. But notes are not stored as XML. Notes are packed binary, and finding them meant a different approach entirely.

## The detective work

Opening `ProjectData` in a hex editor is a wall of bytes. The format is chunked: little-endian FourCC tags mark regions. Reversed in memory (little-endian), you can spot `qSvE` (`EvSq` — an event sequence), `qeSM` (`MSeq` — a MIDI sequence), and `karT` (`Trak`). So MIDI notes live somewhere after a `qSvE` marker. But *where*, and in *what layout*?

Staring at hex won't tell you which bytes are pitch and which are duration. You need a **controlled experiment**: a project where you already know every note, so you can find those known values in the bytes.

Here's the method I landed on, and the part I'm proudest of:

1. **Generate** a Standard MIDI File with notes I chose deliberately — distinctive pitches (61, 73, 85), distinctive velocities (99, 77, 111), and positions exactly one beat apart. Nothing repeated, so every value is a fingerprint.
2. **Drive Logic Pro by automation** to import that `.mid` and save a `.logicx`. (macOS GUI scripting — synthetic input events into Logic, then save.)
3. **Diff the resulting `ProjectData`** against my known note values. Search the bytes for `99`, for `61`, for the tick positions I expect — and watch where they land relative to each other.

That third step is what turns guesswork into a spec. When you know the answer, the structure reveals itself.

## The note record

What fell out: every note is a fixed-layout record anchored by a 15-byte signature that never varies between notes:

```
00 00 01 00 00 00 00 00 00 00 89 00 00 00 00
```

Once you can find that signature, everything else is at a fixed offset from it. Calling the signature's start position `S`:

```
offset      field
S-9 .. S-6  note start position   (uint32, little-endian, ticks)
S-2         velocity              (1 byte, 1-127)
S-1         pitch                 (1 byte, 0-127)
S .. S+14   the 15-byte signature
S+15 .. S+18 note duration        (uint32, little-endian, ticks)
```

Logic runs at **960 ticks per quarter note**. Positions are absolute ticks on Logic's internal timeline, so I normalize every note against the earliest note in the project — that preserves the relative timing between regions without needing to decode Logic's bar-1 origin. The extraction core is just:

```python
SIGNATURE = bytes([0,0,1,0,0,0,0,0,0,0,0x89,0,0,0,0])

i = 0
while (i := data.find(SIGNATURE, i)) >= 0:
    i += 15
    velocity = data[i-17]                          # S-2
    pitch    = data[i-16]                           # S-1
    duration = struct.unpack_from("<I", data, i)[0]      # S+15
    position = struct.unpack_from("<I", data, i-24)[0]   # S-9
    if 0 <= pitch <= 127 and 1 <= velocity <= 127:
        ...  # a real note
```

## Guarding against false positives

A 15-byte signature is fairly specific, but a binary scan *will* occasionally match those bytes by coincidence in unrelated data. Two cheap range checks kill almost all false hits: a real note has pitch in `0–127` and velocity in `1–127` (a velocity of 0 is a note-off, not a note). I also reject absurd durations and positions. Out-of-range matches are simply skipped — no note, no crash.

## Grouping notes back into regions

Notes belong to sequences (Logic's MIDI regions), and I wanted to preserve that grouping rather than dumping one flat note soup. Since the `qSvE` markers and the note records are all just offsets into the same byte stream, I record every sequence-marker offset, then assign each note to the **most recent marker before it** using a binary search:

```python
seq_index = bisect.bisect_right(seq_offsets, note_offset) - 1
```

Each surviving sequence becomes one track, exported as its own Standard MIDI File. The notes within a region carry correct relative timing; chords (multiple notes sharing a start tick) come through intact.

## Proving it — the part that matters

Reverse-engineering is worthless if you can't show it's *correct*. I verified at two levels.

**Synthetic round-trip.** I build a `ProjectData` blob from known notes, extract it back, and assert the pitches, velocities, starts, and durations match exactly — including chords (three notes on the same tick) and out-of-range records that must be rejected. This runs in CI on every commit, on Windows and macOS, across Python 3.11–3.13.

**Real-world ground truth.** The convincing test: I ran the extractor against **Apple's own bundled Logic demo projects** — the Live Loops grids that ship inside Logic Pro Library. No synthetic data, no projects I made:

| Apple demo project | MIDI sequences | Notes extracted |
| --- | --- | --- |
| Solaris | 3 | 74 |
| Tom Misch | 5 | 52 |
| Neon Dreams | 7 | 47 |

The notes aren't just *present* — they're *musically coherent*. Solaris's kick lands on velocity 127 every two beats with quarter-note-ish durations: a textbook four-on-the-floor pattern, exactly what you'd expect from the source. The full pipeline turns each one into a valid Ableton `.als` (gzipped XML, parses clean) plus importable `.mid` files (well-formed SMF, Type-0, correct note-on/note-off pairs).

If it decodes Apple's own files correctly, it decodes yours.

## What this unlocks

logic2ableton now moves MIDI in both directions — Logic notes out to Standard MIDI Files, and Ableton arrangement notes back into Logic-ready packages — on top of audio-timeline reconstruction, tempo, time signature, per-track colors, and plugin identification. It's a real tool people use, not a demo.

But the transferable part isn't the music. It's the method: take an opaque, undocumented binary; build a controlled experiment that turns unknowns into fingerprints; decode the structure; and *prove* the decode against ground truth you don't control. That same loop works on any proprietary format — and most of the valuable data in the world is locked in one.

---

*[logic2ableton](https://github.com/Evilander/logic2ableton) is open source (MIT). The MIDI extractor lives in [`logic_parser.py`](https://github.com/Evilander/logic2ableton/blob/master/logic2ableton/logic_parser.py); the verification tests are in [`tests/test_forward_midi.py`](https://github.com/Evilander/logic2ableton/blob/master/tests/test_forward_midi.py).*
