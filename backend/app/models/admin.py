"""Service-side tables: who may log in, which ATS accounts we crawl with, what happened."""

import enum
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Role(str, enum.Enum):
    admin = "admin"      # full control, including accounts and sync
    operator = "operator"  # may browse, export and trigger sync; cannot touch accounts or users
    viewer = "viewer"    # read-only


class AdminUser(Base):
    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str | None] = mapped_column(Text)
    role: Mapped[Role] = mapped_column(Enum(Role, name="admin_role"), default=Role.viewer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Bumped on password change / forced logout; older tokens stop validating.
    token_epoch: Mapped[int] = mapped_column(Integer, default=0)


class AtsAccount(Base):
    """A Сбер Подбор login the crawler uses as one 'lane'.

    Password and proxy URL are Fernet-encrypted; they are never returned by the API.
    One active token per ATS user is a hard server-side rule, so two lanes must never
    share an email.
    """

    __tablename__ = "ats_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    label: Mapped[str | None] = mapped_column(String(128))
    email: Mapped[str] = mapped_column(String(255), unique=True)
    password_enc: Mapped[str] = mapped_column(Text)
    proxy_enc: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_ok_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    # Rolling counters, reset when the account is edited.
    requests_ok: Mapped[int] = mapped_column(BigInteger, default=0)
    requests_failed: Mapped[int] = mapped_column(BigInteger, default=0)


class SyncKind(str, enum.Enum):
    sweep = "sweep"        # cheap: re-page the profile/vacancy/user lists, find new work
    details = "details"    # resumes + contacts for profiles missing them
    activity = "activity"  # logs + comments per candidate
    full = "full"          # everything, in order
    rolling = "rolling"    # re-check the longest-unverified records
    media = "media"        # mirror photos and original resume files from the CDN


class SyncStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    done = "done"
    failed = "failed"
    cancelled = "cancelled"


class SyncRun(Base):
    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[SyncKind] = mapped_column(Enum(SyncKind, name="sync_kind"))
    status: Mapped[SyncStatus] = mapped_column(Enum(SyncStatus, name="sync_status"), default=SyncStatus.queued)
    triggered_by: Mapped[str | None] = mapped_column(String(255))  # email or "scheduler"

    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    total: Mapped[int] = mapped_column(BigInteger, default=0)
    processed: Mapped[int] = mapped_column(BigInteger, default=0)
    created: Mapped[int] = mapped_column(BigInteger, default=0)
    updated: Mapped[int] = mapped_column(BigInteger, default=0)
    deferred: Mapped[int] = mapped_column(BigInteger, default=0)

    stats: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("ix_sync_runs_status", "status", "queued_at"),)


class ExportStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    done = "done"
    failed = "failed"


class ExportJob(Base):
    __tablename__ = "export_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[ExportStatus] = mapped_column(Enum(ExportStatus, name="export_status"), default=ExportStatus.queued)
    requested_by: Mapped[str | None] = mapped_column(String(255))
    filters: Mapped[dict | None] = mapped_column(JSONB)
    filename: Mapped[str | None] = mapped_column(Text)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    row_count: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id", ondelete="SET NULL"))
    actor_email: Mapped[str | None] = mapped_column(String(255))
    action: Mapped[str] = mapped_column(String(64))
    target: Mapped[str | None] = mapped_column(String(255))
    detail: Mapped[dict | None] = mapped_column(JSONB)
    ip: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_audit_created", "created_at"),)
