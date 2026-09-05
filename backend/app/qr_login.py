"""QR-based Telegram login lifecycle and pending-session state."""
from __future__ import annotations

import asyncio
from contextlib import suppress
import logging
import secrets
import time
from typing import Awaitable, Callable

from telethon.errors import SessionPasswordNeededError

from .config import settings


class QRLoginService:
    """Own QR pending/completed state while the manager remains the facade."""

    def __init__(
        self,
        *,
        client_factory: Callable[..., object],
        safe_disconnect: Callable[..., Awaitable[None]],
        remove_session_files: Callable[[str], None],
        save_2fa: Callable[[str, str], Awaitable[None]],
        cleanup_pending: Callable[[], Awaitable[None]],
        wait_for_scan: Callable[[str], Awaitable[None]],
        logger: logging.Logger | None = None,
    ):
        self._client_factory = client_factory
        self._safe_disconnect = safe_disconnect
        self._remove_session_files = remove_session_files
        self._save_2fa = save_2fa
        self._cleanup_pending = cleanup_pending
        self._wait_for_scan = wait_for_scan
        self._log = logger or logging.getLogger("qr_login")
        self.pending: dict[str, dict] = {}
        self.locks: dict[str, asyncio.Lock] = {}
        self.completed: dict[str, tuple[float, dict]] = {}

    @staticmethod
    def session_path(qr_id: str) -> str:
        return str(settings.sessions_path / f"qr_{qr_id}")

    async def cleanup_expired(self) -> None:
        now = time.monotonic()
        for qr_id, entry in list(self.pending.items()):
            if entry.get("expires_at", 0) <= now:
                entry["error"] = entry.get("error") or "QR code expired"
                await self.close_entry(qr_id, remove=False)
                # Retain terminal state briefly for pollers, then evict it.
                if entry.get("closed_at", now) + 30 <= now:
                    self.pending.pop(qr_id, None)
                    self.locks.pop(qr_id, None)

        completed_ttl = max(30, int(settings.QR_PENDING_TTL_SECONDS))
        for qr_id, (finished_at, _payload) in list(self.completed.items()):
            if finished_at + completed_ttl <= now:
                self.completed.pop(qr_id, None)
                self.locks.pop(qr_id, None)

    async def start(self) -> dict:
        if not settings.api_configured:
            raise RuntimeError(
                "TG_API_ID / TG_API_HASH are not set in backend/.env — cannot start QR login.\n"
                "Get your credentials from https://my.telegram.org and fill them in."
            )

        qr_id = secrets.token_urlsafe(12)
        sess_path = self.session_path(qr_id)
        cli = self._client_factory(
            sess_path,
            settings.tg_api_id,
            settings.TG_API_HASH,
        )
        keep = False
        try:
            await asyncio.wait_for(
                cli.connect(),
                timeout=float(settings.TELEGRAM_CONNECT_TIMEOUT),
            )
            qr_login = await asyncio.wait_for(cli.qr_login(), timeout=30)
            entry = {
                "client": cli,
                "qr_login": qr_login,
                "wait_task": None,
                "needs_2fa": False,
                "authorized": False,
                "error": None,
                "me": None,
                "session_path": sess_path,
                "expires_at": (
                    time.monotonic()
                    + max(30, int(settings.QR_PENDING_TTL_SECONDS))
                ),
                "closed_at": None,
            }
            self.pending[qr_id] = entry
            entry["wait_task"] = asyncio.create_task(
                self._wait_for_scan(qr_id)
            )
            keep = True
        finally:
            if not keep:
                await self._safe_disconnect(
                    cli,
                    context=f"qr_start_cleanup:{qr_id}",
                    suppress_cancelled=True,
                )
                self._remove_session_files(sess_path)

        return {
            "qr_id": qr_id,
            "url": qr_login.url,
            "expires_at": (
                qr_login.expires.isoformat()
                if qr_login.expires
                else None
            ),
        }

    async def wait(self, qr_id: str) -> None:
        entry = self.pending.get(qr_id)
        if not entry:
            return
        cli = entry["client"]
        qr = entry["qr_login"]
        try:
            await qr.wait()
            try:
                me = await cli.get_me()
                entry["me"] = me
                entry["authorized"] = True
            except Exception as exc:
                self._log.warning(
                    "QR login user lookup failed qr_id=%s error=%s",
                    qr_id,
                    type(exc).__name__,
                )
                entry["error"] = "Could not read Telegram user information"
                await self.close_entry(
                    qr_id,
                    remove=False,
                    cancel_wait=False,
                )
        except SessionPasswordNeededError:
            entry["needs_2fa"] = True
        except asyncio.TimeoutError:
            entry["error"] = "QR code expired"
            await self.close_entry(
                qr_id,
                remove=False,
                cancel_wait=False,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._log.warning(
                "QR login wait failed qr_id=%s error=%s",
                qr_id,
                type(exc).__name__,
            )
            entry["error"] = "QR login failed"
            await self.close_entry(
                qr_id,
                remove=False,
                cancel_wait=False,
            )

    async def recreate(self, qr_id: str) -> dict:
        entry = self.pending.get(qr_id)
        if not entry:
            raise RuntimeError("QR session not found")

        await self._cleanup_pending()
        entry = self.pending.get(qr_id)
        if not entry:
            raise RuntimeError("QR session not found")

        old = entry.get("wait_task")
        if old and not old.done():
            old.cancel()
            with suppress(asyncio.CancelledError):
                await old

        cli = entry["client"]
        if entry.get("closed_at") is not None or not cli.is_connected():
            await self._safe_disconnect(
                cli,
                context=f"qr_recreate_closed:{qr_id}",
                suppress_cancelled=True,
            )
            self._remove_session_files(entry["session_path"])
            cli = self._client_factory(
                entry["session_path"],
                settings.tg_api_id,
                settings.TG_API_HASH,
            )
            try:
                await asyncio.wait_for(
                    cli.connect(),
                    timeout=float(settings.TELEGRAM_CONNECT_TIMEOUT),
                )
            except BaseException:
                await self._safe_disconnect(
                    cli,
                    context=f"qr_recreate_connect_failed:{qr_id}",
                    suppress_cancelled=True,
                )
                self._remove_session_files(entry["session_path"])
                raise
            entry["client"] = cli

        try:
            qr_login = await asyncio.wait_for(cli.qr_login(), timeout=30)
        except BaseException:
            entry["error"] = "QR login failed"
            await self.close_entry(
                qr_id,
                remove=False,
                cancel_wait=False,
            )
            raise

        entry["qr_login"] = qr_login
        entry["error"] = None
        entry["authorized"] = False
        entry["needs_2fa"] = False
        entry["closed_at"] = None
        entry["expires_at"] = (
            time.monotonic()
            + max(30, int(settings.QR_PENDING_TTL_SECONDS))
        )
        entry["wait_task"] = asyncio.create_task(
            self._wait_for_scan(qr_id)
        )
        return {
            "qr_id": qr_id,
            "url": qr_login.url,
            "expires_at": (
                qr_login.expires.isoformat()
                if qr_login.expires
                else None
            ),
        }

    async def status(self, qr_id: str) -> dict:
        completed = self.completed.get(qr_id)
        if completed:
            return {"state": "finalized", **completed[1]}

        await self._cleanup_pending()
        entry = self.pending.get(qr_id)
        if not entry:
            return {"state": "missing"}
        if entry["authorized"]:
            return {"state": "authorized"}
        if entry["needs_2fa"]:
            return {"state": "needs_2fa"}
        if entry["error"] == "QR code expired":
            return {"state": "expired"}
        if entry["error"]:
            return {"state": "error", "error": entry["error"]}
        return {"state": "waiting"}

    async def finalize(self, qr_id: str):
        entry = self.pending.get(qr_id)
        if not entry or not entry["authorized"]:
            raise RuntimeError("QR not authorized")
        return entry["me"], entry["client"], entry["session_path"]

    def finalize_lock(self, qr_id: str) -> asyncio.Lock:
        return self.locks.setdefault(qr_id, asyncio.Lock())

    def completed_payload(self, qr_id: str) -> dict | None:
        item = self.completed.get(qr_id)
        return item[1] if item else None

    def mark_completed(self, qr_id: str, payload: dict) -> None:
        self.completed[qr_id] = (time.monotonic(), payload)

    async def submit_2fa(self, qr_id: str, password: str):
        entry = self.pending.get(qr_id)
        if not entry:
            raise RuntimeError("QR session not found")
        if not entry["needs_2fa"]:
            raise RuntimeError("QR session does not require 2FA")

        cli = entry["client"]
        await asyncio.wait_for(cli.sign_in(password=password), timeout=30)
        me = await cli.get_me()
        entry["authorized"] = True
        entry["me"] = me

        try:
            if getattr(me, "phone", None):
                await self._save_2fa(me.phone, password)
        except (OSError, RuntimeError, ValueError) as exc:
            self._log.warning(
                "qr secure 2FA password save failed error=%s",
                type(exc).__name__,
            )
        return me

    async def promote_to_phone(self, qr_id: str, phone: str):
        entry = self.pending.get(qr_id)
        if not entry:
            raise RuntimeError("QR session not found")

        wait_task = entry.get("wait_task")
        if wait_task and not wait_task.done():
            wait_task.cancel()
            with suppress(asyncio.CancelledError):
                await wait_task

        await self._safe_disconnect(
            entry.get("client"),
            context=f"qr_promote:{qr_id}",
            suppress_cancelled=True,
        )
        return entry["session_path"]

    async def finish(self, qr_id: str, *, remove_session: bool) -> None:
        entry = self.pending.pop(qr_id, None)
        if entry and remove_session:
            self._remove_session_files(entry.get("session_path", ""))

    async def close_entry(
        self,
        qr_id: str,
        *,
        remove: bool,
        cancel_wait: bool = True,
    ) -> None:
        entry = self.pending.get(qr_id)
        if not entry:
            return

        wait_task = entry.get("wait_task")
        current = asyncio.current_task()
        if (
            cancel_wait
            and wait_task
            and wait_task is not current
            and not wait_task.done()
        ):
            wait_task.cancel()
            with suppress(asyncio.CancelledError):
                await wait_task

        await self._safe_disconnect(
            entry.get("client"),
            context=f"qr_close:{qr_id}",
            suppress_cancelled=True,
        )
        self._remove_session_files(entry.get("session_path", ""))
        entry["closed_at"] = (
            entry.get("closed_at") or time.monotonic()
        )
        if remove:
            self.pending.pop(qr_id, None)

    async def cancel(self, qr_id: str) -> None:
        await self.close_entry(qr_id, remove=True)
