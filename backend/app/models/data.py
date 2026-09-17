"""Сбер Подбор dataset, mirrored from the ATS.

Column names follow the original SQLite exporter so the migration is a straight copy;
types are tightened (TEXT timestamps -> timestamptz, JSON text -> JSONB, 0/1 -> boolean)
and per-row sync bookkeeping is folded in as columns instead of separate done_* tables.
"""

import enum
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Date,
    Enum,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Profile(Base):
    __tablename__ = "profiles"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    first_name: Mapped[str | None] = mapped_column(Text)
    last_name: Mapped[str | None] = mapped_column(Text)
    middle_name: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(Text)
    cur_position: Mapped[str | None] = mapped_column(Text)
    cur_company: Mapped[str | None] = mapped_column(Text)
    experience: Mapped[str | None] = mapped_column(Text)
    raw: Mapped[dict | None] = mapped_column(JSONB)

    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # NULL = resume/contacts never fetched successfully; drives the details queue.
    details_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_profiles_city", "city"),
        Index("ix_profiles_details_synced", "details_synced_at"),
        Index("ix_profiles_last_name", "last_name"),
    )


class Vacancy(Base):
    __tablename__ = "vacancies"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    title: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(String(64))
    city: Mapped[str | None] = mapped_column(Text)
    persons_count: Mapped[int | None] = mapped_column(Integer)
    desired_closing_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw: Mapped[dict | None] = mapped_column(JSONB)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_vacancies_status", "status"),)


class AtsUser(Base):
    """Recruiters/managers inside Сбер Подбор — not admins of this service."""

    __tablename__ = "ats_users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    first_name: Mapped[str | None] = mapped_column(Text)
    last_name: Mapped[str | None] = mapped_column(Text)
    middle_name: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    role: Mapped[str | None] = mapped_column(String(64))
    position: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(String(32))
    raw: Mapped[dict | None] = mapped_column(JSONB)


class Candidate(Base):
    """One application: a profile attached to a vacancy."""

    __tablename__ = "candidates"

    candidate_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    profile_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    vacancy_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    vacancy_title: Mapped[str | None] = mapped_column(Text)
    status_id: Mapped[int | None] = mapped_column(Integer)
    status_title: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recruiters: Mapped[list | None] = mapped_column(JSONB)
    managers: Mapped[list | None] = mapped_column(JSONB)

    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # NULL = logs/comments never fetched; also reset when a rolling re-check is due.
    activity_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_candidates_status_title", "status_title"),
        Index("ix_candidates_created", "created_at"),
        Index("ix_candidates_activity_synced", "activity_synced_at"),
    )


class Resume(Base):
    __tablename__ = "resumes"

    profile_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("profiles.id", ondelete="CASCADE"), primary_key=True, autoincrement=False
    )
    body_html: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(String(64))
    raw: Mapped[dict | None] = mapped_column(JSONB)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_resumes_source", "source"),)


class Contact(Base):
    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    candidate_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    profile_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    type: Mapped[str | None] = mapped_column(String(64))
    value: Mapped[str | None] = mapped_column(Text)
    is_main: Mapped[bool | None] = mapped_column(Boolean)

    __table_args__ = (Index("ix_contacts_type", "type"),)


class LogEntry(Base):
    __tablename__ = "logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    candidate_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    date_time_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    message: Mapped[str | None] = mapped_column(Text)
    user_full_name: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("ix_logs_dt", "date_time_at"),)


class Comment(Base):
    __tablename__ = "comments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    candidate_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user_id: Mapped[int | None] = mapped_column(BigInteger)
    user_full_name: Mapped[str | None] = mapped_column(Text)
    raw: Mapped[dict | None] = mapped_column(JSONB)

    __table_args__ = (Index("ix_comments_created", "created_at"),)


class Meta(Base):
    """Free-form crawl checkpoints (page cursors, phase flags)."""

    __tablename__ = "meta"

    k: Mapped[str] = mapped_column(String(128), primary_key=True)
    v: Mapped[str | None] = mapped_column(Text)


class WorkHistory(Base):
    """One job from a resume's "Опыт работы" section.

    Derived data, not fetched: the ATS exposes only `currentWork` (a single last job), so the
    per-position history is parsed out of the resume HTML. Rows are replaced wholesale when a
    resume is re-parsed, which is why `ord` — the position within that resume — is part of the
    identity rather than a surrogate meaning.
    """

    __tablename__ = "work_history"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    profile_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("profiles.id", ondelete="CASCADE"), index=True
    )
    ord: Mapped[int] = mapped_column(Integer)

    company: Mapped[str | None] = mapped_column(Text)
    position: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[date | None] = mapped_column(Date)
    finished_at: Mapped[date | None] = mapped_column(Date)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False)
    duration_text: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)

    parsed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("profile_id", "ord", name="uq_work_history_profile_ord"),
        Index("ix_work_history_company", "company"),
        Index("ix_work_history_started", "started_at"),
    )


class MediaKind(str, enum.Enum):
    photo = "photo"                # candidate avatar
    resume_file = "resume_file"    # the original pdf/doc the recruiter uploaded


class MediaStatus(str, enum.Enum):
    pending = "pending"
    done = "done"
    failed = "failed"
    gone = "gone"    # 404 upstream — the record exists but the file no longer does


class MediaFile(Base):
    """A binary the ATS keeps on media.sberpodbor.ru, mirrored to local disk.

    The CDN is a different host from the API: it needs no token and does not share the
    anti-abuse throttling, so downloads run far more concurrently than the crawl and never
    put a lane's session at risk.

    `rel_path` mirrors the upstream path ('2020/12/16/d89c6b355e.jpg'), which is already
    unique and date-sharded, so directories stay a sane size without inventing a scheme.
    """

    __tablename__ = "media_files"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    profile_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("profiles.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[MediaKind] = mapped_column(Enum(MediaKind, name="media_kind"))

    source_url: Mapped[str] = mapped_column(Text)
    rel_path: Mapped[str] = mapped_column(Text)
    content_type: Mapped[str | None] = mapped_column(String(128))
    file_size: Mapped[int | None] = mapped_column(BigInteger)
    # Lets a re-run skip bytes it already has and detect upstream replacement.
    sha256: Mapped[str | None] = mapped_column(String(64))

    status: Mapped[MediaStatus] = mapped_column(
        Enum(MediaStatus, name="media_status"), default=MediaStatus.pending
    )
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)

    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    downloaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("profile_id", "kind", name="uq_media_profile_kind"),
        Index("ix_media_status", "status"),
    )


class CandidateStatus(Base):
    """Статус (этап) подбора из настроек компании.

    Comes from POST /v2/status/struct — the endpoint behind app.sberpodbor.ru/settings/stages.
    It is not discoverable by guessing: singular path, POST, and the response is a dict of
    four groups rather than a JSON:API list.

    Worth collecting even though every application already carries status_id/status_title:
    only this gives the display order, the group (funnel stage vs rejection), and statuses
    that no candidate currently holds.
    """

    __tablename__ = "candidate_statuses"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    title: Mapped[str | None] = mapped_column(Text)
    # 'group' is reserved in SQL, hence the suffix.
    group_code: Mapped[str | None] = mapped_column(String(32))   # start | middle | positive | negative
    group_title: Mapped[str | None] = mapped_column(Text)        # Новый | В работе | Наняты | Отказ
    sort_order: Mapped[int | None] = mapped_column(Integer)
    background_color: Mapped[str | None] = mapped_column(String(16))
    text_color: Mapped[str | None] = mapped_column(String(16))
    color_id: Mapped[int | None] = mapped_column(Integer)
    legal_time_limit: Mapped[int | None] = mapped_column(Integer)
    raw: Mapped[dict | None] = mapped_column(JSONB)

    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_candidate_statuses_sort", "sort_order"),)
