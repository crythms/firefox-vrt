#!/usr/bin/env python3
"""Seed a fake VRT comparison so you can click through the UI without
needing live CI access.

What this does
--------------
Inserts two Captures (baseline + candidate), generates PNGs on disk so
they look like real screenshot artifacts, creates a Comparison between
them, and invokes the *real* comparator pipeline to produce Result rows
with real diff overlays. The only thing skipped is the Treeherder /
Taskcluster fetch path.

Result variety produced (so the UI exercises every status filter):
- one IDENTICAL pair        (e.g. default-chrome.png)
- two DIFFERS pairs         (different sizes of pixel change)
- one ORPHAN on baseline    (file only present on baseline side)
- one ORPHAN on candidate
- one SIZE-MISMATCH pair

Usage
-----
From the project root:

    .venv/bin/python scripts/seed_fake_run.py

It prints URLs to visit. If uvicorn isn't running, start it first
(`uvicorn firefox_vrt.app:app`). The seeded captures use synthetic
hex-only "revisions" so they never collide with real ones.
"""

from __future__ import annotations

import asyncio
import secrets
import sys
from pathlib import Path

import numpy as np
from PIL import Image


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from firefox_vrt import comparator, config, db, models  # noqa: E402


PLATFORM = "linux2404-64"


# (filename, baseline_color, candidate_color, size_kind)
# - color = None means the file is absent on that side (orphan)
# - size_kind:
#     "normal"   -> both 300x400
#     "mismatch" -> baseline 300x400, candidate 200x300 (size-mismatch)
FIXTURES = [
    ("default-chrome.png",      (240, 240, 240), (240, 240, 240), "normal"),
    ("toolbar-default.png",     (100, 100, 100), (140, 105, 100), "normal"),
    ("tabs-pinned.png",         (200, 200, 200), (255,   0,   0), "normal"),
    ("themes-dark.png",         ( 30,  30,  30), ( 30,  30,  60), "normal"),
    ("sidebar-bookmarks.png",   (180, 180, 180),  None,           "normal"),
    ("hamburger-menu-open.png",  None,           (180, 180, 180), "normal"),
    ("window-narrow.png",       (150, 150, 150), (150, 150, 150), "mismatch"),
]


def _make_png(path: Path, color: tuple[int, int, int], size: tuple[int, int]) -> None:
    h, w = size
    arr = np.full((h, w, 3), color, dtype=np.uint8)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(arr).save(path, format="PNG")


async def main() -> None:
    settings = config.load()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.captures_dir.mkdir(parents=True, exist_ok=True)

    db.configure(settings.database_url)
    await db.create_all()
    sf = db.session_factory()

    # Unique 40-char hex revisions so re-runs don't collide.
    baseline_rev = secrets.token_hex(20)
    candidate_rev = secrets.token_hex(20)

    async with sf() as session:
        baseline = models.Capture(
            revision=baseline_rev,
            project="try",
            status=models.CAPTURE_READY,
        )
        candidate = models.Capture(
            revision=candidate_rev,
            project="try",
            status=models.CAPTURE_READY,
        )
        session.add_all([baseline, candidate])
        await session.commit()
        await session.refresh(baseline)
        await session.refresh(candidate)

        baseline.dir_path = str(settings.captures_dir / str(baseline.id))
        candidate.dir_path = str(settings.captures_dir / str(candidate.id))

        # Add CaptureTask rows so the per-platform table renders.
        base_task = models.CaptureTask(
            capture_id=baseline.id,
            platform=PLATFORM,
            task_id="FAKE-BASELINE-TASK",
            run_id=0,
            status=models.TASK_READY,
        )
        cand_task = models.CaptureTask(
            capture_id=candidate.id,
            platform=PLATFORM,
            task_id="FAKE-CANDIDATE-TASK",
            run_id=0,
            status=models.TASK_READY,
        )
        session.add_all([base_task, cand_task])
        await session.commit()

    baseline_platform_dir = Path(baseline.dir_path) / PLATFORM
    candidate_platform_dir = Path(candidate.dir_path) / PLATFORM

    base_count = 0
    cand_count = 0
    for name, base_color, cand_color, size_kind in FIXTURES:
        base_size = (300, 400)
        cand_size = (200, 300) if size_kind == "mismatch" else (300, 400)
        if base_color is not None:
            _make_png(baseline_platform_dir / name, base_color, base_size)
            base_count += 1
        if cand_color is not None:
            _make_png(candidate_platform_dir / name, cand_color, cand_size)
            cand_count += 1

    # Patch artifact_count + downloaded_count on the task rows for honesty.
    async with sf() as session:
        bt = await session.get(models.CaptureTask, base_task.id)
        ct = await session.get(models.CaptureTask, cand_task.id)
        assert bt and ct
        bt.artifact_count = base_count
        bt.downloaded_count = base_count
        ct.artifact_count = cand_count
        ct.downloaded_count = cand_count
        await session.commit()

    # Create the Comparison and run the *real* comparator pipeline.
    async with sf() as session:
        comp = models.Comparison(
            base_capture_id=baseline.id,
            candidate_capture_id=candidate.id,
        )
        session.add(comp)
        await session.commit()
        await session.refresh(comp)
        comparison_id = comp.id

    print(f"Seeded baseline   capture id={baseline.id}  rev={baseline_rev[:12]}…  ({base_count} PNGs)")
    print(f"Seeded candidate  capture id={candidate.id}  rev={candidate_rev[:12]}…  ({cand_count} PNGs)")
    print(f"Running comparator on comparison id={comparison_id} …")
    await comparator.run_comparison(comparison_id, settings)

    async with sf() as session:
        comp = await session.get(models.Comparison, comparison_id)
        assert comp is not None
        print(f"Comparison status: {comp.status}")
        if comp.failure_reason:
            print(f"Failure reason: {comp.failure_reason}")

    print()
    print("Visit (assuming uvicorn on http://localhost:8000):")
    print(f"  Baseline:    http://localhost:8000/capture/{baseline.id}")
    print(f"  Candidate:   http://localhost:8000/capture/{candidate.id}")
    print(f"  Comparison:  http://localhost:8000/comparison/{comparison_id}")
    print()
    print("Or open the landing page (recent captures + comparisons appear there):")
    print("  http://localhost:8000/")


if __name__ == "__main__":
    asyncio.run(main())
