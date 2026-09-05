import ast
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.routers import groups


class _ExpectedRpcError(Exception):
    pass


class _ResolveBugClient:
    async def get_entity(self, _target):
        raise RuntimeError("programming bug")


class _ResolveRpcClient:
    async def get_entity(self, _target):
        raise _ExpectedRpcError("telegram rejected")


class _FakeChannel:
    id = 123


class _MembershipClient:
    def __init__(self, error):
        self.error = error
        self.calls = []

    async def get_entity(self, _target):
        return _FakeChannel()

    async def __call__(self, request):
        self.calls.append(request)
        if request[0] == "participant":
            raise self.error
        return None


class _DeleteClient:
    def __init__(self, delete_error):
        self.delete_error = delete_error

    async def get_me(self):
        return object()

    async def iter_messages(self, _entity, **_kwargs):
        yield SimpleNamespace(id=77)

    async def delete_messages(self, _entity, _batch, revoke=True):
        if self.delete_error is not None:
            raise self.delete_error


class GroupExceptionBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_unexpected_target_resolution_error_propagates(self):
        with self.assertRaises(RuntimeError):
            await groups._leave_by_target_with_client(_ResolveBugClient(), "@target")

    async def test_expected_rpc_target_resolution_becomes_skipped(self):
        with patch.object(groups, "RPCError", _ExpectedRpcError):
            with self.assertLogs("groups", level="INFO") as captured:
                result = await groups._leave_by_target_with_client(
                    _ResolveRpcClient(), "@target"
                )
        self.assertEqual(result.status, "skipped")
        self.assertEqual(result.message_code, "groups.cantResolveTarget")
        self.assertTrue(
            any("group target resolution failed" in line for line in captured.output)
        )

    async def test_unexpected_membership_probe_error_is_not_swallowed(self):
        client = _MembershipClient(RuntimeError("programming bug"))
        with (
            patch.object(groups, "Channel", _FakeChannel),
            patch.object(
                groups,
                "GetParticipantRequest",
                side_effect=lambda entity, who: ("participant", entity, who),
            ),
        ):
            with self.assertRaises(RuntimeError):
                await groups._leave_by_target_with_client(client, "@target")

    async def test_expected_rpc_membership_probe_falls_through_with_log(self):
        client = _MembershipClient(_ExpectedRpcError("telegram state unavailable"))
        with (
            patch.object(groups, "RPCError", _ExpectedRpcError),
            patch.object(groups, "Channel", _FakeChannel),
            patch.object(
                groups,
                "GetParticipantRequest",
                side_effect=lambda entity, who: ("participant", entity, who),
            ),
            patch.object(
                groups,
                "LeaveChannelRequest",
                side_effect=lambda entity: ("leave", entity),
            ),
        ):
            with self.assertLogs("groups", level="WARNING") as captured:
                result = await groups._leave_by_target_with_client(client, "@target")
        self.assertEqual(result.status, "ok")
        self.assertTrue(any(call[0] == "leave" for call in client.calls))
        self.assertTrue(
            any("group membership probe failed" in line for line in captured.output)
        )

    async def test_unexpected_bulk_leave_error_propagates(self):
        entity = SimpleNamespace(id=44)
        with (
            patch(
                "app.routers.groups._collect_chats",
                new=AsyncMock(return_value=[entity]),
            ),
            patch(
                "app.routers.groups._leave_entity",
                new=AsyncMock(side_effect=RuntimeError("programming bug")),
            ),
        ):
            with self.assertRaises(RuntimeError):
                await groups._leave_all_for_client(object(), 9)

    async def test_expected_rpc_bulk_leave_error_is_counted_and_logged(self):
        entity = SimpleNamespace(id=44)
        with (
            patch.object(groups, "RPCError", _ExpectedRpcError),
            patch(
                "app.routers.groups._collect_chats",
                new=AsyncMock(return_value=[entity]),
            ),
            patch(
                "app.routers.groups._leave_entity",
                new=AsyncMock(side_effect=_ExpectedRpcError("telegram rejected")),
            ),
            patch("app.routers.groups.asyncio.sleep", new=AsyncMock()),
        ):
            with self.assertLogs("groups", level="WARNING") as captured:
                result = await groups._leave_all_for_client(object(), 9)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.params["errors"], 1)
        self.assertTrue(any("bulk leave failed" in line for line in captured.output))

    async def test_unexpected_delete_batch_error_propagates(self):
        entity = SimpleNamespace(id=88)
        client = _DeleteClient(RuntimeError("programming bug"))
        with patch(
            "app.routers.groups._collect_chats",
            new=AsyncMock(return_value=[entity]),
        ):
            with self.assertRaises(RuntimeError):
                await groups._delete_all_my_messages_for_client(client, 7, 100)

    async def test_expected_rpc_delete_batch_error_is_logged_and_contained(self):
        entity = SimpleNamespace(id=88)
        client = _DeleteClient(_ExpectedRpcError("telegram rejected"))
        with (
            patch.object(groups, "RPCError", _ExpectedRpcError),
            patch(
                "app.routers.groups._collect_chats",
                new=AsyncMock(return_value=[entity]),
            ),
            patch("app.routers.groups.asyncio.sleep", new=AsyncMock()),
        ):
            with self.assertLogs("groups", level="WARNING") as captured:
                result = await groups._delete_all_my_messages_for_client(client, 7, 100)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.params["deleted"], 0)
        self.assertTrue(
            any("bulk message delete batch failed" in line for line in captured.output)
        )


class GroupExceptionSourceGuardTests(unittest.TestCase):
    def test_groups_router_has_no_silent_broad_exception_pass(self):
        source_path = Path(__file__).resolve().parents[1] / "app" / "routers" / "groups.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        offenders = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler) or node.type is None:
                continue
            catches_exception = isinstance(node.type, ast.Name) and node.type.id == "Exception"
            if isinstance(node.type, ast.Tuple):
                catches_exception = any(
                    isinstance(item, ast.Name) and item.id == "Exception"
                    for item in node.type.elts
                )
            if catches_exception and len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                offenders.append(getattr(node, "lineno", -1))
        self.assertEqual(offenders, [], f"silent broad exception handlers at lines {offenders}")


if __name__ == "__main__":
    unittest.main()
