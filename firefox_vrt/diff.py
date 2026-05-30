"""Pixel diff for two PNGs.

Matches the semantics of the old `compare_screenshots` CLI (ImageMagick's
`-fuzz 3% -metric AE`): a pixel is "changed" if max-channel absolute
delta exceeds an 8/255 tolerance (~3.1%). Below that we treat the
difference as subpixel rendering / AA noise.

Returns a tuple of (status, diff_percent, changed_pixel_count). The
caller is responsible for any storage / pairing concerns. The diff
overlay PNG (red over desaturated baseline) is written to a caller-
provided destination path.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image


# Result status constants.
STATUS_IDENTICAL = "identical"
STATUS_DIFFERS = "differs"
STATUS_SIZE_MISMATCH = "size-mismatch"


DEFAULT_TOLERANCE = 8  # per-channel, 0-255. ~3.1%, matches ImageMagick fuzz=3%.
DEFAULT_THRESHOLD = 0.001  # fraction of pixels that must differ to flag.


@dataclass
class DiffResult:
    status: str
    diff_percent: Optional[float]  # None for size-mismatch
    changed_pixels: Optional[int]
    total_pixels: Optional[int]


def diff_images(
    base_path: Path,
    candidate_path: Path,
    diff_out_path: Optional[Path] = None,
    threshold: float = DEFAULT_THRESHOLD,
    tolerance: int = DEFAULT_TOLERANCE,
) -> DiffResult:
    """Compute the diff between two PNGs.

    If `diff_out_path` is provided and the dimensions match, writes a
    red-on-grey overlay PNG to that location. If dimensions differ, no
    overlay is written and status is `size-mismatch`.
    """
    base_img = Image.open(base_path).convert("RGB")
    candidate_img = Image.open(candidate_path).convert("RGB")

    if base_img.size != candidate_img.size:
        return DiffResult(
            status=STATUS_SIZE_MISMATCH,
            diff_percent=None,
            changed_pixels=None,
            total_pixels=None,
        )

    base_arr = np.asarray(base_img, dtype=np.int16)
    candidate_arr = np.asarray(candidate_img, dtype=np.int16)

    channel_delta = np.abs(base_arr - candidate_arr)
    max_delta = channel_delta.max(axis=2)
    changed_mask = max_delta > tolerance
    changed = int(changed_mask.sum())
    total = int(changed_mask.size)
    diff_percent = changed / total if total else 0.0

    status = STATUS_DIFFERS if diff_percent > threshold else STATUS_IDENTICAL

    if diff_out_path is not None:
        diff_out_path.parent.mkdir(parents=True, exist_ok=True)
        _write_overlay(base_arr, changed_mask, diff_out_path)

    return DiffResult(
        status=status,
        diff_percent=diff_percent,
        changed_pixels=changed,
        total_pixels=total,
    )


def _write_overlay(base_arr: np.ndarray, changed_mask: np.ndarray, dest: Path) -> None:
    """Write a diff overlay: baseline pixels desaturated to grey at 40% alpha
    with changed pixels painted solid red. Easier to scan than a raw delta."""
    luminance = (
        0.299 * base_arr[..., 0]
        + 0.587 * base_arr[..., 1]
        + 0.114 * base_arr[..., 2]
    ).astype(np.uint8)
    h, w = luminance.shape
    out = np.zeros((h, w, 4), dtype=np.uint8)
    out[..., 0] = luminance
    out[..., 1] = luminance
    out[..., 2] = luminance
    out[..., 3] = 102  # 40% alpha background
    out[changed_mask] = (255, 0, 0, 255)
    Image.fromarray(out).save(dest, format="PNG")


__all__ = [
    "DEFAULT_THRESHOLD",
    "DEFAULT_TOLERANCE",
    "DiffResult",
    "STATUS_DIFFERS",
    "STATUS_IDENTICAL",
    "STATUS_SIZE_MISMATCH",
    "diff_images",
]
