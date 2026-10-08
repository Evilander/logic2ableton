"""Bounds for parsing session files and expanding compact loop descriptions."""

from __future__ import annotations

import math
from typing import BinaryIO

# Media is streamed separately; these limits apply only to session metadata.
# They stop a damaged or hostile file from exhausting memory, and sit well
# above real sessions: Logic keeps plug-in states in ProjectData, so a project
# full of sampler instances can be large even when its arrangement is small.
MAX_SESSION_BYTES = 1024 * 1024 * 1024
MAX_EXPANDED_ITEMS = 2_000_000
MAX_EXPANSION_WORK = 50_000_000


def check_session_size(size: int, label: str) -> None:
    if size > MAX_SESSION_BYTES:
        raise ValueError(f"{label} exceeds the {MAX_SESSION_BYTES / 2**20:g} MiB session-data limit")


def read_session_bytes(handle: BinaryIO, label: str) -> bytes:
    data = handle.read(MAX_SESSION_BYTES + 1)
    check_session_size(len(data), label)
    return data


class ExpansionBudget:
    """Count work and output across all clips in one parsing pass."""

    def __init__(self, label: str):
        self.label = label
        self.work = 0
        self.items = 0

    def _check(self, count: int | float, total: int, limit: int) -> int:
        if not isinstance(count, int) and not math.isfinite(count):
            raise ValueError(f"{self.label} has a non-finite loop expansion")
        if count < 0 or count > limit - total:
            raise ValueError(
                f"{self.label} exceeds the safe loop expansion limit; "
                "shorten looped regions or split the session before converting"
            )
        return total + int(count)

    def reserve_work(self, count: int | float) -> None:
        self.work = self._check(count, self.work, MAX_EXPANSION_WORK)

    def add_items(self, count: int = 1) -> None:
        self.items = self._check(count, self.items, MAX_EXPANDED_ITEMS)
