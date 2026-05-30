"""Comparator: orchestrates a Comparison's diffing pipeline.

Given two ready Captures and a Comparison row pointing at both, this:
  1. Walks each Capture's per-platform directories on disk.
  2. Pairs PNGs by (platform, combination_name).
  3. Calls diff.diff_images for each pair, writing overlay PNGs.
  4. Applies known-noise rules to demote `differs` to `known-noise`.
  5. Persists one Result per pair (including orphans on each side).
  6. Marks the Comparison ready / failed.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional

from sqlalchemy import select

from . import diff, known_noise as kn
from .config import Settings
from .db import session_factory
from .models import (
    COMPARISON_DIFFING,
    COMPARISON_FAILED,
    COMPARISON_READY,
    Capture,
    Comparison,
    RESULT_DIFFERS,
    RESULT_IDENTICAL,
    RESULT_KNOWN_NOISE,
    RESULT_ORPHAN,
    RESULT_SIZE_MISMATCH,
    Result,
)
from .pairing import combination_name


log = logging.getLogger(__name__)


async def run_comparison(comparison_id: int, settings: Settings) -> None:
    sf = session_factory()
    async with sf() as session:
        comp = await session.get(Comparison, comparison_id)
        if comp is None:
            log.error("run_comparison: comparison %s not found", comparison_id)
            return
        base = await session.get(Capture, comp.base_capture_id)
        candidate = await session.get(Capture, comp.candidate_capture_id)
        if base is None or candidate is None:
            comp.status = COMPARISON_FAILED
            comp.failure_reason = "Base or candidate capture not found."
            await session.commit()
            return
        comp.status = COMPARISON_DIFFING
        await session.commit()
        threshold = comp.threshold
        base_dir = Path(base.dir_path)
        candidate_dir = Path(candidate.dir_path)

    diff_dir = settings.data_dir / "comparisons" / str(comparison_id) / "diff"
    diff_dir.mkdir(parents=True, exist_ok=True)

    try:
        # CPU-bound diffing; offload to a thread so the event loop stays
        # responsive for HTMX polling. The thread only builds the row dicts;
        # persistence happens back on the main loop with our async session.
        rows = await asyncio.to_thread(
            _build_result_rows,
            base_dir,
            candidate_dir,
            diff_dir,
            threshold,
            settings.data_dir,
        )
        await _persist_results(comparison_id, rows)
    except Exception as exc:  # noqa: BLE001
        log.exception("comparison %s failed", comparison_id)
        async with sf() as session:
            comp = await session.get(Comparison, comparison_id)
            if comp is not None:
                comp.status = COMPARISON_FAILED
                comp.failure_reason = repr(exc)
                await session.commit()
        return

    async with sf() as session:
        comp = await session.get(Comparison, comparison_id)
        if comp is None:
            return
        comp.status = COMPARISON_READY
        await session.commit()


def _build_result_rows(
    base_dir: Path,
    candidate_dir: Path,
    diff_dir: Path,
    threshold: float,
    data_dir: Path,
) -> list[dict]:
    rows: list[dict] = []

    # Each Capture has per-platform subdirectories. Iterate the union of
    # platforms present in either side.
    base_platforms = _platforms_in(base_dir)
    candidate_platforms = _platforms_in(candidate_dir)
    all_platforms = sorted(base_platforms | candidate_platforms)

    for platform in all_platforms:
        base_index = _index_pngs_by_combination(base_dir / platform)
        candidate_index = _index_pngs_by_combination(candidate_dir / platform)
        combinations = sorted(set(base_index) | set(candidate_index))

        for combo in combinations:
            base_path = base_index.get(combo)
            candidate_path = candidate_index.get(combo)

            if base_path is None or candidate_path is None:
                rows.append(
                    {
                        "platform": platform,
                        "combination": combo,
                        "status": RESULT_ORPHAN,
                        "diff_percent": None,
                        "changed_pixels": None,
                        "base_path": _rel_if(base_path, data_dir),
                        "candidate_path": _rel_if(candidate_path, data_dir),
                        "diff_path": None,
                        "noise_reason": None,
                    }
                )
                continue

            overlay_dest = diff_dir / platform / combo
            try:
                result = diff.diff_images(
                    base_path,
                    candidate_path,
                    overlay_dest,
                    threshold=threshold,
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("diff failed for %s/%s: %r", platform, combo, exc)
                continue

            status = result.status
            noise_reason: Optional[str] = None
            if status == diff.STATUS_DIFFERS:
                hit = kn.match(platform, combo, result.changed_pixels or 0)
                if hit is not None:
                    status = RESULT_KNOWN_NOISE
                    noise_reason = hit.reason

            rows.append(
                {
                    "platform": platform,
                    "combination": combo,
                    "status": _map_diff_status(status),
                    "diff_percent": result.diff_percent,
                    "changed_pixels": result.changed_pixels,
                    "base_path": _rel(base_path, data_dir),
                    "candidate_path": _rel(candidate_path, data_dir),
                    "diff_path": _rel(overlay_dest, data_dir)
                    if status not in (RESULT_SIZE_MISMATCH,) and overlay_dest.exists()
                    else None,
                    "noise_reason": noise_reason,
                }
            )

    return rows


def _platforms_in(capture_dir: Path) -> set[str]:
    if not capture_dir.is_dir():
        return set()
    return {p.name for p in capture_dir.iterdir() if p.is_dir()}


def _index_pngs_by_combination(platform_dir: Path) -> dict[str, Path]:
    if not platform_dir.is_dir():
        return {}
    out: dict[str, Path] = {}
    for f in sorted(platform_dir.iterdir()):
        if not f.is_file() or f.suffix.lower() != ".png":
            continue
        out.setdefault(combination_name(f.name), f)
    return out


def _map_diff_status(s: str) -> str:
    return {
        diff.STATUS_IDENTICAL: RESULT_IDENTICAL,
        diff.STATUS_DIFFERS: RESULT_DIFFERS,
        diff.STATUS_SIZE_MISMATCH: RESULT_SIZE_MISMATCH,
        RESULT_KNOWN_NOISE: RESULT_KNOWN_NOISE,
    }.get(s, s)


def _rel(path: Path, data_dir: Path) -> str:
    """Path relative to the data dir, so the web layer can serve it
    via the /data static mount."""
    try:
        return str(path.resolve().relative_to(data_dir.resolve()))
    except ValueError:
        # Path lives outside data_dir — store absolute as a fallback;
        # the web layer will surface it as missing.
        return str(path)


def _rel_if(path: Optional[Path], data_dir: Path) -> Optional[str]:
    return _rel(path, data_dir) if path is not None else None


async def _persist_results(comparison_id: int, rows: list[dict]) -> None:
    sf = session_factory()
    async with sf() as session:
        # Wipe any stale results (e.g. retry of a failed comparison).
        existing = (
            await session.execute(
                select(Result).where(Result.comparison_id == comparison_id)
            )
        ).scalars().all()
        for r in existing:
            await session.delete(r)
        await session.flush()

        for row in rows:
            session.add(Result(comparison_id=comparison_id, **row))
        await session.commit()


__all__ = ["run_comparison"]
