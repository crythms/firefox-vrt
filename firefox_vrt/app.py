"""FastAPI application factory.

We use a factory (not module-level `app = FastAPI()`) so tests can spin up
an isolated instance with a tmp data dir and DB URL via env vars.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import config, db
from .routes import captures, comparisons, index


PACKAGE_DIR = Path(__file__).parent


def create_app() -> FastAPI:
    settings = config.load()
    db.configure(settings.database_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        settings.captures_dir.mkdir(parents=True, exist_ok=True)
        await db.create_all()
        yield

    app = FastAPI(title="Firefox VRT", lifespan=lifespan)
    app.state.settings = settings

    templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")
    app.state.templates = templates

    app.mount(
        "/static",
        StaticFiles(directory=PACKAGE_DIR / "static"),
        name="static",
    )
    # Serve captured / diff PNGs straight off disk.
    app.mount(
        "/data",
        StaticFiles(directory=settings.data_dir),
        name="data",
    )

    app.include_router(index.router)
    app.include_router(captures.router)
    app.include_router(comparisons.router)

    return app


app = create_app()
