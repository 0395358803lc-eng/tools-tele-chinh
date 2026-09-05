"""Windows runtime smoke test for the real FastAPI application.

This script intentionally exercises the same runtime layers used by START.bat:
configuration validation, Windows DPAPI, SQLite/Alembic startup, bundled frontend
serving, cookie authentication, a protected API, and authenticated graceful
shutdown. It never contacts Telegram because the fresh smoke database has no
accounts.
"""
from __future__ import annotations

import hashlib
import hmac
import http.cookiejar
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.request

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
ENV_PATH = BACKEND_ROOT / ".env"
STATIC_INDEX = BACKEND_ROOT / "static" / "index.html"
BASE = "http://127.0.0.1:8000"
PASSWORD = "runtime-smoke-password"
SESSION_SECRET = "runtime-smoke-session-secret-" + ("s" * 48)


def _request(opener, path: str, *, method="GET", body=None, headers=None, timeout=5):
    data = None
    req_headers = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        req_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers=req_headers,
    )
    return opener.open(request, timeout=timeout)


def _wait_for_health(opener, proc: subprocess.Popen, timeout=35):
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"backend exited early with code {proc.returncode}")
        try:
            with _request(opener, "/api/health", timeout=2) as response:
                payload = json.load(response)
            if payload.get("backend") == "ok":
                return payload
        except Exception as exc:
            last_error = exc
        time.sleep(0.25)
    raise RuntimeError(f"backend health timeout: {type(last_error).__name__ if last_error else 'unknown'}")


def _write_smoke_env():
    ENV_PATH.write_text(
        "\n".join(
            [
                "TG_API_ID=12345",
                "TG_API_HASH=0123456789abcdef0123456789abcdef",
                f"APP_PASSWORD={PASSWORD}",
                f"SESSION_SECRET={SESSION_SECRET}",
                "SESSION_DAYS=1",
                "LOGIN_MAX_ATTEMPTS=5",
                "LOGIN_WINDOW_MIN=15",
                "ALLOWED_ORIGIN=http://localhost:5173",
                "",
            ]
        ),
        encoding="utf-8",
    )


def main() -> int:
    if os.name != "nt":
        raise RuntimeError("runtime_smoke.py must run on Windows because secure storage requires DPAPI")
    if not STATIC_INDEX.is_file():
        raise RuntimeError("backend/static/index.html is missing; restore the frontend artifact first")
    if ENV_PATH.exists():
        raise RuntimeError("backend/.env unexpectedly exists in the clean CI checkout")

    cookie_jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))
    proc = None
    log_path = PROJECT_ROOT / "runtime-smoke-uvicorn.log"

    try:
        _write_smoke_env()
        with log_path.open("w", encoding="utf-8") as log_handle:
            proc = subprocess.Popen(
                [sys.executable, "run_server.py"],
                cwd=BACKEND_ROOT,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                text=True,
            )

        health = _wait_for_health(opener, proc)
        assert health["ok"] is True, health
        assert health["database"] == "ok", health
        assert health["secret_store"] == "ok", health
        assert health["telegram_api"] == "configured", health

        # Wrong password must remain rejected.
        try:
            _request(
                opener,
                "/api/auth-app/login",
                method="POST",
                body={"password": "wrong-password"},
            )
            raise AssertionError("wrong password was unexpectedly accepted")
        except urllib.error.HTTPError as exc:
            assert exc.code == 401, exc.code

        # Real signed-cookie login and protected API access.
        with _request(
            opener,
            "/api/auth-app/login",
            method="POST",
            body={"password": PASSWORD},
        ) as response:
            login = json.load(response)
        assert login == {"ok": True}, login
        assert any(cookie.name == "mtm_session" for cookie in cookie_jar), list(cookie_jar)

        with _request(opener, "/api/auth-app/me") as response:
            me = json.load(response)
        assert me.get("authed") is True, me

        with _request(opener, "/api/settings/diagnostics") as response:
            diagnostics = json.load(response)
        assert diagnostics["database"] == "ok", diagnostics
        assert diagnostics["secret_store"] == "ok", diagnostics
        assert diagnostics["accounts"] == 0, diagnostics

        # Bundled production frontend is served by the real FastAPI app.
        with _request(opener, "/") as response:
            html = response.read().decode("utf-8", errors="replace")
            content_type = response.headers.get("Content-Type", "")
        assert "text/html" in content_type.lower(), content_type
        assert "<html" in html.lower() or "<!doctype html" in html.lower(), html[:200]

        # Unknown API routes must stay JSON 404s rather than falling into the SPA.
        try:
            _request(opener, "/api/runtime-smoke-does-not-exist")
            raise AssertionError("unknown API route unexpectedly succeeded")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404, exc.code
            payload = json.loads(exc.read().decode("utf-8"))
            assert payload.get("detail") == "Not Found", payload

        # Exercise the real PID-bound graceful shutdown proof.
        token = hmac.new(
            SESSION_SECRET.encode("utf-8"),
            str(proc.pid).encode("ascii"),
            hashlib.sha256,
        ).hexdigest()
        with _request(
            opener,
            "/api/app/shutdown",
            method="POST",
            headers={"X-MTM-Shutdown-Token": token},
        ) as response:
            shutdown = json.load(response)
        assert shutdown.get("ok") is True and shutdown.get("graceful") is True, shutdown

        try:
            rc = proc.wait(timeout=20)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("backend did not exit after graceful shutdown request") from exc
        if rc != 0:
            raise RuntimeError(f"backend graceful shutdown exited with code {rc}")

        print(
            json.dumps(
                {
                    "ok": True,
                    "health": health,
                    "diagnostics": {
                        "database": diagnostics["database"],
                        "secret_store": diagnostics["secret_store"],
                        "accounts": diagnostics["accounts"],
                    },
                    "graceful_shutdown": True,
                },
                ensure_ascii=False,
            )
        )
        return 0
    finally:
        if proc is not None and proc.poll() is None:
            proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        ENV_PATH.unlink(missing_ok=True)
        if log_path.exists():
            # Keep the log on failure for Actions upload/debug; successful CI can
            # also print it if needed without contaminating runtime data.
            pass


if __name__ == "__main__":
    raise SystemExit(main())
