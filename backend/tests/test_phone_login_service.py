import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from app.phone_login import PhoneLoginService
from app.tg_manager import TgClientManager


class _Expected2FA(Exception):
    pass


def _service(*, safe_disconnect=None, remove=None):
    return PhoneLoginService(
        normalize_phone=lambda value: value.strip(),
        temp_session_path=lambda phone: "/tmp/login_test",
        safe_disconnect=safe_disconnect or AsyncMock(),
        remove_session_files=remove or Mock(),
        phone_file_part=lambda phone: "0001",
    )


class PhoneLoginServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_code_success_keeps_pending_client(self):
        service = _service()
        client = SimpleNamespace(
            connect=AsyncMock(),
            send_code_request=AsyncMock(
                return_value=SimpleNamespace(phone_code_hash="test-hash")
            ),
        )
        fake_settings = SimpleNamespace(
            api_configured=True,
            tg_api_id=123,
            TG_API_HASH="test",
            TELEGRAM_CONNECT_TIMEOUT=20,
            LOGIN_PENDING_TTL_SECONDS=300,
        )

        with (
            patch("app.phone_login.settings", fake_settings),
            patch("app.phone_login.TelegramClient", return_value=client),
        ):
            result = await service.send_code("account-1")

        self.assertEqual(result, "test-hash")
        self.assertIn("account-1", service.pending)
        self.assertIs(service.pending["account-1"]["client"], client)
        client.connect.assert_awaited_once()
        client.send_code_request.assert_awaited_once_with("account-1")

    async def test_hard_code_failure_discards_pending_session(self):
        safe_disconnect = AsyncMock()
        remove = Mock()
        service = _service(
            safe_disconnect=safe_disconnect,
            remove=remove,
        )
        client = SimpleNamespace(
            sign_in=AsyncMock(side_effect=RuntimeError("test-marker")),
        )
        service.pending["account-1"] = {
            "client": client,
            "phone_code_hash": "hash",
            "session_path": "phone-temp",
            "authorized": False,
        }

        with self.assertRaisesRegex(RuntimeError, "test-marker"):
            await service.submit_code("account-1", "12345")

        self.assertNotIn("account-1", service.pending)
        safe_disconnect.assert_awaited_once()
        remove.assert_called_once_with("phone-temp")

    async def test_2fa_requirement_keeps_pending_session(self):
        service = _service()
        client = SimpleNamespace(
            sign_in=AsyncMock(side_effect=_Expected2FA()),
        )
        service.pending["account-1"] = {
            "client": client,
            "phone_code_hash": "hash",
            "session_path": "phone-temp",
            "authorized": False,
        }

        with patch("app.phone_login.SessionPasswordNeededError", _Expected2FA):
            me, needs_2fa = await service.submit_code(
                "account-1",
                "12345",
            )

        self.assertIsNone(me)
        self.assertTrue(needs_2fa)
        self.assertTrue(service.pending["account-1"]["needs_2fa"])

    async def test_secure_store_failure_is_sanitized_and_authorization_survives(self):
        safe_disconnect = AsyncMock()
        service = _service(safe_disconnect=safe_disconnect)
        user = SimpleNamespace(id=7)
        client = SimpleNamespace(
            sign_in=AsyncMock(),
            get_me=AsyncMock(return_value=user),
        )
        service.pending["account-1"] = {
            "client": client,
            "session_path": "phone-temp",
            "authorized": False,
        }

        with (
            patch(
                "app.phone_login.secrets_store.save_2fa",
                new=AsyncMock(side_effect=RuntimeError("test-marker")),
            ),
            self.assertLogs("phone_login", level="WARNING") as captured,
        ):
            result = await service.submit_2fa(
                "account-1",
                "test-value",
            )

        self.assertIs(result, user)
        self.assertTrue(service.pending["account-1"]["authorized"])
        joined = "\n".join(captured.output)
        self.assertIn("RuntimeError", joined)
        self.assertNotIn("test-marker", joined)
        self.assertNotIn("test-value", joined)
        safe_disconnect.assert_awaited_once()

    async def test_expired_pending_cleanup_removes_temp_session(self):
        safe_disconnect = AsyncMock()
        remove = Mock()
        service = _service(
            safe_disconnect=safe_disconnect,
            remove=remove,
        )
        service.pending["account-1"] = {
            "client": SimpleNamespace(),
            "session_path": "expired-temp",
            "expires_at": 0,
        }

        await service.cleanup_expired()

        self.assertNotIn("account-1", service.pending)
        safe_disconnect.assert_awaited_once()
        remove.assert_called_once_with("expired-temp")


class PhoneLoginManagerCompatibilityTests(unittest.TestCase):
    def test_manager_pending_and_lock_aliases_point_to_service_state(self):
        manager = TgClientManager()
        self.assertIs(manager._pending, manager._phone_login.pending)
        self.assertIs(manager._phone_locks, manager._phone_login.locks)

    def test_manager_keeps_only_phone_login_facade(self):
        app_root = Path(__file__).resolve().parents[1] / "app"
        manager_source = (app_root / "tg_manager.py").read_text(encoding="utf-8")
        service_source = (app_root / "phone_login.py").read_text(encoding="utf-8")

        self.assertNotIn("send_code_request(phone)", manager_source)
        self.assertNotIn("cli.sign_in(phone=phone", manager_source)
        self.assertIn("send_code_request(phone)", service_source)
        self.assertIn("self._phone_login.send_code", manager_source)
        self.assertIn("self._phone_login.submit_code", manager_source)
        self.assertIn("self._phone_login.submit_2fa", manager_source)


if __name__ == "__main__":
    unittest.main()
