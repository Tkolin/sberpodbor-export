"""XML export: stream small selections inline, materialise big ones as a job on disk."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import select

from app.api.deps import CurrentUser, RequireOperator, SessionDep, audit
from app.config import settings
from app.db import SessionLocal
from app.models import ExportJob, ExportStatus
from app.schemas import ExportJobOut
from app.services.xml_export import ProfileFilters, stream_export

log = logging.getLogger("exports")
router = APIRouter(prefix="/exports", tags=["exports"])

# Above this, a synchronous download would hold a worker and an HTTP connection for many
# minutes; those go through the job queue instead.
INLINE_LIMIT = 5000


def _filters(
    city: str | None, vacancy_id: int | None, status_title: str | None,
    created_from: datetime | None, created_to: datetime | None,
    with_resume: bool | None, limit: int | None,
) -> ProfileFilters:
    return ProfileFilters(
        city=city, vacancy_id=vacancy_id, status_title=status_title,
        created_from=created_from, created_to=created_to,
        with_resume=with_resume, limit=limit,
    )


@router.get("/xml")
async def export_xml_inline(
    _: CurrentUser,
    session: SessionDep,
    city: str | None = None,
    vacancy_id: int | None = None,
    status_title: str | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    with_resume: bool | None = None,
    limit: int = Query(1000, le=INLINE_LIMIT, description=f"Max {INLINE_LIMIT}; use a job for more"),
    include_reference: bool = True,
) -> StreamingResponse:
    """Stream a filtered slice straight to the client."""
    filters = _filters(city, vacancy_id, status_title, created_from, created_to, with_resume, limit)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return StreamingResponse(
        stream_export(session, filters, include_reference=include_reference),
        media_type="application/xml; charset=utf-8",
        headers={"content-disposition": f'attachment; filename="sberpodbor-{stamp}.xml"'},
    )


async def _run_job(job_id: int) -> None:
    """Materialise the export in its own session — the request is long gone by now."""
    async with SessionLocal() as session:
        job = await session.get(ExportJob, job_id)
        if job is None:
            return
        job.status = ExportStatus.running
        await session.commit()

        path = Path(settings.export_dir) / job.filename
        rows = 0
        try:
            f = filters_from_dict(job.filters or {})
            tmp = path.with_suffix(".part")
            with open(tmp, "wb") as fh:
                async for chunk in stream_export(session, f):
                    fh.write(chunk)
                    rows += chunk.count(b"<profile ")
            os.replace(tmp, path)  # only a complete file ever appears under the final name
            job.status = ExportStatus.done
            job.size_bytes = path.stat().st_size
            job.row_count = rows
        except Exception as exc:  # noqa: BLE001 - surfaced on the job row
            log.exception("export job %s failed", job_id)
            job.status = ExportStatus.failed
            job.error = f"{type(exc).__name__}: {exc}"
        finally:
            job.finished_at = datetime.now(UTC)
            await session.commit()


def filters_from_dict(d: dict) -> ProfileFilters:
    def dt(key: str) -> datetime | None:
        return datetime.fromisoformat(d[key]) if d.get(key) else None

    return ProfileFilters(
        city=d.get("city"), vacancy_id=d.get("vacancy_id"), status_title=d.get("status_title"),
        created_from=dt("created_from"), created_to=dt("created_to"),
        with_resume=d.get("with_resume"), limit=d.get("limit"),
    )


@router.post("/jobs", response_model=ExportJobOut, status_code=status.HTTP_202_ACCEPTED)
async def create_job(
    user: RequireOperator,
    request: Request,
    session: SessionDep,
    background: BackgroundTasks,
    city: str | None = None,
    vacancy_id: int | None = None,
    status_title: str | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    with_resume: bool | None = None,
) -> ExportJob:
    """Queue a full-size export. Poll the job, then download when it reports done."""
    filters = _filters(city, vacancy_id, status_title, created_from, created_to, with_resume, None)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    job = ExportJob(
        requested_by=user.email,
        filters=filters.as_dict(),
        filename=f"sberpodbor-{stamp}-{user.id}.xml",
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)
    await audit(session, request, user, "export.queued", job.filename, filters.as_dict())

    background.add_task(_run_job, job.id)
    return job


@router.get("/jobs", response_model=list[ExportJobOut])
async def list_jobs(_: CurrentUser, session: SessionDep, limit: int = 50) -> list[ExportJob]:
    return list((await session.scalars(
        select(ExportJob).order_by(ExportJob.created_at.desc()).limit(min(limit, 200))
    )).all())


@router.get("/jobs/{job_id}", response_model=ExportJobOut)
async def get_job(job_id: int, _: CurrentUser, session: SessionDep) -> ExportJob:
    job = await session.get(ExportJob, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such job")
    return job


@router.get("/jobs/{job_id}/download")
async def download_job(job_id: int, _: CurrentUser, session: SessionDep) -> FileResponse:
    job = await session.get(ExportJob, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such job")
    if job.status is not ExportStatus.done:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Job is {job.status.value}")

    # filename is generated server-side, but re-anchor anyway so a tampered row cannot
    # walk out of the export directory.
    root = Path(settings.export_dir).resolve()
    path = (root / Path(job.filename).name).resolve()
    if not path.is_relative_to(root) or not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Export file is missing")

    return FileResponse(path, media_type="application/xml", filename=path.name)


@router.get("/schema.xsd")
async def schema(_: CurrentUser) -> FileResponse:
    path = Path(__file__).resolve().parents[3] / "xsd" / "sberpodbor-export-1.0.xsd"
    if not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Schema file is missing")
    return FileResponse(path, media_type="application/xml", filename=path.name)
