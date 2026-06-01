"""Comparison detail, progress polling, triage, side-by-side."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .. import storage

from typing import Iterable, Optional

from ..db import get_db
from ..models import (
    COMPARISON_READY,
    CaptureTask,
    Comparison,
    RESULT_DIFFERS,
    RESULT_IDENTICAL,
    RESULT_KNOWN_NOISE,
    RESULT_ORPHAN,
    RESULT_SIZE_MISMATCH,
    Result,
    TRIAGE_VALUES,
)


router = APIRouter()


# Display order: differs first, then orphan/size-mismatch, then known
# noise, then identical (collapsed by default).
_DISPLAY_ORDER = {
    RESULT_DIFFERS: 0,
    RESULT_ORPHAN: 1,
    RESULT_SIZE_MISMATCH: 2,
    RESULT_KNOWN_NOISE: 3,
    RESULT_IDENTICAL: 4,
}


def normalize_sets(raw_values: Iterable[Optional[str]]) -> Optional[frozenset]:
    """Turn a capture's stored MOZSCREENSHOTS_SETS strings (one per task, e.g.
    "Toolbars,Tabs") into a normalized set of set-names. Returns None when
    *nothing* was recorded (sets unknown), distinct from an empty set."""
    names: set[str] = set()
    found = False
    for raw in raw_values:
        if raw:
            found = True
            for part in raw.split(","):
                part = part.strip()
                if part:
                    names.add(part)
    return frozenset(names) if found else None


def classify_sets(
    base: Optional[frozenset], candidate: Optional[frozenset]
) -> tuple[str, list[str], list[str]]:
    """Compare two captures' sets. Returns (status, base_only, candidate_only)
    where status is "unknown" (either side unrecorded), "match", or
    "mismatch"."""
    if base is None or candidate is None:
        return "unknown", [], []
    if base == candidate:
        return "match", [], []
    return "mismatch", sorted(base - candidate), sorted(candidate - base)


async def _capture_sets(db: AsyncSession, capture_id: int) -> Optional[frozenset]:
    rows = (
        await db.execute(
            select(CaptureTask.mozscreenshots_sets).where(
                CaptureTask.capture_id == capture_id
            )
        )
    ).scalars().all()
    return normalize_sets(rows)


async def _capture_nongreen(db: AsyncSession, capture_id: int) -> list[str]:
    """Distinct non-success source job results for a capture (e.g.
    ["testfailed"]). Empty when every source job was green or unknown — i.e.
    the capture is (probably) complete."""
    rows = (
        await db.execute(
            select(CaptureTask.job_result).where(
                CaptureTask.capture_id == capture_id
            )
        )
    ).scalars().all()
    return sorted({r for r in rows if r and r != "success"})


@router.get("/comparison/{comparison_id}")
async def comparison_detail(
    comparison_id: int, request: Request, db: AsyncSession = Depends(get_db)
):
    templates = request.app.state.templates
    comp = (
        await db.execute(
            select(Comparison)
            .where(Comparison.id == comparison_id)
            .options(
                selectinload(Comparison.base_capture),
                selectinload(Comparison.candidate_capture),
            )
        )
    ).scalars().first()
    if comp is None:
        raise HTTPException(status_code=404, detail="comparison not found")

    results = list(
        (
            await db.execute(
                select(Result).where(Result.comparison_id == comparison_id)
            )
        ).scalars().all()
    )
    results.sort(
        key=lambda r: (
            _DISPLAY_ORDER.get(r.status, 99),
            r.platform,
            r.combination,
        )
    )
    counts = _summary_counts(results)
    total = sum(counts.values())
    orphan_ratio = counts[RESULT_ORPHAN] / total if total else 0.0

    base_sets = await _capture_sets(db, comp.base_capture_id)
    cand_sets = await _capture_sets(db, comp.candidate_capture_id)
    sets_status, base_only_sets, cand_only_sets = classify_sets(base_sets, cand_sets)

    base_nongreen = await _capture_nongreen(db, comp.base_capture_id)
    cand_nongreen = await _capture_nongreen(db, comp.candidate_capture_id)

    return templates.TemplateResponse(
        "comparison.html",
        {
            "request": request,
            "comparison": comp,
            "comparison_id": comp.id,
            "base": comp.base_capture,
            "candidate": comp.candidate_capture,
            "results": results,
            "counts": counts,
            "total_results": total,
            "orphan_ratio": orphan_ratio,
            "sets_status": sets_status,
            "base_sets": sorted(base_sets) if base_sets else [],
            "cand_sets": sorted(cand_sets) if cand_sets else [],
            "base_only_sets": base_only_sets,
            "cand_only_sets": cand_only_sets,
            "base_nongreen": base_nongreen,
            "cand_nongreen": cand_nongreen,
        },
    )


@router.get("/comparison/{comparison_id}/progress")
async def comparison_progress(
    comparison_id: int, request: Request, db: AsyncSession = Depends(get_db)
):
    templates = request.app.state.templates
    comp = await db.get(Comparison, comparison_id)
    if comp is None:
        raise HTTPException(status_code=404, detail="comparison not found")

    # When the comparison just finished, tell HTMX to do a full-page refresh
    # so the user sees the results instead of having to reload manually.
    if comp.status == "ready":
        return Response(headers={"HX-Refresh": "true"})

    return templates.TemplateResponse(
        "partials/comparison_progress.html",
        {"request": request, "comparison": comp},
    )


@router.post("/comparison/{comparison_id}/delete")
async def delete_comparison(
    comparison_id: int, request: Request, db: AsyncSession = Depends(get_db)
):
    """Delete a comparison: its result rows and diff-overlay files. The
    captures it referenced are left untouched."""
    settings = request.app.state.settings
    comp = await db.get(Comparison, comparison_id)
    if comp is None:
        raise HTTPException(status_code=404, detail="comparison not found")
    await db.execute(delete(Result).where(Result.comparison_id == comparison_id))
    storage.remove_dir_within(
        settings.data_dir, storage.comparison_dir(settings.data_dir, comparison_id)
    )
    await db.delete(comp)
    await db.commit()
    return RedirectResponse(url="/", status_code=303)


@router.post("/comparison/{comparison_id}/result/{result_id}/triage")
async def update_triage(
    comparison_id: int,
    result_id: int,
    request: Request,
    triage: str = Form(...),
    notes: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    templates = request.app.state.templates
    if triage not in TRIAGE_VALUES:
        raise HTTPException(status_code=400, detail="invalid triage value")
    result = await db.get(Result, result_id)
    if result is None or result.comparison_id != comparison_id:
        raise HTTPException(status_code=404, detail="result not found")
    result.triage = triage
    result.notes = notes
    await db.commit()
    await db.refresh(result)
    return templates.TemplateResponse(
        "partials/result_row.html",
        {"request": request, "result": result, "comparison_id": comparison_id},
    )


@router.get("/comparison/{comparison_id}/side-by-side/{result_id}")
async def side_by_side(
    comparison_id: int,
    result_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    templates = request.app.state.templates
    result = await db.get(Result, result_id)
    if result is None or result.comparison_id != comparison_id:
        raise HTTPException(status_code=404, detail="result not found")
    return templates.TemplateResponse(
        "side_by_side.html",
        {
            "request": request,
            "result": result,
            "comparison_id": comparison_id,
        },
    )


def _summary_counts(results: list[Result]) -> dict[str, int]:
    out = {
        RESULT_DIFFERS: 0,
        RESULT_IDENTICAL: 0,
        RESULT_KNOWN_NOISE: 0,
        RESULT_ORPHAN: 0,
        RESULT_SIZE_MISMATCH: 0,
    }
    for r in results:
        if r.status in out:
            out[r.status] += 1
    return out
