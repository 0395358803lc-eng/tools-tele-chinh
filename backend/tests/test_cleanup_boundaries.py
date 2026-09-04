import ast
import asyncio
import sqlite3
import unittest
from pathlib import Path

from app.tg_manager import TgClientManager


class _FakeClient:
    def __init__(self, disconnect_error=None, close_error=None):
        self.disconnect_error = disconnect_error
        self.session = _FakeSession(close_error)

    async def disconnect(self):
        if self.disconnect_error is not None:
            raise self.disconnect_error


class _FakeSession:
    def __init__(self, close_error=None):
        self.close_error = close_error

    def close(self):
        if self.close_error is not None:
            raise self.close_error


class _FakeHandlerClient:
    def __init__(self, error=None):
        self.error = error

    def remove_event_handler(self, handler):
        if self.error is not None:
            raise self.error


class CleanupBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_expected_disconnect_failure_is_contained(self):
        manager = TgClientManager()
        client = _FakeClient(OSError("socket already closed"))
        self.assertFalse(
            await manager._safe_disconnect(client, context="test:expected")
        )

    async def test_unexpected_disconnect_failure_is_logged_and_contained(self):
        manager = TgClientManager()
        client = _FakeClient(ValueError("unexpected"))
        with self.assertLogs("tg_manager", level="WARNING") as captured:
            self.assertFalse(
                await manager._safe_disconnect(client, context="test:unexpected")
            )
        self.assertTrue(
            any("unexpected Telegram disconnect cleanup failure" in line for line in captured.output)
        )

    async def test_disconnect_cancellation_propagates_by_default(self):
        manager = TgClientManager()
        client = _FakeClient(asyncio.CancelledError())
        with self.assertRaises(asyncio.CancelledError):
            await manager._safe_disconnect(client, context="test:cancel")

    async def test_disconnect_cancellation_can_be_suppressed_for_shutdown(self):
        manager = TgClientManager()
        client = _FakeClient(asyncio.CancelledError())
        self.assertFalse(
            await manager._safe_disconnect(
                client,
                context="test:shutdown",
                suppress_cancelled=True,
            )
        )

    async def test_session_close_sqlite_failure_is_contained(self):
        manager = TgClientManager()
        client = _FakeClient(close_error=sqlite3.ProgrammingError("already closed"))
        self.assertFalse(
            manager._safe_close_session(client, context="test:session")
        )

    async def test_unexpected_handler_detach_failure_is_logged_and_contained(self):
        manager = TgClientManager()
        client = _FakeHandlerClient(ValueError("unexpected"))
        with self.assertLogs("tg_manager", level="WARNING") as captured:
            self.assertFalse(
                manager._safe_remove_event_handler(
                    client,
                    object(),
                    context="test:handler",
                )
            )
        self.assertTrue(
            any("unexpected Telegram handler detach failure" in line for line in captured.output)
        )


class CleanupSourceGuardTests(unittest.TestCase):
    def test_tg_manager_has_no_silent_broad_exception_pass(self):
        source_path = Path(__file__).resolve().parents[1] / "app" / "tg_manager.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        offenders = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler) or node.type is None:
                continue
            catches_exception = (
                isinstance(node.type, ast.Name)
                and node.type.id == "Exception"
            )
            if isinstance(node.type, ast.Tuple):
                catches_exception = any(
                    isinstance(item, ast.Name) and item.id == "Exception"
                    for item in node.type.elts
                )
            if catches_exception and len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                offenders.append(getattr(node, "lineno", -1))
        self.assertEqual(offenders, [], f"silent broad exception handlers at lines {offenders}")

    def test_tg_manager_has_no_broad_exception_suppressor(self):
        source_path = Path(__file__).resolve().parents[1] / "app" / "tg_manager.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        offenders = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Name) or node.func.id != "suppress":
                continue
            if any(isinstance(arg, ast.Name) and arg.id == "Exception" for arg in node.args):
                offenders.append(getattr(node, "lineno", -1))
        self.assertEqual(offenders, [], f"broad suppress(Exception) calls at lines {offenders}")


if __name__ == "__main__":
    unittest.main()
