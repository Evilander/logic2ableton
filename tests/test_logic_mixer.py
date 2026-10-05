"""Logic's mixer: faders, pans, mutes and bus routing, read from the project and rebuilt in Live."""

import gzip
import json
import math
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from logic2ableton.ableton_generator import _BUNDLED_TEMPLATE, generate_als
from logic2ableton.cli import main
from logic2ableton.logic_parser import parse_logic_project
from logic2ableton.logic_project_data import decode_project_data
from logic2ableton.models import HardwareOutput
from logic2ableton.report import generate_report
from scripts.fixture_builders import build_logic_arrangement_project_data, build_synthetic_logicx, write_test_wav

RATE = 44_100

# A band session: kick and snare play into bus 2, where the "Drums" aux listens; the overhead
# plays into bus 5, whose aux "Overheads" itself plays into bus 2. The click is a software
# instrument on outputs 13-14, the bass sits on outputs 3-4, and "Verb" is fed by sends only.
BAND_STRIPS = [
    {"label": "Audio 1", "track": 1, "fader": 50.6107, "bus": 2},
    {"label": "Audio 2", "track": 2, "muted": True, "bus": 2},
    {"label": "Audio 3", "track": 3, "fader": 63.7795, "pan": 29, "bus": 5},
    {"label": "Inst 1", "track": 4, "fader": 80.2126, "pan": 0, "output": 6},
    {"label": "Audio 4", "track": 5, "fader": 127.0, "stereo": True, "output": 1},
    {"label": "Aux 1", "track": 9, "stereo": True, "fader": 71.4895, "from_bus": 2},
    {"label": "Aux 2", "track": 10, "stereo": True, "pan": 71, "from_bus": 5, "bus": 2},
    {"label": "Aux 3", "track": 11, "stereo": True, "from_bus": 1},
]
BAND_TRACKS = {1: "Kick", 2: "Snare", 3: "Overhead", 4: "Click", 5: "Bass", 9: "Drums", 10: "Overheads", 11: "Verb"}
AUDIO = {1: (10, "Kick.wav"), 2: (20, "Snare.wav"), 3: (30, "Overhead.wav"), 5: (50, "Bass.wav")}


def _data(strips=None, *, tracks=None, **extra) -> bytes:
    mixer = None
    if strips is not None:
        mixer = {"stereo_outputs": 8, "stereo_inputs": 4, "buses": 8, "spare_aux": True, "strips": strips}
        mixer.update(extra.pop("pool", {}))
    return build_logic_arrangement_project_data(
        tracks=tracks or BAND_TRACKS,
        sequences=[{"id": 44, "name": "Click", "length": 3840, "notes": [(0, 60, 100, 240)]}],
        midi_regions=[{"bar": 1, "track": 4, "sequence": 44, "lane": 4}],
        audio_files={file_id: name for file_id, name in AUDIO.values()},
        audio_regions=[
            {"file": file_id, "index": 0, "name": Path(name).stem, "offset": 0, "length": 2 * RATE}
            for file_id, name in AUDIO.values()
        ],
        audio_placements=[
            {"bar": 1, "track": track, "file": file_id, "lane": track} for track, (file_id, _) in AUDIO.items()
        ],
        mixer=mixer,
        **extra,
    )


def _logicx(tmp_path: Path, strips=BAND_STRIPS, **extra) -> Path:
    names = [name for _, name in AUDIO.values()]
    logicx = build_synthetic_logicx(tmp_path / "source", project_data=_data(strips, **extra), used_audio_files=names)
    for name in names:
        write_test_wav(logicx / "Media" / "Audio Files" / name, frames=2 * RATE)
    return logicx


_ROUTING = ("output", "bus", "mono_output", "no_output")


def _strips(**changes) -> list[dict]:
    """The band's strips with some of them changed, by label; a new routing replaces the old one."""
    strips = []
    for strip in BAND_STRIPS:
        change = changes.get(strip["label"], {})
        rerouted = any(key in change for key in _ROUTING)
        strips.append({**{key: value for key, value in strip.items() if not (rerouted and key in _ROUTING)}, **change})
    return strips


def _live_tracks(als_path: Path) -> tuple[ET.Element, list[ET.Element]]:
    with gzip.open(als_path, "rb") as handle:
        root = ET.fromstring(handle.read())
    return root, list(root.find("LiveSet/Tracks"))


def _name(track: ET.Element) -> str:
    return track.find("Name/EffectiveName").get("Value")


def _mixer_value(track: ET.Element, path: str) -> str:
    return track.find(f"DeviceChain/Mixer/{path}").get("Value")


def _output_target(track: ET.Element) -> str:
    return track.find("DeviceChain/AudioOutputRouting/Target").get("Value")


# --- decoding ---------------------------------------------------------------------


def test_fader_pan_and_mute_are_read_from_each_tracks_channel_strip():
    mixer = decode_project_data(_data(BAND_STRIPS)).mixer

    kick, snare, overhead, click, bass = (mixer.strip_for_track(track) for track in (1, 2, 3, 4, 5))
    assert (kick.label, kick.name, kick.track_id) == ("Audio 1", "Kick", 1)
    # Levels typed into Logic as -10, -6 and -2 dB are stored as these fader values.
    assert kick.volume_db == pytest.approx(-10.0, abs=1e-4)
    assert overhead.volume_db == pytest.approx(-5.98, abs=0.01)
    assert click.volume_db == pytest.approx(-2.0, abs=1e-4)
    assert snare.volume_db == 0.0 and snare.muted and not kick.muted
    assert bass.volume_db == pytest.approx(5.98, abs=0.01)
    assert (kick.pan_position, click.pan_position) == (0.0, -1.0)
    assert overhead.pan_position == pytest.approx(-35 / 64)
    assert bass.stereo and not kick.stereo


def test_a_fader_at_the_bottom_is_silence_and_hard_right_is_full_right():
    mixer = decode_project_data(_data(_strips(**{"Audio 1": {"fader": 0.0, "pan": 127}}))).mixer
    kick = mixer.strip_for_track(1)
    assert kick.volume_db == -math.inf
    assert kick.pan_position == 1.0


@pytest.mark.parametrize("stereo_outputs", [2, 10, 64])
def test_a_bus_is_found_whatever_number_the_interface_gives_it(stereo_outputs):
    """The output is an index into [stereo outputs, buses, mono outputs], so the same bus is
    stored as a different number in a project made on a different audio interface."""
    data = _data(BAND_STRIPS, pool={"stereo_outputs": stereo_outputs, "stereo_inputs": stereo_outputs - 1})
    mixer = decode_project_data(data).mixer

    kick = mixer.strip_for_track(1)
    assert kick.output == stereo_outputs + 1
    assert mixer.destination(kick) == ("bus", 2)
    assert [aux.name for aux in mixer.bus_listeners(2)] == ["Drums"]
    assert mixer.destination(mixer.strip_for_track(5)) == ("output", 1)


def test_mono_outputs_come_after_the_buses():
    strips = _strips(**{"Audio 1": {"mono_output": 15}, "Aux 3": {"no_output": True}})
    mixer = decode_project_data(_data(strips)).mixer

    assert mixer.destination(mixer.strip_for_track(1)) == ("mono output", 15)
    assert mixer.destination(mixer.strip_for_track(11)) is None


def test_a_mono_aux_counts_its_bus_after_the_mono_inputs():
    strips = _strips(**{"Aux 1": {"stereo": False}})
    mixer = decode_project_data(_data(strips)).mixer

    drums = mixer.strip_for_track(9)
    assert drums.input == 8 + 1  # eight mono inputs, then the buses
    assert mixer.source_bus(drums) == 2
    assert [aux.name for aux in mixer.bus_listeners(2)] == ["Drums"]


def test_unused_aux_strips_in_the_pool_are_not_on_the_mixer():
    """Logic keeps an aux strip for every bus whether or not the project uses it."""
    mixer = decode_project_data(_data(BAND_STRIPS)).mixer

    spare = [strip for strip in mixer.strips.values() if strip.label == "Aux 107"]
    assert spare and mixer.source_bus(spare[0]) == 7
    assert mixer.bus_listeners(7) == []


def test_objects_that_are_not_channel_strips_get_no_strip():
    """Real projects hold a MIDI click object whose bytes after the name read as "strip 9"; only an
    object of the channel strip type names a strip there."""
    data = _data(BAND_STRIPS, tracks={**BAND_TRACKS, 20: "Folder", 21: "MIDI Click"}, pool={"decoys": {21: 0}})
    mixer = decode_project_data(data).mixer

    assert mixer.strip_for_track(20) is None
    assert mixer.strip_for_track(21) is None
    kick = mixer.strip_for_track(1)
    assert (kick.index, kick.name, kick.track_id) == (0, "Kick", 1)


def test_two_track_objects_on_one_strip_both_get_it():
    strips = [dict(strip, track=[1, 2]) if strip["label"] == "Audio 1" else strip for strip in BAND_STRIPS if strip["label"] != "Audio 2"]
    mixer = decode_project_data(_data(strips)).mixer

    assert mixer.strip_for_track(1) is mixer.strip_for_track(2)
    assert mixer.strip_for_track(1).name == "Kick"


def test_bullet_in_front_of_the_devices_outputs_does_not_shift_the_buses():
    """Logic marks the outputs the audio device has; the stereo ones still count as stereo outputs."""
    mixer = decode_project_data(_data(BAND_STRIPS, pool={"device_outputs": 4})).mixer

    assert sum(1 for strip in mixer.strips.values() if strip.label == "Output 1-2") == 1
    assert (mixer.stereo_outputs, mixer.mono_outputs) == (8, 16)
    assert mixer.destination(mixer.strip_for_track(1)) == ("bus", 2)


def test_output_number_past_the_last_output_is_no_output():
    mixer = decode_project_data(_data(_strips(**{"Audio 1": {"output_code": 0xFFFE}}))).mixer
    assert mixer.destination(mixer.strip_for_track(1)) is None


def test_project_without_channel_strips_has_an_empty_mixer(tmp_path):
    assert decode_project_data(_data(None)).mixer.strips == {}

    project = parse_logic_project(_logicx(tmp_path, strips=None))
    assert project.mixer_state is None and not project.mixer_from_project
    assert not project.track_groups and not project.track_group and not project.track_outputs
    assert "MIXER STATE: defaults" in generate_report(project, [])


# --- the project ------------------------------------------------------------------


def test_tracks_take_logics_fader_pan_and_mute(tmp_path):
    project = parse_logic_project(_logicx(tmp_path))

    assert project.mixer_from_project
    assert set(project.mixer_state) == {"Kick", "Snare", "Overhead", "Bass", "Click"}
    assert project.mixer_state["Kick"].volume_db == pytest.approx(-10.0, abs=1e-4)
    assert project.mixer_state["Snare"].is_muted and not project.mixer_state["Kick"].is_muted
    assert project.mixer_state["Overhead"].pan == pytest.approx(-35 / 64)
    assert project.mixer_state["Click"].volume_db == pytest.approx(-2.0, abs=1e-4)
    assert project.mixer_state["Click"].pan == -1.0


def test_a_bus_and_the_aux_listening_on_it_become_a_group(tmp_path):
    project = parse_logic_project(_logicx(tmp_path))

    assert project.track_group == {"Kick": "Drums", "Snare": "Drums", "Overhead": "Overheads"}
    assert list(project.track_groups) == ["Drums", "Overheads"]
    drums, overheads = project.track_groups["Drums"], project.track_groups["Overheads"]
    assert drums.mixer.volume_db == pytest.approx(-4.0, abs=1e-4) and drums.parent is None
    assert overheads.parent == "Drums" and overheads.mixer.pan == pytest.approx(7 / 63)
    # "Verb" is fed by sends only: nothing plays into it, so it is not rebuilt.
    assert "Verb" not in project.track_groups
    assert not any("bus" in warning.lower() for warning in project.compatibility_warnings)


def test_tracks_on_other_interface_outputs_are_recorded(tmp_path):
    strips = _strips(**{"Audio 1": {"mono_output": 15}, "Aux 1": {"output": 2}})
    project = parse_logic_project(_logicx(tmp_path, strips=strips))

    assert project.track_outputs == {
        "Kick": HardwareOutput(first_channel=15, stereo=False),
        "Bass": HardwareOutput(first_channel=3),
        "Click": HardwareOutput(first_channel=13),
    }
    assert [project.track_outputs[name].label for name in ("Kick", "Bass", "Click")] == ["15", "3-4", "13-14"]
    assert project.track_groups["Drums"].output == HardwareOutput(first_channel=5)
    assert project.track_groups["Overheads"].output is None


def test_bus_that_no_aux_listens_on_leaves_the_track_alone(tmp_path):
    project = parse_logic_project(_logicx(tmp_path, strips=_strips(**{"Audio 1": {"bus": 7}})))

    assert "Kick" not in project.track_group
    assert project.mixer_state["Kick"].volume_db == pytest.approx(-10.0, abs=1e-4)
    assert any(
        "1 track(s) play into a Logic bus with no aux channel listening on it" in warning and "Kick" in warning
        for warning in project.compatibility_warnings
    )


def test_bus_with_two_aux_channels_is_not_grouped(tmp_path):
    strips = _strips(**{"Aux 3": {"from_bus": 2}})
    project = parse_logic_project(_logicx(tmp_path, strips=strips))

    assert set(project.track_group) == {"Overhead"}
    assert list(project.track_groups) == ["Overheads"]
    assert project.track_groups["Overheads"].parent is None
    assert any("more than one aux channel listens on" in warning for warning in project.compatibility_warnings)


def test_aux_channels_feeding_each_other_do_not_loop_forever(tmp_path):
    strips = _strips(**{"Aux 1": {"bus": 5}})  # Drums -> bus 5 -> Overheads -> bus 2 -> Drums
    project = parse_logic_project(_logicx(tmp_path, strips=strips))

    assert project.track_group["Kick"] == "Drums"
    assert project.track_groups["Drums"].parent == "Overheads"
    assert project.track_groups["Overheads"].parent is None
    assert any(
        "1 group(s) play into a Logic bus that leads back to the same channel, so they play to the main output: Overheads"
        in warning for warning in project.compatibility_warnings
    )


def test_aux_playing_into_a_bus_nobody_listens_on_is_reported(tmp_path):
    project = parse_logic_project(_logicx(tmp_path, strips=_strips(**{"Aux 1": {"bus": 7}})))

    assert project.track_group["Kick"] == "Drums" and project.track_groups["Drums"].parent is None
    assert any(
        "1 group(s) play into a Logic bus with no aux channel listening on it, so they play to the main output: Drums"
        in warning for warning in project.compatibility_warnings
    )


def test_two_aux_channels_with_one_name_become_two_groups(tmp_path):
    tracks = {**BAND_TRACKS, 10: "Drums"}
    project = parse_logic_project(_logicx(tmp_path, tracks=tracks))

    assert project.track_group == {"Kick": "Drums", "Snare": "Drums", "Overhead": "Drums (2)"}
    assert project.track_groups["Drums (2)"].parent == "Drums"


def test_the_mixer_is_the_current_one_not_one_from_the_undo_history(tmp_path):
    old = {"stereo_outputs": 8, "stereo_inputs": 4, "buses": 8, "strips": _strips(**{"Audio 1": {"muted": True, "fader": 20.0}})}
    project = parse_logic_project(_logicx(tmp_path, history=[{"tracks": BAND_TRACKS, "mixer": old}]))

    assert not project.mixer_state["Kick"].is_muted
    assert project.mixer_state["Kick"].volume_db == pytest.approx(-10.0, abs=1e-4)


# --- the Live set -----------------------------------------------------------------


def test_live_set_has_the_groups_with_their_tracks_inside(tmp_path):
    project = parse_logic_project(_logicx(tmp_path))
    root, tracks = _live_tracks(generate_als(project, tmp_path / "out", copy_audio=False, template_path=_BUNDLED_TEMPLATE))

    assert [(track.tag, _name(track)) for track in tracks if track.tag != "ReturnTrack"] == [
        ("GroupTrack", "Drums"),
        ("AudioTrack", "Kick"),
        ("AudioTrack", "Snare"),
        ("GroupTrack", "Overheads"),
        ("AudioTrack", "Overhead"),
        ("AudioTrack", "Bass"),
        ("MidiTrack", "Click"),
    ]
    by_name = {_name(track): track for track in tracks}
    drums, overheads = by_name["Drums"], by_name["Overheads"]
    assert drums.find("TrackGroupId").get("Value") == "-1" and _output_target(drums) == "AudioOut/Main"
    for member in ("Kick", "Snare", "Overheads"):
        assert by_name[member].find("TrackGroupId").get("Value") == drums.get("Id")
        assert _output_target(by_name[member]) == "AudioOut/GroupTrack"
    assert by_name["Overhead"].find("TrackGroupId").get("Value") == overheads.get("Id")
    for loose in ("Bass", "Click"):
        assert by_name[loose].find("TrackGroupId").get("Value") == "-1"
        assert _output_target(by_name[loose]) == "AudioOut/Main"
    assert len({track.get("Id") for track in tracks}) == len(tracks)


def test_live_set_carries_levels_pans_and_mutes(tmp_path):
    project = parse_logic_project(_logicx(tmp_path))
    _, tracks = _live_tracks(generate_als(project, tmp_path / "out", copy_audio=False, template_path=_BUNDLED_TEMPLATE))
    by_name = {_name(track): track for track in tracks}

    assert float(_mixer_value(by_name["Kick"], "Volume/Manual")) == pytest.approx(10 ** (-10 / 20), rel=1e-4)
    assert float(_mixer_value(by_name["Drums"], "Volume/Manual")) == pytest.approx(10 ** (-4 / 20), rel=1e-4)
    assert float(_mixer_value(by_name["Overheads"], "Pan/Manual")) == pytest.approx(7 / 63)
    assert _mixer_value(by_name["Snare"], "Speaker/Manual") == "false"
    assert _mixer_value(by_name["Kick"], "Speaker/Manual") == "true"
    # The software instrument's MIDI track gets its channel strip too.
    assert float(_mixer_value(by_name["Click"], "Volume/Manual")) == pytest.approx(10 ** (-2 / 20), rel=1e-4)
    assert float(_mixer_value(by_name["Click"], "Pan/Manual")) == -1.0


def test_group_track_is_stored_the_way_live_stores_one(tmp_path):
    """Live 12 refuses a set whose group track keeps what only a clip track has."""
    project = parse_logic_project(_logicx(tmp_path))
    root, tracks = _live_tracks(generate_als(project, tmp_path / "out", copy_audio=False, template_path=_BUNDLED_TEMPLATE))
    group = next(track for track in tracks if track.tag == "GroupTrack")
    audio = next(track for track in tracks if track.tag == "AudioTrack")

    scenes = len(audio.findall("DeviceChain/MainSequencer/ClipSlotList/ClipSlot"))
    assert scenes > 0
    assert [slot.tag for slot in group.find("Slots")] == ["GroupTrackSlot"] * scenes
    assert group.find("DeviceChain/MainSequencer") is None
    assert len(group.find("DeviceChain/FreezeSequencer/ClipSlotList")) == 0
    for tag in ("SavedPlayingSlot", "SavedPlayingOffset", "NeedArrangerRefreeze", "PostProcessFreezeClips"):
        assert group.find(tag) is None and audio.find(tag) is not None
    chain = [child.tag for child in group.find("DeviceChain")]
    assert chain.index("DeviceChain") < chain.index("FreezeSequencer")
    children = [child.tag for child in group]
    assert children.index("Slots") < children.index("Freeze") < children.index("DeviceChain")

    seen = {}
    for element in root.iter():
        if element.tag in ("AutomationTarget", "ModulationTarget", "Pointee"):
            assert element.get("Id") not in seen, f"duplicate {element.tag} Id"
            seen[element.get("Id")] = element.tag
    assert int(root.find("LiveSet/NextPointeeId").get("Value")) > max(int(value) for value in seen)


def test_group_opens_unfolded_and_without_the_templates_devices(tmp_path):
    with gzip.open(_BUNDLED_TEMPLATE, "rb") as handle:
        template = ET.fromstring(handle.read())
    audio = next(track for track in template.find("LiveSet/Tracks") if track.tag == "AudioTrack")
    ET.SubElement(audio.find("DeviceChain/DeviceChain/Devices"), "Eq8", {"Id": "0"})
    audio.find("TrackUnfolded").set("Value", "false")
    custom = tmp_path / "WithDevice.als"
    with gzip.open(custom, "wb") as handle:
        handle.write(ET.tostring(template))

    project = parse_logic_project(_logicx(tmp_path))
    _, tracks = _live_tracks(generate_als(project, tmp_path / "out", copy_audio=False, template_path=custom))
    group = next(track for track in tracks if track.tag == "GroupTrack")
    kick = next(track for track in tracks if _name(track) == "Kick")

    assert group.find("TrackUnfolded").get("Value") == "true"
    assert len(group.find("DeviceChain/DeviceChain/Devices")) == 0
    assert len(kick.find("DeviceChain/DeviceChain/Devices")) == 1


def test_template_from_a_newer_live_keeps_the_groups_freeze_slots(tmp_path):
    """Sets from Live versions that can freeze a group hold a freeze slot per scene in it; their
    tracks no longer carry NeedArrangerRefreeze. (A set built this way was loaded in Live 12.4.5.)"""
    with gzip.open(_BUNDLED_TEMPLATE, "rb") as handle:
        template = ET.fromstring(handle.read())
    for track in template.find("LiveSet/Tracks"):
        flag = track.find("NeedArrangerRefreeze")
        if flag is not None:
            track.remove(flag)
    newer = tmp_path / "Newer.als"
    with gzip.open(newer, "wb") as handle:
        handle.write(ET.tostring(template))

    project = parse_logic_project(_logicx(tmp_path))
    _, tracks = _live_tracks(generate_als(project, tmp_path / "out", copy_audio=False, template_path=newer))
    group = next(track for track in tracks if track.tag == "GroupTrack")
    audio = next(track for track in tracks if track.tag == "AudioTrack")

    scenes = len(audio.findall("DeviceChain/MainSequencer/ClipSlotList/ClipSlot"))
    assert len(group.find("DeviceChain/FreezeSequencer/ClipSlotList")) == scenes > 0


def test_template_track_that_sits_in_a_group_does_not_pass_that_on(tmp_path):
    with gzip.open(_BUNDLED_TEMPLATE, "rb") as handle:
        template = ET.fromstring(handle.read())
    for track in template.find("LiveSet/Tracks"):
        if track.tag in ("AudioTrack", "MidiTrack"):
            track.find("TrackGroupId").set("Value", "5")
            track.find("DeviceChain/AudioOutputRouting/Target").set("Value", "AudioOut/GroupTrack")
    grouped = tmp_path / "Grouped.als"
    with gzip.open(grouped, "wb") as handle:
        handle.write(ET.tostring(template))

    project = parse_logic_project(_logicx(tmp_path))
    _, tracks = _live_tracks(generate_als(project, tmp_path / "out", copy_audio=False, template_path=grouped))
    by_name = {_name(track): track for track in tracks}

    for loose in ("Drums", "Bass", "Click"):
        assert by_name[loose].find("TrackGroupId").get("Value") == "-1"
        assert _output_target(by_name[loose]) == "AudioOut/Main"
    assert by_name["Kick"].find("TrackGroupId").get("Value") == by_name["Drums"].get("Id")


def test_outputs_stay_on_main_unless_asked_for(tmp_path):
    strips = _strips(**{"Audio 1": {"mono_output": 15}, "Aux 1": {"output": 2}})
    project = parse_logic_project(_logicx(tmp_path, strips=strips))

    _, tracks = _live_tracks(generate_als(project, tmp_path / "plain", copy_audio=False, template_path=_BUNDLED_TEMPLATE))
    plain = {_name(track): _output_target(track) for track in tracks}
    assert plain["Kick"] == plain["Bass"] == plain["Click"] == plain["Drums"] == "AudioOut/Main"

    _, tracks = _live_tracks(generate_als(
        project, tmp_path / "routed", copy_audio=False, template_path=_BUNDLED_TEMPLATE, keep_outputs=True,
    ))
    routed = {_name(track): track.find("DeviceChain/AudioOutputRouting") for track in tracks}

    def routing(name):
        return tuple(routed[name].find(tag).get("Value") for tag in ("Target", "UpperDisplayString", "LowerDisplayString"))

    assert routing("Kick") == ("AudioOut/External/M14", "Ext. Out", "15")
    assert routing("Bass") == ("AudioOut/External/S1", "Ext. Out", "3/4")
    assert routing("Click") == ("AudioOut/External/S6", "Ext. Out", "13/14")
    assert routing("Drums") == ("AudioOut/External/S2", "Ext. Out", "5/6")
    # What plays into a group keeps playing into it.
    assert routing("Snare")[0] == routing("Overheads")[0] == routing("Overhead")[0] == "AudioOut/GroupTrack"


# --- report and command line ---------------------------------------------------------


def test_report_lists_the_mixer_as_read(tmp_path):
    strips = _strips(**{"Audio 1": {"mono_output": 15}})
    project = parse_logic_project(_logicx(tmp_path, strips=strips))
    report = generate_report(project, [])

    assert "MIXER (read from the Logic project, 5 tracks):" in report
    assert "  Kick - -10.0 dB, output 15 in Logic" in report
    assert '  Snare - +0.0 dB, MUTED, in group "Drums"' in report
    assert "  Click - -2.0 dB, pan 100%L, output 13-14 in Logic" in report
    assert "  Groups (2): " in report
    assert "    Drums - -4.0 dB" in report
    assert '    Overheads - +0.0 dB, pan 11%R, in group "Drums"' in report
    assert (
        "3 of the tracks and groups above play to another interface output in Logic and to Live's main output "
        "here; pass --keep-outputs"
    ) in report
    assert "Bus/send routing" not in report and "aux channels fed only by sends" in report

    routed = generate_report(project, [], keep_outputs=True)
    assert "  Kick - -10.0 dB, Ext. Out 15" in routed
    assert "  Bass - +6.0 dB, Ext. Out 3/4" in routed
    assert "3 of the tracks and groups above are routed to the interface outputs they use in Logic (--keep-outputs)" in routed


def test_mixer_file_replaces_only_the_tracks_it_names(tmp_path, capsys):
    logicx = _logicx(tmp_path)
    overrides = tmp_path / "mixer.json"
    overrides.write_text(json.dumps({"Kick": {"volume_db": -3.0, "pan": 0.5}, "Tambourine": {"is_muted": True}}))

    assert main([str(logicx), "--output", str(tmp_path / "out"), "--report-only", "--mixer", str(overrides)]) == 0
    out = capsys.readouterr().out

    assert '  Kick - -3.0 dB, pan 50%R, in group "Drums", from --mixer' in out
    assert '  Snare - +0.0 dB, MUTED, in group "Drums"' in out
    assert "--mixer names 1 track(s) this project does not have, so nothing was done with them: Tambourine" in out
    assert "  Tambourine - " not in out


def test_mixer_file_changes_only_the_fields_it_gives(tmp_path, capsys):
    logicx = _logicx(tmp_path)
    overrides = tmp_path / "mixer.json"
    overrides.write_text(json.dumps({"Snare": {"volume_db": -3.0}, "Overhead": {"is_muted": True}}))

    assert main([str(logicx), "--output", str(tmp_path / "out"), "--report-only", "--mixer", str(overrides)]) == 0
    out = capsys.readouterr().out

    # Snare stays muted and Overhead keeps its level and pan, as in Logic.
    assert '  Snare - -3.0 dB, MUTED, in group "Drums", from --mixer' in out
    assert '  Overhead - -6.0 dB, pan 55%L, MUTED, in group "Overheads", from --mixer' in out


def test_mixer_file_naming_a_group_says_groups_are_not_set(tmp_path, capsys):
    logicx = _logicx(tmp_path)
    overrides = tmp_path / "mixer.json"
    overrides.write_text(json.dumps({"Drums": {"volume_db": -3.0}}))

    assert main([str(logicx), "--output", str(tmp_path / "out"), "--report-only", "--mixer", str(overrides)]) == 0
    out = capsys.readouterr().out

    assert "nothing was done with them (a --mixer file sets tracks, not groups): Drums" in out
    assert "    Drums - -4.0 dB" in out


def test_no_mixer_flag_gives_the_flat_set(tmp_path, capsys):
    logicx = _logicx(tmp_path)

    assert main([
        str(logicx), "--output", str(tmp_path / "out"), "--no-copy", "--no-mixer", "--keep-outputs",
        "--template", str(_BUNDLED_TEMPLATE),
    ]) == 0
    out = capsys.readouterr().out
    _, tracks = _live_tracks(next((tmp_path / "out").rglob("*.als")))

    assert "MIXER STATE: defaults (0 dB, center pan)" in out
    assert [track.tag for track in tracks].count("GroupTrack") == 0
    for track in tracks:
        assert track.find("TrackGroupId").get("Value") == "-1"
        assert _output_target(track) == "AudioOut/Main"
        assert float(_mixer_value(track, "Volume/Manual")) == 1.0
        assert _mixer_value(track, "Speaker/Manual") == "true"


def test_silent_fader_reads_as_minus_infinity_in_the_report(tmp_path):
    project = parse_logic_project(_logicx(tmp_path, strips=_strips(**{"Audio 1": {"fader": 0.0}})))
    assert '  Kick - -inf dB, in group "Drums"' in generate_report(project, [])


def test_mixer_template_starts_from_logics_values(tmp_path, capsys):
    logicx = _logicx(tmp_path, strips=_strips(**{"Audio 1": {"fader": 0.0}}))

    assert main([str(logicx), "--output", str(tmp_path / "out"), "--report-only", "--generate-mixer-template"]) == 0
    capsys.readouterr()
    template = json.loads((tmp_path / "out" / "mixer_overrides.json").read_text(encoding="utf-8"))

    assert list(template) == ["Kick", "Snare", "Overhead", "Bass", "Click"]
    assert template["Kick"] == {"volume_db": -70.0, "pan": 0.0, "is_muted": False, "is_soloed": False}
    assert template["Snare"]["is_muted"] is True
    assert template["Click"] == {"volume_db": -2.0, "pan": -1.0, "is_muted": False, "is_soloed": False}
    # The file it writes is one --mixer accepts back.
    assert main([
        str(logicx), "--output", str(tmp_path / "out"), "--report-only",
        "--mixer", str(tmp_path / "out" / "mixer_overrides.json"),
    ]) == 0


def test_keep_outputs_flag_routes_the_set(tmp_path, capsys):
    logicx = _logicx(tmp_path)

    assert main([
        str(logicx), "--output", str(tmp_path / "out"), "--no-copy", "--keep-outputs",
        "--template", str(_BUNDLED_TEMPLATE),
    ]) == 0
    out = capsys.readouterr().out
    als_path = next((tmp_path / "out").rglob("*.als"))
    _, tracks = _live_tracks(als_path)

    assert {_name(track): _output_target(track) for track in tracks if _name(track) in ("Bass", "Click")} == {
        "Bass": "AudioOut/External/S1", "Click": "AudioOut/External/S6",
    }
    assert "  Bass - +6.0 dB, Ext. Out 3/4" in out
