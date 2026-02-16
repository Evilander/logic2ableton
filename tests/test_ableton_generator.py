import gzip
import xml.etree.ElementTree as ET
from pathlib import Path

from logic2ableton.ableton_generator import generate_als, _pick_best_clip
from logic2ableton.logic_parser import parse_logic_project
from logic2ableton.models import AudioFileRef

TEST_PROJECT = Path("Might Last Forever.logicx")


def test_generate_als_creates_file(tmp_path):
    project = parse_logic_project(TEST_PROJECT)
    als_path = generate_als(project, tmp_path / "output", copy_audio=False)
    assert als_path.exists()
    assert als_path.suffix == ".als"
    assert als_path.name == "Might Last Forever.als"


def test_generate_als_is_valid_gzipped_xml(tmp_path):
    project = parse_logic_project(TEST_PROJECT)
    als_path = generate_als(project, tmp_path / "output", copy_audio=False)
    with gzip.open(als_path, "rb") as f:
        xml_content = f.read().decode("utf-8")
    root = ET.fromstring(xml_content)
    assert root.tag == "Ableton"


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


def test_generate_als_has_correct_tempo(tmp_path):
    project = parse_logic_project(TEST_PROJECT)
    als_path = generate_als(project, tmp_path / "output", copy_audio=False)
    with gzip.open(als_path, "rb") as f:
        root = ET.fromstring(f.read())
    tempo = root.find(".//Tempo/Manual")
    assert tempo.get("Value") == "120"


def test_generate_als_copies_audio(tmp_path):
    project = parse_logic_project(TEST_PROJECT)
    output_dir = tmp_path / "output"
    generate_als(project, output_dir, copy_audio=True)
    samples_dir = output_dir / "Might Last Forever Project" / "Samples" / "Imported"
    assert samples_dir.exists()
    wav_files = list(samples_dir.glob("*.wav"))
    assert len(wav_files) == len(project.audio_files)


# Clip placement tests

def test_generate_als_has_arrangement_clips(tmp_path):
    project = parse_logic_project(TEST_PROJECT)
    als_path = generate_als(project, tmp_path / "output", copy_audio=False)
    with gzip.open(als_path, "rb") as f:
        root = ET.fromstring(f.read())
    clips = root.findall(".//Events/AudioClip")
    assert len(clips) > 0


def test_generate_als_at_least_one_clip_per_track(tmp_path):
    """Each track should have at least one clip."""
    project = parse_logic_project(TEST_PROJECT)
    als_path = generate_als(project, tmp_path / "output", copy_audio=False)
    with gzip.open(als_path, "rb") as f:
        root = ET.fromstring(f.read())
    tracks = root.findall(".//Tracks/AudioTrack")
    clips = root.findall(".//Events/AudioClip")
    assert len(clips) >= len(tracks)


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

def test_generate_als_schema_version(tmp_path):
    project = parse_logic_project(TEST_PROJECT)
    als_path = generate_als(project, tmp_path / "output", copy_audio=False)
    with gzip.open(als_path, "rb") as f:
        root = ET.fromstring(f.read())
    assert root.get("MajorVersion") == "5"
    # Template from Ableton Live 12
    assert root.get("SchemaChangeCount") is not None


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


def test_generate_als_has_return_tracks(tmp_path):
    project = parse_logic_project(TEST_PROJECT)
    als_path = generate_als(project, tmp_path / "output", copy_audio=False)
    with gzip.open(als_path, "rb") as f:
        root = ET.fromstring(f.read())
    returns = root.findall(".//Tracks/ReturnTrack")
    assert len(returns) >= 2


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


def test_generate_als_clip_has_fades(tmp_path):
    project = parse_logic_project(TEST_PROJECT)
    als_path = generate_als(project, tmp_path / "output", copy_audio=False)
    with gzip.open(als_path, "rb") as f:
        root = ET.fromstring(f.read())
    clip = root.find(".//Events/AudioClip")
    fades = clip.find("Fades")
    assert fades is not None
    assert fades.find("FadeInLength") is not None


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


def test_generate_als_unique_critical_ids(tmp_path):
    """AutomationTarget, ModulationTarget, Pointee IDs must be globally unique."""
    project = parse_logic_project(TEST_PROJECT)
    als_path = generate_als(project, tmp_path / "output", copy_audio=False)
    with gzip.open(als_path, "rb") as f:
        root = ET.fromstring(f.read())
    critical_ids = {}
    for elem in root.iter():
        if elem.tag in ("AutomationTarget", "ModulationTarget", "Pointee"):
            id_val = elem.get("Id")
            if id_val is not None:
                assert id_val not in critical_ids, f"Duplicate {elem.tag} Id={id_val}"
                critical_ids[id_val] = elem.tag


def test_generate_als_liveset_metadata(tmp_path):
    project = parse_logic_project(TEST_PROJECT)
    als_path = generate_als(project, tmp_path / "output", copy_audio=False)
    with gzip.open(als_path, "rb") as f:
        root = ET.fromstring(f.read())
    live_set = root.find("LiveSet")
    assert live_set.find("OverwriteProtectionNumber") is not None
    assert live_set.find("LomId") is not None
    assert live_set.find("LomIdView") is not None
