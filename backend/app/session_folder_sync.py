"""Discovery and persistence of authorized Telethon session files."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Awaitable, Callable

from sqlalchemy import select

from .config import settings
from .db import AsyncSessionLocal
from .models import Account
from .session_files import SessionFileStore


class SessionFolderSyncService:
    def __init__(
        self,
        *,
        session_path_candidates: Callable[[Account], list[str]],
        inspect_imported_session: Callable[[str], Awaitable[tuple[object, str]]],
        get_client: Callable[[int], object | None],
        stop_client: Callable[[int], Awaitable[None]],
        start_client: Callable[[Account], Awaitable[object]],
        logger: logging.Logger | None = None,
    ):
        self._session_path_candidates = session_path_candidates
        self._inspect_imported_session = inspect_imported_session
        self._get_client = get_client
        self._stop_client = stop_client
        self._start_client = start_client
        self._log = logger or logging.getLogger("session_folder_sync")
        self.lock = asyncio.Lock()
        self.seen: dict[str, tuple[int, int]] = {}

    @staticmethod
    def session_error_detail(exc: Exception) -> str:
        return f"Session import failed ({type(exc).__name__})"

    @staticmethod
    def is_importable_session_file(path: Path) -> bool:
        return SessionFileStore.is_importable_session_file(path)

    @staticmethod
    def _resolved_session_path(base: str) -> str | None:
        try:
            return str(Path(base + ".session").resolve()).lower()
        except (OSError, RuntimeError):
            return None

    async def sync(self, force: bool = False) -> dict:
        """Discover authorized .session files and upsert missing Account rows."""
        async with self.lock:
            session_files = [
                path
                for path in sorted(settings.sessions_path.glob("*.session"))
                if self.is_importable_session_file(path)
            ]

            async with AsyncSessionLocal() as db:
                res = await db.execute(select(Account))
                accounts = list(res.scalars().all())

            known_paths: set[str] = set()
            for acc in accounts:
                for base in self._session_path_candidates(acc):
                    resolved = self._resolved_session_path(base)
                    if resolved:
                        known_paths.add(resolved)

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
                    if not force and self.seen.get(resolved) == signature:
                        continue
                    self.seen[resolved] = signature

                    session_base = str(path.with_suffix(""))
                    me, phone = await self._inspect_imported_session(session_base)
                    display_name = (
                        f"{getattr(me, 'first_name', '') or ''} "
                        f"{getattr(me, 'last_name', '') or ''}"
                    ).strip() or phone
                    row["phone"] = phone
                    row["name"] = display_name

                    async with AsyncSessionLocal() as db:
                        res = await db.execute(
                            select(Account).where(Account.phone == phone)
                        )
                        acc = res.scalar_one_or_none()
                        replacing = bool(acc)

                        if acc:
                            candidate_paths = []
                            for base in self._session_path_candidates(acc):
                                candidate = self._resolved_session_path(base)
                                if candidate:
                                    candidate_paths.append(candidate)
                            has_existing_file = any(
                                Path(base + ".session").exists()
                                for base in self._session_path_candidates(acc)
                            )
                            if (
                                resolved not in candidate_paths
                                and has_existing_file
                            ):
                                row["status"] = "skipped"
                                row["detail"] = (
                                    "Account already exists from another session file"
                                )
                                row["account_id"] = acc.id
                                skipped += 1
                                results.append(row)
                                continue
                            if self._get_client(acc.id):
                                await self._stop_client(acc.id)

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

                    detail = (
                        "Updated from sessions folder"
                        if replacing
                        else "Imported from sessions folder"
                    )
                    try:
                        await self._start_client(acc)
                    except Exception as exc:
                        # Persistence succeeded; a temporary Telegram/client
                        # startup failure must not roll back the imported row.
                        detail += (
                            "; saved but could not start now: "
                            f"{self.session_error_detail(exc)}"
                        )

                    row["status"] = "ok"
                    row["detail"] = detail
                    success += 1
                    results.append(row)
                    known_paths.add(resolved)
                except Exception as exc:
                    # Per-file import is deliberately isolated so one malformed
                    # or unusable session does not abort discovery of the rest.
                    row["status"] = "failed"
                    row["detail"] = self.session_error_detail(exc)
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
