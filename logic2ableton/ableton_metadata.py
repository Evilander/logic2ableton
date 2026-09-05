"""Live's global mixer parameters (MainTrack in Live 12, MasterTrack earlier)."""

import math
import xml.etree.ElementTree as ET


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


def has_global_changes(live_set: ET.Element, name: str) -> bool:
    track = main_track(live_set)
    if track is None:
        return False
    parameter = track.find(f"DeviceChain/Mixer/{name}")
    if parameter is None:
        return False
    initial = read_global_parameter(live_set, name, 120 if name == "Tempo" else 201)
    return any(float(event.get("Value", str(initial))) != initial for event in parameter_events(track, parameter))
