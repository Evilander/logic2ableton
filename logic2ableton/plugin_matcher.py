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


def match_plugins(plugins: list[PluginInstance], vst3_path: Path) -> list[PluginMatch]:
    vst3_plugins = scan_vst3_plugins(vst3_path)
    vst3_by_category: dict[str, list[str]] = {}
    for vst3 in vst3_plugins:
        vst3_by_category.setdefault(vst3.category, []).append(vst3.name)

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

        suggested = vst3_by_category.get(category, [])[:5]
        matches.append(PluginMatch(
            logic_plugin_name=logic_name,
            preset_name=plugin.name,
            category=category,
            character=character,
            suggested_vst3s=suggested,
        ))
    return matches
