"""Tempo map and markers loaded from an external --timeline JSON file.

Logic Pro projects can carry tempo changes and markers that the .logicx
parser does not extract yet. Until that lands, --timeline lets a user
supply the same information by hand, positioned in bars/beats against the
project's base time signature. A Timeline only carries the raw, validated
events; TempoMap does the actual beat/second math against them.
"""

import json
import math
from dataclasses import dataclass
from pathlib import Path

# A tempo/marker map should never legitimately be this large; anything past
# it is almost certainly the wrong file, so reject before reading it fully.
_MAX_TIMELINE_BYTES = 5 * 1024 * 1024


@dataclass
class TempoEvent:
    beat: float
    bpm: float


@dataclass
class TimelineMarker:
    beat: float
    name: str


@dataclass
class Timeline:
    tempo_events: list[TempoEvent]
    markers: list[TimelineMarker]
    source_path: str = ""


def beats_per_bar(numerator: int, denominator: int) -> float:
    """Length of one bar in quarter-note beats (numerator * 4 / denominator)."""
    return numerator * 4.0 / max(1, denominator)


def _resolve_beat(entry: dict, *, bar_length_beats: float, label: str) -> float:
    """Resolve a JSON position entry ('bar'[+'beat'] or 'beats') to a beat offset."""
    has_bar = "bar" in entry
    has_beats = "beats" in entry
    if has_bar and has_beats:
        raise ValueError(f"{label} specifies both 'bar' and 'beats': {entry}")

    if has_beats:
        beats = entry["beats"]
        if isinstance(beats, bool) or not isinstance(beats, (int, float)):
            raise ValueError(f"{label} has a non-numeric 'beats': {entry}")
        if math.isnan(beats) or math.isinf(beats):
            raise ValueError(f"{label} has a non-finite 'beats': {entry}")
        if beats < 0:
            raise ValueError(f"{label} has a negative 'beats': {entry}")
        return float(beats)

    if has_bar:
        bar = entry["bar"]
        if isinstance(bar, bool) or not isinstance(bar, int) or bar < 1:
            raise ValueError(f"{label} has an invalid 'bar' (must be a positive integer): {entry}")
        beat_in_bar = entry.get("beat", 1)
        if isinstance(beat_in_bar, bool) or not isinstance(beat_in_bar, (int, float)):
            raise ValueError(f"{label} has an invalid 'beat' (must be a number >= 1): {entry}")
        if math.isnan(beat_in_bar) or math.isinf(beat_in_bar):
            raise ValueError(f"{label} has a non-finite 'beat': {entry}")
        if beat_in_bar < 1 or beat_in_bar > bar_length_beats:
            raise ValueError(
                f"{label} has a 'beat' outside the bar's length (1 to {bar_length_beats}): {entry}"
            )
        return (bar - 1) * bar_length_beats + (beat_in_bar - 1)

    raise ValueError(f"{label} is missing a position ('bar' or 'beats'): {entry}")


def load_timeline(path: Path, *, numerator: int, denominator: int, base_tempo: float) -> Timeline:
    """Load and strictly validate a --timeline JSON file.

    ``numerator``/``denominator`` give the project's base time signature,
    used to convert 'bar'+'beat' positions to beats (one bar = numerator *
    4 / denominator beats). ``base_tempo`` is the project's own tempo,
    carried alongside the loaded events for callers building a TempoMap
    from this Timeline.

    Tempo events are sorted by beat; duplicates at the same beat keep the
    last one in the file. Unknown JSON keys are ignored.
    """
    path = Path(path)
    if path.stat().st_size > _MAX_TIMELINE_BYTES:
        raise ValueError(f"Timeline file is too large (max {_MAX_TIMELINE_BYTES} bytes): {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Timeline file must contain a JSON object: {path}")

    bar_length_beats = beats_per_bar(numerator, denominator)

    tempo_raw = raw.get("tempo") if raw.get("tempo") is not None else []
    if not isinstance(tempo_raw, list):
        raise ValueError(f"Timeline 'tempo' must be a list: {tempo_raw!r}")

    tempo_events: list[TempoEvent] = []
    for entry in tempo_raw:
        if not isinstance(entry, dict):
            raise ValueError(f"Tempo entry must be an object: {entry}")
        beat = _resolve_beat(entry, bar_length_beats=bar_length_beats, label="Tempo entry")
        if "bpm" not in entry:
            raise ValueError(f"Tempo entry is missing 'bpm': {entry}")
        bpm = entry["bpm"]
        if isinstance(bpm, bool) or not isinstance(bpm, (int, float)):
            raise ValueError(f"Tempo entry has a non-numeric 'bpm': {entry}")
        if math.isnan(bpm) or math.isinf(bpm):
            raise ValueError(f"Tempo entry has a non-finite 'bpm': {entry}")
        if bpm <= 0:
            raise ValueError(f"Tempo entry has a non-positive 'bpm': {entry}")
        tempo_events.append(TempoEvent(beat=beat, bpm=float(bpm)))

    markers_raw = raw.get("markers") if raw.get("markers") is not None else []
    if not isinstance(markers_raw, list):
        raise ValueError(f"Timeline 'markers' must be a list: {markers_raw!r}")

    markers: list[TimelineMarker] = []
    for entry in markers_raw:
        if not isinstance(entry, dict):
            raise ValueError(f"Marker entry must be an object: {entry}")
        beat = _resolve_beat(entry, bar_length_beats=bar_length_beats, label="Marker entry")
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError(f"Marker entry is missing a 'name': {entry}")
        markers.append(TimelineMarker(beat=beat, name=name))

    tempo_events.sort(key=lambda event: event.beat)
    deduped_tempo: list[TempoEvent] = []
    for event in tempo_events:
        if deduped_tempo and deduped_tempo[-1].beat == event.beat:
            deduped_tempo[-1] = event
        else:
            deduped_tempo.append(event)

    markers.sort(key=lambda marker: marker.beat)

    return Timeline(tempo_events=deduped_tempo, markers=markers, source_path=str(path))


class TempoMap:
    """A piecewise-constant tempo curve: base_tempo from beat 0 until the
    first breakpoint, then each breakpoint holds (a step, not a ramp) until
    the next one. A breakpoint at beat <= 0 overrides the base tempo itself.
    """

    def __init__(self, base_tempo: float, events: list[TempoEvent]):
        self.base_tempo = base_tempo
        self.events = sorted(events, key=lambda event: event.beat)

    def bpm_at(self, beat: float) -> float:
        bpm = self.base_tempo
        for event in self.events:
            if event.beat > beat:
                break
            bpm = event.bpm
        return bpm

    def _segments(self) -> list[tuple[float, float | None, float]]:
        """Ordered (start_beat, end_beat_or_None, bpm) spans; the last is open-ended."""
        start = 0.0
        bpm = self.base_tempo
        segments: list[tuple[float, float | None, float]] = []
        for event in self.events:
            if event.beat <= 0:
                bpm = event.bpm
                continue
            segments.append((start, event.beat, bpm))
            start = event.beat
            bpm = event.bpm
        segments.append((start, None, bpm))
        return segments

    def beats_to_seconds(self, beats: float) -> float:
        segments = self._segments()
        first_start, _, first_bpm = segments[0]
        if beats <= first_start:
            # Extrapolate backward through the first segment's tempo instead
            # of clamping to 0, so this stays the inverse of seconds_to_beats.
            return (beats - first_start) * 60.0 / first_bpm
        seconds = 0.0
        for start, end, bpm in segments:
            if beats <= start:
                break
            seg_end = beats if end is None else min(end, beats)
            seconds += (seg_end - start) * 60.0 / bpm
            if end is None or beats <= end:
                break
        return seconds

    def seconds_to_beats(self, seconds: float) -> float:
        elapsed = 0.0
        for start, end, bpm in self._segments():
            if end is None:
                return start + (seconds - elapsed) * bpm / 60.0
            seg_seconds = (end - start) * 60.0 / bpm
            if seconds <= elapsed + seg_seconds:
                return start + (seconds - elapsed) * bpm / 60.0
            elapsed += seg_seconds
        raise AssertionError("unreachable: the last segment is always open-ended")

    def samples_to_beats(self, samples: float, sample_rate: int) -> float:
        if not self.events:
            # Bit-for-bit with models.samples_to_beats' single-tempo formula.
            return samples * self.base_tempo / (sample_rate * 60)
        return self.seconds_to_beats(samples / sample_rate)

    def breakpoints_between(self, start_beat: float, end_beat: float) -> list[TempoEvent]:
        return [event for event in self.events if start_beat < event.beat < end_beat]
