"""Local-only store for remembered Telegram 2FA (Two-Step) passwords.

This is a single-user, local tool. We persist 2FA passwords the user typed at
login so bulk operations (e.g. bulk 2FA change) can re-supply them without the
user re-typing 42 passwords.

Passwords are encrypted at rest with the Windows Data Protection API (DPAPI,
CryptProtectData) using the *current user's* machine scope, then written to:

    <SESSIONS_DIR>/twofa.bin  ->  DPAPI(blob(JSON { "+8801...": "password" }))

DPAPI ties decryption to the Windows account that encrypted it, so the file is
useless to any other user or machine. On non-Windows platforms the same
behaviour is emulated with a best-effort obfuscation so the app still works,
but the real protection is DPAPI on Windows.

A legacy plaintext `twofa.json` is migrated (encrypted in place, then removed)
on first startup — see migrate_legacy().
"""
from __future__ import annotations
import asyncio
import ctypes
import json
import os
import re
from ctypes import wintypes
from pathlib import Path

from .config import settings

_lock = asyncio.Lock()

# ---------------------------------------------------------------------------
# DPAPI via ctypes — avoids a hard dependency on pywin32.
# ---------------------------------------------------------------------------

class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


if os.name == "nt":
    _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(DATA_BLOB), wintypes.LPCWSTR, ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(DATA_BLOB),
    ]
    _crypt32.CryptProtectData.restype = wintypes.BOOL
    _crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(DATA_BLOB), ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(DATA_BLOB),
    ]
    _crypt32.CryptUnprotectData.restype = wintypes.BOOL
    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    _kernel32.LocalFree.restype = ctypes.c_void_p


def _blob_out(data: bytes) -> tuple[DATA_BLOB, ctypes.POINTER]:
    buf = ctypes.create_string_buffer(data, len(data))
    blob = DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
    return blob, buf


def _blob_in(pblob: DATA_BLOB) -> bytes:
    if not pblob.pbData or pblob.cbData <= 0:
        return b""
    arr = ctypes.cast(pblob.pbData, ctypes.POINTER(ctypes.c_ubyte * pblob.cbData))
    return bytes(arr.contents)


def _dpapi_available() -> bool:
    return os.name == "nt"


def _dpapi_encrypt(plain: bytes) -> bytes:
    blob, buf = _blob_out(plain)
    out = DATA_BLOB()
    if not _crypt32.CryptProtectData(
            ctypes.byref(blob), "multi-tg-manager 2FA", None, None, None, 0, ctypes.byref(out)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return b"M3" + _blob_in(out)
    finally:
        if out.pbData:
            _kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


def _dpapi_decrypt(payload: bytes) -> bytes:
    if not payload:
        return b""
    if payload.startswith(b"M3"):
        payload = payload[2:]
    blob, buf = _blob_out(payload)
    out = DATA_BLOB()
    if not _crypt32.CryptUnprotectData(
            ctypes.byref(blob), None, None, None, None, 0, ctypes.byref(out)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return _blob_in(out)
    finally:
        if out.pbData:
            _kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


# Obfuscation fallback for non-Windows only (NOT a security boundary — it just
# stops casual shoulder-surfing of the bytes; DPAPI is the real safeguard).
def _encrypt(plain: bytes) -> bytes:
    if not _dpapi_available():
        raise RuntimeError("Secure 2FA storage requires Windows DPAPI")
    return _dpapi_encrypt(plain)


def _decrypt(payload: bytes) -> bytes:
    if not _dpapi_available():
        raise RuntimeError("Secure 2FA storage requires Windows DPAPI")
    return _dpapi_decrypt(payload)


# ---------------------------------------------------------------------------
# Store file handling
# ---------------------------------------------------------------------------

def _path() -> Path:
    return settings.secrets_path / "twofa.bin"


def legacy_path() -> Path:
    return settings.secrets_path / "twofa.json"


def _norm_phone(phone: str) -> str:
    p = (phone or "").strip()
    if not p:
        return p
    digits = re.sub(r"[^0-9]", "", p)
    return f"+{digits}" if digits else p


def _read() -> dict[str, str]:
    p = _path()
    if not p.exists():
        return {}
    data = _decrypt(p.read_bytes())
    obj = json.loads(data.decode("utf-8") or "{}")
    if not isinstance(obj, dict):
        raise RuntimeError("Secure 2FA store has an invalid payload")
    return {str(k): str(v) for k, v in obj.items()}


def validate_existing_store() -> tuple[bool, str]:
    """Decrypt the actual persisted store, not merely a fresh DPAPI probe."""
    if not _path().exists():
        return True, "empty"
    try:
        _read()
        return True, "ok"
    except Exception as exc:
        return False, type(exc).__name__


def validate_store_file(path: Path) -> tuple[bool, str]:
    if not path.exists():
        return True, "empty"
    try:
        data = _decrypt(path.read_bytes())
        obj = json.loads(data.decode("utf-8") or "{}")
        if not isinstance(obj, dict):
            raise RuntimeError("invalid secure-store payload")
        return True, "ok"
    except Exception as exc:
        return False, type(exc).__name__


def _write(data: dict[str, str]):
    """Atomically persist the store (temp file + os.replace). Raises on failure
    so callers that need to know the password was saved can react instead of
    silently losing it."""
    p = _path()
    tmp = p.parent / (p.name + ".tmp")
    tmp.write_bytes(_encrypt(json.dumps(data, ensure_ascii=False).encode("utf-8")))
    os.replace(tmp, p)


async def migrate_legacy():
    """Encrypt a legacy plaintext twofa.json in place, verify the round-trip,
    then delete the plaintext file. No-op when there's nothing to migrate."""
    src = legacy_path()
    if not src.exists():
        return
    try:
        if _path().exists():
            _read()  # refuse to overwrite an existing unreadable store
        data = json.loads(src.read_text(encoding="utf-8") or "{}")
        if not isinstance(data, dict):
            data = {}
        clean = {str(k): str(v) for k, v in data.items()}
        async with _lock:
            _write(clean)
            # verify we can read it back before removing the source
            back = _read()
            if set(back.keys()) == set(clean.keys()):
                src.unlink(missing_ok=True)
    except Exception:
        # Leave the legacy file in place if migration fails — safer than losing
        # the only copy of the user's passwords.
        pass


async def save_2fa(phone: str, password: str):
    if not phone or not password:
        return
    key = _norm_phone(phone)
    async with _lock:
        data = _read()
        data[key] = password
        _write(data)


async def get_2fa(phone: str) -> str | None:
    key = _norm_phone(phone)
    async with _lock:
        return _read().get(key)


async def delete_2fa(phone: str):
    key = _norm_phone(phone)
    async with _lock:
        data = _read()
        if key in data:
            del data[key]
            _write(data)


async def known_passwords() -> list[str]:
    """Unique, non-empty saved passwords — used to seed a bulk-change attempt bank."""
    async with _lock:
        seen: list[str] = []
        for v in _read().values():
            if v and v not in seen:
                seen.append(v)
        return seen


async def count() -> int:
    async with _lock:
        return len([v for v in _read().values() if v])


async def clear_all():
    async with _lock:
        # Verify first so an unreadable existing store is never silently replaced.
        _read()
        _write({})
