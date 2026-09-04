import asyncio
import time
import unittest
from unittest.mock import AsyncMock

from telethon.errors import FloodWaitError

from app.config import settings
from app.tg_manager import TgClientManager


class AccountSchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.old_rate = (settings.RATE_MIN, settings.RATE_MAX)
        settings.RATE_MIN = 0
        settings.RATE_MAX = 0
        self.manager = TgClientManager()

    async def asyncTearDown(self):
        settings.RATE_MIN, settings.RATE_MAX = self.old_rate

    async def test_same_account_actions_are_serialized(self):
        active = 0
        overlap = False
        order = []

        async def action(name, delay):
            nonlocal active, overlap
            active += 1
            overlap = overlap or active > 1
            order.append(f"{name}:start")
            await asyncio.sleep(delay)
            order.append(f"{name}:end")
            active -= 1

        first = asyncio.create_task(
            self.manager.run_account_action(1, lambda: action("a", 0.05))
        )
        await asyncio.sleep(0.01)
        second = asyncio.create_task(
            self.manager.run_account_action(1, lambda: action("b", 0))
        )
        await asyncio.gather(first, second)

        self.assertFalse(overlap)
        self.assertEqual(order, ["a:start", "a:end", "b:start", "b:end"])

    async def test_different_accounts_run_in_parallel(self):
        both_started = asyncio.Event()
        active = 0
        max_active = 0

        async def action():
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            if active == 2:
                both_started.set()
            await asyncio.wait_for(both_started.wait(), timeout=0.5)
            active -= 1

        await asyncio.gather(
            self.manager.run_account_action(1, action),
            self.manager.run_account_action(2, action),
        )
        self.assertEqual(max_active, 2)

    async def test_flood_wait_pauses_only_affected_account(self):
        flood = FloodWaitError(request=None, capture=1)
        flood.seconds = 0.08

        async def limited():
            raise flood

        with self.assertRaises(FloodWaitError):
            await self.manager.run_account_action(1, limited)

        started = time.monotonic()
        same_account = asyncio.create_task(
            self.manager.run_account_action(1, lambda: asyncio.sleep(0))
        )
        other_account = asyncio.create_task(
            self.manager.run_account_action(2, lambda: asyncio.sleep(0))
        )
        await other_account
        other_elapsed = time.monotonic() - started
        await same_account
        same_elapsed = time.monotonic() - started

        self.assertLess(other_elapsed, 0.05)
        self.assertGreaterEqual(same_elapsed, 0.06)

    async def test_normal_pacing_is_per_account(self):
        settings.RATE_MIN = settings.RATE_MAX = 0.06
        await self.manager.run_account_action(1, lambda: asyncio.sleep(0))

        started = time.monotonic()
        await asyncio.gather(
            self.manager.run_account_action(1, lambda: asyncio.sleep(0)),
            self.manager.run_account_action(2, lambda: asyncio.sleep(0)),
        )
        self.assertGreaterEqual(time.monotonic() - started, 0.045)

    async def test_shutdown_rejects_new_actions(self):
        await self.manager.shutdown(action_timeout=0.01)
        with self.assertRaisesRegex(RuntimeError, "shutting down"):
            await self.manager.run_account_action(1, lambda: asyncio.sleep(0))

    async def test_shutdown_waits_for_inflight_action(self):
        entered = asyncio.Event()
        release = asyncio.Event()

        async def action():
            entered.set()
            await release.wait()

        running = asyncio.create_task(self.manager.run_account_action(1, action))
        await entered.wait()
        shutting_down = asyncio.create_task(self.manager.shutdown(action_timeout=0.5))
        await asyncio.sleep(0.02)
        self.assertFalse(shutting_down.done())
        release.set()
        await asyncio.gather(running, shutting_down)

    async def test_reconnect_backoff_stops_after_bounded_failures(self):
        self.manager._set_status = AsyncMock()
        for expected_attempt in range(1, 5):
            await self.manager._record_connection_failure(7, OSError("offline"))
            self.assertEqual(self.manager._reconnect_attempts[7], expected_attempt)
            self.assertIn(7, self.manager._reconnect_at)
            self.assertNotIn(7, self.manager._reconnect_exhausted)

        await self.manager._record_connection_failure(7, OSError("offline"))
        self.assertIn(7, self.manager._reconnect_exhausted)
        self.assertNotIn(7, self.manager._reconnect_at)

    async def test_revoked_session_is_not_scheduled_for_reconnect(self):
        SessionRevokedError = type("SessionRevokedError", (Exception,), {})
        self.manager._set_status = AsyncMock()
        await self.manager._record_connection_failure(9, SessionRevokedError())
        self.manager._set_status.assert_awaited_once_with(9, "session_revoked")
        self.assertIn(9, self.manager._reconnect_exhausted)
        self.assertNotIn(9, self.manager._reconnect_at)


class AuthVerifyThrottlingTests(unittest.IsolatedAsyncioTestCase):
    """Verify verify_authorizations_all() staggers expensive auth RPCs."""

    class _FakeClient:
        def __init__(self, connected=True, authorized=True):
            self._connected = connected
            self._authorized = authorized
            self.is_user_authorized = AsyncMock(return_value=authorized)

        def is_connected(self):
            return self._connected

    async def asyncSetUp(self):
        self.old = (
            settings.STATUS_POLL_SECS,
            settings.STATUS_AUTH_INTERVAL,
            settings.RATE_MIN,
            settings.RATE_MAX,
        )
        settings.STATUS_POLL_SECS = 1.0
        settings.STATUS_AUTH_INTERVAL = 30.0
        settings.RATE_MIN = 0
        settings.RATE_MAX = 0
        self.manager = TgClientManager()
        self.manager._set_status = AsyncMock()
        self.manager._mark_banned = AsyncMock()
        self.manager._reset_reconnect = lambda aid: None

    async def asyncTearDown(self):
        settings.STATUS_POLL_SECS, settings.STATUS_AUTH_INTERVAL, \
            settings.RATE_MIN, settings.RATE_MAX = self.old

    async def test_auth_verify_is_staggered_across_ticks(self):
        # 10 connected accounts, interval/poll = 30 -> slice_n=30, so each tick
        # verifies only a fraction (ceil spread via step slicing). With 10
        # accounts and slice_n=30 the cursor step of 30 yields ~1 account/tick.
        for i in range(1, 11):
            self.manager._clients[i] = self._FakeClient()

        first = await self.manager.verify_authorizations_all()
        self.assertLess(first, 11)  # not all in one tick

        total_authorized_calls = sum(
            c.is_user_authorized.await_count for c in self.manager._clients.values()
        )
        self.assertLessEqual(first, 1)  # step of 30 over a 10-length list -> <=1

    async def test_auth_verify_respects_throttle_interval(self):
        for i in (1, 2, 3, 4):
            self.manager._clients[i] = self._FakeClient()
            self.manager._last_auth_check[i] = time.monotonic()  # all "fresh"

        await self.manager.verify_authorizations_all()
        # Not done yet this tick because every account was just verified.
        for c in self.manager._clients.values():
            self.assertEqual(c.is_user_authorized.await_count, 0)

    async def test_disconnected_client_not_verified(self):
        cli = self._FakeClient(connected=False)
        self.manager._clients[1] = cli
        await self.manager.verify_authorizations_all()
        self.assertEqual(cli.is_user_authorized.await_count, 0)


if __name__ == "__main__":
    unittest.main()
