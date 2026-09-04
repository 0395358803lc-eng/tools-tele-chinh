"""Local-only mock API used for the browser render smoke test."""
from pathlib import Path

from fastapi import Cookie, FastAPI, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

app = FastAPI()
static = Path(__file__).resolve().parents[1] / "static"


class Login(BaseModel):
    password: str


@app.get("/api/health")
async def health():
    return {
        "ok": True, "backend": "ok", "database": "ok",
        "telegram_api": "configured", "sessions_dir": "ok", "secret_store": "ok",
        "clients": {"total": 0, "connected": 0},
    }


@app.get("/api/auth-app/me")
async def me(mtm_smoke: str | None = Cookie(None)):
    return {"authed": mtm_smoke == "yes"}


@app.post("/api/auth-app/login")
async def login(body: Login, response: Response):
    if body.password != "ui-smoke-password":
        return JSONResponse({"detail": "Wrong password"}, status_code=401)
    response.set_cookie("mtm_smoke", "yes", httponly=True, samesite="lax")
    return {"ok": True}


@app.post("/api/auth-app/logout")
async def logout(response: Response):
    response.delete_cookie("mtm_smoke")
    return {"ok": True}


@app.get("/api/accounts")
async def accounts(): return []


@app.get("/api/gone_accounts")
async def gone(): return []


@app.get("/api/stats")
async def stats():
    return {"total": 0, "connected": 0, "banned": 0, "with_2fa": 0, "unread_security": 0}


@app.get("/api/settings")
async def get_settings():
    return {"rate_min": 0.7, "rate_max": 1.5, "concurrency": 8, "auto_reconnect": True}


@app.get("/api/settings/backups")
async def backups(): return {"backups": []}


@app.get("/api/settings/diagnostics")
async def diagnostics():
    return {"app_version": "smoke", "database": "ok", "accounts": 0, "connected": 0}


@app.get("/api/security/messages")
async def messages(): return []


@app.get("/api/security/twofa_known")
async def twofa_known(): return {"count": 0}


@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def api_fallback(path: str):
    return {}


app.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")


@app.get("/{path:path}")
async def spa(path: str):
    candidate = static / path
    return FileResponse(candidate if candidate.is_file() else static / "index.html")
