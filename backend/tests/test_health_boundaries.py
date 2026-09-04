import unittest
from unittest.mock import AsyncMock

from app.config import settings
from app.tg_manager import TgClientManager


class _AuthClient:
    def __init__(self, *, connected=True, connected_error=None, auth_error=None):
        self.connected = connected
        self.connected_error = connected_error
        self.is_user_authorized = AsyncMock(side_effect=auth_error)

    def is_connected(self):
        if self.connected_error is not None:
            raise self.connected_error
        return self.connected


class HealthBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.old = (settings.STATUS_POLL_SECS, settings.STATUS_AUTH_INTERVAL)
        settings.STATUS_POLL_SECS = 30.0
        settings.STATUS_AUTH_INTERVAL = 30.0
        self.manager = TgClientManager()
        self.manager._set_status = AsyncMock()

    async def asyncTearDown(self):
        settings.STATUS_POLL_SECS, settings.STATUS_AUTH_INTERVAL = self.old

    async def test_transient_authorization_network_error_is_deferred(self):
        client = _AuthClient(auth_error=OSError("offline"))
        self.manager._clients[1] = client

        verified = await self.manager.verify_authorizations_all()

        self.assertEqual(verified, 0)
        self.assertEqual(client.is_user_authorized.await_count, 1)
        self.manager._set_status.assert_not_awaited()

    async def test_authorization_programming_error_is_not_silently_swallowed(self):
        client = _AuthClient(auth_error=ValueError("bad internal state"))
        self.manager._clients[1] = client

        with self.assertRaisesRegex(ValueError, "bad internal state"):
            await self.manager.verify_authorizations_all()

    async def test_connection_state_programming_error_is_not_recast_as_disconnect(self):
        client = _AuthClient(connected_error=ValueError("bad internal state"))
        self.manager._clients[1] = client

        with self.assertRaisesRegex(ValueError, "bad internal state"):
            await self.manager.verify_authorizations_all()


if __name__ == "__main__":
    unittest.main()
