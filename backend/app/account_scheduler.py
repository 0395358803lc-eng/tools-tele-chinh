"""Per-account Telegram mutation scheduler.

Serializes mutations for the same account, keeps different accounts parallel,
applies Telegram FloodWait cooldowns, normal pacing, and graceful shutdown
coordination independently from Telegram client lifecycle management.
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Awaitable, Callable, TypeVar

from telethon.errors import FloodWaitError

from .config import settings

T = TypeVar("T")


class AccountActionScheduler:
    def __init__(self, logger: logging.Logger | None = None):
        self._log = logger or logging.getLogger("account_scheduler")
        self._locks: dict[int, asyncio.Lock] = {}
        self._cooldown_until: dict[int, float] = {}
        self._next_allowed_at: dict[int, float] = {}
        self._accepting = True
        self._active = 0
        self._idle = asyncio.Event()
        self._idle.set()

    @property
    def active_actions(self) -> int:
        return self._active

    def action_lock(self, account_id: int) -> asyncio.Lock:
        """Return the mutation lock for one account.

        All accesses happen on the application's single asyncio event loop, so
        creating the lock here does not need an additional async guard.
        """
        lock = self._locks.get(account_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[account_id] = lock
        return lock

    async def wait_for_cooldown(self, account_id: int):
        until = max(
            self._cooldown_until.get(account_id, 0.0),
            self._next_allowed_at.get(account_id, 0.0),
        )
        delay = until - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)

    def note_flood_wait(self, account_id: int, seconds: int | float):
        seconds = max(0.0, float(seconds))
        until = time.monotonic() + seconds
        self._cooldown_until[account_id] = max(
            self._cooldown_until.get(account_id, 0.0), until
        )
        self._log.warning(
            "account=%s operation=telegram_mutation error_code=FLOOD_WAIT seconds=%s",
            account_id,
            int(seconds),
        )

    def cooldown_remaining(self, account_id: int) -> float:
        return max(
            0.0,
            self._cooldown_until.get(account_id, 0.0) - time.monotonic(),
        )

    def resume(self):
        self._accepting = True

    def stop_accepting(self):
        self._accepting = False

    async def wait_idle(self, timeout: float) -> bool:
        """Wait until in-flight actions complete. Return False on timeout."""
        try:
            await asyncio.wait_for(self._idle.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def run(
        self,
        account_id: int,
        action: Callable[[], Awaitable[T]],
        operation: str = "telegram_mutation",
    ) -> T:
        if not self._accepting:
            raise RuntimeError("Application is shutting down")

        self._active += 1
        self._idle.clear()
        started = time.monotonic()
        try:
            async with self.action_lock(account_id):
                await self.wait_for_cooldown(account_id)
                try:
                    result = await action()
                except FloodWaitError as exc:
                    self.note_flood_wait(account_id, exc.seconds)
                    raise
                finally:
                    lo = max(0.0, float(getattr(settings, "RATE_MIN", 0.0)))
                    hi = max(lo, float(getattr(settings, "RATE_MAX", lo)))
                    self._next_allowed_at[account_id] = (
                        time.monotonic() + random.uniform(lo, hi)
                    )

                duration_ms = int((time.monotonic() - started) * 1000)
                self._log.info(
                    "account=%s operation=%s status=success duration_ms=%s",
                    account_id,
                    operation,
                    duration_ms,
                )
                return result
        except FloodWaitError:
            duration_ms = int((time.monotonic() - started) * 1000)
            self._log.warning(
                "account=%s operation=%s status=rate_limited duration_ms=%s",
                account_id,
                operation,
                duration_ms,
            )
            raise
        except Exception:
            duration_ms = int((time.monotonic() - started) * 1000)
            self._log.exception(
                "account=%s operation=%s status=failed duration_ms=%s",
                account_id,
                operation,
                duration_ms,
            )
            raise
        finally:
            self._active -= 1
            if self._active == 0:
                self._idle.set()
