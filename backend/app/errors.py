"""Map every backend user-facing message to a machine-readable i18n key.

The frontend is the single translation source: it looks up keys under its
``errors.*`` namespace (or full dotted keys) and falls back to the raw detail
text only when no translation exists. This module guarantees *every* message the
backend sends into the UI carries a stable key:

* ``resolve_error(detail)`` — for ``HTTPException(detail=...)`` raise sites,
  matched on the English detail string (including every ``friendly_error()``
  output, via ``utils._ERROR_MESSAGES``).
* ``error_code_of(e)`` — for streamed/row detail, matched on the Telethon
  exception class name (all 34 classes in ``_ERROR_MESSAGES``).
* ``error_params_of(e)`` — extra interpolation params (FloodWait seconds).

Codes that are simple ``errors.*`` members are written bare (e.g.
``ACCOUNT_NOT_FOUND``); full dotted keys (e.g. ``addAccount.importFailed``)
are used where the translation lives in another namespace.
"""
import re

# Explicit codes for known static detail strings (HTTPException paths).
_MESSAGE_CODES: dict[str, str] = {
    "Account not found": "ACCOUNT_NOT_FOUND",
    "Account not connected": "ACCOUNT_NOT_CONNECTED",
    "Account is not connected": "ACCOUNT_NOT_CONNECTED",
    "No connected account to read reactions with": "ACCOUNT_NOT_CONNECTED",
    "This account is banned/deactivated by Telegram.": "ACCOUNT_BANNED",
    "This account is deactivated.": "ACCOUNT_BANNED",
    "Session expired — reconnect this account.": "SESSION_INVALID",
    "Invalid username": "USERNAME_INVALID",
    "Invalid username (5–32 chars, letters/digits/underscore, must start with a letter).": "USERNAME_INVALID",
    "Enter a bot, channel, group, or user username": "USERNAME_INVALID",
    "Username already taken": "USERNAME_OCCUPIED",
    "That username is already taken.": "USERNAME_OCCUPIED",
    "No such username — nobody is using it.": "TARGET_NOT_FOUND",
    "Post not found (bad message id / link).": "TARGET_NOT_FOUND",
    "Can't access this chat from this account.": "TARGET_NOT_FOUND",
    "Wrong password": "INVALID_PASSWORD",
    "Wrong 2FA password": "INVALID_PASSWORD",
    "Wrong current 2FA password.": "INVALID_PASSWORD",
    "Message is empty": "MESSAGE_SEND_FAILED",
    "Not authenticated": "UNAUTHORIZED",
    "Bio max 70 chars": "BIO_TOO_LONG",
    "Could not read user info from Telegram": "USER_INFO_FAILED",
    "Telegram did not return a phone number for this user": "PHONE_MISSING",
    "New password is required": "NEW_PASSWORD_REQUIRED",
    "password required": "PASSWORD_REQUIRED",
    "No accounts selected for any reaction": "NO_REACTIONS_SELECTED",
    "No session files uploaded": "NO_SESSION_FILES",
    "This account is in too many groups/channels (Telegram cap ~500). Leave some first.": "CHANNEL_LIMIT_REACHED",
    "This account is in too many groups/channels. Leave some first.": "CHANNEL_LIMIT_REACHED",
    "Telegram took too long to respond. Try again.": "TIMEOUT",
    "Telegram took too long. Try again.": "TIMEOUT",
}

# Telethon exception class name -> code. Bare `errors.*` keys where we already
# had them, plus auto-named BULK_* codes for everything else. Overrides first.
_CLASS_OVERRIDES: dict[str, str] = {
    "FloodWaitError": "FLOOD_WAIT",
    "UserDeactivatedBanError": "ACCOUNT_BANNED",
    "UserDeactivatedError": "ACCOUNT_BANNED",
    "AuthKeyUnregisteredError": "SESSION_INVALID",
    "PasswordHashInvalidError": "INVALID_PASSWORD",
    "UsernameNotOccupiedError": "TARGET_NOT_FOUND",
    "MsgIdInvalidError": "TARGET_NOT_FOUND",
    "MessageIdInvalidError": "TARGET_NOT_FOUND",
    "PeerIdInvalidError": "TARGET_NOT_FOUND",
    "UsernameInvalidError": "USERNAME_INVALID",
    "UsernameOccupiedError": "USERNAME_OCCUPIED",
    "ChannelsTooMuchError": "CHANNEL_LIMIT_REACHED",
    "UserChannelsTooMuchError": "CHANNEL_LIMIT_REACHED",
    "RuntimeError": "addAccount.importFailed",
}
_TO_UPPER = re.compile(r"Error$")
_PART_RE = re.compile(r"[A-Z]+(?![a-z])|[A-Z][a-z]+|[0-9]+")


def _auto_code(name: str) -> str:
    code = _CLASS_OVERRIDES.get(name)
    if code:
        return code
    base = _TO_UPPER.sub("", name)
    parts = [p.upper() for p in _PART_RE.findall(base) if p]
    return "BULK_" + "_".join(parts)


_CLASS_CODES: dict[str, str] = {}


def _class_codes() -> dict[str, str]:
    """All class-name codes (built lazily to avoid an import cycle with utils)."""
    if not _CLASS_CODES:
        from .utils import _ERROR_MESSAGES  # utils imports .errors at module load
        _CLASS_CODES.update({name: _auto_code(name) for name in _ERROR_MESSAGES})
        _CLASS_CODES["FloodWaitError"] = "FLOOD_WAIT"
    return _CLASS_CODES


# e.g. "Rate limited — wait 5s before trying again." / "FloodWait: wait 10s after 3 deleted"
_FLOOD_RE = re.compile(r"(?:FloodWait|Rate limited)[^\d]*(\d+)s")
# app login throttling
_APP_RATE_RE = re.compile(r"Too many attempts\. Try again in (\d+)s.")


def resolve_error(detail: str) -> tuple[str | None, dict | None]:
    """Return ``(error_code, error_params)`` for a detail string, or ``(None, None)``."""
    code = _MESSAGE_CODES.get(detail)
    if code:
        return code, None
    # friendly_error() outputs (utils._ERROR_MESSAGES) -> same BULK_* codes.
    by_msg = _friendly_codes()
    code = by_msg.get(detail)
    if code:
        return code, None
    m = _FLOOD_RE.search(detail)
    if m:
        try:
            return "FLOOD_WAIT", {"seconds": int(m.group(1))}
        except ValueError:
            pass
    m = _APP_RATE_RE.search(detail)
    if m:
        try:
            return "APP_RATE_LIMIT", {"retry": int(m.group(1))}
        except ValueError:
            pass
    return None, None


_friendly_cache: dict[str, str] = {}


def _friendly_codes() -> dict[str, str]:
    if not _friendly_cache:
        from .utils import _ERROR_MESSAGES
        for name, msg in _ERROR_MESSAGES.items():
            if name not in _CLASS_OVERRIDES and msg not in _MESSAGE_CODES:
                _friendly_cache[msg] = _auto_code(name)
    return _friendly_cache


def error_code_of(e: Exception) -> str | None:
    """Code for a resolved/raised exception (used for streamed rows)."""
    return _class_codes().get(type(e).__name__)


def error_params_of(e: Exception) -> dict | None:
    from telethon.errors import FloodWaitError
    if isinstance(e, FloodWaitError):
        return {"seconds": e.seconds}
    return None