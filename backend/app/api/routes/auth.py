from __future__ import annotations

from datetime import UTC, datetime

import jwt
from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select

from app.api.deps import CurrentUser, SessionDep, audit
from app.config import settings
from app.core.security import create_token, decode_token, hash_password, verify_password
from app.models import AdminUser
from app.schemas import LoginIn, MeOut, PasswordChangeIn, RefreshIn, TokenOut

router = APIRouter(tags=["auth"])


def _tokens(user: AdminUser) -> TokenOut:
    return TokenOut(
        access_token=create_token(sub=str(user.id), role=user.role.value, epoch=user.token_epoch),
        refresh_token=create_token(
            sub=str(user.id), role=user.role.value, epoch=user.token_epoch, kind="refresh"
        ),
        expires_in=settings.access_token_ttl_min * 60,
    )


@router.post("/login", response_model=TokenOut)
async def login(payload: LoginIn, request: Request, session: SessionDep) -> TokenOut:
    user = await session.scalar(select(AdminUser).where(AdminUser.email == payload.email.lower()))
    # Same error and roughly the same work either way — do not leak which emails exist.
    if user is None or not verify_password(payload.password, user.password_hash):
        await audit(session, request, None, "login.failed", payload.email)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account disabled")

    user.last_login_at = datetime.now(UTC)
    await session.commit()
    await audit(session, request, user, "login.ok")
    return _tokens(user)


@router.post("/refresh", response_model=TokenOut)
async def refresh(payload: RefreshIn, session: SessionDep) -> TokenOut:
    try:
        claims = decode_token(payload.refresh_token)
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token") from None
    if claims.get("kind") != "refresh":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong token type")

    user = await session.get(AdminUser, int(claims["sub"]))
    if user is None or not user.is_active or int(claims.get("epoch", -1)) != user.token_epoch:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session revoked")
    return _tokens(user)


@router.get("/me", response_model=MeOut)
async def me(user: CurrentUser) -> AdminUser:
    return user


@router.post("/me/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    payload: PasswordChangeIn, user: CurrentUser, request: Request, session: SessionDep
) -> None:
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is wrong")
    user.password_hash = hash_password(payload.new_password)
    user.token_epoch += 1  # every existing session, including this one, stops working
    await session.commit()
    await audit(session, request, user, "password.changed")
