"""Password auth: bcrypt-verified password, itsdangerous-signed cookie, IP rate limit."""
from __future__ import annotations
import asyncio
import hashlib
import time
import secrets
from collections import defaultdict, deque
from typing import Optional

import bcrypt
from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from itsdangerous import TimestampSigner, BadSignature, SignatureExpired
from pydantic import BaseModel, Field

from .config import settings

COOKIE_NAME = "mtm_session"

# --- bcrypt hash cache (compute once per process) ---
_pw_hash: Optional[bytes] = None


def _password_hash() -> bytes:
    global _pw_hash
    if _pw_hash is None:
        if not settings.APP_PASSWORD:
            raise RuntimeError("APP_PASSWORD not set in .env")
        # Pre-hash so bcrypt's 72-byte input ceiling cannot make a valid
        # (especially Unicode) configured password impossible to verify.
        material = hashlib.sha256(settings.APP_PASSWORD.encode("utf-8")).digest()
        _pw_hash = bcrypt.hashpw(material, bcrypt.gensalt(rounds=12))
    return _pw_hash


def _verify_password(pw: str) -> bool:
    if not pw:
        return False
    try:
        material = hashlib.sha256(pw.encode("utf-8")).digest()
        return bcrypt.checkpw(material, _password_hash())
    except Exception:
        return False


def verify_app_password(pw: str) -> bool:
    return _verify_password(pw)


# --- signer ---
_signer: Optional[TimestampSigner] = None


def _get_signer() -> TimestampSigner:
    global _signer
    if _signer is None:
        secret = settings.SESSION_SECRET
        if not secret or len(secret) < 48:
            raise RuntimeError("SESSION_SECRET not set or too short (min 48 chars) in .env")
        _signer = TimestampSigner(secret, salt="mtm-session-v1")
    return _signer


def _make_token() -> str:
    return _get_signer().sign(secrets.token_urlsafe(24)).decode()


def _verify_token(token: str) -> bool:
    if not token:
        return False
    try:
        max_age = max(1, settings.SESSION_DAYS) * 86400
        _get_signer().unsign(token, max_age=max_age)
        return True
    except (BadSignature, SignatureExpired):
        return False
    except Exception:
        return False


# --- rate limit: per-IP sliding window ---
_attempts: dict[str, deque[float]] = defaultdict(deque)


def _check_rate(ip: str) -> tuple[bool, int]:
    """Return (allowed, remaining_seconds_if_blocked)."""
    window = settings.LOGIN_WINDOW_MIN * 60
    now = time.time()
    dq = _attempts[ip]
    while dq and now - dq[0] > window:
        dq.popleft()
    if len(dq) >= settings.LOGIN_MAX_ATTEMPTS:
        oldest = dq[0]
        remaining = int(window - (now - oldest))
        return False, max(remaining, 1)
    return True, 0


def _record_failed(ip: str):
    _attempts[ip].append(time.time())


def _client_ip(req: Request) -> str:
    peer = req.client.host if req.client else "unknown"
    trusted = {item.strip() for item in settings.TRUSTED_PROXY_IPS.split(",") if item.strip()}
    if peer in trusted:
        fwd = req.headers.get("x-forwarded-for")
        if fwd:
            candidate = fwd.split(",")[0].strip()
            if candidate:
                return candidate
    return peer


# --- FastAPI dependency ---
async def require_auth(request: Request, mtm_session: str = Cookie(None)):
    if not _verify_token(mtm_session or ""):
        raise HTTPException(401, "Not authenticated")
    return True


# --- router ---
router = APIRouter(prefix="/api/auth-app", tags=["auth-app"])


class LoginIn(BaseModel):
    password: str = Field(min_length=1, max_length=256)


@router.post("/login")
async def login(body: LoginIn, request: Request, response: Response):
    ip = _client_ip(request)
    allowed, retry = _check_rate(ip)
    if not allowed:
        raise HTTPException(429, f"Too many attempts. Try again in {retry}s.")
    if not _verify_password(body.password):
        _record_failed(ip)
        # constant-ish response time floor (non-blocking on the event loop)
        await asyncio.sleep(0.4)
        raise HTTPException(401, "Wrong password")
    token = _make_token()
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=settings.SESSION_DAYS * 86400,
        httponly=True,
        samesite="lax",
        secure=False,  # served on localhost; set True if you put behind HTTPS
        path="/",
    )
    # clear failed attempts on success
    _attempts[ip].clear()
    return {"ok": True}


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me")
async def me(mtm_session: str = Cookie(None)):
    return {"authed": _verify_token(mtm_session or "")}
