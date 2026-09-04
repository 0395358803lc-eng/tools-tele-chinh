"""End-to-end Windows integration test: backup -> wipe -> restore.

Exercises the real data lifecycle the production scripts use:
  1. seed a database + a session file (+ a DPAPI-encrypted 2FA secret when the
     sandbox has DPAPI available),
  2. create a backup,
  3. wipe the current tree as if the app data were lost/corrupt,
  4. restore from the backup,
  5. assert the database rows, session bytes and secret survive intact,
     and that a best-effort safety backup of the wiped state was captured.

All paths are redirected to a throwaway temp tree so the real data/ is never
touched.
"""

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from app.backup_service import _create_backup_sync, list_backups
from app.config import settings as app_settings
from app import secrets_store
from restore_backup import restore


def _dpapi_usable():
    if not getattr(secrets_store, "_dpapi_available", lambda: False)():
        return False
    try:
        return (
            secrets_store._dpapi_decrypt(secrets_store._dpapi_encrypt(b"probe"))
            == b"probe"
        )
    except OSError:
        return False


class RestoreIntegrationTests(unittest.TestCase):
    def _build_tree(self):
        raw = tempfile.mkdtemp(prefix="mtgm_restore_")
        root = Path(raw)
        database = root / "database"; database.mkdir()
        sessions = root / "sessions"; sessions.mkdir()
        secrets = root / "secrets"; secrets.mkdir()
        backups = root / "backups"; backups.mkdir()
        return root, database, sessions, secrets, backups

    def _seed(self, database: Path, sessions: Path, secrets: Path):
        with closing(sqlite3.connect(database / "app.db")) as db:
            db.execute(
                "CREATE TABLE accounts(id INTEGER PRIMARY KEY, phone TEXT UNIQUE, status TEXT)"
            )
            db.executemany(
                "INSERT INTO accounts(phone, status) VALUES (?, ?)",
                [("+10000000001", "connected"), ("+10000000002", "disconnected")],
            )
            db.commit()
        with closing(sqlite3.connect(sessions / "+10000000001.session")) as db:
            db.execute("CREATE TABLE session_data(value INTEGER)")
            db.execute("INSERT INTO session_data VALUES (42)")
            db.commit()
        with patch("app.secrets_store._path", return_value=secrets / "twofa.bin"):
            if _dpapi_usable():
                import asyncio
                asyncio.run(secrets_store.save_2fa("+10000000001", "login-secret"))

    def _paths_patch(self, root, database, sessions, secrets, backups):
        return patch.multiple(
            "app.config",
            DATABASE_DIR=database,
            SESSIONS_DIR=sessions,
            SECRETS_DIR=secrets,
            BACKUPS_DIR=backups,
        )

    def test_backup_then_restore_preserves_all_data(self):
        root, database, sessions, secrets, backups = self._build_tree()
        try:
            with self._paths_patch(root, database, sessions, secrets, backups):
                self._seed(database, sessions, secrets)

                backup = _create_backup_sync(best_effort=True)
                self.assertTrue(backup.is_dir())
                self.assertTrue((backup / "database" / "app.db").is_file())

                # Wipe the current state as if data were lost/corrupt.
                (database / "app.db").unlink()
                (database / "app.db").write_bytes(b"\x00" * 4096)
                for s in sessions.glob("*.session"):
                    s.unlink()
                (secrets / "twofa.bin").unlink(missing_ok=True)

                # Restore the known-good backup.
                safety = restore(backup.name)
                self.assertNotEqual(safety, backup.name)

                # Current DB matches the backed-up one.
                with closing(sqlite3.connect(database / "app.db")) as db:
                    rows = db.execute(
                        "SELECT phone FROM accounts ORDER BY id"
                    ).fetchall()
                    self.assertEqual(rows, [("+10000000001",), ("+10000000002",)])
                    self.assertEqual(db.execute("PRAGMA quick_check").fetchone()[0], "ok")

                # Session file restored byte-identical.
                with closing(sqlite3.connect(sessions / "+10000000001.session")) as db:
                    self.assertEqual(
                        db.execute("SELECT value FROM session_data").fetchone()[0], 42
                    )

                if _dpapi_usable():
                    import asyncio
                    restored_secret = asyncio.run(
                        secrets_store.get_2fa("+10000000001")
                    )
                    self.assertEqual(restored_secret, "login-secret")

                # A best-effort safety backup of the corrupt wipe was captured.
                backups_list = list_backups()
                self.assertEqual(len(backups_list), 2)  # the good + the safety wipe
        finally:
            import shutil
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
