from __future__ import annotations

import json
from pathlib import Path

import pytest

from firefox_vrt import known_noise as kn


@pytest.fixture
def fixture_db(tmp_path: Path) -> Path:
    data = {
        "entries": [
            {
                "platform": "linux1804-64",
                "name_regex": "(?i).*controlcenter.*",
                "max_diff": 5000,
                "reason": "shadow jitter",
            },
            {
                "platform": "windows7-32",
                "name_regex": ".*",
                "min_diff": 0,
                "max_diff": 2000,
                "reason": "font hinting",
            },
            {
                # Malformed entry — missing max_diff. Should be skipped.
                "platform": "junk",
                "name_regex": ".*",
            },
        ]
    }
    p = tmp_path / "kn.json"
    p.write_text(json.dumps(data))
    # Bust the lru_cache so we get a fresh load.
    kn.load.cache_clear()
    return p


def test_load_skips_malformed(fixture_db: Path) -> None:
    entries = kn.load(fixture_db)
    assert len(entries) == 2  # the malformed one dropped
    platforms = {e.platform for e in entries}
    assert platforms == {"linux1804-64", "windows7-32"}


def test_match_by_platform_and_name_and_pixels(fixture_db: Path) -> None:
    entries = kn.load(fixture_db)
    hit = kn.match("linux1804-64", "primaryUI_ControlCenter_default.png", 1500, entries)
    assert hit is not None
    assert "shadow" in hit.reason


def test_no_match_for_other_platform(fixture_db: Path) -> None:
    entries = kn.load(fixture_db)
    hit = kn.match("macosx64", "primaryUI_ControlCenter_default.png", 1500, entries)
    assert hit is None


def test_no_match_when_pixel_count_above_range(fixture_db: Path) -> None:
    entries = kn.load(fixture_db)
    hit = kn.match("linux1804-64", "primaryUI_ControlCenter_default.png", 999_999, entries)
    assert hit is None


def test_no_match_when_name_doesnt_match(fixture_db: Path) -> None:
    entries = kn.load(fixture_db)
    hit = kn.match("linux1804-64", "primaryUI_Tabs_pinned.png", 100, entries)
    assert hit is None


def test_default_database_loads():
    # Shipped JSON should be loadable and non-empty.
    kn.load.cache_clear()
    entries = kn.load()
    assert len(entries) > 0


def test_missing_file_returns_empty(tmp_path: Path) -> None:
    kn.load.cache_clear()
    entries = kn.load(tmp_path / "nope.json")
    assert entries == ()
