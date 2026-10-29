"""FinancialAuthority: the FastAPI app, the MCP endpoint, the scheduler and the built frontend, in one process."""

from __future__ import annotations

import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import auth, crypto, factors, oauth
from .security import SecurityMiddleware
from .api import apphealth, appearance, inputs, business, datacheck, approvals, banks, borrowing, credit, custom_reports, exports, goals, guide, homeassistant, housing, imports, moving, needsyou, people, moves, investments, ledger_rules, llm, mail, payoff, planned, plaid_webhook, privacy, projections, push, recap, registers, restore, routes, scenarios, sinking, tools, watch
from .config import settings
from .db import init_db, session_scope
from .engine.household import demo_rows, seed
from .jobs.scheduler import jobs
from .mcp_server import asgi_routes, mcp_server
from .version import __version__

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("fa")


class _HideFeedToken(logging.Filter):
    """The calendar feed's address is its secret: the access log shows the route, never the token."""

    PATTERN = re.compile(r"/api/calendar/[^/?\s\"]+\.ics")

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(self.PATTERN.sub("/api/calendar/<hidden>.ics", a) if isinstance(a, str) else a for a in record.args)
        elif isinstance(record.msg, str):
            record.msg = self.PATTERN.sub("/api/calendar/<hidden>.ics", record.msg)
        return True


logging.getLogger("uvicorn.access").addFilter(_HideFeedToken())
# Two libraries log more than the operator should see: the SSE writer logs every chunk it sends at DEBUG (a tool's
# whole result, rows and all), and the MCP transport logs each session id at INFO. Neither line carries anything the
# log needs, and a raised log level must never put a row in it.
logging.getLogger("sse_starlette").setLevel(logging.INFO)
logging.getLogger("mcp.server.streamable_http_manager").setLevel(logging.WARNING)

FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


def _owner_only(db: Path) -> None:
    """The database and its journal readable by the app's own user alone. SQLite makes the journal files with the
    database file's mode, so the file's mode carries to them; a mount that refuses the change is left as it is."""
    for path in (db, db.with_name(db.name + "-wal"), db.with_name(db.name + "-shm")):
        try:
            if path.exists():
                path.chmod(0o600)
        except OSError:
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.ensure_dirs()
    problem = auth.open_install_problem()
    if problem:
        log.error(problem)
        raise RuntimeError(problem)  # an installed copy never runs open; the log says what to set
    init_db()
    problem = crypto.data_key_problem()
    if problem:
        log.error(problem)
        raise RuntimeError(problem)  # a changed data key is said once, here, not found feature by feature
    _owner_only(settings.db_path)
    if settings.demo:
        with session_scope() as session:
            result = seed(session)
            log.info("demo mode: %s", result)
        if result.get("seeded"):
            from .jobs.scheduler import detect_recurring

            log.info("demo detection: %s", detect_recurring())
    else:
        with session_scope() as session:
            if demo_rows(session):
                log.error("demo mode is off, but this database holds the example household's rows; they are counted as real. Put back a restore point from before they appeared.")
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


def cors_origins(raw: list[str]) -> list[str]:
    """The other origins allowed to call the API with the owner's cookie: named ones only. A wildcard is dropped,
    because with credentials Starlette answers it with whatever origin asked, which is every site on the web
    reading the API as the owner."""
    named = [o.strip() for o in raw if o.strip()]
    kept = [o for o in named if o != "*"]
    if len(kept) != len(named):
        log.warning("FA_CORS_ORIGINS holds a wildcard; it is ignored, since the API answers with the owner's cookie. Name each origin instead.")
    return kept


_docs = {} if not auth.auth_enabled() else {"docs_url": None, "redoc_url": None, "openapi_url": None}
app = FastAPI(title="FinancialAuthority", version=__version__, lifespan=lifespan, dependencies=[Depends(inputs.bounded_params)], **_docs)
app.add_exception_handler(RequestValidationError, inputs.validation_error)
_cors = cors_origins(settings.cors_origins)
if _cors:
    app.add_middleware(CORSMiddleware, allow_origins=_cors, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
from starlette.middleware.gzip import GZipMiddleware  # noqa: E402

app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(SecurityMiddleware)
app.include_router(auth.router)
app.include_router(factors.router)
app.include_router(privacy.router)
app.include_router(plaid_webhook.router)
for module in (custom_reports, sinking, payoff, recap, watch, homeassistant, housing, people, moving, needsyou, credit, apphealth, datacheck, business):
    app.include_router(module.router)
app.include_router(moving.exit_router)
app.include_router(business.tax_router)
app.include_router(datacheck.learn_router)
app.include_router(routes.router)
app.include_router(registers.router)
app.include_router(mail.router)
app.include_router(oauth.router)
app.include_router(llm.router)
app.include_router(banks.router)
app.include_router(push.router)
app.include_router(approvals.router)
app.include_router(tools.router)
app.include_router(goals.router)
app.include_router(ledger_rules.router)
app.include_router(borrowing.router)
app.include_router(planned.router)
app.include_router(scenarios.router)
app.include_router(guide.router)
app.include_router(appearance.router)
app.include_router(exports.router)
app.include_router(moves.router)
app.include_router(restore.router)
app.include_router(investments.router)
app.include_router(imports.router)
app.include_router(projections.router)
for route in asgi_routes():
    app.router.routes.append(route)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "demo": settings.demo}


@app.get("/api/version")
def version(_: auth.Principal = Depends(auth.require_read)) -> dict:
    return {"version": __version__}


class SPAStaticFiles(StaticFiles):
    """Serves index.html for unknown paths so client-side routes survive a refresh."""

    async def get_response(self, path: str, scope):  # noqa: ANN001
        try:
            response = await super().get_response(path, scope)
            if path.replace("\\", "/").lstrip("/").startswith("assets/") and response.status_code == 200:
                response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            return response
        except StarletteHTTPException as error:
            # A missing built file is a plain 404, never the page: an old tab asking for a renamed piece after an update
            # must fail cleanly so it can reload, not get HTML where a script belongs.
            if error.status_code == 404 and not path.replace("\\", "/").lstrip("/").startswith(("api/", "mcp", "oauth/", "assets/")):
                return await super().get_response("index.html", scope)
            raise


if FRONTEND_DIST.exists():
    app.mount("/", SPAStaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")
else:
    log.warning("no built frontend at %s; only the API is served", FRONTEND_DIST)
