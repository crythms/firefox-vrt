"""Unit tests for the comparison-page screenshot-set classification."""

from __future__ import annotations

from firefox_vrt.routes.comparisons import classify_sets, normalize_sets


def test_normalize_splits_and_strips() -> None:
    assert normalize_sets(["Toolbars, Tabs"]) == frozenset({"Toolbars", "Tabs"})


def test_normalize_unions_across_tasks() -> None:
    assert normalize_sets(["Toolbars,Tabs", "Tabs,DevTools"]) == frozenset(
        {"Toolbars", "Tabs", "DevTools"}
    )


def test_normalize_none_when_nothing_recorded() -> None:
    # All-None (or empty) inputs mean "unknown", distinct from an empty set.
    assert normalize_sets([None, None]) is None
    assert normalize_sets([]) is None
    assert normalize_sets(["", None]) is None


def test_classify_match() -> None:
    a = frozenset({"Toolbars", "Tabs"})
    status, base_only, cand_only = classify_sets(a, frozenset({"Tabs", "Toolbars"}))
    assert status == "match"
    assert base_only == [] and cand_only == []


def test_classify_mismatch_reports_differences() -> None:
    base = frozenset({"Toolbars", "Tabs", "DevTools"})
    cand = frozenset({"Toolbars", "Tabs"})
    status, base_only, cand_only = classify_sets(base, cand)
    assert status == "mismatch"
    assert base_only == ["DevTools"]
    assert cand_only == []


def test_classify_unknown_when_either_side_missing() -> None:
    assert classify_sets(None, frozenset({"Tabs"}))[0] == "unknown"
    assert classify_sets(frozenset({"Tabs"}), None)[0] == "unknown"
