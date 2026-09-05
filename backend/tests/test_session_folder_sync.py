import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from app.models import Account
from app.session_folder_sync import SessionFolderSyncService


class _ScalarResult:
    def __init__(self, *, rows=None, scalar=None):
        self._rows = list(rows or [])
        self._scalar = scalar

    class _Scalars:
        def __init__(self, rows):
            self._rows = rows

        def all(self):
            return list(self._rows)

    def scalars(self):
        return self._Scalars(self._rows)

    def scalar_one_or_none(self):
        return self._scalar


class _Db:
    def __init__(self, *results):
        self._results = list(results)
        self.added = []
        self.execute = AsyncMock(side_effect=self._next_result)
        self.commit = AsyncMock()
        self.refresh = AsyncMock(side_effect=self._refresh)

    def _next_result(self, _query):
        if not self._results:
            raise AssertionError("unexpected execute")
        return self._results.pop(0)

    async def _refresh(self, obj):
        if getattr(obj, "id", None) is None:
            obj.id = 42

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
            raise AssertionError("unexpected AsyncSessionLocal()")
        return self._sessions.pop(0)


def _service(*, inspect=None, start=None, candidates=None, get_client=None, stop=None):
    return SessionFolderSyncService(
        session_path_candidates=candidates or (lambda _acc: []),
        inspect_imported_session=inspect or AsyncMock(),
        get_client=get_client or (lambda _aid: None),
        stop_client=stop or AsyncMock(),
        start_client=start or AsyncMock(),
    )


class SessionFolderSyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_authorized_session_creates_account_and_starts_client(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            session = root / "pasted.session"
            session.write_bytes(b"session")
            me = SimpleNamespace(
                id=1001,
                first_name="Demo",
                last_name="User",
                username="demo",
            )
            inspect = AsyncMock(return_value=(me, "+84123456789"))
            start = AsyncMock()
            initial = _Db(_ScalarResult(rows=[]))
            write = _Db(_ScalarResult(scalar=None))
            service = _service(inspect=inspect, start=start)

            with (
                patch(
                    "app.session_folder_sync.settings",
                    SimpleNamespace(sessions_path=root),
                ),
                patch(
                    "app.session_folder_sync.AsyncSessionLocal",
                    _SessionFactory(initial, write),
                ),
            ):
                result = await service.sync(force=True)

        self.assertEqual(result["success"], 1)
        self.assertEqual(result["failed"], 0)
        self.assertEqual(result["results"][0]["status"], "ok")
        self.assertEqual(result["results"][0]["account_id"], 42)
        self.assertEqual(len(write.added), 1)
        created = write.added[0]
        self.assertIsInstance(created, Account)
        self.assertEqual(created.phone, "+84123456789")
        self.assertEqual(created.session_file, "pasted")
        start.assert_awaited_once_with(created)

    async def test_start_failure_after_commit_keeps_import_success_without_raw_error(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "pasted.session").write_bytes(b"session")
            me = SimpleNamespace(
                id=1002,
                first_name="Demo",
                last_name="",
                username="demo",
            )
            initial = _Db(_ScalarResult(rows=[]))
            write = _Db(_ScalarResult(scalar=None))
            service = _service(
                inspect=AsyncMock(return_value=(me, "+84999999999")),
                start=AsyncMock(side_effect=RuntimeError("sensitive-marker")),
            )

            with (
                patch(
                    "app.session_folder_sync.settings",
                    SimpleNamespace(sessions_path=root),
                ),
                patch(
                    "app.session_folder_sync.AsyncSessionLocal",
                    _SessionFactory(initial, write),
                ),
            ):
                result = await service.sync(force=True)

        row = result["results"][0]
        self.assertEqual(row["status"], "ok")
        self.assertIn("RuntimeError", row["detail"])
        self.assertNotIn("sensitive-marker", row["detail"])

    async def test_bad_session_is_isolated_per_file(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "bad.session").write_bytes(b"bad")
            initial = _Db(_ScalarResult(rows=[]))
            service = _service(
                inspect=AsyncMock(side_effect=RuntimeError("sensitive-marker"))
            )

            with (
                patch(
                    "app.session_folder_sync.settings",
                    SimpleNamespace(sessions_path=root),
                ),
                patch(
                    "app.session_folder_sync.AsyncSessionLocal",
                    _SessionFactory(initial),
                ),
            ):
                result = await service.sync(force=True)

        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["results"][0]["status"], "failed")
        self.assertIn("RuntimeError", result["results"][0]["detail"])
        self.assertNotIn("sensitive-marker", result["results"][0]["detail"])

    def test_manager_keeps_only_session_sync_facade(self):
        app_root = Path(__file__).resolve().parents[1] / "app"
        manager_source = (app_root / "tg_manager.py").read_text(encoding="utf-8")
        service_source = (app_root / "session_folder_sync.py").read_text(
            encoding="utf-8"
        )

        self.assertNotIn('glob("*.session")', manager_source)
        self.assertIn('glob("*.session")', service_source)
        self.assertIn("self._session_folder_sync.sync", manager_source)
        self.assertLess(manager_source.count("_session_scan_seen"), 3)


if __name__ == "__main__":
    unittest.main()
