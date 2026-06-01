"""Fetcher: orchestrates a Capture's full fetch from Mozilla CI.

Given a Capture row in the DB, this:
  1. Asks Treeherder for the browser-screenshots tasks on that push.
  2. Lists each task's artifacts via Taskcluster.
  3. Downloads PNG artifacts (in parallel, with a semaphore) to disk.
  4. Updates the Capture and per-platform CaptureTask statuses as it goes.

Intended to be invoked as a FastAPI BackgroundTask. Polling endpoints
read the same row to surface progress to the UI.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Callable, Optional

from sqlalchemy import select

from . import models
from .config import Settings
from .db import session_factory
from .models import (
    CAPTURE_FAILED,
    CAPTURE_FETCHING,
    CAPTURE_READY,
    Capture,
    CaptureTask,
    TASK_FAILED,
    TASK_FETCHING,
    TASK_READY,
)
from .taskcluster import (
    Artifact,
    ArtifactsExpired,
    Clients,
    NoScreenshotsJob,
)


log = logging.getLogger(__name__)


ClientsFactory = Callable[[], Clients]


async def fetch_capture(
    capture_id: int,
    settings: Settings,
    clients_factory: Optional[ClientsFactory] = None,
) -> None:
    """Drive the full fetch for one Capture. Runs in a background task.

    Wrapped in a top-level try/except so ANY unhandled exception lands as
    a clean `failed` state with a reason the UI can show, instead of
    leaving the Capture stuck in `fetching` forever.
    """
    try:
        await _fetch_capture_impl(capture_id, settings, clients_factory)
    except Exception as exc:  # noqa: BLE001
        log.exception("fetch_capture: unhandled error for capture %s", capture_id)
        await _mark_capture_failed(capture_id, f"Unhandled error: {exc!r}")


async def _fetch_capture_impl(
    capture_id: int,
    settings: Settings,
    clients_factory: Optional[ClientsFactory] = None,
) -> None:
    factory = clients_factory or (lambda: Clients(
        taskcluster_root=settings.taskcluster_root,
        treeherder_root=settings.treeherder_root,
        hg_root=settings.hg_root,
        user_agent=settings.user_agent,
    ))

    sf = session_factory()
    async with sf() as session:
        cap = await session.get(Capture, capture_id)
        if cap is None:
            log.error("fetch_capture: capture %s not found", capture_id)
            return

        cap.status = CAPTURE_FETCHING
        capture_dir = settings.captures_dir / str(cap.id)
        capture_dir.mkdir(parents=True, exist_ok=True)
        cap.dir_path = str(capture_dir)
        await session.commit()
        revision = cap.revision
        project = cap.project

    async with factory() as clients:
        # --- Resolve tasks via Treeherder ---
        try:
            tasks = await clients.find_screenshots_tasks(project, revision)
        except NoScreenshotsJob as exc:
            await _mark_capture_failed(capture_id, str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            log.exception("fetch_capture: treeherder lookup failed")
            await _mark_capture_failed(capture_id, f"Treeherder error: {exc!r}")
            return

        # Persist task rows and resolve push id.
        try:
            push_id_lookup = await clients.resolve_push(project, revision)
        except Exception as exc:  # noqa: BLE001
            log.exception("fetch_capture: push lookup failed")
            await _mark_capture_failed(capture_id, f"Push lookup error: {exc!r}")
            return

        async with sf() as session:
            cap = await session.get(Capture, capture_id)
            assert cap is not None
            cap.push_id = push_id_lookup
            for t in tasks:
                # Record which MOZSCREENSHOTS_SETS this task ran, from its
                # Taskcluster task definition. Best-effort: a failure here
                # (expired def, network blip) just leaves sets unknown and
                # never blocks the fetch.
                sets = None
                try:
                    env = await clients.get_task_env(t.task_id)
                    sets = env.get("MOZSCREENSHOTS_SETS") or None
                except Exception as exc:  # noqa: BLE001
                    log.warning("get_task_env failed for %s: %r", t.task_id, exc)
                session.add(
                    CaptureTask(
                        capture_id=cap.id,
                        platform=t.platform,
                        task_id=t.task_id,
                        run_id=t.run_id,
                        status=models.TASK_PENDING,
                        mozscreenshots_sets=sets,
                    )
                )
            await session.commit()
            await session.refresh(cap)

        # --- Fetch each task's artifacts ---
        sem = asyncio.Semaphore(settings.download_concurrency)

        async with sf() as session:
            db_tasks = (
                await session.execute(
                    select(CaptureTask).where(CaptureTask.capture_id == capture_id)
                )
            ).scalars().all()

        # return_exceptions=True so one failed platform doesn't abort the others.
        gather_results = await asyncio.gather(
            *[
                _fetch_one_task(db_task.id, capture_dir, clients, sem)
                for db_task in db_tasks
            ],
            return_exceptions=True,
        )
        for r in gather_results:
            if isinstance(r, Exception):
                log.warning("_fetch_one_task raised: %r", r)

    # --- Final status: ready if any task succeeded, failed otherwise ---
    async with sf() as session:
        cap = await session.get(Capture, capture_id)
        assert cap is not None
        task_rows = (
            await session.execute(
                select(CaptureTask).where(CaptureTask.capture_id == capture_id)
            )
        ).scalars().all()
        if any(t.status == TASK_READY for t in task_rows):
            cap.status = CAPTURE_READY
        else:
            cap.status = CAPTURE_FAILED
            cap.failure_reason = cap.failure_reason or "All per-platform fetches failed."
        await session.commit()


async def _fetch_one_task(
    db_task_id: int,
    capture_dir: Path,
    clients: Clients,
    sem: asyncio.Semaphore,
) -> None:
    sf = session_factory()

    async with sf() as session:
        db_task = await session.get(CaptureTask, db_task_id)
        assert db_task is not None
        db_task.status = TASK_FETCHING
        await session.commit()
        platform = db_task.platform
        task_id = db_task.task_id
        run_id = db_task.run_id

    platform_dir = capture_dir / platform
    platform_dir.mkdir(parents=True, exist_ok=True)

    try:
        artifacts = await clients.list_artifacts(task_id, run_id)
    except ArtifactsExpired as exc:
        await _mark_task_failed(db_task_id, str(exc))
        return
    except Exception as exc:  # noqa: BLE001
        log.exception("list_artifacts failed for %s/%s", task_id, run_id)
        await _mark_task_failed(db_task_id, f"list_artifacts: {exc!r}")
        return

    pngs = [
        a
        for a in artifacts
        if a.content_type == "image/png"
        and a.name.endswith(".png")
        and "mozilla-test-fail-" not in a.name
    ]

    async with sf() as session:
        db_task = await session.get(CaptureTask, db_task_id)
        assert db_task is not None
        db_task.artifact_count = len(pngs)
        await session.commit()

    if not pngs:
        await _mark_task_failed(
            db_task_id,
            (
                f"Task {task_id} published {len(artifacts)} artifacts but no PNGs. "
                "The mochitest-browser-screenshots job is a no-op unless the task "
                "is launched with MOZSCREENSHOTS_SETS set in its env. Most in-tree "
                "CI runs (autoland tip, m-c nightly) don't set it, so they "
                "complete green without capturing anything. Use a try push that "
                "explicitly opts in (e.g. `mach try fuzzy -q browser-screenshots "
                "--env MOZSCREENSHOTS_SETS=Toolbars,Tabs,WindowSize,...`)."
            ),
        )
        return

    # Download each PNG; tolerate individual failures.
    succeeded = 0
    for art in pngs:
        async with sem:
            try:
                await _download_artifact(
                    clients, task_id, run_id, art, platform_dir, db_task_id
                )
                succeeded += 1
            except Exception as exc:  # noqa: BLE001
                log.warning("download failed for %s: %r", art.name, exc)

    async with sf() as session:
        db_task = await session.get(CaptureTask, db_task_id)
        assert db_task is not None
        if succeeded == 0:
            db_task.status = TASK_FAILED
            db_task.failure_reason = "All artifact downloads failed."
        else:
            db_task.status = TASK_READY
        await session.commit()


async def _download_artifact(
    clients: Clients,
    task_id: str,
    run_id: int,
    artifact: Artifact,
    dest_dir: Path,
    db_task_id: int,
) -> None:
    # Use basename only — Taskcluster paths nest under public/test_info/ etc.,
    # but we want filename-only on disk for clean pairing.
    filename = artifact.name.rsplit("/", 1)[-1]
    dest = dest_dir / filename
    tmp = dest.with_suffix(dest.suffix + ".part")

    with open(tmp, "wb") as fh:
        async for chunk in clients.stream_artifact(task_id, run_id, artifact.name):
            fh.write(chunk)
    tmp.replace(dest)

    sf = session_factory()
    async with sf() as session:
        db_task = await session.get(CaptureTask, db_task_id)
        assert db_task is not None
        db_task.downloaded_count += 1
        await session.commit()


async def _mark_capture_failed(capture_id: int, reason: str) -> None:
    sf = session_factory()
    async with sf() as session:
        cap = await session.get(Capture, capture_id)
        if cap is None:
            return
        cap.status = CAPTURE_FAILED
        cap.failure_reason = reason
        await session.commit()


async def _mark_task_failed(task_id: int, reason: str) -> None:
    sf = session_factory()
    async with sf() as session:
        t = await session.get(CaptureTask, task_id)
        if t is None:
            return
        t.status = TASK_FAILED
        t.failure_reason = reason
        await session.commit()


__all__ = ["fetch_capture"]
