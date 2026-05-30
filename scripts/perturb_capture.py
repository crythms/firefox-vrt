#!/usr/bin/env python3
"""Clone an existing (real) capture and inject deliberate visual diffs,
then compare the original against the clone — so you can see the
`differs` path and the triage UI light up on real screenshots without
needing an actual chrome regression in CI.

What this does
--------------
1. Reads a source Capture (e.g. the one you just fetched from try).
2. Creates a new Capture that copies every PNG from the source.
3. Paints a magenta rectangle onto a few of the cloned PNGs to simulate
   a chrome change (one subtle, one large), leaving the rest identical.
4. Optionally drops one file (orphan) and resizes one (size-mismatch),
   so every status chip has something to show.
5. Creates a Comparison (source = baseline, clone = candidate) and runs
   the *real* comparator pipeline to produce Result rows + diff overlays.

Usage
-----
From the project root:

    .venv/bin/python scripts/perturb_capture.py <source_capture_id>
    # exercise every status chip (orphan + size-mismatch too):
    .venv/bin/python scripts/perturb_capture.py <source_capture_id> --all-statuses
"""

from __future__ import annotations

import argparse
import asyncio
import secrets
import shutil
import sys
from pathlib import Path

from PIL import Image, ImageDraw
from sqlalchemy import select

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from firefox_vrt import comparator, config, db, models  # noqa: E402

PERTURB = (255, 0, 255)  # magenta — well outside the 8/255 diff tolerance


def _paint_rect(path: Path, frac_w: float, frac_h: float) -> None:
    """Paint a filled rectangle covering frac_w x frac_h of the image,
    anchored near the top (where the toolbar lives)."""
    img = Image.open(path).convert("RGB")
    w, h = img.size
    rw, rh = max(1, int(w * frac_w)), max(1, int(h * frac_h))
    x0 = (w - rw) // 2
    y0 = max(0, int(h * 0.04))
    ImageDraw.Draw(img).rectangle([x0, y0, x0 + rw, y0 + rh], fill=PERTURB)
    img.save(path, format="PNG")


def _resize(path: Path) -> None:
    img = Image.open(path).convert("RGB")
    w, h = img.size
    img.resize((max(1, w - 40), max(1, h - 40))).save(path, format="PNG")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("source_capture_id", type=int)
    ap.add_argument("--all-statuses", action="store_true",
                    help="also create an orphan + size-mismatch result")
    args = ap.parse_args()

    settings = config.load()
    db.configure(settings.database_url)
    sf = db.session_factory()

    async with sf() as session:
        src = await session.get(models.Capture, args.source_capture_id)
        if src is None:
            print(f"No capture with id={args.source_capture_id}")
            sys.exit(1)
        src_dir = Path(src.dir_path)
        src_project = src.project
        task_rows = (await session.execute(
            select(models.CaptureTask).where(
                models.CaptureTask.capture_id == args.source_capture_id
            )
        )).scalars().all()
        # Extract plain values; the ORM objects detach after the session closes.
        src_tasks = [
            (t.platform, t.task_id, t.run_id, t.artifact_count, t.downloaded_count)
            for t in task_rows
        ]
        src_platforms = [p.name for p in src_dir.iterdir() if p.is_dir()]

    if not src_platforms:
        print(f"Source capture {args.source_capture_id} has no platform dirs on disk.")
        sys.exit(1)

    # Create the clone capture.
    async with sf() as session:
        clone = models.Capture(
            revision="perturbed-" + secrets.token_hex(12),
            project=src_project,
            status=models.CAPTURE_READY,
        )
        session.add(clone)
        await session.commit()
        await session.refresh(clone)
        clone.dir_path = str(settings.captures_dir / str(clone.id))
        for platform, task_id, run_id, artifact_count, downloaded_count in src_tasks:
            session.add(models.CaptureTask(
                capture_id=clone.id,
                platform=platform,
                task_id=f"PERTURBED-{task_id}",
                run_id=run_id,
                status=models.TASK_READY,
                artifact_count=artifact_count,
                downloaded_count=downloaded_count or 0,
            ))
        await session.commit()
        clone_id = clone.id
        clone_dir = Path(clone.dir_path)

    # Copy all PNGs, then perturb a subset.
    if clone_dir.exists():
        shutil.rmtree(clone_dir)
    shutil.copytree(src_dir, clone_dir)

    notes: list[str] = []
    for platform in src_platforms:
        pngs = sorted((clone_dir / platform).glob("*.png"))
        if not pngs:
            continue
        # Subtle diff on the first image, large diff on the second.
        _paint_rect(pngs[0], frac_w=0.10, frac_h=0.03)
        notes.append(f"{platform}/{pngs[0].name}: subtle diff")
        if len(pngs) > 1:
            _paint_rect(pngs[1], frac_w=0.30, frac_h=0.20)
            notes.append(f"{platform}/{pngs[1].name}: large diff")
        if args.all_statuses and len(pngs) > 2:
            pngs[2].unlink()
            notes.append(f"{platform}/{pngs[2].name}: removed (orphan)")
        if args.all_statuses and len(pngs) > 3:
            _resize(pngs[3])
            notes.append(f"{platform}/{pngs[3].name}: resized (size-mismatch)")

    # Create the comparison: source = baseline, clone = candidate.
    async with sf() as session:
        comp = models.Comparison(
            base_capture_id=args.source_capture_id,
            candidate_capture_id=clone_id,
        )
        session.add(comp)
        await session.commit()
        await session.refresh(comp)
        comparison_id = comp.id

    print(f"Cloned capture {args.source_capture_id} -> {clone_id}")
    for n in notes:
        print(f"  perturbed: {n}")
    print(f"Running comparator on comparison id={comparison_id} ...")
    await comparator.run_comparison(comparison_id, settings)

    async with sf() as session:
        comp = await session.get(models.Comparison, comparison_id)
        assert comp is not None
        print(f"Comparison status: {comp.status}")
        if comp.failure_reason:
            print(f"Failure reason: {comp.failure_reason}")

    print()
    print("Visit (assuming uvicorn on http://localhost:8000):")
    print(f"  Comparison: http://localhost:8000/comparison/{comparison_id}")


if __name__ == "__main__":
    asyncio.run(main())
