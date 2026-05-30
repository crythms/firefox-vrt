"""Landing page + capture-creation entry point."""

from __future__ import annotations

import re

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import fetcher
from ..db import get_db
from ..models import Capture, Comparison


router = APIRouter()


VALID_PROJECTS = {"mozilla-central", "autoland", "try"}
REVISION_RE = re.compile(r"^[0-9a-fA-F]{12,40}$")


@router.get("/")
async def landing(request: Request, db: AsyncSession = Depends(get_db)):
    templates = request.app.state.templates
    captures = (
        await db.execute(
            select(Capture).order_by(Capture.id.desc()).limit(20)
        )
    ).scalars().all()
    comparisons = (
        await db.execute(
            select(Comparison).order_by(Comparison.id.desc()).limit(20)
        )
    ).scalars().all()
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "captures": captures,
            "comparisons": comparisons,
            "projects": sorted(VALID_PROJECTS),
        },
    )


@router.post("/capture")
async def create_capture(
    request: Request,
    background: BackgroundTasks,
    revision: str = Form(...),
    project: str = Form("try"),
    db: AsyncSession = Depends(get_db),
):
    settings = request.app.state.settings
    templates = request.app.state.templates

    rev = revision.strip().lower()
    if project not in VALID_PROJECTS:
        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "captures": [],
                "comparisons": [],
                "projects": sorted(VALID_PROJECTS),
                "error": f"Unknown project '{project}'.",
            },
            status_code=400,
        )
    if not REVISION_RE.match(rev):
        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "captures": [],
                "comparisons": [],
                "projects": sorted(VALID_PROJECTS),
                "error": "Revision must be 12–40 hex characters.",
            },
            status_code=400,
        )

    # If we already have a capture for this (rev, project), reuse it.
    existing = (
        await db.execute(
            select(Capture).where(
                Capture.revision == rev, Capture.project == project
            )
        )
    ).scalars().first()
    if existing is not None:
        return RedirectResponse(url=f"/capture/{existing.id}", status_code=303)

    cap = Capture(revision=rev, project=project)
    db.add(cap)
    await db.commit()
    await db.refresh(cap)

    background.add_task(fetcher.fetch_capture, cap.id, settings)
    return RedirectResponse(url=f"/capture/{cap.id}", status_code=303)
