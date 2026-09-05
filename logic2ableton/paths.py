"""Portable output names and contained, non-destructive export destinations."""

from __future__ import annotations

import re
from pathlib import Path


def safe_name(value: str, fallback: str = "project", *, max_bytes: int = 120) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', "_", value)
    name = re.sub(r"\s+", " ", name).strip(" .")
    name = name.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore").rstrip(" .")
    if not name:
        name = fallback
    if re.fullmatch(r"CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9]", name.split(".")[0], re.I):
        name = "_" + name
    return name


def output_path(directory: Path, filename: str) -> Path:
    """Reject existing symlinks that would redirect a write outside its root."""
    root = Path(directory).resolve()
    candidate = root / safe_name(filename, max_bytes=220)
    if not candidate.resolve().is_relative_to(root):
        raise ValueError(f"Output path escapes the selected directory: {candidate.name}")
    return candidate


def create_output_directory(directory: Path, name: str) -> Path:
    """Allocate a fresh package; reruns retain previous exports separately."""
    root = Path(directory).resolve()
    root.mkdir(parents=True, exist_ok=True)
    name = safe_name(name)
    number = 1
    while True:
        candidate = root / (name if number == 1 else f"{name} ({number})")
        try:
            candidate.mkdir()
            return candidate
        except FileExistsError:
            number += 1
