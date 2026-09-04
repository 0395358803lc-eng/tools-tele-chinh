import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.session_files import SessionFileStore


class SessionFileStoreTests(unittest.TestCase):
    def test_naming_and_temp_paths_are_normalized_and_unique(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            store = SessionFileStore()
            fake_settings = SimpleNamespace(sessions_path=root)
            with patch("app.session_files.settings", fake_settings):
                self.assertEqual(store.session_file_name("+84 123 456 789", "@demo-user", 7), "demouser_84123456789")
                first = store.phone_temp_session_path("+84 123 456 789")
                second = store.phone_temp_session_path("+84 123 456 789")
                self.assertNotEqual(first, second)
                self.assertEqual(Path(first).parent, root)
                self.assertTrue(Path(first).name.startswith("login_84123456789_"))

    def test_existing_candidate_is_selected_without_duplicates(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            store = SessionFileStore()
            fake_settings = SimpleNamespace(sessions_path=root)
            acc = SimpleNamespace(
                session_file="preferred.session",
                phone="+84123456789",
                username="demo",
                tg_user_id=123,
            )
            preferred = root / "preferred.session"
            preferred.write_bytes(b"session")

            with patch("app.session_files.settings", fake_settings):
                candidates = store.session_path_candidates(acc)
                self.assertEqual(len(candidates), len(set(candidates)))
                self.assertEqual(store.session_path_for_account(acc), str(preferred.with_suffix("")))

    def test_atomic_swap_rolls_back_session_and_sidecar(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "incoming"
            target = root / "account"
            Path(str(source) + ".session").write_bytes(b"new")
            Path(str(source) + ".session-wal").write_bytes(b"new-wal")
            Path(str(target) + ".session").write_bytes(b"old")
            Path(str(target) + ".session-wal").write_bytes(b"old-wal")

            store = SessionFileStore()
            state = store.begin_session_swap(str(source), str(target))
            self.assertEqual(Path(str(target) + ".session").read_bytes(), b"new")
            self.assertEqual(Path(str(target) + ".session-wal").read_bytes(), b"new-wal")

            store.rollback_session_swap(state)
            self.assertEqual(Path(str(target) + ".session").read_bytes(), b"old")
            self.assertEqual(Path(str(target) + ".session-wal").read_bytes(), b"old-wal")
            self.assertEqual(Path(str(source) + ".session").read_bytes(), b"new")
            self.assertEqual(Path(str(source) + ".session-wal").read_bytes(), b"new-wal")

    def test_commit_removes_rollback_and_old_session_tree(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "incoming"
            target = root / "account"
            legacy = root / "legacy"
            Path(str(source) + ".session").write_bytes(b"new")
            Path(str(target) + ".session").write_bytes(b"old")
            Path(str(legacy) + ".session").write_bytes(b"legacy")
            Path(str(legacy) + ".session-shm").write_bytes(b"legacy-shm")

            store = SessionFileStore()
            state = store.begin_session_swap(str(source), str(target))
            store.commit_session_swap(state, [str(legacy)])

            self.assertEqual(Path(str(target) + ".session").read_bytes(), b"new")
            self.assertFalse(Path(str(legacy) + ".session").exists())
            self.assertFalse(Path(str(legacy) + ".session-shm").exists())
            self.assertFalse(any(root.glob("*.rollback-*")))

    def test_import_filter_rejects_temporary_session_names(self):
        store = SessionFileStore()
        self.assertTrue(store.is_importable_session_file(Path("normal.session")))
        self.assertFalse(store.is_importable_session_file(Path("qr_abc.session")))
        self.assertFalse(store.is_importable_session_file(Path("mtm_import_abc.session")))
        self.assertFalse(store.is_importable_session_file(Path("normal.txt")))


if __name__ == "__main__":
    unittest.main()
