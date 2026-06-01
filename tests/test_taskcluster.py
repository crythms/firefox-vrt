from __future__ import annotations

import json

import httpx
import pytest
import respx

from firefox_vrt.taskcluster import (
    ArtifactsExpired,
    Clients,
    NoScreenshotsJob,
)


TC = "https://firefox-ci-tc.services.mozilla.com"
TH = "https://treeherder.mozilla.org"
HG = "https://hg.mozilla.org"


@pytest.fixture
def clients() -> Clients:
    return Clients(
        taskcluster_root=TC,
        treeherder_root=TH,
        hg_root=HG,
        user_agent="firefox-vrt-test/0",
        trust_env=False,  # ignore proxy env vars during tests
    )


@pytest.mark.asyncio
async def test_resolve_push_returns_id(clients: Clients) -> None:
    with respx.mock(base_url=TH) as router:
        router.get("/api/project/try/push/").mock(
            return_value=httpx.Response(
                200, json={"results": [{"id": 12345, "revision": "abc"}]}
            )
        )
        push_id = await clients.resolve_push("try", "abc")
    assert push_id == 12345
    await clients.close()


@pytest.mark.asyncio
async def test_resolve_push_returns_none_when_empty(clients: Clients) -> None:
    with respx.mock(base_url=TH) as router:
        router.get("/api/project/try/push/").mock(
            return_value=httpx.Response(200, json={"results": []})
        )
        push_id = await clients.resolve_push("try", "abc")
    assert push_id is None
    await clients.close()


@pytest.mark.asyncio
async def test_find_screenshots_tasks_parses_treeherder_dict_rows(clients: Clients) -> None:
    """Modern response shape: results is a list of dicts."""
    with respx.mock() as router:
        router.get(f"{TH}/api/project/autoland/push/").mock(
            return_value=httpx.Response(200, json={"results": [{"id": 999}]})
        )
        router.get(f"{TH}/api/project/autoland/jobs/").mock(
            return_value=httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "task_id": "K_oZRB_FS9OwcIITPIiT5g",
                            "job_type_name": "test-linux2404-64-shippable/opt-mochitest-browser-screenshots",
                            "retry_id": 0,
                            "result": "success",
                        },
                        {
                            "task_id": "TASK_W1",
                            "job_type_name": "test-windows10-64/opt-mochitest-browser-screenshots",
                            "retry_id": 0,
                            "result": "success",
                        },
                        {
                            # A non-screenshots job that should be filtered out.
                            "task_id": "TASK_OTHER",
                            "job_type_name": "test-linux1804-64/opt-mochitest-plain",
                            "retry_id": 0,
                            "result": "success",
                        },
                    ],
                },
            )
        )
        tasks = await clients.find_screenshots_tasks("autoland", "deadbeef")
    assert len(tasks) == 2  # browser-screenshots only, plain mochitest filtered
    assert {t.platform for t in tasks} == {"linux2404-64", "windows10-64"}
    assert tasks[0].task_id == "K_oZRB_FS9OwcIITPIiT5g"
    assert tasks[0].run_id == 0
    await clients.close()


@pytest.mark.asyncio
async def test_find_screenshots_tasks_drops_nofis_variant(clients: Clients) -> None:
    """A push that ran both M(ss) and M-nofis(ss) on the same platform should
    yield only the canonical M(ss) task — not two tasks for one platform."""
    with respx.mock() as router:
        router.get(f"{TH}/api/project/try/push/").mock(
            return_value=httpx.Response(200, json={"results": [{"id": 1932925}]})
        )
        router.get(f"{TH}/api/project/try/jobs/").mock(
            return_value=httpx.Response(
                200,
                json={
                    "results": [
                        {
                            # M-nofis(ss) — Fission disabled; should be dropped.
                            "task_id": "QFMqJ36URNqYdA1ahKUzLw",
                            "job_type_name": "test-linux2404-64/opt-mochitest-browser-screenshots-nofis",
                            "job_group_symbol": "M-nofis",
                            "retry_id": 0,
                            "result": "success",
                        },
                        {
                            # M(ss) — the canonical Fission run; should be kept.
                            "task_id": "XtoSiB-4Tzi98XSvl5Bqjw",
                            "job_type_name": "test-linux2404-64/opt-mochitest-browser-screenshots",
                            "job_group_symbol": "M",
                            "retry_id": 0,
                            "result": "success",
                        },
                    ],
                },
            )
        )
        tasks = await clients.find_screenshots_tasks("try", "deadbeef")
    assert len(tasks) == 1
    assert tasks[0].platform == "linux2404-64"
    assert tasks[0].task_id == "XtoSiB-4Tzi98XSvl5Bqjw"
    await clients.close()


@pytest.mark.asyncio
async def test_find_screenshots_tasks_parses_legacy_compact_format(clients: Clients) -> None:
    """Legacy compact response: job_property_names + list-of-lists."""
    with respx.mock() as router:
        router.get(f"{TH}/api/project/autoland/push/").mock(
            return_value=httpx.Response(200, json={"results": [{"id": 999}]})
        )
        router.get(f"{TH}/api/project/autoland/jobs/").mock(
            return_value=httpx.Response(
                200,
                json={
                    "job_property_names": [
                        "task_id",
                        "job_type_name",
                        "retry_id",
                        "result",
                    ],
                    "results": [
                        ["TASK_L1", "test-linux1804-64/opt-browser-screenshots-e10s", 0, "success"],
                    ],
                },
            )
        )
        tasks = await clients.find_screenshots_tasks("autoland", "deadbeef")
    assert len(tasks) == 1
    assert tasks[0].task_id == "TASK_L1"
    await clients.close()


@pytest.mark.asyncio
async def test_find_screenshots_tasks_includes_nongreen(clients: Clients) -> None:
    """A flaky `testfailed` screenshots job is still fetched (for its partial
    artifacts), with the result recorded so the UI can flag it."""
    with respx.mock(base_url=TH) as router:
        router.get("/api/project/try/push/").mock(
            return_value=httpx.Response(200, json={"results": [{"id": 1}]})
        )
        router.get("/api/project/try/jobs/").mock(
            return_value=httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "task_id": "FAILED_TASK",
                            "job_type_name": "test-linux2404-64/opt-mochitest-browser-screenshots",
                            "job_group_symbol": "M",
                            "retry_id": 0,
                            "result": "testfailed",
                        },
                    ]
                },
            )
        )
        tasks = await clients.find_screenshots_tasks("try", "deadbeef")
    assert len(tasks) == 1
    assert tasks[0].task_id == "FAILED_TASK"
    assert tasks[0].result == "testfailed"
    await clients.close()


@pytest.mark.asyncio
async def test_find_screenshots_tasks_prefers_success_and_skips_retry(
    clients: Clients,
) -> None:
    """Among runs of one platform's job: skip the retried run, and prefer the
    successful run over a failed one."""
    job = "test-linux2404-64/opt-mochitest-browser-screenshots"
    with respx.mock(base_url=TH) as router:
        router.get("/api/project/try/push/").mock(
            return_value=httpx.Response(200, json={"results": [{"id": 1}]})
        )
        router.get("/api/project/try/jobs/").mock(
            return_value=httpx.Response(
                200,
                json={
                    "results": [
                        {"task_id": "RETRIED", "job_type_name": job, "job_group_symbol": "M", "retry_id": 0, "result": "retry"},
                        {"task_id": "FAILED", "job_type_name": job, "job_group_symbol": "M", "retry_id": 1, "result": "testfailed"},
                        {"task_id": "GREEN", "job_type_name": job, "job_group_symbol": "M", "retry_id": 2, "result": "success"},
                    ]
                },
            )
        )
        tasks = await clients.find_screenshots_tasks("try", "deadbeef")
    assert len(tasks) == 1
    assert tasks[0].task_id == "GREEN"
    assert tasks[0].result == "success"
    await clients.close()


@pytest.mark.asyncio
async def test_get_task_env_returns_env(clients: Clients) -> None:
    with respx.mock(base_url=TC) as router:
        router.get("/api/queue/v1/task/TASK_X").mock(
            return_value=httpx.Response(
                200,
                json={"payload": {"env": {"MOZSCREENSHOTS_SETS": "Toolbars,Tabs"}}},
            )
        )
        env = await clients.get_task_env("TASK_X")
    assert env == {"MOZSCREENSHOTS_SETS": "Toolbars,Tabs"}
    await clients.close()


@pytest.mark.asyncio
async def test_get_task_env_empty_when_expired(clients: Clients) -> None:
    """An expired/missing task definition (404) yields an empty dict, not an
    error — the caller treats it as 'sets unknown'."""
    with respx.mock(base_url=TC) as router:
        router.get("/api/queue/v1/task/GONE").mock(
            return_value=httpx.Response(404, json={"code": "ResourceNotFound"})
        )
        env = await clients.get_task_env("GONE")
    assert env == {}
    await clients.close()


@pytest.mark.asyncio
async def test_find_screenshots_tasks_raises_when_no_push(clients: Clients) -> None:
    with respx.mock(base_url=TH) as router:
        router.get("/api/project/try/push/").mock(
            return_value=httpx.Response(200, json={"results": []})
        )
        with pytest.raises(NoScreenshotsJob):
            await clients.find_screenshots_tasks("try", "abc")
    await clients.close()


@pytest.mark.asyncio
async def test_find_screenshots_tasks_raises_when_no_jobs(clients: Clients) -> None:
    with respx.mock() as router:
        router.get(f"{TH}/api/project/try/push/").mock(
            return_value=httpx.Response(200, json={"results": [{"id": 1}]})
        )
        router.get(f"{TH}/api/project/try/jobs/").mock(
            return_value=httpx.Response(
                200,
                json={"job_property_names": [], "results": []},
            )
        )
        with pytest.raises(NoScreenshotsJob):
            await clients.find_screenshots_tasks("try", "abc")
    await clients.close()


@pytest.mark.asyncio
async def test_list_artifacts(clients: Clients) -> None:
    with respx.mock(base_url=TC) as router:
        router.get("/api/queue/v1/task/T/runs/0/artifacts").mock(
            return_value=httpx.Response(
                200,
                json={
                    "artifacts": [
                        {"name": "public/test_info/foo.png", "contentType": "image/png"},
                        {"name": "public/logs/log.txt", "contentType": "text/plain"},
                    ]
                },
            )
        )
        arts = await clients.list_artifacts("T", 0)
    assert len(arts) == 2
    pngs = [a for a in arts if a.content_type == "image/png"]
    assert pngs[0].name == "public/test_info/foo.png"
    await clients.close()


@pytest.mark.asyncio
async def test_list_artifacts_404_raises_expired(clients: Clients) -> None:
    with respx.mock(base_url=TC) as router:
        router.get("/api/queue/v1/task/T/runs/0/artifacts").mock(
            return_value=httpx.Response(404, json={"message": "no such task"})
        )
        with pytest.raises(ArtifactsExpired):
            await clients.list_artifacts("T", 0)
    await clients.close()


@pytest.mark.asyncio
async def test_stream_artifact_yields_chunks(clients: Clients) -> None:
    payload = b"PNG-CONTENT-HERE" * 10
    with respx.mock(base_url=TC) as router:
        router.get("/api/queue/v1/task/T/runs/0/artifacts/public/x.png").mock(
            return_value=httpx.Response(200, content=payload)
        )
        chunks = []
        async for chunk in clients.stream_artifact("T", 0, "public/x.png"):
            chunks.append(chunk)
    assert b"".join(chunks) == payload
    await clients.close()


@pytest.mark.asyncio
async def test_parent_revision_dict_form(clients: Clients) -> None:
    with respx.mock(base_url=HG) as router:
        router.get("/mozilla-central/json-rev/abc").mock(
            return_value=httpx.Response(
                200, json={"parents": [{"node": "parent-sha"}]}
            )
        )
        parent = await clients.parent_revision("mozilla-central", "abc")
    assert parent == "parent-sha"
    await clients.close()


@pytest.mark.asyncio
async def test_parent_revision_string_form(clients: Clients) -> None:
    with respx.mock(base_url=HG) as router:
        router.get("/try/json-rev/abc").mock(
            return_value=httpx.Response(200, json={"parents": ["parent-sha"]})
        )
        parent = await clients.parent_revision("try", "abc")
    assert parent == "parent-sha"
    await clients.close()


@pytest.mark.asyncio
async def test_parent_revision_404_returns_none(clients: Clients) -> None:
    with respx.mock(base_url=HG) as router:
        router.get("/try/json-rev/missing").mock(
            return_value=httpx.Response(404)
        )
        parent = await clients.parent_revision("try", "missing")
    assert parent is None
    await clients.close()
