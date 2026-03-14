"""Create a tiny Ableton Live Set fixture for binary smoke tests."""

from __future__ import annotations

import gzip
import sys
import wave
import xml.etree.ElementTree as ET
from pathlib import Path


def write_test_wav(path: Path, frames: int = 44_100, sample_rate: int = 44_100) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(b"\x00\x00" * frames)


def main(argv: list[str] | None = None) -> int:
    args = argv or sys.argv[1:]
    if len(args) != 1:
        print("Usage: python scripts/create_sample_als_fixture.py <output.als>", file=sys.stderr)
        return 1

    als_path = Path(args[0])
    project_dir = als_path.parent
    samples_dir = project_dir / "Samples" / "Imported"
    write_test_wav(samples_dir / "kick.wav")

    root = ET.Element("Ableton")
    live_set = ET.SubElement(root, "LiveSet")
    transport = ET.SubElement(live_set, "Transport")
    tempo = ET.SubElement(ET.SubElement(transport, "Tempo"), "Manual")
    tempo.set("Value", "128")
    ts = ET.SubElement(ET.SubElement(transport, "TimeSignatures"), "RemoteableTimeSignature")
    ET.SubElement(ts, "Numerator").set("Value", "4")
    ET.SubElement(ts, "Denominator").set("Value", "4")

    locators = ET.SubElement(ET.SubElement(live_set, "Locators"), "Locators")
    locator = ET.SubElement(locators, "Locator")
    ET.SubElement(locator, "Name").set("Value", "Verse")
    ET.SubElement(locator, "Time").set("Value", "17")

    tracks = ET.SubElement(live_set, "Tracks")
    drums = ET.SubElement(tracks, "AudioTrack")
    ET.SubElement(ET.SubElement(drums, "Name"), "EffectiveName").set("Value", "Drums")
    device_chain = ET.SubElement(drums, "DeviceChain")
    main_seq = ET.SubElement(device_chain, "MainSequencer")
    sample = ET.SubElement(main_seq, "Sample")
    arranger = ET.SubElement(sample, "ArrangerAutomation")
    events = ET.SubElement(arranger, "Events")
    clip = ET.SubElement(events, "AudioClip")
    clip.set("Time", "1")
    ET.SubElement(clip, "CurrentStart").set("Value", "1")
    ET.SubElement(clip, "CurrentEnd").set("Value", "5")
    ET.SubElement(clip, "Name").set("Value", "Kick Loop")
    loop = ET.SubElement(clip, "Loop")
    ET.SubElement(loop, "StartRelative").set("Value", "0")
    ET.SubElement(clip, "IsWarped").set("Value", "false")
    ET.SubElement(clip, "Disabled").set("Value", "false")
    sample_ref = ET.SubElement(clip, "SampleRef")
    file_ref = ET.SubElement(sample_ref, "FileRef")
    ET.SubElement(file_ref, "Path").set("Value", str((samples_dir / "kick.wav").resolve()))
    ET.SubElement(file_ref, "RelativePath").set("Value", "Samples/Imported/kick.wav")
    ET.SubElement(sample_ref, "DefaultDuration").set("Value", "44100")
    ET.SubElement(sample_ref, "DefaultSampleRate").set("Value", "44100")

    als_path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(als_path, "wb") as handle:
        handle.write(ET.tostring(root, encoding="utf-8", xml_declaration=True))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
