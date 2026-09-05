from contextlib import asynccontextmanager
from contextlib import suppress
import asyncio
import hashlib
import hmac
import logging
import os
from pathlib import Path
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from fastapi import FastAPI, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import migrate_legacy_runtime_data, settings
from .db import AsyncSessionLocal, check_database_integrity, init_db, run_migrations, shutdown_db
from .models import Account
from .errors import resolve_error
from . import secrets_store
from .tg_manager import manager
from .auth import router as auth_router, require_auth
from .routers import accounts, profile, security, groups, messaging, settings as settings_router, bulk
from .logging_config import configure_logging

configure_logging()
log = logging.getLogger("main")


async def _run_status_tick() -> bool:
    """Run one background health tick without leaking raw exception details.

    The status loop is a resilience boundary: one transient or unexpected error
    must not kill future reconnect/auth checks. Return False when a tick failed
    so tests/diagnostics can observe the boundary without exposing exception text.
    """
    try:
        await manager.refresh_status_all()
        await manager.verify_authorizations_all()
        return True
    except Exception as exc:
        log.warning("status refresh failed error_type=%s", type(exc).__name__)
        return False


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Uvicorn's dictConfig runs after the module-level configure_logging()
    # at import time, so its "uvicorn.error" logger (which emits
    # "Application startup complete" / "Uvicorn running ...") loses our
    # file handler. Re-bridge here with force=True so both console and
    # data/logs/app.log receive every startup line.
    configure_logging(force=True)
    migrate_legacy_runtime_data()
    # Never expose a backend whose login endpoint is guaranteed to fail.
    settings.validate_security_config()
    try:
        probe = secrets_store._dpapi_encrypt(b"multi-tg-manager-preflight")
        secret_ok = secrets_store._dpapi_decrypt(probe) == b"multi-tg-manager-preflight"
        if secret_ok:
            secret_ok, secret_detail = secrets_store.validate_existing_store()
        else:
            secret_detail = "DPAPI round-trip failed"
    except (OSError, RuntimeError, ValueError) as exc:
        secret_ok = False
        secret_detail = type(exc).__name__
        log.critical("Windows DPAPI preflight failed error=%s", type(exc).__name__)
    app.state.secret_store_status = "ok" if secret_ok else "error"
    app.state.secret_store_detail = secret_detail
    # validate critical env
    if not settings.APP_PASSWORD:
        log.warning("APP_PASSWORD is empty — set it in backend/.env!")
    if not settings.SESSION_SECRET or len(settings.SESSION_SECRET) < 48:
        log.warning("SESSION_SECRET is missing or too short — set it in backend/.env!")
    if not settings.api_configured:
        log.warning(
            "TG_API_ID / TG_API_HASH are missing or invalid — get them from "
            "https://my.telegram.org and fill backend/.env. Logging in / importing "
            "sessions will not work until then."
        )
    integrity_ok, integrity_detail = await check_database_integrity()
    app.state.database_status = "ok" if integrity_ok else "error"
    app.state.database_detail = integrity_detail
    if not integrity_ok:
        log.critical("Database integrity problem detected: %s", integrity_detail)
        try:
            yield
        finally:
            await shutdown_db()
        return

    if not secret_ok:
        log.critical("Telegram clients were not started because secure storage is unavailable")
        try:
            yield
        finally:
            await shutdown_db()
        return

    await init_db()
    await run_migrations()
    # alembic/env.py calls logging.config.fileConfig which replaces the root
    # handlers (even with disable_existing_loggers=False the root's handlers
    # are set to the [handler_console] from alembic.ini). Re-bridge our
    # RotatingFileHandler to root + uvicorn loggers so "backend startup
    # complete" and the later "Application startup complete" / "Uvicorn
    # running ..." messages both persist to data/logs/app.log.
    configure_logging(force=True)
    manager.set_loop(asyncio.get_event_loop())
    await secrets_store.migrate_legacy()
    await manager.startup_load_all()

    async def status_loop():
        poll = max(0.5, float(getattr(settings, "STATUS_POLL_SECS", 5.0)))
        while True:
            await _run_status_tick()
            await asyncio.sleep(poll)
    task = asyncio.create_task(status_loop())
    log.info("backend startup complete host=127.0.0.1 database=ok")
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        try:
            await manager.shutdown()
        finally:
            await shutdown_db()
            log.info("backend graceful shutdown complete")


app = FastAPI(title="Multi TG Manager", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.ALLOWED_ORIGIN, "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/api/app/shutdown")
async def request_shutdown(request: Request):
    """Request graceful shutdown from the local STOP.bat launcher.

    Loopback alone is insufficient: local/browser-originated traffic could
    otherwise stop the service. STOP.bat sends an HMAC bound to the current
    server PID, derived from SESSION_SECRET without persisting another secret.
    """
    host = request.client.host if request.client else ""
    if host not in ("127.0.0.1", "::1", "localhost"):
        log.warning("shutdown request refused from non-loopback client host=%s", host)
        return JSONResponse({"detail": "Forbidden"}, status_code=403)
    supplied = request.headers.get("x-mtm-shutdown-token", "")
    expected = hmac.new(
        settings.SESSION_SECRET.encode("utf-8"),
        str(os.getpid()).encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    if not supplied or not hmac.compare_digest(supplied, expected):
        log.warning("shutdown request refused: invalid launcher proof")
        return JSONResponse({"detail": "Forbidden"}, status_code=403)
    asyncio.create_task(_trigger_graceful_shutdown())
    app.state.shutdown_notified = True
    return {"ok": True, "graceful": True}


async def _trigger_graceful_shutdown():
    """Give the response time to flush, then ask the owned Uvicorn Server to exit.

    The canonical runner registers an in-process callback on app.state. This
    avoids CTRL_BREAK/SIGTERM signals escaping to a parent console or process
    group while still letting Uvicorn execute the normal lifespan shutdown.
    """
    await asyncio.sleep(0.5)
    callback = getattr(app.state, "request_server_shutdown", None)
    if not callable(callback):
        log.warning(
            "graceful shutdown hook is unavailable; run backend/run_server.py "
            "instead of invoking uvicorn directly"
        )
        return
    try:
        callback()
    except Exception as exc:  # lifecycle boundary: never fail the flushed response
        log.warning(
            "graceful shutdown hook failed error=%s",
            type(exc).__name__,
        )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Attach a machine-readable ``error_code`` to API error responses.

    Raisers stay untouched: the detail string is resolved via ``errors.py`` and
    the resulting code + params ride alongside, so the frontend can show a
    localized message. Unmapped errors keep the original response shape.
    """
    detail = exc.detail
    if isinstance(detail, str):
        code, params = resolve_error(detail)
        if code:
            payload: dict = {"detail": detail, "error_code": code}
            if params:
                payload["error_params"] = params
            return JSONResponse(payload, status_code=exc.status_code)
    return JSONResponse({"detail": detail}, status_code=exc.status_code)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    log.exception(
        "unhandled backend exception method=%s path=%s error=%s",
        request.method,
        request.url.path,
        type(exc).__name__,
    )
    return JSONResponse(
        {"ok": False, "error": {"code": "INTERNAL_ERROR"}},
        status_code=500,
    )

# auth endpoints (public)
app.include_router(auth_router)


@app.get("/api/health")
async def health():
    database = getattr(app.state, "database_status", "starting")
    secret_store = getattr(app.state, "secret_store_status", "starting")
    clients = await manager.all_clients()
    connected = sum(1 for client in clients.values() if client.is_connected())
    account_total = 0
    if database == "ok":
        try:
            async with AsyncSessionLocal() as db:
                account_total = await db.scalar(select(func.count(Account.id))) or 0
        except SQLAlchemyError:
            database = "error"
    return {
        "ok": database == "ok" and secret_store == "ok" and settings.api_configured,
        "backend": "ok",
        "database": database,
        "telegram_api": "configured" if settings.api_configured else "missing",
        "sessions_dir": "ok" if settings.sessions_path.is_dir() else "error",
        "secret_store": secret_store,
        "secret_store_detail": getattr(app.state, "secret_store_detail", "starting"),
        "clients": {"total": account_total, "connected": connected},
    }


# all data routers require auth
PROTECTED_DEPS = [Depends(require_auth)]
app.include_router(accounts.router,        dependencies=PROTECTED_DEPS)
app.include_router(profile.router,         dependencies=PROTECTED_DEPS)
app.include_router(security.router,        dependencies=PROTECTED_DEPS)
app.include_router(groups.router,          dependencies=PROTECTED_DEPS)
app.include_router(messaging.router,       dependencies=PROTECTED_DEPS)
app.include_router(settings_router.router, dependencies=PROTECTED_DEPS)
app.include_router(bulk.router,            dependencies=PROTECTED_DEPS)


# Any /api/* path that didn't match a real route above returns a clean JSON 404
# for ANY method. Registered before the GET-only SPA fallback so an unmatched
# POST/PUT/DELETE (e.g. calling a new endpoint against a stale server) surfaces a
# proper "Not Found" instead of a confusing "405 Method Not Allowed".
@app.api_route(
    "/api/{rest:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    include_in_schema=False,
)
async def api_not_found(rest: str):
    return JSONResponse({"detail": "Not Found"}, status_code=404)


# ---- serve built frontend (single-port mode) ----
# `start.bat` builds the frontend into backend/static/. If that folder exists, serve it.
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

if STATIC_DIR.is_dir():
    assets_dir = STATIC_DIR / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str, request: Request):
        # never intercept the api
        if full_path.startswith("api/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        # try a real file first (favicon, etc.) — resolve() + containment check
        # rejects any ../ traversal attempt that escapes the static dir.
        candidate = (STATIC_DIR / full_path).resolve()
        try:
            candidate.relative_to(STATIC_DIR.resolve())
        except ValueError:
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        if candidate.is_file():
            return FileResponse(str(candidate))
        index = STATIC_DIR / "index.html"
        if index.is_file():
            return FileResponse(str(index))
        return JSONResponse({"detail": "Frontend not built. Run start.bat."}, status_code=503)
else:
    @app.get("/")
    async def no_static():
        return JSONResponse(
            {"detail": "Frontend not built. Run `npm run build` in frontend/ or use start.bat."},
            status_code=503,
        )
