import ast
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.routers import security


class _ExpectedRpcError(Exception):
    pass


class _ExpectedFloodWait(Exception):
    pass


class _AuthorizationClient:
    def __init__(self, reset_error=None):
        self.reset_error = reset_error
        self.reset_calls = 0

    async def __call__(self, request):
        if request == "get-authorizations":
            return SimpleNamespace(
                authorizations=[
                    SimpleNamespace(current=True, hash=1),
                    SimpleNamespace(current=False, hash=2),
                ]
            )
        self.reset_calls += 1
        if self.reset_error is not None:
            raise self.reset_error
        return None


class SecurityExceptionBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_secure_store_success_returns_no_warning(self):
        with patch(
            "app.routers.security.secrets_store.save_2fa",
            new=AsyncMock(),
        ) as save:
            warned = await security._save_2fa_after_remote_change(
                "+84123456789", "new-secret"
            )
        self.assertFalse(warned)
        save.assert_awaited_once_with("+84123456789", "new-secret")

    async def test_post_commit_store_failure_returns_warning_without_leaking_details(self):
        with (
            patch(
                "app.routers.security.secrets_store.save_2fa",
                new=AsyncMock(side_effect=RuntimeError("sensitive-marker")),
            ),
            self.assertLogs("security", level="WARNING") as captured,
        ):
            warned = await security._save_2fa_after_remote_change(
                "+84123456789", "new-secret"
            )

        self.assertTrue(warned)
        joined = "\n".join(captured.output)
        self.assertIn("secure-store save failed", joined)
        self.assertIn("RuntimeError", joined)
        self.assertNotIn("sensitive-marker", joined)
        self.assertNotIn("new-secret", joined)
        self.assertNotIn("+84123456789", joined)

    async def test_expected_rpc_session_reset_is_counted_and_logged(self):
        client = _AuthorizationClient(_ExpectedRpcError("telegram rejected"))
        with (
            patch.object(security, "RPCError", _ExpectedRpcError),
            patch.object(
                security,
                "GetAuthorizationsRequest",
                return_value="get-authorizations",
            ),
            patch.object(
                security,
                "ResetAuthorizationRequest",
                side_effect=lambda **kwargs: ("reset", kwargs["hash"]),
            ),
            self.assertLogs("security", level="WARNING") as captured,
        ):
            killed, failed = await security._terminate_other_authorizations(client)

        self.assertEqual((killed, failed), (0, 1))
        self.assertEqual(client.reset_calls, 1)
        self.assertTrue(
            any("Telegram authorization reset failed" in line for line in captured.output)
        )

    async def test_unexpected_session_reset_error_propagates(self):
        client = _AuthorizationClient(RuntimeError("programming bug"))
        with (
            patch.object(
                security,
                "GetAuthorizationsRequest",
                return_value="get-authorizations",
            ),
            patch.object(
                security,
                "ResetAuthorizationRequest",
                side_effect=lambda **kwargs: ("reset", kwargs["hash"]),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "programming bug"):
                await security._terminate_other_authorizations(client)

    async def test_flood_wait_session_reset_propagates(self):
        client = _AuthorizationClient(_ExpectedFloodWait("rate limited"))
        with (
            patch.object(security, "FloodWaitError", _ExpectedFloodWait),
            patch.object(
                security,
                "GetAuthorizationsRequest",
                return_value="get-authorizations",
            ),
            patch.object(
                security,
                "ResetAuthorizationRequest",
                side_effect=lambda **kwargs: ("reset", kwargs["hash"]),
            ),
        ):
            with self.assertRaisesRegex(_ExpectedFloodWait, "rate limited"):
                await security._terminate_other_authorizations(client)


class SecurityExceptionSourceGuardTests(unittest.TestCase):
    def test_session_reset_helper_does_not_catch_broad_exception(self):
        source_path = Path(__file__).resolve().parents[1] / "app" / "routers" / "security.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        offenders = []
        for node in tree.body:
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            if node.name != "_terminate_other_authorizations":
                continue
            for child in ast.walk(node):
                if not isinstance(child, ast.ExceptHandler) or child.type is None:
                    continue
                if isinstance(child.type, ast.Name) and child.type.id == "Exception":
                    offenders.append(getattr(child, "lineno", -1))
        self.assertEqual(
            offenders,
            [],
            f"broad session-reset exception handlers at lines {offenders}",
        )

    def test_post_commit_store_boundary_remains_explicit_and_logged(self):
        source_path = Path(__file__).resolve().parents[1] / "app" / "routers" / "security.py"
        source = source_path.read_text(encoding="utf-8")
        self.assertIn("async def _save_2fa_after_remote_change", source)
        self.assertIn("except Exception as exc:", source)
        self.assertIn("secure-store save failed error_type=%s", source)


if __name__ == "__main__":
    unittest.main()
