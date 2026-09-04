"""Best-effort Telethon lifecycle cleanup with explicit diagnostics."""
from __future__ import annotations

import asyncio
import logging
import sqlite3

from telethon.errors import RPCError


class TelegramClientCleanup:
    def __init__(self, logger: logging.Logger | None = None):
        self._log = logger or logging.getLogger("client_cleanup")

    async def disconnect(
        self,
        cli,
        *,
        context: str,
        suppress_cancelled: bool = False,
    ) -> bool:
        """Disconnect a client without allowing cleanup failures to hide.

        Expected transport/runtime failures are logged at debug level. Unexpected
        exceptions are still contained at this lifecycle boundary, but are logged
        as warnings. Cancellation propagates unless the caller explicitly asks
        for shutdown-style suppression.
        """
        if cli is None:
            return True
        try:
            await cli.disconnect()
            return True
        except asyncio.CancelledError:
            if suppress_cancelled:
                self._log.debug("Telegram disconnect cancelled context=%s", context)
                return False
            raise
        except (RPCError, ConnectionError, OSError, RuntimeError) as exc:
            self._log.debug(
                "Telegram disconnect cleanup failed context=%s error=%s",
                context,
                type(exc).__name__,
            )
            return False
        except Exception as exc:
            self._log.warning(
                "unexpected Telegram disconnect cleanup failure context=%s error=%s",
                context,
                type(exc).__name__,
            )
            return False

    def close_session(self, cli, *, context: str) -> bool:
        """Close Telethon's local session handle without silent swallowing."""
        session = getattr(cli, "session", None)
        if session is None:
            return True
        try:
            session.close()
            return True
        except (sqlite3.Error, OSError, RuntimeError) as exc:
            self._log.debug(
                "Telegram session close failed context=%s error=%s",
                context,
                type(exc).__name__,
            )
            return False
        except Exception as exc:
            self._log.warning(
                "unexpected Telegram session close failure context=%s error=%s",
                context,
                type(exc).__name__,
            )
            return False

    def remove_event_handler(self, cli, handler, *, context: str) -> bool:
        """Detach a callback without allowing cleanup to abort lifecycle."""
        try:
            cli.remove_event_handler(handler)
            return True
        except (OSError, RuntimeError) as exc:
            self._log.debug(
                "Telegram handler detach failed context=%s error=%s",
                context,
                type(exc).__name__,
            )
            return False
        except Exception as exc:
            self._log.warning(
                "unexpected Telegram handler detach failure context=%s error=%s",
                context,
                type(exc).__name__,
            )
            return False
