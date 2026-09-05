"""Focused client-start lifecycle for authorized Telegram account sessions."""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from telethon.errors import RPCError

from .config import settings


class ClientStartService:
    def __init__(
        self,
        *,
        clients: dict[int, object],
        manual_disconnect: set[int],
        reconnect_exhausted: set[int],
        client_factory: Callable[..., object],
        account_lock: Callable[[int], Awaitable[asyncio.Lock]],
        session_path_for_account: Callable[[object], str],
        reset_reconnect: Callable[[int], None],
        safe_disconnect: Callable[..., Awaitable[None]],
        record_connection_failure: Callable[[int, Exception], Awaitable[None]],
        set_status: Callable[[int, str], Awaitable[None]],
        attach_listener: Callable[[int, object], None],
        sync_profile: Callable[[int, object], Awaitable[None]],
        backfill_security: Callable[..., Awaitable[object]],
        logger: logging.Logger | None = None,
    ):
        self._clients = clients
        self._manual_disconnect = manual_disconnect
        self._reconnect_exhausted = reconnect_exhausted
        self._client_factory = client_factory
        self._account_lock = account_lock
        self._session_path_for_account = session_path_for_account
        self._reset_reconnect = reset_reconnect
        self._safe_disconnect = safe_disconnect
        self._record_connection_failure = record_connection_failure
        self._set_status = set_status
        self._attach_listener = attach_listener
        self._sync_profile = sync_profile
        self._backfill_security = backfill_security
        self._log = logger or logging.getLogger("client_start")

    async def start_locked(self, acc, *, reset_reconnect: bool):
        """Start/authorize one account while preserving the manager lock order."""
        async with await self._account_lock(acc.id):
            if reset_reconnect:
                self._reset_reconnect(acc.id)
                self._manual_disconnect.discard(acc.id)

            cli = self._clients.get(acc.id)
            if cli and cli.is_connected():
                await self._set_status(acc.id, "connected")
                return cli

            if cli is None:
                cli = self._client_factory(
                    self._session_path_for_account(acc),
                    settings.tg_api_id,
                    settings.TG_API_HASH,
                    auto_reconnect=False,
                )

            try:
                await asyncio.wait_for(
                    cli.connect(),
                    timeout=float(settings.TELEGRAM_CONNECT_TIMEOUT),
                )
                authorized = await asyncio.wait_for(
                    cli.is_user_authorized(),
                    timeout=float(settings.TELEGRAM_AUTH_TIMEOUT),
                )
            except asyncio.CancelledError:
                await self._safe_disconnect(
                    cli,
                    context=f"start_client_cancelled:{acc.id}",
                )
                self._clients.pop(acc.id, None)
                raise
            except Exception as exc:
                await self._safe_disconnect(
                    cli,
                    context=f"start_client_failed:{acc.id}",
                )
                self._clients.pop(acc.id, None)
                await self._record_connection_failure(acc.id, exc)
                raise

            if not authorized:
                await self._safe_disconnect(
                    cli,
                    context=f"start_client_unauthorized:{acc.id}",
                )
                self._clients.pop(acc.id, None)
                self._reconnect_exhausted.add(acc.id)
                await self._set_status(acc.id, "session_revoked")
                raise RuntimeError("Session is not authorized")

            self._clients[acc.id] = cli
            self._attach_listener(acc.id, cli)
            self._reset_reconnect(acc.id)
            await self._set_status(acc.id, "connected")

            try:
                me = await asyncio.wait_for(
                    cli.get_me(),
                    timeout=float(settings.TELEGRAM_AUTH_TIMEOUT),
                )
            except (
                RPCError,
                asyncio.TimeoutError,
                ConnectionError,
                OSError,
            ) as exc:
                self._log.warning(
                    "account=%s profile refresh skipped error=%s",
                    acc.id,
                    type(exc).__name__,
                )
            else:
                await self._sync_profile(acc.id, me)

            try:
                await asyncio.wait_for(
                    self._backfill_security(acc.id, cli, limit=50),
                    timeout=30,
                )
            except Exception as exc:
                # Backfill is startup best-effort; live session availability wins.
                self._log.warning(
                    "startup backfill failed account=%s error_type=%s",
                    acc.id,
                    type(exc).__name__,
                )

            return cli
