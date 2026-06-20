"""Manage one Telethon client per account, with 777000 listeners."""
from __future__ import annotations
import asyncio
import logging
import re
import secrets
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from telethon import TelegramClient, events
from telethon.errors import (
    AuthKeyUnregisteredError,
    UserDeactivatedBanError,
    UserDeactivatedError,
    SessionPasswordNeededError,
    RPCError,
)
from telethon.tl.functions.account import UpdateProfileRequest, UpdateUsernameRequest
from telethon.tl.functions.photos import UploadProfilePhotoRequest
from telethon.tl.types import User as TgUser
from sqlalchemy import select

from .config import settings
from .db import AsyncSessionLocal
from .models import Account, SecurityMessage, GoneAccount
from . import secrets_store

log = logging.getLogger("tg_manager")

SERVICE_ID = 777000


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
        gone_at=datetime.utcnow(),
    ))


def classify_777000(text: str) -> str:
    low = text.lower()
    if re.search(r"login code|\b\d{5}\b", low):
        return "login_code"
    if "new login" in low or "new device" in low:
        return "new_login"
    if "two-step" in low or "password" in low:
        return "2fa_change"
    if "delete" in low or "deactivation" in low:
        return "account_deletion"
    return "unknown"


class TgClientManager:
    def __init__(self):
        self._clients: dict[int, TelegramClient] = {}  # account_id -> client
        self._pending: dict[str, dict] = {}  # phone -> {'client', 'phone_code_hash', 'needs_2fa'}
        self._qr_pending: dict[str, dict] = {}  # qr_id -> {'client', 'qr_login', 'wait_task', 'needs_2fa', 'session_path'}
        # Per-account locks so two calls can't start/stop the SAME account at
        # once, while DIFFERENT accounts still connect concurrently (a single
        # global lock would serialize all 100+ accounts on boot).
        self._locks: dict[int, asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._new_msg_callbacks: list = []
        self._session_scan_lock = asyncio.Lock()
        self._session_scan_seen: dict[str, tuple[int, int]] = {}

    def set_loop(self, loop):
        self._loop = loop

    async def _acc_lock(self, account_id: int) -> asyncio.Lock:
        async with self._locks_guard:
            lk = self._locks.get(account_id)
            if lk is None:
                lk = asyncio.Lock()
                self._locks[account_id] = lk
            return lk

    # ---------- helpers ----------
    def _session_path(self, phone: str) -> str:
        safe = re.sub(r"[^0-9]", "", phone)
        return str(settings.sessions_path / f"acc_{safe}")

    @staticmethod
    def _phone_file_part(phone: str) -> str:
        digits = re.sub(r"\D", "", phone or "")
        return digits or "unknown"

    @staticmethod
    def _username_file_part(username: str | None, user_id: int | None = None) -> str:
        user = re.sub(r"[^A-Za-z0-9_]", "", (username or "").strip().lstrip("@"))
        if user:
            return user[:64]
        if user_id:
            return f"user{user_id}"
        return "no_username"

    def session_file_name(self, phone: str, username: str | None = None, user_id: int | None = None) -> str:
        return f"{self._username_file_part(username, user_id)}_{self._phone_file_part(phone)}"

    def _desired_session_path(self, phone: str, username: str | None = None, user_id: int | None = None) -> str:
        return str(settings.sessions_path / self.session_file_name(phone, username, user_id))

    def _path_from_session_file(self, session_file: str) -> str:
        p = Path(session_file or "")
        if p.suffix == ".session":
            p = p.with_suffix("")
        if p.is_absolute():
            return str(p)
        return str(settings.sessions_path / p.name)

    def _session_path_candidates(self, acc: Account) -> list[str]:
        candidates = []
        if acc.session_file:
            candidates.append(self._path_from_session_file(acc.session_file))
        candidates.append(self._desired_session_path(acc.phone, acc.username, acc.tg_user_id))
        candidates.append(self._path_from_session_file(f"acc_{acc.phone}"))
        candidates.append(self._session_path(acc.phone))

        unique = []
        seen = set()
        for c in candidates:
            if c and c not in seen:
                unique.append(c)
                seen.add(c)
        return unique

    def _session_path_for_account(self, acc: Account) -> str:
        candidates = self._session_path_candidates(acc)
        for c in candidates:
            if Path(c + ".session").exists():
                return c
        return candidates[0]

    def _move_session_files(self, src_base: str, dst_base: str):
        src = Path(src_base)
        dst = Path(dst_base)
        if src.resolve() == dst.resolve():
            return

        suffixes = [".session", ".session-journal", ".session-wal", ".session-shm"]
        if not any(Path(str(src) + suffix).exists() for suffix in suffixes):
            return

        dst.parent.mkdir(parents=True, exist_ok=True)
        for suffix in suffixes:
            self._safe_unlink(str(dst) + suffix)
        for suffix in suffixes:
            s = Path(str(src) + suffix)
            if not s.exists():
                continue
            d = Path(str(dst) + suffix)
            shutil.move(str(s), str(d))

    def _remove_session_files(self, base: str):
        for suffix in [".session", ".session-journal", ".session-wal", ".session-shm"]:
            self._safe_unlink(base + suffix)

    async def promote_phone_session(self, phone: str, me: TgUser) -> str:
        dst = self._desired_session_path(phone, getattr(me, "username", None), getattr(me, "id", None))
        self._move_session_files(self._session_path(phone), dst)
        return Path(dst).name

    async def inspect_imported_session(self, session_base: str) -> tuple[TgUser, str]:
        """Open an uploaded Telethon session and return its user + normalized phone.

        The caller owns moving or deleting the session files after this returns.
        """
        cli = TelegramClient(session_base, settings.TG_API_ID, settings.TG_API_HASH)
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
            try:
                await cli.disconnect()
            except Exception:
                pass
            try:
                cli.session.close()
            except Exception:
                pass

    async def promote_imported_session(self, session_base: str, phone: str, me: TgUser) -> str:
        dst = self._desired_session_path(phone, getattr(me, "username", None), getattr(me, "id", None))
        self._move_session_files(session_base, dst)
        if not Path(dst + ".session").exists():
            raise RuntimeError("Imported session file could not be saved")
        return Path(dst).name

    @staticmethod
    def _session_error_detail(e: Exception) -> str:
        if type(e).__name__ == "RuntimeError":
            return str(e) or "Session import failed"
        msg = str(e)
        return f"{type(e).__name__}: {msg[:140]}" if msg else type(e).__name__

    @staticmethod
    def _is_importable_session_file(path: Path) -> bool:
        if path.suffix.lower() != ".session":
            return False
        stem = path.stem.lower()
        return not (stem.startswith("qr_") or stem.startswith("mtm_import_"))

    async def sync_session_folder(self, force: bool = False) -> dict:
        """Discover authorized .session files pasted into the sessions folder.

        This fills missing Account rows without asking for a login code. It does
        not create a new Telegram auth key; it only reuses session files that are
        already authorized.
        """
        async with self._session_scan_lock:
            session_files = [
                p for p in sorted(settings.sessions_path.glob("*.session"))
                if self._is_importable_session_file(p)
            ]

            async with AsyncSessionLocal() as db:
                res = await db.execute(select(Account))
                accounts = list(res.scalars().all())

            known_paths: set[str] = set()
            for acc in accounts:
                for base in self._session_path_candidates(acc):
                    try:
                        known_paths.add(str(Path(base + ".session").resolve()).lower())
                    except Exception:
                        pass

            success = failed = skipped = 0
            results: list[dict] = []

            for path in session_files:
                row = {
                    "filename": path.name,
                    "phone": "",
                    "name": "",
                    "account_id": None,
                    "status": "failed",
                    "detail": "",
                }

                try:
                    resolved = str(path.resolve()).lower()
                    if resolved in known_paths:
                        if force:
                            row["status"] = "skipped"
                            row["detail"] = "Already added"
                            skipped += 1
                            results.append(row)
                        continue

                    stat = path.stat()
                    signature = (int(stat.st_size), int(stat.st_mtime_ns))
                    if not force and self._session_scan_seen.get(resolved) == signature:
                        continue
                    self._session_scan_seen[resolved] = signature

                    session_base = str(path.with_suffix(""))
                    me, phone = await self.inspect_imported_session(session_base)
                    display_name = f"{me.first_name or ''} {me.last_name or ''}".strip() or phone
                    row["phone"] = phone
                    row["name"] = display_name

                    async with AsyncSessionLocal() as db:
                        res = await db.execute(select(Account).where(Account.phone == phone))
                        acc = res.scalar_one_or_none()
                        replacing = bool(acc)

                        if acc:
                            candidate_paths = []
                            for base in self._session_path_candidates(acc):
                                try:
                                    candidate_paths.append(str(Path(base + ".session").resolve()).lower())
                                except Exception:
                                    pass
                            has_existing_file = any(Path(base + ".session").exists() for base in self._session_path_candidates(acc))
                            if resolved not in candidate_paths and has_existing_file:
                                row["status"] = "skipped"
                                row["detail"] = "Account already exists from another session file"
                                row["account_id"] = acc.id
                                skipped += 1
                                results.append(row)
                                continue
                            if self.get(acc.id):
                                await self.stop_client(acc.id)

                        session_file = path.stem
                        if not acc:
                            acc = Account(
                                phone=phone,
                                tg_user_id=me.id,
                                first_name=me.first_name or "",
                                last_name=me.last_name or "",
                                username=me.username or "",
                                session_file=session_file,
                                status="connected",
                            )
                            db.add(acc)
                        else:
                            acc.tg_user_id = me.id
                            acc.first_name = me.first_name or ""
                            acc.last_name = me.last_name or ""
                            acc.username = me.username or ""
                            acc.session_file = session_file
                            acc.status = "connected"

                        await db.commit()
                        await db.refresh(acc)
                        row["account_id"] = acc.id

                    detail = "Updated from sessions folder" if replacing else "Imported from sessions folder"
                    try:
                        await self.start_client(acc)
                    except Exception as start_err:
                        detail += f"; saved but could not start now: {self._session_error_detail(start_err)}"

                    row["status"] = "ok"
                    row["detail"] = detail
                    success += 1
                    results.append(row)
                    known_paths.add(resolved)
                except Exception as e:
                    row["status"] = "failed"
                    row["detail"] = self._session_error_detail(e)
                    failed += 1
                    results.append(row)

            return {
                "ok": True,
                "total": len(session_files),
                "success": success,
                "failed": failed,
                "skipped": skipped,
                "results": results,
            }

    def get(self, account_id: int) -> Optional[TelegramClient]:
        return self._clients.get(account_id)

    async def all_clients(self) -> dict[int, TelegramClient]:
        return dict(self._clients)

    # ---------- lifecycle ----------
    async def startup_load_all(self):
        """On boot, start clients for every previously-authorized account."""
        async with AsyncSessionLocal() as db:
            res = await db.execute(select(Account))
            accounts = res.scalars().all()
        conc = max(1, getattr(settings, "STARTUP_CONCURRENCY", 10))
        sem = asyncio.Semaphore(conc)

        async def _start_one(acc: Account):
            async with sem:
                try:
                    await self.start_client(acc)
                except Exception as e:
                    log.warning("Failed to start client for %s: %s", acc.phone, e)

        await asyncio.gather(*(_start_one(acc) for acc in accounts))

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
            except Exception as e:
                log.warning("session folder sync failed: %s", e)

        asyncio.create_task(_sync_pasted_sessions())

    async def shutdown(self):
        for cli in list(self._clients.values()):
            try:
                await cli.disconnect()
            except Exception:
                pass
        for pend in list(self._pending.values()):
            try: await pend['client'].disconnect()
            except Exception: pass
        for qr in list(self._qr_pending.values()):
            t = qr.get('wait_task')
            if t and not t.done():
                t.cancel()
            try: await qr['client'].disconnect()
            except Exception: pass
        self._clients.clear()
        self._pending.clear()
        self._qr_pending.clear()

    async def start_client(self, acc: Account) -> TelegramClient:
        lock = await self._acc_lock(acc.id)
        async with lock:
            if acc.id in self._clients:
                return self._clients[acc.id]
            cli = TelegramClient(self._session_path_for_account(acc), settings.TG_API_ID, settings.TG_API_HASH)
            await cli.connect()
            if not await cli.is_user_authorized():
                await cli.disconnect()
                await self._set_status(acc.id, "disconnected")
                raise RuntimeError("not authorized")
            self._clients[acc.id] = cli
            self._attach_listener(acc.id, cli)
            await self._set_status(acc.id, "connected")
            # sync profile
            try:
                me = await cli.get_me()
                await self._sync_profile(acc.id, me)
            except Exception:
                pass
            # backfill recent 777000 messages we may have missed while offline
            try:
                await self._backfill_777000(acc.id, cli, limit=50)
            except Exception as e:
                log.warning("backfill 777000 for %s: %s", acc.phone, e)
            return cli

    async def stop_client(self, account_id: int):
        lock = await self._acc_lock(account_id)
        async with lock:
            cli = self._clients.pop(account_id, None)
        if cli:
            try:
                await cli.disconnect()
            except Exception:
                pass

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

    async def send_code(self, phone: str) -> str:
        # If there's a stale pending login for this phone, kill it first
        prev = self._pending.pop(phone, None)
        if prev:
            try: await prev['client'].disconnect()
            except Exception: pass
        cli = TelegramClient(self._session_path(phone), settings.TG_API_ID, settings.TG_API_HASH)
        await asyncio.wait_for(cli.connect(), timeout=20)
        sent = await asyncio.wait_for(cli.send_code_request(phone), timeout=30)
        self._pending[phone] = {
            'client': cli,
            'phone_code_hash': sent.phone_code_hash,
            'needs_2fa': False,
        }
        return sent.phone_code_hash

    async def submit_code(self, phone: str, code: str) -> tuple[TgUser | None, bool]:
        """Returns (user, needs_2fa). If needs_2fa=True, user is None and the
        client is kept alive for a follow-up submit_2fa call."""
        from telethon.errors import SessionPasswordNeededError
        pend = self._pending.get(phone)
        if not pend:
            raise RuntimeError("No pending login. Send code first.")
        cli: TelegramClient = pend['client']
        try:
            await asyncio.wait_for(
                cli.sign_in(phone=phone, code=code, phone_code_hash=pend['phone_code_hash']),
                timeout=30,
            )
        except SessionPasswordNeededError:
            pend['needs_2fa'] = True
            return None, True
        except Exception:
            # On hard error, give up the pending session so user can re-send code
            await self._kill_pending(phone)
            raise
        me = await cli.get_me()
        # Code accepted, no 2FA. Disconnect this temp client now that session is saved.
        await self._kill_pending(phone, disconnect=True)
        return me, False

    async def submit_2fa(self, phone: str, password: str) -> TgUser:
        pend = self._pending.get(phone)
        if not pend:
            raise RuntimeError("No pending 2FA session. Send code first.")
        cli: TelegramClient = pend['client']
        try:
            await asyncio.wait_for(cli.sign_in(password=password), timeout=30)
        except Exception:
            # wrong password OR network: keep pending so user can retry
            raise
        me = await cli.get_me()
        # Remember this 2FA password locally so bulk ops can reuse it.
        try:
            await secrets_store.save_2fa(phone, password)
        except Exception:
            pass
        await self._kill_pending(phone, disconnect=True)
        return me

    async def cancel_pending(self, phone: str):
        await self._kill_pending(phone)

    async def _kill_pending(self, phone: str, disconnect: bool = True):
        pend = self._pending.pop(phone, None)
        if pend and disconnect:
            try: await pend['client'].disconnect()
            except Exception: pass

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
        qr_id = secrets.token_urlsafe(12)
        sess_path = self._qr_session_path(qr_id)
        cli = TelegramClient(sess_path, settings.TG_API_ID, settings.TG_API_HASH)
        await asyncio.wait_for(cli.connect(), timeout=20)
        try:
            qr_login = await asyncio.wait_for(cli.qr_login(), timeout=30)
        except Exception:
            try: await cli.disconnect()
            except Exception: pass
            self._safe_unlink(sess_path + ".session")
            raise
        wait_task = asyncio.create_task(self._qr_wait(qr_id))
        self._qr_pending[qr_id] = {
            'client': cli,
            'qr_login': qr_login,
            'wait_task': wait_task,
            'needs_2fa': False,
            'authorized': False,
            'error': None,
            'me': None,
            'session_path': sess_path,
        }
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
            # success: client is authorized
            entry['authorized'] = True
            try:
                me = await cli.get_me()
                entry['me'] = me
            except Exception as e:
                entry['error'] = f"get_me failed: {e}"
        except SessionPasswordNeededError:
            entry['needs_2fa'] = True
        except asyncio.TimeoutError:
            entry['error'] = "QR code expired"
        except asyncio.CancelledError:
            raise
        except Exception as e:
            entry['error'] = str(e)

    async def qr_recreate(self, qr_id: str) -> dict:
        """Refresh the QR token within an existing pending entry (same client)."""
        entry = self._qr_pending.get(qr_id)
        if not entry:
            raise RuntimeError("QR session not found")
        cli: TelegramClient = entry['client']
        # cancel the old wait task before issuing a new qr_login
        old = entry.get('wait_task')
        if old and not old.done():
            old.cancel()
            try: await old
            except Exception: pass
        qr_login = await asyncio.wait_for(cli.qr_login(), timeout=30)
        entry['qr_login'] = qr_login
        entry['error'] = None
        entry['wait_task'] = asyncio.create_task(self._qr_wait(qr_id))
        return {
            'qr_id': qr_id,
            'url': qr_login.url,
            'expires_at': qr_login.expires.isoformat() if qr_login.expires else None,
        }

    async def qr_status(self, qr_id: str) -> dict:
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
        except Exception:
            pass
        return me

    async def qr_promote_to_phone(self, qr_id: str, phone: str):
        """Move the QR-temp session file to the canonical acc_<phone>.session
        path and disconnect the temp client. Returns the new path."""
        entry = self._qr_pending.pop(qr_id, None)
        if not entry:
            raise RuntimeError("QR session not found")
        wait_task = entry.get('wait_task')
        if wait_task and not wait_task.done():
            wait_task.cancel()
            try: await wait_task
            except Exception: pass
        try: await entry['client'].disconnect()
        except Exception: pass
        src = entry['session_path']
        dst = self._session_path(phone)
        try:
            self._move_session_files(src, dst)
            return dst
        except Exception as e:
            log.warning("qr session move failed: %s", e)
            return dst
    async def qr_cancel(self, qr_id: str):
        entry = self._qr_pending.pop(qr_id, None)
        if not entry:
            return
        wait_task = entry.get('wait_task')
        if wait_task and not wait_task.done():
            wait_task.cancel()
            try: await wait_task
            except Exception: pass
        try: await entry['client'].disconnect()
        except Exception: pass
        # Only remove the temp session file if not yet promoted
        self._safe_unlink(entry['session_path'] + ".session")

    @staticmethod
    def _safe_unlink(path: str):
        try:
            p = Path(path)
            if p.exists():
                p.unlink()
        except Exception:
            pass

    # ---------- listener ----------
    def _attach_listener(self, account_id: int, cli: TelegramClient):
        @cli.on(events.NewMessage(from_users=SERVICE_ID))
        async def _handler(event):
            try:
                text = event.message.message or ""
                msg_id = event.message.id
                m_type = classify_777000(text)
                async with AsyncSessionLocal() as db:
                    sm = SecurityMessage(
                        account_id=account_id,
                        tg_msg_id=msg_id,
                        message_text=text,
                        type=m_type,
                        is_read=False,
                        received_at=datetime.utcnow(),
                    )
                    db.add(sm)
                    await db.commit()
                    await db.refresh(sm)
                # notify pub/sub
                for cb in list(self._new_msg_callbacks):
                    try:
                        cb({
                            "id": sm.id,
                            "account_id": account_id,
                            "type": m_type,
                            "message_text": text,
                            "received_at": sm.received_at.isoformat(),
                        })
                    except Exception:
                        pass
            except Exception as e:
                log.exception("777000 handler failed: %s", e)

    def subscribe_new_messages(self, cb):
        self._new_msg_callbacks.append(cb)

    def unsubscribe_new_messages(self, cb):
        try:
            self._new_msg_callbacks.remove(cb)
        except ValueError:
            pass

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
            # 2FA detection
            try:
                cli = self._clients.get(account_id)
                if cli:
                    from telethon.tl.functions.account import GetPasswordRequest
                    pw = await cli(GetPasswordRequest())
                    acc.has_2fa = bool(pw.has_password)
            except Exception:
                pass
            await db.commit()

    async def _backfill_777000(self, account_id: int, cli: TelegramClient, limit: int = 50):
        """Read recent messages from 777000 and persist any we don't yet have.
        Marks them as already-read so the user isn't flooded with old alerts."""
        async with AsyncSessionLocal() as db:
            res = await db.execute(
                select(SecurityMessage.tg_msg_id).where(SecurityMessage.account_id == account_id)
            )
            seen = {row[0] for row in res.all()}
        added = 0
        try:
            async for msg in cli.iter_messages(SERVICE_ID, limit=limit):
                if msg.id in seen:
                    continue
                text = msg.message or ""
                if not text:
                    continue
                m_type = classify_777000(text)
                async with AsyncSessionLocal() as db:
                    sm = SecurityMessage(
                        account_id=account_id,
                        tg_msg_id=msg.id,
                        message_text=text,
                        type=m_type,
                        is_read=True,  # backfilled history: don't spam unread
                        received_at=msg.date.replace(tzinfo=None) if msg.date else datetime.utcnow(),
                    )
                    db.add(sm)
                    await db.commit()
                added += 1
        except Exception as e:
            log.warning("backfill iter failed for account %s: %s", account_id, e)
        if added:
            log.info("backfilled %d 777000 messages for account %s", added, account_id)

    async def refresh_status_all(self):
        for aid, cli in list(self._clients.items()):
            try:
                if not cli.is_connected():
                    await cli.connect()
                ok = await cli.is_user_authorized()
                await self._set_status(aid, "connected" if ok else "disconnected")
            except (UserDeactivatedBanError, UserDeactivatedError):
                # Real, permanent ban/deactivation — drop into Gone/Banned history
                # and stop polling the dead client so we don't re-hit this every 30s.
                await self._mark_banned(aid)
                await self.stop_client(aid)
            except AuthKeyUnregisteredError:
                # Session expired/revoked — recoverable by reconnecting, NOT a ban.
                await self._set_status(aid, "disconnected")
            except Exception:
                await self._set_status(aid, "disconnected")


manager = TgClientManager()
