from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime
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
from ..auth import verify_app_password
from ..utils import friendly_error

router = APIRouter(prefix="/api", tags=["accounts"])


async def _account_to_out(acc: Account, db: AsyncSession) -> AccountOut:
    unread = await db.scalar(
        select(func.count(SecurityMessage.id)).where(
            SecurityMessage.account_id == acc.id, SecurityMessage.is_read == False  # noqa: E712
        )
    )
    return AccountOut(
        id=acc.id,
        phone=acc.phone,
        tg_user_id=acc.tg_user_id,
        first_name=acc.first_name,
        last_name=acc.last_name,
        username=acc.username,
        bio=acc.bio,
        status=acc.status,
        has_2fa=acc.has_2fa,
        is_online=acc.is_online,
        last_seen=acc.last_seen,
        unread_security=unread or 0,
    )


@router.get("/accounts", response_model=list[AccountOut])
async def list_accounts(db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Account).order_by(Account.id))
    out = []
    for acc in res.scalars().all():
        out.append(await _account_to_out(acc, db))
    return out


@router.post("/accounts/remove_all")
async def remove_all_accounts(body: RemoveAllAccountsIn, db: AsyncSession = Depends(get_db)):
    if not verify_app_password(body.password):
        raise HTTPException(400, "Wrong password")

    res = await db.execute(select(Account).order_by(Account.id))
    accounts = list(res.scalars().all())
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


@router.delete("/accounts/{account_id}")
async def delete_account(account_id: int, db: AsyncSession = Depends(get_db)):
    acc = await db.get(Account, account_id)
    if not acc:
        raise HTTPException(404, "Account not found")
    # Tombstone the departing account so it shows in Gone/Banned history. Skip
    # if it's already banned — that was logged at the ban transition (no dupes).
    if acc.status != "banned":
        await record_gone_account(db, acc, "removed")
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
        raise HTTPException(400, f"send_code failed: {e}")
    return {"ok": True}


async def _persist_account(db: AsyncSession, phone: str, me) -> Account:
    res = await db.execute(select(Account).where(Account.phone == phone))
    acc = res.scalar_one_or_none()
    if acc and manager.get(acc.id):
        await manager.stop_client(acc.id)
    session_file = await manager.promote_phone_session(phone, me)
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
    try:
        await manager.start_client(acc)
    except Exception:
        pass
    return acc


def _import_error(e: Exception) -> str:
    if type(e).__name__ == "RuntimeError":
        return str(e) or "Import failed"
    return friendly_error(e)


@router.post("/auth/import_sessions")
async def import_sessions(files: list[UploadFile] = File(...)):
    if not files:
        raise HTTPException(400, "No session files uploaded")

    results: list[dict] = []
    success = failed = skipped = 0

    for idx, upload in enumerate(files, start=1):
        filename = upload.filename or f"session_{idx}.session"
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
                skipped += 1
                results.append(row)
                continue

            data = await upload.read()
            if not data:
                row["status"] = "skipped"
                row["detail"] = "File is empty"
                skipped += 1
                results.append(row)
                continue

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

                if acc and manager.get(acc.id):
                    await manager.stop_client(acc.id)

                session_file = await manager.promote_imported_session(temp_base, phone, me)
                promoted = True

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
                row["account_id"] = acc.id

                detail = "Updated existing account" if replacing else "Imported"
                try:
                    await manager.start_client(acc)
                except Exception as start_err:
                    detail += f"; saved but could not start now: {_import_error(start_err)}"

                row["status"] = "ok"
                row["detail"] = detail
                success += 1
        except Exception as e:
            failed += 1
            row["status"] = "failed"
            row["detail"] = _import_error(e)
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
        raise HTTPException(400, f"sign_in failed: {e}")

    if needs_2fa:
        return {"needs_2fa": True}

    acc = await _persist_account(db, body.phone, me)
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
        # Likely wrong password. Keep pending session alive so user can retry.
        msg = str(e)
        if "PASSWORD" in msg.upper() or "password" in msg:
            raise HTTPException(400, "Wrong 2FA password")
        raise HTTPException(400, f"2FA failed: {e}")

    acc = await _persist_account(db, body.phone, me)
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
        raise HTTPException(400, f"qr_start failed: {e}")
    return info


@router.post("/auth/qr/recreate", response_model=QrStartOut)
async def qr_recreate(body: QrPollIn):
    try:
        info = await asyncio.wait_for(manager.qr_recreate(body.qr_id), timeout=45)
    except asyncio.TimeoutError:
        raise HTTPException(504, "Telegram took too long. Try again.")
    except Exception as e:
        raise HTTPException(400, f"qr_recreate failed: {e}")
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
        msg = str(e)
        if "PASSWORD" in msg.upper() or "password" in msg:
            raise HTTPException(400, "Wrong 2FA password")
        raise HTTPException(400, f"2FA failed: {e}")
    return await _finalize_qr(body.qr_id, db)


@router.post("/auth/qr/cancel")
async def qr_cancel(body: QrPollIn):
    await manager.qr_cancel(body.qr_id)
    return {"ok": True}


async def _finalize_qr(qr_id: str, db: AsyncSession):
    """Promote QR-temp session to phone-keyed session, persist account row,
    start a long-lived client. Returns the response payload for poll/2fa."""
    me, _temp_cli, _temp_path = await manager.qr_finalize(qr_id)
    if not me:
        raise HTTPException(400, "Could not read user info from Telegram")
    phone = me.phone
    if not phone:
        raise HTTPException(400, "Telegram did not return a phone number for this user")
    phone = phone if phone.startswith('+') else f"+{phone}"

    res = await db.execute(select(Account).where(Account.phone == phone))
    existing = res.scalar_one_or_none()
    if existing and manager.get(existing.id):
        await manager.stop_client(existing.id)

    # Move temp session file to canonical acc_<phone>.session, disconnect temp client
    await manager.qr_promote_to_phone(qr_id, phone)

    acc = await _persist_account(db, phone, me)
    out = await _account_to_out(acc, db)
    return {"state": "authorized", "account": out.model_dump(mode="json")}
