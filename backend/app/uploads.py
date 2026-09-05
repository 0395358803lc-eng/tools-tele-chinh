"""Shared upload guardrails."""
from __future__ import annotations

import io
import logging
import os
import re
from pathlib import Path

from fastapi import HTTPException, UploadFile

from .config import settings

log = logging.getLogger("uploads")

SESSION_MAX_BYTES = max(1, int(settings.MAX_SESSION_UPLOAD_MB)) * 1024 * 1024
IMAGE_MAX_BYTES = max(1, int(settings.MAX_IMAGE_UPLOAD_MB)) * 1024 * 1024
ALLOWED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_IMAGE_FORMATS = {"JPEG", "PNG", "WEBP"}


def _mb(value: int) -> str:
    return f"{value // (1024 * 1024)}MB"


def sanitize_filename(name: str | None) -> str:
    if isinstance(name, (tuple, list)):
        name = name[0] if name else None
    base = os.path.basename((name or "").replace("\\", "/")).strip()
    base = re.sub(r"[^A-Za-z0-9._+-]", "_", base).strip("._")
    return base[:200] or "file"


def ensure_upload_size(data: bytes, max_bytes: int, label: str):
    if len(data) > max_bytes:
        raise HTTPException(413, f"{label} is too large - max {_mb(max_bytes)}")


async def read_limited(
    upload: UploadFile, max_bytes: int, chunk_size: int = 64 * 1024
) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(chunk_size)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(413, f"Upload is too large - max {_mb(max_bytes)}")
        chunks.append(chunk)
    return b"".join(chunks)


def validate_image_bytes(data: bytes):
    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
            image_format = (image.format or "").upper()
    except Exception:
        raise HTTPException(415, "Uploaded file is not a valid image")
    if image_format not in ALLOWED_IMAGE_FORMATS:
        raise HTTPException(415, "Only JPEG, PNG and WEBP images are allowed")


def _ext_of(upload: UploadFile) -> str:
    return os.path.splitext(sanitize_filename(upload.filename))[1].lower()


def ensure_image_upload(upload: UploadFile):
    content_type = (upload.content_type or "").lower()
    if content_type.startswith("image/") or _ext_of(upload) in ALLOWED_IMAGE_EXTS:
        return
    raise HTTPException(415, "Only image files (jpg/png/webp) are allowed")



def cleanup_temp_files(paths: list[str]) -> None:
    """Best-effort removal for staged upload files with observable failures."""
    for raw_path in paths:
        if not raw_path:
            continue
        path = Path(raw_path)
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            log.warning(
                "temporary upload cleanup failed file=%s error=%s",
                path.name,
                type(exc).__name__,
            )
