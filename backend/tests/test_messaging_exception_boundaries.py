import ast
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app.routers import messaging


class _ExpectedRpcError(Exception):
    pass


class _CallErrorClient:
    def __init__(self, error):
        self.error = error

    async def __call__(self, _request):
        raise self.error


class _WipeProbeClient:
    def __init__(self, probe_error):
        self.probe_error = probe_error
        self.deleted = False

    async def get_entity(self, _target):
        return SimpleNamespace(id=55)

    async def iter_messages(self, _entity, limit=1):
        if self.probe_error is not None:
            raise self.probe_error
        if False:
            yield None

    async def delete_dialog(self, _entity, revoke=True):
        self.deleted = True


class _BotClient:
    async def get_entity(self, _target):
        return SimpleNamespace(id=77, bot=True, username="TestBot")

    async def send_message(self, _entity, _text):
        return None


class MessagingExceptionBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_global_reactions_expected_rpc_error_is_logged_and_contained(self):
        client = _CallErrorClient(_ExpectedRpcError("telegram unavailable"))
        with patch.object(messaging, "RPCError", _ExpectedRpcError):
            with self.assertLogs("messaging", level="INFO") as captured:
                result = await messaging._global_standard_reactions(client)
        self.assertEqual(result, [])
        self.assertTrue(
            any("global reactions unavailable" in line for line in captured.output)
        )

    async def test_global_reactions_unexpected_error_propagates(self):
        client = _CallErrorClient(RuntimeError("programming bug"))
        with self.assertRaises(RuntimeError):
            await messaging._global_standard_reactions(client)

    async def test_custom_reaction_expected_rpc_error_uses_generic_metadata(self):
        client = _CallErrorClient(_ExpectedRpcError("telegram unavailable"))
        with patch.object(messaging, "RPCError", _ExpectedRpcError):
            with self.assertLogs("messaging", level="INFO") as captured:
                result = await messaging._resolve_custom(client, [101, 202])
        self.assertEqual([item.id for item in result], [101, 202])
        self.assertTrue(all(item.alt == "⭐" for item in result))
        self.assertTrue(
            any("custom reaction metadata unavailable" in line for line in captured.output)
        )

    async def test_custom_reaction_unexpected_error_propagates(self):
        client = _CallErrorClient(RuntimeError("programming bug"))
        with self.assertRaises(RuntimeError):
            await messaging._resolve_custom(client, [101])

    async def test_wipe_probe_expected_rpc_error_logs_then_deletes(self):
        client = _WipeProbeClient(_ExpectedRpcError("telegram unavailable"))
        with patch.object(messaging, "RPCError", _ExpectedRpcError):
            with self.assertLogs("messaging", level="INFO") as captured:
                result = await messaging._wipe_chat_for_client(client, "@target")
        self.assertTrue(client.deleted)
        self.assertEqual(result.status, "ok")
        self.assertTrue(
            any("chat wipe existence probe unavailable" in line for line in captured.output)
        )

    async def test_wipe_probe_unexpected_error_does_not_delete(self):
        client = _WipeProbeClient(RuntimeError("programming bug"))
        with self.assertRaises(RuntimeError):
            await messaging._wipe_chat_for_client(client, "@target")
        self.assertFalse(client.deleted)

    async def test_startbot_expected_rpc_error_uses_single_message_fallback(self):
        client = _BotClient()
        action = AsyncMock(side_effect=[_ExpectedRpcError("telegram rejected"), None])
        with (
            patch.object(messaging, "RPCError", _ExpectedRpcError),
            patch.object(messaging.manager, "get", return_value=client),
            patch.object(messaging.manager, "run_account_action", new=action),
            patch("app.routers.messaging._history", new=AsyncMock(return_value=[])),
            patch("app.routers.messaging.asyncio.sleep", new=AsyncMock()),
        ):
            with self.assertLogs("messaging", level="INFO") as captured:
                result = await messaging.open_chat(
                    1,
                    messaging.OpenChatIn(
                        input="https://t.me/TestBot?start=ref123",
                        limit=1,
                    ),
                )
        self.assertTrue(result["started"])
        self.assertEqual(action.await_count, 2)
        self.assertTrue(
            any("StartBot rejected" in line for line in captured.output)
        )

    async def test_startbot_unexpected_error_does_not_trigger_fallback_mutation(self):
        client = _BotClient()
        action = AsyncMock(side_effect=RuntimeError("programming bug"))
        with (
            patch.object(messaging.manager, "get", return_value=client),
            patch.object(messaging.manager, "run_account_action", new=action),
        ):
            with self.assertRaises(HTTPException):
                await messaging.open_chat(
                    1,
                    messaging.OpenChatIn(
                        input="https://t.me/TestBot?start=ref123",
                        limit=1,
                    ),
                )
        self.assertEqual(action.await_count, 1)


class MessagingExceptionSourceGuardTests(unittest.TestCase):
    def test_internal_best_effort_helpers_do_not_catch_broad_exception(self):
        source_path = Path(__file__).resolve().parents[1] / "app" / "routers" / "messaging.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        guarded = {
            "_global_standard_reactions",
            "_resolve_custom",
            "_wipe_chat_for_client",
        }
        offenders = []
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name not in guarded:
                continue
            for child in ast.walk(node):
                if not isinstance(child, ast.ExceptHandler) or child.type is None:
                    continue
                if isinstance(child.type, ast.Name) and child.type.id == "Exception":
                    offenders.append((node.name, getattr(child, "lineno", -1)))
        self.assertEqual(offenders, [], f"broad helper exception handlers: {offenders}")


if __name__ == "__main__":
    unittest.main()
