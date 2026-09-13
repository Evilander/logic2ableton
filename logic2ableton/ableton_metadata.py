"""Live's global mixer parameters (MainTrack in Live 12, MasterTrack earlier)."""

import math
import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from logic2ableton.timeline import TempoMap

# The sentinel event Live writes on every automated envelope to carry its
# initial value at playback start, well before any real automation begins.
_SENTINEL_TIME = "-63072000"


def encode_meter(numerator: int, denominator: int) -> int:
    # Live enumerates 1..99 over denominators 1, 2, 4, 8, 16.
    # Also documented by DawVert's Ableton input/output implementations:
    # https://github.com/SatyrDiamond/DawVert/blob/main/plugins/input/r_ableton.py
    if not 1 <= numerator <= 99 or denominator not in (1, 2, 4, 8, 16):
        raise ValueError("Live supports meters with numerator 1–99 and denominator 1, 2, 4, 8, or 16")
    return 99 * (denominator.bit_length() - 1) + numerator - 1


def decode_meter(value: int) -> tuple[int, int]:
    if not 0 <= value < 495:
        raise ValueError(f"Invalid Live time signature: {value}")
    power, numerator = divmod(value, 99)
    return numerator + 1, 1 << power


def main_track(live_set: ET.Element) -> ET.Element | None:
    for tag in ("MainTrack", "MasterTrack"):
        track = live_set.find(tag)
        if track is not None:
            return track
    return None


def parameter_events(track: ET.Element, parameter: ET.Element) -> list[ET.Element]:
    target = parameter.find("AutomationTarget")
    if target is None:
        return []
    target_id = target.get("Id")
    for envelope in track.findall("AutomationEnvelopes/Envelopes/AutomationEnvelope"):
        pointee = envelope.find("EnvelopeTarget/PointeeId")
        if pointee is not None and pointee.get("Value") == target_id:
            events = envelope.find("Automation/Events")
            return list(events) if events is not None else []
    return []


def read_global_parameter(live_set: ET.Element, name: str, default: float) -> float:
    track = main_track(live_set)
    if track is None:
        manual = live_set.find(f"Transport/{name}/Manual")
        return float(manual.get("Value", str(default))) if manual is not None else default
    parameter = track.find(f"DeviceChain/Mixer/{name}")
    if parameter is None:
        return default
    manual = parameter.find("Manual")
    value = float(manual.get("Value", str(default))) if manual is not None else default
    initial = [event for event in parameter_events(track, parameter) if float(event.get("Time", "0")) <= 0]
    if initial:
        value = float(max(initial, key=lambda event: float(event.get("Time", "0"))).get("Value", str(value)))
    if not math.isfinite(value):
        raise ValueError(f"Invalid Live {name}: {value}")
    return value


def set_global_parameter(live_set: ET.Element, name: str, value: str) -> None:
    track = main_track(live_set)
    if track is None:
        return
    parameter = track.find(f"DeviceChain/Mixer/{name}")
    if parameter is None:
        return
    manual = parameter.find("Manual")
    if manual is not None:
        manual.set("Value", value)
    # A template's automation can override its Manual value at playback start.
    for event in parameter_events(track, parameter):
        event.set("Value", value)


def _format_number(value: float) -> str:
    """Format like ableton_generator's number formatting: no dropped fractional precision."""
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.6f}".rstrip("0").rstrip(".")


def set_tempo_automation(live_set: ET.Element, tempo_map: "TempoMap", allocator) -> None:
    """Set the project tempo and, when the map has breakpoints, step the
    Tempo envelope through them.

    With no breakpoints this only sets Manual and mirrors it onto any
    existing envelope events via set_global_parameter — unchanged from
    before tempo automation existed, and template-preserving (it never
    touches an envelope's event count or timing, only values).

    With breakpoints: the sentinel event (Time=-63072000, Live's carrier
    for the initial value) is rewritten to the tempo at beat 0, every other
    existing event on the envelope is dropped, and each breakpoint after
    beat 0 that actually changes the tempo gets a two-event step at its own
    Time — the previous bpm immediately followed by the new one — since
    Live's automation is linear between events and a bare single point
    would ramp instead of stepping.
    """
    base_bpm = tempo_map.bpm_at(0.0)
    value = _format_number(base_bpm)
    if not tempo_map.events:
        set_global_parameter(live_set, "Tempo", value)
        return

    track = main_track(live_set)
    if track is None:
        return
    parameter = track.find("DeviceChain/Mixer/Tempo")
    if parameter is None:
        return
    manual = parameter.find("Manual")
    if manual is not None:
        manual.set("Value", value)

    target = parameter.find("AutomationTarget")
    if target is None:
        return
    target_id = target.get("Id")
    events_elem = None
    for envelope in track.findall("AutomationEnvelopes/Envelopes/AutomationEnvelope"):
        pointee = envelope.find("EnvelopeTarget/PointeeId")
        if pointee is not None and pointee.get("Value") == target_id:
            events_elem = envelope.find("Automation/Events")
            break
    if events_elem is None:
        return

    sentinel = None
    for event in list(events_elem):
        if sentinel is None and event.get("Time") == _SENTINEL_TIME:
            sentinel = event
        events_elem.remove(event)
    if sentinel is None:
        sentinel = ET.SubElement(events_elem, "FloatEvent")
        sentinel.set("Id", str(allocator.next()))
        sentinel.set("Time", _SENTINEL_TIME)
    else:
        events_elem.append(sentinel)
    sentinel.set("Value", value)

    previous_bpm = base_bpm
    for event in tempo_map.events:
        if event.beat <= 0:
            previous_bpm = event.bpm
            continue
        if event.bpm != previous_bpm:
            for bpm in (previous_bpm, event.bpm):
                step = ET.SubElement(events_elem, "FloatEvent")
                step.set("Id", str(allocator.next()))
                step.set("Time", _format_number(event.beat))
                step.set("Value", _format_number(bpm))
        previous_bpm = event.bpm


def has_global_changes(live_set: ET.Element, name: str) -> bool:
    track = main_track(live_set)
    if track is None:
        return False
    parameter = track.find(f"DeviceChain/Mixer/{name}")
    if parameter is None:
        return False
    initial = read_global_parameter(live_set, name, 120 if name == "Tempo" else 201)
    return any(float(event.get("Value", str(initial))) != initial for event in parameter_events(track, parameter))
