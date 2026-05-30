"""Pair CI-produced screenshot filenames across two captures.

mozscreenshots artifacts arrive with filenames like:
  primaryUI_01_tabsOutsideTitlebar_twoPinnedWithOverflow_allToolbars.png

Some artifacts (especially from try pushes) carry a platform prefix:
  test-linux1804-64_primaryUI_01_tabsOutsideTitlebar_...png

To pair a screenshot from capture A with the same screenshot from capture B,
we strip both the optional platform prefix and any leading numeric index,
yielding a "combination name" that's stable across captures.

Ported from `mnoorenberghe/mozscreenshots/web/compare.js:calculateCombinationDisplayName`.
"""

from __future__ import annotations

import re
from typing import Optional


# Platform prefixes the CI job may prepend to filenames. Order matters only
# in that more-specific entries should come first (none currently overlap).
_PLATFORM_PREFIX_RE = re.compile(
    r"^(?:test-)?(?:"
    r"linux1804-64|"
    r"linux2204-64|"
    r"linux2404-64|"
    r"linux64|"
    r"windows7-32|"
    r"windows10-64|"
    r"windows11-64|"
    r"macosx64|"
    r"osx-cross"
    r")[-a-z0-9]*_"
)

# Leading numeric index, e.g. "01_" or "123_".
_LEADING_INDEX_RE = re.compile(r"^\d+_")


def combination_name(filename: str) -> str:
    """Strip platform prefix + leading numeric index from a screenshot
    filename. The returned value is the pairing key shared across captures.

    Examples:
        primaryUI_01_tabs.png             -> primaryUI_01_tabs.png
        test-linux1804-64_primaryUI_01_tabs.png -> primaryUI_01_tabs.png
        01_Tabs_3-pinned-tabs.png         -> Tabs_3-pinned-tabs.png

    The "primaryUI_NN_..." segment isn't an index in the same sense — it's
    part of the configuration set name, so it's preserved. Only a *leading*
    bare-number_ prefix at the very start of the filename is stripped.
    """
    name = filename
    # 1. Strip platform prefix, if present.
    name = _PLATFORM_PREFIX_RE.sub("", name)
    # 2. Strip a leading bare numeric index ONLY when it's at the very start
    #    (not after "primaryUI_" or similar). After the platform strip in
    #    step 1, this catches local-run filenames like "01_Tabs_pinned.png".
    name = _LEADING_INDEX_RE.sub("", name)
    return name


# Canonical platforms we recognize. Captures from different platforms never
# pair (different rendering pipelines, different OS chrome).
KNOWN_PLATFORMS = (
    "linux1804-64",
    "linux2204-64",
    "linux2404-64",
    "linux64",
    "windows7-32",
    "windows10-64",
    "windows11-64",
    "macosx64",
)


def platform_from_job_name(job_name: str) -> Optional[str]:
    """Extract a canonical platform identifier from a Treeherder job_type_name
    like `test-linux1804-64/opt-browser-screenshots-e10s`."""
    if not job_name:
        return None
    # job_type_name is `test-<platform-tier>/<...>-browser-screenshots-...`.
    head = job_name.split("/", 1)[0]
    if head.startswith("test-"):
        head = head[5:]
    # head is now something like "linux1804-64" or "linux1804-64-shippable-qr".
    # Reduce to the canonical platform key by matching a known prefix.
    for p in KNOWN_PLATFORMS:
        if head.startswith(p):
            return p
    return head or None


__all__ = [
    "KNOWN_PLATFORMS",
    "combination_name",
    "platform_from_job_name",
]
