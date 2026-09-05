import ast
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

    async def test_conflicting_staging_plaintext_is_preserved(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            secure = root / "twofa.bin"
            staging = root / "data-twofa.json"
            legacy = root / "backend-twofa.json"
            secure_payload = {"+84123456789": "new-secret"}
            staging_payload = {"+84123456789": "old-secret", "+84987654321": "legacy-only"}
            staging.write_text(json.dumps(staging_payload), encoding="utf-8")

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
                self.assertEqual(
                    secrets_store._read(),
                    {
                        "+84123456789": "new-secret",
                        "+84987654321": "legacy-only",
                    },
                )

            self.assertTrue(staging.exists())


    async def test_expected_migration_io_failure_is_logged_and_plaintext_preserved(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            secure = root / "twofa.bin"
            staging = root / "data-twofa.json"
            legacy = root / "backend-twofa.json"
            staging.write_text(json.dumps({"+84123456789": "secret-one"}), encoding="utf-8")

            with (
                patch("app.secrets_store._path", return_value=secure),
                patch("app.secrets_store.legacy_path", return_value=staging),
                patch("app.secrets_store._legacy_backend_plaintext_path", return_value=legacy),
                patch("app.secrets_store._write", side_effect=OSError("sensitive-marker")),
                self.assertLogs("secrets_store", level="WARNING") as captured,
            ):
                await secrets_store.migrate_legacy()

            self.assertTrue(staging.exists())
            joined = "\n".join(captured.output)
            self.assertIn("legacy 2FA migration deferred", joined)
            self.assertIn("OSError", joined)
            self.assertNotIn("sensitive-marker", joined)

    async def test_unexpected_migration_programming_error_propagates(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            secure = root / "twofa.bin"
            staging = root / "data-twofa.json"
            legacy = root / "backend-twofa.json"
            staging.write_text(json.dumps({"+84123456789": "secret-one"}), encoding="utf-8")

            with (
                patch("app.secrets_store._path", return_value=secure),
                patch("app.secrets_store.legacy_path", return_value=staging),
                patch("app.secrets_store._legacy_backend_plaintext_path", return_value=legacy),
                patch("app.secrets_store._write", side_effect=ValueError("programming bug")),
            ):
                with self.assertRaisesRegex(ValueError, "programming bug"):
                    await secrets_store.migrate_legacy()

            self.assertTrue(staging.exists())

    async def test_malformed_duplicate_plaintext_is_retained_with_diagnostic(self):
        with tempfile.TemporaryDirectory() as raw:
            legacy = Path(raw) / "backend-twofa.json"
            legacy.write_text("{not-json", encoding="utf-8")

            with (
                patch("app.secrets_store._legacy_backend_plaintext_path", return_value=legacy),
                self.assertLogs("secrets_store", level="WARNING") as captured,
            ):
                removed = secrets_store._remove_legacy_plaintext_if_covered({})

            self.assertFalse(removed)
            self.assertTrue(legacy.exists())
            joined = "\n".join(captured.output)
            self.assertIn("stage=duplicate_read", joined)
            self.assertIn("JSONDecodeError", joined)


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


class LegacyMigrationSourceGuardTests(unittest.TestCase):
    def test_migration_helpers_do_not_swallow_broad_exception(self):
        source_path = Path(__file__).resolve().parents[1] / "app" / "secrets_store.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        guarded = {"migrate_legacy", "_remove_legacy_plaintext_if_covered"}
        offenders = []
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name not in guarded:
                continue
            for child in ast.walk(node):
                if not isinstance(child, ast.ExceptHandler) or child.type is None:
                    continue
                if isinstance(child.type, ast.Name) and child.type.id == "Exception":
                    offenders.append((node.name, getattr(child, "lineno", -1)))
        self.assertEqual(offenders, [], f"broad migration exception handlers: {offenders}")


if __name__ == "__main__":
    unittest.main()
