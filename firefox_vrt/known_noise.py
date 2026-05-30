"""Load + match the known-noise database.

If a Result's (platform, combination, changed_pixels) matches any entry,
mark it `known-noise` instead of `differs`. UI hides those by
default to keep the signal high.

Entries live in `firefox_vrt/data/known_noise.json`. Shape:

    {
      "platform":   "linux1804-64",   // canonical platform key
      "name_regex": "(?i).*foo.*",    // matched against combination name
      "min_diff":   0,                // inclusive lower bound, default 0
      "max_diff":   5000,             // inclusive upper bound (required)
      "reason":     "free-form text"  // shown in the UI tooltip
    }
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Optional


@dataclass(frozen=True)
class Entry:
    platform: str
    name_regex: re.Pattern
    min_diff: int
    max_diff: int
    reason: str


DEFAULT_PATH = Path(__file__).parent / "data" / "known_noise.json"


@lru_cache(maxsize=1)
def load(path: Optional[Path] = None) -> tuple[Entry, ...]:
    p = Path(path) if path else DEFAULT_PATH
    if not p.is_file():
        return ()
    raw = json.loads(p.read_text(encoding="utf-8"))
    out: list[Entry] = []
    for item in raw.get("entries", []):
        try:
            out.append(
                Entry(
                    platform=item["platform"],
                    name_regex=re.compile(item["name_regex"]),
                    min_diff=int(item.get("min_diff", 0)),
                    max_diff=int(item["max_diff"]),
                    reason=item.get("reason", ""),
                )
            )
        except (KeyError, re.error, TypeError, ValueError):
            # Skip malformed entries silently; this is a maintained file but
            # we don't want a typo to crash the whole comparison pipeline.
            continue
    return tuple(out)


def match(
    platform: str,
    combination: str,
    changed_pixels: int,
    entries: Optional[Iterable[Entry]] = None,
) -> Optional[Entry]:
    """Return the first matching entry, or None."""
    pool = entries if entries is not None else load()
    for e in pool:
        if e.platform != platform:
            continue
        if not e.name_regex.search(combination):
            continue
        if not (e.min_diff <= changed_pixels <= e.max_diff):
            continue
        return e
    return None


__all__ = ["Entry", "load", "match"]
