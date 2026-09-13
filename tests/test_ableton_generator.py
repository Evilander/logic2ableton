import gzip
import wave
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from logic2ableton.ableton_generator import (
    _BUNDLED_TEMPLATE,
    generate_als,
    _pick_best_clip,
    _find_template,
    unmatched_keep_unwarped_warnings,
)
from logic2ableton.ableton_metadata import main_track, parameter_events
from logic2ableton.logic_parser import parse_logic_project
from logic2ableton.models import AudioFileRef, LogicMidiNote, LogicMidiTrack, LogicProject, TrackMixerState
from logic2ableton.timeline import TempoEvent, Timeline, TimelineMarker

from conftest import TEST_PROJECT, TEST_PROJECT_NAME, write_test_wav


@pytest.mark.needs_test_project
def test_generate_als_creates_file(tmp_path):
    project = parse_logic_project(TEST_PROJECT)
    als_path = generate_als(project, tmp_path / "output", copy_audio=False)
    assert als_path.exists()
    assert als_path.suffix == ".als"
    assert als_path.name == f"{TEST_PROJECT_NAME}.als"


@pytest.mark.needs_test_project
def test_generate_als_is_valid_gzipped_xml(tmp_path):
    project = parse_logic_project(TEST_PROJECT)
    als_path = generate_als(project, tmp_path / "output", copy_audio=False)
    with gzip.open(als_path, "rb") as f:
        xml_content = f.read().decode("utf-8")
    root = ET.fromstring(xml_content)
    assert root.tag == "Ableton"


@pytest.mark.needs_test_project
def test_generate_als_has_correct_tracks(tmp_path):
    project = parse_logic_project(TEST_PROJECT)
    als_path = generate_als(project, tmp_path / "output", copy_audio=False)
    with gzip.open(als_path, "rb") as f:
        root = ET.fromstring(f.read())
    tracks = root.findall(".//Tracks/AudioTrack")
    assert len(tracks) == len(project.track_names)
    names = [t.find(".//Name/EffectiveName").get("Value") for t in tracks]
    for tn in project.track_names:
        assert tn in names


@pytest.mark.needs_test_project
def test_generate_als_has_correct_tempo(tmp_path):
    project = parse_logic_project(TEST_PROJECT)
    als_path = generate_als(project, tmp_path / "output", copy_audio=False)
    with gzip.open(als_path, "rb") as f:
        root = ET.fromstring(f.read())
    tempo = root.find(".//Tempo/Manual")
    assert tempo.get("Value") == "120"


@pytest.mark.needs_test_project
def test_generate_als_copies_audio(tmp_path):
    project = parse_logic_project(TEST_PROJECT)
    output_dir = tmp_path / "output"
    generate_als(project, output_dir, copy_audio=True)
    samples_dir = output_dir / f"{TEST_PROJECT_NAME} Project" / "Samples" / "Imported"
    assert samples_dir.exists()
    wav_files = list(samples_dir.glob("*.wav"))
    assert len(wav_files) == len(project.audio_files)


# Clip placement tests

@pytest.mark.needs_test_project
def test_generate_als_has_arrangement_clips(tmp_path):
    project = parse_logic_project(TEST_PROJECT)
    als_path = generate_als(project, tmp_path / "output", copy_audio=False)
    with gzip.open(als_path, "rb") as f:
        root = ET.fromstring(f.read())
    clips = root.findall(".//Events/AudioClip")
    assert len(clips) > 0


@pytest.mark.needs_test_project
def test_generate_als_at_least_one_clip_per_track(tmp_path):
    """Each track should have at least one clip."""
    project = parse_logic_project(TEST_PROJECT)
    als_path = generate_als(project, tmp_path / "output", copy_audio=False)
    with gzip.open(als_path, "rb") as f:
        root = ET.fromstring(f.read())
    tracks = root.findall(".//Tracks/AudioTrack")
    clips = root.findall(".//Events/AudioClip")
    assert len(clips) >= len(tracks)


@pytest.mark.needs_test_project
def test_generate_als_clips_at_bwf_positions(tmp_path):
    """Clips should be placed at BWF-derived timeline positions."""
    project = parse_logic_project(TEST_PROJECT)
    als_path = generate_als(project, tmp_path / "output", copy_audio=False)
    with gzip.open(als_path, "rb") as f:
        root = ET.fromstring(f.read())
    clips = root.findall(".//Events/AudioClip")
    # At least some clips should be at non-zero positions
    times = [float(clip.get("Time")) for clip in clips]
    assert any(t > 0 for t in times), "Expected some clips at non-zero positions"


@pytest.mark.needs_test_project
def test_generate_als_kick_in_positions(tmp_path):
    """KICK IN clips should be at their correct BWF positions."""
    project = parse_logic_project(TEST_PROJECT)
    als_path = generate_als(project, tmp_path / "output", copy_audio=False)
    with gzip.open(als_path, "rb") as f:
        root = ET.fromstring(f.read())
    clips = root.findall(".//Events/AudioClip")
    kick_clips = [c for c in clips if "KICK IN" in c.find("Name").get("Value")]
    # KICK IN has 3 sessions at different positions
    kick_times = sorted(float(c.get("Time")) for c in kick_clips)
    assert len(kick_times) == 3
    # Session 1 at ~beat 4 (bar 2), Session 2 at ~beat 1276 (bar 320), Session 3 at ~beat 2048 (bar 513)
    assert kick_times[0] < 10  # near start
    assert 1200 < kick_times[1] < 1400  # session 2
    assert 2000 < kick_times[2] < 2100  # session 3


@pytest.mark.needs_test_project
def test_generate_als_clip_has_sample_ref(tmp_path):
    project = parse_logic_project(TEST_PROJECT)
    als_path = generate_als(project, tmp_path / "output", copy_audio=False)
    with gzip.open(als_path, "rb") as f:
        root = ET.fromstring(f.read())
    clips = root.findall(".//Events/AudioClip")
    for clip in clips:
        rel_path = clip.find(".//SampleRef/FileRef/RelativePath")
        assert rel_path is not None
        assert rel_path.get("Value").startswith("Samples/Imported/")


def test_generate_als_prefers_comp_over_takes():
    """_pick_best_clip should prefer comp files over regular takes."""
    dummy_path = Path("dummy.wav")
    clips = [
        AudioFileRef("track#01.wav", "track", 1, False, "", dummy_path),
        AudioFileRef("track#02.wav", "track", 2, False, "", dummy_path),
        AudioFileRef("track_ Comp A.wav", "track", 0, True, "Comp A", dummy_path),
    ]
    best = _pick_best_clip(clips)
    assert best.is_comp
    assert best.comp_name == "Comp A"


def test_generate_als_prefers_bip_over_takes():
    """_pick_best_clip should prefer bounce-in-place over regular takes."""
    dummy_path = Path("dummy.wav")
    clips = [
        AudioFileRef("track#01.wav", "track", 1, False, "", dummy_path),
        AudioFileRef("track#02.wav", "track", 2, False, "", dummy_path),
        AudioFileRef("track_bip.wav", "track", 0, False, "", dummy_path),
    ]
    best = _pick_best_clip(clips)
    assert "_bip" in best.filename


def test_generate_als_prefers_latest_take():
    """_pick_best_clip should prefer the latest take when no comp/bip exists."""
    dummy_path = Path("dummy.wav")
    clips = [
        AudioFileRef("track#01.wav", "track", 1, False, "", dummy_path),
        AudioFileRef("track#03.wav", "track", 3, False, "", dummy_path),
        AudioFileRef("track#02.wav", "track", 2, False, "", dummy_path),
    ]
    best = _pick_best_clip(clips)
    assert best.take_number == 3


@pytest.mark.needs_test_project
def test_generate_als_bass_guitar_has_bip(tmp_path):
    """BASS GUITAR track should include the _bip file (preferred over overlapping takes)."""
    project = parse_logic_project(TEST_PROJECT)
    als_path = generate_als(project, tmp_path / "output", copy_audio=False)
    with gzip.open(als_path, "rb") as f:
        root = ET.fromstring(f.read())
    clips = root.findall(".//Events/AudioClip")
    bass_clips = [c for c in clips if "BASS GUITAR" in c.find("Name").get("Value")]
    bass_names = [c.find("Name").get("Value") for c in bass_clips]
    assert any("bip" in n for n in bass_names), f"Expected _bip clip, got: {bass_names}"


@pytest.mark.needs_test_project
def test_generate_als_scratch_vox_2_has_comp(tmp_path):
    """scratch vox 2 track should include the Comp A file (preferred over overlapping takes)."""
    project = parse_logic_project(TEST_PROJECT)
    als_path = generate_als(project, tmp_path / "output", copy_audio=False)
    with gzip.open(als_path, "rb") as f:
        root = ET.fromstring(f.read())
    clips = root.findall(".//Events/AudioClip")
    sv2_clips = [c for c in clips if "scratch vox 2" in c.find("Name").get("Value")]
    sv2_names = [c.find("Name").get("Value") for c in sv2_clips]
    assert any("Comp" in n for n in sv2_names), f"Expected Comp clip, got: {sv2_names}"


# Template-based structural tests

@pytest.mark.needs_test_project
def test_generate_als_schema_version(tmp_path):
    project = parse_logic_project(TEST_PROJECT)
    als_path = generate_als(project, tmp_path / "output", copy_audio=False)
    with gzip.open(als_path, "rb") as f:
        root = ET.fromstring(f.read())
    assert root.get("MajorVersion") == "5"
    # Template from Ableton Live 12
    assert root.get("SchemaChangeCount") is not None


@pytest.mark.needs_test_project
def test_generate_als_has_main_track_mixer(tmp_path):
    project = parse_logic_project(TEST_PROJECT)
    als_path = generate_als(project, tmp_path / "output", copy_audio=False)
    with gzip.open(als_path, "rb") as f:
        root = ET.fromstring(f.read())
    main_track = root.find(".//MainTrack")
    assert main_track is not None
    mixer = main_track.find(".//DeviceChain/Mixer")
    assert mixer is not None
    assert mixer.find("Volume/Manual") is not None
    assert mixer.find("Tempo/Manual") is not None
    assert mixer.find("Tempo/Manual").get("Value") == "120"


@pytest.mark.needs_test_project
def test_generate_als_track_has_main_sequencer(tmp_path):
    project = parse_logic_project(TEST_PROJECT)
    als_path = generate_als(project, tmp_path / "output", copy_audio=False)
    with gzip.open(als_path, "rb") as f:
        root = ET.fromstring(f.read())
    tracks = root.findall(".//Tracks/AudioTrack")
    for track in tracks:
        main_seq = track.find(".//MainSequencer")
        assert main_seq is not None
        assert main_seq.find("ClipSlotList") is not None
        assert main_seq.find("Recorder/IsArmed") is not None
        assert main_seq.find("Sample/ArrangerAutomation/Events") is not None


@pytest.mark.needs_test_project
def test_generate_als_has_return_tracks(tmp_path):
    project = parse_logic_project(TEST_PROJECT)
    als_path = generate_als(project, tmp_path / "output", copy_audio=False)
    with gzip.open(als_path, "rb") as f:
        root = ET.fromstring(f.read())
    returns = root.findall(".//Tracks/ReturnTrack")
    assert len(returns) >= 2


@pytest.mark.needs_test_project
def test_generate_als_clip_has_warp_markers(tmp_path):
    project = parse_logic_project(TEST_PROJECT)
    als_path = generate_als(project, tmp_path / "output", copy_audio=False)
    with gzip.open(als_path, "rb") as f:
        root = ET.fromstring(f.read())
    clips = root.findall(".//Events/AudioClip")
    assert len(clips) > 0
    for clip in clips:
        warp_markers = clip.find("WarpMarkers")
        assert warp_markers is not None
        markers = warp_markers.findall("WarpMarker")
        assert len(markers) == 2


@pytest.mark.needs_test_project
def test_generate_als_clip_has_fades(tmp_path):
    project = parse_logic_project(TEST_PROJECT)
    als_path = generate_als(project, tmp_path / "output", copy_audio=False)
    with gzip.open(als_path, "rb") as f:
        root = ET.fromstring(f.read())
    clip = root.find(".//Events/AudioClip")
    fades = clip.find("Fades")
    assert fades is not None
    assert fades.find("FadeInLength") is not None


@pytest.mark.needs_test_project
def test_generate_als_file_ref_complete(tmp_path):
    project = parse_logic_project(TEST_PROJECT)
    als_path = generate_als(project, tmp_path / "output", copy_audio=False)
    with gzip.open(als_path, "rb") as f:
        root = ET.fromstring(f.read())
    clip = root.find(".//Events/AudioClip")
    file_ref = clip.find(".//SampleRef/FileRef")
    assert file_ref is not None
    assert file_ref.find("RelativePathType").get("Value") == "1"
    assert file_ref.find("RelativePath") is not None
    assert file_ref.find("Path") is not None
    assert file_ref.find("Type").get("Value") == "1"
    assert file_ref.find("LivePackName") is not None
    assert file_ref.find("LivePackId") is not None
    assert file_ref.find("OriginalFileSize") is not None


@pytest.mark.needs_test_project
def test_generate_als_sample_ref_complete(tmp_path):
    project = parse_logic_project(TEST_PROJECT)
    als_path = generate_als(project, tmp_path / "output", copy_audio=False)
    with gzip.open(als_path, "rb") as f:
        root = ET.fromstring(f.read())
    clip = root.find(".//Events/AudioClip")
    sample_ref = clip.find("SampleRef")
    assert sample_ref is not None
    assert sample_ref.find("SourceContext") is not None
    assert sample_ref.find("SampleUsageHint") is not None
    assert sample_ref.find("DefaultDuration") is not None
    assert sample_ref.find("DefaultSampleRate") is not None


def _assert_unique_critical_ids(root: ET.Element) -> None:
    critical_tags = {"AutomationTarget", "ModulationTarget", "Pointee"}
    critical_ids = {}
    for elem in root.iter():
        if elem.tag in critical_tags:
            id_val = elem.get("Id")
            if id_val is not None:
                assert id_val not in critical_ids, f"Duplicate {elem.tag} Id={id_val}"
                critical_ids[id_val] = elem.tag
    assert set(critical_ids.values()) == critical_tags


@pytest.mark.needs_test_project
def test_generate_als_unique_critical_ids(tmp_path):
    project = parse_logic_project(TEST_PROJECT)
    als_path = generate_als(project, tmp_path / "output", copy_audio=False)
    with gzip.open(als_path, "rb") as f:
        root = ET.fromstring(f.read())
    _assert_unique_critical_ids(root)


def test_generate_als_unique_critical_ids_synthetic(tmp_path):
    """The global ID invariant must run in CI without a private Logic project."""
    audio_path = write_test_wav(tmp_path / "media" / "Guitar.wav")
    project = LogicProject(
        name="Synthetic IDs",
        tempo=120.0,
        time_sig_numerator=4,
        time_sig_denominator=4,
        sample_rate=44_100,
        audio_files=[
            AudioFileRef(
                filename="Guitar.wav",
                track_name="Guitar",
                take_number=0,
                is_comp=False,
                comp_name="",
                file_path=audio_path,
            )
        ],
        plugins=[],
        track_names=["Guitar"],
        alternative=0,
        midi_tracks=[
            LogicMidiTrack(
                name="Keys",
                notes=[
                    LogicMidiNote(
                        pitch=60,
                        start_beats=4.0,
                        duration_beats=1.0,
                        velocity=100,
                    )
                ],
            )
        ],
    )
    als_path = generate_als(
        project,
        tmp_path / "output",
        copy_audio=False,
        template_path=_BUNDLED_TEMPLATE,
    )
    with gzip.open(als_path, "rb") as f:
        root = ET.fromstring(f.read())
    _assert_unique_critical_ids(root)


@pytest.mark.needs_test_project
def test_generate_als_liveset_metadata(tmp_path):
    project = parse_logic_project(TEST_PROJECT)
    als_path = generate_als(project, tmp_path / "output", copy_audio=False)
    with gzip.open(als_path, "rb") as f:
        root = ET.fromstring(f.read())
    live_set = root.find("LiveSet")
    assert live_set.find("OverwriteProtectionNumber") is not None
    assert live_set.find("LomId") is not None
    assert live_set.find("LomIdView") is not None


@pytest.mark.needs_test_project
def test_generate_als_custom_mixer_state(tmp_path):
    project = parse_logic_project(TEST_PROJECT)
    project.mixer_state = {
        "KICK IN": TrackMixerState(volume_db=-6.0, pan=0.3),
    }

    als_path = generate_als(project, tmp_path / "output", copy_audio=False)
    with gzip.open(als_path, "rb") as f:
        root = ET.fromstring(f.read())

    for track in root.iter("AudioTrack"):
        name = track.find(".//Name/EffectiveName")
        if name is None or name.get("Value") != "KICK IN":
            continue
        mixer = track.find(".//DeviceChain/Mixer")
        vol = float(mixer.find("Volume/Manual").get("Value"))
        pan = float(mixer.find("Pan/Manual").get("Value"))
        assert abs(vol - 0.5012) < 0.01
        assert abs(pan - 0.3) < 0.01
        return

    raise AssertionError("KICK IN track not found")


def test_find_template_custom_path(tmp_path):
    """--template flag should override auto-discovery."""
    fake_template = tmp_path / "custom.als"
    fake_template.write_bytes(b"fake")
    result = _find_template(custom_path=fake_template)
    assert result == fake_template


def test_find_template_custom_path_missing():
    """Missing custom template returns None."""
    result = _find_template(custom_path=Path("/nonexistent/template.als"))
    assert result is None


def test_generate_als_preserves_fractional_tempo_and_project_time_signature(tmp_path):
    sample_path = tmp_path / "clip.wav"
    with wave.open(str(sample_path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(48_000)
        wav_file.writeframes(b"\x00\x00" * 48_000)

    project = LogicProject(
        name="Fractional Tempo",
        tempo=123.5,
        time_sig_numerator=7,
        time_sig_denominator=8,
        sample_rate=48_000,
        audio_files=[
            AudioFileRef(
                filename="clip.wav",
                track_name="Track 1",
                take_number=0,
                is_comp=False,
                comp_name="",
                file_path=sample_path,
            )
        ],
        plugins=[],
        track_names=["Track 1"],
        alternative=0,
    )

    als_path = generate_als(project, tmp_path / "output", copy_audio=False)
    with gzip.open(als_path, "rb") as f:
        root = ET.fromstring(f.read())

    tempo = root.find(".//Tempo/Manual")
    assert tempo is not None
    assert tempo.get("Value") == "123.5"

    clip_ts = root.find(".//Events/AudioClip/TimeSignature/TimeSignatures/RemoteableTimeSignature")
    assert clip_ts is not None
    assert clip_ts.find("Numerator").get("Value") == "7"
    assert clip_ts.find("Denominator").get("Value") == "8"


@pytest.mark.skipif(
    not any(
        Path(p).exists()
        for p in [
            "C:/ProgramData/Ableton",
            "/Library/Application Support/Ableton",
        ]
    ),
    reason="Ableton Live not installed",
)
def test_find_template_auto_discovery():
    """Auto-discovery should find a template on this machine."""
    result = _find_template()
    assert result is not None
    assert result.name == "DefaultLiveSet.als"


@pytest.mark.needs_test_project
def test_generate_als_with_custom_template(tmp_path):
    """generate_als should accept template_path parameter."""
    # Use the real template found by auto-discovery
    real_template = _find_template()
    assert real_template is not None
    project = parse_logic_project(TEST_PROJECT)
    als_path = generate_als(project, tmp_path / "output", copy_audio=False, template_path=real_template)
    assert als_path.exists()


def _write_wav(path: Path, frames: int = 44100) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as h:
        h.setnchannels(1)
        h.setsampwidth(2)
        h.setframerate(44100)
        h.writeframes(b"\x00\x00" * frames)
    return path


def test_generated_clip_color_matches_track(tmp_path):
    """Each arrangement clip should carry its track's color (Reddit request)."""
    media = tmp_path / "media"
    refs = []
    for name in ("Drums", "Bass", "Vocals"):
        wav = _write_wav(media / f"{name}.wav")
        refs.append(
            AudioFileRef(
                filename=f"{name}.wav",
                track_name=name,
                take_number=0,
                is_comp=False,
                comp_name="",
                file_path=wav,
            )
        )
    project = LogicProject(
        name="Colors",
        tempo=120.0,
        time_sig_numerator=4,
        time_sig_denominator=4,
        sample_rate=44100,
        audio_files=refs,
        plugins=[],
        track_names=["Drums", "Bass", "Vocals"],
        alternative=0,
    )
    als_path = generate_als(project, tmp_path / "out", copy_audio=False)
    with gzip.open(als_path, "rb") as f:
        root = ET.fromstring(f.read())

    tracks = root.findall(".//Tracks/AudioTrack")
    assert len(tracks) == 3
    saw_clip = False
    for track in tracks:
        track_color = track.find("Color").get("Value")
        for clip in track.findall(".//Events/AudioClip"):
            saw_clip = True
            assert clip.find("Color").get("Value") == track_color
    assert saw_clip, "expected at least one arrangement clip"
    # Distinct tracks should not all share color 0.
    colors = {t.find("Color").get("Value") for t in tracks}
    assert len(colors) > 1


# --keep-unwarped, tempo automation, and locators (--timeline)


def _timeline_project(name: str, track_names: list[str], audio_files: list[AudioFileRef], tempo: float = 120.0) -> LogicProject:
    return LogicProject(
        name=name,
        tempo=tempo,
        time_sig_numerator=4,
        time_sig_denominator=4,
        sample_rate=44100,
        audio_files=audio_files,
        plugins=[],
        track_names=track_names,
        alternative=0,
    )


def test_generate_als_keep_unwarped_marks_matching_tracks(tmp_path):
    media = tmp_path / "media"
    refs = []
    for name in ("DRUMS", "Bass", "Vocals"):
        wav = _write_wav(media / f"{name}.wav")
        refs.append(
            AudioFileRef(
                filename=f"{name}.wav",
                track_name=name,
                take_number=0,
                is_comp=False,
                comp_name="",
                file_path=wav,
            )
        )
    project = _timeline_project("Unwarped", ["DRUMS", "Bass", "Vocals"], refs)

    als_path = generate_als(project, tmp_path / "out", copy_audio=False, keep_unwarped=["drums", "voc*"])
    with gzip.open(als_path, "rb") as f:
        root = ET.fromstring(f.read())

    by_track = {}
    for track in root.findall(".//Tracks/AudioTrack"):
        name = track.find(".//Name/EffectiveName").get("Value")
        clip = track.find(".//Events/AudioClip")
        by_track[name] = clip.find("IsWarped").get("Value")
    assert by_track["DRUMS"] == "false"
    assert by_track["Vocals"] == "false"
    assert by_track["Bass"] == "true"


def test_generate_als_unwarped_clip_loop_in_seconds(tmp_path):
    wav = _write_wav(tmp_path / "media" / "Drums.wav", frames=44100)
    ref = AudioFileRef(filename="Drums.wav", track_name="Drums", take_number=0, is_comp=False, comp_name="", file_path=wav)
    project = _timeline_project("UnwarpedLoop", ["Drums"], [ref])

    als_path = generate_als(project, tmp_path / "out", copy_audio=False, keep_unwarped=["Drums"])
    with gzip.open(als_path, "rb") as f:
        root = ET.fromstring(f.read())

    clip = root.find(".//Events/AudioClip")
    assert clip.find("IsWarped").get("Value") == "false"
    loop = clip.find("Loop")
    assert loop.find("LoopStart").get("Value") == "0"
    assert loop.find("LoopEnd").get("Value") == "1"
    assert loop.find("StartRelative").get("Value") == "0"
    markers = clip.find("WarpMarkers").findall("WarpMarker")
    assert len(markers) == 2
    assert clip.find("WarpMode").get("Value") == "0"


def test_generate_als_keep_unwarped_unmatched_pattern_warns(tmp_path):
    wav = _write_wav(tmp_path / "media" / "Drums.wav")
    ref = AudioFileRef(filename="Drums.wav", track_name="Drums", take_number=0, is_comp=False, comp_name="", file_path=wav)
    project = _timeline_project("UnmatchedPattern", ["Drums"], [ref])

    generate_als(project, tmp_path / "out", copy_audio=False, keep_unwarped=["Drums", "Nonexistent*"])
    assert any("Nonexistent*" in w for w in project.compatibility_warnings)
    assert not any("'Drums'" in w for w in project.compatibility_warnings)


def test_generate_als_extra_warp_marker_at_tempo_breakpoint(tmp_path):
    wav = _write_wav(tmp_path / "media" / "Guitar.wav", frames=441_000)  # 10s @ 44100Hz
    ref = AudioFileRef(filename="Guitar.wav", track_name="Guitar", take_number=0, is_comp=False, comp_name="", file_path=wav)
    project = _timeline_project("Breakpoint", ["Guitar"], [ref])
    project.timeline = Timeline(tempo_events=[TempoEvent(beat=8.0, bpm=140.0)], markers=[])

    als_path = generate_als(project, tmp_path / "out", copy_audio=False)
    with gzip.open(als_path, "rb") as f:
        root = ET.fromstring(f.read())

    clip = root.find(".//Events/AudioClip")
    markers = clip.find("WarpMarkers").findall("WarpMarker")
    assert len(markers) == 3
    # 8 beats at 120bpm = 4s in; the file is 10s long, so beat 22 (8 + 6s @140bpm) is the end.
    assert markers[0].get("SecTime") == "0"
    assert markers[0].get("BeatTime") == "0"
    assert markers[1].get("SecTime") == "4.0"
    assert markers[1].get("BeatTime") == "8.0"
    assert markers[2].get("SecTime") == "10.0"
    assert markers[2].get("BeatTime") == "22.0"
    ids = [m.get("Id") for m in markers]
    assert len(ids) == len(set(ids))


def test_generate_als_warped_clip_length_is_anchored_at_its_position(tmp_path):
    wav = _write_wav(tmp_path / "media" / "Guitar.wav", frames=441_000)  # 10s @ 44100Hz
    ref = AudioFileRef(filename="Guitar.wav", track_name="Guitar", take_number=0, is_comp=False, comp_name="", file_path=wav)
    ref.start_position_samples = 88_200  # 2s -> beat 4 at 120bpm
    project = _timeline_project("Anchored", ["Guitar"], [ref])
    project.timeline = Timeline(tempo_events=[TempoEvent(beat=8.0, bpm=140.0)], markers=[])

    als_path = generate_als(project, tmp_path / "out", copy_audio=False)
    with gzip.open(als_path, "rb") as f:
        root = ET.fromstring(f.read())

    clip = root.find(".//Events/AudioClip")
    # 2s at 120bpm reach the breakpoint (4 beats), the remaining 8s run at 140bpm (18.667 beats).
    expected_beats = 4.0 + 8.0 * 140.0 / 60.0
    assert clip.find("CurrentStart").get("Value") == "4.0"
    assert abs(float(clip.find("CurrentEnd").get("Value")) - (4.0 + expected_beats)) < 1e-9
    assert abs(float(clip.find("Loop/LoopEnd").get("Value")) - expected_beats) < 1e-6
    markers = clip.find("WarpMarkers").findall("WarpMarker")
    assert [(m.get("SecTime"), m.get("BeatTime")) for m in markers][:2] == [("0", "0"), ("2.0", "4.0")]
    assert abs(float(markers[2].get("BeatTime")) - expected_beats) < 1e-9


def test_generate_als_tempo_automation_writes_step_pairs(tmp_path):
    project = _timeline_project("TempoAutomation", [], [])
    project.timeline = Timeline(
        tempo_events=[
            TempoEvent(beat=8.0, bpm=140.0),
            TempoEvent(beat=16.0, bpm=140.0),  # no change from the previous breakpoint: no step
            TempoEvent(beat=24.0, bpm=90.0),
        ],
        markers=[],
    )

    als_path = generate_als(project, tmp_path / "out", copy_audio=False)
    with gzip.open(als_path, "rb") as f:
        root = ET.fromstring(f.read())

    manual = root.find(".//Tempo/Manual")
    assert manual.get("Value") == "120"

    live_set = root.find("LiveSet")
    track = main_track(live_set)
    tempo_param = track.find("DeviceChain/Mixer/Tempo")
    events = parameter_events(track, tempo_param)
    values = [(e.get("Time"), e.get("Value")) for e in events]

    assert values[0] == ("-63072000", "120")
    assert ("8", "120") in values
    assert ("8", "140") in values
    assert ("24", "140") in values
    assert ("24", "90") in values
    assert len(values) == 5

    ids = [e.get("Id") for e in events]
    assert len(ids) == len(set(ids))


def test_generate_als_locators_schema_and_order(tmp_path):
    project = _timeline_project("Locators", [], [])
    project.timeline = Timeline(
        tempo_events=[],
        markers=[TimelineMarker(beat=32.0, name="Chorus"), TimelineMarker(beat=0.0, name="Intro")],
    )

    als_path = generate_als(project, tmp_path / "out", copy_audio=False)
    with gzip.open(als_path, "rb") as f:
        root = ET.fromstring(f.read())

    locators = root.find(".//Locators/Locators").findall("Locator")
    assert [loc.get("Id") for loc in locators] == ["0", "1"]
    assert [loc.find("Name").get("Value") for loc in locators] == ["Intro", "Chorus"]
    assert [loc.find("Time").get("Value") for loc in locators] == ["0", "32"]
    for locator in locators:
        assert locator.find("LomId").get("Value") == "0"
        assert locator.find("Annotation").get("Value") == ""
        assert locator.find("IsSongStart").get("Value") == "false"


def test_generate_als_unique_critical_ids_with_tempo_automation_and_locators(tmp_path):
    """The global ID invariant must hold with tempo automation and locators present."""
    audio_path = write_test_wav(tmp_path / "media" / "Guitar.wav")
    ref = AudioFileRef(filename="Guitar.wav", track_name="Guitar", take_number=0, is_comp=False, comp_name="", file_path=audio_path)
    project = _timeline_project("Synthetic IDs Timeline", ["Guitar"], [ref])
    project.timeline = Timeline(
        tempo_events=[TempoEvent(beat=8.0, bpm=140.0)],
        markers=[TimelineMarker(beat=0.0, name="Intro"), TimelineMarker(beat=32.0, name="Chorus")],
    )

    als_path = generate_als(
        project,
        tmp_path / "output",
        copy_audio=False,
        template_path=_BUNDLED_TEMPLATE,
        keep_unwarped=["Guitar"],
    )
    with gzip.open(als_path, "rb") as f:
        root = ET.fromstring(f.read())
    _assert_unique_critical_ids(root)


def test_unmatched_keep_unwarped_warnings_usable_without_generate_als():
    """The unmatched-pattern warning is plain string matching, so a caller
    (e.g. a report-only path that never calls generate_als) can compute it
    directly from track names alone.
    """
    warnings = unmatched_keep_unwarped_warnings(["Drums", "Bass"], ["Drums", "Nonexistent*"])
    assert warnings == ["--keep-unwarped pattern 'Nonexistent*' did not match any track name."]
    assert unmatched_keep_unwarped_warnings(["Drums"], ["Drums"]) == []
    assert unmatched_keep_unwarped_warnings(["Drums"], None) == []


def test_generate_als_warped_offset_beats_use_flat_content_relative_formula(tmp_path):
    """content_offset_samples is a slice position within the source file, not
    an arrangement position, so it must convert to beats on the flat
    single-tempo formula even when a timeline tempo breakpoint sits under
    the clip's arrangement position.
    """
    wav = _write_wav(tmp_path / "media" / "Guitar.wav", frames=220_500)  # 5s @ 44100Hz
    ref = AudioFileRef(
        filename="Guitar.wav",
        track_name="Guitar",
        take_number=0,
        is_comp=False,
        comp_name="",
        file_path=wav,
        content_offset_samples=132_300,  # 3s slice offset within the source file
        content_duration_samples=88_200,  # 2s of content
    )
    project = _timeline_project("OffsetWithBreakpoint", ["Guitar"], [ref])
    project.timeline = Timeline(tempo_events=[TempoEvent(beat=2.0, bpm=240.0)], markers=[])

    als_path = generate_als(project, tmp_path / "out", copy_audio=False)
    with gzip.open(als_path, "rb") as f:
        root = ET.fromstring(f.read())

    clip = root.find(".//Events/AudioClip")
    # 3s at the flat 120bpm formula is 6 beats. Routing the offset through
    # the breakpoint at beat 2 (as if it were an arrangement position)
    # would instead give 10 beats.
    assert clip.find("Loop/LoopStart").get("Value") == "6"
    assert clip.find("Loop/LoopEnd").get("Value") == "12"


def test_write_locators_strips_control_characters_from_marker_name(tmp_path):
    """A --timeline marker name carrying a raw control character must not
    corrupt the generated .als, which is itself XML.
    """
    wav = _write_wav(tmp_path / "media" / "Guitar.wav")
    ref = AudioFileRef(filename="Guitar.wav", track_name="Guitar", take_number=0, is_comp=False, comp_name="", file_path=wav)
    project = _timeline_project("TaintedMarker", ["Guitar"], [ref])
    project.timeline = Timeline(tempo_events=[], markers=[TimelineMarker(beat=0.0, name="Chorus\x07Bell")])

    als_path = generate_als(project, tmp_path / "out", copy_audio=False)
    with gzip.open(als_path, "rb") as f:
        xml_bytes = f.read()

    # Must round-trip through ElementTree's own parser without error.
    root = ET.fromstring(xml_bytes)
    locator = root.find(".//Locators/Locators/Locator")
    assert locator.find("Name").get("Value") == "ChorusBell"
