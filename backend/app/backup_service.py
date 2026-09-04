from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from .config import settings


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sqlite_backup(source_path: Path, destination_path: Path):
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(str(source_path))
    destination = sqlite3.connect(str(destination_path))
    try:
        source.backup(destination)
        row = destination.execute("PRAGMA quick_check").fetchone()
        if not row or row[0] != "ok":
            raise RuntimeError("Backup database integrity check failed")
    finally:
        destination.close()
        source.close()


def _raw_copy(source_path: Path, destination_path: Path):
    """Best-effort fallback: copy a DB file byte-for-byte when the SQLite
    online-backup API can't open it (e.g. the current DB is corrupt). No
    integrity check is possible on the copy; that's why this is reserved for
    safety backups where "something is better than nothing"."""
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, destination_path)


def _best_effort_backup(source_path: Path, destination_path: Path) -> tuple[bool, bool]:
    """Try a verified SQLite backup first; on any failure fall back to a raw
    copy so a corrupt source can never abort the whole backup.

    Returns (success, used_raw_copy)."""
    try:
        _sqlite_backup(source_path, destination_path)
        return True, False
    except Exception:
        pass
    try:
        _raw_copy(source_path, destination_path)
        return True, True
    except Exception:
        return False, False


def _create_backup_sync(
    database_path: Path | None = None,
    sessions_path: Path | None = None,
    secrets_path: Path | None = None,
    backups_path: Path | None = None,
    best_effort: bool = False,
    backup_current_db: bool = True,
) -> Path:
    """Create a backup under backups/.

    - ``best_effort=True``: a corrupt/read-only source DB degrades to a raw copy
      instead of raising, so the caller (e.g. restore) can still capture a safety
      snapshot of the CURRENT state regardless of its health.
    - ``backup_current_db=False``: skip persisting the *current* (possibly
      corrupt) database entirely; used by restore to avoid bloating the safety
      backup with a broken DB when a good one is about to replace it.
    """
    database_path = database_path or settings.database_path
    sessions_path = sessions_path or settings.sessions_path
    secrets_path = secrets_path or settings.secrets_path
    backups_path = backups_path or settings.backups_path
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S_%f")
    target = backups_path / stamp
    target.mkdir(parents=True, exist_ok=False)

    copied_db = False
    db_raw = False
    if backup_current_db and database_path.is_file():
        if best_effort:
            copied_db, db_raw = _best_effort_backup(database_path, target / "database" / "app.db")
        else:
            _sqlite_backup(database_path, target / "database" / "app.db")
            copied_db = True

    session_target = target / "sessions"
    session_target.mkdir()
    for session in sessions_path.glob("*.session"):
        try:
            _sqlite_backup(session, session_target / session.name)
        except Exception:
            if best_effort:
                _raw_copy(session, session_target / session.name)
            else:
                raise

    secret = secrets_path / "twofa.bin"
    if secret.exists():
        (target / "secrets").mkdir()
        shutil.copy2(secret, target / "secrets" / "twofa.bin")

    manifest = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "database": "database/app.db" if copied_db else None,
        "database_raw_copy": db_raw,
        "sessions": len(list(session_target.glob("*.session"))),
        "twofa_included": secret.exists(),
        "dpapi_notice": "twofa.bin is tied to the Windows user context that created it",
    }
    checksums = {}
    for file_path in target.rglob("*"):
        if file_path.is_file() and file_path.name != "manifest.json":
            checksums[file_path.relative_to(target).as_posix()] = _sha256(file_path)
    manifest["checksums"] = checksums
    (target / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return target


async def create_backup() -> Path:
    path = await asyncio.to_thread(_create_backup_sync)
    await asyncio.to_thread(prune_backups, int(settings.BACKUP_RETENTION_COUNT), {path.name})
    return path


def prune_backups(retain: int | None = None, protect: set[str] | None = None) -> list[str]:
    """Keep the newest N valid backup directories; return names removed."""
    retain = max(1, int(retain or settings.BACKUP_RETENTION_COUNT))
    protect = protect or set()
    candidates = [
        path for path in sorted(settings.backups_path.iterdir(), reverse=True)
        if path.is_dir() and (path / "manifest.json").is_file()
    ]
    removed = []
    kept = 0
    for path in candidates:
        if path.name in protect or kept < retain:
            kept += 1
            continue
        shutil.rmtree(path)
        removed.append(path.name)
    return removed


def list_backups() -> list[dict]:
    out = []
    for path in sorted(settings.backups_path.iterdir(), reverse=True):
        manifest_path = path / "manifest.json"
        if not path.is_dir() or not manifest_path.is_file():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        out.append({"name": path.name, **manifest})
    return out
