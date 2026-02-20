import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class VST3Plugin:
    name: str
    path: Path
    category: str


# Exact-match overrides for known plugins (checked first)
KNOWN_PLUGINS: dict[str, str] = {
    # Synths / Instruments
    "Pianoteq 8": "synth",
    "Pianoteq 9": "synth",
    "Serum2": "synth",
    "Omnisphere": "synth",
    "Keyscape": "synth",
    "Pigments": "synth",
    "Massive X": "synth",
    "Diva": "synth",
    "Repro-1": "synth",
    "Repro-5": "synth",
    "Hive 2": "synth",
    "Zebra2": "synth",
    "Vital": "synth",
    "Phase Plant": "synth",
    "Spire": "synth",
    "Sylenth1": "synth",
    "ANA 2": "synth",
    # Samplers
    "Kontakt 7": "sampler",
    "Kontakt 6": "sampler",
    "Battery 4": "sampler",
    "Addictive Drums 2": "sampler",
    "Superior Drummer 3": "sampler",
    "LABS": "sampler",
    "BBCSO": "sampler",
    "Play": "sampler",
    # Multi-FX
    "ShaperBox 3": "multi_fx",
    "Turnado": "multi_fx",
    "Guitar Rig 6": "multi_fx",
    "Guitar Rig 7": "multi_fx",
    "BIAS FX 2": "multi_fx",
    "Helix Native": "multi_fx",
    "Archetype": "multi_fx",
    "AmpliTube 5": "multi_fx",
    "TH-U": "multi_fx",
    "Soundtoys Effect Rack": "multi_fx",
    # EQ
    "Smooth Operator": "eq",
    "Dragon EQ": "eq",
    "Falcon Air EQ": "eq",
    "SlickEQ": "eq",
    "TDR Nova": "eq",
    "Kirchhoff-EQ": "eq",
    # Compressors
    "TAIP": "saturation",
    "Parallel Aggressor": "compressor",
    "Sun Bear Bus Compressor": "compressor",
    "Retro Sta-Level": "compressor",
    "Supercharger GT": "compressor",
    "Supercharger": "compressor",
    "OTT": "compressor",
    # Limiters
    "Lion Master": "limiter",
    "Level-Or": "limiter",
    "Elephant": "limiter",
    "Invisible Limiter": "limiter",
    # Reverbs
    "Crystalline": "reverb",
    "Dirty Dog Reverb": "reverb",
    "Hippie Elephant Reverb": "reverb",
    "LadyBug Reverb": "reverb",
    "Rhino Reverb": "reverb",
    "Flamingo Verb": "reverb",
    "ValhallaVintageVerb": "reverb",
    "ValhallaRoom": "reverb",
    "ValhallaShimmer": "reverb",
    "ValhallaSupermassive": "reverb",
    "ValhallaPlate": "reverb",
    "ValhallaDelay": "delay",
    "Raum": "reverb",
    "Blackhole": "reverb",
    "Little Plate": "reverb",
    "RC-20 Retro Color": "saturation",
    # Delays
    "Yak Delay": "delay",
    "Fox Echo Chorus": "delay",
    "Comeback Kid": "delay",
    "TimeMachine": "delay",
    "EchoBoy": "delay",
    "PrimalTap": "delay",
    "Replika XT": "delay",
    "Replika": "delay",
    "H-Delay": "delay",
    "Echorec": "delay",
    "UltraTap": "delay",
    # Modulation
    "Hawk Phaser": "modulation",
    "Pixel Cat": "modulation",
    "Transit 2": "modulation",
    "MicroShift": "modulation",
    "PanMan": "modulation",
    "Tremolator": "modulation",
    "FilterFreak": "modulation",
    "PhaseMistress": "modulation",
    "Phasis": "modulation",
    "Choral": "modulation",
    # Saturation
    "Decapitator": "saturation",
    "Radiator": "saturation",
    "Saturn 2": "saturation",
    "Thermal": "saturation",
    "Trash 2": "saturation",
    "Saturation Knob": "saturation",
    "Devil-Loc": "compressor",
    "Devil-Loc Deluxe": "compressor",
    # Pitch
    "Auto-Tune Pro": "pitch_correction",
    "Auto-Tune Access": "pitch_correction",
    "Melodyne": "pitch_correction",
    "Little AlterBoy": "pitch_correction",
    # Channel Strips
    "Console 1": "channel_strip",
    "Virtual Mix Rack": "channel_strip",
    "bx_console SSL 9000 J": "channel_strip",
    "bx_console SSL 4000 E": "channel_strip",
    "bx_console SSL 4000 G": "channel_strip",
    "SSL Native Channel Strip 2": "channel_strip",
    "SSL Native Bus Compressor 2": "compressor",
    # Mastering
    "Ozone": "mastering",
    "FG-X": "mastering",
    "Weiss MM-1": "mastering",
    # Noise reduction
    "RX": "noise_reduction",
    # Utility
    "Soothe2": "utility",
    "SPAN": "utility",
    "YouLean Loudness Meter": "utility",
    "VocAlign": "utility",
    "Portal": "modulation",
    "Movement": "modulation",
}

# Noise reduction plugins (must be checked before "Comp" pattern)
NOISE_REDUCTION_PREFIXES = ["CrumplePop"]

CATEGORY_PATTERNS: list[tuple[str, str]] = [
    # Specific first
    ("Pro-Q", "eq"), ("Pro-C", "compressor"), ("Pro-L", "limiter"),
    ("Pro-DS", "de_esser"), ("Pro-MB", "compressor"), ("Pro-R", "reverb"),
    # EQ (careful: "EQ" but not "Pianoteq")
    (" EQ", "eq"), ("-EQ", "eq"), ("Equaliz", "eq"),
    # Compressors (careful: not CrumplePop)
    ("Compressor", "compressor"), ("Comp FET", "compressor"),
    ("Bus Comp", "compressor"), ("Sta-Level", "compressor"),
    # Limiters
    ("Limiter", "limiter"), ("Maximizer", "limiter"),
    # De-essers
    ("De-Ess", "de_esser"), ("DeEss", "de_esser"), ("Sibilance", "de_esser"),
    # Reverb
    ("Reverb", "reverb"), ("Verb", "reverb"),
    ("ValhallaDSP", "reverb"), ("Valhalla DSP", "reverb"),
    # Delay
    ("Delay", "delay"), ("Echo", "delay"),
    # Saturation / Distortion
    ("Drive", "saturation"), ("Tape", "saturation"), ("Fuzz", "saturation"),
    ("Distort", "saturation"), ("Overdrive", "saturation"),
    # Modulation
    ("Chorus", "modulation"), ("Flanger", "modulation"), ("Phaser", "modulation"),
    # Channel strips
    ("Channel", "channel_strip"), ("Strip", "channel_strip"),
    # Synths / Instruments
    ("Synth", "synth"), ("Piano", "synth"), ("Serum", "synth"),
    ("Organ", "synth"), ("Korg", "synth"),
    # Utility
    ("Meter", "utility"), ("Analyzer", "utility"), ("Gain", "utility"),
    ("VocAlign", "utility"),
    # Noise reduction
    ("Denoise", "noise_reduction"), ("AudioDenoise", "noise_reduction"),
    ("Remover", "noise_reduction"), ("Noise", "noise_reduction"),
]


def _categorize_plugin(name: str) -> str:
    # Check exact overrides first
    if name in KNOWN_PLUGINS:
        return KNOWN_PLUGINS[name]

    # Check noise reduction prefixes before general patterns
    for prefix in NOISE_REDUCTION_PREFIXES:
        if name.startswith(prefix):
            return "noise_reduction"

    for pattern, category in CATEGORY_PATTERNS:
        if pattern.lower() in name.lower():
            return category
    return "unknown"


def default_vst3_path() -> Path:
    """Return the platform-appropriate default VST3 directory."""
    if sys.platform == "darwin":
        return Path("/Library/Audio/Plug-Ins/VST3")
    return Path("C:/Program Files/Common Files/VST3")


def _vst3_search_paths(vst3_path: Path) -> list[Path]:
    """Return all VST3 directories to scan. On macOS, includes both system and user dirs."""
    paths = [vst3_path]
    if sys.platform == "darwin":
        user_vst3 = Path.home() / "Library" / "Audio" / "Plug-Ins" / "VST3"
        if user_vst3 != vst3_path and user_vst3.exists():
            paths.append(user_vst3)
    return paths


def scan_vst3_plugins(vst3_path: Path) -> list[VST3Plugin]:
    plugins = []
    for search_path in _vst3_search_paths(vst3_path):
        if not search_path.exists():
            continue
        for entry in sorted(search_path.iterdir()):
            name = entry.stem
            plugins.append(VST3Plugin(name=name, path=entry, category=_categorize_plugin(name)))
    return plugins
