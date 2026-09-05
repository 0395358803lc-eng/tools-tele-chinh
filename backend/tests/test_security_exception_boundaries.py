import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app import secrets_store
from app.routers import security


class _ExpectedRpcError(Exception):
    pass


class _AuthorizationClient:
    def __init__(self, reset_error=None):
        self.reset_error = reset_error
        self.calls = 0

    async def __call__(self, _request):
        self.calls += 1
        if self.calls == 1:
            return SimpleNamespace(
                authorizations=[
                    SimpleNamespace(current=True, hash=1),
                    SimpleNamespace(current=False, hash=2),
                ]
            )
        if self.reset_error is not None:
            raise self.reset_error
        return None


class SecurityExceptionBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_2fa_local_save_success_has_no_warning_flag(self):
        with patch(
            "app.routers.security.secrets_store.save_2fa",
            new=AsyncMock(return_value=None),
        ):
            warned = await security._save_2fa_best_effort("+10000000000", "secret")
        self.assertFalse(warned)

    async def test_2fa_local_save_failure_is_logged_and_does_not_recast_remote_success(self):
        with patch(
            "app.routers.security.secrets_store.save_2fa",
            new=AsyncMock(side_effect=RuntimeError("storage bug")),
        ):
            with self.assertLogs("security", level="WARNING") as captured:
                warned = await security._save_2fa_best_effort(
                    "+10000000000", "secret"
                )
        self.assertTrue(warned)
        self.assertTrue(
            any("2FA secret persistence failed" in line for line in captured.output)
        )

    async def test_expected_rpc_session_reset_failure_is_counted_and_logged(self):
        client = _AuthorizationClient(_ExpectedRpcError("telegram rejected"))
        with patch.object(security, "RPCError", _ExpectedRpcError):
            with self.assertLogs("security", level="WARNING") as captured:
                killed, failed = await security._terminate_other_authorizations(client)
        self.assertEqual((killed, failed), (0, 1))
        self.assertTrue(
            any("terminate other authorization failed" in line for line in captured.output)
        )

    async def test_unexpected_session_reset_error_propagates(self):
        client = _AuthorizationClient(RuntimeError("programming bug"))
        with self.assertRaises(RuntimeError):
            await security._terminate_other_authorizations(client)


class LegacySecretMigrationBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_malformed_legacy_store_is_retained_and_logged(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            legacy = root / "twofa.json"
            encrypted = root / "twofa.bin"
            legacy.write_text("{not-json", encoding="utf-8")

            with (
                patch("app.secrets_store.legacy_path", return_value=legacy),
                patch("app.secrets_store._path", return_value=encrypted),
            ):
                with self.assertLogs("secrets_store", level="WARNING") as captured:
                    await secrets_store.migrate_legacy()

            self.assertTrue(legacy.exists())
            self.assertFalse(encrypted.exists())
            self.assertTrue(
                any("legacy 2FA migration deferred" in line for line in captured.output)
            )


if __name__ == "__main__":
    unittest.main()
