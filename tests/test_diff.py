from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from firefox_vrt.diff import (
    DEFAULT_TOLERANCE,
    STATUS_DIFFERS,
    STATUS_IDENTICAL,
    STATUS_SIZE_MISMATCH,
    diff_images,
)


def _solid(color, size=(80, 60)):
    return Image.fromarray(np.full((size[1], size[0], 3), color, dtype=np.uint8))


def _checker(a, b, size=(80, 60), block=10):
    w, h = size
    arr = np.zeros((h, w, 3), dtype=np.uint8)
    for y in range(h):
        for x in range(w):
            arr[y, x] = a if ((x // block) + (y // block)) % 2 == 0 else b
    return Image.fromarray(arr)


def test_diff_identical(tmp_path: Path) -> None:
    base = tmp_path / "base.png"
    candidate = tmp_path / "candidate.png"
    diff = tmp_path / "diff.png"
    _solid((100, 100, 100)).save(base)
    _solid((100, 100, 100)).save(candidate)
    result = diff_images(base, candidate, diff)
    assert result.status == STATUS_IDENTICAL
    assert result.diff_percent == pytest.approx(0.0)
    assert diff.exists()


def test_diff_real_difference(tmp_path: Path) -> None:
    base = tmp_path / "base.png"
    candidate = tmp_path / "candidate.png"
    diff = tmp_path / "diff.png"
    _solid((100, 100, 100)).save(base)
    _checker((100, 100, 100), (255, 0, 0)).save(candidate)
    result = diff_images(base, candidate, diff, threshold=0.001)
    assert result.status == STATUS_DIFFERS
    assert result.diff_percent > 0.4
    assert diff.exists()


def test_diff_subpixel_noise_below_tolerance(tmp_path: Path) -> None:
    """Sub-tolerance jitter should not register as a diff."""
    base = tmp_path / "base.png"
    candidate = tmp_path / "candidate.png"
    diff = tmp_path / "diff.png"
    arr = np.full((60, 80, 3), 128, dtype=np.uint8)
    rng = np.random.default_rng(seed=42)
    jitter = rng.integers(-5, 6, size=arr.shape, dtype=np.int16)
    noisy = np.clip(arr.astype(np.int16) + jitter, 0, 255).astype(np.uint8)
    Image.fromarray(arr).save(base)
    Image.fromarray(noisy).save(candidate)
    result = diff_images(base, candidate, diff, tolerance=DEFAULT_TOLERANCE)
    assert result.status == STATUS_IDENTICAL


def test_diff_size_mismatch(tmp_path: Path) -> None:
    base = tmp_path / "base.png"
    candidate = tmp_path / "candidate.png"
    diff = tmp_path / "diff.png"
    _solid((100, 100, 100), size=(80, 60)).save(base)
    _solid((100, 100, 100), size=(100, 60)).save(candidate)
    result = diff_images(base, candidate, diff)
    assert result.status == STATUS_SIZE_MISMATCH
    assert result.diff_percent is None
    assert not diff.exists()


def test_diff_no_overlay_when_path_omitted(tmp_path: Path) -> None:
    base = tmp_path / "base.png"
    candidate = tmp_path / "candidate.png"
    _solid((50, 50, 50)).save(base)
    _solid((200, 50, 50)).save(candidate)
    result = diff_images(base, candidate, diff_out_path=None)
    assert result.status == STATUS_DIFFERS
    # No overlay file produced (no path given).
    assert list(tmp_path.glob("diff*.png")) == []


def test_diff_threshold_boundary(tmp_path: Path) -> None:
    """A diff just under threshold stays identical; just over flips to differs."""
    base = tmp_path / "base.png"
    candidate = tmp_path / "candidate.png"
    base_arr = np.full((100, 100, 3), 100, dtype=np.uint8)
    # Change exactly 200 pixels (2%) by a delta well above tolerance.
    candidate_arr = base_arr.copy()
    candidate_arr[:20, :10] = (255, 0, 0)  # 200 pixels changed
    Image.fromarray(base_arr).save(base)
    Image.fromarray(candidate_arr).save(candidate)

    # Threshold 0.01 (1%) — 2% > 1%, should differ.
    result_diff = diff_images(base, candidate, tmp_path / "d1.png", threshold=0.01)
    assert result_diff.status == STATUS_DIFFERS

    # Threshold 0.05 (5%) — 2% < 5%, should be identical.
    result_same = diff_images(base, candidate, tmp_path / "d2.png", threshold=0.05)
    assert result_same.status == STATUS_IDENTICAL
