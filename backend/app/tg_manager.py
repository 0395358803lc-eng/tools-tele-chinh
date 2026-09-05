"""Manage one Telethon client per account, with 777000 listeners."""
from __future__ import annotations
import asyncio
from contextlib import suppress
import logging
import secrets
import time
from pathlib import Path
from typing import Awaitable, Callable, Optional, TypeVar

from telethon import TelegramClient
from telethon.errors import (
    SessionPasswordNeededError,
    RPCError,
)
from telethon.tl.functions.account import UpdateProfileRequest, UpdateUsernameRequest
from telethon.tl.functions.photos import UploadProfilePhotoRequest
from telethon.tl.types import User as TgUser
from sqlalchemy import select

from .config import settings
from .account_scheduler import AccountActionScheduler
from .account_health import AccountHealthService
from .client_cleanup import TelegramClientCleanup
from .session_files import SessionFileStore
from .session_folder_sync import SessionFolderSyncService
from .phone_login import PhoneLoginService
from .security_messages import SecurityMessageService, SERVICE_ID
from .db import AsyncSessionLocal
from .models import Account, AppSetting, GoneAccount
from . import secrets_store
from .time_utils import utc_now_naive
from .tg_utils import (
    classify_777000,  # compatibility re-export for existing callers/tests
    permanent_connection_status,
    redact_login_code,
)

log = logging.getLogger("tg_manager")

T = TypeVar("T")


async def record_gone_account(db, acc: Account, reason: str):
    """Insert a GoneAccount tombstone for an account that is leaving the active
    list. Captures a snapshot plus `old_serial` = the account's 1-based rank
    among active (non-banned) accounts ordered by id, computed BEFORE the
    departure is committed. Does NOT commit — the caller owns the transaction."""
    res = await db.execute(
        select(Account.id).where(Account.status != "banned").order_by(Account.id)
    )
    ids = [row[0] for row in res.all()]
    try:
        serial = ids.index(acc.id) + 1
    except ValueError:
        serial = len(ids) + 1
    db.add(GoneAccount(
        account_id=acc.id,
        tg_user_id=acc.tg_user_id,
        phone=acc.phone,
        first_name=acc.first_name or "",
        last_name=acc.last_name or "",
        username=acc.username or "",
        old_serial=serial,
        reason=reason,
        gone_at=utc_now_naive(),
    ))




class TgClientManager:
    def __init__(self):
        self._clients: dict[int, TelegramClient] = {}  # account_id -> client
        self._qr_pending: dict[str, dict] = {}  # qr_id -> {'client', 'qr_login', 'wait_task', 'needs_2fa', 'session_path'}
        self._qr_locks: dict[str, asyncio.Lock] = {}
        self._qr_completed: dict[str, tuple[float, dict]] = {}
        # Per-account locks so two calls can't start/stop the SAME account at
        # once, while DIFFERENT accounts still connect concurrently (a single
        # global lock would serialize all 100+ accounts on boot).
        self._locks: dict[int, asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()
        # Telegram state-changing calls are delegated to a focused scheduler.
        # Lifecycle locks above still protect client creation/removal, while the
        # scheduler serializes mutations per account and handles FloodWait/pacing.
        self._action_scheduler = AccountActionScheduler(log)
        self._cleanup = TelegramClientCleanup(log)
        self._session_files = SessionFileStore(log)
        self._phone_login = PhoneLoginService(
            normalize_phone=self.normalize_phone,
            temp_session_path=self._phone_temp_session_path,
            safe_disconnect=self._safe_disconnect,
            remove_session_files=self._remove_session_files,
            phone_file_part=self._phone_file_part,
            logger=log,
        )
        self._security_messages = SecurityMessageService(log)
        self._session_folder_sync = SessionFolderSyncService(
            session_path_candidates=self._session_path_candidates,
            inspect_imported_session=self.inspect_imported_session,
            get_client=self.get,
            stop_client=self.stop_client,
            start_client=self.start_client,
            logger=log,
        )
        # Compatibility aliases for older internal tests/callers while focused
        # services own the actual collections.
        self._pending = self._phone_login.pending
        self._phone_locks = self._phone_login.locks
        self._service_handlers = self._security_messages.handlers
        self._new_msg_callbacks = self._security_messages.callbacks
        self.auto_reconnect = True
        self._reconnect_attempts: dict[int, int] = {}
        self._reconnect_at: dict[int, float] = {}
        self._reconnect_exhausted: set[int] = set()
        self._manual_disconnect: set[int] = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        # Compatibility aliases while SessionFolderSyncService owns scan state.
        self._session_scan_lock = self._session_folder_sync.lock
        self._session_scan_seen = self._session_folder_sync.seen
        self._background_tasks: set[asyncio.Task] = set()
        self._last_auth_check: dict[int, float] = {}
        self._auth_cursor: int = 0
        self._account_health = AccountHealthService(
            clients=self._clients,
            reconnect_attempts=self._reconnect_attempts,
            reconnect_at=self._reconnect_at,
            reconnect_exhausted=self._reconnect_exhausted,
            manual_disconnect=self._manual_disconnect,
            last_auth_check=self._last_auth_check,
            get_auth_cursor=lambda: self._auth_cursor,
            set_auth_cursor=lambda value: setattr(self, "_auth_cursor", value),
            auto_reconnect_enabled=lambda: self.auto_reconnect,
            set_status=lambda account_id, status: self._set_status(account_id, status),
            mark_banned=lambda account_id: self._mark_banned(account_id),
            start_client=lambda acc, reset_reconnect=False: self.start_client(
                acc,
                reset_reconnect=reset_reconnect,
            ),
            stop_client=lambda account_id: self.stop_client(account_id),
            logger=log,
        )

    def set_loop(self, loop):
        self._loop = loop

    async def _acc_lock(self, account_id: int) -> asyncio.Lock:
        async with self._locks_guard:
            lk = self._locks.get(account_id)
            if lk is None:
                lk = asyncio.Lock()
                self._locks[account_id] = lk
            return lk

    def get_action_lock(self, account_id: int) -> asyncio.Lock:
        """Compatibility wrapper for lifecycle code that shares mutation locks."""
        return self._action_scheduler.action_lock(account_id)

    async def wait_for_account_cooldown(self, account_id: int):
        await self._action_scheduler.wait_for_cooldown(account_id)

    def note_flood_wait(self, account_id: int, seconds: int | float):
        self._action_scheduler.note_flood_wait(account_id, seconds)

    def cooldown_remaining(self, account_id: int) -> float:
        return self._action_scheduler.cooldown_remaining(account_id)

    async def run_account_action(
        self,
        account_id: int,
        action: Callable[[], Awaitable[T]],
        operation: str = "telegram_mutation",
    ) -> T:
        """Run one Telegram mutation through the per-account scheduler."""
        return await self._action_scheduler.run(account_id, action, operation)

    def resume_actions(self):
        """Allow actions after startup (mainly useful for lifespan/tests)."""
        self._action_scheduler.resume()

    async def _safe_disconnect(
        self,
        cli,
        *,
        context: str,
        suppress_cancelled: bool = False,
    ) -> bool:
        return await self._cleanup.disconnect(
            cli,
            context=context,
            suppress_cancelled=suppress_cancelled,
        )

    def _safe_close_session(self, cli, *, context: str) -> bool:
        return self._cleanup.close_session(cli, context=context)

    def _safe_remove_event_handler(self, cli, handler, *, context: str) -> bool:
        return self._cleanup.remove_event_handler(cli, handler, context=context)

    # ---------- session-file compatibility wrappers ----------
    @staticmethod
    def normalize_phone(phone: str) -> str:
        return SessionFileStore.normalize_phone(phone)

    def _session_path(self, phone: str) -> str:
        return self._session_files.session_path(phone)

    def _phone_temp_session_path(self, phone: str) -> str:
        return self._session_files.phone_temp_session_path(phone)

    @staticmethod
    def _phone_file_part(phone: str) -> str:
        return SessionFileStore.phone_file_part(phone)

    @staticmethod
    def _username_file_part(username: str | None, user_id: int | None = None) -> str:
        return SessionFileStore.username_file_part(username, user_id)

    def session_file_name(
        self,
        phone: str,
        username: str | None = None,
        user_id: int | None = None,
    ) -> str:
        return self._session_files.session_file_name(phone, username, user_id)

    def _desired_session_path(
        self,
        phone: str,
        username: str | None = None,
        user_id: int | None = None,
    ) -> str:
        return self._session_files.desired_session_path(phone, username, user_id)

    def _path_from_session_file(self, session_file: str) -> str:
        return self._session_files.path_from_session_file(session_file)

    def _session_path_candidates(self, acc: Account) -> list[str]:
        return self._session_files.session_path_candidates(acc)

    def _session_path_for_account(self, acc: Account) -> str:
        return self._session_files.session_path_for_account(acc)

    def _move_session_files(self, src_base: str, dst_base: str):
        self._session_files.move_session_files(src_base, dst_base)

    def begin_session_swap(self, src_base: str, dst_base: str) -> dict:
        return self._session_files.begin_session_swap(src_base, dst_base)

    def rollback_session_swap(self, state: dict):
        self._session_files.rollback_session_swap(state)

    def commit_session_swap(self, state: dict, old_bases: list[str] | None = None):
        self._session_files.commit_session_swap(state, old_bases)

    def _remove_session_files(self, base: str):
        self._session_files.remove_session_files(base)

    async def promote_phone_session(self, phone: str, me: TgUser) -> str:
        dst = self._desired_session_path(
            phone, getattr(me, "username", None), getattr(me, "id", None)
        )
        self._session_files.move_session_files(self._session_path(phone), dst)
        return Path(dst).name

    async def inspect_imported_session(self, session_base: str) -> tuple[TgUser, str]:
        """Open an uploaded Telethon session and return its user + normalized phone.

        The caller owns moving or deleting the session files after this returns.
        """
        if not settings.api_configured:
            raise RuntimeError(
                "TG_API_ID / TG_API_HASH are not set in backend/.env — cannot inspect session.\n"
                "Get your credentials from https://my.telegram.org and fill them in."
            )
        cli = TelegramClient(session_base, settings.tg_api_id, settings.TG_API_HASH)
        try:
            await asyncio.wait_for(cli.connect(), timeout=20)
            if not await asyncio.wait_for(cli.is_user_authorized(), timeout=20):
                raise RuntimeError("Session is not authorized")
            me = await asyncio.wait_for(cli.get_me(), timeout=30)
            if not me:
                raise RuntimeError("Could not read account info from this session")
            phone = getattr(me, "phone", None)
            if not phone:
                raise RuntimeError("Telegram did not return a phone number for this session")
            phone = phone if phone.startswith("+") else f"+{phone}"
            return me, phone
        finally:
            await self._safe_disconnect(cli, context="inspect_imported_session")
            self._safe_close_session(cli, context="inspect_imported_session")

    async def promote_imported_session(self, session_base: str, phone: str, me: TgUser) -> str:
        dst = self._desired_session_path(
            phone, getattr(me, "username", None), getattr(me, "id", None)
        )
        self._session_files.move_session_files(session_base, dst)
        if not Path(dst + ".session").exists():
            raise RuntimeError("Imported session file could not be saved")
        return Path(dst).name

    @staticmethod
    def _session_error_detail(exc: Exception) -> str:
        return SessionFolderSyncService.session_error_detail(exc)

    @staticmethod
    def _is_importable_session_file(path: Path) -> bool:
        return SessionFolderSyncService.is_importable_session_file(path)

    async def sync_session_folder(self, force: bool = False) -> dict:
        return await self._session_folder_sync.sync(force=force)

    def get(self, account_id: int) -> Optional[TelegramClient]:
        return self._clients.get(account_id)

    async def all_clients(self) -> dict[int, TelegramClient]:
        return dict(self._clients)

    # ---------- lifecycle ----------
    async def redact_stored_login_codes(self):
        return await self._security_messages.redact_stored_login_codes()

    async def load_runtime_settings(self):
        """Apply persisted settings before any Telegram client is created."""
        async with AsyncSessionLocal() as db:
            res = await db.execute(select(AppSetting))
            values = {row.key: row.value for row in res.scalars().all()}

        def _float(name: str, current: float) -> float:
            try:
                return float(values.get(name, current))
            except (TypeError, ValueError):
                return current

        def _int(name: str, current: int) -> int:
            try:
                return max(1, int(float(values.get(name, current))))
            except (TypeError, ValueError):
                return current

        settings.RATE_MIN = max(0.0, _float("rate_min", settings.RATE_MIN))
        settings.RATE_MAX = max(settings.RATE_MIN, _float("rate_max", settings.RATE_MAX))
        settings.CONCURRENCY = _int("concurrency", settings.CONCURRENCY)
        self.auto_reconnect = values.get("auto_reconnect", "true").lower() == "true"

    def _background_task_done(self, task: asyncio.Task, *, name: str):
        """Remove a finished task and report unexpected task failure safely."""
        self._background_tasks.discard(task)
        if task.cancelled():
            return
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            return
        except Exception as probe_exc:
            log.warning(
                "background task inspection failed name=%s error_type=%s",
                name,
                type(probe_exc).__name__,
            )
            return
        if exc is not None:
            log.warning(
                "background task failed name=%s error_type=%s",
                name,
                type(exc).__name__,
            )

    def _track_background_task(self, task: asyncio.Task, *, name: str) -> asyncio.Task:
        self._background_tasks.add(task)
        task.add_done_callback(
            lambda done, task_name=name: self._background_task_done(
                done,
                name=task_name,
            )
        )
        return task

    async def startup_load_all(self):
        """Load settings and optionally connect previously-authorized accounts."""
        await self.redact_stored_login_codes()
        await self.load_runtime_settings()
        self.resume_actions()
        if not self.auto_reconnect:
            async with AsyncSessionLocal() as db:
                res = await db.execute(select(Account))
                for acc in res.scalars().all():
                    if acc.status not in {"banned", "session_revoked", "auth_error"}:
                        acc.status = "disconnected"
                await db.commit()
            log.info("auto_reconnect is disabled; Telegram clients remain disconnected")
            return
        async with AsyncSessionLocal() as db:
            res = await db.execute(select(Account))
            accounts = res.scalars().all()
        conc = max(1, getattr(settings, "STARTUP_CONCURRENCY", 10))
        sem = asyncio.Semaphore(conc)

        async def _start_one(acc: Account):
            async with sem:
                try:
                    await self.start_client(acc)
                except Exception as exc:
                    log.warning(
                        "startup client failed account=%s error_type=%s",
                        acc.id,
                        type(exc).__name__,
                    )

        eligible = [
            acc for acc in accounts
            if acc.status not in {"banned", "session_revoked", "auth_error"}
        ]
        await asyncio.gather(*(_start_one(acc) for acc in eligible))

        self._track_background_task(
            asyncio.create_task(self._pending_janitor()),
            name="pending_janitor",
        )

        async def _sync_pasted_sessions():
            try:
                report = await self.sync_session_folder()
                if report.get("success") or report.get("failed"):
                    log.info(
                        "session folder sync: %s imported, %s failed, %s skipped",
                        report.get("success", 0),
                        report.get("failed", 0),
                        report.get("skipped", 0),
                    )
            except Exception as exc:
                log.warning(
                    "session folder sync failed error_type=%s",
                    type(exc).__name__,
                )

        self._track_background_task(
            asyncio.create_task(_sync_pasted_sessions()),
            name="session_folder_sync",
        )

    async def shutdown(self, action_timeout: float = 30.0):
        self._action_scheduler.stop_accepting()
        background = list(self._background_tasks)
        for task in background:
            task.cancel()
        if background:
            await asyncio.gather(*background, return_exceptions=True)
        self._background_tasks.clear()
        if not await self._action_scheduler.wait_idle(action_timeout):
            log.warning(
                "graceful shutdown timed out with %s Telegram action(s) pending",
                self._action_scheduler.active_actions,
            )
        for cli in list(self._clients.values()):
            await self._safe_disconnect(cli, context="shutdown:active_client")
        self._security_messages.clear_handlers()
        for pend in list(self._pending.values()):
            await self._safe_disconnect(
                pend.get('client'),
                context="shutdown:pending_phone",
                suppress_cancelled=True,
            )
            self._remove_session_files(pend.get('session_path', ''))
        for qr in list(self._qr_pending.values()):
            t = qr.get('wait_task')
            if t and not t.done():
                t.cancel()
                with suppress(asyncio.CancelledError):
                    await t
            await self._safe_disconnect(
                qr.get('client'),
                context="shutdown:pending_qr",
                suppress_cancelled=True,
            )
            self._remove_session_files(qr.get('session_path', ''))
        self._clients.clear()
        self._pending.clear()
        self._qr_pending.clear()
        self._qr_completed.clear()
        self._qr_locks.clear()

    @staticmethod
    def _permanent_status(exc: Exception) -> str | None:
        return permanent_connection_status(exc)

    def _reset_reconnect(self, account_id: int):
        return self._account_health.reset_reconnect(account_id)

    async def _record_connection_failure(self, account_id: int, exc: Exception):
        return await self._account_health.record_connection_failure(
            account_id,
            exc,
        )

    async def start_client(self, acc: Account, *, reset_reconnect: bool = True) -> TelegramClient:
        if not settings.api_configured:
            raise RuntimeError(
                "TG_API_ID / TG_API_HASH are not set in backend/.env — cannot connect.\n"
                "Get your credentials from https://my.telegram.org and fill them in."
            )
        # Serialize against in-flight mutations AND concurrent lifecycle on the
        # same account. The action lock is taken FIRST so the lock ordering
        # matches stop_client / run_account_action (action -> lifecycle) and
        # can never deadlock with a mutation that is tearing this client down.
        async with self.get_action_lock(acc.id):
            return await self._start_client_locked(acc, reset_reconnect=reset_reconnect)

    async def _start_client_locked(self, acc: Account, *, reset_reconnect: bool) -> TelegramClient:
        async with await self._acc_lock(acc.id):
            if reset_reconnect:
                self._reset_reconnect(acc.id)
                self._manual_disconnect.discard(acc.id)
            cli = self._clients.get(acc.id)
            if cli and cli.is_connected():
                await self._set_status(acc.id, "connected")
                return cli
            if cli is None:
                cli = TelegramClient(
                    self._session_path_for_account(acc),
                    settings.tg_api_id,
                    settings.TG_API_HASH,
                    auto_reconnect=False,
                )
            try:
                await asyncio.wait_for(
                    cli.connect(), timeout=float(settings.TELEGRAM_CONNECT_TIMEOUT)
                )
                authorized = await asyncio.wait_for(
                    cli.is_user_authorized(), timeout=float(settings.TELEGRAM_AUTH_TIMEOUT)
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
            # Sync profile best-effort for Telegram/network failures. Database or
            # programming errors are intentionally not swallowed.
            try:
                me = await asyncio.wait_for(
                    cli.get_me(), timeout=float(settings.TELEGRAM_AUTH_TIMEOUT)
                )
            except (RPCError, asyncio.TimeoutError, ConnectionError, OSError) as exc:
                log.warning(
                    "account=%s profile refresh skipped error=%s",
                    acc.id,
                    type(exc).__name__,
                )
            else:
                await self._sync_profile(acc.id, me)
            # backfill recent 777000 messages we may have missed while offline
            try:
                await asyncio.wait_for(
                    self._backfill_777000(acc.id, cli, limit=50), timeout=30
                )
            except Exception as exc:
                log.warning(
                    "startup backfill failed account=%s error_type=%s",
                    acc.id,
                    type(exc).__name__,
                )
            return cli

    async def stop_client(self, account_id: int):
        # Never disconnect a session while one of its mutations is in flight.
        async with self.get_action_lock(account_id):
            lock = await self._acc_lock(account_id)
            async with lock:
                cli = self._clients.pop(account_id, None)
            if cli:
                self._security_messages.detach(
                    account_id,
                    context=f"stop_client:{account_id}",
                )
                await self._safe_disconnect(
                    cli,
                    context=f"stop_client:{account_id}",
                )

    async def disconnect_account(self, account_id: int):
        """User-requested disconnect; auto-reconnect must not undo it."""
        self._manual_disconnect.add(account_id)
        self._reset_reconnect(account_id)
        await self.stop_client(account_id)
        await self._set_status(account_id, "disconnected")

    async def remove_account_instance(self, acc: Account, delete_session_file: bool = True):
        await self.stop_client(acc.id)
        if delete_session_file:
            for base in self._session_path_candidates(acc):
                self._remove_session_files(base)

    async def remove_account(self, account_id: int, delete_session_file: bool = True):
        if delete_session_file:
            async with AsyncSessionLocal() as db:
                acc = await db.get(Account, account_id)
                if acc:
                    await self.remove_account_instance(acc, delete_session_file=True)
                else:
                    await self.stop_client(account_id)
        else:
            await self.stop_client(account_id)

    # ---------- auth flow ----------
    # Pending logins are clients that successfully sent a code OR successfully
    # passed the code step but need a 2FA password. Keyed by phone.
    # Each entry: { 'client': TelegramClient, 'phone_code_hash': str, 'needs_2fa': bool }

    async def _pending_janitor(self):
        """Bound abandoned phone/QR login state and clean temporary sessions."""
        while True:
            await asyncio.sleep(15)
            await self.cleanup_expired_pending()

    async def cleanup_expired_pending(self):
        now = time.monotonic()
        await self._phone_login.cleanup_expired()
        for qr_id, entry in list(self._qr_pending.items()):
            if entry.get('expires_at', 0) <= now:
                entry['error'] = entry.get('error') or "QR code expired"
                await self._close_qr_entry(qr_id, remove=False)
                # Keep a terminal status briefly for the poller, then evict it.
                if entry.get('closed_at', now) + 30 <= now:
                    self._qr_pending.pop(qr_id, None)
                    self._qr_locks.pop(qr_id, None)
        completed_ttl = max(30, int(settings.QR_PENDING_TTL_SECONDS))
        for qr_id, (finished_at, _payload) in list(self._qr_completed.items()):
            if finished_at + completed_ttl <= now:
                self._qr_completed.pop(qr_id, None)
                self._qr_locks.pop(qr_id, None)

    async def send_code(self, phone: str) -> str:
        await self.cleanup_expired_pending()
        return await self._phone_login.send_code(phone)

    async def submit_code(
        self,
        phone: str,
        code: str,
    ) -> tuple[TgUser | None, bool]:
        await self.cleanup_expired_pending()
        return await self._phone_login.submit_code(phone, code)

    async def submit_2fa(self, phone: str, password: str) -> TgUser:
        await self.cleanup_expired_pending()
        return await self._phone_login.submit_2fa(phone, password)

    async def cancel_pending(self, phone: str):
        return await self._phone_login.cancel(phone)

    def phone_session_source(self, phone: str) -> str:
        return self._phone_login.session_source(phone)

    async def finish_phone_login(self, phone: str, *, remove_session: bool):
        return await self._phone_login.finish(
            phone,
            remove_session=remove_session,
        )

    async def _kill_pending(
        self,
        phone: str,
        disconnect: bool = True,
        remove_session: bool = True,
    ):
        return await self._phone_login.kill_pending(
            phone,
            disconnect=disconnect,
            remove_session=remove_session,
        )

    # ---------- QR login flow ----------
    # Telethon's `qr_login()` returns a QRLogin object. Its `url` field is a
    # tg://login?token=... string that the official Telegram mobile app scans
    # in Settings -> Devices -> Link Desktop Device. We expose this URL to the
    # frontend, which renders it as a QR image. We poll qr.wait() in a task
    # and mark the pending entry done/failed/needs_2fa accordingly.

    def _qr_session_path(self, qr_id: str) -> str:
        return str(settings.sessions_path / f"qr_{qr_id}")

    async def qr_start(self) -> dict:
        """Begin a new QR login. Returns {qr_id, url, expires_at}."""
        if not settings.api_configured:
            raise RuntimeError(
                "TG_API_ID / TG_API_HASH are not set in backend/.env — cannot start QR login.\n"
                "Get your credentials from https://my.telegram.org and fill them in."
            )
        qr_id = secrets.token_urlsafe(12)
        sess_path = self._qr_session_path(qr_id)
        cli = TelegramClient(sess_path, settings.tg_api_id, settings.TG_API_HASH)
        keep = False
        try:
            await asyncio.wait_for(
                cli.connect(), timeout=float(settings.TELEGRAM_CONNECT_TIMEOUT)
            )
            qr_login = await asyncio.wait_for(cli.qr_login(), timeout=30)
            entry = {
                'client': cli,
                'qr_login': qr_login,
                'wait_task': None,
                'needs_2fa': False,
                'authorized': False,
                'error': None,
                'me': None,
                'session_path': sess_path,
                'expires_at': time.monotonic() + max(30, int(settings.QR_PENDING_TTL_SECONDS)),
                'closed_at': None,
            }
            self._qr_pending[qr_id] = entry
            entry['wait_task'] = asyncio.create_task(self._qr_wait(qr_id))
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
            'qr_id': qr_id,
            'url': qr_login.url,
            'expires_at': qr_login.expires.isoformat() if qr_login.expires else None,
        }

    async def _qr_wait(self, qr_id: str):
        """Background task that waits for QR scan -> auth completion."""
        entry = self._qr_pending.get(qr_id)
        if not entry:
            return
        cli: TelegramClient = entry['client']
        qr = entry['qr_login']
        try:
            await qr.wait()
            try:
                me = await cli.get_me()
                entry['me'] = me
                entry['authorized'] = True
            except Exception as exc:
                log.warning(
                    "QR login user lookup failed qr_id=%s error=%s",
                    qr_id,
                    type(exc).__name__,
                )
                entry['error'] = "Could not read Telegram user information"
                await self._close_qr_entry(qr_id, remove=False, cancel_wait=False)
        except SessionPasswordNeededError:
            entry['needs_2fa'] = True
        except asyncio.TimeoutError:
            entry['error'] = "QR code expired"
            await self._close_qr_entry(qr_id, remove=False, cancel_wait=False)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning(
                "QR login wait failed qr_id=%s error=%s",
                qr_id,
                type(exc).__name__,
            )
            entry['error'] = "QR login failed"
            await self._close_qr_entry(qr_id, remove=False, cancel_wait=False)

    async def qr_recreate(self, qr_id: str) -> dict:
        """Refresh the QR token within an existing pending entry (same client)."""
        entry = self._qr_pending.get(qr_id)
        if not entry:
            raise RuntimeError("QR session not found")
        await self.cleanup_expired_pending()
        entry = self._qr_pending.get(qr_id)
        if not entry:
            raise RuntimeError("QR session not found")
        # cancel the old wait task before issuing a new qr_login
        old = entry.get('wait_task')
        if old and not old.done():
            old.cancel()
            with suppress(asyncio.CancelledError):
                await old
        cli: TelegramClient = entry['client']
        if entry.get('closed_at') is not None or not cli.is_connected():
            await self._safe_disconnect(
                cli,
                context=f"qr_recreate_closed:{qr_id}",
                suppress_cancelled=True,
            )
            self._remove_session_files(entry['session_path'])
            cli = TelegramClient(
                entry['session_path'], settings.tg_api_id, settings.TG_API_HASH
            )
            try:
                await asyncio.wait_for(
                    cli.connect(), timeout=float(settings.TELEGRAM_CONNECT_TIMEOUT)
                )
            except BaseException:
                await self._safe_disconnect(
                    cli,
                    context=f"qr_recreate_connect_failed:{qr_id}",
                    suppress_cancelled=True,
                )
                self._remove_session_files(entry['session_path'])
                raise
            entry['client'] = cli
        try:
            qr_login = await asyncio.wait_for(cli.qr_login(), timeout=30)
        except BaseException:
            entry['error'] = "QR login failed"
            await self._close_qr_entry(qr_id, remove=False, cancel_wait=False)
            raise
        entry['qr_login'] = qr_login
        entry['error'] = None
        entry['authorized'] = False
        entry['needs_2fa'] = False
        entry['closed_at'] = None
        entry['expires_at'] = time.monotonic() + max(30, int(settings.QR_PENDING_TTL_SECONDS))
        entry['wait_task'] = asyncio.create_task(self._qr_wait(qr_id))
        return {
            'qr_id': qr_id,
            'url': qr_login.url,
            'expires_at': qr_login.expires.isoformat() if qr_login.expires else None,
        }

    async def qr_status(self, qr_id: str) -> dict:
        completed = self._qr_completed.get(qr_id)
        if completed:
            return {'state': 'finalized', **completed[1]}
        await self.cleanup_expired_pending()
        entry = self._qr_pending.get(qr_id)
        if not entry:
            return {'state': 'missing'}
        if entry['authorized']:
            return {'state': 'authorized'}
        if entry['needs_2fa']:
            return {'state': 'needs_2fa'}
        if entry['error'] == 'QR code expired':
            return {'state': 'expired'}
        if entry['error']:
            return {'state': 'error', 'error': entry['error']}
        return {'state': 'waiting'}

    async def qr_finalize(self, qr_id: str):
        """After authorized, return (me, session_path) so the caller can persist
        the account and rename the session file to phone-keyed naming."""
        entry = self._qr_pending.get(qr_id)
        if not entry or not entry['authorized']:
            raise RuntimeError("QR not authorized")
        return entry['me'], entry['client'], entry['session_path']

    def qr_finalize_lock(self, qr_id: str) -> asyncio.Lock:
        return self._qr_locks.setdefault(qr_id, asyncio.Lock())

    def qr_completed(self, qr_id: str) -> dict | None:
        item = self._qr_completed.get(qr_id)
        return item[1] if item else None

    def mark_qr_completed(self, qr_id: str, payload: dict):
        self._qr_completed[qr_id] = (time.monotonic(), payload)

    async def qr_submit_2fa(self, qr_id: str, password: str):
        entry = self._qr_pending.get(qr_id)
        if not entry:
            raise RuntimeError("QR session not found")
        if not entry['needs_2fa']:
            raise RuntimeError("QR session does not require 2FA")
        cli: TelegramClient = entry['client']
        await asyncio.wait_for(cli.sign_in(password=password), timeout=30)
        me = await cli.get_me()
        entry['authorized'] = True
        entry['me'] = me
        # Remember this 2FA password locally (keyed by the account's phone).
        try:
            if getattr(me, "phone", None):
                await secrets_store.save_2fa(me.phone, password)
        except (OSError, RuntimeError, ValueError) as exc:
            log.warning(
                "qr secure 2FA password save failed error=%s",
                type(exc).__name__,
            )
        return me

    async def qr_promote_to_phone(self, qr_id: str, phone: str):
        """Move the QR-temp session file to the canonical acc_<phone>.session
        path and disconnect the temp client. Returns the new path."""
        entry = self._qr_pending.get(qr_id)
        if not entry:
            raise RuntimeError("QR session not found")
        wait_task = entry.get('wait_task')
        if wait_task and not wait_task.done():
            wait_task.cancel()
            with suppress(asyncio.CancelledError):
                await wait_task
        await self._safe_disconnect(
            entry.get('client'),
            context=f"qr_promote:{qr_id}",
            suppress_cancelled=True,
        )
        return entry['session_path']

    async def finish_qr(self, qr_id: str, *, remove_session: bool):
        entry = self._qr_pending.pop(qr_id, None)
        if entry and remove_session:
            self._remove_session_files(entry.get('session_path', ''))

    async def _close_qr_entry(
        self, qr_id: str, *, remove: bool, cancel_wait: bool = True
    ):
        entry = self._qr_pending.get(qr_id)
        if not entry:
            return
        wait_task = entry.get('wait_task')
        current = asyncio.current_task()
        if cancel_wait and wait_task and wait_task is not current and not wait_task.done():
            wait_task.cancel()
            with suppress(asyncio.CancelledError):
                await wait_task
        await self._safe_disconnect(
            entry.get('client'),
            context=f"qr_close:{qr_id}",
            suppress_cancelled=True,
        )
        self._remove_session_files(entry.get('session_path', ''))
        entry['closed_at'] = entry.get('closed_at') or time.monotonic()
        if remove:
            self._qr_pending.pop(qr_id, None)

    async def qr_cancel(self, qr_id: str):
        await self._close_qr_entry(qr_id, remove=True)

    def _safe_unlink(self, path: str):
        self._session_files.safe_unlink(path)

    # ---------- 777000 security messages ----------
    def _attach_listener(self, account_id: int, cli: TelegramClient):
        return self._security_messages.attach(account_id, cli)

    def subscribe_new_messages(self, cb):
        return self._security_messages.subscribe(cb)

    def unsubscribe_new_messages(self, cb):
        return self._security_messages.unsubscribe(cb)

    # ---------- DB helpers ----------
    async def _set_status(self, account_id: int, status: str):
        async with AsyncSessionLocal() as db:
            acc = await db.get(Account, account_id)
            if acc:
                acc.status = status
                await db.commit()

    async def _mark_banned(self, account_id: int):
        """Transition an account to 'banned' and, on the FIRST such transition,
        log a GoneAccount tombstone. Guarded on the previous status so the 30s
        status loop doesn't re-log a banned account every cycle."""
        async with AsyncSessionLocal() as db:
            acc = await db.get(Account, account_id)
            if not acc or acc.status == "banned":
                return
            await record_gone_account(db, acc, "banned")
            acc.status = "banned"
            await db.commit()

    async def _sync_profile(self, account_id: int, me: TgUser):
        async with AsyncSessionLocal() as db:
            acc = await db.get(Account, account_id)
            if not acc:
                return
            acc.first_name = me.first_name or ""
            acc.last_name = me.last_name or ""
            acc.username = me.username or ""
            acc.tg_user_id = me.id
            # 2FA detection is best-effort for Telegram/network failures. Keep DB
            # failures visible instead of silently turning them into stale profile data.
            try:
                cli = self._clients.get(account_id)
                if cli:
                    from telethon.tl.functions.account import GetPasswordRequest
                    pw = await cli(GetPasswordRequest())
                    acc.has_2fa = bool(pw.has_password)
            except (RPCError, ConnectionError, OSError) as exc:
                log.debug(
                    "account=%s 2FA status refresh skipped error=%s",
                    account_id,
                    type(exc).__name__,
                )
            await db.commit()

    async def _backfill_777000(
        self,
        account_id: int,
        cli: TelegramClient,
        limit: int = 50,
    ):
        return await self._security_messages.backfill(account_id, cli, limit)

    async def refresh_status_all(self):
        return await self._account_health.refresh_status_all()

    async def verify_authorizations_all(self) -> int:
        return await self._account_health.verify_authorizations_all(
            reset_reconnect=self._reset_reconnect,
        )


manager = TgClientManager()
