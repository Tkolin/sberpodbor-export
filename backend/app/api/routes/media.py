"""Serving and monitoring the mirrored photos and resume files."""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Response, status
from fastapi.responses import FileResponse
from sqlalchemy import select

from app.api.deps import CurrentUser, SessionDep
from app.models import MediaFile, MediaKind, MediaStatus
from app.scraper.media import local_path, media_stats

router = APIRouter(prefix="/media", tags=["media"])


@router.get("/stats")
async def stats(_: CurrentUser, session: SessionDep) -> dict[str, Any]:
    return await media_stats(session)


@router.get("/profile/{profile_id}")
async def for_profile(profile_id: int, _: CurrentUser, session: SessionDep) -> list[dict]:
    rows = (await session.scalars(
        select(MediaFile).where(MediaFile.profile_id == profile_id)
    )).all()
    return [
        {
            "id": m.id,
            "kind": m.kind.value,
            "status": m.status.value,
            "file_size": m.file_size,
            "content_type": m.content_type,
            "filename": Path(m.rel_path).name,
            "source_url": m.source_url,
        }
        for m in rows
    ]


@router.get("/{media_id}/file")
async def serve(media_id: int, _: CurrentUser, session: SessionDep) -> Response:
    m = await session.get(MediaFile, media_id)
    if m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Нет такого файла")
    if m.status is not MediaStatus.done:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Файл не скачан ({m.status.value})")

    path = local_path(m.kind, m.rel_path)
    if not path.exists():
        # The row says done but the byte are gone — surface it instead of a blank 500.
        raise HTTPException(status.HTTP_410_GONE, "Файл числится скачанным, но отсутствует на диске")

    # Photos are shown inline in the panel; resume files are meant to be saved.
    disposition = "inline" if m.kind is MediaKind.photo else "attachment"
    # Files adopted from an earlier run have no stored content type; guessing from the
    # extension keeps images rendering inline instead of downloading as octet-stream.
    content_type = m.content_type or mimetypes.guess_type(path.name)[0]

    return FileResponse(
        path,
        media_type=content_type or "application/octet-stream",
        headers={"content-disposition": f'{disposition}; filename="{path.name}"'},
    )
