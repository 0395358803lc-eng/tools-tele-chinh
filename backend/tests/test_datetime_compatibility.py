import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.time_utils import utc_now_naive


class UtcCompatibilityTests(unittest.TestCase):
    def test_utc_now_naive_preserves_existing_naive_utc_representation(self):
        before = datetime.now(timezone.utc).replace(tzinfo=None)
        value = utc_now_naive()
        after = datetime.now(timezone.utc).replace(tzinfo=None)

        self.assertIsNone(value.tzinfo)
        self.assertLessEqual(before - timedelta(seconds=1), value)
        self.assertLessEqual(value, after + timedelta(seconds=1))

    def test_backend_app_does_not_use_deprecated_datetime_utcnow(self):
        app_root = Path(__file__).resolve().parents[1] / "app"
        offenders = []
        for path in app_root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if "datetime.utcnow" in text:
                offenders.append(str(path.relative_to(app_root)))
        self.assertEqual(
            offenders,
            [],
            f"deprecated datetime.utcnow usages remain: {offenders}",
        )


if __name__ == "__main__":
    unittest.main()
