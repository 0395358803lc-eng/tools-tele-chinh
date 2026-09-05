import ast
import unittest
from pathlib import Path
from unittest.mock import patch

from app.routers import profile


class _ExpectedRpcError(Exception):
    pass


class _UsernameClient:
    def __init__(self, error):
        self.error = error

    async def __call__(self, _request):
        raise self.error


class _PhotoClient:
    def __init__(self, error):
        self.error = error

    async def get_me(self):
        return object()

    async def download_profile_photo(self, _me, file):
        raise self.error


class ProfileReadBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_username_expected_rpc_error_returns_check_failed(self):
        client = _UsernameClient(_ExpectedRpcError("telegram unavailable"))
        with (
            patch.object(profile, "RPCError", _ExpectedRpcError),
            patch.object(profile.manager, "get", return_value=client),
        ):
            with self.assertLogs("profile", level="INFO") as captured:
                result = await profile.check_username(1, "candidate_name")
        self.assertFalse(result.available)
        self.assertEqual(result.reason, "check_failed")
        self.assertTrue(
            any("username availability check failed" in line for line in captured.output)
        )

    async def test_username_unexpected_error_propagates(self):
        client = _UsernameClient(RuntimeError("programming bug"))
        with patch.object(profile.manager, "get", return_value=client):
            with self.assertRaises(RuntimeError):
                await profile.check_username(1, "candidate_name")

    async def test_photo_preview_expected_rpc_error_returns_none(self):
        client = _PhotoClient(_ExpectedRpcError("telegram unavailable"))
        with (
            patch.object(profile, "RPCError", _ExpectedRpcError),
            patch.object(profile.manager, "get", return_value=client),
        ):
            with self.assertLogs("profile", level="INFO") as captured:
                result = await profile.get_photo_url(1)
        self.assertEqual(result, {"data_url": None})
        self.assertTrue(
            any("profile photo preview unavailable" in line for line in captured.output)
        )

    async def test_photo_preview_unexpected_error_propagates(self):
        client = _PhotoClient(RuntimeError("programming bug"))
        with patch.object(profile.manager, "get", return_value=client):
            with self.assertRaises(RuntimeError):
                await profile.get_photo_url(1)


class ProfileReadSourceGuardTests(unittest.TestCase):
    def test_read_fallbacks_do_not_catch_broad_exception(self):
        source_path = Path(__file__).resolve().parents[1] / "app" / "routers" / "profile.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        guarded = {"check_username", "get_photo_url"}
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
        self.assertEqual(offenders, [], f"broad profile read handlers: {offenders}")


if __name__ == "__main__":
    unittest.main()
