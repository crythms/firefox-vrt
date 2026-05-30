"""Application configuration via environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _abs(path: str) -> Path:
    p = Path(path).expanduser()
    return p if p.is_absolute() else (Path.cwd() / p).resolve()


@dataclass
class Settings:
    data_dir: Path
    database_url: str
    taskcluster_root: str
    treeherder_root: str
    hg_root: str
    user_agent: str
    # Concurrency limit for parallel artifact downloads per task.
    download_concurrency: int

    @property
    def captures_dir(self) -> Path:
        return self.data_dir / "captures"


def load() -> Settings:
    data_dir = _abs(os.environ.get("DATA_DIR", "./data"))
    database_url = os.environ.get(
        "DATABASE_URL", f"sqlite+aiosqlite:///{data_dir}/firefox-vrt.db"
    )
    return Settings(
        data_dir=data_dir,
        database_url=database_url,
        taskcluster_root=os.environ.get(
            "TASKCLUSTER_ROOT", "https://firefox-ci-tc.services.mozilla.com"
        ),
        treeherder_root=os.environ.get(
            "TREEHERDER_ROOT", "https://treeherder.mozilla.org"
        ),
        hg_root=os.environ.get("HG_ROOT", "https://hg.mozilla.org"),
        user_agent=os.environ.get(
            "USER_AGENT",
            "firefox-vrt/0.1 (https://github.com/mozilla/firefox-vrt)",
        ),
        download_concurrency=int(os.environ.get("DOWNLOAD_CONCURRENCY", "5")),
    )


__all__ = ["Settings", "load"]
