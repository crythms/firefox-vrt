"""Capture detail, progress polling, and triggering a Comparison."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import comparator, storage
from ..db import get_db
from ..models import (
    CAPTURE_FAILED,
    CAPTURE_READY,
    Capture,
    CaptureTask,
    Comparison,
    Result,
)


router = APIRouter()


@router.get("/capture/{capture_id}")
async def capture_detail(
    capture_id: int, request: Request, db: AsyncSession = Depends(get_db)
):
    templates = request.app.state.templates
    cap = await db.get(Capture, capture_id)
    if cap is None:
        raise HTTPException(status_code=404, detail="capture not found")
    tasks = (
        await db.execute(
            select(CaptureTask).where(CaptureTask.capture_id == capture_id)
        )
    ).scalars().all()
    # Source CI jobs that weren't green → this capture may be partial. Surface
    # as a warning so a missing screenshot reads as "job died early", not data.
    nongreen_tasks = [
        t for t in tasks if t.job_result and t.job_result != "success"
    ]
    # Recent captures usable as a baseline picker. Includes the current
    # capture so engineers can do a "diff against self" sanity check —
    # useful for validating the fetch + diff pipeline without needing
    # two real pushes.
    baseline_candidates = (
        await db.execute(
            select(Capture)
            .where(Capture.status == CAPTURE_READY)
            .order_by(Capture.id.desc())
            .limit(20)
        )
    ).scalars().all()
    # Screenshot sets per candidate, for the baseline dropdown labels.
    cand_ids = [c.id for c in baseline_candidates]
    candidate_sets: dict[int, str] = {}
    if cand_ids:
        rows = await db.execute(
            select(CaptureTask.capture_id, CaptureTask.mozscreenshots_sets).where(
                CaptureTask.capture_id.in_(cand_ids)
            )
        )
        raw: dict[int, set] = {}
        for cap_id, sets in rows.all():
            if sets:
                raw.setdefault(cap_id, set()).add(sets)
        candidate_sets = {k: " / ".join(sorted(v)) for k, v in raw.items()}
    return templates.TemplateResponse(
        "capture.html",
        {
            "request": request,
            "capture": cap,
            "tasks": tasks,
            "nongreen_tasks": nongreen_tasks,
            "baseline_candidates": baseline_candidates,
            "candidate_sets": candidate_sets,
        },
    )


@router.get("/capture/{capture_id}/progress")
async def capture_progress(
    capture_id: int, request: Request, db: AsyncSession = Depends(get_db)
):
    templates = request.app.state.templates
    cap = await db.get(Capture, capture_id)
    if cap is None:
        raise HTTPException(status_code=404, detail="capture not found")
    # Once the fetch reaches a terminal state, refresh the whole page rather
    # than swapping just the #progress region. Sections that live outside it —
    # the partial-capture warning and the "Compare against a baseline" form —
    # are only rendered on a full page load, so without this they'd require a
    # manual refresh to appear.
    if cap.status in (CAPTURE_READY, CAPTURE_FAILED):
        return Response(headers={"HX-Refresh": "true"})
    tasks = (
        await db.execute(
            select(CaptureTask).where(CaptureTask.capture_id == capture_id)
        )
    ).scalars().all()
    return templates.TemplateResponse(
        "partials/capture_progress.html",
        {"request": request, "capture": cap, "tasks": tasks},
    )


@router.post("/capture/{capture_id}/compare")
async def kick_comparison(
    capture_id: int,
    request: Request,
    background: BackgroundTasks,
    base_capture_id: int = Form(...),
    db: AsyncSession = Depends(get_db),
):
    settings = request.app.state.settings
    candidate = await db.get(Capture, capture_id)
    base = await db.get(Capture, base_capture_id)
    if candidate is None or base is None:
        raise HTTPException(status_code=404, detail="capture not found")
    if candidate.status != CAPTURE_READY or base.status != CAPTURE_READY:
        raise HTTPException(
            status_code=400, detail="both captures must be ready"
        )

    # Reuse an existing Comparison row if we already diffed this pair.
    existing = (
        await db.execute(
            select(Comparison).where(
                Comparison.base_capture_id == base.id,
                Comparison.candidate_capture_id == candidate.id,
            )
        )
    ).scalars().first()
    if existing is not None:
        return RedirectResponse(url=f"/comparison/{existing.id}", status_code=303)

    comp = Comparison(base_capture_id=base.id, candidate_capture_id=candidate.id)
    db.add(comp)
    await db.commit()
    await db.refresh(comp)

    background.add_task(comparator.run_comparison, comp.id, settings)
    return RedirectResponse(url=f"/comparison/{comp.id}", status_code=303)


@router.post("/capture/{capture_id}/delete")
async def delete_capture(
    capture_id: int, request: Request, db: AsyncSession = Depends(get_db)
):
    """Delete a capture: its task rows and screenshot files, plus any
    comparison that referenced it (a comparison is meaningless once one side is
    gone, and would otherwise render a broken page). Files removed too."""
    settings = request.app.state.settings
    cap = await db.get(Capture, capture_id)
    if cap is None:
        raise HTTPException(status_code=404, detail="capture not found")

    # Comparisons that use this capture on either side go with it.
    dependent = (
        await db.execute(
            select(Comparison).where(
                or_(
                    Comparison.base_capture_id == capture_id,
                    Comparison.candidate_capture_id == capture_id,
                )
            )
        )
    ).scalars().all()
    for comp in dependent:
        await db.execute(delete(Result).where(Result.comparison_id == comp.id))
        storage.remove_dir_within(
            settings.data_dir, storage.comparison_dir(settings.data_dir, comp.id)
        )
        await db.delete(comp)

    await db.execute(delete(CaptureTask).where(CaptureTask.capture_id == capture_id))
    cap_dir = (
        Path(cap.dir_path)
        if cap.dir_path
        else storage.capture_dir(settings.data_dir, capture_id)
    )
    storage.remove_dir_within(settings.data_dir, cap_dir)
    await db.delete(cap)
    await db.commit()
    return RedirectResponse(url="/", status_code=303)
