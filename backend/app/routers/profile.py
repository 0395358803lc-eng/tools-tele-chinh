from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
from telethon.tl.functions.account import (
    UpdateProfileRequest, UpdateUsernameRequest, CheckUsernameRequest,
)
from telethon.tl.functions.photos import UploadProfilePhotoRequest, DeletePhotosRequest, GetUserPhotosRequest
from telethon.errors import UsernameOccupiedError, UsernameInvalidError, FloodWaitError
import io
import logging
import os
import tempfile

from ..db import get_db
from ..models import Account
from ..schemas import AccountOut, ProfileUpdateIn, UsernameUpdateIn, UsernameCheckOut
from ..tg_manager import manager
from ..uploads import ensure_image_upload, read_limited, sanitize_filename, validate_image_bytes, IMAGE_MAX_BYTES
from ..utils import friendly_error
from .accounts import _account_to_out

router = APIRouter(prefix="/api/accounts/{account_id}/profile", tags=["profile"])
log = logging.getLogger("profile")


def _cleanup_temp_file(path: str) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        return
    except OSError as exc:
        log.warning(
            "profile photo temp cleanup failed file=%s error_type=%s",
            os.path.basename(path),
            type(exc).__name__,
        )


def _client_or_404(account_id: int):
    cli = manager.get(account_id)
    if not cli:
        raise HTTPException(409, "Account is not connected")
    return cli


@router.put("", response_model=AccountOut)
async def update_profile(account_id: int, body: ProfileUpdateIn, db: AsyncSession = Depends(get_db)):
    acc = await db.get(Account, account_id)
    if not acc:
        raise HTTPException(404, "Account not found")
    cli = _client_or_404(account_id)
    try:
        kw = {}
        if body.first_name is not None:
            kw["first_name"] = body.first_name
        if body.last_name is not None:
            kw["last_name"] = body.last_name
        if body.bio is not None:
            if len(body.bio) > 70:
                raise HTTPException(400, "Bio max 70 chars")
            kw["about"] = body.bio
        if kw:
            await manager.run_account_action(
                account_id,
                lambda: cli(UpdateProfileRequest(**kw)),
                operation="update_profile",
            )
        if body.first_name is not None: acc.first_name = body.first_name
        if body.last_name is not None: acc.last_name = body.last_name
        if body.bio is not None: acc.bio = body.bio
        await db.commit()
        await db.refresh(acc)
    except FloodWaitError as e:
        raise HTTPException(429, f"FloodWait: wait {e.seconds}s")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, friendly_error(e))
    return await _account_to_out(acc, db)


@router.get("/check_username", response_model=UsernameCheckOut)
async def check_username(account_id: int, username: str):
    cli = _client_or_404(account_id)
    if not username:
        return UsernameCheckOut(available=False, reason="empty")
    try:
        ok = await cli(CheckUsernameRequest(username=username))
        return UsernameCheckOut(available=bool(ok))
    except UsernameInvalidError:
        return UsernameCheckOut(available=False, reason="invalid")
    except UsernameOccupiedError:
        return UsernameCheckOut(available=False, reason="occupied")
    except Exception:
        return UsernameCheckOut(available=False, reason="check_failed")


@router.put("/username", response_model=AccountOut)
async def update_username(account_id: int, body: UsernameUpdateIn, db: AsyncSession = Depends(get_db)):
    acc = await db.get(Account, account_id)
    if not acc:
        raise HTTPException(404, "Account not found")
    cli = _client_or_404(account_id)
    try:
        await manager.run_account_action(
            account_id,
            lambda: cli(UpdateUsernameRequest(username=body.username)),
            operation="update_username",
        )
        acc.username = body.username
        await db.commit()
        await db.refresh(acc)
    except UsernameOccupiedError:
        raise HTTPException(409, "Username already taken")
    except UsernameInvalidError:
        raise HTTPException(400, "Invalid username")
    except FloodWaitError as e:
        raise HTTPException(429, f"FloodWait: wait {e.seconds}s")
    except Exception as e:
        raise HTTPException(400, friendly_error(e))
    return await _account_to_out(acc, db)


@router.post("/photo", response_model=AccountOut)
async def upload_photo(account_id: int, file: UploadFile = File(...), db: AsyncSession = Depends(get_db)):
    acc = await db.get(Account, account_id)
    if not acc:
        raise HTTPException(404, "Account not found")
    cli = _client_or_404(account_id)
    ensure_image_upload(file)
    data = await read_limited(file, IMAGE_MAX_BYTES)
    validate_image_bytes(data)
    # Telethon needs a file path or BinaryIO with a name
    suffix = sanitize_filename(file.filename) or "photo.jpg"
    suffix = os.path.splitext(suffix)[1] or ".jpg"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(data)
        tmp_path = tmp.name
    try:
        async def _upload_photo():
            uploaded = await cli.upload_file(tmp_path)
            await cli(UploadProfilePhotoRequest(file=uploaded))

        await manager.run_account_action(
            account_id, _upload_photo, operation="upload_profile_photo"
        )
    except FloodWaitError as e:
        raise HTTPException(429, f"FloodWait: wait {e.seconds}s")
    except Exception as e:
        raise HTTPException(400, friendly_error(e))
    finally:
        _cleanup_temp_file(tmp_path)
    return await _account_to_out(acc, db)


@router.get("/photo_url")
async def get_photo_url(account_id: int):
    """Return current profile photo as base64 data url (small)."""
    cli = _client_or_404(account_id)
    try:
        me = await cli.get_me()
        buf = io.BytesIO()
        downloaded = await cli.download_profile_photo(me, file=buf)
        if not downloaded:
            return {"data_url": None}
        import base64
        b64 = base64.b64encode(buf.getvalue()).decode()
        return {"data_url": f"data:image/jpeg;base64,{b64}"}
    except Exception:
        return {"data_url": None}
