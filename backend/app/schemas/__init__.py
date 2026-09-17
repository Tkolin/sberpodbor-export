from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models import Role


# ── auth ────────────────────────────────────────────────────────────────────
class LoginIn(BaseModel):
    # Plain str, not EmailStr: a malformed address here is simply a failed login, and
    # answering 422 instead of 401 both leaks validation behaviour and rejects perfectly
    # valid internal addresses like admin@intranet.
    email: str
    password: str


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class RefreshIn(BaseModel):
    refresh_token: str


class MeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: str
    full_name: str | None
    role: Role
    is_active: bool
    last_login_at: datetime | None


class PasswordChangeIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=10)


# ── admin users ─────────────────────────────────────────────────────────────
class AdminUserIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10)
    full_name: str | None = None
    role: Role = Role.viewer


class AdminUserPatch(BaseModel):
    full_name: str | None = None
    role: Role | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=10)


# ── ATS accounts ────────────────────────────────────────────────────────────
class AtsAccountIn(BaseModel):
    label: str | None = None
    email: EmailStr
    password: str
    proxy: str | None = None
    is_active: bool = True


class AtsAccountPatch(BaseModel):
    label: str | None = None
    password: str | None = None
    proxy: str | None = None
    is_active: bool | None = None


class AtsAccountOut(BaseModel):
    """Never carries the password. `proxy` is masked to host only."""

    id: int
    label: str | None
    email: str
    proxy: str | None
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None
    last_ok_at: datetime | None
    last_error: str | None
    requests_ok: int
    requests_failed: int


class AccountCheckOut(BaseModel):
    ok: bool
    detail: str
    checked_at: datetime


# ── sync ────────────────────────────────────────────────────────────────────
class SyncRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    kind: str
    status: str
    triggered_by: str | None
    queued_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    total: int
    processed: int
    deferred: int
    stats: dict[str, Any] | None
    error: str | None


class SyncTriggerIn(BaseModel):
    kind: str = Field(pattern="^(sweep|details|activity|full|rolling|media)$")


# ── data browsing ───────────────────────────────────────────────────────────
class Page(BaseModel):
    items: list[Any]
    total: int
    page: int
    per_page: int


class ProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    first_name: str | None
    last_name: str | None
    middle_name: str | None
    phone: str | None
    email: str | None
    city: str | None
    cur_position: str | None
    cur_company: str | None
    experience: str | None
    details_synced_at: datetime | None


class ExportJobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    status: str
    requested_by: str | None
    filters: dict[str, Any] | None
    filename: str | None
    size_bytes: int | None
    row_count: int | None
    created_at: datetime
    finished_at: datetime | None
    error: str | None
