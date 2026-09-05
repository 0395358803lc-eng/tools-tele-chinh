import ast
import io
import unittest
from pathlib import Path
from unittest.mock import patch

from app import logging_config


class _CloseHandler:
    def __init__(self, error=None):
        self.error = error
        self.closed = 0

    def close(self):
        self.closed += 1
        if self.error is not None:
            raise self.error


class LoggingCleanupBoundaryTests(unittest.TestCase):
    def test_expected_close_io_failure_is_reported_without_raw_message(self):
        handler = _CloseHandler(OSError("secret-log-path"))
        stderr = io.StringIO()
        with patch.object(logging_config.sys, "stderr", stderr):
            self.assertFalse(
                logging_config._safe_close_rotating_handler(
                    handler,
                    owner="uvicorn.error",
                )
            )
        self.assertEqual(handler.closed, 1)
        diagnostic = stderr.getvalue()
        self.assertIn("owner=uvicorn.error", diagnostic)
        self.assertIn("error_type=OSError", diagnostic)
        self.assertNotIn("secret-log-path", diagnostic)

    def test_successful_close_returns_true(self):
        handler = _CloseHandler()
        self.assertTrue(
            logging_config._safe_close_rotating_handler(handler, owner="root")
        )
        self.assertEqual(handler.closed, 1)

    def test_unexpected_close_programming_error_propagates(self):
        handler = _CloseHandler(RuntimeError("programming bug"))
        with self.assertRaisesRegex(RuntimeError, "programming bug"):
            logging_config._safe_close_rotating_handler(handler, owner="root")


class LoggingCleanupSourceGuardTests(unittest.TestCase):
    def test_logging_config_has_no_silent_broad_exception_handler(self):
        source_path = Path(__file__).resolve().parents[1] / "app" / "logging_config.py"
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
        self.assertEqual(offenders, [], f"silent broad logging handlers: {offenders}")

    def test_log_directory_creation_is_not_silently_suppressed(self):
        source = (
            Path(__file__).resolve().parents[1] / "app" / "logging_config.py"
        ).read_text(encoding="utf-8")
        self.assertIn("settings.logs_path.mkdir(parents=True, exist_ok=True)", source)
        self.assertNotIn("except Exception:\n        pass", source)


if __name__ == "__main__":
    unittest.main()
