import json

import pytest

from logic2ableton import timeline
from logic2ableton.models import samples_to_beats
from logic2ableton.timeline import TempoEvent, TempoMap, TimelineMarker, load_timeline


def _write(tmp_path, data):
    path = tmp_path / "timeline.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


# load_timeline


def test_load_timeline_bar_beat_4_4(tmp_path):
    path = _write(tmp_path, {"tempo": [{"bar": 1, "bpm": 120}, {"bar": 3, "beat": 2.5, "bpm": 140}]})
    timeline = load_timeline(path, numerator=4, denominator=4, base_tempo=120)
    assert [e.beat for e in timeline.tempo_events] == [0.0, 9.5]
    assert [e.bpm for e in timeline.tempo_events] == [120.0, 140.0]
    assert timeline.source_path == str(path)


def test_load_timeline_bar_beat_6_8(tmp_path):
    # 6/8: one bar = 6 * 4 / 8 = 3 beats.
    path = _write(tmp_path, {"tempo": [{"bar": 2, "beat": 1, "bpm": 100}, {"bar": 3, "beat": 2, "bpm": 90}]})
    timeline = load_timeline(path, numerator=6, denominator=8, base_tempo=120)
    assert [e.beat for e in timeline.tempo_events] == [3.0, 7.0]


def test_load_timeline_beats_form(tmp_path):
    path = _write(tmp_path, {"markers": [{"beats": 12.5, "name": "Drop"}]})
    timeline = load_timeline(path, numerator=4, denominator=4, base_tempo=120)
    assert timeline.markers == [TimelineMarker(beat=12.5, name="Drop")]


def test_load_timeline_beat_zero_override(tmp_path):
    path = _write(tmp_path, {"tempo": [{"bar": 1, "beat": 1, "bpm": 140}]})
    timeline = load_timeline(path, numerator=4, denominator=4, base_tempo=120)
    tempo_map = TempoMap(120, timeline.tempo_events)
    assert tempo_map.bpm_at(0.0) == 140


def test_load_timeline_unknown_keys_ignored(tmp_path):
    path = _write(tmp_path, {"tempo": [{"bar": 1, "bpm": 120, "curve": "linear"}], "extra_top_level": True})
    timeline = load_timeline(path, numerator=4, denominator=4, base_tempo=120)
    assert timeline.tempo_events == [TempoEvent(beat=0.0, bpm=120.0)]


def test_load_timeline_missing_lists_default_empty(tmp_path):
    path = _write(tmp_path, {})
    timeline = load_timeline(path, numerator=4, denominator=4, base_tempo=120)
    assert timeline.tempo_events == []
    assert timeline.markers == []


def test_load_timeline_dedup_keeps_last(tmp_path):
    path = _write(tmp_path, {"tempo": [{"bar": 5, "bpm": 100}, {"bar": 5, "bpm": 110}]})
    timeline = load_timeline(path, numerator=4, denominator=4, base_tempo=120)
    assert len(timeline.tempo_events) == 1
    assert timeline.tempo_events[0].bpm == 110.0


@pytest.mark.parametrize("entry", [
    {"bar": 0, "bpm": 120},
    {"bar": 1.5, "bpm": 120},
    {"bar": 1, "beat": 0, "bpm": 120},
    {"bar": 1, "beats": 4, "bpm": 120},
    {"bpm": 120},
    {"bar": 1, "bpm": 0},
    {"bar": 1, "bpm": -5},
    {"bar": 1},
    {"bar": 3, "beat": 999, "bpm": 100},
])
def test_load_timeline_rejects_bad_tempo_entry(tmp_path, entry):
    path = _write(tmp_path, {"tempo": [entry]})
    with pytest.raises(ValueError):
        load_timeline(path, numerator=4, denominator=4, base_tempo=120)


def test_load_timeline_rejects_beat_beyond_bar_length(tmp_path):
    # 4/4: one bar is 4 beats long, so beat 999 lands far past bar 3's end.
    path = _write(tmp_path, {"tempo": [{"bar": 3, "beat": 999, "bpm": 100}]})
    with pytest.raises(ValueError, match="beat"):
        load_timeline(path, numerator=4, denominator=4, base_tempo=120)


def test_load_timeline_accepts_beat_at_bar_length(tmp_path):
    # The last beat of a 4/4 bar (beat 4) is still inside the bar.
    path = _write(tmp_path, {"tempo": [{"bar": 1, "beat": 4, "bpm": 120}]})
    timeline_result = load_timeline(path, numerator=4, denominator=4, base_tempo=120)
    assert timeline_result.tempo_events[0].beat == 3.0


def test_load_timeline_bpm_type_error_message_distinct_from_sign_error(tmp_path):
    path = _write(tmp_path, {"tempo": [{"bar": 1, "bpm": "90"}]})
    with pytest.raises(ValueError, match="non-numeric 'bpm'"):
        load_timeline(path, numerator=4, denominator=4, base_tempo=120)


def test_load_timeline_negative_bpm_still_reports_non_positive(tmp_path):
    path = _write(tmp_path, {"tempo": [{"bar": 1, "bpm": -5}]})
    with pytest.raises(ValueError, match="non-positive 'bpm'"):
        load_timeline(path, numerator=4, denominator=4, base_tempo=120)


def test_load_timeline_rejects_nan_bpm(tmp_path):
    path = tmp_path / "timeline.json"
    path.write_text(json.dumps({"tempo": [{"bar": 1, "bpm": None}]}).replace("null", "NaN"), encoding="utf-8")
    with pytest.raises(ValueError, match="non-finite 'bpm'"):
        load_timeline(path, numerator=4, denominator=4, base_tempo=120)


def test_load_timeline_rejects_infinite_bpm(tmp_path):
    path = tmp_path / "timeline.json"
    path.write_text(json.dumps({"tempo": [{"bar": 1, "bpm": None}]}).replace("null", "Infinity"), encoding="utf-8")
    with pytest.raises(ValueError, match="non-finite 'bpm'"):
        load_timeline(path, numerator=4, denominator=4, base_tempo=120)


def test_load_timeline_rejects_nan_beats(tmp_path):
    path = tmp_path / "timeline.json"
    path.write_text(
        json.dumps({"markers": [{"beats": None, "name": "Drop"}]}).replace("null", "NaN"), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="non-finite 'beats'"):
        load_timeline(path, numerator=4, denominator=4, base_tempo=120)


def test_load_timeline_null_tempo_treated_as_empty(tmp_path):
    path = _write(tmp_path, {"tempo": None})
    timeline_result = load_timeline(path, numerator=4, denominator=4, base_tempo=120)
    assert timeline_result.tempo_events == []


def test_load_timeline_null_markers_treated_as_empty(tmp_path):
    path = _write(tmp_path, {"markers": None})
    timeline_result = load_timeline(path, numerator=4, denominator=4, base_tempo=120)
    assert timeline_result.markers == []


def test_load_timeline_rejects_non_list_tempo(tmp_path):
    path = _write(tmp_path, {"tempo": "not-a-list"})
    with pytest.raises(ValueError):
        load_timeline(path, numerator=4, denominator=4, base_tempo=120)


def test_load_timeline_rejects_oversized_file(tmp_path, monkeypatch):
    monkeypatch.setattr(timeline, "_MAX_TIMELINE_BYTES", 10)
    path = _write(tmp_path, {"tempo": [{"bar": 1, "bpm": 120}]})
    with pytest.raises(ValueError, match="too large"):
        load_timeline(path, numerator=4, denominator=4, base_tempo=120)


def test_load_timeline_rejects_negative_beats(tmp_path):
    path = _write(tmp_path, {"tempo": [{"beats": -1, "bpm": 120}]})
    with pytest.raises(ValueError):
        load_timeline(path, numerator=4, denominator=4, base_tempo=120)


def test_load_timeline_rejects_marker_missing_name(tmp_path):
    path = _write(tmp_path, {"markers": [{"bar": 1}]})
    with pytest.raises(ValueError):
        load_timeline(path, numerator=4, denominator=4, base_tempo=120)


# TempoMap


def test_tempo_map_no_events_matches_samples_to_beats():
    """Bit-for-bit with the pre-existing single-tempo formula."""
    tempo_map = TempoMap(120.0, [])
    assert tempo_map.samples_to_beats(23_725_800, 44100) == samples_to_beats(23_725_800, 120.0, 44100)


def test_tempo_map_bpm_at_before_and_after_breakpoint():
    tempo_map = TempoMap(120.0, [TempoEvent(beat=8.0, bpm=140.0)])
    assert tempo_map.bpm_at(0.0) == 120.0
    assert tempo_map.bpm_at(7.999) == 120.0
    assert tempo_map.bpm_at(8.0) == 140.0
    assert tempo_map.bpm_at(100.0) == 140.0


def test_tempo_map_beats_to_seconds_integrates_segments():
    # 8 beats at 120bpm = 4s, then 4 more beats at 140bpm.
    tempo_map = TempoMap(120.0, [TempoEvent(beat=8.0, bpm=140.0)])
    assert tempo_map.beats_to_seconds(8.0) == pytest.approx(4.0)
    assert tempo_map.beats_to_seconds(12.0) == pytest.approx(4.0 + 4 * 60 / 140)


def test_tempo_map_seconds_to_beats_inverts_beats_to_seconds():
    tempo_map = TempoMap(120.0, [TempoEvent(beat=8.0, bpm=140.0), TempoEvent(beat=20.0, bpm=90.0)])
    for beats in (0.0, 3.5, 8.0, 15.0, 20.0, 40.0):
        seconds = tempo_map.beats_to_seconds(beats)
        assert tempo_map.seconds_to_beats(seconds) == pytest.approx(beats)


def test_tempo_map_beats_to_seconds_extrapolates_negative_beats():
    tempo_map = TempoMap(120.0, [TempoEvent(beat=8.0, bpm=140.0)])
    assert tempo_map.beats_to_seconds(-5.0) == pytest.approx(-2.5)


def test_tempo_map_beats_to_seconds_inverts_seconds_to_beats_for_negative_input():
    tempo_map = TempoMap(120.0, [TempoEvent(beat=8.0, bpm=140.0)])
    for beats in (-5.0, -0.5, 0.0):
        seconds = tempo_map.beats_to_seconds(beats)
        assert tempo_map.seconds_to_beats(seconds) == pytest.approx(beats)


def test_tempo_map_samples_to_beats_with_events():
    tempo_map = TempoMap(120.0, [TempoEvent(beat=8.0, bpm=140.0)])
    # 6 seconds: first 4s covers beats 0-8 at 120bpm, remaining 2s at 140bpm.
    beats = tempo_map.samples_to_beats(44100 * 6, 44100)
    assert beats == pytest.approx(8.0 + 2.0 * 140.0 / 60.0)


def test_tempo_map_breakpoints_between_excludes_boundaries():
    tempo_map = TempoMap(120.0, [
        TempoEvent(beat=4.0, bpm=100.0),
        TempoEvent(beat=8.0, bpm=140.0),
        TempoEvent(beat=16.0, bpm=90.0),
    ])
    result = tempo_map.breakpoints_between(4.0, 16.0)
    assert [e.beat for e in result] == [8.0]


def test_tempo_map_sorts_events_on_construction():
    tempo_map = TempoMap(120.0, [TempoEvent(beat=8.0, bpm=140.0), TempoEvent(beat=2.0, bpm=100.0)])
    assert [e.beat for e in tempo_map.events] == [2.0, 8.0]
