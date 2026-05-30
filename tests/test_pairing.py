from __future__ import annotations

from firefox_vrt.pairing import (
    KNOWN_PLATFORMS,
    combination_name,
    platform_from_job_name,
)


def test_combination_name_no_prefix_unchanged():
    assert combination_name("primaryUI_01_tabs.png") == "primaryUI_01_tabs.png"


def test_combination_name_strips_linux_prefix():
    assert (
        combination_name("test-linux1804-64_primaryUI_01_tabs.png")
        == "primaryUI_01_tabs.png"
    )


def test_combination_name_strips_windows_prefix():
    assert (
        combination_name("test-windows10-64_primaryUI_01_tabs.png")
        == "primaryUI_01_tabs.png"
    )


def test_combination_name_strips_qr_suffix():
    assert (
        combination_name("test-linux1804-64-shippable-qr_primaryUI_01.png")
        == "primaryUI_01.png"
    )


def test_combination_name_strips_leading_index():
    assert combination_name("01_Tabs_pinned.png") == "Tabs_pinned.png"
    assert combination_name("123_WindowSize_1024x768.png") == "WindowSize_1024x768.png"


def test_combination_name_preserves_internal_digits():
    # The "01" in "primaryUI_01_tabs" is NOT a leading index; preserve.
    name = "test-linux1804-64_primaryUI_01_tabs.png"
    assert combination_name(name) == "primaryUI_01_tabs.png"


def test_pairs_match_across_platforms_only_after_stripping():
    a = combination_name("test-linux1804-64_primaryUI_01_tabs.png")
    b = combination_name("test-windows10-64_primaryUI_01_tabs.png")
    # The combination names match (we'd then reject pairing at the platform
    # level, but the names align).
    assert a == b


def test_platform_from_job_name():
    assert (
        platform_from_job_name("test-linux1804-64/opt-browser-screenshots-e10s")
        == "linux1804-64"
    )
    assert (
        platform_from_job_name("test-windows10-64/opt-browser-screenshots-e10s")
        == "windows10-64"
    )
    assert (
        platform_from_job_name(
            "test-linux1804-64-shippable-qr/opt-browser-screenshots-fis-e10s"
        )
        == "linux1804-64"
    )


def test_platform_from_job_name_handles_none():
    assert platform_from_job_name("") is None
    assert platform_from_job_name(None) is None


def test_known_platforms_constant_nonempty():
    assert "linux1804-64" in KNOWN_PLATFORMS
    assert "windows10-64" in KNOWN_PLATFORMS
