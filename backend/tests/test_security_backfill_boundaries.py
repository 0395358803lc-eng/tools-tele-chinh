import ast
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy.exc import IntegrityError

from app import security_messages, tg_manager


class _ExpectedRpcError(Exception):
    pass


class _Rows:
    def __init__(self, rows=None):
        self._rows = rows or []

    def all(self):
        return list(self._rows)


class _Db:
    def __init__(self, *, rows=None, commit_error=None):
        self.execute = AsyncMock(return_value=_Rows(rows))
        self.commit = AsyncMock(side_effect=commit_error)
        self.rollback = AsyncMock()
        self.added = []

    def add(self, obj):
        self.added.append(obj)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _SessionFactory:
    def __init__(self, *sessions):
        self._sessions = list(sessions)

    def __call__(self):
        if not self._sessions:
            raise AssertionError("unexpected AsyncSessionLocal() call")
        return self._sessions.pop(0)


class _AsyncIterator:
    def __init__(self, items=None, *, terminal_error=None):
        self.items = list(items or [])
        self.terminal_error = terminal_error

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.items:
            return self.items.pop(0)
        if self.terminal_error is not None:
            error = self.terminal_error
            self.terminal_error = None
            raise error
        raise StopAsyncIteration


class _Client:
    def __init__(self, iterator):
        self.iterator = iterator

    def iter_messages(self, _peer, *, limit):
        return self.iterator


def _message(msg_id=1, text="Security alert"):
    return SimpleNamespace(id=msg_id, message=text, date=None)


class SecurityBackfillBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_expected_telegram_read_failure_is_logged_and_contained(self):
        initial_db = _Db(rows=[])
        client = _Client(_AsyncIterator(terminal_error=_ExpectedRpcError("sensitive-marker")))

        with (
            patch.object(security_messages, "RPCError", _ExpectedRpcError),
            patch.object(security_messages, "AsyncSessionLocal", _SessionFactory(initial_db)),
            self.assertLogs("tg_manager", level="WARNING") as captured,
        ):
            await tg_manager.manager._backfill_777000(7, client, limit=10)

        joined = "\n".join(captured.output)
        self.assertIn("backfill Telegram read stopped", joined)
        self.assertIn("_ExpectedRpcError", joined)
        self.assertNotIn("sensitive-marker", joined)

    async def test_unexpected_iterator_programming_error_propagates(self):
        initial_db = _Db(rows=[])
        client = _Client(_AsyncIterator(terminal_error=RuntimeError("programming bug")))

        with patch.object(
            security_messages,
            "AsyncSessionLocal",
            _SessionFactory(initial_db),
        ):
            with self.assertRaisesRegex(RuntimeError, "programming bug"):
                await tg_manager.manager._backfill_777000(7, client, limit=10)

    async def test_database_commit_programming_error_propagates(self):
        initial_db = _Db(rows=[])
        write_db = _Db(commit_error=RuntimeError("database bug"))
        client = _Client(_AsyncIterator([_message()]))

        with patch.object(
            security_messages,
            "AsyncSessionLocal",
            _SessionFactory(initial_db, write_db),
        ):
            with self.assertRaisesRegex(RuntimeError, "database bug"):
                await tg_manager.manager._backfill_777000(7, client, limit=10)

        self.assertEqual(len(write_db.added), 1)

    async def test_duplicate_message_integrity_error_rolls_back_and_continues(self):
        initial_db = _Db(rows=[])
        duplicate = IntegrityError("insert", {}, Exception("duplicate"))
        write_db = _Db(commit_error=duplicate)
        client = _Client(_AsyncIterator([_message()]))

        with patch.object(
            security_messages,
            "AsyncSessionLocal",
            _SessionFactory(initial_db, write_db),
        ):
            await tg_manager.manager._backfill_777000(7, client, limit=10)

        write_db.rollback.assert_awaited_once()


class SecurityBackfillSourceGuardTests(unittest.TestCase):
    def test_backfill_does_not_catch_broad_exception(self):
        source_path = Path(__file__).resolve().parents[1] / "app" / "security_messages.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        offenders = []
        found = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef) or node.name != "backfill":
                continue
            found = True
            for child in ast.walk(node):
                if not isinstance(child, ast.ExceptHandler) or child.type is None:
                    continue
                if isinstance(child.type, ast.Name) and child.type.id == "Exception":
                    offenders.append(getattr(child, "lineno", -1))
        self.assertTrue(found, "SecurityMessageService.backfill missing")
        self.assertEqual(offenders, [], f"broad backfill exception handlers: {offenders}")

    def test_tg_manager_keeps_only_security_message_compatibility_wrappers(self):
        app_root = Path(__file__).resolve().parents[1] / "app"
        manager_source = (app_root / "tg_manager.py").read_text(encoding="utf-8")
        service_source = (app_root / "security_messages.py").read_text(encoding="utf-8")

        self.assertNotIn("SecurityMessage(", manager_source)
        self.assertNotIn("events.NewMessage", manager_source)
        self.assertIn("SecurityMessage(", service_source)
        self.assertIn("events.NewMessage", service_source)
        self.assertIn("self._security_messages.backfill", manager_source)
        self.assertIn("self._security_messages.attach", manager_source)


if __name__ == "__main__":
    unittest.main()
