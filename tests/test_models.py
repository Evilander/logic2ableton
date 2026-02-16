from pathlib import Path

from logic2ableton.models import AudioFileRef, LogicProject, PluginInstance, parse_audio_filename, samples_to_beats


def test_audio_file_ref_creation():
    ref = AudioFileRef(
        filename="KICK IN#01.wav",
        track_name="KICK IN",
        take_number=1,
        is_comp=False,
        comp_name="",
        file_path=Path("/fake/path/KICK IN#01.wav"),
    )
    assert ref.track_name == "KICK IN"
    assert ref.take_number == 1
    assert ref.is_comp is False


def test_plugin_instance_creation():
    plugin = PluginInstance(
        name="Big Guitar 2",
        au_type="aufx",
        au_subtype="TG5M",
        au_manufacturer="ksWV",
        is_waves=True,
        raw_plist={"name": "Big Guitar 2"},
    )
    assert plugin.au_subtype == "TG5M"
    assert plugin.is_waves is True


def test_logic_project_creation():
    project = LogicProject(
        name="Might Last Forever",
        tempo=120.0,
        time_sig_numerator=4,
        time_sig_denominator=4,
        sample_rate=44100,
        audio_files=[],
        plugins=[],
        track_names=["KICK IN", "Tyler Amp"],
        alternative=0,
    )
    assert project.tempo == 120.0
    assert len(project.track_names) == 2


def test_parse_take_filename():
    result = parse_audio_filename("KICK IN#01.wav")
    assert result == ("KICK IN", 1, False, "")


def test_parse_take_filename_high_number():
    result = parse_audio_filename("Tyler Amp#03.wav")
    assert result == ("Tyler Amp", 3, False, "")


def test_parse_comp_filename():
    result = parse_audio_filename("scratch vox 2_ Comp A.wav")
    assert result == ("scratch vox 2", 0, True, "Comp A")


def test_parse_simple_filename():
    result = parse_audio_filename("scratch vox 1.wav")
    assert result == ("scratch vox 1", 0, False, "")


def test_parse_bip_filename():
    result = parse_audio_filename("BASS GUITAR_bip.wav")
    assert result == ("BASS GUITAR", 0, False, "")


def test_parse_reference_track():
    result = parse_audio_filename("01 One Headlight.wav")
    assert result == ("01 One Headlight", 0, False, "")


def test_parse_keys_filename():
    result = parse_audio_filename("keys#01.wav")
    assert result == ("keys", 1, False, "")


def test_parse_kick_drum_sc():
    result = parse_audio_filename("Kick Drum (SC)#01.wav")
    assert result == ("Kick Drum (SC)", 1, False, "")


# Phase 2: Region timing tests

def test_audio_file_ref_default_start_position():
    ref = AudioFileRef(
        filename="test.wav",
        track_name="test",
        take_number=1,
        is_comp=False,
        comp_name="",
        file_path=Path("/fake/test.wav"),
    )
    assert ref.start_position_samples == 0


def test_audio_file_ref_with_start_position():
    ref = AudioFileRef(
        filename="test.wav",
        track_name="test",
        take_number=1,
        is_comp=False,
        comp_name="",
        file_path=Path("/fake/test.wav"),
        start_position_samples=23_725_800,
    )
    assert ref.start_position_samples == 23_725_800


def test_samples_to_beats_zero():
    assert samples_to_beats(0, 120, 44100) == 0.0


def test_samples_to_beats_one_second():
    # 1 second at 120 BPM = 2 beats
    assert samples_to_beats(44100, 120, 44100) == 2.0


def test_samples_to_beats_kick_in_01():
    # 23,725,800 samples at 120 BPM / 44100 = 1076 beats (269 bars * 4)
    assert samples_to_beats(23_725_800, 120, 44100) == 1076.0


def test_samples_to_beats_kick_in_02():
    # 11,554,200 samples at 120 BPM / 44100 = 524 beats (131 bars * 4)
    assert samples_to_beats(11_554_200, 120, 44100) == 524.0
