import asyncio
import json
import logging
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from telethon.tl.types import Channel, Chat, ChatForbidden, ChannelForbidden
from telethon.tl.functions.channels import JoinChannelRequest, LeaveChannelRequest, GetParticipantRequest
from telethon.tl.functions.messages import ImportChatInviteRequest, CheckChatInviteRequest
from telethon.errors import (
    FloodWaitError, UserAlreadyParticipantError, InviteHashExpiredError, UserNotParticipantError,
    RPCError,
)
from time import time

from ..db import get_db
from ..models import Account
from ..schemas import (
    JoinIn, BulkJoinIn, GroupOut, LeaveIn, BulkLeaveIn,
    BulkLeaveTargetIn, BulkLeaveAllIn, BulkDeleteMyMessagesIn,
)
from ..tg_manager import manager
from ..utils import friendly_error, bulk_stream, BulkResult, ok_result, skipped_result, pending_result

# Optional import — older Telethon builds may not expose this name.
try:
    from telethon.errors import InviteRequestSentError
except ImportError:  # pragma: no cover
    InviteRequestSentError = None

router = APIRouter(prefix="/api/groups", tags=["groups"])
log = logging.getLogger("groups")

# crude 5-min cache: account_id -> (ts, list)
_cache: dict[int, tuple[float, list[dict]]] = {}
_CACHE_MAX_ACCOUNTS = 200
CACHE_TTL = 300


def _parse_invite(target: str) -> tuple[str, str | None]:
    """Return (kind, payload). kind in {'username','invite'}."""
    t = target.strip()
    if t.startswith("https://t.me/+") or t.startswith("t.me/+") or t.startswith("https://t.me/joinchat/"):
        # invite link
        payload = t.split("/")[-1].lstrip("+")
        return "invite", payload
    if t.startswith("https://t.me/") or t.startswith("t.me/"):
        u = t.split("/")[-1].lstrip("@")
        return "username", u
    if t.startswith("@"):
        return "username", t[1:]
    return "username", t


async def _join_with_client(cli, target: str):
    kind, payload = _parse_invite(target)
    if kind == "invite":
        return await cli(ImportChatInviteRequest(payload))
    return await cli(JoinChannelRequest(payload))


async def _join_handle(cli, target: str) -> BulkResult:
    """Join a group/channel. Returns a structured BulkResult.

    status is 'ok' or 'pending'. Recoverable cases are handled here:
      - already a member -> ok
      - join request sent (approval needed) -> pending
      - FloodWait -> propagate to the account-wide scheduler/cooldown
    Anything else is raised so the caller can map it via friendly_error().
    The "account is at the channels cap" error is deliberately NOT auto-handled
    (the app never silently leaves a chat to make room); it propagates so the
    UI shows CHANNEL_LIMIT_REACHED and the user decides what to leave.
    """
    try:
        await _join_with_client(cli, target)
        return ok_result("groups.joined")
    except UserAlreadyParticipantError:
        return ok_result("groups.alreadyMember")
    except FloodWaitError:
        raise
    except Exception as e:
        if InviteRequestSentError is not None and isinstance(e, InviteRequestSentError):
            return pending_result("groups.joinRequestSent")
        raise


@router.post("/{account_id}/join")
async def join_one(account_id: int, body: JoinIn):
    cli = manager.get(account_id)
    if not cli:
        raise HTTPException(409, "Account not connected")
    try:
        res = await manager.run_account_action(
            account_id,
            lambda: _join_handle(cli, body.target),
            operation="join_group",
        )
        _cache.pop(account_id, None)
    except FloodWaitError as e:
        raise HTTPException(429, f"Rate limited — wait {e.seconds}s")
    except Exception as e:
        raise HTTPException(400, friendly_error(e))
    return {"ok": True, "status": res.status, "message_code": res.message_code,
            "params": res.params or {}, "detail": res.detail}


@router.post("/bulk_join")
async def bulk_join(body: BulkJoinIn, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Account).where(Account.id.in_(body.account_ids)))
    accounts = [
        (a.id, a.phone, (f"{a.first_name or ''} {a.last_name or ''}".strip() or a.phone))
        for a in res.scalars().all()
    ]
    target = body.target

    return StreamingResponse(
        bulk_stream(accounts, lambda cli, aid: _join_handle(cli, target),
                    on_success=lambda aid: _cache.pop(aid, None)),
        media_type="application/x-ndjson",
    )


@router.get("/{account_id}/list", response_model=list[GroupOut])
async def list_groups(account_id: int):
    now = time()
    hit = _cache.get(account_id)
    if hit and (now - hit[0]) < CACHE_TTL:
        return hit[1]
    cli = manager.get(account_id)
    if not cli:
        raise HTTPException(409, "Account not connected")
    out: list[dict] = []
    async for dialog in cli.iter_dialogs():
        e = dialog.entity
        if isinstance(e, (Chat, ChatForbidden)):
            out.append({
                "id": int(e.id),
                "title": getattr(e, "title", "") or "",
                "username": None,
                "type": "group",
                "members": getattr(e, "participants_count", None),
                "invite_link": None,
            })
        elif isinstance(e, (Channel, ChannelForbidden)):
            is_megagroup = getattr(e, "megagroup", False)
            out.append({
                "id": int(e.id),
                "title": getattr(e, "title", "") or "",
                "username": getattr(e, "username", None),
                "type": "supergroup" if is_megagroup else "channel",
                "members": getattr(e, "participants_count", None),
                "invite_link": f"https://t.me/{e.username}" if getattr(e, "username", None) else None,
            })
    _cache.pop(account_id, None)
    _cache[account_id] = (now, out)
    while len(_cache) > _CACHE_MAX_ACCOUNTS:
        _cache.pop(next(iter(_cache)))
    return out


@router.post("/{account_id}/leave")
async def leave_one(account_id: int, body: LeaveIn):
    cli = manager.get(account_id)
    if not cli:
        raise HTTPException(409, "Account not connected")
    try:
        async def _leave():
            entity = await cli.get_entity(body.chat_id)
            if isinstance(entity, (Channel, ChannelForbidden)):
                await cli(LeaveChannelRequest(entity))
            else:
                await cli.delete_dialog(entity)

        await manager.run_account_action(
            account_id, _leave, operation="leave_group"
        )
        _cache.pop(account_id, None)
    except Exception as e:
        raise HTTPException(400, friendly_error(e))
    return {"ok": True}


async def _leave_with_client(cli, chat_id: int) -> BulkResult:
    entity = await cli.get_entity(chat_id)
    if isinstance(entity, (Channel, ChannelForbidden)):
        await cli(LeaveChannelRequest(entity))
    else:
        await cli.delete_dialog(entity)
    return ok_result("groups.left")


@router.post("/bulk_leave")
async def bulk_leave(body: BulkLeaveIn, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Account).where(Account.id.in_(body.account_ids)))
    accounts = [
        (a.id, a.phone, (f"{a.first_name or ''} {a.last_name or ''}".strip() or a.phone))
        for a in res.scalars().all()
    ]
    chat_id = body.chat_id

    return StreamingResponse(
        bulk_stream(accounts, lambda cli, aid: _leave_with_client(cli, chat_id),
                    on_success=lambda aid: _cache.pop(aid, None)),
        media_type="application/x-ndjson",
    )


async def _leave_by_target_with_client(cli, target: str) -> BulkResult:
    """Leave a group/channel given by @username or invite link — but only if this
    account is actually a member. Non-members are reported as a soft 'skipped'."""
    kind, payload = _parse_invite(target)
    if kind == "invite":
        # Private link: CheckChatInvite tells us if we're already in (has .chat).
        try:
            inv = await cli(CheckChatInviteRequest(payload))
        except FloodWaitError:
            raise
        except RPCError as exc:
            log.info(
                "invite membership check rejected error_type=%s",
                type(exc).__name__,
            )
            return skipped_result("groups.invalidInvite")
        entity = getattr(inv, "chat", None)
        if entity is None:
            return skipped_result("groups.notMember")
    else:
        try:
            entity = await cli.get_entity(payload)
        except FloodWaitError:
            raise
        except (RPCError, ValueError) as exc:
            log.info(
                "group target resolution failed error_type=%s",
                type(exc).__name__,
            )
            return skipped_result("groups.cantResolveTarget")

    if isinstance(entity, (Channel, ChannelForbidden)):
        # Confirm membership so we don't report a no-op leave as success.
        try:
            await cli(GetParticipantRequest(entity, "me"))
        except UserNotParticipantError:
            return skipped_result("groups.notMember")
        except FloodWaitError:
            raise
        except RPCError as exc:
            # Some Telegram-side permission/state errors can make the membership
            # probe unavailable even though LeaveChannel still succeeds.
            log.warning(
                "group membership probe failed error_type=%s; attempting leave",
                type(exc).__name__,
            )
        await cli(LeaveChannelRequest(entity))
    else:
        try:
            await cli.delete_dialog(entity)
        except FloodWaitError:
            raise
        except RPCError as exc:
            log.info(
                "group dialog leave rejected error_type=%s",
                type(exc).__name__,
            )
            return skipped_result("groups.notMember")
    return ok_result("groups.left")


@router.post("/bulk_leave_target")
async def bulk_leave_target(body: BulkLeaveTargetIn, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Account).where(Account.id.in_(body.account_ids)))
    accounts = [
        (a.id, a.phone, (f"{a.first_name or ''} {a.last_name or ''}".strip() or a.phone))
        for a in res.scalars().all()
    ]
    target = body.target

    return StreamingResponse(
        bulk_stream(accounts, lambda cli, aid: _leave_by_target_with_client(cli, target),
                    on_success=lambda aid: _cache.pop(aid, None)),
        media_type="application/x-ndjson",
    )


# Short pause between per-group operations WITHIN one account, to stay under
# Telegram's per-account rate limits when an account is in many groups.
_INTRA_DELAY = 0.4


async def _collect_chats(cli) -> list:
    """All group/supergroup/channel entities this account is in (skips DMs/bots)."""
    entities = []
    async for dialog in cli.iter_dialogs():
        e = dialog.entity
        if isinstance(e, (Chat, ChatForbidden, Channel, ChannelForbidden)):
            entities.append(e)
    return entities


async def _leave_entity(cli, entity):
    if isinstance(entity, (Channel, ChannelForbidden)):
        await cli(LeaveChannelRequest(entity))
    else:
        await cli.delete_dialog(entity)


async def _leave_all_for_client(cli, aid: int) -> BulkResult:
    """Leave every group/channel this account is in. Never raises FloodWait —
    a long wait stops this account early and reports partial progress."""
    entities = await _collect_chats(cli)
    left = errors = 0
    for entity in entities:
        try:
            await _leave_entity(cli, entity)
            left += 1
        except FloodWaitError:
            raise
        except RPCError as exc:
            errors += 1
            log.warning(
                "bulk leave failed account_id=%s entity_id=%s error_type=%s",
                aid,
                getattr(entity, "id", None),
                type(exc).__name__,
            )
        await asyncio.sleep(_INTRA_DELAY)
    detail = f"left {left} of {len(entities)}" + (f", {errors} error(s)" if errors else "")
    return ok_result("groups.leaveAllDone", {"left": left, "total": len(entities),
                                             "errors": errors}, detail)


async def _delete_all_my_messages_for_client(cli, aid: int, max_scan: int) -> BulkResult:
    """Delete (revoke) every message this account sent across all its groups/channels."""
    me = await cli.get_me()
    entities = await _collect_chats(cli)
    scan = min(max(max_scan, 1), 10000)
    total_deleted = groups_touched = 0
    for entity in entities:
        ids: list[int] = []
        try:
            async for msg in cli.iter_messages(entity, from_user=me, limit=scan):
                ids.append(msg.id)
        except FloodWaitError:
            raise
        except RPCError as exc:
            log.warning(
                "bulk message scan skipped account_id=%s entity_id=%s error_type=%s",
                aid,
                getattr(entity, "id", None),
                type(exc).__name__,
            )
            continue  # Telegram refused this chat (banned/forbidden) — skip it
        if not ids:
            continue
        deleted_here = 0
        for i in range(0, len(ids), 100):
            batch = ids[i:i + 100]
            try:
                await cli.delete_messages(entity, batch, revoke=True)
                deleted_here += len(batch)
            except FloodWaitError:
                raise
            except RPCError as exc:
                log.warning(
                    "bulk message delete batch failed account_id=%s entity_id=%s error_type=%s",
                    aid,
                    getattr(entity, "id", None),
                    type(exc).__name__,
                )
        if deleted_here:
            total_deleted += deleted_here
            groups_touched += 1
        await asyncio.sleep(_INTRA_DELAY)
    return ok_result("groups.deleteDone", {"deleted": total_deleted, "groups": groups_touched})


@router.post("/bulk_leave_all")
async def bulk_leave_all(body: BulkLeaveAllIn, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Account).where(Account.id.in_(body.account_ids)))
    accounts = [
        (a.id, a.phone, (f"{a.first_name or ''} {a.last_name or ''}".strip() or a.phone))
        for a in res.scalars().all()
    ]
    return StreamingResponse(
        bulk_stream(accounts, _leave_all_for_client,
                    on_success=lambda aid: _cache.pop(aid, None)),
        media_type="application/x-ndjson",
    )


@router.post("/bulk_delete_my_messages")
async def bulk_delete_my_messages(body: BulkDeleteMyMessagesIn, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Account).where(Account.id.in_(body.account_ids)))
    accounts = [
        (a.id, a.phone, (f"{a.first_name or ''} {a.last_name or ''}".strip() or a.phone))
        for a in res.scalars().all()
    ]
    max_scan = body.max_scan

    return StreamingResponse(
        bulk_stream(accounts, lambda cli, aid: _delete_all_my_messages_for_client(cli, aid, max_scan)),
        media_type="application/x-ndjson",
    )


@router.get("/{account_id}/my_messages_count")
async def count_my_messages(account_id: int, chat_id: int, max_scan: int = 1000):
    """Count how many messages the logged-in user has in the given chat
    (scans up to max_scan recent messages)."""
    cli = manager.get(account_id)
    if not cli:
        raise HTTPException(409, "Account not connected")
    try:
        entity = await cli.get_entity(chat_id)
        me = await cli.get_me()
        count = 0
        async for msg in cli.iter_messages(entity, from_user=me, limit=min(max(max_scan, 1), 5000)):
            count += 1
        return {"count": count, "scanned_limit": max_scan}
    except Exception as e:
        raise HTTPException(400, friendly_error(e))


@router.post("/{account_id}/delete_my_messages")
async def delete_my_messages(account_id: int, chat_id: int, max_scan: int = 2000):
    """Delete every message the logged-in user sent in the given chat
    (for everyone, revoke=True). Scans up to max_scan recent messages."""
    cli = manager.get(account_id)
    if not cli:
        raise HTTPException(409, "Account not connected")
    try:
        entity = await cli.get_entity(chat_id)
        me = await cli.get_me()
        ids: list[int] = []
        async for msg in cli.iter_messages(entity, from_user=me, limit=min(max(max_scan, 1), 10000)):
            ids.append(msg.id)
        if not ids:
            return {"deleted": 0, "scanned_limit": max_scan}
        # Telegram limits delete batches to 100
        deleted = 0
        for i in range(0, len(ids), 100):
            batch = ids[i:i+100]
            try:
                res = await manager.run_account_action(
                    account_id,
                    lambda batch=batch: cli.delete_messages(entity, batch, revoke=True),
                    operation="delete_messages",
                )
                # res can be list or PtsCountInt; treat success per id
                deleted += len(batch)
            except FloodWaitError as e:
                raise HTTPException(429, f"FloodWait: wait {e.seconds}s after {deleted} deleted")
        _cache.pop(account_id, None)
        return {"deleted": deleted, "scanned_limit": max_scan}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, friendly_error(e))
