import asyncio
import logging
import re
from urllib.parse import urlparse, parse_qs

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from telethon.tl.functions.messages import (
    SendReactionRequest, GetMessagesViewsRequest,
    GetAvailableReactionsRequest, GetCustomEmojiDocumentsRequest,
    StartBotRequest,
)
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.errors import FloodWaitError, RPCError
from telethon.tl.types import (
    ReactionEmoji, ReactionCustomEmoji,
    ChatReactionsAll, ChatReactionsSome, ChatReactionsNone,
    DocumentAttributeCustomEmoji,
    User, Chat, Channel,
)

from ..db import get_db
from ..models import Account
from ..schemas import (
    SendMessageIn, BulkMessageIn, ReactIn, ViewPostIn,
    AllowedReactionsIn, AllowedReactionsOut, AllowedCustomReaction,
    OpenChatIn, ChatSendIn, BulkWipeChatIn, TargetUsageCheckIn,
)
from ..tg_manager import manager
from ..errors import error_code_of, error_params_of
from ..utils import friendly_error, bulk_stream, ok_result, skipped_result
from ..config import settings

router = APIRouter(prefix="/api/messaging", tags=["messaging"])
log = logging.getLogger("messaging")


def _reaction_obj(emoji: str | None, custom_emoji_id: int | None):
    """Build a Telethon reaction object: custom emoji if an id is given, else standard."""
    if custom_emoji_id:
        return ReactionCustomEmoji(document_id=int(custom_emoji_id))
    return ReactionEmoji(emoticon=emoji)


def _parse_post_link(link: str) -> tuple[str | int, int]:
    # supports t.me/<username>/<id> and https://t.me/c/<channel_id>/<id>
    s = link.strip().replace("https://", "").replace("http://", "")
    if s.startswith("t.me/"):
        parts = s[5:].split("/")
        if len(parts) >= 2 and parts[0] == "c" and len(parts) >= 3:
            channel_id = re.sub(r"\D.*$", "", parts[1])
            message_id = re.sub(r"\D.*$", "", parts[2])
            if not channel_id or not message_id:
                raise ValueError("Invalid private-channel post link")
            # Telethon's marked channel IDs use the -100<bare-id> form.
            return int(f"-100{channel_id}"), int(message_id)
        if len(parts) >= 2:
            return parts[0], int(parts[1])
    raise ValueError("Invalid post link")


async def _accounts_named(db: AsyncSession, ids: list[int]) -> list[tuple[int, str, str]]:
    res = await db.execute(select(Account).where(Account.id.in_(ids)))
    return [
        (a.id, a.phone, (f"{a.first_name or ''} {a.last_name or ''}".strip() or a.phone))
        for a in res.scalars().all()
    ]


async def _all_accounts_named(db: AsyncSession) -> list[tuple[int, str, str]]:
    res = await db.execute(select(Account).where(Account.status != "banned").order_by(Account.id))
    return [
        (a.id, a.phone, (f"{a.first_name or ''} {a.last_name or ''}".strip() or a.phone))
        for a in res.scalars().all()
    ]


@router.post("/{account_id}/send")
async def send_message(account_id: int, body: SendMessageIn):
    cli = manager.get(account_id)
    if not cli:
        raise HTTPException(409, "Account not connected")
    try:
        await manager.run_account_action(
            account_id,
            lambda: cli.send_message(body.target, body.text),
            operation="send_message",
        )
    except Exception as e:
        raise HTTPException(400, friendly_error(e))
    return {"ok": True}


@router.post("/bulk_send")
async def bulk_send(body: BulkMessageIn, db: AsyncSession = Depends(get_db)):
    accounts = await _accounts_named(db, body.account_ids)
    target, text = body.target, body.text

    async def _send(cli, aid):
        await cli.send_message(target, text)
        return ok_result("messaging.sent")

    return StreamingResponse(bulk_stream(accounts, _send), media_type="application/x-ndjson")


@router.post("/react")
async def react(body: ReactIn, db: AsyncSession = Depends(get_db)):
    try:
        chan, msg_id = _parse_post_link(body.post_link)
    except ValueError:
        raise HTTPException(400, "Invalid Telegram target or link")

    # Flatten assignments into a per-account reaction map (later assignment wins
    # on overlap). Value is (display_glyph, custom_emoji_id_or_None).
    react_by_id: dict[int, tuple[str, int | None]] = {}
    for a in body.reactions:
        for aid in a.account_ids:
            react_by_id[aid] = (a.emoji, a.custom_emoji_id)
    if not react_by_id:
        raise HTTPException(400, "No accounts selected for any reaction")

    accounts = await _accounts_named(db, list(react_by_id.keys()))

    async def _react(cli, aid):
        emoji, custom_id = react_by_id.get(aid, (None, None))
        entity = await cli.get_entity(chan)
        await cli(SendReactionRequest(
            peer=entity, msg_id=msg_id,
            reaction=[_reaction_obj(emoji, custom_id)],
        ))
        message = f"{emoji} (custom)" if custom_id else emoji
        return ok_result("messaging.reacted", {"emoji": message})

    return StreamingResponse(bulk_stream(accounts, _react), media_type="application/x-ndjson")


@router.post("/allowed_reactions", response_model=AllowedReactionsOut)
async def allowed_reactions(body: AllowedReactionsIn, db: AsyncSession = Depends(get_db)):
    """What reactions does this post's chat allow? Used to populate the reaction
    picker so the user only chooses emoji that will actually work (admin-disabled
    ones simply aren't offered)."""
    try:
        chan, _msg_id = _parse_post_link(body.post_link)
    except ValueError:
        raise HTTPException(400, "Invalid Telegram target or link")

    # pick a client to query through
    cli = manager.get(body.account_id) if body.account_id else None
    if not cli:
        clients = await manager.all_clients()
        cli = next(iter(clients.values()), None)
    if not cli:
        raise HTTPException(409, "No connected account to read reactions with")

    try:
        entity = await cli.get_entity(chan)
        avail = None
        try:
            full = await cli(GetFullChannelRequest(entity))
            avail = getattr(full.full_chat, "available_reactions", None)
        except FloodWaitError:
            raise
        except RPCError as exc:
            log.info(
                "chat reaction policy unavailable error_type=%s",
                type(exc).__name__,
            )
            avail = None

        if isinstance(avail, ChatReactionsNone):
            return AllowedReactionsOut(mode="none")

        if isinstance(avail, ChatReactionsSome):
            standard: list[str] = []
            custom_ids: list[int] = []
            for r in avail.reactions:
                if isinstance(r, ReactionEmoji):
                    standard.append(r.emoticon)
                elif isinstance(r, ReactionCustomEmoji):
                    custom_ids.append(r.document_id)
            custom = await _resolve_custom(cli, custom_ids)
            return AllowedReactionsOut(mode="some", standard=standard, custom=custom)

        # ChatReactionsAll or None -> all standard reactions allowed
        allow_custom = bool(getattr(avail, "allow_custom", False)) if avail is not None else False
        standard = await _global_standard_reactions(cli)
        return AllowedReactionsOut(mode="all", allow_custom=allow_custom, standard=standard)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, friendly_error(e))


async def _global_standard_reactions(cli) -> list[str]:
    try:
        res = await cli(GetAvailableReactionsRequest(hash=0))
        out = []
        for r in getattr(res, "reactions", []):
            if getattr(r, "inactive", False):
                continue
            emo = getattr(r, "reaction", None)
            if emo:
                out.append(emo)
        return out
    except FloodWaitError:
        raise
    except RPCError as exc:
        log.info(
            "global reactions unavailable error_type=%s",
            type(exc).__name__,
        )
        return []


async def _resolve_custom(cli, ids: list[int]) -> list[AllowedCustomReaction]:
    if not ids:
        return []
    out: list[AllowedCustomReaction] = []
    try:
        docs = await cli(GetCustomEmojiDocumentsRequest(document_id=ids))
        for d in docs:
            alt = ""
            for attr in getattr(d, "attributes", []) or []:
                if isinstance(attr, DocumentAttributeCustomEmoji):
                    alt = attr.alt or ""
                    break
            out.append(AllowedCustomReaction(id=d.id, alt=alt))
    except FloodWaitError:
        raise
    except RPCError as exc:
        # If Telegram can't resolve alts, keep the custom IDs usable with a
        # generic glyph; unexpected runtime/programming errors still propagate.
        log.info(
            "custom reaction metadata unavailable error_type=%s",
            type(exc).__name__,
        )
        out = [AllowedCustomReaction(id=i, alt="⭐") for i in ids]
    return out


@router.post("/view")
async def view(body: ViewPostIn, db: AsyncSession = Depends(get_db)):
    try:
        chan, msg_id = _parse_post_link(body.post_link)
    except ValueError:
        raise HTTPException(400, "Invalid Telegram target or link")
    accounts = await _accounts_named(db, body.account_ids)

    async def _view(cli, aid):
        entity = await cli.get_entity(chan)
        r = await cli(GetMessagesViewsRequest(peer=entity, id=[msg_id], increment=True))
        n = r.views[0].views if (r and r.views) else None
        return ok_result("messaging.views", {"n": n or 0},
                         (f"{n} views" if n is not None else ""))

    return StreamingResponse(bulk_stream(accounts, _view), media_type="application/x-ndjson")


# ---------------------------------------------------------------------------
# Telegram-like chat panel (Profile tab): open a chat by @username / t.me link,
# read recent history, send messages, and fire bot referral /start links.
# ---------------------------------------------------------------------------

def _parse_chat_input(raw: str) -> tuple[str, str | None]:
    """Parse what the user pasted into (peer, start_param).

    Handles: '@user', bare 'user', 'https://t.me/user', 't.me/user?start=PAY',
    'tg://resolve?domain=user&start=PAY'. Raises ValueError for invite links
    (t.me/+hash or t.me/joinchat/HASH), which require joining first."""
    s = (raw or "").strip()
    if not s:
        raise ValueError("Enter a username or link")

    # tg://resolve?domain=foo&start=bar
    if s.lower().startswith("tg://resolve"):
        q = parse_qs(urlparse(s).query)
        dom = (q.get("domain") or [""])[0]
        if not dom:
            raise ValueError("Invalid tg:// link")
        return dom, (q.get("start") or [None])[0]

    body = s
    for pre in ("https://", "http://"):
        if body.lower().startswith(pre):
            body = body[len(pre):]
            break

    low = body.lower()
    if low.startswith("t.me/") or low.startswith("telegram.me/"):
        rest = body.split("/", 1)[1]
        if rest.startswith("+") or rest.lower().startswith("joinchat/"):
            raise ValueError(
                "That's an invite link — join it from the Groups tab first, "
                "then open it here by @username."
            )
        path, _, query = rest.partition("?")
        seg = path.split("/")[0]
        if not seg:
            raise ValueError("Invalid t.me link")
        start_param = (parse_qs(query).get("start") or [None])[0] if query else None
        return seg, start_param

    if s.startswith("@"):
        return s[1:], None
    return s, None


def _coerce_peer(peer: str):
    """get_entity accepts a username string or an int id. Numeric strings must
    be cast to int or Telethon treats them as usernames."""
    p = (peer or "").strip()
    if re.fullmatch(r"-?\d+", p):
        return int(p)
    return p


def _peer_info(entity) -> dict:
    if isinstance(entity, User):
        name = (f"{entity.first_name or ''} {entity.last_name or ''}").strip() \
            or (entity.username or str(entity.id))
        kind = "bot" if getattr(entity, "bot", False) else "user"
    elif isinstance(entity, Channel):
        name = entity.title or (entity.username or str(entity.id))
        kind = "channel" if getattr(entity, "broadcast", False) else "group"
    elif isinstance(entity, Chat):
        name = entity.title or str(entity.id)
        kind = "group"
    else:
        name = str(getattr(entity, "id", "")) or "chat"
        kind = "unknown"
    username = getattr(entity, "username", None)
    return {
        "id": entity.id,
        "ref": username or str(entity.id),
        "title": name,
        "username": username,
        "kind": kind,
        "is_bot": bool(getattr(entity, "bot", False)),
    }


def _media_label(msg) -> str | None:
    media = getattr(msg, "media", None)
    if media is None:
        return None
    t = type(media).__name__
    if "Photo" in t:
        return "[photo]"
    if "Document" in t:
        doc = getattr(media, "document", None)
        mime = getattr(doc, "mime_type", "") or ""
        if "image" in mime:
            return "[sticker/image]"
        if "video" in mime:
            return "[video]"
        if "audio" in mime:
            return "[audio]"
        return "[file]"
    if "WebPage" in t:
        return None  # link preview — the URL is already in the text
    if "Poll" in t:
        return "[poll]"
    if "Geo" in t:
        return "[location]"
    if "Contact" in t:
        return "[contact]"
    return "[media]"


def _msg_to_dict(msg) -> dict:
    text = getattr(msg, "message", None) or ""
    date = getattr(msg, "date", None)
    return {
        "id": getattr(msg, "id", 0),
        "out": bool(getattr(msg, "out", False)),
        "text": text,
        "media": _media_label(msg) if not text else None,
        "date": date.isoformat() if date else None,
        "service": bool(getattr(msg, "action", None)),
        "sender_id": getattr(msg, "sender_id", None),
    }


async def _history(cli, entity, limit: int = 40) -> list[dict]:
    out: list[dict] = []
    async for m in cli.iter_messages(entity, limit=max(1, min(limit, 100))):
        out.append(_msg_to_dict(m))
    out.reverse()  # oldest-first for natural top-to-bottom display
    return out


async def _has_any_message(cli, entity) -> bool:
    async for _m in cli.iter_messages(entity, limit=1):
        return True
    return False


async def _has_dialog(cli, entity) -> bool:
    target_id = getattr(entity, "id", None)
    target_username = (getattr(entity, "username", None) or "").lower()
    async for dialog in cli.iter_dialogs():
        e = dialog.entity
        if target_id is not None and getattr(e, "id", None) == target_id:
            return True
        if target_username and (getattr(e, "username", None) or "").lower() == target_username:
            return True
    return False


async def _target_usage_for_client(cli, target: str) -> dict:
    peer_ref, _ = _parse_chat_input(target)
    entity = await cli.get_entity(_coerce_peer(peer_ref))
    peer = _peer_info(entity)

    if isinstance(entity, User):
        has_messages = await _has_any_message(cli, entity)
        label = "bot" if getattr(entity, "bot", False) else "user"
        return {
            "status": "present" if has_messages else "absent",
            "detail": f"has messages with this {label}" if has_messages else f"no messages with this {label}",
            "error_code": "checker.hasMessages" if has_messages else "checker.noMessages",
            "error_params": {"label": label},
            "peer": peer,
        }

    if isinstance(entity, (Channel, Chat)):
        joined = await _has_dialog(cli, entity)
        label = peer.get("kind") or "chat"
        return {
            "status": "present" if joined else "absent",
            "detail": f"joined/open in dialogs ({label})" if joined else f"not joined/opened ({label})",
            "error_code": "checker.joinedOpen" if joined else "checker.notJoinedOpen",
            "error_params": {"label": label},
            "peer": peer,
        }

    has_messages = await _has_any_message(cli, entity)
    return {
        "status": "present" if has_messages else "absent",
        "detail": "has messages" if has_messages else "no messages found",
        "error_code": "checker.hasMessagesPlain" if has_messages else "checker.noMessagesFound",
        "peer": peer,
    }


@router.post("/target_check")
async def target_check(body: TargetUsageCheckIn, db: AsyncSession = Depends(get_db)):
    target = (body.target or "").strip()
    if not target:
        raise HTTPException(400, "Enter a bot, channel, group, or user username")
    try:
        _parse_chat_input(target)
    except ValueError:
        raise HTTPException(400, "Invalid Telegram target or link")

    accounts = await _all_accounts_named(db)
    conc = max(1, int(getattr(settings, "CONCURRENCY", 8) or 8))
    sem = asyncio.Semaphore(conc)
    peer: dict | None = None

    async def _one(aid: int, phone: str, name: str):
        nonlocal peer
        async with sem:
            cli = manager.get(aid)
            if not cli:
                return {
                    "id": aid, "phone": phone, "name": name,
                    "status": "skipped", "detail": "not connected",
                    "error_code": "ACCOUNT_NOT_CONNECTED",
                }
            try:
                checked = await _target_usage_for_client(cli, target)
                if peer is None:
                    peer = checked.get("peer")
                row = {
                    "id": aid, "phone": phone, "name": name,
                    "status": checked["status"], "detail": checked["detail"],
                }
                if checked.get("error_code"):
                    row["error_code"] = checked["error_code"]
                    if checked.get("error_params"):
                        row["error_params"] = checked["error_params"]
                return row
            except Exception as e:
                detail = friendly_error(e)
                row = {
                    "id": aid, "phone": phone, "name": name,
                    "status": "failed", "detail": detail,
                }
                code = error_code_of(e)
                if code:
                    row["error_code"] = code
                    params = error_params_of(e)
                    if params:
                        row["error_params"] = params
                return row

    results = await asyncio.gather(*(_one(aid, phone, name) for aid, phone, name in accounts))
    return {
        "target": target,
        "peer": peer,
        "total": len(results),
        "present": [r for r in results if r["status"] == "present"],
        "absent": [r for r in results if r["status"] == "absent"],
        "skipped": [r for r in results if r["status"] == "skipped"],
        "failed": [r for r in results if r["status"] == "failed"],
        "results": results,
    }


@router.post("/{account_id}/open")
async def open_chat(account_id: int, body: OpenChatIn):
    """Resolve a chat by @username / t.me link, optionally fire a bot referral
    /start, and return the peer info + recent history."""
    cli = manager.get(account_id)
    if not cli:
        raise HTTPException(409, "Account not connected")
    try:
        peer_ref, start_param = _parse_chat_input(body.input)
    except ValueError:
        raise HTTPException(400, "Invalid Telegram target or link")
    try:
        entity = await cli.get_entity(_coerce_peer(peer_ref))
        started = False
        if start_param and getattr(entity, "bot", False):
            try:
                await manager.run_account_action(
                    account_id,
                    lambda: cli(StartBotRequest(bot=entity, peer=entity, start_param=start_param)),
                    operation="start_bot",
                )
            except FloodWaitError:
                raise
            except RPCError as exc:
                # Fall back only when Telegram rejects StartBot. Unexpected
                # runtime/programming errors must not trigger a second mutation.
                log.info(
                    "StartBot rejected; using message fallback error_type=%s",
                    type(exc).__name__,
                )
                await manager.run_account_action(
                    account_id,
                    lambda: cli.send_message(entity, f"/start {start_param}"),
                    operation="start_bot_fallback",
                )
            started = True
            await asyncio.sleep(1.0)  # let the bot reply before we read history
        peer = _peer_info(entity)
        msgs = await _history(cli, entity, body.limit)
        return {"peer": peer, "started": started, "start_param": start_param, "messages": msgs}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, friendly_error(e))


@router.get("/{account_id}/history")
async def chat_history(account_id: int, peer: str, limit: int = 40):
    cli = manager.get(account_id)
    if not cli:
        raise HTTPException(409, "Account not connected")
    try:
        entity = await cli.get_entity(_coerce_peer(peer))
        return {"messages": await _history(cli, entity, limit)}
    except Exception as e:
        raise HTTPException(400, friendly_error(e))


@router.post("/{account_id}/chat_send")
async def chat_send(account_id: int, body: ChatSendIn):
    cli = manager.get(account_id)
    if not cli:
        raise HTTPException(409, "Account not connected")
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(400, "Message is empty")
    try:
        entity = await cli.get_entity(_coerce_peer(body.peer))
        sent = await manager.run_account_action(
            account_id,
            lambda: cli.send_message(entity, text),
            operation="send_message",
        )
        return {"ok": True, "message": _msg_to_dict(sent)}
    except Exception as e:
        raise HTTPException(400, friendly_error(e))


# ---------------------------------------------------------------------------
# Bulk wipe a whole conversation by @username / t.me link: from every selected
# account, delete the ENTIRE chat with that user — history is cleared for both
# sides (revoke=True) and the dialog is removed, so the chat ceases to exist.
# ---------------------------------------------------------------------------

async def _wipe_chat_for_client(cli, target: str) -> tuple[str, str]:
    """Resolve `target` to a peer and delete the whole conversation with it.

    delete_dialog(revoke=True) issues messages.DeleteHistory(revoke=True) for a
    user/bot peer, which removes every message for BOTH sides and drops the
    dialog. Accounts that never had a chat with the user are reported as a soft
    'skipped' rather than a misleading success.
    """
    peer_ref, _ = _parse_chat_input(target)
    entity = await cli.get_entity(_coerce_peer(peer_ref))

    # Cheap existence probe so a no-op wipe isn't reported as a real delete.
    had: bool | None = False
    try:
        async for _m in cli.iter_messages(entity, limit=1):
            had = True
            break
    except FloodWaitError:
        raise
    except RPCError as exc:
        log.info(
            "chat wipe existence probe unavailable error_type=%s",
            type(exc).__name__,
        )
        had = None  # Telegram couldn't answer — don't claim anything either way

    await cli.delete_dialog(entity, revoke=True)

    if had is False:
        return skipped_result("messaging.noChatToWipe")
    return ok_result("messaging.chatWiped")


@router.post("/bulk_wipe_chat")
async def bulk_wipe_chat(body: BulkWipeChatIn, db: AsyncSession = Depends(get_db)):
    accounts = await _accounts_named(db, body.account_ids)
    target = body.target
    return StreamingResponse(
        bulk_stream(accounts, lambda cli, aid: _wipe_chat_for_client(cli, target)),
        media_type="application/x-ndjson",
    )
