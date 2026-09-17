from __future__ import annotations

from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_token
from app.db import get_session
from app.models import AdminUser, AuditLog, Role

bearer = HTTPBearer(auto_error=False)

SessionDep = Annotated[AsyncSession, Depends(get_session)]

_ROLE_RANK = {Role.viewer: 0, Role.operator: 1, Role.admin: 2}


async def current_user(
    request: Request,
    session: SessionDep,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)] = None,
) -> AdminUser:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    try:
        payload = decode_token(creds.credentials)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token expired") from None
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token") from None

    if payload.get("kind") != "access":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong token type")

    user = await session.get(AdminUser, int(payload["sub"]))
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User disabled")
    # Password changes and forced logouts bump the epoch, retiring older tokens.
    if int(payload.get("epoch", -1)) != user.token_epoch:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session revoked")

    request.state.actor = user
    return user


CurrentUser = Annotated[AdminUser, Depends(current_user)]


def require_role(minimum: Role):
    async def guard(user: CurrentUser) -> AdminUser:
        if _ROLE_RANK[user.role] < _ROLE_RANK[minimum]:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Requires role '{minimum.value}' or higher; you are '{user.role.value}'",
            )
        return user

    return guard


RequireOperator = Annotated[AdminUser, Depends(require_role(Role.operator))]
RequireAdmin = Annotated[AdminUser, Depends(require_role(Role.admin))]


async def audit(
    session: AsyncSession,
    request: Request,
    actor: AdminUser | None,
    action: str,
    target: str | None = None,
    detail: dict | None = None,
) -> None:
    """Append-only trail. Never records secrets — callers pass identifiers, not values."""
    session.add(
        AuditLog(
            actor_id=actor.id if actor else None,
            actor_email=actor.email if actor else None,
            action=action,
            target=target,
            detail=detail,
            ip=(request.client.host if request.client else None),
        )
    )
    await session.commit()
