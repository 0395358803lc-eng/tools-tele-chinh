"""Deterministic Telethon session-file naming and atomic file operations."""
from __future__ import annotations

import logging
import os
import re
import secrets
import shutil
from pathlib import Path

from .config import settings
from .models import Account
from .tg_utils import normalize_phone

SESSION_SUFFIXES = (".session", ".session-journal", ".session-wal", ".session-shm")


class SessionFileStore:
    def __init__(self, logger: logging.Logger | None = None):
        self._log = logger or logging.getLogger("session_files")

    @staticmethod
    def normalize_phone(phone: str) -> str:
        return normalize_phone(phone)

    def session_path(self, phone: str) -> str:
        safe = self.normalize_phone(phone)[1:]
        return str(settings.sessions_path / f"acc_{safe}")

    def phone_temp_session_path(self, phone: str) -> str:
        digits = self.normalize_phone(phone)[1:]
        return str(settings.sessions_path / f"login_{digits}_{secrets.token_urlsafe(8)}")

    @staticmethod
    def phone_file_part(phone: str) -> str:
        digits = re.sub(r"\D", "", phone or "")
        return digits or "unknown"

    @staticmethod
    def username_file_part(username: str | None, user_id: int | None = None) -> str:
        user = re.sub(r"[^A-Za-z0-9_]", "", (username or "").strip().lstrip("@"))
        if user:
            return user[:64]
        if user_id:
            return f"user{user_id}"
        return "no_username"

    def session_file_name(
        self,
        phone: str,
        username: str | None = None,
        user_id: int | None = None,
    ) -> str:
        return f"{self.username_file_part(username, user_id)}_{self.phone_file_part(phone)}"

    def desired_session_path(
        self,
        phone: str,
        username: str | None = None,
        user_id: int | None = None,
    ) -> str:
        return str(settings.sessions_path / self.session_file_name(phone, username, user_id))

    def path_from_session_file(self, session_file: str) -> str:
        p = Path(session_file or "")
        if p.suffix == ".session":
            p = p.with_suffix("")
        if p.is_absolute():
            return str(p)
        return str(settings.sessions_path / p.name)

    def session_path_candidates(self, acc: Account) -> list[str]:
        candidates: list[str] = []
        if acc.session_file:
            candidates.append(self.path_from_session_file(acc.session_file))
        candidates.append(self.desired_session_path(acc.phone, acc.username, acc.tg_user_id))
        candidates.append(self.path_from_session_file(f"acc_{acc.phone}"))
        candidates.append(self.session_path(acc.phone))

        unique: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            if candidate and candidate not in seen:
                unique.append(candidate)
                seen.add(candidate)
        return unique

    def session_path_for_account(self, acc: Account) -> str:
        candidates = self.session_path_candidates(acc)
        for candidate in candidates:
            if Path(candidate + ".session").exists():
                return candidate
        return candidates[0]

    def safe_unlink(self, path: str):
        try:
            p = Path(path)
            if p.exists():
                p.unlink()
        except OSError as exc:
            self._log.debug(
                "could not remove session artifact path=%s error=%s",
                path,
                type(exc).__name__,
            )

    def move_session_files(self, src_base: str, dst_base: str):
        src = Path(src_base)
        dst = Path(dst_base)
        if src.resolve() == dst.resolve():
            return

        if not any(Path(str(src) + suffix).exists() for suffix in SESSION_SUFFIXES):
            return

        dst.parent.mkdir(parents=True, exist_ok=True)
        for suffix in SESSION_SUFFIXES:
            self.safe_unlink(str(dst) + suffix)
        for suffix in SESSION_SUFFIXES:
            source = Path(str(src) + suffix)
            if not source.exists():
                continue
            target = Path(str(dst) + suffix)
            shutil.move(str(source), str(target))

    def begin_session_swap(self, src_base: str, dst_base: str) -> dict:
        """Atomically install a verified disconnected session with rollback data."""
        src = Path(src_base)
        dst = Path(dst_base)
        src_main = Path(str(src) + ".session")
        if not src_main.is_file():
            raise RuntimeError("Verified session file is missing")

        dst.parent.mkdir(parents=True, exist_ok=True)
        token = secrets.token_hex(8)
        state = {
            "src": str(src),
            "dst": str(dst),
            "moved": [],
            "backups": [],
        }
        try:
            for suffix in SESSION_SUFFIXES:
                target = Path(str(dst) + suffix)
                if target.exists():
                    backup = Path(str(target) + f".rollback-{token}")
                    os.replace(target, backup)
                    state["backups"].append((str(target), str(backup)))

            for suffix in SESSION_SUFFIXES:
                source = Path(str(src) + suffix)
                if source.exists():
                    target = Path(str(dst) + suffix)
                    os.replace(source, target)
                    state["moved"].append((str(source), str(target)))

            if not Path(str(dst) + ".session").is_file():
                raise RuntimeError("Session replace did not produce a destination file")
            return state
        except BaseException:
            self.rollback_session_swap(state)
            raise

    def rollback_session_swap(self, state: dict):
        for source, target in reversed(state.get("moved", [])):
            target_path = Path(target)
            if target_path.exists():
                Path(source).parent.mkdir(parents=True, exist_ok=True)
                os.replace(target_path, source)

        for target, backup in reversed(state.get("backups", [])):
            backup_path = Path(backup)
            if backup_path.exists():
                os.replace(backup_path, target)

    def commit_session_swap(self, state: dict, old_bases: list[str] | None = None):
        for _target, backup in state.get("backups", []):
            self.safe_unlink(backup)

        destination = str(Path(state["dst"]).resolve()).lower()
        for base in old_bases or []:
            if base and str(Path(base).resolve()).lower() != destination:
                self.remove_session_files(base)

    def remove_session_files(self, base: str):
        if not base:
            return
        for suffix in SESSION_SUFFIXES:
            self.safe_unlink(base + suffix)

    @staticmethod
    def is_importable_session_file(path: Path) -> bool:
        if path.suffix.lower() != ".session":
            return False
        stem = path.stem.lower()
        return not (stem.startswith("qr_") or stem.startswith("mtm_import_"))
