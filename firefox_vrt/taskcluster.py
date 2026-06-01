"""Read-only async clients for Mozilla's public APIs.

We talk to three services:
  - Treeherder: resolve revision -> push_id, then list browser-screenshots jobs
  - Taskcluster: list artifacts of a task, download them
  - hg.mozilla.org: look up a revision's parent (for default baseline)

All endpoints used here are public-readable; no auth tokens. We send a
polite `User-Agent` and reuse a single httpx.AsyncClient per Clients
instance for keepalive efficiency.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator, Optional

import httpx

from .pairing import platform_from_job_name


SCREENSHOT_JOB_SYMBOL = "ss"
SCREENSHOT_JOB_GROUP_SYMBOL = "M"

# How much we prefer each Treeherder job result when picking the run to fetch
# for a platform. We deliberately include non-green *completed* runs: a flaky
# mozscreenshots job that ends `testfailed` still uploads the screenshots it
# captured before the failure, and a partial-but-labelled capture beats a
# misleading "nothing found". Higher rank wins; rank 0 results (retried,
# superseded, cancelled, still-running) are skipped — they have no usable final
# artifacts. Ties broken by the latest run.
_RESULT_RANK = {
    "success": 3,
    "testfailed": 2,
    "busted": 1,
    "exception": 1,
}


@dataclass
class ScreenshotTask:
    platform: str  # canonical (e.g. "linux1804-64")
    job_type_name: str
    task_id: str
    run_id: int
    result: str  # treeherder job result string ("success", "testfailed", etc.)


@dataclass
class Artifact:
    name: str  # full Taskcluster artifact name, e.g. "public/test_info/foo.png"
    content_type: str


class ArtifactsExpired(Exception):
    """Raised when an artifact 404s — typically a >28-day-old try push."""


class NoScreenshotsJob(Exception):
    """Raised when a revision has no browser-screenshots jobs at all."""


class Clients:
    """Holds httpx.AsyncClient + endpoint roots. Use as an async context
    manager so the underlying connection pool gets closed cleanly."""

    def __init__(
        self,
        taskcluster_root: str,
        treeherder_root: str,
        hg_root: str,
        user_agent: str,
        timeout: float = 30.0,
        trust_env: bool = True,
    ) -> None:
        self.taskcluster_root = taskcluster_root.rstrip("/")
        self.treeherder_root = treeherder_root.rstrip("/")
        self.hg_root = hg_root.rstrip("/")
        self._client = httpx.AsyncClient(
            headers={"User-Agent": user_agent, "Accept": "application/json"},
            timeout=timeout,
            follow_redirects=True,
            trust_env=trust_env,
        )

    async def __aenter__(self) -> "Clients":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    # ---- Treeherder ----

    async def resolve_push(self, project: str, revision: str) -> Optional[int]:
        """Look up the Treeherder push_id for a (project, revision)."""
        url = f"{self.treeherder_root}/api/project/{project}/push/"
        params = {"count": 2, "full": "true", "revision": revision}
        r = await self._client.get(url, params=params)
        r.raise_for_status()
        data = r.json()
        results = data.get("results", []) if isinstance(data, dict) else []
        if not results:
            return None
        return int(results[0]["id"])

    async def find_screenshots_tasks(
        self, project: str, revision: str
    ) -> list[ScreenshotTask]:
        """Return one ScreenshotTask per platform that ran browser-screenshots
        for this revision. Raises NoScreenshotsJob when nothing is found."""
        push_id = await self.resolve_push(project, revision)
        if push_id is None:
            raise NoScreenshotsJob(
                f"No Treeherder push found for {project} revision {revision}"
            )

        url = f"{self.treeherder_root}/api/project/{project}/jobs/"
        # Notes on params:
        #  - `tier` must explicitly include 3, because browser-screenshots-e10s
        #    is Tier 3 and Treeherder's /jobs/ endpoint excludes Tier 3 by default.
        #  - We deliberately do NOT filter `result=success`. mozscreenshots is
        #    flaky; a `testfailed` run still uploads the screenshots it captured
        #    before dying, so we fetch those (and flag the capture as partial)
        #    rather than report a misleading "no job found". Non-terminal runs
        #    (retried / running) are dropped below via _RESULT_RANK.
        #  - We do NOT filter by job_type_symbol/job_group_symbol here. Earlier
        #    we tried filtering server-side and got 0 results for a push that
        #    visibly had M(ss) jobs — the symbol-filter combination was too
        #    strict in practice. We pull all jobs for the push and filter
        #    client-side by job_type_name containing "browser-screenshots".
        params = {
            "count": 2000,
            "push_id": push_id,
            "tier": "1,2,3",
        }
        r = await self._client.get(url, params=params)
        r.raise_for_status()
        data = r.json()
        # Treeherder /jobs/ has two response shapes depending on params /
        # endpoint version:
        #   - Modern (what we see in practice): {"results": [{...}, {...}]}
        #   - Legacy / compact: {"job_property_names": [...], "results": [[v, v, ...]]}
        # Detect which one we got by peeking at the first row.
        rows = data.get("results", [])
        if rows and isinstance(rows[0], dict):
            jobs = rows
        else:
            names = data.get("job_property_names", [])
            jobs = [dict(zip(names, row)) for row in rows]

        # A push can run several browser-screenshots variants on the *same*
        # platform — notably M(ss) (Fission, the default) and M-nofis(ss)
        # (Fission disabled). They render the same chrome but are distinct
        # Treeherder jobs, so without filtering we'd fetch both and end up with
        # two CaptureTasks per platform. We keep only the canonical M(ss) run,
        # and among that platform's runs pick the best terminal one (success
        # over a flaky testfailed; latest run on a tie).
        chosen: dict[str, ScreenshotTask] = {}
        chosen_key: dict[str, tuple] = {}
        for job in jobs:
            task_id = job.get("task_id")
            job_type_name = job.get("job_type_name") or ""
            # Only browser-screenshots jobs. Other Tier-3 jobs (talos, raptor,
            # etc.) match the broader push query but aren't relevant to us.
            if "browser-screenshots" not in job_type_name:
                continue
            if not task_id:
                continue
            group_symbol = (job.get("job_group_symbol") or "").strip()
            # Drop the no-Fission variant (Treeherder symbol M-nofis(ss)); we
            # only want the default Fission run, M(ss). The marker shows up in
            # the group symbol and, as a fallback for compact API responses,
            # as a "-nofis" suffix on the job name.
            if "nofis" in group_symbol.lower() or "-nofis" in job_type_name.lower():
                continue
            result = str(job.get("result", ""))
            rank = _RESULT_RANK.get(result, 0)
            if rank == 0:
                continue  # retried / superseded / cancelled / still running
            run_id = int(job.get("retry_id", 0) or 0)
            platform = platform_from_job_name(job_type_name) or "unknown"
            # One task per platform: keep the highest (result rank, run) seen.
            key = (rank, run_id)
            if platform in chosen and key <= chosen_key[platform]:
                continue
            chosen[platform] = ScreenshotTask(
                platform=platform,
                job_type_name=job_type_name,
                task_id=str(task_id),
                run_id=run_id,
                result=result,
            )
            chosen_key[platform] = key

        tasks = list(chosen.values())
        if not tasks:
            raise NoScreenshotsJob(
                f"No browser-screenshots tasks on push {push_id} ({project}/{revision})"
            )
        return tasks

    # ---- Taskcluster ----

    async def list_artifacts(self, task_id: str, run_id: int) -> list[Artifact]:
        """List the artifacts published by a task run."""
        url = (
            f"{self.taskcluster_root}/api/queue/v1/task/{task_id}"
            f"/runs/{run_id}/artifacts"
        )
        r = await self._client.get(url)
        if r.status_code == 404:
            raise ArtifactsExpired(
                f"Artifacts for task {task_id} run {run_id} are not available "
                "(possibly expired)."
            )
        r.raise_for_status()
        data = r.json()
        out: list[Artifact] = []
        for item in data.get("artifacts", []):
            out.append(
                Artifact(
                    name=item.get("name", ""),
                    content_type=item.get("contentType", ""),
                )
            )
        return out

    async def get_task_env(self, task_id: str) -> dict:
        """Return the task definition's `payload.env` dict — e.g. containing
        ``MOZSCREENSHOTS_SETS``. Returns an empty dict if the task definition
        is gone (expired) or has no env; callers treat that as 'unknown'.

        Note: task *definitions* expire on the same schedule as artifacts, so
        this is only reliable at fetch time — which is exactly why we persist
        the result on the CaptureTask rather than looking it up lazily later.
        """
        url = f"{self.taskcluster_root}/api/queue/v1/task/{task_id}"
        r = await self._client.get(url)
        if r.status_code == 404:
            return {}
        r.raise_for_status()
        data = r.json()
        env = data.get("payload", {}).get("env", {})
        return env if isinstance(env, dict) else {}

    async def stream_artifact(
        self,
        task_id: str,
        run_id: int,
        name: str,
        chunk_size: int = 64 * 1024,
    ) -> AsyncIterator[bytes]:
        """Yield bytes for a Taskcluster artifact."""
        url = (
            f"{self.taskcluster_root}/api/queue/v1/task/{task_id}"
            f"/runs/{run_id}/artifacts/{name}"
        )
        async with self._client.stream("GET", url) as r:
            if r.status_code == 404:
                raise ArtifactsExpired(
                    f"Artifact {name} for task {task_id} run {run_id} not found "
                    "(possibly expired)."
                )
            r.raise_for_status()
            async for chunk in r.aiter_bytes(chunk_size):
                yield chunk

    # ---- hg.mozilla.org ----

    async def parent_revision(
        self, project: str, revision: str
    ) -> Optional[str]:
        """Return the first parent's node from hg's JSON revision endpoint.
        Used to suggest a default baseline for a try / autoland push."""
        url = f"{self.hg_root}/{project}/json-rev/{revision}"
        r = await self._client.get(url)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        data = r.json()
        parents = data.get("parents", []) if isinstance(data, dict) else []
        if not parents:
            return None
        first = parents[0]
        # Parents come back as either string nodes or dicts with "node".
        if isinstance(first, dict):
            return first.get("node")
        return str(first)


__all__ = [
    "Artifact",
    "ArtifactsExpired",
    "Clients",
    "NoScreenshotsJob",
    "ScreenshotTask",
]
