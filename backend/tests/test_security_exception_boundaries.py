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
    def __init__(self, reset_errors=None):
        self.reset_errors = dict(reset_errors or {})
        self.reset_calls = []

    async def __call__(self, request):
        if request[0] == "get":
            return SimpleNamespace(
                authorizations=[
                    SimpleNamespace(current=True, hash=999),
                    SimpleNamespace(current=False, hash=1),
                    SimpleNamespace(current=False, hash=2),
                ]
            )
        if request[0] == "reset":
            hash_id = request[1]
            self.reset_calls.append(hash_id)
            error = self.reset_errors.get(hash_id)
            if error is not None:
                raise error
            return None
        raise AssertionError(f"unexpected request: {request!r}")


class SecurityExceptionBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_expected_rpc_termination_failure_is_counted_and_loop_continues(self):
        client = _AuthorizationClient({1: _ExpectedRpcError("telegram rejected")})
        with (
            patch.object(security, "RPCError", _ExpectedRpcError),
            patch.object(security, "GetAuthorizationsRequest", return_value=("get",)),
            patch.object(
                security,
                "ResetAuthorizationRequest",
                side_effect=lambda hash: ("reset", hash),
            ),
            self.assertLogs("security", level="WARNING") as captured,
        ):
            killed, failed = await security._terminate_other_authorizations(client)

        self.assertEqual((killed, failed), (1, 1))
        self.assertEqual(client.reset_calls, [1, 2])
        joined = "\n".join(captured.output)
        self.assertIn("session termination rejected", joined)
        self.assertIn("error_type=_ExpectedRpcError", joined)
        self.assertNotIn("telegram rejected", joined)

    async def test_unexpected_termination_error_propagates_and_stops_loop(self):
        client = _AuthorizationClient({1: RuntimeError("programming bug")})
        with (
            patch.object(security, "GetAuthorizationsRequest", return_value=("get",)),
            patch.object(
                security,
                "ResetAuthorizationRequest",
                side_effect=lambda hash: ("reset", hash),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "programming bug"):
                await security._terminate_other_authorizations(client)

        self.assertEqual(client.reset_calls, [1])

    async def test_floodwait_termination_error_propagates(self):
        client = _AuthorizationClient({1: _ExpectedFloodWait("wait")})
        with (
            patch.object(security, "FloodWaitError", _ExpectedFloodWait),
            patch.object(security, "GetAuthorizationsRequest", return_value=("get",)),
            patch.object(
                security,
                "ResetAuthorizationRequest",
                side_effect=lambda hash: ("reset", hash),
            ),
        ):
            with self.assertRaises(_ExpectedFloodWait):
                await security._terminate_other_authorizations(client)

        self.assertEqual(client.reset_calls, [1])

    async def test_local_2fa_save_failure_is_warning_not_false_telegram_failure(self):
        with (
            patch.object(
                security.secrets_store,
                "save_2fa",
                new=AsyncMock(side_effect=RuntimeError("secret-material")),
            ),
            self.assertLogs("security", level="WARNING") as captured,
        ):
            warned = await security._save_2fa_after_telegram_change(
                "+84123456789",
                "new-secret",
            )

        self.assertTrue(warned)
        joined = "\n".join(captured.output)
        self.assertIn("phone_suffix=6789", joined)
        self.assertIn("error_type=RuntimeError", joined)
        self.assertNotIn("secret-material", joined)
        self.assertNotIn("new-secret", joined)
        self.assertNotIn("+84123456789", joined)

    async def test_local_2fa_save_success_has_no_warning(self):
        save = AsyncMock()
        with patch.object(security.secrets_store, "save_2fa", new=save):
            warned = await security._save_2fa_after_telegram_change(
                "+84123456789",
                "new-secret",
            )
        self.assertFalse(warned)
        save.assert_awaited_once_with("+84123456789", "new-secret")


class SecurityExceptionSourceGuardTests(unittest.TestCase):
    def test_session_termination_helper_has_no_broad_exception_handler(self):
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
        self.assertEqual(offenders, [], f"broad termination handlers: {offenders}")


if __name__ == "__main__":
    unittest.main()
