"""Sync control. The API only queues work; the singleton worker executes it."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select

from app.api.deps import CurrentUser, RequireOperator, SessionDep, audit
from app.models import SyncKind, SyncRun, SyncStatus
from app.scraper.phases import queue_sizes
from app.schemas import SyncRunOut, SyncTriggerIn

router = APIRouter(prefix="/sync", tags=["sync"])


@router.get("/status")
async def sync_status(_: CurrentUser, session: SessionDep) -> dict:
    active = await session.scalar(
        select(SyncRun)
        .where(SyncRun.status.in_([SyncStatus.queued, SyncStatus.running]))
        .order_by(SyncRun.queued_at)
        .limit(1)
    )
    last = await session.scalar(
        select(SyncRun).where(SyncRun.status == SyncStatus.done).order_by(SyncRun.finished_at.desc()).limit(1)
    )
    return {
        "active": SyncRunOut.model_validate(active) if active else None,
        "last_completed": SyncRunOut.model_validate(last) if last else None,
        "queues": await queue_sizes(session),
    }


@router.get("/runs", response_model=list[SyncRunOut])
async def list_runs(_: CurrentUser, session: SessionDep, limit: int = 50) -> list[SyncRun]:
    return list((await session.scalars(
        select(SyncRun).order_by(SyncRun.queued_at.desc()).limit(min(limit, 200))
    )).all())


@router.post("/trigger", response_model=SyncRunOut, status_code=status.HTTP_202_ACCEPTED)
async def trigger(
    payload: SyncTriggerIn, user: RequireOperator, request: Request, session: SessionDep
) -> SyncRun:
    busy = await session.scalar(
        select(SyncRun.id).where(SyncRun.status.in_([SyncStatus.queued, SyncStatus.running])).limit(1)
    )
    if busy:
        # Serialised on purpose: concurrent crawls would fight over the one token per account.
        raise HTTPException(status.HTTP_409_CONFLICT, "A sync is already queued or running")

    run = SyncRun(kind=SyncKind(payload.kind), triggered_by=user.email)
    session.add(run)
    await session.commit()
    await session.refresh(run)
    await audit(session, request, user, "sync.triggered", payload.kind, {"run_id": run.id})
    return run


@router.post("/runs/{run_id}/cancel", response_model=SyncRunOut)
async def cancel(run_id: int, user: RequireOperator, request: Request, session: SessionDep) -> SyncRun:
    run = await session.get(SyncRun, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such run")
    if run.status not in (SyncStatus.queued, SyncStatus.running):
        raise HTTPException(status.HTTP_409_CONFLICT, f"Run is already {run.status.value}")

    # A queued run vanishes immediately; a running one stops at its next checkpoint, since
    # killing it mid-request would leave the lane's token state unknown.
    run.status = SyncStatus.cancelled
    run.finished_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(run)
    await audit(session, request, user, "sync.cancelled", str(run_id))
    return run
