import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.tg_manager import TgClientManager


class QRLoginServiceCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_manager_qr_state_aliases_point_to_service_state(self):
        manager = TgClientManager()
        self.assertIs(manager._qr_pending, manager._qr_login.pending)
        self.assertIs(manager._qr_locks, manager._qr_login.locks)
        self.assertIs(manager._qr_completed, manager._qr_login.completed)

    async def test_manager_save_2fa_patch_path_remains_compatible(self):
        manager = TgClientManager()
        with patch(
            "app.tg_manager.secrets_store.save_2fa",
            new=AsyncMock(),
        ) as save:
            await manager._qr_login._save_2fa(
                "account-1",
                "test-value",
            )
        save.assert_awaited_once_with("account-1", "test-value")

    def test_manager_keeps_only_qr_login_facades(self):
        app_root = Path(__file__).resolve().parents[1] / "app"
        manager_source = (app_root / "tg_manager.py").read_text(encoding="utf-8")
        service_source = (app_root / "qr_login.py").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("qr_login = await", manager_source)
        self.assertNotIn('entry["qr_login"] = qr_login', manager_source)
        self.assertIn("qr_login = await", service_source)
        self.assertIn('entry["qr_login"] = qr_login', service_source)
        self.assertIn("self._qr_login.start", manager_source)
        self.assertIn("self._qr_login.recreate", manager_source)
        self.assertIn("self._qr_login.submit_2fa", manager_source)


if __name__ == "__main__":
    unittest.main()
