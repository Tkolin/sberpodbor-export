"""Crawl phases.

Cost shape, measured: the profile list is ~5 300 cheap paged requests (~20 min); resumes and
contacts are ~3.5 requests per profile; logs and comments are 2 per candidate and dominate
everything (~650 000 requests). So the continuous-refresh design is:

  sweep    - cheap, frequent. Re-pages the lists, upserts, and marks anything new as unsynced.
  details  - fills resumes/contacts for profiles that have none.
  activity - fills logs/comments for candidates that have none.
  rolling  - slowly re-queues the longest-unverified records so edits are eventually caught.

Nothing here checkpoints a record whose fetch hard-failed; see client.GAVE_UP.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import (
    Candidate, CandidateStatus, Comment, Contact, LogEntry, Profile, Resume, Vacancy,
)
from app.models.data import AtsUser
from app.models.data import WorkHistory
from app.scraper.client import FORBIDDEN, GAVE_UP, NOT_FOUND, ApiClient, Lane, Sentinel
from app.services.work_history import parse_work_history

log = logging.getLogger("scraper.phases")


def parse_dt(value: Any) -> datetime | None:
    """'2020-12-15T23:24:34+03:00' -> aware datetime. Tolerates junk and nulls."""
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def now() -> datetime:
    return datetime.now(UTC)


def as_int(value: Any) -> int | None:
    """The ATS returns ids as JSON strings ("814") about as often as numbers.

    SQLite accepted either without complaint; Postgres rejects a str for a BIGINT column,
    so every id crossing into the database goes through here.
    """
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def _upsert(session: AsyncSession, model, rows: list[dict], pk: str, update_cols: list[str]) -> int:
    if not rows:
        return 0
    stmt = insert(model).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=[pk],
        set_={c: getattr(stmt.excluded, c) for c in update_cols},
    )
    await session.execute(stmt)
    return len(rows)


# ── cheap list phases ───────────────────────────────────────────────────────
async def sweep_vacancies(client: ApiClient, session: AsyncSession) -> dict[str, int]:
    lane = client.lanes[0]
    page, total, seen = 1, None, 0
    per = settings.profiles_per_page
    while total is None or (page - 1) * per < total:
        j = await client.request(lane, f"/v2/vacancies?page={page}&itemsPerPage={per}")
        if isinstance(j, Sentinel) or not j.get("data"):
            break
        total = (j.get("meta") or {}).get("totalItems", total or 0)
        rows = []
        for v in j["data"]:
            a = v.get("attributes", {})
            rows.append(
                dict(
                    id=as_int(a["_id"]), title=a.get("title"), status=a.get("status"), city=a.get("city"),
                    persons_count=as_int(a.get("personsCount")),
                    desired_closing_at=parse_dt(a.get("desiredClosingAt")),
                    created_at=parse_dt(a.get("createdAt")), raw=a, last_seen_at=now(),
                )
            )
        seen += await _upsert(
            session, Vacancy, rows, "id",
            ["title", "status", "city", "persons_count", "desired_closing_at", "created_at", "raw", "last_seen_at"],
        )
        await session.commit()
        page += 1
    return {"vacancies": seen}


async def sweep_users(client: ApiClient, session: AsyncSession) -> dict[str, int]:
    lane = client.lanes[0]
    page, total, seen = 1, None, 0
    per = settings.profiles_per_page
    while total is None or (page - 1) * per < total:
        j = await client.request(lane, f"/v2/users?page={page}&itemsPerPage={per}")
        if isinstance(j, Sentinel) or not j.get("data"):
            break
        total = (j.get("meta") or {}).get("totalItems", total or 0)
        rows = [
            dict(
                id=as_int(u["attributes"]["_id"]),
                first_name=u["attributes"].get("firstName"),
                last_name=u["attributes"].get("lastName"),
                middle_name=u["attributes"].get("middleName"),
                email=u["attributes"].get("email"),
                role=u["attributes"].get("role"),
                position=u["attributes"].get("position"),
                status=u["attributes"].get("status"),
                raw=u["attributes"],
            )
            for u in j["data"]
        ]
        seen += await _upsert(
            session, AtsUser, rows, "id",
            ["first_name", "last_name", "middle_name", "email", "role", "position", "status", "raw"],
        )
        await session.commit()
        page += 1
    return {"ats_users": seen}


async def sweep_statuses(client: ApiClient, session: AsyncSession) -> dict[str, int]:
    """Статусы (этапы) подбора из настроек компании.

    POST /v2/status/struct — the endpoint the settings screen uses. Not guessable: singular
    path, POST with an empty body, and the response is a dict keyed by group rather than the
    JSON:API list every other collection returns:

        {"data": {"start":    {"title": "Новый",    "statuses": [...]},
                  "middle":   {"title": "В работе", "statuses": [...]},
                  "positive": {"title": "Наняты",   "statuses": [...]},
                  "negative": {"title": "Отказ",    "statuses": [...]}}}

    The per-application status_id/status_title we already store cannot replace this: it has
    no ordering, no group, and misses statuses nobody currently holds.
    """
    lane = client.lanes[0]
    j = await client.request(lane, "/v2/status/struct", method="POST", json_body={}, json_api=True)
    if isinstance(j, Sentinel) or not isinstance(j.get("data"), dict):
        log.warning("status struct unavailable (%r)", j)
        return {"statuses": 0}

    rows = []
    for group_code, body in j["data"].items():
        if not isinstance(body, dict):
            continue
        for st in body.get("statuses") or []:
            sid = as_int(st.get("id"))
            if sid is None:
                continue
            rows.append(
                dict(
                    id=sid, title=st.get("title"),
                    group_code=group_code, group_title=body.get("title"),
                    sort_order=as_int(st.get("sortOrder")),
                    background_color=st.get("backgroundColor"),
                    text_color=st.get("textColor"),
                    color_id=as_int(st.get("colorId")),
                    legal_time_limit=as_int(st.get("legalTimeLimit")),
                    raw=st, last_seen_at=now(),
                )
            )

    n = await _upsert(
        session, CandidateStatus, rows, "id",
        ["title", "group_code", "group_title", "sort_order", "background_color",
         "text_color", "color_id", "legal_time_limit", "raw", "last_seen_at"],
    )
    await session.commit()
    return {"statuses": n}


async def sweep_profiles(client: ApiClient, session: AsyncSession, *, on_progress=None) -> dict[str, int]:
    """Re-page the whole profile list.

    This is the backbone of incremental refresh: the list rows carry the profile fields AND
    `vacancies[{id,title,candidateId}]`, so brand-new applications are discovered here without
    touching the expensive per-candidate endpoints. Anything new lands with
    details_synced_at / activity_synced_at NULL and is picked up by the deep phases.
    """
    lane = client.lanes[0]
    per = settings.profiles_per_page
    page, total, seen_profiles, seen_candidates = 1, None, 0, 0

    while total is None or (page - 1) * per < total:
        j = await client.request(
            lane, "/v2/applicant_profiles/list",
            method="POST", json_api=True,
            json_body={"data": {"attributes": {"page": page, "itemsPerPage": per}}},
        )
        if isinstance(j, Sentinel) or not j.get("data"):
            log.warning("profile sweep stopped at page %s (%r)", page, j)
            break
        total = (j.get("meta") or {}).get("totalItems", total or 0)

        prows, crows = [], []
        for p in j["data"]:
            a = p.get("attributes", {})
            work = a.get("currentWork") or {}
            prows.append(
                dict(
                    id=as_int(a["_id"]), first_name=a.get("firstName"), last_name=a.get("lastName"),
                    middle_name=a.get("middleName"), phone=a.get("phone"), email=a.get("email"),
                    city=a.get("city"), cur_position=work.get("position"),
                    cur_company=work.get("company"), experience=work.get("experience"),
                    # Whole JSON:API node, matching what the legacy exporter stored,
                    # so raw->'attributes'->>... works the same for old and new rows.
                    raw=p, last_seen_at=now(),
                )
            )
            for v in a.get("vacancies") or []:
                if as_int(v.get("candidateId")) is not None:
                    crows.append(
                        dict(
                            candidate_id=as_int(v["candidateId"]), profile_id=as_int(a["_id"]),
                            vacancy_id=as_int(v.get("id")), vacancy_title=v.get("title"),
                            last_seen_at=now(),
                        )
                    )

        seen_profiles += await _upsert(
            session, Profile, prows, "id",
            ["first_name", "last_name", "middle_name", "phone", "email", "city",
             "cur_position", "cur_company", "experience", "raw", "last_seen_at"],
        )
        # Only identity fields are refreshed here; status/recruiters come from candmeta.
        seen_candidates += await _upsert(
            session, Candidate, crows, "candidate_id",
            ["profile_id", "vacancy_id", "vacancy_title", "last_seen_at"],
        )
        await session.commit()

        if on_progress and page % 50 == 0:
            await on_progress(page * per, total)
        page += 1

    return {"profiles": seen_profiles, "candidates": seen_candidates}


# ── deep per-record phases ──────────────────────────────────────────────────
async def sync_candmeta(client: ApiClient, session: AsyncSession, profile_ids: list[int], *, on_progress=None):
    """Per-profile application list: status, recruiters, managers."""
    stats = {"processed": 0, "deferred": 0}
    # All lane workers share one AsyncSession, which wraps a single connection and is not
    # safe for concurrent use — two workers writing at once abort the transaction and wedge
    # the phase. Every database touch below goes through this lock.
    db = asyncio.Lock()

    async def worker(lane: Lane, pid: int) -> None:
        j = await client.request(
            lane, f"/v2/applicant_profiles/{pid}/candidates_list?page=1&itemsPerPage=50"
        )
        if j is GAVE_UP:
            stats["deferred"] += 1
            return
        if isinstance(j, Sentinel):
            stats["processed"] += 1
            return
        rows = []
        for c in j.get("data", []):
            a = c.get("attributes", {})
            if as_int(a.get("candidateId")) is None:
                continue
            rows.append(
                dict(
                    candidate_id=as_int(a["candidateId"]), profile_id=pid,
                    vacancy_id=as_int(a.get("vacancyId")),
                    vacancy_title=a.get("vacancyTitle"),
                    status_id=as_int(a.get("candidateStatusId")),
                    status_title=a.get("candidateStatusTitle"),
                    created_at=parse_dt(a.get("candidateCreatedAt")),
                    recruiters=a.get("recruiters") or [], managers=a.get("managers") or [],
                    last_seen_at=now(),
                )
            )
        async with db:
            await _upsert(
                session, Candidate, rows, "candidate_id",
                ["profile_id", "vacancy_id", "vacancy_title", "status_id", "status_title",
                 "created_at", "recruiters", "managers", "last_seen_at"],
            )
        stats["processed"] += 1

    await client.shard(profile_ids, worker, on_progress=on_progress)
    await session.commit()
    return stats


async def sync_details(client: ApiClient, session: AsyncSession, profile_ids: list[int], *, on_progress=None):
    """Resume + contacts per profile.

    A profile is only checkpointed when nothing hard-failed. FORBIDDEN counts as answered:
    those records are permanently withheld and would otherwise be retried forever.
    """
    stats = {"processed": 0, "deferred": 0, "resumes": 0, "contacts": 0, "work_history": 0}
    db = asyncio.Lock()  # see sync_candmeta: one session, many workers

    async def worker(lane: Lane, pid: int) -> None:
        hard_fail = False

        j = await client.request(lane, f"/v2/applicant_profiles/{pid}/resumes/last?include=source")
        if j is GAVE_UP:
            hard_fail = True
        elif not isinstance(j, Sentinel) and j.get("data"):
            included = j.get("included") or []
            source = included[0].get("attributes", {}).get("alias") if included else None
            body = (j["data"].get("attributes") or {}).get("body")
            async with db:
                await _upsert(
                    session, Resume,
                    [dict(profile_id=pid, body_html=body, source=source, raw=j, updated_at=now())],
                    "profile_id", ["body_html", "source", "raw", "updated_at"],
                )
                # Derive the per-job history straight away: the ATS only exposes the current
                # job, so this is the only place the full history exists. Replaced wholesale
                # so a re-fetched resume never leaves stale positions behind.
                await session.execute(
                    delete(WorkHistory).where(WorkHistory.profile_id == pid)
                )
                jobs = parse_work_history(body)
                if jobs:
                    session.add_all([
                        WorkHistory(
                            profile_id=pid, ord=job.order, company=job.company,
                            position=job.position, started_at=job.started_at,
                            finished_at=job.finished_at, is_current=job.is_current,
                            duration_text=job.duration_text, description=job.description,
                        )
                        for job in jobs
                    ])
                    stats["work_history"] = stats.get("work_history", 0) + len(jobs)
            stats["resumes"] += 1

        cid = await session.scalar(
            select(Candidate.candidate_id).where(Candidate.profile_id == pid).limit(1)
        )
        if cid:
            c = await client.request(lane, f"/v2/candidates/{cid}")
            if c is GAVE_UP:
                hard_fail = True
            elif not isinstance(c, Sentinel):
                refs = (((c.get("data") or {}).get("relationships") or {}).get("profileContacts") or {}).get("data") or []
                rows = []
                for ref in refs:
                    contact_id = int(str(ref["id"]).rsplit("/", 1)[-1])
                    pc = await client.request(lane, f"/v2/profile_contacts/{contact_id}")
                    if pc is GAVE_UP:
                        hard_fail = True
                        continue
                    if isinstance(pc, Sentinel) or not pc.get("data"):
                        continue
                    a = pc["data"].get("attributes", {})
                    rows.append(
                        dict(
                            id=as_int(a["_id"]), candidate_id=cid, profile_id=pid, type=a.get("type"),
                            value=a.get("value"), is_main=bool(a.get("isMain")),
                        )
                    )
                if rows:
                    async with db:
                        await _upsert(
                            session, Contact, rows, "id",
                            ["candidate_id", "profile_id", "type", "value", "is_main"],
                        )
                    stats["contacts"] += len(rows)

        if hard_fail:
            stats["deferred"] += 1
            return
        async with db:
            await session.execute(
                update(Profile).where(Profile.id == pid).values(details_synced_at=now())
            )
        stats["processed"] += 1

    await client.shard(profile_ids, worker, on_progress=on_progress)
    await session.commit()
    return stats


async def sync_activity(client: ApiClient, session: AsyncSession, candidate_ids: list[int], *, on_progress=None):
    """Logs + comments per candidate — the heavy phase."""
    stats = {"processed": 0, "deferred": 0, "logs": 0, "comments": 0}
    db = asyncio.Lock()  # see sync_candmeta: one session, many workers

    async def worker(lane: Lane, cid: int) -> None:
        hard_fail = False

        j = await client.request(lane, f"/v2/candidates/{cid}/logs")
        if j is GAVE_UP:
            hard_fail = True
        elif not isinstance(j, Sentinel) and j.get("data"):
            rows = [
                dict(
                    id=as_int(l["attributes"]["id"]), candidate_id=cid,
                    date_time_at=parse_dt(l["attributes"].get("dateTimeAt")),
                    message=l["attributes"].get("message"),
                    user_full_name=l["attributes"].get("userFullName"),
                )
                for l in j["data"] if as_int(l.get("attributes", {}).get("id")) is not None
            ]
            if rows:
                async with db:
                    await _upsert(session, LogEntry, rows, "id",
                                  ["candidate_id", "date_time_at", "message", "user_full_name"])
                stats["logs"] += len(rows)

        j = await client.request(lane, f"/v2/candidates/{cid}/comments?include=user")
        if j is GAVE_UP:
            hard_fail = True
        elif not isinstance(j, Sentinel) and j.get("data"):
            names = {
                inc["id"]: " ".join(
                    x for x in [inc["attributes"].get("firstName"), inc["attributes"].get("lastName")] if x
                )
                for inc in (j.get("included") or []) if inc.get("type") == "User"
            }
            rows = []
            for c in j["data"]:
                a = c.get("attributes", {})
                uref = ((c.get("relationships") or {}).get("user") or {}).get("data")
                uid = int(str(uref["id"]).rsplit("/", 1)[-1]) if uref else None
                rows.append(
                    dict(
                        id=as_int(a["_id"]), candidate_id=cid, comment=a.get("comment"),
                        created_at=parse_dt(a.get("createdAt")), changed_at=parse_dt(a.get("changedAt")),
                        user_id=uid, user_full_name=names.get(uref["id"]) if uref else None, raw=c,
                    )
                )
            if rows:
                async with db:
                    await _upsert(session, Comment, rows, "id",
                                  ["candidate_id", "comment", "created_at", "changed_at",
                                   "user_id", "user_full_name", "raw"])
                stats["comments"] += len(rows)

        if hard_fail:
            stats["deferred"] += 1
            return
        async with db:
            await session.execute(
                update(Candidate).where(Candidate.candidate_id == cid).values(activity_synced_at=now())
            )
        stats["processed"] += 1

    await client.shard(candidate_ids, worker, on_progress=on_progress)
    await session.commit()
    return stats


# ── queue helpers ───────────────────────────────────────────────────────────
async def pending_detail_profiles(session: AsyncSession, limit: int | None = None) -> list[int]:
    q = select(Profile.id).where(Profile.details_synced_at.is_(None)).order_by(Profile.id)
    if limit:
        q = q.limit(limit)
    return list((await session.scalars(q)).all())


async def pending_activity_candidates(session: AsyncSession, limit: int | None = None) -> list[int]:
    q = (
        select(Candidate.candidate_id)
        .where(Candidate.activity_synced_at.is_(None))
        .order_by(Candidate.candidate_id)
    )
    if limit:
        q = q.limit(limit)
    return list((await session.scalars(q)).all())


async def stale_activity_candidates(session: AsyncSession, limit: int) -> list[int]:
    """Oldest-verified candidates, for the rolling re-check that catches edits."""
    cutoff = now() - timedelta(days=settings.recheck_after_days)
    q = (
        select(Candidate.candidate_id)
        .where(Candidate.activity_synced_at.is_not(None), Candidate.activity_synced_at < cutoff)
        .order_by(Candidate.activity_synced_at)
        .limit(limit)
    )
    return list((await session.scalars(q)).all())


async def queue_sizes(session: AsyncSession) -> dict[str, int]:
    cutoff = now() - timedelta(days=settings.recheck_after_days)
    return {
        "details_pending": await session.scalar(
            select(func.count()).select_from(Profile).where(Profile.details_synced_at.is_(None))
        ) or 0,
        "activity_pending": await session.scalar(
            select(func.count()).select_from(Candidate).where(Candidate.activity_synced_at.is_(None))
        ) or 0,
        "recheck_due": await session.scalar(
            select(func.count()).select_from(Candidate).where(
                Candidate.activity_synced_at.is_not(None), Candidate.activity_synced_at < cutoff
            )
        ) or 0,
    }
