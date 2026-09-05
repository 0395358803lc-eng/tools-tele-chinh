"""Telegram 777000 security-message listener, persistence, and backfill."""
from __future__ import annotations

import asyncio
import logging
from typing import Callable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from telethon import events
from telethon.errors import RPCError

from .client_cleanup import TelegramClientCleanup
from .db import AsyncSessionLocal
from .models import SecurityMessage
from .tg_utils import classify_777000, redact_login_code
from .time_utils import utc_now_naive

SERVICE_ID = 777000


class SecurityMessageService:
    def __init__(self, logger: logging.Logger | None = None):
        self._log = logger or logging.getLogger("security_messages")
        self._cleanup = TelegramClientCleanup(self._log)
        self.handlers: dict[int, tuple[object, object]] = {}
        self.callbacks: list[Callable[[dict], None]] = []

    async def redact_stored_login_codes(self) -> None:
        """Scrub OTP digits stored by older versions without blocking startup."""
        try:
            async with AsyncSessionLocal() as db:
                res = await db.execute(
                    select(SecurityMessage).where(SecurityMessage.type == "login_code")
                )
                changed = 0
                for sm in res.scalars().all():
                    cleaned = redact_login_code(sm.message_text or "")
                    if cleaned != sm.message_text:
                        sm.message_text = cleaned
                        changed += 1
                if changed:
                    await db.commit()
                    self._log.info(
                        "redacted OTP digits from %d stored 777000 login messages",
                        changed,
                    )
        except Exception as exc:
            # Deliberate startup resilience boundary: old-message redaction must
            # not make the application unavailable. Keep diagnostics sanitized.
            self._log.warning(
                "login-code redaction skipped error_type=%s",
                type(exc).__name__,
            )

    def attach(self, account_id: int, cli) -> None:
        previous = self.handlers.pop(account_id, None)
        if previous:
            old_cli, old_handler = previous
            self._cleanup.remove_event_handler(
                old_cli,
                old_handler,
                context=f"replace_listener:{account_id}",
            )

        async def _handler(event):
            try:
                text = event.message.message or ""
                msg_id = event.message.id
                m_type = classify_777000(text)
                if m_type == "login_code":
                    text = redact_login_code(text)
                async with AsyncSessionLocal() as db:
                    sm = SecurityMessage(
                        account_id=account_id,
                        tg_msg_id=msg_id,
                        message_text=text,
                        type=m_type,
                        is_read=False,
                        received_at=utc_now_naive(),
                    )
                    db.add(sm)
                    try:
                        await db.commit()
                    except IntegrityError:
                        await db.rollback()
                        return
                    await db.refresh(sm)

                payload = {
                    "id": sm.id,
                    "account_id": account_id,
                    "type": m_type,
                    "message_text": text,
                    "received_at": sm.received_at.isoformat(),
                }
                for callback in list(self.callbacks):
                    try:
                        callback(payload)
                    except Exception as exc:
                        self._log.warning(
                            "new-message subscriber failed account=%s callback=%s error=%s",
                            account_id,
                            getattr(callback, "__name__", type(callback).__name__),
                            type(exc).__name__,
                        )
            except Exception as exc:
                # Event handlers are an asynchronous resilience boundary. A
                # single malformed/update failure must not detach the listener.
                self._log.warning(
                    "777000 handler failed account=%s error_type=%s",
                    account_id,
                    type(exc).__name__,
                )

        cli.add_event_handler(_handler, events.NewMessage(from_users=SERVICE_ID))
        self.handlers[account_id] = (cli, _handler)

    def detach(self, account_id: int, *, context: str) -> bool:
        entry = self.handlers.pop(account_id, None)
        if not entry:
            return True
        cli, handler = entry
        return self._cleanup.remove_event_handler(cli, handler, context=context)

    def clear_handlers(self) -> None:
        self.handlers.clear()

    def subscribe(self, callback: Callable[[dict], None]) -> None:
        self.callbacks.append(callback)

    def unsubscribe(self, callback: Callable[[dict], None]) -> None:
        try:
            self.callbacks.remove(callback)
        except ValueError:
            pass

    async def backfill(self, account_id: int, cli, limit: int = 50) -> int:
        """Persist unseen 777000 history while isolating Telegram read failures."""
        async with AsyncSessionLocal() as db:
            res = await db.execute(
                select(SecurityMessage.tg_msg_id).where(
                    SecurityMessage.account_id == account_id
                )
            )
            seen = {row[0] for row in res.all()}

        added = 0
        try:
            iterator = cli.iter_messages(SERVICE_ID, limit=limit).__aiter__()
        except (RPCError, ConnectionError, OSError, asyncio.TimeoutError) as exc:
            self._log.warning(
                "backfill Telegram stream unavailable account=%s error_type=%s",
                account_id,
                type(exc).__name__,
            )
            return 0

        while True:
            try:
                msg = await iterator.__anext__()
            except StopAsyncIteration:
                break
            except (RPCError, ConnectionError, OSError, asyncio.TimeoutError) as exc:
                self._log.warning(
                    "backfill Telegram read stopped account=%s error_type=%s",
                    account_id,
                    type(exc).__name__,
                )
                break

            if msg.id in seen:
                continue
            text = msg.message or ""
            if not text:
                continue
            m_type = classify_777000(text)
            if m_type == "login_code":
                text = redact_login_code(text)
            async with AsyncSessionLocal() as db:
                sm = SecurityMessage(
                    account_id=account_id,
                    tg_msg_id=msg.id,
                    message_text=text,
                    type=m_type,
                    is_read=True,
                    received_at=(
                        msg.date.replace(tzinfo=None) if msg.date else utc_now_naive()
                    ),
                )
                db.add(sm)
                try:
                    await db.commit()
                except IntegrityError:
                    await db.rollback()
                    continue
            added += 1

        if added:
            self._log.info(
                "backfilled %d 777000 messages for account %s",
                added,
                account_id,
            )
        return added
