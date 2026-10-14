"""FinancialAuthority: the FastAPI app, the MCP endpoint, the scheduler and the built frontend, in one process."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import auth, oauth
from .api import llm, mail, registers, routes
from .config import settings
from .db import init_db, session_scope
from .engine.household import seed
from .jobs.scheduler import jobs
from .mcp_server import asgi_routes, mcp_server
from .version import __version__

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("fa")

FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.ensure_dirs()
    init_db()
    if settings.demo:
        with session_scope() as session:
            result = seed(session)
            log.info("demo mode: %s", result)
        if result.get("seeded"):
            from .jobs.scheduler import detect_recurring

            log.info("demo detection: %s", detect_recurring())
    log.info("data dir: %s · imports: %s · tz: %s", settings.data_dir, settings.import_root, settings.tz)
    if not auth.auth_enabled():
        log.warning("sign-in is OFF because FA_PASSWORD is empty; never run like this where anyone else can reach it")
    if not settings.data_key:
        log.warning("FA_DATA_KEY is empty; connector credentials cannot be stored until it is set")
    if settings.jobs:
        jobs.start()
    async with mcp_server.session_manager.run():
        yield
    jobs.stop()


_docs = {} if not auth.auth_enabled() else {"docs_url": None, "redoc_url": None, "openapi_url": None}
app = FastAPI(title="FinancialAuthority", version=__version__, lifespan=lifespan, **_docs)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.include_router(auth.router)
app.include_router(routes.router)
app.include_router(registers.router)
app.include_router(mail.router)
app.include_router(oauth.router)
app.include_router(llm.router)
for route in asgi_routes():
    app.router.routes.append(route)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "version": __version__, "demo": settings.demo}


@app.get("/api/version")
def version() -> dict:
    return {"version": __version__}


class SPAStaticFiles(StaticFiles):
    """Serves index.html for unknown paths so client-side routes survive a refresh."""

    async def get_response(self, path: str, scope):  # noqa: ANN001
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as error:
            if error.status_code == 404 and not path.startswith("api/"):
                return await super().get_response("index.html", scope)
            raise


if FRONTEND_DIST.exists():
    app.mount("/", SPAStaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")
else:
    log.warning("no built frontend at %s; only the API is served", FRONTEND_DIST)
