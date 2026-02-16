from dataclasses import dataclass
from pathlib import Path


@dataclass
class VST3Plugin:
    name: str
    path: Path
    category: str


# Exact-match overrides for known plugins (checked first)
KNOWN_PLUGINS: dict[str, str] = {
    "Pianoteq 8": "synth",
    "Pianoteq 9": "synth",
    "Serum2": "synth",
    "ShaperBox 3": "multi_fx",
    "Turnado": "multi_fx",
    "Smooth Operator": "eq",
    "TAIP": "saturation",
    "Parallel Aggressor": "compressor",
    "Sun Bear Bus Compressor": "compressor",
    "Retro Sta-Level": "compressor",
    "Lion Master": "limiter",
    "Level-Or": "limiter",
    "Crystalline": "reverb",
    "Dirty Dog Reverb": "reverb",
    "Hippie Elephant Reverb": "reverb",
    "LadyBug Reverb": "reverb",
    "Rhino Reverb": "reverb",
    "Flamingo Verb": "reverb",
    "Yak Delay": "delay",
    "Fox Echo Chorus": "delay",
    "Comeback Kid": "delay",
    "TimeMachine": "delay",
    "Hawk Phaser": "modulation",
    "Pixel Cat": "modulation",
    "Transit 2": "modulation",
    "Dragon EQ": "eq",
    "Falcon Air EQ": "eq",
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


def scan_vst3_plugins(vst3_path: Path) -> list[VST3Plugin]:
    if not vst3_path.exists():
        return []
    plugins = []
    for entry in sorted(vst3_path.iterdir()):
        name = entry.stem
        plugins.append(VST3Plugin(name=name, path=entry, category=_categorize_plugin(name)))
    return plugins
