import asyncio
import time
import unittest

from telethon.errors import FloodWaitError

from app.account_scheduler import AccountActionScheduler
from app.config import settings


class AccountActionSchedulerUnitTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.old_rate = (settings.RATE_MIN, settings.RATE_MAX)
        settings.RATE_MIN = 0
        settings.RATE_MAX = 0
        self.scheduler = AccountActionScheduler()

    async def asyncTearDown(self):
        settings.RATE_MIN, settings.RATE_MAX = self.old_rate

    async def test_same_account_is_serialized(self):
        order = []

        async def action(name, delay):
            order.append(f"{name}:start")
            await asyncio.sleep(delay)
            order.append(f"{name}:end")

        first = asyncio.create_task(self.scheduler.run(1, lambda: action("a", 0.03)))
        await asyncio.sleep(0.005)
        second = asyncio.create_task(self.scheduler.run(1, lambda: action("b", 0)))
        await asyncio.gather(first, second)
        self.assertEqual(order, ["a:start", "a:end", "b:start", "b:end"])

    async def test_different_accounts_can_overlap(self):
        release = asyncio.Event()
        entered = 0
        both = asyncio.Event()

        async def action():
            nonlocal entered
            entered += 1
            if entered == 2:
                both.set()
            await asyncio.wait_for(both.wait(), timeout=0.5)
            await release.wait()

        a = asyncio.create_task(self.scheduler.run(1, action))
        b = asyncio.create_task(self.scheduler.run(2, action))
        await asyncio.wait_for(both.wait(), timeout=0.5)
        self.assertEqual(self.scheduler.active_actions, 2)
        release.set()
        await asyncio.gather(a, b)

    async def test_flood_wait_is_scoped_to_one_account(self):
        flood = FloodWaitError(request=None, capture=1)
        flood.seconds = 0.06

        async def limited():
            raise flood

        with self.assertRaises(FloodWaitError):
            await self.scheduler.run(1, limited)

        started = time.monotonic()
        other = asyncio.create_task(self.scheduler.run(2, lambda: asyncio.sleep(0)))
        affected = asyncio.create_task(self.scheduler.run(1, lambda: asyncio.sleep(0)))
        await other
        other_elapsed = time.monotonic() - started
        await affected
        affected_elapsed = time.monotonic() - started

        self.assertLess(other_elapsed, 0.04)
        self.assertGreaterEqual(affected_elapsed, 0.045)

    async def test_stop_accepting_wait_idle_and_resume(self):
        entered = asyncio.Event()
        release = asyncio.Event()

        async def action():
            entered.set()
            await release.wait()

        running = asyncio.create_task(self.scheduler.run(1, action))
        await entered.wait()
        self.scheduler.stop_accepting()

        with self.assertRaisesRegex(RuntimeError, "shutting down"):
            await self.scheduler.run(2, lambda: asyncio.sleep(0))

        self.assertFalse(await self.scheduler.wait_idle(0.01))
        release.set()
        await running
        self.assertTrue(await self.scheduler.wait_idle(0.1))

        self.scheduler.resume()
        await self.scheduler.run(2, lambda: asyncio.sleep(0))


if __name__ == "__main__":
    unittest.main()
