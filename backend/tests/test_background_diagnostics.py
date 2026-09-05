import ast
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app import main as app_main
from app.tg_manager import TgClientManager


class _EmptyResult:
    def all(self):
        return []


class _ReadSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def execute(self, _query):
        return _EmptyResult()


class _BrokenIteratorClient:
    def iter_messages(self, *_args, **_kwargs):
        return _BrokenAsyncIterator()


class _BrokenAsyncIterator:
    def __aiter__(self):
        return self

    async def __anext__(self):
        raise RuntimeError("sensitive-marker")


class BackgroundDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def test_status_tick_failure_is_contained_without_raw_error_text(self):
        with (
            patch.object(
                app_main.manager,
                "refresh_status_all",
                new=AsyncMock(side_effect=RuntimeError("sensitive-marker")),
            ),
            patch.object(
                app_main.manager,
                "verify_authorizations_all",
                new=AsyncMock(),
            ) as verify,
            self.assertLogs("main", level="WARNING") as captured,
        ):
            ok = await app_main._run_status_tick()

        self.assertFalse(ok)
        verify.assert_not_awaited()
        joined = "\n".join(captured.output)
        self.assertIn("status refresh failed", joined)
        self.assertIn("RuntimeError", joined)
        self.assertNotIn("sensitive-marker", joined)

    async def test_status_tick_success_runs_connection_and_auth_passes(self):
        with (
            patch.object(
                app_main.manager,
                "refresh_status_all",
                new=AsyncMock(),
            ) as refresh,
            patch.object(
                app_main.manager,
                "verify_authorizations_all",
                new=AsyncMock(return_value=1),
            ) as verify,
        ):
            ok = await app_main._run_status_tick()

        self.assertTrue(ok)
        refresh.assert_awaited_once()
        verify.assert_awaited_once()

    async def test_login_code_redaction_failure_does_not_leak_raw_error(self):
        manager = TgClientManager()
        with (
            patch(
                "app.tg_manager.AsyncSessionLocal",
                side_effect=RuntimeError("sensitive-marker"),
            ),
            self.assertLogs("tg_manager", level="WARNING") as captured,
        ):
            await manager.redact_stored_login_codes()

        joined = "\n".join(captured.output)
        self.assertIn("login-code redaction skipped", joined)
        self.assertIn("RuntimeError", joined)
        self.assertNotIn("sensitive-marker", joined)

    async def test_backfill_iterator_failure_is_contained_without_raw_error(self):
        manager = TgClientManager()
        with (
            patch("app.tg_manager.AsyncSessionLocal", return_value=_ReadSession()),
            self.assertLogs("tg_manager", level="WARNING") as captured,
        ):
            await manager._backfill_777000(
                42,
                _BrokenIteratorClient(),
                limit=10,
            )

        joined = "\n".join(captured.output)
        self.assertIn("backfill iter failed account=42", joined)
        self.assertIn("RuntimeError", joined)
        self.assertNotIn("sensitive-marker", joined)


class BackgroundDiagnosticsSourceGuardTests(unittest.TestCase):
    def test_background_boundaries_do_not_format_raw_exception_objects(self):
        main_source = (
            Path(__file__).resolve().parents[1] / "app" / "main.py"
        ).read_text(encoding="utf-8")
        tg_source = (
            Path(__file__).resolve().parents[1] / "app" / "tg_manager.py"
        ).read_text(encoding="utf-8")

        self.assertNotIn('log.warning("status refresh: %s", e)', main_source)
        self.assertNotIn('log.warning("login-code redaction skipped: %s", e)', tg_source)
        self.assertNotIn('log.exception("777000 handler failed: %s", e)', tg_source)
        self.assertNotIn(
            'log.warning("backfill iter failed for account %s: %s", account_id, e)',
            tg_source,
        )

    def test_status_tick_keeps_explicit_resilience_boundary(self):
        source_path = Path(__file__).resolve().parents[1] / "app" / "main.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        found = False
        for node in tree.body:
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "_run_status_tick":
                found = True
                handlers = [
                    child
                    for child in ast.walk(node)
                    if isinstance(child, ast.ExceptHandler)
                ]
                self.assertTrue(
                    any(
                        isinstance(handler.type, ast.Name)
                        and handler.type.id == "Exception"
                        for handler in handlers
                    ),
                    "status tick should remain a deliberate resilience boundary",
                )
        self.assertTrue(found, "_run_status_tick helper missing")


if __name__ == "__main__":
    unittest.main()
