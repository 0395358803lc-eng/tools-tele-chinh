import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import secrets_store


class LegacySecretMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_migration_removes_all_plaintext_copies_after_verified_write(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            secure = root / "twofa.bin"
            staging = root / "data-twofa.json"
            legacy = root / "backend-twofa.json"
            payload = {"+84123456789": "secret-one", "+84987654321": "secret-two"}
            staging.write_text(json.dumps(payload), encoding="utf-8")
            legacy.write_text(json.dumps(payload), encoding="utf-8")

            def encrypt(data: bytes) -> bytes:
                return b"TEST:" + data

            def decrypt(data: bytes) -> bytes:
                if not data.startswith(b"TEST:"):
                    raise OSError("bad test envelope")
                return data[5:]

            with (
                patch("app.secrets_store._path", return_value=secure),
                patch("app.secrets_store.legacy_path", return_value=staging),
                patch("app.secrets_store._legacy_backend_plaintext_path", return_value=legacy),
                patch("app.secrets_store._encrypt", side_effect=encrypt),
                patch("app.secrets_store._decrypt", side_effect=decrypt),
            ):
                await secrets_store.migrate_legacy()
                self.assertEqual(secrets_store._read(), payload)

            self.assertTrue(secure.exists())
            self.assertFalse(staging.exists())
            self.assertFalse(legacy.exists())

    async def test_existing_secure_store_cleans_old_duplicate(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            secure = root / "twofa.bin"
            staging = root / "missing-twofa.json"
            legacy = root / "backend-twofa.json"
            payload = {"+84123456789": "secret-one"}
            legacy.write_text(json.dumps(payload), encoding="utf-8")

            def encrypt(data: bytes) -> bytes:
                return b"TEST:" + data

            def decrypt(data: bytes) -> bytes:
                if not data.startswith(b"TEST:"):
                    raise OSError("bad test envelope")
                return data[5:]

            secure.write_bytes(encrypt(json.dumps(payload).encode("utf-8")))
            with (
                patch("app.secrets_store._path", return_value=secure),
                patch("app.secrets_store.legacy_path", return_value=staging),
                patch("app.secrets_store._legacy_backend_plaintext_path", return_value=legacy),
                patch("app.secrets_store._encrypt", side_effect=encrypt),
                patch("app.secrets_store._decrypt", side_effect=decrypt),
            ):
                await secrets_store.migrate_legacy()

            self.assertFalse(legacy.exists())

    async def test_mismatched_legacy_plaintext_is_never_deleted(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            secure = root / "twofa.bin"
            staging = root / "missing-twofa.json"
            legacy = root / "backend-twofa.json"
            secure_payload = {"+84123456789": "new-secret"}
            legacy_payload = {"+84123456789": "old-secret"}
            legacy.write_text(json.dumps(legacy_payload), encoding="utf-8")

            def encrypt(data: bytes) -> bytes:
                return b"TEST:" + data

            def decrypt(data: bytes) -> bytes:
                if not data.startswith(b"TEST:"):
                    raise OSError("bad test envelope")
                return data[5:]

            secure.write_bytes(encrypt(json.dumps(secure_payload).encode("utf-8")))
            with (
                patch("app.secrets_store._path", return_value=secure),
                patch("app.secrets_store.legacy_path", return_value=staging),
                patch("app.secrets_store._legacy_backend_plaintext_path", return_value=legacy),
                patch("app.secrets_store._encrypt", side_effect=encrypt),
                patch("app.secrets_store._decrypt", side_effect=decrypt),
            ):
                await secrets_store.migrate_legacy()

            self.assertTrue(legacy.exists())


if __name__ == "__main__":
    unittest.main()
