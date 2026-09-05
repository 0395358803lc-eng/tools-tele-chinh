"""Account connection health, reconnect scheduling, and auth verification."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable

from sqlalchemy import select
from telethon.errors import (
    AuthKeyUnregisteredError,
    RPCError,
    UserDeactivatedBanError,
    UserDeactivatedError,
)

from .config import settings
from .db import AsyncSessionLocal
from .models import Account
from .tg_utils import RECONNECT_BACKOFF_SECONDS, permanent_connection_status


class AccountHealthService:
    def __init__(
        self,
        *,
        clients: dict[int, object],
        reconnect_attempts: dict[int, int],
        reconnect_at: dict[int, float],
        reconnect_exhausted: set[int],
        manual_disconnect: set[int],
        last_auth_check: dict[int, float],
        get_auth_cursor: Callable[[], int],
        set_auth_cursor: Callable[[int], None],
        auto_reconnect_enabled: Callable[[], bool],
        set_status: Callable[[int, str], Awaitable[None]],
        mark_banned: Callable[[int], Awaitable[None]],
        start_client: Callable[..., Awaitable[object]],
        stop_client: Callable[[int], Awaitable[None]],
        logger: logging.Logger | None = None,
    ):
        self._clients = clients
        self._reconnect_attempts = reconnect_attempts
        self._reconnect_at = reconnect_at
        self._reconnect_exhausted = reconnect_exhausted
        self._manual_disconnect = manual_disconnect
        self._last_auth_check = last_auth_check
        self._get_auth_cursor = get_auth_cursor
        self._set_auth_cursor = set_auth_cursor
        self._auto_reconnect_enabled = auto_reconnect_enabled
        self._set_status = set_status
        self._mark_banned = mark_banned
        self._start_client = start_client
        self._stop_client = stop_client
        self._log = logger or logging.getLogger("account_health")

    @staticmethod
    def permanent_status(exc: Exception) -> str | None:
        return permanent_connection_status(exc)

    def reset_reconnect(self, account_id: int) -> None:
        self._reconnect_attempts.pop(account_id, None)
        self._reconnect_at.pop(account_id, None)
        self._reconnect_exhausted.discard(account_id)

    async def record_connection_failure(
        self,
        account_id: int,
        exc: Exception,
    ) -> None:
        permanent = self.permanent_status(exc)
        if permanent:
            self._reconnect_exhausted.add(account_id)
            self._reconnect_at.pop(account_id, None)
            if permanent == "banned":
                await self._mark_banned(account_id)
            else:
                await self._set_status(account_id, permanent)
            return

        attempts = self._reconnect_attempts.get(account_id, 0) + 1
        self._reconnect_attempts[account_id] = attempts
        await self._set_status(account_id, "disconnected")
        if attempts > len(RECONNECT_BACKOFF_SECONDS):
            self._reconnect_exhausted.add(account_id)
            self._reconnect_at.pop(account_id, None)
            self._log.warning(
                "account=%s reconnect exhausted after %s attempts",
                account_id,
                attempts,
            )
            return

        delay = RECONNECT_BACKOFF_SECONDS[attempts - 1]
        self._reconnect_at[account_id] = time.monotonic() + delay
        self._log.warning(
            "account=%s reconnect attempt=%s failed; retry_in_seconds=%s",
            account_id,
            attempts,
            delay,
        )

    async def refresh_status_all(self) -> None:
        async with AsyncSessionLocal() as db:
            res = await db.execute(select(Account))
            accounts = list(res.scalars().all())
        by_id = {acc.id: acc for acc in accounts}

        for aid, cli in list(self._clients.items()):
            try:
                if not cli.is_connected():
                    await self._set_status(aid, "disconnected")
            except (OSError, RuntimeError) as exc:
                self._log.debug(
                    "account=%s connection-state probe failed error=%s",
                    aid,
                    type(exc).__name__,
                )
                await self._set_status(aid, "disconnected")
                if self.permanent_status(exc):
                    await self.record_connection_failure(aid, exc)

        if not self._auto_reconnect_enabled():
            return

        now = time.monotonic()
        due: list[tuple[int, Account]] = []
        for aid, acc in by_id.items():
            cli = self._clients.get(aid)
            if cli and cli.is_connected():
                continue
            if acc.status in {"banned", "session_revoked", "auth_error"}:
                continue
            if aid in self._reconnect_exhausted:
                continue
            if aid in self._manual_disconnect:
                continue
            retry_at = self._reconnect_at.get(aid)
            if retry_at is None:
                self._reconnect_attempts.setdefault(aid, 1)
                self._reconnect_at[aid] = now + RECONNECT_BACKOFF_SECONDS[0]
                continue
            if retry_at > now:
                continue
            due.append((aid, acc))

        async def _reconnect_one(aid: int, acc: Account):
            await self._set_status(aid, "connecting")
            try:
                await asyncio.wait_for(
                    self._start_client(acc, reset_reconnect=False),
                    timeout=75,
                )
                self._last_auth_check[aid] = time.monotonic()
            except Exception as exc:
                self._log.warning(
                    "account=%s reconnect failed error=%s",
                    aid,
                    type(exc).__name__,
                )

        if due:
            await asyncio.gather(
                *(_reconnect_one(aid, acc) for aid, acc in due)
            )

    async def verify_authorizations_all(
        self,
        *,
        reset_reconnect: Callable[[int], None],
    ) -> int:
        interval = max(
            30.0,
            float(getattr(settings, "STATUS_AUTH_INTERVAL", 60.0)),
        )
        poll = max(
            0.1,
            float(getattr(settings, "STATUS_POLL_SECS", 5.0)),
        )
        now = time.monotonic()

        connected_ids: list[int] = []
        for aid, cli in list(self._clients.items()):
            try:
                ok = cli.is_connected()
            except (OSError, RuntimeError) as exc:
                self._log.debug(
                    "account=%s connection-state read failed error=%s",
                    aid,
                    type(exc).__name__,
                )
                ok = False
            if ok:
                connected_ids.append(aid)
            else:
                self._last_auth_check.pop(aid, None)

        if not connected_ids:
            return 0

        slice_n = max(1, int(round(interval / poll)))
        cursor = self._get_auth_cursor()
        self._set_auth_cursor((cursor + 1) % max(slice_n, 1))
        batch = [
            aid
            for aid in connected_ids[cursor::slice_n]
            if (now - self._last_auth_check.get(aid, 0.0)) >= interval
        ]

        verified = 0
        for aid in batch:
            cli = self._clients.get(aid)
            if not cli:
                continue
            try:
                ok = await asyncio.wait_for(
                    cli.is_user_authorized(),
                    timeout=float(settings.TELEGRAM_AUTH_TIMEOUT),
                )
            except (UserDeactivatedBanError, UserDeactivatedError):
                await self._mark_banned(aid)
                self._reconnect_exhausted.add(aid)
                await self._stop_client(aid)
                self._last_auth_check[aid] = now
                continue
            except AuthKeyUnregisteredError:
                await self._set_status(aid, "session_revoked")
                self._reconnect_exhausted.add(aid)
                await self._stop_client(aid)
                self._last_auth_check[aid] = now
                continue
            except (RPCError, asyncio.TimeoutError, ConnectionError, OSError) as exc:
                self._log.debug(
                    "account=%s authorization verify deferred error=%s",
                    aid,
                    type(exc).__name__,
                )
                continue

            self._last_auth_check[aid] = now
            if ok:
                await self._set_status(aid, "connected")
                reset_reconnect(aid)
            else:
                await self._set_status(aid, "session_revoked")
                self._reconnect_exhausted.add(aid)
                await self._stop_client(aid)
            verified += 1

        return verified
