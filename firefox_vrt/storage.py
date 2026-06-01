"""On-disk cleanup helpers for deleting captures and comparisons.

Deleting a row should also remove its files (screenshots / diff overlays).
These live under DATA_DIR; we refuse to remove anything that isn't strictly
inside it, so a bad/empty path can never escalate into deleting the data root
or something outside it.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

log = logging.getLogger(__name__)


def remove_dir_within(data_dir: Path, target: Path) -> None:
    """Recursively delete ``target`` — but only if it resolves to a path
    strictly inside ``data_dir``. No-op otherwise (or if it doesn't exist)."""
    try:
        data_root = data_dir.resolve()
        resolved = target.resolve()
    except OSError as exc:  # pragma: no cover - defensive
        log.warning("remove_dir_within: cannot resolve %s: %r", target, exc)
        return
    if resolved == data_root or data_root not in resolved.parents:
        log.warning("remove_dir_within: refusing to delete %s (outside %s)", resolved, data_root)
        return
    if resolved.is_dir():
        shutil.rmtree(resolved, ignore_errors=True)


def capture_dir(data_dir: Path, capture_id: int) -> Path:
    return data_dir / "captures" / str(capture_id)


def comparison_dir(data_dir: Path, comparison_id: int) -> Path:
    return data_dir / "comparisons" / str(comparison_id)
