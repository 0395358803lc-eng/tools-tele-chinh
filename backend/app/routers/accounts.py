from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime
from contextlib import suppress
import asyncio
import tempfile
from pathlib import Path

from ..db import get_db, AsyncSessionLocal
from ..models import Account, SecurityMessage, PendingLogin, GoneAccount
from ..schemas import (
    AccountOut, GoneAccountOut, StatsOut, SendCodeIn, SignInIn,
    QrStartOut, QrPollIn, QrSubmit2faIn, RemoveAllAccountsIn,
)
from ..tg_manager import manager, record_gone_account
from ..config import settings
from ..auth import verify_app_password
from ..utils import friendly_error
from ..errors import error_code_of, error_params_of
from ..uploads import sanitize_filename, read_limited, SESSION_MAX_BYTES
from .. import secrets_store

router = APIRouter(prefix="/api", tags=["accounts"])


async def _account_to_out(acc: Account, db: AsyncSession, unread: int | None = None) -> AccountOut:
    if unread is None:
        unread = await db.scalar(
            select(func.count(SecurityMessage.id)).where(
                SecurityMessage.account_id == acc.id, SecurityMessage.is_read == False  # noqa: E712
            )
        )
    runtime_status = "cooldown" if manager.cooldown_remaining(acc.id) > 0 else acc.status
    return AccountOut(
        id=acc.id,
        phone=acc.phone,
        tg_user_id=acc.tg_user_id,
        first_name=acc.first_name,
        last_name=acc.last_name,
        username=acc.username,
        bio=acc.bio,
        status=runtime_status,
        has_2fa=acc.has_2fa,
        is_online=acc.is_online,
        last_seen=acc.last_seen,
        unread_security=unread or 0,
    )


@router.get("/accounts", response_model=list[AccountOut])
async def list_accounts(db: AsyncSession = Depends(get_db)):
    unread_count = func.count(SecurityMessage.id).filter(
        SecurityMessage.is_read == False  # noqa: E712
    )
    res = await db.execute(
        select(Account, unread_count)
        .outerjoin(SecurityMessage, SecurityMessage.account_id == Account.id)
        .group_by(Account.id)
        .order_by(Account.id)
    )
    return [await _account_to_out(acc, db, int(unread or 0)) for acc, unread in res.all()]


@router.post("/accounts/remove_all")
async def remove_all_accounts(body: RemoveAllAccountsIn, db: AsyncSession = Depends(get_db)):
    if not verify_app_password(body.password):
        raise HTTPException(400, "Wrong password")

    res = await db.execute(select(Account).order_by(Account.id))
    accounts = list(res.scalars().all())
    for acc in accounts:
        await secrets_store.delete_2fa(acc.phone)
    removed = 0
    for acc in accounts:
        if acc.status != "banned":
            await record_gone_account(db, acc, "removed")
        await manager.remove_account_instance(acc)
        await db.delete(acc)
        removed += 1
    await db.commit()
    return {"ok": True, "removed": removed}


@router.get("/accounts/{account_id}", response_model=AccountOut)
async def get_account(account_id: int, db: AsyncSession = Depends(get_db)):
    acc = await db.get(Account, account_id)
    if not acc:
        raise HTTPException(404, "Account not found")
    return await _account_to_out(acc, db)


@router.post("/accounts/{account_id}/connect")
async def connect_account(account_id: int, db: AsyncSession = Depends(get_db)):
    acc = await db.get(Account, account_id)
    if not acc:
        raise HTTPException(404, "Account not found")
    if acc.status in {"banned", "session_revoked", "auth_error"}:
        raise HTTPException(409, "Account has a permanent authentication status")
    try:
        await manager._set_status(account_id, "connecting")
        await manager.start_client(acc)
    except Exception as e:
        raise HTTPException(400, friendly_error(e))
    return {"ok": True, "status": "connected"}


@router.post("/accounts/{account_id}/disconnect")
async def disconnect_account(account_id: int, db: AsyncSession = Depends(get_db)):
    if not await db.get(Account, account_id):
        raise HTTPException(404, "Account not found")
    await manager.disconnect_account(account_id)
    return {"ok": True, "status": "disconnected"}


@router.post("/accounts/connect_all")
async def connect_all_accounts(db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        select(Account).where(
            Account.status.notin_(["banned", "session_revoked", "auth_error"])
        ).order_by(Account.id)
    )
    accounts = list(res.scalars().all())
    sem = asyncio.Semaphore(max(1, int(settings.STARTUP_CONCURRENCY)))

    async def _connect(acc: Account):
        async with sem:
            try:
                await manager._set_status(acc.id, "connecting")
                await manager.start_client(acc)
                return {"account_id": acc.id, "status": "connected"}
            except Exception as exc:
                return {
                    "account_id": acc.id,
                    "status": "failed",
                    "detail": friendly_error(exc),
                }

    results = await asyncio.gather(*(_connect(acc) for acc in accounts))
    return {
        "ok": all(row["status"] == "connected" for row in results),
        "connected": sum(row["status"] == "connected" for row in results),
        "failed": sum(row["status"] == "failed" for row in results),
        "results": results,
    }


@router.post("/accounts/disconnect_all")
async def disconnect_all_accounts(db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Account.id).where(Account.status != "banned"))
    account_ids = list(res.scalars().all())
    await asyncio.gather(*(manager.disconnect_account(aid) for aid in account_ids))
    return {"ok": True, "disconnected": len(account_ids)}


@router.delete("/accounts/{account_id}")
async def delete_account(account_id: int, db: AsyncSession = Depends(get_db)):
    acc = await db.get(Account, account_id)
    if not acc:
        raise HTTPException(404, "Account not found")
    # Tombstone the departing account so it shows in Gone/Banned history. Skip
    # if it's already banned — that was logged at the ban transition (no dupes).
    if acc.status != "banned":
        await record_gone_account(db, acc, "removed")
    await secrets_store.delete_2fa(acc.phone)
    from .groups import _cache as group_cache
    group_cache.pop(account_id, None)
    await manager.remove_account_instance(acc)
    await db.delete(acc)
    await db.commit()
    return {"ok": True}


@router.get("/gone_accounts", response_model=list[GoneAccountOut])
async def list_gone_accounts(db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(GoneAccount).order_by(GoneAccount.gone_at.desc()))
    return res.scalars().all()


@router.delete("/gone_accounts")
async def clear_gone_accounts(db: AsyncSession = Depends(get_db)):
    await db.execute(delete(GoneAccount))
    await db.commit()
    return {"ok": True}


@router.delete("/gone_accounts/{gid}")
async def delete_gone_account(gid: int, db: AsyncSession = Depends(get_db)):
    g = await db.get(GoneAccount, gid)
    if g:
        await db.delete(g)
        await db.commit()
    return {"ok": True}


@router.get("/stats", response_model=StatsOut)
async def stats(db: AsyncSession = Depends(get_db)):
    total = await db.scalar(select(func.count(Account.id))) or 0
    connected = await db.scalar(select(func.count(Account.id)).where(Account.status == "connected")) or 0
    banned = await db.scalar(select(func.count(Account.id)).where(Account.status == "banned")) or 0
    with_2fa = await db.scalar(select(func.count(Account.id)).where(Account.has_2fa == True)) or 0  # noqa: E712
    unread = await db.scalar(select(func.count(SecurityMessage.id)).where(SecurityMessage.is_read == False)) or 0  # noqa: E712
    return StatsOut(total=total, connected=connected, banned=banned, with_2fa=with_2fa, unread_security=unread)


# ----- Auth -----
@router.post("/auth/send_code")
async def send_code(body: SendCodeIn):
    try:
        await asyncio.wait_for(manager.send_code(body.phone), timeout=45)
    except asyncio.TimeoutError:
        raise HTTPException(504, "Telegram took too long to respond. Try again.")
    except Exception as e:
        raise HTTPException(400, friendly_error(e))
    return {"ok": True}


async def _persist_account(db: AsyncSession, phone: str, me, source_base: str) -> Account:
    """Atomically install a verified session and its account row.

    The prior session and database values remain recoverable until the new
    client has started successfully. Any commit/start failure restores both.
    """
    phone = manager.normalize_phone(phone)
    res = await db.execute(select(Account).where(Account.phone == phone))
    acc = res.scalar_one_or_none()
    existed = acc is not None
    old_values = None
    old_bases: list[str] = []
    was_running = False
    if acc:
        old_values = {
            name: getattr(acc, name) for name in (
                "phone", "tg_user_id", "first_name", "last_name", "username",
                "session_file", "status", "has_2fa", "is_online", "last_seen",
            )
        }
        old_bases = manager._session_path_candidates(acc)
        was_running = manager.get(acc.id) is not None
    if acc and was_running:
        await manager.stop_client(acc.id)
    destination = manager._desired_session_path(
        phone, getattr(me, "username", None), getattr(me, "id", None)
    )
    swap = manager.begin_session_swap(source_base, destination)
    try:
        session_file = Path(destination).name
        if not acc:
            acc = Account(
                phone=phone,
                tg_user_id=me.id,
                first_name=me.first_name or "",
                last_name=me.last_name or "",
                username=me.username or "",
                session_file=session_file,
                status="connected",
            )
            db.add(acc)
        else:
            acc.tg_user_id = me.id
            acc.first_name = me.first_name or ""
            acc.last_name = me.last_name or ""
            acc.username = me.username or ""
            acc.session_file = session_file
            acc.status = "connected"
        await db.commit()
        await db.refresh(acc)
        await manager.start_client(acc)
    except BaseException:
        with suppress(Exception):
            await db.rollback()
        if acc and getattr(acc, "id", None):
            with suppress(Exception):
                if existed and old_values:
                    current = await db.get(Account, acc.id)
                    if current:
                        for name, value in old_values.items():
                            setattr(current, name, value)
                else:
                    current = await db.get(Account, acc.id)
                    if current:
                        await db.delete(current)
                await db.commit()
        manager.rollback_session_swap(swap)
        if existed and was_running and acc:
            with suppress(Exception):
                restored = await db.get(Account, acc.id)
                if restored:
                    await manager.start_client(restored)
        raise
    manager.commit_session_swap(swap, old_bases)
    return acc


def _import_error(e: Exception) -> str:
    return friendly_error(e)


@router.post("/auth/import_sessions")
async def import_sessions(files: list[UploadFile] = File(...)):
    if not files:
        raise HTTPException(400, "No session files uploaded")
    if len(files) > int(settings.MAX_SESSION_UPLOAD_FILES):
        raise HTTPException(413, "Too many session files uploaded")

    results: list[dict] = []
    success = failed = skipped = 0
    total_upload_bytes = 0
    total_limit = max(1, int(settings.MAX_SESSION_UPLOAD_TOTAL_MB)) * 1024 * 1024

    for idx, upload in enumerate(files, start=1):
        filename = sanitize_filename(upload.filename) or f"session_{idx}.session"
        row = {
            "filename": filename,
            "phone": "",
            "name": "",
            "account_id": None,
            "status": "failed",
            "detail": "",
        }
        temp_base = ""
        promoted = False

        try:
            if not filename.lower().endswith(".session"):
                row["status"] = "skipped"
                row["detail"] = "Only .session files can be imported"
                row["error_code"] = "addAccount.onlySession"
                skipped += 1
                results.append(row)
                continue

            try:
                data = await read_limited(upload, SESSION_MAX_BYTES)
            except HTTPException as e:
                row["status"] = "failed"
                row["detail"] = e.detail
                row["error_code"] = "addAccount.importFailed"
                failed += 1
                results.append(row)
                continue
            if not data:
                row["status"] = "skipped"
                row["detail"] = "File is empty"
                row["error_code"] = "addAccount.fileEmpty"
                skipped += 1
                results.append(row)
                continue
            total_upload_bytes += len(data)
            if total_upload_bytes > total_limit:
                raise HTTPException(413, "Combined session upload is too large")

            with tempfile.NamedTemporaryFile(prefix="mtm_import_", suffix=".session", delete=False) as tmp:
                tmp.write(data)
                temp_base = str(Path(tmp.name).with_suffix(""))

            me, phone = await manager.inspect_imported_session(temp_base)
            display_name = f"{me.first_name or ''} {me.last_name or ''}".strip() or phone
            row["phone"] = phone
            row["name"] = display_name

            async with AsyncSessionLocal() as db:
                res = await db.execute(select(Account).where(Account.phone == phone))
                acc = res.scalar_one_or_none()
                replacing = bool(acc)

                acc = await _persist_account(db, phone, me, temp_base)
                promoted = True
                row["account_id"] = acc.id

                detail = "Updated existing account" if replacing else "Imported"
                row["error_code"] = "addAccount.updatedExisting" if replacing else "addAccount.imported"
                row["status"] = "ok"
                row["detail"] = detail
                success += 1
        except HTTPException as e:
            if e.status_code == 413 and e.detail == "Combined session upload is too large":
                raise
            failed += 1
            row["status"] = "failed"
            row["detail"] = str(e.detail)
        except Exception as e:
            failed += 1
            row["status"] = "failed"
            row["detail"] = _import_error(e)
            code = error_code_of(e)
            if code:
                row["error_code"] = code
                params = error_params_of(e)
                if params:
                    row["error_params"] = params
        finally:
            if temp_base and not promoted:
                manager._remove_session_files(temp_base)

        results.append(row)

    return {
        "ok": True,
        "total": len(files),
        "success": success,
        "failed": failed,
        "skipped": skipped,
        "results": results,
    }


@router.post("/auth/sync_sessions_folder")
async def sync_sessions_folder():
    return await manager.sync_session_folder(force=True)


@router.post("/auth/sign_in")
async def sign_in(body: SignInIn, db: AsyncSession = Depends(get_db)):
    """Step 1: submit the SMS code. If 2FA is enabled, returns
    {"needs_2fa": true} with HTTP 200 (NOT 401, which would log the user out)."""
    try:
        me, needs_2fa = await asyncio.wait_for(
            manager.submit_code(body.phone, body.code), timeout=45
        )
    except asyncio.TimeoutError:
        raise HTTPException(504, "Telegram took too long. Try again.")
    except Exception as e:
        raise HTTPException(400, friendly_error(e))

    if needs_2fa:
        return {"needs_2fa": True}

    phone = manager.normalize_phone(body.phone)
    try:
        source = manager.phone_session_source(phone)
        acc = await _persist_account(db, phone, me, source)
    finally:
        await manager.finish_phone_login(phone, remove_session=True)
    out = await _account_to_out(acc, db)
    return {"needs_2fa": False, "account": out.model_dump(mode="json")}


@router.post("/auth/sign_in_2fa")
async def sign_in_2fa(body: SignInIn, db: AsyncSession = Depends(get_db)):
    """Step 2: submit 2FA password. Phone is the same one passed to /sign_in earlier."""
    if not body.password:
        raise HTTPException(400, "password required")
    try:
        me = await asyncio.wait_for(
            manager.submit_2fa(body.phone, body.password), timeout=45
        )
    except asyncio.TimeoutError:
        raise HTTPException(504, "Telegram took too long. Try again.")
    except Exception as e:
        if type(e).__name__ == "PasswordHashInvalidError":
            raise HTTPException(400, "Wrong 2FA password")
        raise HTTPException(400, friendly_error(e))

    phone = manager.normalize_phone(body.phone)
    try:
        source = manager.phone_session_source(phone)
        acc = await _persist_account(db, phone, me, source)
    finally:
        await manager.finish_phone_login(phone, remove_session=True)
    out = await _account_to_out(acc, db)
    return {"account": out.model_dump(mode="json")}


@router.post("/auth/cancel")
async def auth_cancel(body: SendCodeIn):
    await manager.cancel_pending(body.phone)
    return {"ok": True}


# ----- QR Auth -----
@router.post("/auth/qr/start", response_model=QrStartOut)
async def qr_start():
    try:
        info = await asyncio.wait_for(manager.qr_start(), timeout=45)
    except asyncio.TimeoutError:
        raise HTTPException(504, "Telegram took too long. Try again.")
    except Exception as e:
        raise HTTPException(400, friendly_error(e))
    return info


@router.post("/auth/qr/recreate", response_model=QrStartOut)
async def qr_recreate(body: QrPollIn):
    try:
        info = await asyncio.wait_for(manager.qr_recreate(body.qr_id), timeout=45)
    except asyncio.TimeoutError:
        raise HTTPException(504, "Telegram took too long. Try again.")
    except Exception as e:
        raise HTTPException(400, friendly_error(e))
    return info


@router.post("/auth/qr/poll")
async def qr_poll(body: QrPollIn, db: AsyncSession = Depends(get_db)):
    """Poll the QR session. Possible response shapes:
      {"state": "waiting"}
      {"state": "needs_2fa"}
      {"state": "expired"}
      {"state": "error", "error": "..."}
      {"state": "authorized", "account": {...}}  -- account persisted
    """
    status = await manager.qr_status(body.qr_id)
    if status.get('state') == 'finalized':
        return {k: v for k, v in status.items() if k != 'state'}
    if status.get('state') != 'authorized':
        return status
    return await _finalize_qr(body.qr_id, db)


@router.post("/auth/qr/sign_in_2fa")
async def qr_sign_in_2fa(body: QrSubmit2faIn, db: AsyncSession = Depends(get_db)):
    if not body.password:
        raise HTTPException(400, "password required")
    try:
        await asyncio.wait_for(
            manager.qr_submit_2fa(body.qr_id, body.password), timeout=45
        )
    except asyncio.TimeoutError:
        raise HTTPException(504, "Telegram took too long. Try again.")
    except Exception as e:
        if type(e).__name__ == "PasswordHashInvalidError":
            raise HTTPException(400, "Wrong 2FA password")
        raise HTTPException(400, friendly_error(e))
    return await _finalize_qr(body.qr_id, db)


@router.post("/auth/qr/cancel")
async def qr_cancel(body: QrPollIn):
    await manager.qr_cancel(body.qr_id)
    return {"ok": True}


async def _finalize_qr(qr_id: str, db: AsyncSession):
    """Promote QR-temp session to phone-keyed session, persist account row,
    start a long-lived client. Returns the response payload for poll/2fa."""
    async with manager.qr_finalize_lock(qr_id):
        completed = manager.qr_completed(qr_id)
        if completed:
            return completed
        me, _temp_cli, _temp_path = await manager.qr_finalize(qr_id)
        if not me:
            raise HTTPException(400, "Could not read user info from Telegram")
        phone = getattr(me, "phone", None)
        if not phone:
            raise HTTPException(400, "Telegram did not return a phone number for this user")
        phone = manager.normalize_phone(phone)
        source = await manager.qr_promote_to_phone(qr_id, phone)
        acc = await _persist_account(db, phone, me, source)
        out = await _account_to_out(acc, db)
        payload = {"state": "authorized", "account": out.model_dump(mode="json")}
        manager.mark_qr_completed(qr_id, payload)
        await manager.finish_qr(qr_id, remove_session=True)
        return payload
