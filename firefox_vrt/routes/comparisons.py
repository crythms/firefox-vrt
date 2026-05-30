"""Comparison detail, progress polling, triage, side-by-side."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..db import get_db
from ..models import (
    COMPARISON_READY,
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
