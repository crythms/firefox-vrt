#!/usr/bin/env python3
"""Backfill CaptureTask.mozscreenshots_sets for captures fetched before we
started recording it.

For every CaptureTask with no recorded sets, re-read its Taskcluster task
definition's MOZSCREENSHOTS_SETS env var and store it. Tasks whose definition
has already expired (~4 weeks on try) stay unknown — there's nothing left to
read, which is precisely why we now capture this at fetch time.

Usage:
    .venv/bin/python scripts/backfill_mozscreenshots_sets.py
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select

from firefox_vrt import config
from firefox_vrt.db import configure, session_factory
from firefox_vrt.models import CaptureTask
from firefox_vrt.taskcluster import Clients


async def main() -> None:
    settings = config.load()
    configure(settings.database_url)
    sf = session_factory()

    async with sf() as session:
        rows = (
            await session.execute(
                select(CaptureTask).where(CaptureTask.mozscreenshots_sets.is_(None))
            )
        ).scalars().all()
        targets = [(t.id, t.task_id) for t in rows]

    if not targets:
        print("Nothing to backfill — every task already has sets recorded.")
        return

    print(f"Backfilling {len(targets)} task(s)...")
    async with Clients(
        taskcluster_root=settings.taskcluster_root,
        treeherder_root=settings.treeherder_root,
        hg_root=settings.hg_root,
        user_agent=settings.user_agent,
        trust_env=False,
    ) as clients:
        for db_id, task_id in targets:
            sets = None
            try:
                env = await clients.get_task_env(task_id)
                sets = env.get("MOZSCREENSHOTS_SETS") or None
            except Exception as exc:  # noqa: BLE001
                print(f"  task {task_id}: lookup failed ({exc!r})")
                continue
            label = sets if sets else "(unknown — def expired / no env)"
            print(f"  task {task_id}: {label}")
            if sets is None:
                continue
            async with sf() as session:
                t = await session.get(CaptureTask, db_id)
                if t is not None:
                    t.mozscreenshots_sets = sets
                    await session.commit()

    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
