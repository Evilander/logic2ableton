"""Standard MIDI File helpers shared by both transfer lanes.

`build_midi_note_file` is duck-typed: it accepts any track object exposing a
``notes`` iterable whose items have ``pitch``, ``velocity``, ``start_beats``,
and ``duration_beats`` attributes (both AbletonMidiTrack and LogicMidiTrack).
"""

from __future__ import annotations

import struct

MIDI_TICKS_PER_QUARTER = 960


def write_var_len(value: int) -> bytes:
    buffer = value & 0x7F
    encoded = bytearray([buffer])
    value >>= 7
    while value:
        buffer = (value & 0x7F) | 0x80
        encoded.insert(0, buffer)
        value >>= 7
    return bytes(encoded)


def tempo_meta(tempo: float) -> bytes:
    mpqn = int(round(60_000_000 / max(1e-6, tempo)))
    return b"\x00\xff\x51\x03" + mpqn.to_bytes(3, "big")


def time_signature_meta(numerator: int, denominator: int) -> bytes:
    denominator_power = 0
    denom = max(1, denominator)
    while (1 << denominator_power) < denom and denominator_power < 7:
        denominator_power += 1
    return b"\x00\xff\x58\x04" + bytes([max(1, numerator), denominator_power, 24, 8])


def wrap_midi_track(track_data: bytes) -> bytes:
    track = b"MTrk" + struct.pack(">I", len(track_data)) + track_data
    header = b"MThd" + struct.pack(">IHHH", 6, 0, 1, MIDI_TICKS_PER_QUARTER)
    return header + track


def beats_to_ticks(beats: float) -> int:
    return max(0, int(round(beats * MIDI_TICKS_PER_QUARTER)))


def build_midi_note_file(track, *, tempo: float, numerator: int, denominator: int) -> bytes:
    """Build a self-contained Type-0 SMF with the track's notes, tempo, and time signature."""
    track_data = bytearray()
    name_bytes = track.name.encode("utf-8")[:255]
    track_data.extend(b"\x00\xff\x03" + write_var_len(len(name_bytes)) + name_bytes)
    track_data.extend(tempo_meta(tempo))
    track_data.extend(time_signature_meta(numerator, denominator))

    # (tick, ordering, status, pitch, velocity). Note-offs (ordering 0) sort
    # before note-ons at the same tick so back-to-back same-pitch notes do not hang.
    events: list[tuple[int, int, int, int, int]] = []
    for note in track.notes:
        on_tick = beats_to_ticks(note.start_beats)
        off_tick = max(on_tick + 1, beats_to_ticks(note.start_beats + note.duration_beats))
        events.append((on_tick, 1, 0x90, note.pitch, note.velocity))
        events.append((off_tick, 0, 0x80, note.pitch, 0))
    events.sort(key=lambda event: (event[0], event[1]))

    previous_tick = 0
    for tick, _ordering, status, pitch, velocity in events:
        track_data.extend(write_var_len(tick - previous_tick))
        track_data.extend(bytes([status, pitch & 0x7F, velocity & 0x7F]))
        previous_tick = tick

    track_data.extend(b"\x00\xff\x2f\x00")
    return wrap_midi_track(bytes(track_data))
