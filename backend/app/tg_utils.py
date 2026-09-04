"""Pure helpers shared by Telegram lifecycle and security-message handling.

Keep these functions independent from Telethon/SQLAlchemy so they are easy to test
and so TgClientManager can focus on orchestration and stateful lifecycle logic.
"""
from __future__ import annotations

import re


RECONNECT_BACKOFF_SECONDS = (5, 15, 30, 60)
_LOGIN_CODE_RE = re.compile(r"(?<!\d)\d{4,}\b")


def classify_777000(text: str) -> str:
    low = (text or "").lower()
    if re.search(r"login code|\b\d{5}\b", low):
        return "login_code"
    if "new login" in low or "new device" in low:
        return "new_login"
    if "two-step" in low or "password" in low:
        return "2fa_change"
    if "delete" in low or "deactivation" in low:
        return "account_deletion"
    return "unknown"


def redact_login_code(text: str) -> str:
    """Remove standalone 4+ digit OTP-like runs before persistence."""
    if not text:
        return text
    return _LOGIN_CODE_RE.sub("[code]", text)


def normalize_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if not digits:
        raise ValueError("Phone number must contain digits")
    if len(digits) > 15:
        raise ValueError("Phone number is too long")
    return f"+{digits}"


def permanent_connection_status(exc: BaseException) -> str | None:
    """Map known permanent Telegram auth/account failures to persisted status."""
    name = type(exc).__name__
    if name in {"UserDeactivatedBanError", "UserDeactivatedError", "PhoneNumberBannedError"}:
        return "banned"
    if name in {
        "AuthKeyUnregisteredError",
        "SessionRevokedError",
        "AuthKeyDuplicatedError",
        "AuthKeyInvalidError",
    }:
        return "session_revoked"
    return None
