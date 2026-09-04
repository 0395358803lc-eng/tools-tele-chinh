import logging
import re
from logging.handlers import RotatingFileHandler

from .config import settings


class SecretRedactionFilter(logging.Filter):
    _patterns = (
        (re.compile(r"(?i)(api_hash|access_hash|password|phone_code|session_secret)\s*[=:]\s*[^\s,;]+"), r"\1=[REDACTED]"),
        (re.compile(r"\b[0-9a-fA-F]{32,}\b"), "[REDACTED]"),
        (re.compile(r"(?<!\d)\d{4,8}(?!\d)"), "[REDACTED]"),
        (re.compile(r"(?<!\d)\+?\d{9,15}(?!\d)"), lambda m: "***" + m.group(0)[-4:]),
    )

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        for pattern, replacement in self._patterns:
            message = pattern.sub(replacement, message)
        record.msg = message
        record.args = ()
        return True


class RedactingFormatter(logging.Formatter):
    def formatException(self, exc_info):
        text = super().formatException(exc_info)
        for pattern, replacement in SecretRedactionFilter._patterns:
            text = pattern.sub(replacement, text)
        return text


def configure_logging(force: bool = False):
    root = logging.getLogger()
    if getattr(root, "_mtm_configured", False) and not force:
        return
    # force=True means uvicorn's dictConfig has already run and we must
    # re-attach our handlers (and also bridge uvicorn's loggers to the file).
    if force:
        # Close and remove old file handlers to avoid duplicate writes / leaks
        for h in list(root.handlers):
            if isinstance(h, RotatingFileHandler):
                try:
                    h.close()
                except Exception:
                    pass
        # We will rebuild below; keep the flag to allow rebuild
        root.handlers.clear()
        # also clear uvicorn logger handlers that dictConfig created
        for _name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
            lg = logging.getLogger(_name)
            for h in list(lg.handlers):
                if isinstance(h, RotatingFileHandler):
                    try:
                        h.close()
                    except Exception:
                        pass
            lg.handlers.clear()
    else:
        if getattr(root, "_mtm_configured", False):
            return

    root.setLevel(logging.INFO)
    formatter = RedactingFormatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    redaction = SecretRedactionFilter()
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    console.addFilter(redaction)
    # Ensure log directory exists before handler creation
    try:
        settings.logs_path.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    file_handler = RotatingFileHandler(
        settings.logs_path / "app.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(redaction)
    if not force:
        root.handlers.clear()
        root.addHandler(console)
        root.addHandler(file_handler)
    else:
        # force path: root was cleared above, re-add
        root.addHandler(console)
        root.addHandler(file_handler)
    # Bridge uvicorn's isolated loggers (propagate=False) to the same file
    # so "Application startup complete" / "Uvicorn running on ..." also
    # persist to data/logs/app.log and keep growing after startup.
    for _name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(_name)
        lg.setLevel(logging.INFO)
        # avoid duplicating the same handler object
        if file_handler not in lg.handlers:
            lg.addHandler(file_handler)
        # keep uvicorn's console output via root's console handler through
        # propagation, but also ensure redaction on uvicorn loggers
        already = any(isinstance(f, SecretRedactionFilter) for f in lg.filters)
        if not already:
            lg.addFilter(redaction)
        # Do NOT set propagate=True blindly - uvicorn sets it to False by
        # design. We keep False to avoid double console lines via root,
        # file is now attached directly so persistence is guaranteed.
        # For our own loggers, propagate remains True (default).
        lg.propagate = False
    # Ensure our app loggers propagate to root (file+console)
    # sqlalchemy is intentionally WARN to suppress verbose ORM mapper logs;
    # alembic stays INFO to keep migration logs.
    for _name, level in (
        ("main", logging.INFO),
        ("tg_manager", logging.INFO),
        ("config", logging.INFO),
        ("alembic", logging.INFO),
        ("sqlalchemy", logging.WARNING),
        ("sqlalchemy.engine", logging.WARNING),
        ("sqlalchemy.pool", logging.WARNING),
        ("sqlalchemy.orm", logging.WARNING),
    ):
        lg = logging.getLogger(_name)
        lg.setLevel(level)
        if not any(isinstance(f, SecretRedactionFilter) for f in lg.filters):
            lg.addFilter(redaction)

    root._mtm_configured = True
    # For force-reconfig, emit a marker so operator can verify the fix landed
    if force:
        logging.getLogger("main").info("logging reconfigured after uvicorn (file+console bridged)")
