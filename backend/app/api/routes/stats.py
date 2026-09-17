from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import CurrentUser, RequireAdmin, SessionDep
from app.models import AuditLog
from app.api.routes.data import rows as data_rows
from app.services import stats as svc

router = APIRouter(tags=["stats"])


@router.get("/stats/dashboard")
async def dashboard(_: CurrentUser, session: SessionDep) -> dict[str, Any]:
    """Everything the dashboard needs, in one round trip."""
    return await svc.dashboard(session)


@router.get("/stats/totals")
async def totals(_: CurrentUser, session: SessionDep) -> dict[str, int]:
    return await svc.totals(session)


@router.get("/stats/coverage")
async def coverage(_: CurrentUser, session: SessionDep) -> dict[str, Any]:
    return await svc.coverage(session)


@router.get("/audit")
async def audit_log(
    _: RequireAdmin, session: SessionDep, limit: int = 100, offset: int = 0
) -> list[dict[str, Any]]:
    found = (await session.scalars(
        select(AuditLog).order_by(AuditLog.created_at.desc()).offset(offset).limit(min(limit, 500))
    )).all()
    return data_rows(found)
