import ast
import asyncio
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from PIL import Image

from app import uploads
from app.routers import bulk


class _Scalars:
    def all(self):
        return []


class _Result:
    def scalars(self):
        return _Scalars()


class _Db:
    async def execute(self, _query):
        return _Result()


class _Upload:
    def __init__(self, data: bytes, *, filename="photo.png", content_type="image/png", error=None):
        self.filename = filename
        self.content_type = content_type
        self._data = data
        self._offset = 0
        self._error = error

    async def read(self, size=-1):
        if self._error is not None:
            error, self._error = self._error, None
            raise error
        if self._offset >= len(self._data):
            return b""
        if size is None or size < 0:
            size = len(self._data) - self._offset
        chunk = self._data[self._offset:self._offset + size]
        self._offset += len(chunk)
        return chunk


def _valid_png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (1, 1)).save(buffer, format="PNG")
    return buffer.getvalue()


class TempUploadCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def _call_with_tracked_tempfiles(self, upload_list):
        created = []
        original = tempfile.NamedTemporaryFile

        def tracking_tempfile(*args, **kwargs):
            handle = original(*args, **kwargs)
            created.append(handle.name)
            return handle

        with patch.object(bulk.tempfile, "NamedTemporaryFile", side_effect=tracking_tempfile):
            result = await bulk.bulk_photo("1", upload_list, _Db())
        return result, created

    async def test_validation_failure_removes_already_staged_files(self):
        created = []
        original = tempfile.NamedTemporaryFile

        def tracking_tempfile(*args, **kwargs):
            handle = original(*args, **kwargs)
            created.append(handle.name)
            return handle

        valid = _Upload(_valid_png())
        invalid = _Upload(b"not-an-image")
        with patch.object(bulk.tempfile, "NamedTemporaryFile", side_effect=tracking_tempfile):
            with self.assertRaises(HTTPException) as raised:
                await bulk.bulk_photo("1", [valid, invalid], _Db())

        self.assertEqual(raised.exception.status_code, 415)
        self.assertEqual(len(created), 1)
        self.assertFalse(Path(created[0]).exists())

    async def test_cancellation_removes_already_staged_files(self):
        created = []
        original = tempfile.NamedTemporaryFile

        def tracking_tempfile(*args, **kwargs):
            handle = original(*args, **kwargs)
            created.append(handle.name)
            return handle

        valid = _Upload(_valid_png())
        cancelled = _Upload(b"", error=asyncio.CancelledError())
        with patch.object(bulk.tempfile, "NamedTemporaryFile", side_effect=tracking_tempfile):
            with self.assertRaises(asyncio.CancelledError):
                await bulk.bulk_photo("1", [valid, cancelled], _Db())

        self.assertEqual(len(created), 1)
        self.assertFalse(Path(created[0]).exists())

    async def test_stream_completion_removes_staged_files(self):
        response, created = await self._call_with_tracked_tempfiles([_Upload(_valid_png())])
        self.assertEqual(len(created), 1)
        self.assertTrue(Path(created[0]).exists())

        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk)

        self.assertTrue(chunks)
        self.assertFalse(Path(created[0]).exists())


class CleanupHelperTests(unittest.TestCase):
    def test_cleanup_temp_files_removes_existing_and_ignores_missing(self):
        with tempfile.TemporaryDirectory() as raw:
            existing = Path(raw) / "staged.tmp"
            existing.write_bytes(b"x")
            missing = Path(raw) / "missing.tmp"

            uploads.cleanup_temp_files([str(existing), str(missing)])

            self.assertFalse(existing.exists())

    def test_cleanup_temp_files_logs_oserror_without_raising(self):
        path = Path(tempfile.gettempdir()) / "mtm-cleanup-denied.tmp"
        with patch.object(Path, "unlink", side_effect=PermissionError("denied")):
            with self.assertLogs("uploads", level="WARNING") as captured:
                uploads.cleanup_temp_files([str(path)])

        self.assertTrue(
            any("temporary upload cleanup failed" in line for line in captured.output)
        )


class BulkCleanupSourceGuardTests(unittest.TestCase):
    def test_bulk_router_has_no_silent_broad_exception_cleanup(self):
        source_path = Path(__file__).resolve().parents[1] / "app" / "routers" / "bulk.py"
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

        self.assertEqual(offenders, [], f"silent broad cleanup handlers at lines {offenders}")


if __name__ == "__main__":
    unittest.main()
