from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
import json
from collections import deque
import os
import platform

from ..db import get_db
from ..models import AppSetting, Account
from ..schemas import SettingsIn, SettingsOut
from ..config import settings as env_settings
from ..tg_manager import manager
from ..backup_service import create_backup, list_backups
from ..db import check_database_integrity
from ..version import APP_VERSION
from ..time_utils import utc_now_naive
from .. import secrets_store

router = APIRouter(prefix="/api/settings", tags=["settings"])

DEFAULTS = {
    "rate_min": "0.7",
    "rate_max": "1.5",
    "concurrency": "8",
    "auto_reconnect": "true",
}


async def _read_all(db: AsyncSession) -> dict[str, str]:
    res = await db.execute(select(AppSetting))
    rows = res.scalars().all()
    cur = {r.key: r.value for r in rows}
    for k, v in DEFAULTS.items():
        cur.setdefault(k, v)
    return cur


@router.get("", response_model=SettingsOut)
async def get_settings(db: AsyncSession = Depends(get_db)):
    cur = await _read_all(db)
    try:
        conc = int(float(cur.get("concurrency", "5")))
    except (TypeError, ValueError):
        conc = 5
    return SettingsOut(
        rate_min=float(cur["rate_min"]),
        rate_max=float(cur["rate_max"]),
        concurrency=max(1, conc),
        auto_reconnect=cur["auto_reconnect"] == "true",
    )


@router.put("", response_model=SettingsOut)
async def update_settings(body: SettingsIn, db: AsyncSession = Depends(get_db)):
    conc = max(1, int(body.concurrency or 5))
    rate_min = max(0.0, body.rate_min)
    rate_max = max(rate_min, body.rate_max)
    payload = {
        "rate_min": str(rate_min),
        "rate_max": str(rate_max),
        "concurrency": str(conc),
        "auto_reconnect": "true" if body.auto_reconnect else "false",
    }
    res = await db.execute(select(AppSetting))
    existing = {r.key: r for r in res.scalars().all()}
    for k, v in payload.items():
        if k in existing:
            existing[k].value = v
        else:
            db.add(AppSetting(key=k, value=v))
    await db.commit()
    # apply rate + concurrency to env_settings live (no restart needed)
    env_settings.RATE_MIN = rate_min
    env_settings.RATE_MAX = rate_max
    env_settings.CONCURRENCY = conc
    manager.auto_reconnect = body.auto_reconnect
    return await get_settings(db)


@router.get("/export")
async def export_json(db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Account))
    accounts = res.scalars().all()
    out = []
    for a in accounts:
        out.append({
            "id": a.id, "phone": a.phone, "first_name": a.first_name,
            "last_name": a.last_name, "username": a.username, "bio": a.bio,
            "status": a.status, "has_2fa": a.has_2fa,
            "tg_user_id": a.tg_user_id, "created_at": a.created_at.isoformat() if a.created_at else None,
        })
    return {
        "exported_at": utc_now_naive().isoformat(),
        "count": len(out),
        "accounts": out,
    }


@router.post("/backup")
async def create_local_backup():
    path = await create_backup()
    return {"ok": True, "name": path.name}


@router.get("/backups")
async def get_local_backups():
    return {"backups": list_backups()}


@router.get("/diagnostics")
async def diagnostics(db: AsyncSession = Depends(get_db)):
    database_ok, _ = await check_database_integrity()
    account_count = await db.scalar(select(func.count(Account.id))) or 0
    clients = await manager.all_clients()
    return {
        "app_version": APP_VERSION,
        "python_version": platform.python_version(),
        "windows_version": platform.platform(),
        "database": "ok" if database_ok else "error",
        "secret_store": "ok" if secrets_store.validate_existing_store()[0] else "error",
        "secret_store_detail": secrets_store.validate_existing_store()[1],
        "sessions_directory": str(env_settings.sessions_path),
        "accounts": account_count,
        "connected": sum(1 for client in clients.values() if client.is_connected()),
        "log_directory": str(env_settings.logs_path),
        "pid": os.getpid(),
    }


@router.get("/logs")
async def recent_logs(limit: int = 100, errors_only: bool = False):
    limit = min(max(limit, 1), 500)
    path = env_settings.logs_path / "app.log"
    if not path.exists():
        return {"lines": []}
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        lines = deque(handle, maxlen=5000)
    if errors_only:
        lines = deque((line for line in lines if " ERROR " in line or " CRITICAL " in line), maxlen=limit)
    return {"lines": list(lines)[-limit:]}


@router.post("/logs/open-folder")
async def open_log_folder():
    if os.name != "nt":
        raise RuntimeError("Opening the log folder is only supported on Windows")
    os.startfile(str(env_settings.logs_path))
    return {"ok": True}
