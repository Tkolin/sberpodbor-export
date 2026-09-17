"""Read-only browsing of the collected dataset."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import Select, func, inspect as sa_inspect, or_, select

from app.api.deps import CurrentUser, SessionDep
from app.models import (
    AtsUser, Candidate, Comment, Contact, LogEntry, MediaFile, MediaStatus,
    Profile, Resume, Vacancy, WorkHistory,
)

router = APIRouter(prefix="/data", tags=["data"])

MAX_PER_PAGE = 200


def row(obj: Any) -> dict[str, Any] | None:
    """SQLAlchemy object -> plain dict of its mapped columns.

    FastAPI cannot serialise ORM instances behind a `dict[str, Any]` annotation — Pydantic
    raises "Unable to serialize unknown type". Converting explicitly also keeps internal
    attributes and lazy relationships out of the response by construction.
    """
    if obj is None:
        return None
    return {c.key: getattr(obj, c.key) for c in sa_inspect(obj).mapper.column_attrs}


def rows(objs) -> list[dict[str, Any]]:
    return [row(o) for o in objs]


async def _paginate(session, stmt: Select, page: int, per_page: int) -> dict[str, Any]:
    per_page = min(per_page, MAX_PER_PAGE)
    total = await session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    found = (await session.execute(stmt.offset((page - 1) * per_page).limit(per_page))).scalars().all()
    return {"items": rows(found), "total": total, "page": page, "per_page": per_page}


@router.get("/profiles")
async def list_profiles(
    _: CurrentUser,
    session: SessionDep,
    q: Annotated[str | None, Query(description="Name, email or phone substring")] = None,
    city: str | None = None,
    vacancy_id: int | None = None,
    status_title: str | None = None,
    with_resume: bool | None = None,
    page: int = 1,
    per_page: int = 50,
) -> dict[str, Any]:
    stmt = select(Profile).order_by(Profile.id)

    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                Profile.first_name.ilike(like),
                Profile.last_name.ilike(like),
                Profile.middle_name.ilike(like),
                Profile.email.ilike(like),
                Profile.phone.ilike(like),
            )
        )
    if city:
        stmt = stmt.where(Profile.city == city)
    if with_resume is not None:
        sub = select(Resume.profile_id).where(Resume.profile_id == Profile.id)
        stmt = stmt.where(sub.exists() if with_resume else ~sub.exists())
    if vacancy_id or status_title:
        sub = select(Candidate.profile_id).where(Candidate.profile_id == Profile.id)
        if vacancy_id:
            sub = sub.where(Candidate.vacancy_id == vacancy_id)
        if status_title:
            sub = sub.where(Candidate.status_title == status_title)
        stmt = stmt.where(sub.exists())

    page_data = await _paginate(session, stmt, page, per_page)

    # Attach the photo id so the table can render avatars without an extra request per row.
    ids = [p["id"] for p in page_data["items"]]
    if ids:
        photos = dict((await session.execute(
            select(MediaFile.profile_id, MediaFile.id).where(
                MediaFile.profile_id.in_(ids),
                MediaFile.kind == "photo",
                MediaFile.status == MediaStatus.done,
            )
        )).all())
        page_data["photos"] = {str(k): v for k, v in photos.items()}
    return page_data


@router.get("/profiles/{profile_id}")
async def profile_detail(profile_id: int, _: CurrentUser, session: SessionDep) -> dict[str, Any]:
    """Everything known about one person, assembled for the detail drawer."""
    profile = await session.get(Profile, profile_id)
    if profile is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such profile")

    resume = await session.get(Resume, profile_id)
    contacts = (await session.scalars(
        select(Contact).where(Contact.profile_id == profile_id)
    )).all()
    candidates = list((await session.scalars(
        select(Candidate).where(Candidate.profile_id == profile_id).order_by(Candidate.candidate_id)
    )).all())
    cand_ids = [c.candidate_id for c in candidates]

    logs = comments = []
    if cand_ids:
        logs = (await session.scalars(
            select(LogEntry).where(LogEntry.candidate_id.in_(cand_ids)).order_by(LogEntry.date_time_at)
        )).all()
        comments = (await session.scalars(
            select(Comment).where(Comment.candidate_id.in_(cand_ids)).order_by(Comment.created_at)
        )).all()

    works = (await session.scalars(
        select(WorkHistory).where(WorkHistory.profile_id == profile_id).order_by(WorkHistory.ord)
    )).all()
    media = (await session.scalars(
        select(MediaFile).where(MediaFile.profile_id == profile_id)
    )).all()

    return {
        "profile": row(profile),
        "resume": row(resume),
        "contacts": rows(contacts),
        "candidates": rows(candidates),
        "logs": rows(logs),
        "comments": rows(comments),
        "work_history": rows(works),
        "media": [
            {"id": m.id, "kind": m.kind.value, "status": m.status.value,
             "file_size": m.file_size, "content_type": m.content_type}
            for m in media
        ],
    }


@router.get("/candidates")
async def list_candidates(
    _: CurrentUser,
    session: SessionDep,
    vacancy_id: int | None = None,
    status_title: str | None = None,
    page: int = 1,
    per_page: int = 50,
) -> dict[str, Any]:
    stmt = select(Candidate).order_by(Candidate.candidate_id)
    if vacancy_id:
        stmt = stmt.where(Candidate.vacancy_id == vacancy_id)
    if status_title:
        stmt = stmt.where(Candidate.status_title == status_title)
    return await _paginate(session, stmt, page, per_page)


@router.get("/vacancies")
async def list_vacancies(
    _: CurrentUser, session: SessionDep, status_filter: str | None = None,
    page: int = 1, per_page: int = 50,
) -> dict[str, Any]:
    stmt = select(Vacancy).order_by(Vacancy.id.desc())
    if status_filter:
        stmt = stmt.where(Vacancy.status == status_filter)
    return await _paginate(session, stmt, page, per_page)


@router.get("/ats-users")
async def list_ats_users(_: CurrentUser, session: SessionDep) -> list[dict[str, Any]]:
    return rows((await session.scalars(select(AtsUser).order_by(AtsUser.id))).all())


@router.get("/facets")
async def facets(_: CurrentUser, session: SessionDep) -> dict[str, list]:
    """Distinct values that drive the filter dropdowns."""
    cities = (await session.execute(
        select(Profile.city).where(Profile.city.is_not(None), Profile.city != "")
        .group_by(Profile.city).order_by(func.count().desc()).limit(200)
    )).scalars().all()
    statuses = (await session.execute(
        select(Candidate.status_title).where(Candidate.status_title.is_not(None))
        .group_by(Candidate.status_title).order_by(func.count().desc())
    )).scalars().all()
    vacancies = (await session.execute(
        select(Vacancy.id, Vacancy.title).order_by(Vacancy.id.desc()).limit(500)
    )).all()
    return {
        "cities": list(cities),
        "statuses": list(statuses),
        "vacancies": [{"id": v[0], "title": v[1]} for v in vacancies],
    }
