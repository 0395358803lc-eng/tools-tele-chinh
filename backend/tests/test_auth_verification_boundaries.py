import ast
import unittest
from pathlib import Path
from unittest.mock import patch

from itsdangerous import BadSignature, SignatureExpired

from app import auth
from app.config import settings


class AuthVerificationBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.old = (
            settings.APP_PASSWORD,
            settings.SESSION_SECRET,
            settings.SESSION_DAYS,
        )
        settings.APP_PASSWORD = "correct horse battery staple"
        settings.SESSION_SECRET = "s" * 64
        settings.SESSION_DAYS = 14
        auth._pw_hash = None
        auth._signer = None

    def tearDown(self):
        settings.APP_PASSWORD, settings.SESSION_SECRET, settings.SESSION_DAYS = self.old
        auth._pw_hash = None
        auth._signer = None

    def test_wrong_password_remains_false(self):
        self.assertFalse(auth._verify_password("wrong"))

    def test_invalid_unicode_password_is_treated_as_invalid_input(self):
        self.assertFalse(auth._verify_password("\ud800"))

    def test_missing_app_password_configuration_propagates(self):
        settings.APP_PASSWORD = ""
        auth._pw_hash = None
        with self.assertRaisesRegex(RuntimeError, "APP_PASSWORD"):
            auth._verify_password("candidate")

    def test_unexpected_bcrypt_failure_propagates(self):
        with patch("app.auth.bcrypt.checkpw", side_effect=RuntimeError("bcrypt bug")):
            with self.assertRaisesRegex(RuntimeError, "bcrypt bug"):
                auth._verify_password("candidate")

    def test_bad_and_expired_tokens_remain_false(self):
        class BadSigner:
            def unsign(self, *_args, **_kwargs):
                raise BadSignature("bad")

        class ExpiredSigner:
            def unsign(self, *_args, **_kwargs):
                raise SignatureExpired("expired")

        with patch("app.auth._get_signer", return_value=BadSigner()):
            self.assertFalse(auth._verify_token("bad-token"))
        with patch("app.auth._get_signer", return_value=ExpiredSigner()):
            self.assertFalse(auth._verify_token("expired-token"))

    def test_invalid_unicode_token_is_treated_as_invalid_input(self):
        self.assertFalse(auth._verify_token("\ud800"))

    def test_missing_session_secret_configuration_propagates(self):
        settings.SESSION_SECRET = ""
        auth._signer = None
        with self.assertRaisesRegex(RuntimeError, "SESSION_SECRET"):
            auth._verify_token("candidate-token")

    def test_unexpected_signer_failure_propagates(self):
        class BrokenSigner:
            def unsign(self, *_args, **_kwargs):
                raise RuntimeError("signer bug")

        with patch("app.auth._get_signer", return_value=BrokenSigner()):
            with self.assertRaisesRegex(RuntimeError, "signer bug"):
                auth._verify_token("candidate-token")


class AuthVerificationSourceGuardTests(unittest.TestCase):
    def test_verification_helpers_do_not_catch_broad_exception(self):
        source_path = Path(__file__).resolve().parents[1] / "app" / "auth.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        guarded = {"_verify_password", "_verify_token"}
        offenders = []
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef) or node.name not in guarded:
                continue
            for child in ast.walk(node):
                if not isinstance(child, ast.ExceptHandler) or child.type is None:
                    continue
                if isinstance(child.type, ast.Name) and child.type.id == "Exception":
                    offenders.append((node.name, getattr(child, "lineno", -1)))
        self.assertEqual(offenders, [], f"broad auth verification handlers: {offenders}")


if __name__ == "__main__":
    unittest.main()
