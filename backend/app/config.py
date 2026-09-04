from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path
import logging
import shutil
import sqlite3

log = logging.getLogger("config")
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = PROJECT_ROOT / "data"
DATABASE_DIR = DATA_ROOT / "database"
SESSIONS_DIR = DATA_ROOT / "sessions"
SECRETS_DIR = DATA_ROOT / "secrets"
BACKUPS_DIR = DATA_ROOT / "backups"
LOGS_DIR = DATA_ROOT / "logs"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Held as a plain string so an empty/malformed value in .env doesn't crash
    # the app during Settings() construction with a raw pydantic traceback.
    # Use the `tg_api_id` property to get a validated int (0 when unset/invalid).
    TG_API_ID: str = "0"
    TG_API_HASH: str = ""

    @field_validator("TG_API_ID", mode="before")
    @classmethod
    def _strip_api_id(cls, v):
        if v is None:
            return "0"
        if isinstance(v, str):
            v = v.strip()
        return v or "0"

    @field_validator("DB_URL", mode="before")
    @classmethod
    def _fixed_database_path(cls, value):
        return f"sqlite+aiosqlite:///{(DATABASE_DIR / 'app.db').as_posix()}"

    @property
    def tg_api_id(self) -> int:
        try:
            return int(float(self.TG_API_ID))
        except (TypeError, ValueError):
            log.warning("TG_API_ID is not a valid integer (%r) â€” treating as 0", self.TG_API_ID)
            return 0

    @property
    def api_configured(self) -> bool:
        """True only when both TG_API_ID (>0) and TG_API_HASH are set."""
        return self.tg_api_id > 0 and bool(self.TG_API_HASH.strip())

    SESSIONS_DIR: str = str(SESSIONS_DIR)
    DB_URL: str = f"sqlite+aiosqlite:///{(DATABASE_DIR / 'app.db').as_posix()}"
    RATE_MIN: float = 0.7
    RATE_MAX: float = 1.5
    CONCURRENCY: int = 8  # how many accounts a bulk task processes in parallel
    STARTUP_CONCURRENCY: int = 10  # how many accounts connect in parallel on boot
    # How often the background loop re-verifies each account's Telegram auth
    # (is_user_authorized). Connections themselves are checked every STATUS_POLL_SECS.
    # Verified-checks are spread/staggered so a large account fleet doesn't burst
    # auth RPCs all at once; this knob bounds how stale an auth check can get.
    STATUS_POLL_SECS: float = 5.0
    STATUS_AUTH_INTERVAL: float = 60.0
    ALLOWED_ORIGIN: str = "http://localhost:5173"

    APP_PASSWORD: str = ""
    SESSION_SECRET: str = ""
    SESSION_DAYS: int = 14
    LOGIN_MAX_ATTEMPTS: int = 5
    LOGIN_WINDOW_MIN: int = 15
    TRUSTED_PROXY_IPS: str = ""  # comma-separated direct peers allowed to supply X-Forwarded-For

    MAX_SESSION_UPLOAD_MB: int = 10   # .session import file cap
    MAX_IMAGE_UPLOAD_MB: int = 10     # profile photo cap
    MAX_SESSION_UPLOAD_FILES: int = 100
    MAX_SESSION_UPLOAD_TOTAL_MB: int = 100
    LOGIN_PENDING_TTL_SECONDS: int = 600
    QR_PENDING_TTL_SECONDS: int = 300
    TELEGRAM_CONNECT_TIMEOUT: float = 20.0
    TELEGRAM_AUTH_TIMEOUT: float = 20.0
    BACKUP_RETENTION_COUNT: int = 20

    def validate_security_config(self):
        """Fail before serving requests when local authentication is unusable."""
        password = self.APP_PASSWORD or ""
        if password == "change-me-to-a-long-strong-password":
            raise RuntimeError("APP_PASSWORD is still the example placeholder")
        if not 12 <= len(password) <= 256:
            raise RuntimeError("APP_PASSWORD must contain 12-256 characters")
        if not 48 <= len(self.SESSION_SECRET) <= 1024:
            raise RuntimeError("SESSION_SECRET must contain 48-1024 characters")

    @property
    def sessions_path(self) -> Path:
        p = SESSIONS_DIR
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def database_path(self) -> Path:
        return DATABASE_DIR / "app.db"

    @property
    def secrets_path(self) -> Path:
        SECRETS_DIR.mkdir(parents=True, exist_ok=True)
        return SECRETS_DIR

    @property
    def backups_path(self) -> Path:
        BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
        return BACKUPS_DIR

    @property
    def logs_path(self) -> Path:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        return LOGS_DIR

    def ensure_data_directories(self):
        for path in (DATABASE_DIR, SESSIONS_DIR, SECRETS_DIR, BACKUPS_DIR, LOGS_DIR):
            path.mkdir(parents=True, exist_ok=True)


settings = Settings()


def migrate_legacy_runtime_data():
    """Copy legacy backend runtime files into data/ without deleting originals."""
    settings.ensure_data_directories()
    legacy_backend = PROJECT_ROOT / "backend"
    old_db = legacy_backend / "app.db"
    new_db = settings.database_path
    if old_db.exists() and not new_db.exists():
        source = sqlite3.connect(str(old_db))
        destination = sqlite3.connect(str(new_db))
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()
        log.info("migrated legacy database to %s", new_db)

    legacy_sessions = legacy_backend / "sessions"
    if legacy_sessions.is_dir():
        for source in legacy_sessions.glob("*.session"):
            destination = settings.sessions_path / source.name
            if not destination.exists():
                source_db = sqlite3.connect(str(source))
                destination_db = sqlite3.connect(str(destination))
                try:
                    source_db.backup(destination_db)
                finally:
                    destination_db.close()
                    source_db.close()
        for name in ("twofa.bin", "twofa.json"):
            source = legacy_sessions / name
            destination = settings.secrets_path / name
            if source.exists() and not destination.exists():
                shutil.copy2(source, destination)
