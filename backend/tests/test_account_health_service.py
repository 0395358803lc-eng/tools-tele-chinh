import unittest
from pathlib import Path

from app.tg_manager import TgClientManager


class AccountHealthServiceCompatibilityTests(unittest.TestCase):
    def test_health_service_uses_manager_compatibility_state_objects(self):
        manager = TgClientManager()
        service = manager._account_health

        self.assertIs(manager._reconnect_attempts, service._reconnect_attempts)
        self.assertIs(manager._reconnect_at, service._reconnect_at)
        self.assertIs(manager._reconnect_exhausted, service._reconnect_exhausted)
        self.assertIs(manager._manual_disconnect, service._manual_disconnect)
        self.assertIs(manager._last_auth_check, service._last_auth_check)

    def test_reset_reconnect_facade_mutates_existing_manager_state(self):
        manager = TgClientManager()
        manager._reconnect_attempts[7] = 3
        manager._reconnect_at[7] = 123.0
        manager._reconnect_exhausted.add(7)

        manager._reset_reconnect(7)

        self.assertNotIn(7, manager._reconnect_attempts)
        self.assertNotIn(7, manager._reconnect_at)
        self.assertNotIn(7, manager._reconnect_exhausted)

    def test_manager_keeps_only_account_health_facades(self):
        app_root = Path(__file__).resolve().parents[1] / "app"
        manager_source = (app_root / "tg_manager.py").read_text(encoding="utf-8")
        service_source = (app_root / "account_health.py").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("connected_ids: list[int] = []", manager_source)
        self.assertNotIn("due: list[tuple[int, Account]] = []", manager_source)
        self.assertIn("connected_ids: list[int] = []", service_source)
        self.assertIn("due: list[tuple[int, Account]] = []", service_source)
        self.assertIn("self._account_health.refresh_status_all", manager_source)
        self.assertIn(
            "self._account_health.verify_authorizations_all",
            manager_source,
        )


if __name__ == "__main__":
    unittest.main()
