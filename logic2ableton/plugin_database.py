from dataclasses import dataclass


@dataclass
class PluginInfo:
    name: str
    category: str       # eq, compressor, limiter, de_esser, reverb, delay, saturation, modulation, etc.
    character: str


AU_PLUGINS: dict[tuple[str, str], PluginInfo] = {
    # Waves (ksWV)
    ("ksWV", "76CM"): PluginInfo("Waves CLA-76", "compressor", "FET compressor"),
    ("ksWV", "76CS"): PluginInfo("Waves CLA-76", "compressor", "FET compressor stereo"),
    ("ksWV", "TG5M"): PluginInfo("Waves Abbey Road TG Mastering Chain", "channel_strip", "vintage console"),
    ("ksWV", "TG5S"): PluginInfo("Waves Abbey Road TG Mastering Chain", "channel_strip", "vintage console stereo"),
    ("ksWV", "DSAM"): PluginInfo("Waves De-Esser", "de_esser", "sibilance de-esser"),
    ("ksWV", "DSAS"): PluginInfo("Waves De-Esser", "de_esser", "sibilance de-esser stereo"),
    ("ksWV", "LA2M"): PluginInfo("Waves CLA-2A", "compressor", "opto compressor"),
    ("ksWV", "LA2S"): PluginInfo("Waves CLA-2A", "compressor", "opto compressor stereo"),
    ("ksWV", "LA3M"): PluginInfo("Waves CLA-3A", "compressor", "opto compressor"),
    ("ksWV", "LA3S"): PluginInfo("Waves CLA-3A", "compressor", "opto compressor stereo"),
    ("ksWV", "L1CM"): PluginInfo("Waves L1 Limiter", "limiter", "brickwall peak limiter"),
    ("ksWV", "L1CS"): PluginInfo("Waves L1 Limiter", "limiter", "brickwall peak limiter stereo"),
    ("ksWV", "BSLM"): PluginInfo("Waves Bass Rider", "utility", "bass level rider"),
    ("ksWV", "BSLS"): PluginInfo("Waves Bass Rider", "utility", "bass level rider stereo"),
    ("ksWV", "T37M"): PluginInfo("Waves J37 Tape", "saturation", "tape saturation"),
    ("ksWV", "T37S"): PluginInfo("Waves J37 Tape", "saturation", "tape saturation stereo"),
    ("ksWV", "NIDM"): PluginInfo("Waves Renaissance De-Esser", "de_esser", "renaissance de-esser"),
    ("ksWV", "NIDS"): PluginInfo("Waves Renaissance De-Esser", "de_esser", "renaissance de-esser stereo"),
    ("ksWV", "APCM"): PluginInfo("Waves API 2500", "compressor", "bus compressor"),
    ("ksWV", "APCS"): PluginInfo("Waves API 2500", "compressor", "bus compressor stereo"),
    ("ksWV", "TAPM"): PluginInfo("Waves J37 Tape", "saturation", "tape emulation"),
    ("ksWV", "TAPS"): PluginInfo("Waves J37 Tape", "saturation", "tape emulation stereo"),
    ("ksWV", "PLTM"): PluginInfo("Waves PuigTec EQ", "eq", "tube equalizer"),
    ("ksWV", "PLTS"): PluginInfo("Waves PuigTec EQ", "eq", "tube equalizer stereo"),
    ("ksWV", "RDRM"): PluginInfo("Waves Renaissance De-Esser", "de_esser", "de-esser"),
    ("ksWV", "RDRS"): PluginInfo("Waves Renaissance De-Esser", "de_esser", "de-esser stereo"),
    # Apple built-in (appl)
    ("appl", "bceq"): PluginInfo("Channel EQ", "eq", "parametric EQ"),
    ("appl", "chor"): PluginInfo("Chorus", "modulation", "chorus effect"),
    ("appl", "pdlb"): PluginInfo("Pedalboard", "multi_fx", "guitar effect pedalboard"),
    ("appl", "lmtr"): PluginInfo("Limiter", "limiter", "brickwall limiter"),
    ("appl", "mcmp"): PluginInfo("Multipressor", "compressor", "multiband compressor"),
    ("appl", "cmpr"): PluginInfo("Compressor", "compressor", "general compressor"),
    ("appl", "spdz"): PluginInfo("Space Designer", "reverb", "convolution reverb"),
    ("appl", "chrm"): PluginInfo("ChromaVerb", "reverb", "algorithmic reverb"),
    ("appl", "tdly"): PluginInfo("Tape Delay", "delay", "tape delay"),
    ("appl", "ngat"): PluginInfo("Noise Gate", "gate", "noise gate"),
    ("appl", "dees"): PluginInfo("DeEsser 2", "de_esser", "de-esser"),
}


def lookup_au_plugin(manufacturer: str, subtype: str) -> PluginInfo | None:
    return AU_PLUGINS.get((manufacturer, subtype))
