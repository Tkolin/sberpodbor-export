"""Streaming XML export.

The dataset is far too large to build a tree in memory (266k profiles, resumes are HTML
blobs), so this yields bytes incrementally: profiles are walked in id-ordered pages and
each page's children are fetched in bulk to avoid N+1 queries.

Text is escaped rather than wrapped in CDATA — CDATA cannot contain ']]>' and resume HTML
is arbitrary, so escaping is the only form that is always correct. Characters that XML 1.0
forbids outright (most C0 controls) are dropped, since no encoding can represent them.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AtsUser, Candidate, Comment, Contact, LogEntry, MediaFile, MediaKind,
    MediaStatus, Profile, Resume, Vacancy,
)
from app.services.xmlchars import attr as _attr
from app.services.xmlchars import clean as _clean  # noqa: F401  (re-exported for tests)
from app.services.xmlchars import text as _text

NAMESPACE = "urn:sberpodbor:export:1.0"
SCHEMA_VERSION = "1.0"
PAGE = 500

def _person_list(tag: str, people: list[dict] | None) -> str:
    if not people:
        return ""
    out = [f"      <{tag}>"]
    for p in people:
        out.append(
            "        <person"
            + _attr("id", p.get("id"))
            + _attr("firstName", p.get("firstName"))
            + _attr("lastName", p.get("lastName"))
            + _attr("middleName", p.get("middleName"))
            + _attr("position", p.get("position"))
            + "/>"
        )
    out.append(f"      </{tag}>")
    return "\n".join(out) + "\n"


class ProfileFilters:
    """Whitelisted, indexable filters. Everything is optional."""

    def __init__(
        self,
        *,
        city: str | None = None,
        vacancy_id: int | None = None,
        status_title: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        with_resume: bool | None = None,
        limit: int | None = None,
    ) -> None:
        self.city = city
        self.vacancy_id = vacancy_id
        self.status_title = status_title
        self.created_from = created_from
        self.created_to = created_to
        self.with_resume = with_resume
        self.limit = limit

    def as_dict(self) -> dict[str, Any]:
        return {
            k: (v.isoformat() if isinstance(v, datetime) else v)
            for k, v in self.__dict__.items()
            if v is not None
        }

    def apply(self, stmt: Select) -> Select:
        if self.city:
            stmt = stmt.where(Profile.city == self.city)
        if self.with_resume is not None:
            sub = select(Resume.profile_id).where(Resume.profile_id == Profile.id)
            stmt = stmt.where(sub.exists() if self.with_resume else ~sub.exists())
        if self.vacancy_id or self.status_title or self.created_from or self.created_to:
            sub = select(Candidate.profile_id).where(Candidate.profile_id == Profile.id)
            if self.vacancy_id:
                sub = sub.where(Candidate.vacancy_id == self.vacancy_id)
            if self.status_title:
                sub = sub.where(Candidate.status_title == self.status_title)
            if self.created_from:
                sub = sub.where(Candidate.created_at >= self.created_from)
            if self.created_to:
                sub = sub.where(Candidate.created_at <= self.created_to)
            stmt = stmt.where(sub.exists())
        return stmt


async def stream_export(
    session: AsyncSession,
    filters: ProfileFilters | None = None,
    *,
    include_reference: bool = True,
) -> AsyncIterator[bytes]:
    filters = filters or ProfileFilters()

    def emit(chunk: str) -> bytes:
        return chunk.encode("utf-8")

    yield emit(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<sberpodborExport xmlns="{NAMESPACE}"'
        f' version="{SCHEMA_VERSION}"'
        f' generatedAt="{datetime.now(UTC).isoformat()}">\n'
    )

    if include_reference:
        yield emit("  <vacancies>\n")
        for v in (await session.scalars(select(Vacancy).order_by(Vacancy.id))).all():
            yield emit(
                "    <vacancy"
                + _attr("id", v.id)
                + _attr("status", v.status)
                + _attr("city", v.city)
                + _attr("personsCount", v.persons_count)
                + _attr("createdAt", v.created_at)
                + _attr("desiredClosingAt", v.desired_closing_at)
                + f">{_text(v.title)}</vacancy>\n"
            )
        yield emit("  </vacancies>\n  <users>\n")
        for u in (await session.scalars(select(AtsUser).order_by(AtsUser.id))).all():
            yield emit(
                "    <user"
                + _attr("id", u.id)
                + _attr("role", u.role)
                + _attr("status", u.status)
                + _attr("email", u.email)
                + _attr("position", u.position)
                + ">"
                + _text(" ".join(x for x in [u.last_name, u.first_name, u.middle_name] if x))
                + "</user>\n"
            )
        yield emit("  </users>\n")

    yield emit("  <profiles>\n")

    last_id = 0
    emitted = 0
    while True:
        stmt = filters.apply(
            select(Profile).where(Profile.id > last_id).order_by(Profile.id).limit(PAGE)
        )
        batch = list((await session.scalars(stmt)).all())
        if not batch:
            break
        ids = [p.id for p in batch]
        last_id = ids[-1]

        resumes = {
            r.profile_id: r
            for r in (await session.scalars(select(Resume).where(Resume.profile_id.in_(ids)))).all()
        }
        media: dict[int, dict[str, MediaFile]] = {}
        for m in (await session.scalars(
            select(MediaFile).where(
                MediaFile.profile_id.in_(ids), MediaFile.status == MediaStatus.done
            )
        )).all():
            media.setdefault(m.profile_id, {})[m.kind.value] = m

        contacts: dict[int, list[Contact]] = {}
        for c in (await session.scalars(select(Contact).where(Contact.profile_id.in_(ids)))).all():
            contacts.setdefault(c.profile_id, []).append(c)

        cands = list((await session.scalars(
            select(Candidate).where(Candidate.profile_id.in_(ids)).order_by(Candidate.candidate_id)
        )).all())
        by_profile: dict[int, list[Candidate]] = {}
        for c in cands:
            by_profile.setdefault(c.profile_id, []).append(c)
        cand_ids = [c.candidate_id for c in cands]

        logs: dict[int, list[LogEntry]] = {}
        comments: dict[int, list[Comment]] = {}
        if cand_ids:
            for l in (await session.scalars(
                select(LogEntry).where(LogEntry.candidate_id.in_(cand_ids)).order_by(LogEntry.date_time_at)
            )).all():
                logs.setdefault(l.candidate_id, []).append(l)
            for cm in (await session.scalars(
                select(Comment).where(Comment.candidate_id.in_(cand_ids)).order_by(Comment.created_at)
            )).all():
                comments.setdefault(cm.candidate_id, []).append(cm)

        for p in batch:
            if filters.limit and emitted >= filters.limit:
                break
            emitted += 1
            out = [
                "    <profile" + _attr("id", p.id) + ">",
                f"      <firstName>{_text(p.first_name)}</firstName>",
                f"      <lastName>{_text(p.last_name)}</lastName>",
                f"      <middleName>{_text(p.middle_name)}</middleName>",
                f"      <phone>{_text(p.phone)}</phone>",
                f"      <email>{_text(p.email)}</email>",
                f"      <city>{_text(p.city)}</city>",
                "      <currentWork"
                + _attr("position", p.cur_position)
                + _attr("company", p.cur_company)
                + _attr("experience", p.experience)
                + "/>",
            ]

            plist = contacts.get(p.id) or []
            if plist:
                out.append("      <contacts>")
                out += [
                    "        <contact"
                    + _attr("id", c.id)
                    + _attr("type", c.type)
                    + _attr("isMain", bool(c.is_main))
                    + f">{_text(c.value)}</contact>"
                    for c in plist
                ]
                out.append("      </contacts>")

            files = media.get(p.id) or {}
            if files:
                out.append("      <files>")
                photo = files.get("photo")
                if photo is not None:
                    out.append(
                        "        <photo"
                        + _attr("mediaId", photo.id)
                        + _attr("contentType", photo.content_type)
                        + _attr("bytes", photo.file_size)
                        + f">{_text(photo.source_url)}</photo>"
                    )
                doc = files.get("resume_file")
                if doc is not None:
                    out.append(
                        "        <resumeFile"
                        + _attr("mediaId", doc.id)
                        + _attr("contentType", doc.content_type)
                        + _attr("bytes", doc.file_size)
                        + f">{_text(doc.source_url)}</resumeFile>"
                    )
                out.append("      </files>")

            res = resumes.get(p.id)
            if res is not None and res.body_html:
                out.append(
                    "      <resume" + _attr("source", res.source) + ">"
                    + _text(res.body_html) + "</resume>"
                )

            apps = by_profile.get(p.id) or []
            if apps:
                out.append("      <applications>")
                for a in apps:
                    out.append(
                        "        <application"
                        + _attr("candidateId", a.candidate_id)
                        + _attr("vacancyId", a.vacancy_id)
                        + _attr("vacancyTitle", a.vacancy_title)
                        + _attr("statusId", a.status_id)
                        + _attr("statusTitle", a.status_title)
                        + _attr("createdAt", a.created_at)
                        + ">"
                    )
                    out.append(_person_list("recruiters", a.recruiters).rstrip("\n"))
                    out.append(_person_list("managers", a.managers).rstrip("\n"))
                    alogs = logs.get(a.candidate_id) or []
                    if alogs:
                        out.append("          <logs>")
                        out += [
                            "            <log"
                            + _attr("id", l.id)
                            + _attr("at", l.date_time_at)
                            + _attr("author", l.user_full_name)
                            + f">{_text(l.message)}</log>"
                            for l in alogs
                        ]
                        out.append("          </logs>")
                    acomments = comments.get(a.candidate_id) or []
                    if acomments:
                        out.append("          <comments>")
                        out += [
                            "            <comment"
                            + _attr("id", c.id)
                            + _attr("createdAt", c.created_at)
                            + _attr("changedAt", c.changed_at)
                            + _attr("authorId", c.user_id)
                            + _attr("author", c.user_full_name)
                            + f">{_text(c.comment)}</comment>"
                            for c in acomments
                        ]
                        out.append("          </comments>")
                    out.append("        </application>")
                out.append("      </applications>")

            out.append("    </profile>")
            yield emit("\n".join(x for x in out if x) + "\n")

        if filters.limit and emitted >= filters.limit:
            break

    yield emit("  </profiles>\n</sberpodborExport>\n")
