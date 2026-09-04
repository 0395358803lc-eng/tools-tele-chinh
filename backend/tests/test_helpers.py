import io
import logging
import unittest

from fastapi import HTTPException, UploadFile
from PIL import Image

from app.config import DATA_ROOT, settings
from app.errors import error_code_of, resolve_error
from app.logging_config import SecretRedactionFilter
from app.schemas import SettingsIn
from app.tg_manager import TgClientManager, classify_777000, redact_login_code
from app.uploads import read_limited, sanitize_filename, validate_image_bytes
from app.utils import _redact, friendly_error


class ErrorAndLoggingTests(unittest.TestCase):
    def test_known_error_has_stable_code(self):
        self.assertEqual(resolve_error("Account not found")[0], "ACCOUNT_NOT_FOUND")

    def test_flood_detail_has_seconds_parameter(self):
        code, params = resolve_error("Rate limited â€” wait 12s before trying again.")
        self.assertEqual(code, "FLOOD_WAIT")
        self.assertEqual(params, {"seconds": 12})

    def test_unknown_exception_does_not_expose_message(self):
        result = friendly_error(RuntimeError("password=do-not-leak"))
        self.assertNotIn("do-not-leak", result)

    def test_generic_redactor_removes_hash_and_long_digits(self):
        raw = "api_hash=abcdef0123456789abcdef0123456789 otp 123456"
        cleaned = _redact(raw)
        self.assertNotIn("abcdef0123456789abcdef0123456789", cleaned)
        self.assertNotIn("123456", cleaned)

    def test_log_filter_removes_credentials(self):
        record = logging.LogRecord("test", logging.INFO, __file__, 1, "password=secret 12345", (), None)
        SecretRedactionFilter().filter(record)
        self.assertNotIn("secret", record.getMessage())
        self.assertNotIn("12345", record.getMessage())

    def test_exception_class_maps_without_raw_message(self):
        UsernameOccupiedError = type("UsernameOccupiedError", (Exception,), {})
        self.assertEqual(error_code_of(UsernameOccupiedError("private")), "USERNAME_OCCUPIED")


class ClassificationAndConfigTests(unittest.TestCase):
    def test_login_code_classification(self):
        self.assertEqual(classify_777000("Your login code is 12345"), "login_code")

    def test_new_login_classification(self):
        self.assertEqual(classify_777000("New login from a new device"), "new_login")

    def test_2fa_classification(self):
        self.assertEqual(classify_777000("Two-step password changed"), "2fa_change")

    def test_multiple_codes_are_redacted(self):
        cleaned = redact_login_code("codes 12345 and 987654")
        self.assertNotIn("12345", cleaned)
        self.assertNotIn("987654", cleaned)

    def test_runtime_paths_are_under_data_root(self):
        self.assertTrue(settings.database_path.is_relative_to(DATA_ROOT))
        self.assertTrue(settings.sessions_path.is_relative_to(DATA_ROOT))
        self.assertTrue(settings.logs_path.is_relative_to(DATA_ROOT))

    def test_valid_settings_are_accepted(self):
        value = SettingsIn(rate_min=1, rate_max=2, concurrency=8, auto_reconnect=False)
        self.assertEqual(value.concurrency, 8)

    def test_permanent_account_status_classification(self):
        manager = TgClientManager()
        SessionRevokedError = type("SessionRevokedError", (Exception,), {})
        PhoneNumberBannedError = type("PhoneNumberBannedError", (Exception,), {})
        self.assertEqual(manager._permanent_status(SessionRevokedError()), "session_revoked")
        self.assertEqual(manager._permanent_status(PhoneNumberBannedError()), "banned")


class AdditionalUploadTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_upload(self):
        upload = UploadFile(filename="empty.session", file=io.BytesIO(b""))
        self.assertEqual(await read_limited(upload, 10), b"")

    async def test_long_filename_is_bounded(self):
        self.assertLessEqual(len(sanitize_filename("a" * 300 + ".session")), 200)

    async def test_gif_is_rejected(self):
        buffer = io.BytesIO()
        Image.new("RGB", (1, 1)).save(buffer, format="GIF")
        with self.assertRaises(HTTPException):
            validate_image_bytes(buffer.getvalue())
