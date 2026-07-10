"""Local drawWindow capture *source*.

Turns the ui_states x configs matrix into `Capture` rows (+ screenshots on disk +
`CaptureTask`) that the app's existing pairing/diff/UI consume unchanged -- the
local analogue of `fetcher.fetch_capture`, which pulls mozscreenshots artifacts
from CI. One `Capture` per config: `revision` = config name, `project` = a label
(default "local"); each ui_state is a `combination` (filename `<ui_state>.png`)
under a single platform directory.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional

from sqlalchemy import select

from .. import models, storage
from ..config import Settings
from ..db import session_factory
from ..models import (
    CAPTURE_PENDING,
    CAPTURE_READY,
    TASK_READY,
    Capture,
    CaptureTask,
    Comparison,
)
from .runner import capture_matrix, capture_states
from .scenarios import DEFAULT_SCENARIO, Scenario

log = logging.getLogger(__name__)

DEFAULT_PLATFORM = "local"


async def _replace_existing(session, settings: Settings, revision: str, project: str) -> None:
    """Delete any prior Capture for (revision, project) and its on-disk files,
    so re-capturing a config is idempotent."""
    existing = (
        await session.execute(
            select(Capture).where(
                Capture.revision == revision, Capture.project == project
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        storage.remove_dir_within(
            settings.data_dir, storage.capture_dir(settings.data_dir, existing.id)
        )
        await session.delete(existing)
        await session.flush()


async def create_capture(
    settings: Settings,
    revision: str,
    project: str,
    screenshots: dict[str, bytes],
    platform: str = DEFAULT_PLATFORM,
) -> int:
    """Persist {ui_state: png_bytes} as one ready Capture. Returns its id."""
    sf = session_factory()
    async with sf() as session:
        await _replace_existing(session, settings, revision, project)
        cap = Capture(revision=revision, project=project, status=CAPTURE_PENDING)
        session.add(cap)
        await session.commit()
        await session.refresh(cap)

        cap_dir = settings.captures_dir / str(cap.id)
        platform_dir = cap_dir / platform
        platform_dir.mkdir(parents=True, exist_ok=True)
        for ui_state, png in screenshots.items():
            (platform_dir / f"{ui_state}.png").write_bytes(png)

        cap.dir_path = str(cap_dir)
        cap.status = CAPTURE_READY
        session.add(
            CaptureTask(
                capture_id=cap.id,
                platform=platform,
                task_id="local",
                run_id=0,
                status=TASK_READY,
                artifact_count=len(screenshots),
                downloaded_count=len(screenshots),
            )
        )
        await session.commit()
        return cap.id


async def capture_configs(
    binary,
    settings: Settings,
    scenario: Scenario = DEFAULT_SCENARIO,
    project: str = "local",
    log_dir: Optional[Path] = None,
) -> dict[str, int]:
    """Run the whole matrix and create one Capture per config.

    Returns {config_name: capture_id}. The matrix launches Firefox (blocking), so
    it runs in a worker thread to keep an event loop responsive.
    """
    matrix = await asyncio.to_thread(capture_matrix, binary, scenario, log_dir)
    ids: dict[str, int] = {}
    for config_name, states in matrix.items():
        ids[config_name] = await create_capture(settings, config_name, project, states)
    return ids


async def create_comparison(
    settings: Settings, base_id: int, cand_id: int, threshold: float = 0.001
) -> int:
    """Create (replacing any existing) a base-vs-candidate Comparison; return id."""
    sf = session_factory()
    async with sf() as session:
        existing = (
            await session.execute(
                select(Comparison).where(
                    Comparison.base_capture_id == base_id,
                    Comparison.candidate_capture_id == cand_id,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            await session.delete(existing)
            await session.flush()
        comp = Comparison(
            base_capture_id=base_id, candidate_capture_id=cand_id, threshold=threshold
        )
        session.add(comp)
        await session.commit()
        await session.refresh(comp)
        return comp.id


async def capture_versions(
    base_binary,
    candidate_binary,
    settings: Settings,
    scenario: Scenario = DEFAULT_SCENARIO,
    config=None,
    base_label: str = "base",
    candidate_label: str = "candidate",
    log_dir: Optional[Path] = None,
) -> tuple[int, int, int]:
    """Version-vs-version: capture one config on two builds and auto-create the
    comparison. Captures are keyed revision=<label>, project="local:<config>"
    (same config, different build). Returns (base_id, candidate_id, comparison_id).
    """
    cfg = config or scenario.configs[0]
    project = f"local:{cfg.name}"
    base_states = await asyncio.to_thread(
        capture_states, base_binary, scenario, cfg.prefs, log_dir, "base"
    )
    cand_states = await asyncio.to_thread(
        capture_states, candidate_binary, scenario, cfg.prefs, log_dir, "candidate"
    )
    base_id = await create_capture(settings, base_label, project, base_states)
    cand_id = await create_capture(settings, candidate_label, project, cand_states)
    comp_id = await create_comparison(settings, base_id, cand_id)
    return base_id, cand_id, comp_id


__all__ = [
    "DEFAULT_PLATFORM",
    "capture_configs",
    "capture_versions",
    "create_capture",
    "create_comparison",
]


if __name__ == "__main__":
    # Demo/verify. Default mode diffs two configs on one build; `versions` mode
    # captures one config on two builds and diffs (pass the same binary twice as
    # a wiring/determinism check -> expect ~0 diff).
    import glob
    import os
    import sys

    from .. import config, db
    from ..comparator import run_comparison
    from ..models import Result

    async def _print_results(comp_id):
        sf = session_factory()
        async with sf() as session:
            comp = await session.get(Comparison, comp_id)
            rows = (
                await session.execute(
                    select(Result)
                    .where(Result.comparison_id == comp_id)
                    .order_by(Result.combination)
                )
            ).scalars().all()
            print(f"\ncomparison {comp_id}: status={comp.status}, {len(rows)} results")
            for r in rows:
                print(
                    f"  [{r.platform}] {r.combination}: {r.status}"
                    f" diff={r.diff_percent} px={r.changed_pixels}"
                )

    async def _main():
        repo = Path(__file__).resolve().parents[2]
        os.environ.setdefault("DATA_DIR", str(repo / "data"))
        settings = config.load()
        db.configure(settings.database_url)
        await db.create_all()

        matches = sorted(
            glob.glob("/Users/cthomas/firefox/obj-*/dist/*.app/Contents/MacOS/firefox")
        )
        if not matches:
            raise SystemExit("no local build found")
        binary = matches[-1]

        logs = repo / "spike_out" / "matrix" / "logs"
        logs.mkdir(parents=True, exist_ok=True)

        if sys.argv[1:] and sys.argv[1] == "versions":
            base_id, cand_id, comp_id = await capture_versions(
                binary, binary, settings, log_dir=logs
            )
            print(f"base={base_id} candidate={cand_id} comparison={comp_id}")
        else:
            ids = await capture_configs(binary, settings, log_dir=logs)
            print(f"captures: {ids}")
            comp_id = await create_comparison(
                settings, ids["default"], ids["sidebar-revamp"]
            )
        await run_comparison(comp_id, settings)
        await _print_results(comp_id)

    asyncio.run(_main())
