"""Phone-based Telegram login lifecycle and pending-session state."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.tl.types import User as TgUser

from . import secrets_store
from .config import settings


class PhoneLoginService:
    """Own pending phone-login clients while the manager remains the facade."""

    def __init__(
        self,
        *,
        normalize_phone: Callable[[str], str],
        temp_session_path: Callable[[str], str],
        safe_disconnect: Callable[..., Awaitable[None]],
        remove_session_files: Callable[[str], None],
        phone_file_part: Callable[[str], str],
        logger: logging.Logger | None = None,
    ):
        self._normalize_phone = normalize_phone
        self._temp_session_path = temp_session_path
        self._safe_disconnect = safe_disconnect
        self._remove_session_files = remove_session_files
        self._phone_file_part = phone_file_part
        self._log = logger or logging.getLogger("phone_login")
        self.pending: dict[str, dict] = {}
        self.locks: dict[str, asyncio.Lock] = {}

    async def cleanup_expired(self) -> None:
        now = time.monotonic()
        for phone, entry in list(self.pending.items()):
            if entry.get("expires_at", 0) <= now:
                await self.kill_pending(phone)

    async def send_code(self, phone: str) -> str:
        if not settings.api_configured:
            raise RuntimeError(
                "TG_API_ID / TG_API_HASH are not set in backend/.env — cannot request a code.\n"
                "Get your credentials from https://my.telegram.org and fill them in."
            )
        phone = self._normalize_phone(phone)
        lock = self.locks.setdefault(phone, asyncio.Lock())
        async with lock:
            if phone in self.pending:
                raise RuntimeError("A login request is already pending for this phone")
            session_path = self._temp_session_path(phone)
            cli = TelegramClient(
                session_path,
                settings.tg_api_id,
                settings.TG_API_HASH,
            )
            keep = False
            try:
                await asyncio.wait_for(
                    cli.connect(),
                    timeout=float(settings.TELEGRAM_CONNECT_TIMEOUT),
                )
                sent = await asyncio.wait_for(
                    cli.send_code_request(phone),
                    timeout=30,
                )
                self.pending[phone] = {
                    "client": cli,
                    "phone_code_hash": sent.phone_code_hash,
                    "needs_2fa": False,
                    "authorized": False,
                    "session_path": session_path,
                    "expires_at": (
                        time.monotonic()
                        + max(30, int(settings.LOGIN_PENDING_TTL_SECONDS))
                    ),
                }
                keep = True
                return sent.phone_code_hash
            finally:
                if not keep:
                    await self._safe_disconnect(
                        cli,
                        context=f"send_code_cleanup:{phone}",
                        suppress_cancelled=True,
                    )
                    self._remove_session_files(session_path)

    async def submit_code(
        self,
        phone: str,
        code: str,
    ) -> tuple[TgUser | None, bool]:
        """Return (user, needs_2fa), retaining the temp session for promotion."""
        phone = self._normalize_phone(phone)
        pend = self.pending.get(phone)
        if not pend:
            raise RuntimeError("No pending login. Send code first.")
        cli: TelegramClient = pend["client"]
        try:
            await asyncio.wait_for(
                cli.sign_in(
                    phone=phone,
                    code=code,
                    phone_code_hash=pend["phone_code_hash"],
                ),
                timeout=30,
            )
        except SessionPasswordNeededError:
            pend["needs_2fa"] = True
            return None, True
        except Exception:
            # Hard code/auth failures discard the temporary login so the caller
            # can request a fresh code. Preserve the original exception.
            await self.kill_pending(phone)
            raise

        me = await cli.get_me()
        pend["authorized"] = True
        pend["me"] = me
        await self._safe_disconnect(
            cli,
            context=f"submit_code_complete:{phone}",
            suppress_cancelled=True,
        )
        return me, False

    async def submit_2fa(self, phone: str, password: str) -> TgUser:
        phone = self._normalize_phone(phone)
        pend = self.pending.get(phone)
        if not pend:
            raise RuntimeError("No pending 2FA session. Send code first.")
        cli: TelegramClient = pend["client"]

        # Keep pending state on authentication/network failure so the user can
        # retry until TTL cleanup. Do not catch sign-in failures here.
        await asyncio.wait_for(cli.sign_in(password=password), timeout=30)
        me = await cli.get_me()

        try:
            await secrets_store.save_2fa(phone, password)
        except (OSError, RuntimeError, ValueError) as exc:
            self._log.warning(
                "phone=%s secure 2FA password save failed error=%s",
                self._phone_file_part(phone)[-4:],
                type(exc).__name__,
            )

        pend["authorized"] = True
        pend["me"] = me
        await self._safe_disconnect(
            cli,
            context=f"submit_2fa_complete:{phone}",
            suppress_cancelled=True,
        )
        return me

    async def cancel(self, phone: str) -> None:
        await self.kill_pending(self._normalize_phone(phone))

    def session_source(self, phone: str) -> str:
        phone = self._normalize_phone(phone)
        entry = self.pending.get(phone)
        if not entry or not entry.get("authorized"):
            raise RuntimeError("Phone login session is not ready")
        return entry["session_path"]

    async def finish(self, phone: str, *, remove_session: bool) -> None:
        await self.kill_pending(
            self._normalize_phone(phone),
            remove_session=remove_session,
        )

    async def kill_pending(
        self,
        phone: str,
        disconnect: bool = True,
        remove_session: bool = True,
    ) -> None:
        pend = self.pending.pop(phone, None)
        if pend and disconnect:
            await self._safe_disconnect(
                pend.get("client"),
                context=f"kill_pending:{phone}",
                suppress_cancelled=True,
            )
        if pend and remove_session:
            self._remove_session_files(pend.get("session_path", ""))
