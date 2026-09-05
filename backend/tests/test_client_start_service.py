import asyncio
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from app.client_start import ClientStartService
from app.tg_manager import TgClientManager


class _AsyncLock:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _service(*, client=None, record_failure=None):
    clients = {}
    manual = set()
    exhausted = set()
    fake_client = client or SimpleNamespace(
        is_connected=lambda: False,
        connect=AsyncMock(),
        is_user_authorized=AsyncMock(return_value=True),
        get_me=AsyncMock(return_value=SimpleNamespace(id=1)),
    )
    service = ClientStartService(
        clients=clients,
        manual_disconnect=manual,
        reconnect_exhausted=exhausted,
        client_factory=Mock(return_value=fake_client),
        account_lock=AsyncMock(return_value=_AsyncLock()),
        session_path_for_account=Mock(return_value="session-base"),
        reset_reconnect=Mock(),
        safe_disconnect=AsyncMock(),
        record_connection_failure=record_failure or AsyncMock(),
        set_status=AsyncMock(),
        attach_listener=Mock(),
        sync_profile=AsyncMock(),
        backfill_security=AsyncMock(),
    )
    return service, clients, manual, exhausted, fake_client


class ClientStartServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_authorized_start_registers_client_and_runs_post_start_hooks(self):
        service, clients, _manual, _exhausted, client = _service()
        acc = SimpleNamespace(id=7)

        result = await service.start_locked(acc, reset_reconnect=True)

        self.assertIs(result, client)
        self.assertIs(clients[7], client)
        service._attach_listener.assert_called_once_with(7, client)
        service._set_status.assert_awaited_with(7, "connected")
        service._sync_profile.assert_awaited_once()
        service._backfill_security.assert_awaited_once_with(
            7,
            client,
            limit=50,
        )

    async def test_unauthorized_session_is_disconnected_and_marked_revoked(self):
        client = SimpleNamespace(
            is_connected=lambda: False,
            connect=AsyncMock(),
            is_user_authorized=AsyncMock(return_value=False),
        )
        service, clients, _manual, exhausted, _ = _service(client=client)
        acc = SimpleNamespace(id=9)

        with self.assertRaisesRegex(RuntimeError, "not authorized"):
            await service.start_locked(acc, reset_reconnect=True)

        self.assertNotIn(9, clients)
        self.assertIn(9, exhausted)
        service._safe_disconnect.assert_awaited_once()
        service._set_status.assert_awaited_with(9, "session_revoked")
        service._attach_listener.assert_not_called()

    async def test_connect_failure_is_cleaned_recorded_and_propagated(self):
        client = SimpleNamespace(
            is_connected=lambda: False,
            connect=AsyncMock(side_effect=OSError("offline")),
            is_user_authorized=AsyncMock(),
        )
        record = AsyncMock()
        service, clients, _manual, _exhausted, _ = _service(
            client=client,
            record_failure=record,
        )
        acc = SimpleNamespace(id=11)

        with self.assertRaisesRegex(OSError, "offline"):
            await service.start_locked(acc, reset_reconnect=False)

        self.assertNotIn(11, clients)
        service._safe_disconnect.assert_awaited_once()
        record.assert_awaited_once()
        self.assertEqual(record.await_args.args[0], 11)

    async def test_transient_profile_failure_does_not_fail_authorized_start(self):
        client = SimpleNamespace(
            is_connected=lambda: False,
            connect=AsyncMock(),
            is_user_authorized=AsyncMock(return_value=True),
            get_me=AsyncMock(side_effect=OSError("offline")),
        )
        service, clients, _manual, _exhausted, _ = _service(client=client)
        acc = SimpleNamespace(id=12)

        with self.assertLogs("client_start", level="WARNING") as captured:
            result = await service.start_locked(acc, reset_reconnect=True)

        self.assertIs(result, client)
        self.assertIs(clients[12], client)
        service._sync_profile.assert_not_awaited()
        service._backfill_security.assert_awaited_once()
        self.assertNotIn("offline", "\n".join(captured.output))


class ClientStartManagerCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_manager_legacy_telegram_client_patch_path_still_controls_factory(self):
        manager = TgClientManager()
        acc = SimpleNamespace(id=15)
        client = SimpleNamespace(
            is_connected=lambda: False,
            connect=AsyncMock(),
            is_user_authorized=AsyncMock(return_value=True),
            get_me=AsyncMock(return_value=SimpleNamespace(id=15)),
        )
        manager._set_status = AsyncMock()
        manager._sync_profile = AsyncMock()
        manager._backfill_777000 = AsyncMock()
        manager._attach_listener = Mock()
        manager._reset_reconnect = Mock()

        with patch("app.tg_manager.TelegramClient", return_value=client) as factory:
            result = await manager._start_client_locked(
                acc,
                reset_reconnect=True,
            )

        self.assertIs(result, client)
        factory.assert_called_once()
        manager._attach_listener.assert_called_once_with(15, client)

    def test_manager_keeps_only_client_start_facade(self):
        app_root = Path(__file__).resolve().parents[1] / "app"
        manager_source = (app_root / "tg_manager.py").read_text(encoding="utf-8")
        service_source = (app_root / "client_start.py").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("authorized = await asyncio.wait_for", manager_source)
        self.assertIn("authorized = await asyncio.wait_for", service_source)
        self.assertIn("self._client_start.start_locked", manager_source)


if __name__ == "__main__":
    unittest.main()
