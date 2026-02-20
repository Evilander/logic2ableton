import re
from dataclasses import dataclass, field
from pathlib import Path

from logic2ableton.models import PluginInstance
from logic2ableton.plugin_database import lookup_au_plugin
from logic2ableton.vst3_scanner import scan_vst3_plugins


@dataclass
class PluginMatch:
    logic_plugin_name: str
    preset_name: str
    category: str
    character: str
    suggested_vst3s: list[str] = field(default_factory=list)


def _tokenize(name: str) -> set[str]:
    """Split a plugin name into lowercase tokens for similarity matching."""
    return set(re.split(r"[\s\-_/()]+", name.lower())) - {"", "v2", "v3", "v4", "v5", "stereo", "mono"}


def _name_similarity(a: str, b: str) -> float:
    """Score 0.0-1.0 based on shared tokens between two plugin names."""
    tokens_a = _tokenize(a)
    tokens_b = _tokenize(b)
    if not tokens_a or not tokens_b:
        return 0.0
    shared = tokens_a & tokens_b
    return len(shared) / max(len(tokens_a), len(tokens_b))


def match_plugins(plugins: list[PluginInstance], vst3_path: Path) -> list[PluginMatch]:
    vst3_plugins = scan_vst3_plugins(vst3_path)
    vst3_by_category: dict[str, list[str]] = {}
    for vst3 in vst3_plugins:
        vst3_by_category.setdefault(vst3.category, []).append(vst3.name)

    all_vst3_names = [v.name for v in vst3_plugins]

    matches = []
    for plugin in plugins:
        info = lookup_au_plugin(plugin.au_manufacturer, plugin.au_subtype)
        if info:
            logic_name = info.name
            category = info.category
            character = info.character
        else:
            logic_name = f"Unknown ({plugin.au_manufacturer}/{plugin.au_subtype})"
            category = "unknown"
            character = ""

        candidates = vst3_by_category.get(category, [])

        # If we have too many category matches or none, use name similarity to rank
        if len(candidates) > 5 and info:
            scored = [(c, _name_similarity(info.name, c)) for c in candidates]
            scored.sort(key=lambda x: x[1], reverse=True)
            suggested = [name for name, _ in scored[:5]]
        elif not candidates and info:
            # No category matches — try name similarity across all VST3s
            scored = [(c, _name_similarity(info.name, c)) for c in all_vst3_names]
            scored = [(name, score) for name, score in scored if score > 0.15]
            scored.sort(key=lambda x: x[1], reverse=True)
            suggested = [name for name, _ in scored[:5]]
        else:
            suggested = candidates[:5]

        matches.append(PluginMatch(
            logic_plugin_name=logic_name,
            preset_name=plugin.name,
            category=category,
            character=character,
            suggested_vst3s=suggested,
        ))
    return matches
