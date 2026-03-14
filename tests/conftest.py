import gzip
import os
import wave
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

TEST_PROJECT = Path("Might Last Forever.logicx")
HAS_TEST_PROJECT = TEST_PROJECT.exists() and (TEST_PROJECT / "Resources" / "ProjectInformation.plist").exists()

HAS_VST3 = Path(os.environ.get("VST3_PATH", "C:/Program Files/Common Files/VST3")).exists()


def pytest_collection_modifyitems(config, items):
    skip_no_project = pytest.mark.skip(reason="test .logicx project not available")
    skip_no_vst3 = pytest.mark.skip(reason="VST3 plugins not available")

    for item in items:
        if "needs_test_project" in item.keywords and not HAS_TEST_PROJECT:
            item.add_marker(skip_no_project)
        if "needs_vst3" in item.keywords and not HAS_VST3:
            item.add_marker(skip_no_vst3)


def write_test_wav(path: Path, *, frames: int = 44100, sample_rate: int = 44100) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(b"\x00\x00" * frames)
    return path


def create_test_als(
    tmp_path: Path,
    *,
    include_missing_clip: bool = False,
    include_warped_clip: bool = True,
    stale_absolute_paths: bool = False,
) -> Path:
    project_dir = tmp_path / "Ableton Project"
    samples_dir = project_dir / "Samples" / "Imported"
    source_a = write_test_wav(samples_dir / "kick.wav", frames=44_100)
    source_b = write_test_wav(samples_dir / "vox.wav", frames=88_200)

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

    def add_clip(
        track: ET.Element,
        *,
        clip_name: str,
        absolute_path: str,
        relative_path: str,
        start_beats: float,
        end_beats: float,
        start_relative: float = 0.0,
        warped: bool = False,
    ) -> None:
        events = track.find(".//MainSequencer/Sample/ArrangerAutomation/Events")
        if events is None:
            device_chain = ET.SubElement(track, "DeviceChain")
            main_seq = ET.SubElement(device_chain, "MainSequencer")
            sample = ET.SubElement(main_seq, "Sample")
            arranger = ET.SubElement(sample, "ArrangerAutomation")
            events = ET.SubElement(arranger, "Events")

        clip = ET.SubElement(events, "AudioClip")
        clip.set("Time", str(start_beats))
        ET.SubElement(clip, "CurrentStart").set("Value", str(start_beats))
        ET.SubElement(clip, "CurrentEnd").set("Value", str(end_beats))
        ET.SubElement(clip, "Name").set("Value", clip_name)
        loop = ET.SubElement(clip, "Loop")
        ET.SubElement(loop, "StartRelative").set("Value", str(start_relative))
        ET.SubElement(clip, "IsWarped").set("Value", "true" if warped else "false")
        ET.SubElement(clip, "Disabled").set("Value", "false")
        sample_ref = ET.SubElement(clip, "SampleRef")
        file_ref = ET.SubElement(sample_ref, "FileRef")
        ET.SubElement(file_ref, "Path").set("Value", absolute_path)
        ET.SubElement(file_ref, "RelativePath").set("Value", relative_path)
        ET.SubElement(sample_ref, "DefaultDuration").set("Value", "44100")
        ET.SubElement(sample_ref, "DefaultSampleRate").set("Value", "44100")

    drums = ET.SubElement(tracks, "AudioTrack")
    ET.SubElement(ET.SubElement(drums, "Name"), "EffectiveName").set("Value", "Drums")
    add_clip(
        drums,
        clip_name="Kick Loop",
        absolute_path=str(((project_dir / "stale" / "kick.wav") if stale_absolute_paths else source_a).resolve()),
        relative_path="Samples/Imported/kick.wav",
        start_beats=1.0,
        end_beats=5.0,
    )

    vocals = ET.SubElement(tracks, "AudioTrack")
    ET.SubElement(ET.SubElement(vocals, "Name"), "EffectiveName").set("Value", "Vocals")
    add_clip(
        vocals,
        clip_name="Lead Vox",
        absolute_path=str(((project_dir / "stale" / "vox.wav") if stale_absolute_paths else source_b).resolve()),
        relative_path="Samples/Imported/vox.wav",
        start_beats=9.0,
        end_beats=11.0,
        start_relative=0.5,
        warped=include_warped_clip,
    )

    if include_missing_clip:
        add_clip(
            vocals,
            clip_name="Missing FX",
            absolute_path=str((samples_dir / "missing.wav").resolve()),
            relative_path="Samples/Imported/missing.wav",
            start_beats=13.0,
            end_beats=15.0,
        )

    als_path = project_dir / "Demo Set.als"
    with gzip.open(als_path, "wb") as handle:
        handle.write(ET.tostring(root, encoding="utf-8", xml_declaration=True))
    return als_path
