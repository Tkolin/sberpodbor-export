"""Dashboard aggregates.

Every query here is bounded (top-N or grouped by month) — the tables run to hundreds of
thousands of rows and the dashboard polls, so nothing may scan the whole dataset per call
without an index behind it.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AtsUser,
    Candidate,
    Comment,
    Contact,
    LogEntry,
    Profile,
    Resume,
    SyncRun,
    Vacancy,
)
from app.scraper.phases import queue_sizes


async def totals(session: AsyncSession) -> dict[str, int]:
    async def count(model) -> int:
        return await session.scalar(select(func.count()).select_from(model)) or 0

    return {
        "profiles": await count(Profile),
        "candidates": await count(Candidate),
        "vacancies": await count(Vacancy),
        "ats_users": await count(AtsUser),
        "resumes": await count(Resume),
        "contacts": await count(Contact),
        "logs": await count(LogEntry),
        "comments": await count(Comment),
    }


async def coverage(session: AsyncSession) -> dict[str, Any]:
    """How complete the dataset is — the numbers the operator actually watches."""
    profiles = await session.scalar(select(func.count()).select_from(Profile)) or 0
    candidates = await session.scalar(select(func.count()).select_from(Candidate)) or 0
    details_done = await session.scalar(
        select(func.count()).select_from(Profile).where(Profile.details_synced_at.is_not(None))
    ) or 0
    activity_done = await session.scalar(
        select(func.count()).select_from(Candidate).where(Candidate.activity_synced_at.is_not(None))
    ) or 0
    sizes = await queue_sizes(session)
    return {
        "profiles": profiles,
        "candidates": candidates,
        "details_done": details_done,
        "activity_done": activity_done,
        "details_pct": round(details_done / profiles * 100, 2) if profiles else 0.0,
        "activity_pct": round(activity_done / candidates * 100, 2) if candidates else 0.0,
        **sizes,
    }


async def candidates_by_status(session: AsyncSession, limit: int = 12) -> list[dict]:
    rows = (
        await session.execute(
            select(Candidate.status_title, func.count().label("n"))
            .where(Candidate.status_title.is_not(None))
            .group_by(Candidate.status_title)
            .order_by(func.count().desc())
            .limit(limit)
        )
    ).all()
    return [{"status": r[0], "count": r[1]} for r in rows]


async def candidates_over_time(session: AsyncSession, months: int = 36) -> list[dict]:
    """When applications appeared, by month.

    Deliberately NOT candidates.created_at: the ATS returns that field for almost nothing
    (41 rows out of 326k in the current dataset), so a chart built on it would look empty
    and be wrong. The first log entry of an application ("Прикреплён к вакансии…") is the
    real creation moment and is present for every candidate that has been crawled.
    """
    stmt = text(
        """
        WITH first_touch AS (
            SELECT candidate_id, min(coalesce(c.created_at, l.date_time_at)) AS at
            FROM logs l
            JOIN candidates c USING (candidate_id)
            WHERE l.date_time_at IS NOT NULL
            GROUP BY candidate_id
        )
        SELECT to_char(date_trunc('month', at), 'YYYY-MM') AS bucket, count(*) AS n
        FROM first_touch
        WHERE at >= date_trunc('month', now()) - make_interval(months => :months)
        GROUP BY 1 ORDER BY 1
        """
    )
    rows = (await session.execute(stmt, {"months": months})).all()
    return [{"month": r[0], "count": r[1]} for r in rows]


async def activity_over_time(session: AsyncSession, months: int = 36) -> list[dict]:
    stmt = text(
        """
        WITH l AS (
            SELECT to_char(date_trunc('month', date_time_at), 'YYYY-MM') AS bucket, count(*) AS n
            FROM logs
            WHERE date_time_at IS NOT NULL
              AND date_time_at >= date_trunc('month', now()) - make_interval(months => :months)
            GROUP BY 1
        ), c AS (
            SELECT to_char(date_trunc('month', created_at), 'YYYY-MM') AS bucket, count(*) AS n
            FROM comments
            WHERE created_at IS NOT NULL
              AND created_at >= date_trunc('month', now()) - make_interval(months => :months)
            GROUP BY 1
        )
        SELECT coalesce(l.bucket, c.bucket) AS bucket,
               coalesce(l.n, 0) AS logs,
               coalesce(c.n, 0) AS comments
        FROM l FULL OUTER JOIN c ON l.bucket = c.bucket
        ORDER BY 1
        """
    )
    rows = (await session.execute(stmt, {"months": months})).all()
    return [{"month": r[0], "logs": r[1], "comments": r[2]} for r in rows]


async def top_cities(session: AsyncSession, limit: int = 12) -> list[dict]:
    rows = (
        await session.execute(
            select(Profile.city, func.count().label("n"))
            .where(Profile.city.is_not(None), Profile.city != "")
            .group_by(Profile.city)
            .order_by(func.count().desc())
            .limit(limit)
        )
    ).all()
    return [{"city": r[0], "count": r[1]} for r in rows]


async def resume_sources(session: AsyncSession) -> list[dict]:
    # Group by the bare column: coalesce() in both SELECT and GROUP BY compiles to two
    # separate bind parameters, which Postgres refuses to treat as the same expression.
    rows = (
        await session.execute(
            select(Resume.source, func.count().label("n"))
            .group_by(Resume.source)
            .order_by(func.count().desc())
            .limit(12)
        )
    ).all()
    return [{"source": r[0] or "не указан", "count": r[1]} for r in rows]


async def top_vacancies(session: AsyncSession, limit: int = 10) -> list[dict]:
    rows = (
        await session.execute(
            select(Candidate.vacancy_title, func.count().label("n"))
            .where(Candidate.vacancy_title.is_not(None))
            .group_by(Candidate.vacancy_title)
            .order_by(func.count().desc())
            .limit(limit)
        )
    ).all()
    return [{"vacancy": r[0], "count": r[1]} for r in rows]


async def recruiter_workload(session: AsyncSession, limit: int = 10) -> list[dict]:
    """Recruiters are a JSONB array on each application; unnest and count."""
    stmt = text(
        """
        SELECT trim(concat_ws(' ', r->>'lastName', r->>'firstName')) AS name,
               count(*) AS n
        FROM candidates, jsonb_array_elements(recruiters) AS r
        WHERE recruiters IS NOT NULL AND jsonb_typeof(recruiters) = 'array'
        GROUP BY 1
        HAVING trim(concat_ws(' ', r->>'lastName', r->>'firstName')) <> ''
        ORDER BY 2 DESC
        LIMIT :limit
        """
    )
    rows = (await session.execute(stmt, {"limit": limit})).all()
    return [{"recruiter": r[0], "count": r[1]} for r in rows]


async def recent_runs(session: AsyncSession, limit: int = 10) -> list[dict]:
    rows = (await session.scalars(select(SyncRun).order_by(SyncRun.queued_at.desc()).limit(limit))).all()
    return [
        {
            "id": r.id,
            "kind": r.kind.value,
            "status": r.status.value,
            "triggered_by": r.triggered_by,
            "queued_at": r.queued_at,
            "started_at": r.started_at,
            "finished_at": r.finished_at,
            "total": r.total,
            "processed": r.processed,
            "deferred": r.deferred,
            "error": r.error,
        }
        for r in rows
    ]


async def dashboard(session: AsyncSession) -> dict[str, Any]:
    return {
        "totals": await totals(session),
        "coverage": await coverage(session),
        "candidates_by_status": await candidates_by_status(session),
        "candidates_over_time": await candidates_over_time(session),
        "activity_over_time": await activity_over_time(session),
        "top_cities": await top_cities(session),
        "resume_sources": await resume_sources(session),
        "top_vacancies": await top_vacancies(session),
        "recruiter_workload": await recruiter_workload(session),
        "recent_runs": await recent_runs(session),
    }
