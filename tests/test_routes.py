"""End-to-end tests against the FastAPI app with mocked external HTTP."""

from __future__ import annotations

import asyncio
import importlib
import os
from pathlib import Path

import httpx
import numpy as np
import pytest
import respx
from PIL import Image


TC = "https://firefox-ci-tc.services.mozilla.com"
TH = "https://treeherder.mozilla.org"
HG = "https://hg.mozilla.org"


@pytest.fixture
async def isolated_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Build a fresh app with tmp data dir + DB.

    Modules cache the engine in `db._engine`. We unset that between tests so
    each test gets its own SQLite file. We also force trust_env=False on the
    fetcher's Clients via env so respx's mocks aren't bypassed by SOCKS env vars.

    httpx's ASGITransport doesn't run FastAPI lifespan events, so we explicitly
    initialize the DB schema and data dirs here.
    """
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/db.sqlite")
    (tmp_path / "captures").mkdir(parents=True, exist_ok=True)

    import firefox_vrt.db as db
    import firefox_vrt.app as app_mod
    db._engine = None
    db._session_factory = None
    importlib.reload(app_mod)
    # Manually run what lifespan would have done.
    await db.create_all()

    # Patch fetcher's default Clients factory to disable trust_env so respx
    # mocks are honored even when system env exports a SOCKS proxy.
    from firefox_vrt import fetcher
    from firefox_vrt.taskcluster import Clients as _Clients

    def patched_default_factory(settings):
        return _Clients(
            taskcluster_root=settings.taskcluster_root,
            treeherder_root=settings.treeherder_root,
            hg_root=settings.hg_root,
            user_agent=settings.user_agent,
            trust_env=False,
        )

    real_fetch = fetcher.fetch_capture

    async def patched_fetch(capture_id, settings, clients_factory=None):
        if clients_factory is None:
            clients_factory = lambda: patched_default_factory(settings)
        await real_fetch(capture_id, settings, clients_factory)

    monkeypatch.setattr(fetcher, "fetch_capture", patched_fetch)

    return app_mod.create_app()


async def _wait_for(client: httpx.AsyncClient, url: str, status_field_keyword: str, timeout: float = 5.0):
    """Poll a JSON-less HTML endpoint by GETting it and looking for a keyword
    in the body. Used to wait out background tasks in tests."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        r = await client.get(url)
        if status_field_keyword in r.text:
            return r
        await asyncio.sleep(0.05)
    raise TimeoutError(f"timed out waiting for '{status_field_keyword}' at {url}")


def _png_bytes(color, size=(40, 30)) -> bytes:
    arr = np.full((size[1], size[0], 3), color, dtype=np.uint8)
    import io
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG")
    return buf.getvalue()


@pytest.mark.asyncio
async def test_landing_page_renders(isolated_app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=isolated_app), base_url="http://test"
    ) as client:
        r = await client.get("/")
        assert r.status_code == 200
        assert "Firefox VRT" in r.text
        assert "mochitest-browser-screenshots" in r.text


@pytest.mark.asyncio
async def test_capture_form_rejects_bad_revision(isolated_app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=isolated_app), base_url="http://test"
    ) as client:
        r = await client.post(
            "/capture", data={"revision": "not-hex", "project": "try"}
        )
        assert r.status_code == 400
        assert "12&#8211;40" in r.text or "12" in r.text


@pytest.mark.asyncio
async def test_capture_form_rejects_unknown_project(isolated_app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=isolated_app), base_url="http://test"
    ) as client:
        r = await client.post(
            "/capture",
            data={"revision": "abcdef1234567890", "project": "bogus"},
        )
        assert r.status_code == 400


@pytest.mark.asyncio
async def test_happy_path_capture_to_comparison(isolated_app):
    """Resolve revision → fetch artifacts → compare → triage. All external
    HTTP mocked."""
    base_png = _png_bytes((100, 100, 100))
    candidate_png = _png_bytes((200, 100, 100))  # solidly different

    with respx.mock(assert_all_called=False) as router:
        # Treeherder push lookup (called twice: once in find_screenshots_tasks
        # and again after, so we use a permanent route).
        router.get(f"{TH}/api/project/try/push/").mock(
            return_value=httpx.Response(200, json={"results": [{"id": 42}]})
        )
        router.get(f"{TH}/api/project/try/jobs/").mock(
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
                        [
                            "TASK_L1",
                            "test-linux1804-64/opt-browser-screenshots-e10s",
                            0,
                            "success",
                        ],
                    ],
                },
            )
        )
        # And for the baseline revision...
        router.get(f"{TH}/api/project/mozilla-central/push/").mock(
            return_value=httpx.Response(200, json={"results": [{"id": 41}]})
        )
        router.get(f"{TH}/api/project/mozilla-central/jobs/").mock(
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
                        [
                            "TASK_L0",
                            "test-linux1804-64/opt-browser-screenshots-e10s",
                            0,
                            "success",
                        ],
                    ],
                },
            )
        )
        # Artifact listings — one PNG per task.
        for tid in ("TASK_L0", "TASK_L1"):
            router.get(
                f"{TC}/api/queue/v1/task/{tid}/runs/0/artifacts"
            ).mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "artifacts": [
                            {
                                "name": "public/test_info/primaryUI_01_tabs.png",
                                "contentType": "image/png",
                            },
                        ]
                    },
                )
            )
        # Artifact downloads — baseline grey, candidate red.
        router.get(
            f"{TC}/api/queue/v1/task/TASK_L0/runs/0/artifacts/public/test_info/primaryUI_01_tabs.png"
        ).mock(return_value=httpx.Response(200, content=base_png))
        router.get(
            f"{TC}/api/queue/v1/task/TASK_L1/runs/0/artifacts/public/test_info/primaryUI_01_tabs.png"
        ).mock(return_value=httpx.Response(200, content=candidate_png))

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=isolated_app),
            base_url="http://test",
        ) as client:
            # 1. Kick off candidate capture (the "try" push).
            r = await client.post(
                "/capture",
                data={"revision": "deadbeef1234", "project": "try"},
                follow_redirects=True,
            )
            assert r.status_code == 200
            # Pull the candidate capture id out of the redirected URL.
            candidate_url = str(r.url)
            assert "/capture/" in candidate_url
            candidate_id = int(candidate_url.rstrip("/").rsplit("/", 1)[-1])

            # Wait until capture is ready.
            await _wait_for(client, f"/capture/{candidate_id}", "badge-ready")

            # 2. Kick off baseline capture (mozilla-central).
            r2 = await client.post(
                "/capture",
                data={"revision": "cafebabe1234", "project": "mozilla-central"},
                follow_redirects=True,
            )
            assert r2.status_code == 200
            baseline_id = int(str(r2.url).rstrip("/").rsplit("/", 1)[-1])
            await _wait_for(client, f"/capture/{baseline_id}", "badge-ready")

            # 3. Run comparison.
            r3 = await client.post(
                f"/capture/{candidate_id}/compare",
                data={"base_capture_id": str(baseline_id)},
                follow_redirects=True,
            )
            assert r3.status_code == 200
            comparison_url = str(r3.url)
            comparison_id = int(comparison_url.rstrip("/").rsplit("/", 1)[-1])

            await _wait_for(
                client, f"/comparison/{comparison_id}", "badge-ready"
            )

            # 4. Confirm a result row is present and reports the diff.
            comp_page = await client.get(f"/comparison/{comparison_id}")
            assert comp_page.status_code == 200
            assert "primaryUI_01_tabs" in comp_page.text
            assert "linux1804-64" in comp_page.text
            # Solid grey vs solid red — must be flagged differs.
            assert 'data-status="differs"' in comp_page.text

            # Triage UI removed per user request — formerly tested radio
            # button POSTs here. The endpoint still exists but isn't surfaced
            # in the UI; no value in keeping it under test coverage.


@pytest.mark.asyncio
async def test_capture_404_for_missing_id(isolated_app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=isolated_app), base_url="http://test"
    ) as client:
        r = await client.get("/capture/99999")
        assert r.status_code == 404
