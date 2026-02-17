import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class AudioFileRef:
    filename: str           # e.g., "KICK IN#01.wav"
    track_name: str         # e.g., "KICK IN" (stripped of take suffix)
    take_number: int        # e.g., 1 (from #01)
    is_comp: bool           # True if this is a comp bounce
    comp_name: str          # e.g., "Comp A"
    file_path: Path         # Absolute path to the audio file in Media/
    start_position_samples: int = 0  # Region start position in samples


@dataclass
class TrackMixerState:
    """Per-track mixer state: volume, pan, mute, solo."""
    volume_db: float = 0.0
    pan: float = 0.0
    is_muted: bool = False
    is_soloed: bool = False

    @property
    def volume_linear(self) -> float:
        """Convert dB to Ableton's linear fader scale."""
        linear = 10 ** (self.volume_db / 20)
        return max(0.0003162277571, min(1.99526238, linear))


@dataclass
class PluginInstance:
    name: str               # Preset name
    au_type: str            # 4CC type (aufx, aumu, etc.)
    au_subtype: str         # 4CC subtype (76CM, TG5M, etc.)
    au_manufacturer: str    # 4CC manufacturer (ksWV, appl, etc.)
    is_waves: bool          # Has Waves_XPst data
    raw_plist: dict         # Full parsed plist for future use


@dataclass
class LogicProject:
    name: str
    tempo: float
    time_sig_numerator: int
    time_sig_denominator: int
    sample_rate: int
    audio_files: list[AudioFileRef]
    plugins: list[PluginInstance]
    track_names: list[str]  # Ordered list of unique track names
    alternative: int        # Which alternative was parsed
    mixer_state: dict[str, TrackMixerState] | None = None


def parse_audio_filename(filename: str) -> tuple[str, int, bool, str]:
    """Parse a Logic Pro audio filename into (track_name, take_number, is_comp, comp_name).

    Handles these patterns:
    - Take files: "KICK IN#01.wav" -> ("KICK IN", 1, False, "")
    - Comp files: "scratch vox 2_ Comp A.wav" -> ("scratch vox 2", 0, True, "Comp A")
    - BIP files: "BASS GUITAR_bip.wav" -> ("BASS GUITAR", 0, False, "")
    - Simple files: "scratch vox 1.wav" -> ("scratch vox 1", 0, False, "")
    """
    stem = filename.rsplit(".", 1)[0]

    # Check for comp pattern: "name_ Comp X" or "name/ Comp X"
    comp_match = re.match(r"^(.+?)[_/]\s*Comp\s+(.+)$", stem)
    if comp_match:
        return (comp_match.group(1).strip(), 0, True, f"Comp {comp_match.group(2).strip()}")

    # Check for take pattern: "name#NN"
    take_match = re.match(r"^(.+?)#(\d+)$", stem)
    if take_match:
        return (take_match.group(1), int(take_match.group(2)), False, "")

    # Check for _bip suffix (bounce-in-place)
    bip_match = re.match(r"^(.+?)_bip$", stem)
    if bip_match:
        return (bip_match.group(1), 0, False, "")

    return (stem, 0, False, "")


def samples_to_beats(samples: int, tempo: float, sample_rate: int) -> float:
    """Convert a sample position to a beat position.

    beats = samples * tempo / (sample_rate * 60)
    """
    return samples * tempo / (sample_rate * 60)
