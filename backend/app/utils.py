"""Helpers for bulk action delays and FloodWait handling."""
import asyncio
import json
import random
import re
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional
from telethon.errors import FloodWaitError
from .config import settings
from .errors import error_code_of, error_params_of


class _NotConnected(Exception):
    """Internal sentinel: the account's client vanished (disconnected/replaced)
    between task scheduling and execution under the action lock."""


async def jitter_delay(min_s: float | None = None, max_s: float | None = None):
    lo = min_s if min_s is not None else settings.RATE_MIN
    hi = max_s if max_s is not None else settings.RATE_MAX
    if hi < lo:
        hi = lo
    await asyncio.sleep(random.uniform(lo, hi))


# Map Telethon exception class names -> short, human-friendly explanations.
# Matched by class name so we don't need to import every error type.
_ERROR_MESSAGES = {
    "ChannelsTooMuchError": "This account is in too many groups/channels (Telegram cap ~500). Leave some first.",
    "UserChannelsTooMuchError": "This account is in too many groups/channels. Leave some first.",
    "ChannelPrivateError": "Channel/group is private, or this account was removed/banned from it.",
    "InviteHashExpiredError": "Invite link has expired.",
    "InviteHashInvalidError": "Invite link is invalid.",
    "InviteHashEmptyError": "Invite link is empty/invalid.",
    "UsernameNotOccupiedError": "No such username — nobody is using it.",
    "UsernameInvalidError": "Invalid username (5–32 chars, letters/digits/underscore, must start with a letter).",
    "UsernameOccupiedError": "That username is already taken.",
    "UsernamePurchaseAvailableError": "That username is reserved/for sale — pick another.",
    "ReactionInvalidError": "This chat doesn't allow that reaction.",
    "ReactionEmptyError": "No reaction was sent.",
    "ReactionsTooManyError": "Too many reactions — this chat allows fewer.",
    "ChatWriteForbiddenError": "No permission to do this here.",
    "ChatAdminRequiredError": "Admin rights are required for this action.",
    "ChatGuestSendForbiddenError": "You must join the chat before you can do this.",
    "ChatRestrictedError": "This chat is restricted for this account.",
    "ChatForbiddenError": "This account can't access this chat.",
    "MsgIdInvalidError": "Post not found (bad message id / link).",
    "MessageIdInvalidError": "Post not found (bad message id / link).",
    "PeerIdInvalidError": "Can't access this chat from this account.",
    "UserDeactivatedBanError": "This account is banned/deactivated by Telegram.",
    "UserDeactivatedError": "This account is deactivated.",
    "AuthKeyUnregisteredError": "Session expired — reconnect this account.",
    "UserBannedInChannelError": "This account is banned from sending here.",
    "UserAlreadyParticipantError": "Already a member.",
    "InviteRequestSentError": "Join request sent — waiting for an admin to approve.",
    "UserAlreadyInvitedError": "Join request already sent — waiting for approval.",
    "UsersTooMuchError": "This group/channel is full.",
    "PasswordHashInvalidError": "Wrong current 2FA password.",
    "FreshResetAuthorisationForbiddenError": "Telegram is blocking 2FA changes on a freshly-added session — try again later.",
    "PasswordTooFreshError": "2FA was changed too recently — Telegram requires a wait before changing it again.",
    "SessionTooFreshError": "This session is too new — Telegram requires a wait before changing 2FA.",
    "DocumentInvalidError": "That custom emoji isn't valid for this chat.",
}


def _redact(text: str) -> str:
    """Strip value-like secrets out of arbitrary error text before it can reach
    a log line or the UI. Catches long digit runs (OTP codes), hex tokens and
    api_hash= style parameters."""
    if not text:
        return text
    t = text
    # api_id/api_hash/hash=tokens (32+ hex)
    t = re.sub(r"(?i)(api_hash|api_id|hash)=\b[0-9a-f]{8,}\b", r"\1=[REDACTED]", t)
    # bare 32+ hex tokens (access hashes, session ids)
    t = re.sub(r"\b[0-9a-f]{32,}\b", "[REDACTED]", t)
    # Phone numbers retain only their final four digits; OTPs never survive.
    t = re.sub(r"(?<!\d)\d{4,8}(?!\d)", "[REDACTED]", t)
    t = re.sub(r"(?<!\d)\+?\d{9,15}(?!\d)", lambda m: "***" + m.group(0)[-4:], t)
    return t


def friendly_error(e: Exception) -> str:
    if isinstance(e, FloodWaitError):
        return f"Rate limited — wait {e.seconds}s before trying again."
    name = type(e).__name__
    if name in _ERROR_MESSAGES:
        return _ERROR_MESSAGES[name]
    # Some Telethon errors are dynamically named like 'FloodWaitError' subclasses.
    return f"Telegram operation failed ({name})"


# Errors that aren't real failures — they're expected conditions where the
# action simply can't apply (account full, chat disallows the reaction, group
# full). We surface these as a soft "skipped" with a plain reason instead of a
# scary red "failed", so a bulk run still finishes cleanly.
# NOTE: ChannelsTooMuchError is deliberately NOT here — since auto-leave was
# removed, hitting the ~500 chat cap is a real failure the user must act on.
SOFT_SKIP_ERRORS = {
    "ReactionInvalidError",     # this chat doesn't allow that reaction
    "ReactionEmptyError",       # reaction not accepted
    "ReactionsTooManyError",    # chat allows fewer reactions
    "UsersTooMuchError",        # group/channel is full
    "UserAlreadyParticipantError",  # already a member — nothing to do
    "DocumentInvalidError",     # custom emoji not allowed here
}


def is_soft_error(e: Exception) -> bool:
    return type(e).__name__ in SOFT_SKIP_ERRORS


@dataclass
class BulkResult:
    """Structured result for one account in a bulk run.

    `message_code` is a full i18n key (frontend translates it), `params` are its
    interpolation values, and `detail` is a debug/fallback string only — it is
    never the primary text the UI shows for this row."""
    status: str  # ok | failed | skipped | pending
    message_code: Optional[str] = None
    params: Optional[dict] = field(default_factory=dict)
    detail: Optional[str] = None


def ok_result(message_code=None, params=None, detail=None):
    return BulkResult("ok", message_code, params, detail)


def skipped_result(message_code=None, params=None, detail=None):
    return BulkResult("skipped", message_code, params, detail)


def failed_result(message_code=None, params=None, detail=None):
    return BulkResult("failed", message_code, params, detail)


def pending_result(message_code=None, params=None, detail=None):
    return BulkResult("pending", message_code, params, detail)


def _row_from_result(aid: int, phone: str, name: str, res: BulkResult | tuple) -> dict:
    if isinstance(res, BulkResult):
        row: dict = {"id": aid, "phone": phone, "name": name,
                     "status": res.status, "detail": res.detail}
        if res.message_code:
            row["message_code"] = res.message_code
            if res.params:
                row["params"] = res.params
        return row
    # backward-compat (status, detail) tuple — treated as an ok row with detail
    status, detail = res
    return {"id": aid, "phone": phone, "name": name,
            "status": "ok" if status in ("pending", "skipped") else status,
            "detail": detail or None}


async def bulk_stream(
    accounts: list[tuple[int, str, str]],
    action: Callable[[object, int], Awaitable[BulkResult | tuple[str, str]]],
    on_success: Optional[Callable[[int], None]] = None,
    concurrency: int | None = None,
):
    """Run `action` over accounts with bounded concurrency, yielding NDJSON lines.

    accounts: list of (account_id, phone, display_name).
    action(client, account_id) -> BulkResult (preferred) or (status, detail) tuple.
        Raise for failures (mapped via friendly_error). Expected/non-fatal errors
        (see SOFT_SKIP_ERRORS) become a soft 'skipped' instead of 'failed'.
        Accounts that aren't connected are skipped automatically.
    on_success(account_id): optional side-effect after a non-failing action.
    concurrency: how many accounts run at once (defaults to settings.CONCURRENCY).

    Emits one `{"type":"progress", ...}` line as each account finishes and a final
    `{"type":"done", ...}` line. Rows carry `message_code`+`params` (structured
    outcome) or `error_code`+`error_params` (raised exception); `detail` is always
    kept as a debug fallback. Telegram rate limits are per-account, so running
    different accounts in parallel is safe; each account still does a single paced
    action with a jitter delay from the Settings window.
    """
    from .tg_manager import manager  # lazy import to avoid circular import

    total = len(accounts)
    conc = concurrency if concurrency is not None else getattr(settings, "CONCURRENCY", 5)
    try:
        conc = max(1, int(conc))
    except (TypeError, ValueError):
        conc = 5

    success = failed = skipped = pending = 0
    results: list[dict] = []
    sem = asyncio.Semaphore(conc)
    out_q: asyncio.Queue = asyncio.Queue()

    async def worker(aid: int, phone: str, name: str):
        async with sem:
            # The client is resolved lazily, *inside* the per-account action
            # lock, so a reconnect that swaps the client between here and the
            # lock acquisition can never hand the action a stale/disconnected
            # reference racing a connect().
            if not manager.get(aid):
                row = {"id": aid, "phone": phone, "name": name,
                       "status": "skipped", "detail": "not connected",
                       "error_code": "ACCOUNT_NOT_CONNECTED"}
            else:
                try:
                    operation = getattr(action, "__name__", "bulk_mutation")
                    if operation.startswith("<"):
                        operation = "bulk_mutation"

                    async def _invoke():
                        cli = manager.get(aid)
                        if not cli:
                            raise _NotConnected()
                        return await action(cli, aid)

                    res = await manager.run_account_action(aid, _invoke, operation=operation)
                    status = res.status if isinstance(res, BulkResult) else res[0]
                    row = _row_from_result(aid, phone, name, res)
                    if on_success and status in ("ok", "pending"):
                        on_success(aid)
                except _NotConnected:
                    row = {"id": aid, "phone": phone, "name": name,
                           "status": "skipped", "detail": "not connected",
                           "error_code": "ACCOUNT_NOT_CONNECTED"}
                except Exception as e:
                    soft = is_soft_error(e)
                    detail = friendly_error(e)
                    row = {"id": aid, "phone": phone, "name": name,
                           "status": "skipped" if soft else "failed",
                           "detail": detail}
                    code = error_code_of(e)
                    if code:
                        row["error_code"] = code
                        params = error_params_of(e)
                        if params:
                            row["error_params"] = params
        await out_q.put(row)

    tasks = [asyncio.create_task(worker(aid, phone, name)) for aid, phone, name in accounts]

    try:
        for done_count in range(1, total + 1):
            row = await out_q.get()
            status = row["status"]
            if status == "pending":
                pending += 1
            elif status == "ok":
                success += 1
            elif status == "skipped":
                skipped += 1
            else:
                failed += 1
            results.append(row)
            line: dict = {
                "type": "progress", "current": done_count, "total": total,
                "account_name": row.get("name", ""), "status": status,
                "detail": row.get("detail", ""),
                "success": success, "failed": failed, "skipped": skipped, "pending": pending,
            }
            for code_field, param_field in (("message_code", "params"), ("error_code", "error_params")):
                if row.get(code_field):
                    line[code_field] = row[code_field]
                    if row.get(param_field):
                        line[param_field] = row[param_field]
            yield json.dumps(line) + "\n"
    finally:
        await asyncio.gather(*tasks, return_exceptions=True)

    yield json.dumps({
        "type": "done", "total": total,
        "success": success, "failed": failed, "skipped": skipped, "pending": pending,
        "results": results,
    }) + "\n"
