"""Сбер Подбор account (lane) management."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import select

from app.api.deps import RequireAdmin, SessionDep, audit
from app.config import settings
from app.core.security import mask_proxy, secret_box
from app.models import AtsAccount, SyncRun, SyncStatus
from app.schemas import AccountCheckOut, AtsAccountIn, AtsAccountOut, AtsAccountPatch

router = APIRouter(prefix="/accounts", tags=["accounts"])


def _out(acc: AtsAccount) -> AtsAccountOut:
    return AtsAccountOut(
        id=acc.id,
        label=acc.label,
        email=acc.email,
        proxy=mask_proxy(secret_box().decrypt(acc.proxy_enc)),
        is_active=acc.is_active,
        created_at=acc.created_at,
        last_login_at=acc.last_login_at,
        last_ok_at=acc.last_ok_at,
        last_error=acc.last_error,
        requests_ok=acc.requests_ok,
        requests_failed=acc.requests_failed,
    )


@router.get("", response_model=list[AtsAccountOut])
async def list_accounts(_: RequireAdmin, session: SessionDep) -> list[AtsAccountOut]:
    accounts = (await session.scalars(select(AtsAccount).order_by(AtsAccount.id))).all()
    return [_out(a) for a in accounts]


@router.post("", response_model=AtsAccountOut, status_code=status.HTTP_201_CREATED)
async def create_account(
    payload: AtsAccountIn, admin: RequireAdmin, request: Request, session: SessionDep
) -> AtsAccountOut:
    email = payload.email.lower()
    if await session.scalar(select(AtsAccount).where(AtsAccount.email == email)):
        # Two lanes on one ATS login would fight over the single allowed token.
        raise HTTPException(status.HTTP_409_CONFLICT, "This ATS account is already configured")

    box = secret_box()
    acc = AtsAccount(
        label=payload.label,
        email=email,
        password_enc=box.encrypt(payload.password),
        proxy_enc=box.encrypt(payload.proxy),
        is_active=payload.is_active,
    )
    session.add(acc)
    await session.commit()
    await session.refresh(acc)
    await audit(session, request, admin, "account.created", email, {"proxy": mask_proxy(payload.proxy)})
    return _out(acc)


@router.patch("/{account_id}", response_model=AtsAccountOut)
async def update_account(
    account_id: int,
    payload: AtsAccountPatch,
    admin: RequireAdmin,
    request: Request,
    session: SessionDep,
) -> AtsAccountOut:
    acc = await session.get(AtsAccount, account_id)
    if acc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such account")

    box = secret_box()
    changed: list[str] = []
    if payload.label is not None:
        acc.label = payload.label
        changed.append("label")
    if payload.password:
        acc.password_enc = box.encrypt(payload.password)
        acc.requests_ok = acc.requests_failed = 0
        changed.append("password")
    if payload.proxy is not None:
        acc.proxy_enc = box.encrypt(payload.proxy or None)
        acc.requests_ok = acc.requests_failed = 0
        changed.append("proxy")
    if payload.is_active is not None:
        acc.is_active = payload.is_active
        changed.append("is_active")

    await session.commit()
    await session.refresh(acc)
    await audit(session, request, admin, "account.updated", acc.email, {"fields": changed})
    return _out(acc)


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(
    account_id: int, admin: RequireAdmin, request: Request, session: SessionDep
) -> None:
    acc = await session.get(AtsAccount, account_id)
    if acc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such account")
    email = acc.email
    await session.delete(acc)
    await session.commit()
    await audit(session, request, admin, "account.deleted", email)


@router.post("/{account_id}/check", response_model=AccountCheckOut)
async def check_account(
    account_id: int,
    admin: RequireAdmin,
    request: Request,
    session: SessionDep,
    force: bool = Query(
        False,
        description="Log in even though a sync is running. This WILL break the running crawl.",
    ),
) -> AccountCheckOut:
    """Verify the credentials by performing a real login.

    Сбер Подбор allows exactly one active token per user, so this call invalidates whatever
    token the crawler currently holds for this account. Doing it mid-crawl throws every
    in-flight worker into 401 and can trip the anti-abuse block, so it is refused while a
    sync is running unless explicitly forced.
    """
    acc = await session.get(AtsAccount, account_id)
    if acc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such account")

    if not force:
        busy = await session.scalar(
            select(SyncRun.id).where(SyncRun.status == SyncStatus.running).limit(1)
        )
        if busy:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "A sync is running. Checking an account logs in again and invalidates the "
                "crawler's token for it — stop the sync first, or repeat with force=true.",
            )

    box = secret_box()
    proxy = box.decrypt(acc.proxy_enc)
    try:
        async with httpx.AsyncClient(
            base_url=settings.api_base, proxy=proxy, timeout=httpx.Timeout(settings.req_timeout_s)
        ) as client:
            r = await client.post(
                "/v2/auth/login",
                headers={"content-type": "application/vnd.api+json"},
                json={"data": {"attributes": {"login": acc.email, "password": box.decrypt(acc.password_enc)}}},
            )
        ok = r.status_code in (200, 201)
        detail = "Login OK" if ok else f"HTTP {r.status_code}"
        if ok:
            acc.last_login_at = datetime.now(UTC)
            acc.last_ok_at = datetime.now(UTC)
            acc.last_error = None
        else:
            acc.last_error = detail
    except httpx.HTTPError as exc:
        ok, detail = False, f"{type(exc).__name__}: {exc}"
        acc.last_error = detail

    await session.commit()
    await audit(session, request, admin, "account.checked", acc.email, {"ok": ok, "forced": force})
    return AccountCheckOut(ok=ok, detail=detail, checked_at=datetime.now(UTC))
