import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from app.routers import accounts


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class AccountPersistRollbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_account_state_session_and_client_are_restored(self):
        current = SimpleNamespace(
            phone="+100",
            username="new",
            status="connected",
        )
        db = SimpleNamespace(
            rollback=AsyncMock(),
            get=AsyncMock(return_value=current),
            delete=AsyncMock(),
            commit=AsyncMock(),
        )
        old_values = {
            "phone": "+100",
            "username": "old",
            "status": "disconnected",
        }

        with (
            patch.object(accounts.manager, "rollback_session_swap") as session_rollback,
            patch.object(accounts.manager, "start_client", new=AsyncMock()) as start_client,
        ):
            await accounts._rollback_account_persist(
                db,
                account_id=7,
                existed=True,
                old_values=old_values,
                swap={"dst": "account"},
                was_running=True,
            )

        db.rollback.assert_awaited_once()
        db.commit.assert_awaited_once()
        db.delete.assert_not_awaited()
        self.assertEqual(current.username, "old")
        self.assertEqual(current.status, "disconnected")
        session_rollback.assert_called_once_with({"dst": "account"})
        start_client.assert_awaited_once_with(current)

    async def test_new_account_row_is_deleted_during_rollback(self):
        current = SimpleNamespace(id=9)
        db = SimpleNamespace(
            rollback=AsyncMock(),
            get=AsyncMock(return_value=current),
            delete=AsyncMock(),
            commit=AsyncMock(),
        )
        with (
            patch.object(accounts.manager, "rollback_session_swap"),
            patch.object(accounts.manager, "start_client", new=AsyncMock()) as start_client,
        ):
            await accounts._rollback_account_persist(
                db,
                account_id=9,
                existed=False,
                old_values=None,
                swap={"dst": "account"},
                was_running=False,
            )

        db.delete.assert_awaited_once_with(current)
        db.commit.assert_awaited_once()
        start_client.assert_not_awaited()

    async def test_cleanup_failures_are_logged_without_blocking_later_steps(self):
        restored = SimpleNamespace(id=11)
        db = SimpleNamespace(
            rollback=AsyncMock(side_effect=[OSError("db-secret"), None]),
            get=AsyncMock(side_effect=[restored, restored]),
            delete=AsyncMock(),
            commit=AsyncMock(side_effect=RuntimeError("commit-secret")),
        )

        with (
            patch.object(
                accounts.manager,
                "rollback_session_swap",
                side_effect=OSError("session-secret"),
            ) as session_rollback,
            patch.object(
                accounts.manager,
                "start_client",
                new=AsyncMock(side_effect=RuntimeError("client-secret")),
            ) as start_client,
            self.assertLogs("accounts", level="WARNING") as captured,
        ):
            await accounts._rollback_account_persist(
                db,
                account_id=11,
                existed=True,
                old_values={"status": "disconnected"},
                swap={"dst": "account"},
                was_running=True,
            )

        session_rollback.assert_called_once()
        start_client.assert_awaited_once_with(restored)
        joined = "\n".join(captured.output)
        for step in (
            "step=db_rollback",
            "step=db_restore",
            "step=session_restore",
            "step=client_restart",
        ):
            self.assertIn(step, joined)
        for secret in ("db-secret", "commit-secret", "session-secret", "client-secret"):
            self.assertNotIn(secret, joined)

    async def test_cancelled_start_client_triggers_persist_rollback_then_reraises(self):
        acc = SimpleNamespace(
            id=21,
            phone="+100",
            tg_user_id=1,
            first_name="Old",
            last_name="",
            username="old",
            session_file="old.session",
            status="disconnected",
            has_2fa=False,
            is_online=False,
            last_seen=None,
        )
        me = SimpleNamespace(
            id=2,
            first_name="New",
            last_name="Name",
            username="new",
        )
        db = SimpleNamespace(
            execute=AsyncMock(return_value=_ScalarResult(acc)),
            commit=AsyncMock(),
            refresh=AsyncMock(),
        )
        rollback = AsyncMock()

        with (
            patch.object(accounts.manager, "normalize_phone", return_value="+100"),
            patch.object(accounts.manager, "_session_path_candidates", return_value=["old"]),
            patch.object(accounts.manager, "get", return_value=None),
            patch.object(accounts.manager, "_desired_session_path", return_value="new"),
            patch.object(accounts.manager, "begin_session_swap", return_value={"dst": "new"}),
            patch.object(
                accounts.manager,
                "start_client",
                new=AsyncMock(side_effect=asyncio.CancelledError()),
            ),
            patch.object(accounts, "_rollback_account_persist", new=rollback),
        ):
            with self.assertRaises(asyncio.CancelledError):
                await accounts._persist_account(db, "+100", me, "incoming")

        rollback.assert_awaited_once()
        kwargs = rollback.await_args.kwargs
        self.assertEqual(kwargs["account_id"], 21)
        self.assertTrue(kwargs["existed"])
        self.assertFalse(kwargs["was_running"])


class AccountPersistRollbackSourceGuardTests(unittest.TestCase):
    def test_accounts_router_has_no_broad_exception_suppressor(self):
        source = (
            __import__("pathlib").Path(__file__).resolve().parents[1]
            / "app"
            / "routers"
            / "accounts.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("suppress(Exception)", source)
        self.assertNotIn("from contextlib import suppress", source)


if __name__ == "__main__":
    unittest.main()
