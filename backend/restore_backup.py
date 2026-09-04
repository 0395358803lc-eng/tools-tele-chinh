"""Offline, staged backup restore with rollback. Stop the app before use."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import tempfile
from pathlib import Path

from app.backup_service import _create_backup_sync, _sqlite_backup
from app.config import settings
from app.secrets_store import validate_store_file


def _port_in_use(port=8000):
    with socket.socket() as sock:
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_manifest(source: Path) -> dict:
    try:
        manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError("Backup manifest is invalid") from exc
    checksums = manifest.get("checksums")
    if not isinstance(checksums, dict) or not checksums:
        raise RuntimeError("Backup has no checksums; create a new backup before restore")
    for relative, expected in checksums.items():
        candidate = (source / relative).resolve()
        candidate.relative_to(source.resolve())
        if not candidate.is_file() or _sha256(candidate) != expected:
            raise RuntimeError(f"Backup checksum failed: {relative}")
    return manifest


def _stage_backup(source: Path, stage: Path):
    source_db = source / "database" / "app.db"
    if not source_db.is_file():
        raise RuntimeError("Backup database is missing")
    (stage / "database").mkdir(parents=True)
    (stage / "sessions").mkdir()
    (stage / "secrets").mkdir()
    _sqlite_backup(source_db, stage / "database" / "app.db")
    for session in (source / "sessions").glob("*.session"):
        _sqlite_backup(session, stage / "sessions" / session.name)
    secret = source / "secrets" / "twofa.bin"
    if secret.exists():
        shutil.copy2(secret, stage / "secrets" / "twofa.bin")
        ok, detail = validate_store_file(stage / "secrets" / "twofa.bin")
        if not ok:
            raise RuntimeError(f"Backup secure store cannot be decrypted: {detail}")


def _swap_trees(stage: Path, rollback: Path, live_dirs: dict[str, Path]):
    """Swap all runtime trees, reversing every completed swap on any failure."""
    swaps: list[tuple[Path, Path, Path]] = []
    try:
        for name in ("database", "sessions", "secrets"):
            live = live_dirs[name]
            staged = stage / name
            old = rollback / name
            if live.exists():
                os.replace(live, old)
            try:
                os.replace(staged, live)
            except BaseException:
                if old.exists():
                    os.replace(old, live)
                raise
            swaps.append((live, staged, old))
    except BaseException:
        for live, staged, old in reversed(swaps):
            if live.exists():
                os.replace(live, staged)
            if old.exists():
                os.replace(old, live)
        raise


def restore(name: str):
    if _port_in_use():
        raise RuntimeError("Stop Multi TG Manager before restoring a backup")
    if not name or any(ch not in "0123456789_-" for ch in name):
        raise RuntimeError("Invalid backup name")
    source = (settings.backups_path / name).resolve()
    source.relative_to(settings.backups_path.resolve())
    if not source.is_dir() or not (source / "manifest.json").is_file():
        raise RuntimeError("Backup is incomplete or does not exist")

    _validate_manifest(source)
    # A recoverable snapshot is created before any live path is renamed.
    safety = _create_backup_sync(best_effort=True)
    live_dirs = {
        "database": settings.database_path.parent,
        "sessions": settings.sessions_path,
        "secrets": settings.secrets_path,
    }
    runtime_root = settings.database_path.parent.parent
    root = Path(tempfile.mkdtemp(prefix=".mtm-restore-", dir=runtime_root))
    stage = root / "stage"
    rollback = root / "rollback"
    stage.mkdir()
    rollback.mkdir()
    swapped = False
    try:
        _stage_backup(source, stage)
        _swap_trees(stage, rollback, live_dirs)
        swapped = True
        # Validate the live database after swap before discarding rollback data.
        check = root / "post-restore-check.db"
        _sqlite_backup(settings.database_path, check)
        check.unlink(missing_ok=True)
    except BaseException:
        if swapped:
            # Restore every old tree. Move the failed restored trees aside first.
            for name in reversed(("database", "sessions", "secrets")):
                live = live_dirs[name]
                old = rollback / name
                failed = stage / name
                if live.exists():
                    os.replace(live, failed)
                if old.exists():
                    os.replace(old, live)
        raise
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return safety.name


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("backup_name", help="folder name under data/backups")
    args = parser.parse_args()
    safety_name = restore(args.backup_name)
    print(f"Restore completed. Pre-restore safety backup: {safety_name}")
