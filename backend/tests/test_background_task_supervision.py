import asyncio
import unittest
from pathlib import Path

from app.tg_manager import TgClientManager


class BackgroundTaskSupervisionTests(unittest.IsolatedAsyncioTestCase):
    async def test_successful_background_task_is_removed_without_warning(self):
        manager = TgClientManager()

        async def succeed():
            await asyncio.sleep(0)

        with self.assertNoLogs("tg_manager", level="WARNING"):
            task = manager._track_background_task(
                asyncio.create_task(succeed()),
                name="success-task",
            )
            await task
            await asyncio.sleep(0)

        self.assertNotIn(task, manager._background_tasks)

    async def test_cancelled_background_task_is_removed_without_warning(self):
        manager = TgClientManager()
        blocker = asyncio.Event()

        async def wait_forever():
            await blocker.wait()

        with self.assertNoLogs("tg_manager", level="WARNING"):
            task = manager._track_background_task(
                asyncio.create_task(wait_forever()),
                name="cancel-task",
            )
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await asyncio.sleep(0)

        self.assertTrue(task.cancelled())
        self.assertNotIn(task, manager._background_tasks)

    async def test_failed_background_task_is_logged_safely_and_removed(self):
        manager = TgClientManager()

        async def fail():
            raise RuntimeError("sensitive-marker")

        with self.assertLogs("tg_manager", level="WARNING") as captured:
            task = manager._track_background_task(
                asyncio.create_task(fail()),
                name="failing-task",
            )
            await asyncio.gather(task, return_exceptions=True)
            await asyncio.sleep(0)

        self.assertNotIn(task, manager._background_tasks)
        joined = "\n".join(captured.output)
        self.assertIn("background task failed", joined)
        self.assertIn("name=failing-task", joined)
        self.assertIn("RuntimeError", joined)
        self.assertNotIn("sensitive-marker", joined)


class BackgroundTaskSupervisionSourceGuardTests(unittest.TestCase):
    def test_startup_background_tasks_use_supervision_helper(self):
        source_path = Path(__file__).resolve().parents[1] / "app" / "tg_manager.py"
        source = source_path.read_text(encoding="utf-8")

        self.assertIn('name="pending_janitor"', source)
        self.assertIn('name="session_folder_sync"', source)
        self.assertNotIn(
            "janitor.add_done_callback(self._background_tasks.discard)",
            source,
        )
        self.assertNotIn(
            "sync_task.add_done_callback(self._background_tasks.discard)",
            source,
        )

    def test_startup_logs_do_not_format_raw_phone_or_exception(self):
        source_path = Path(__file__).resolve().parents[1] / "app" / "tg_manager.py"
        source = source_path.read_text(encoding="utf-8")

        self.assertNotIn('log.warning("Failed to start client for %s: %s", acc.phone, e)', source)
        self.assertNotIn('log.warning("session folder sync failed: %s", e)', source)
        self.assertIn("startup client failed account=%s error_type=%s", source)
        self.assertIn("session folder sync failed error_type=%s", source)


if __name__ == "__main__":
    unittest.main()
