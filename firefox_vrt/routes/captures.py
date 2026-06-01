"""Capture detail, progress polling, and triggering a Comparison."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import comparator
from ..db import get_db
from ..models import (
    CAPTURE_READY,
    Capture,
    CaptureTask,
    Comparison,
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
    # Capture-level summary of the screenshot sets these tasks ran. Usually a
    # single value shared across platforms; flag the rare per-platform split.
    set_values = sorted({t.mozscreenshots_sets for t in tasks if t.mozscreenshots_sets})
    if not set_values:
        sets_summary = None
    elif len(set_values) == 1:
        sets_summary = set_values[0]
    else:
        sets_summary = "varies by platform — see per-task table"
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
    return templates.TemplateResponse(
        "capture.html",
        {
            "request": request,
            "capture": cap,
            "tasks": tasks,
            "sets_summary": sets_summary,
            "baseline_candidates": baseline_candidates,
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
