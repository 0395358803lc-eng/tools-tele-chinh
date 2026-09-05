import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException, UploadFile
from PIL import Image

from app.backup_service import _best_effort_backup, _create_backup_sync, _sqlite_backup, list_backups
from app.schemas import SettingsIn
from app.tg_manager import redact_login_code
from app.uploads import read_limited, sanitize_filename, validate_image_bytes
from app import secrets_store


class UploadAndRedactionTests(unittest.IsolatedAsyncioTestCase):
    async def test_streaming_upload_accepts_within_limit(self):
        upload = UploadFile(filename="safe.session", file=io.BytesIO(b"abc"))
        self.assertEqual(await read_limited(upload, 3), b"abc")

    async def test_streaming_upload_aborts_when_oversized(self):
        upload = UploadFile(filename="large.session", file=io.BytesIO(b"abcd"))
        with self.assertRaises(HTTPException) as caught:
            await read_limited(upload, 3, chunk_size=2)
        self.assertEqual(caught.exception.status_code, 413)

    async def test_filename_path_traversal_is_removed(self):
        self.assertEqual(sanitize_filename("../../secret.session"), "secret.session")
        self.assertEqual(sanitize_filename(r"..\..\secret.session"), "secret.session")

    async def test_real_image_validation(self):
        buffer = io.BytesIO()
        Image.new("RGB", (2, 2), "red").save(buffer, format="PNG")
        validate_image_bytes(buffer.getvalue())

    async def test_fake_image_is_rejected(self):
        with self.assertRaises(HTTPException) as caught:
            validate_image_bytes(b"not an image")
        self.assertEqual(caught.exception.status_code, 415)

    async def test_telegram_login_code_is_redacted(self):
        cleaned = redact_login_code("Your login code is 12345")
        self.assertNotIn("12345", cleaned)
        self.assertIn("[code]", cleaned)


class BackupAndSettingsTests(unittest.TestCase):
    def test_database_survives_reopen(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "app.db"
            with closing(sqlite3.connect(path)) as db:
                db.execute("CREATE TABLE accounts(id INTEGER PRIMARY KEY, name TEXT)")
                db.execute("INSERT INTO accounts(name) VALUES ('persisted')")
                db.commit()
            with closing(sqlite3.connect(path)) as db:
                self.assertEqual(db.execute("SELECT name FROM accounts").fetchone()[0], "persisted")

    def test_sqlite_backup_can_be_restored(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "source.db"; backup = root / "backup.db"; restored = root / "restored.db"
            with closing(sqlite3.connect(source)) as db:
                db.execute("CREATE TABLE values_table(value TEXT)")
                db.execute("INSERT INTO values_table VALUES ('restored')")
                db.commit()
            _sqlite_backup(source, backup)
            _sqlite_backup(backup, restored)
            with closing(sqlite3.connect(restored)) as db:
                self.assertEqual(db.execute("SELECT value FROM values_table").fetchone()[0], "restored")

    def test_database_session_and_secret_backup(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            database = root / "database" / "app.db"
            sessions = root / "sessions"
            secrets = root / "secrets"
            backups = root / "backups"
            database.parent.mkdir(); sessions.mkdir(); secrets.mkdir(); backups.mkdir()
            with closing(sqlite3.connect(database)) as db:
                db.execute("CREATE TABLE sample(value TEXT)")
                db.execute("INSERT INTO sample VALUES ('kept')")
                db.commit()
            with closing(sqlite3.connect(sessions / "one.session")) as db:
                db.execute("CREATE TABLE session_data(value INTEGER)")
                db.execute("INSERT INTO session_data VALUES (1)")
                db.commit()
            (secrets / "twofa.bin").write_bytes(b"encrypted")

            target = _create_backup_sync(database, sessions, secrets, backups)
            with closing(sqlite3.connect(target / "database" / "app.db")) as db:
                self.assertEqual(db.execute("SELECT value FROM sample").fetchone()[0], "kept")
                self.assertEqual(db.execute("PRAGMA quick_check").fetchone()[0], "ok")
            self.assertTrue((target / "sessions" / "one.session").exists())
            self.assertEqual((target / "secrets" / "twofa.bin").read_bytes(), b"encrypted")
            manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["sessions"], 1)

    def test_best_effort_backup_falls_back_for_expected_sqlite_error(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "source.db"
            destination = root / "backup.db"
            source.write_bytes(b"raw-db")
            with patch(
                "app.backup_service._sqlite_backup",
                side_effect=sqlite3.DatabaseError("corrupt"),
            ):
                with self.assertLogs("backup_service", level="WARNING") as captured:
                    success, used_raw = _best_effort_backup(source, destination)
            self.assertTrue(success)
            self.assertTrue(used_raw)
            self.assertEqual(destination.read_bytes(), b"raw-db")
            self.assertTrue(
                any("trying raw copy" in line for line in captured.output)
            )

    def test_best_effort_backup_does_not_swallow_programming_error(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "source.db"
            destination = root / "backup.db"
            source.write_bytes(b"data")
            with patch(
                "app.backup_service._sqlite_backup",
                side_effect=ValueError("programming bug"),
            ):
                with self.assertRaises(ValueError):
                    _best_effort_backup(source, destination)

    def test_best_effort_raw_copy_does_not_swallow_programming_error(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "source.db"
            destination = root / "backup.db"
            source.write_bytes(b"data")
            with (
                patch(
                    "app.backup_service._sqlite_backup",
                    side_effect=sqlite3.DatabaseError("corrupt"),
                ),
                patch(
                    "app.backup_service._raw_copy",
                    side_effect=ValueError("programming bug"),
                ),
            ):
                with self.assertRaises(ValueError):
                    _best_effort_backup(source, destination)

    def test_list_backups_skips_malformed_manifest_with_log(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            bad = root / "2026-09-05_000000_000001"
            bad.mkdir()
            (bad / "manifest.json").write_text("{not-json", encoding="utf-8")
            with patch("app.config.BACKUPS_DIR", root):
                with self.assertLogs("backup_service", level="WARNING") as captured:
                    result = list_backups()
            self.assertEqual(result, [])
            self.assertTrue(
                any("backup manifest skipped" in line for line in captured.output)
            )

    def test_settings_reject_invalid_runtime_values(self):
        with self.assertRaises(Exception):
            SettingsIn(rate_min=-1, rate_max=2, concurrency=1, auto_reconnect=True)
        with self.assertRaises(Exception):
            SettingsIn(rate_min=1, rate_max=2, concurrency=0, auto_reconnect=True)

    def test_best_effort_backup_survives_corrupt_current_db(self):
        """A corrupt current DB must not abort the safety backup used during
        restore: it degrades to a raw copy (or is skipped) instead of raising."""
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            database = root / "database" / "app.db"
            sessions = root / "sessions"
            secrets = root / "secrets"
            backups = root / "backups"
            database.parent.mkdir(); sessions.mkdir(); secrets.mkdir(); backups.mkdir()
            # Corrupt, non-SQLite bytes for the current DB.
            database.write_bytes(b"\x00" * 4096)

            target = _create_backup_sync(database, sessions, secrets, backups, best_effort=True)
            manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual((target / "database" / "app.db").read_bytes(), b"\x00" * 4096)
            self.assertTrue(manifest["database_raw_copy"])

    def test_backup_skips_current_db_when_requested(self):
        """Restore may opt out of persisting a known-corrupt current DB."""
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            database = root / "database" / "app.db"
            sessions = root / "sessions"
            secrets = root / "secrets"
            backups = root / "backups"
            database.parent.mkdir(); sessions.mkdir(); secrets.mkdir(); backups.mkdir()
            database.write_bytes(b"\x00" * 4096)

            target = _create_backup_sync(
                database, sessions, secrets, backups, best_effort=True, backup_current_db=False
            )
            manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
            self.assertIsNone(manifest["database"])
            self.assertFalse((target / "database" / "app.db").exists())


def _dpapi_usable():
    if not secrets_store._dpapi_available():
        return False
    try:
        return secrets_store._dpapi_decrypt(secrets_store._dpapi_encrypt(b"probe")) == b"probe"
    except OSError:
        return False


@unittest.skipUnless(_dpapi_usable(), "Windows DPAPI unavailable in this sandbox")
class SecretStoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_set_get_update_delete(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "twofa.bin"
            with patch("app.secrets_store._path", return_value=path):
                await secrets_store.save_2fa("+10000000000", "first-secret")
                self.assertEqual(await secrets_store.get_2fa("+10000000000"), "first-secret")
                await secrets_store.save_2fa("+10000000000", "second-secret")
                self.assertEqual(await secrets_store.get_2fa("+10000000000"), "second-secret")
                self.assertNotIn(b"second-secret", path.read_bytes())
                await secrets_store.delete_2fa("+10000000000")
                self.assertIsNone(await secrets_store.get_2fa("+10000000000"))
