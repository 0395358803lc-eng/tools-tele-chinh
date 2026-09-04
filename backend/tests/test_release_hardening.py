import asyncio
import logging
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from alembic import command
from alembic.config import Config
from fastapi import HTTPException, Request, Response
from itsdangerous import SignatureExpired
from sqlalchemy import inspect, create_engine

from app.config import PROJECT_ROOT, Settings, settings
from app.logging_config import SecretRedactionFilter
from app.routers.messaging import _parse_post_link
from app.schemas import BulkJoinIn, SendCodeIn
from app import secrets_store
from app.backup_service import _create_backup_sync, _sqlite_backup as real_sqlite_backup
from restore_backup import restore
from app import auth as app_auth
from app.tg_manager import TgClientManager, manager as global_manager
from app.routers.accounts import _finalize_qr


class SessionSwapTests(unittest.TestCase):
    def test_session_swap_rolls_back_old_file(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "incoming"
            target = root / "account"
            Path(str(source) + ".session").write_bytes(b"new")
            Path(str(target) + ".session").write_bytes(b"old")
            manager = TgClientManager()
            state = manager.begin_session_swap(str(source), str(target))
            self.assertEqual(Path(str(target) + ".session").read_bytes(), b"new")
            manager.rollback_session_swap(state)
            self.assertEqual(Path(str(target) + ".session").read_bytes(), b"old")
            self.assertEqual(Path(str(source) + ".session").read_bytes(), b"new")

    def test_session_swap_commit_removes_rollback_copy(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "incoming"
            target = root / "account"
            old_other = root / "legacy"
            Path(str(source) + ".session").write_bytes(b"new")
            Path(str(target) + ".session").write_bytes(b"old")
            Path(str(old_other) + ".session").write_bytes(b"legacy")
            manager = TgClientManager()
            state = manager.begin_session_swap(str(source), str(target))
            manager.commit_session_swap(state, [str(old_other)])
            self.assertEqual(Path(str(target) + ".session").read_bytes(), b"new")
            self.assertFalse(Path(str(old_other) + ".session").exists())
            self.assertFalse(any(root.glob("*.rollback-*")))


class AuthApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.old = (
            settings.APP_PASSWORD, settings.SESSION_SECRET,
            settings.LOGIN_MAX_ATTEMPTS, settings.LOGIN_WINDOW_MIN,
            settings.TRUSTED_PROXY_IPS,
        )
        settings.APP_PASSWORD = "correct horse battery staple"
        settings.SESSION_SECRET = "s" * 64
        settings.LOGIN_MAX_ATTEMPTS = 2
        settings.LOGIN_WINDOW_MIN = 15
        settings.TRUSTED_PROXY_IPS = ""
        app_auth._pw_hash = None
        app_auth._signer = None
        app_auth._attempts.clear()
        self.request = Request({
            "type": "http", "headers": [], "method": "POST", "path": "/login",
            "client": ("127.0.0.1", 12345), "scheme": "http", "server": ("test", 80),
            "query_string": b"",
        })

    def tearDown(self):
        (
            settings.APP_PASSWORD, settings.SESSION_SECRET,
            settings.LOGIN_MAX_ATTEMPTS, settings.LOGIN_WINDOW_MIN,
            settings.TRUSTED_PROXY_IPS,
        ) = self.old
        app_auth._pw_hash = None
        app_auth._signer = None
        app_auth._attempts.clear()

    async def test_correct_and_wrong_password_and_cookie(self):
        with patch("app.auth.asyncio.sleep", new=AsyncMock()):
            with self.assertRaises(HTTPException) as raised:
                await app_auth.login(app_auth.LoginIn(password="wrong"), self.request, Response())
        self.assertEqual(raised.exception.status_code, 401)
        response = Response()
        result = await app_auth.login(
            app_auth.LoginIn(password="correct horse battery staple"), self.request, response
        )
        self.assertTrue(result["ok"])
        cookie = response.headers["set-cookie"].split("mtm_session=", 1)[1].split(";", 1)[0]
        self.assertTrue((await app_auth.me(cookie))["authed"])

    async def test_forwarded_for_is_ignored_unless_direct_peer_is_trusted(self):
        scope = {
            "type": "http", "headers": [(b"x-forwarded-for", b"203.0.113.9")],
            "method": "POST", "path": "/login",
            "client": ("127.0.0.1", 12345), "scheme": "http", "server": ("test", 80),
            "query_string": b"",
        }
        request = Request(scope)
        self.assertEqual(app_auth._client_ip(request), "127.0.0.1")
        settings.TRUSTED_PROXY_IPS = "127.0.0.1"
        self.assertEqual(app_auth._client_ip(request), "203.0.113.9")

    async def test_rate_limit_and_expired_cookie(self):
        with patch("app.auth.asyncio.sleep", new=AsyncMock()):
            for value in ("bad-1", "bad-2"):
                with self.assertRaises(HTTPException):
                    await app_auth.login(app_auth.LoginIn(password=value), self.request, Response())
            with self.assertRaises(HTTPException) as blocked:
                await app_auth.login(app_auth.LoginIn(password="bad-3"), self.request, Response())
        self.assertEqual(blocked.exception.status_code, 429)

        class ExpiredSigner:
            def unsign(self, *_args, **_kwargs):
                raise SignatureExpired("expired")

        with patch("app.auth._get_signer", return_value=ExpiredSigner()):
            self.assertFalse(app_auth._verify_token("old-cookie"))


class ShutdownHardeningTests(unittest.IsolatedAsyncioTestCase):
    async def test_shutdown_requires_pid_bound_hmac(self):
        from app import main as app_main
        import hashlib
        import hmac

        old_secret = settings.SESSION_SECRET
        settings.SESSION_SECRET = "z" * 64
        try:
            bad_request = Request({
                "type": "http", "headers": [], "method": "POST",
                "path": "/api/app/shutdown", "client": ("127.0.0.1", 12345),
                "scheme": "http", "server": ("test", 80), "query_string": b"",
            })
            with patch("app.main._trigger_graceful_shutdown", new=AsyncMock()) as trigger:
                denied = await app_main.request_shutdown(bad_request)
                self.assertEqual(denied.status_code, 403)
                trigger.assert_not_called()

            pid = 4242
            token = hmac.new(
                settings.SESSION_SECRET.encode("utf-8"), b"4242", hashlib.sha256
            ).hexdigest().encode("ascii")
            good_request = Request({
                "type": "http",
                "headers": [(b"x-mtm-shutdown-token", token)],
                "method": "POST", "path": "/api/app/shutdown",
                "client": ("127.0.0.1", 12345), "scheme": "http",
                "server": ("test", 80), "query_string": b"",
            })
            with patch("app.main.os.getpid", return_value=pid), patch(
                "app.main._trigger_graceful_shutdown", new=AsyncMock()
            ) as trigger:
                accepted = await app_main.request_shutdown(good_request)
                await asyncio.sleep(0)
                self.assertTrue(accepted["ok"])
                trigger.assert_called_once()
        finally:
            settings.SESSION_SECRET = old_secret


class PendingLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_expired_phone_login_disconnects_and_deletes_temp_session(self):
        with tempfile.TemporaryDirectory() as raw:
            base = str(Path(raw) / "login_temp")
            Path(base + ".session").write_bytes(b"temporary")
            manager = TgClientManager()
            client = SimpleNamespace(disconnect=AsyncMock())
            manager._pending["+84123456789"] = {
                "client": client,
                "session_path": base,
                "expires_at": time.monotonic() - 1,
            }
            await manager.cleanup_expired_pending()
            client.disconnect.assert_awaited_once()
            self.assertNotIn("+84123456789", manager._pending)
            self.assertFalse(Path(base + ".session").exists())

    async def test_qr_cancel_awaits_cancelled_task_and_cleans_file(self):
        with tempfile.TemporaryDirectory() as raw:
            base = str(Path(raw) / "qr_temp")
            Path(base + ".session").write_bytes(b"temporary")
            task = asyncio.create_task(asyncio.Event().wait())
            await asyncio.sleep(0)
            manager = TgClientManager()
            client = SimpleNamespace(disconnect=AsyncMock())
            manager._qr_pending["qr"] = {
                "client": client, "wait_task": task, "session_path": base,
            }
            await manager.qr_cancel("qr")
            self.assertTrue(task.cancelled())
            client.disconnect.assert_awaited_once()
            self.assertFalse(Path(base + ".session").exists())

    async def test_qr_expiry_closes_client_and_retains_terminal_state(self):
        with tempfile.TemporaryDirectory() as raw:
            base = str(Path(raw) / "qr_temp")
            Path(base + ".session").write_bytes(b"temporary")
            qr = SimpleNamespace(wait=AsyncMock(side_effect=asyncio.TimeoutError()))
            client = SimpleNamespace(disconnect=AsyncMock())
            manager = TgClientManager()
            manager._qr_pending["qr"] = {
                "client": client, "qr_login": qr, "wait_task": None,
                "session_path": base, "error": None, "authorized": False,
                "needs_2fa": False, "expires_at": time.monotonic() + 30,
                "closed_at": None,
            }
            await manager._qr_wait("qr")
            self.assertEqual((await manager.qr_status("qr"))["state"], "expired")
            client.disconnect.assert_awaited_once()
            self.assertFalse(Path(base + ".session").exists())

    async def test_qr_refresh_awaits_old_cancel_and_can_be_cancelled(self):
        manager = TgClientManager()
        old = asyncio.create_task(asyncio.Event().wait())
        await asyncio.sleep(0)
        new_wait = asyncio.Event()
        async def wait_for_scan():
            await new_wait.wait()
        qr = SimpleNamespace(
            url="tg://login?token=test", expires=None,
            wait=AsyncMock(side_effect=wait_for_scan),
        )
        client = SimpleNamespace(
            is_connected=lambda: True,
            qr_login=AsyncMock(return_value=qr),
            disconnect=AsyncMock(),
        )
        manager._qr_pending["qr"] = {
            "client": client, "wait_task": old, "session_path": "unused",
            "error": None, "authorized": False, "needs_2fa": False,
            "expires_at": time.monotonic() + 30, "closed_at": None,
        }
        result = await manager.qr_recreate("qr")
        self.assertTrue(old.cancelled())
        self.assertEqual(result["url"], qr.url)
        await manager.qr_cancel("qr")

    async def test_qr_2fa_password_is_not_stripped(self):
        manager = TgClientManager()
        me = SimpleNamespace(phone="84123456789")
        client = SimpleNamespace(
            sign_in=AsyncMock(), get_me=AsyncMock(return_value=me)
        )
        manager._qr_pending["qr"] = {
            "client": client, "needs_2fa": True, "authorized": False,
        }
        with patch("app.tg_manager.secrets_store.save_2fa", new=AsyncMock()) as save:
            await manager.qr_submit_2fa("qr", "  intentional spaces  ")
        client.sign_in.assert_awaited_once_with(password="  intentional spaces  ")
        save.assert_awaited_once_with("84123456789", "  intentional spaces  ")

    async def test_qr_finalize_is_idempotent_under_concurrent_polls(self):
        qr_id = "finalize-once"
        global_manager._qr_completed.pop(qr_id, None)
        global_manager._qr_locks.pop(qr_id, None)
        me = SimpleNamespace(phone="84123456789")
        account = SimpleNamespace()
        out = SimpleNamespace(model_dump=lambda mode: {"id": 1, "phone": "+84123456789"})
        with patch.object(
            global_manager, "qr_finalize", new=AsyncMock(return_value=(me, object(), "temp"))
        ) as finalize, patch.object(
            global_manager, "qr_promote_to_phone", new=AsyncMock(return_value="temp")
        ), patch.object(
            global_manager, "finish_qr", new=AsyncMock()
        ), patch(
            "app.routers.accounts._persist_account", new=AsyncMock(return_value=account)
        ) as persist, patch(
            "app.routers.accounts._account_to_out", new=AsyncMock(return_value=out)
        ):
            first, second = await asyncio.gather(
                _finalize_qr(qr_id, object()), _finalize_qr(qr_id, object())
            )
        self.assertEqual(first, second)
        self.assertEqual(finalize.await_count, 1)
        self.assertEqual(persist.await_count, 1)
        global_manager._qr_completed.pop(qr_id, None)
        global_manager._qr_locks.pop(qr_id, None)


class StartupAndListenerTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_hundred_fake_accounts_can_run_in_parallel(self):
        manager = TgClientManager()
        active = 0
        peak = 0

        async def action():
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1

        with patch.object(settings, "RATE_MIN", 0.0), patch.object(settings, "RATE_MAX", 0.0):
            await asyncio.gather(*(
                manager.run_account_action(account_id, action)
                for account_id in range(1, 101)
            ))
        self.assertGreaterEqual(peak, 50)

    async def test_startup_skips_permanent_statuses(self):
        accounts = [
            SimpleNamespace(id=1, status="disconnected"),
            SimpleNamespace(id=2, status="banned"),
            SimpleNamespace(id=3, status="session_revoked"),
            SimpleNamespace(id=4, status="auth_error"),
        ]

        class Result:
            def scalars(self): return self
            def all(self): return accounts

        class Session:
            async def __aenter__(self): return self
            async def __aexit__(self, *_args): return False
            async def execute(self, _query): return Result()

        manager = TgClientManager()
        manager.redact_stored_login_codes = AsyncMock()
        manager.load_runtime_settings = AsyncMock()
        manager.start_client = AsyncMock()
        manager.sync_session_folder = AsyncMock(return_value={})
        with patch("app.tg_manager.AsyncSessionLocal", return_value=Session()):
            await manager.startup_load_all()
            await asyncio.sleep(0)
            called_ids = [call.args[0].id for call in manager.start_client.await_args_list]
            self.assertEqual(called_ids, [1])
            await manager.shutdown()

    async def test_listener_is_replaced_not_duplicated(self):
        class Client:
            def __init__(self): self.added = []; self.removed = []
            def add_event_handler(self, handler, event): self.added.append((handler, event))
            def remove_event_handler(self, handler): self.removed.append(handler)

        manager = TgClientManager()
        first = Client(); second = Client()
        manager._attach_listener(1, first)
        manager._attach_listener(1, second)
        self.assertEqual(len(first.added), 1)
        self.assertEqual(first.removed, [first.added[0][0]])
        self.assertEqual(len(second.added), 1)


class ValidationAndMigrationTests(unittest.TestCase):
    def test_phone_and_bulk_ids_are_normalized(self):
        self.assertEqual(SendCodeIn(phone="(84) 123-456-789").phone, "+84123456789")
        body = BulkJoinIn(account_ids=[3, 3, 1], target=" @channel ")
        self.assertEqual(body.account_ids, [3, 1])
        self.assertEqual(body.target, "@channel")

    def test_security_config_rejects_short_and_placeholder_passwords(self):
        with self.assertRaises(RuntimeError):
            Settings(_env_file=None, APP_PASSWORD="short", SESSION_SECRET="x" * 48).validate_security_config()
        with self.assertRaises(RuntimeError):
            Settings(
                _env_file=None,
                APP_PASSWORD="change-me-to-a-long-strong-password",
                SESSION_SECRET="x" * 48,
            ).validate_security_config()

    def test_private_channel_link_uses_marked_telethon_id(self):
        self.assertEqual(
            _parse_post_link("https://t.me/c/123456/789?single"),
            (-100123456, 789),
        )

    def test_new_migration_enforces_security_message_uniqueness(self):
        with tempfile.TemporaryDirectory() as raw:
            db_path = Path(raw) / "migration.db"
            config = Config(str(PROJECT_ROOT / "backend" / "alembic.ini"))
            url = f"sqlite:///{db_path.as_posix()}"
            with patch.object(settings, "DB_URL", url):
                command.upgrade(config, "head")
            engine = create_engine(url)
            try:
                indexes = inspect(engine).get_indexes("security_messages")
                self.assertTrue(any(i["name"] == "uq_security_account_tg_msg" and i["unique"] for i in indexes))
            finally:
                engine.dispose()


class SecureStoreAndLoggingTests(unittest.IsolatedAsyncioTestCase):
    async def test_unreadable_existing_store_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "twofa.bin"
            path.write_bytes(b"broken")
            with patch("app.secrets_store._path", return_value=path), patch(
                "app.secrets_store._decrypt", side_effect=OSError("cannot decrypt")
            ):
                with self.assertRaises(OSError):
                    await secrets_store.save_2fa("+84123456789", "secret")
            self.assertEqual(path.read_bytes(), b"broken")

    async def test_log_filter_masks_phone_and_six_digit_otp(self):
        record = logging.LogRecord(
            "test", logging.INFO, __file__, 1,
            "phone=+84123456789 otp=654321", (), None,
        )
        SecretRedactionFilter().filter(record)
        message = record.getMessage()
        self.assertNotIn("+84123456789", message)
        self.assertIn("***6789", message)
        self.assertNotIn("654321", message)


class RestoreRollbackTests(unittest.TestCase):
    def test_post_swap_failure_restores_entire_running_tree(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            database = root / "database"; database.mkdir()
            sessions = root / "sessions"; sessions.mkdir()
            secret_dir = root / "secrets"; secret_dir.mkdir()
            backups = root / "backups"; backups.mkdir()
            with patch.multiple(
                "app.config", DATABASE_DIR=database, SESSIONS_DIR=sessions,
                SECRETS_DIR=secret_dir, BACKUPS_DIR=backups,
            ):
                with closing(sqlite3.connect(database / "app.db")) as db:
                    db.execute("CREATE TABLE marker(value TEXT)")
                    db.execute("INSERT INTO marker VALUES ('backup')")
                    db.commit()
                with closing(sqlite3.connect(sessions / "backup.session")) as session_db:
                    session_db.execute("CREATE TABLE session_marker(value TEXT)")
                    session_db.execute("INSERT INTO session_marker VALUES ('backup')")
                    session_db.commit()
                backup = _create_backup_sync()
                with closing(sqlite3.connect(database / "app.db")) as db:
                    db.execute("UPDATE marker SET value='current'")
                    db.commit()
                (sessions / "current.session").write_bytes(
                    (sessions / "backup.session").read_bytes()
                )

                def fail_post_check(source, destination):
                    if Path(destination).name == "post-restore-check.db":
                        raise RuntimeError("injected post-swap failure")
                    return real_sqlite_backup(Path(source), Path(destination))

                with patch("restore_backup._sqlite_backup", side_effect=fail_post_check):
                    with self.assertRaises(RuntimeError):
                        restore(backup.name)
                with closing(sqlite3.connect(database / "app.db")) as db:
                    self.assertEqual(db.execute("SELECT value FROM marker").fetchone()[0], "current")
                self.assertTrue((sessions / "current.session").exists())
