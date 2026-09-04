"""Standalone scheduler endurance test used by SOAK_TEST.bat and CI."""
from __future__ import annotations

import argparse
import asyncio
import json
import time

from app.account_scheduler import AccountActionScheduler
from app.config import settings


async def run_soak(seconds: float, accounts: int, actions_per_account: int) -> dict:
    old_rate = (settings.RATE_MIN, settings.RATE_MAX)
    settings.RATE_MIN = 0
    settings.RATE_MAX = 0
    scheduler = AccountActionScheduler()

    active_by_account: dict[int, int] = {}
    max_global = 0
    global_active = 0
    completed = 0
    rounds = 0
    started = time.monotonic()
    deadline = started + max(0.1, seconds)

    async def action(account_id: int):
        nonlocal global_active, max_global, completed
        active_by_account[account_id] = active_by_account.get(account_id, 0) + 1
        if active_by_account[account_id] != 1:
            raise AssertionError(f"same-account overlap detected for account {account_id}")
        global_active += 1
        max_global = max(max_global, global_active)
        try:
            # Keep each action alive briefly so cross-account parallelism and
            # same-account serialization are both exercised under task churn.
            await asyncio.sleep(0.001)
            completed += 1
        finally:
            global_active -= 1
            active_by_account[account_id] -= 1

    try:
        while time.monotonic() < deadline:
            tasks = [
                asyncio.create_task(
                    scheduler.run(aid, lambda aid=aid: action(aid), operation="soak")
                )
                for aid in range(1, accounts + 1)
                for _ in range(actions_per_account)
            ]
            await asyncio.gather(*tasks)
            rounds += 1
    finally:
        settings.RATE_MIN, settings.RATE_MAX = old_rate

    elapsed = time.monotonic() - started
    if scheduler.active_actions != 0:
        raise AssertionError(f"scheduler leaked {scheduler.active_actions} active action(s)")
    if accounts > 1 and max_global < 2:
        raise AssertionError("different accounts never executed concurrently")

    return {
        "ok": True,
        "seconds": round(elapsed, 3),
        "accounts": accounts,
        "actions_per_account": actions_per_account,
        "rounds": rounds,
        "completed": completed,
        "max_parallel_accounts": max_global,
        "active_actions_after": scheduler.active_actions,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=300.0)
    parser.add_argument("--accounts", type=int, default=100)
    parser.add_argument("--actions-per-account", type=int, default=2)
    args = parser.parse_args()
    if args.accounts < 1 or args.accounts > 1000:
        parser.error("--accounts must be between 1 and 1000")
    if args.actions_per_account < 1 or args.actions_per_account > 20:
        parser.error("--actions-per-account must be between 1 and 20")
    result = asyncio.run(
        run_soak(
            max(0.1, args.seconds),
            args.accounts,
            args.actions_per_account,
        )
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
