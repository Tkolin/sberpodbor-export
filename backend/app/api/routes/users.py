"""Admin user management (the people who log into this panel)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import func, select

from app.api.deps import RequireAdmin, SessionDep, audit
from app.core.security import hash_password
from app.models import AdminUser, Role
from app.schemas import AdminUserIn, AdminUserPatch, MeOut

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=list[MeOut])
async def list_users(_: RequireAdmin, session: SessionDep) -> list[AdminUser]:
    return list((await session.scalars(select(AdminUser).order_by(AdminUser.id))).all())


@router.post("", response_model=MeOut, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: AdminUserIn, admin: RequireAdmin, request: Request, session: SessionDep
) -> AdminUser:
    email = payload.email.lower()
    if await session.scalar(select(AdminUser).where(AdminUser.email == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "That email already has an account")

    user = AdminUser(
        email=email,
        password_hash=hash_password(payload.password),
        full_name=payload.full_name,
        role=payload.role,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    await audit(session, request, admin, "user.created", email, {"role": payload.role.value})
    return user


@router.patch("/{user_id}", response_model=MeOut)
async def update_user(
    user_id: int, payload: AdminUserPatch, admin: RequireAdmin, request: Request, session: SessionDep
) -> AdminUser:
    user = await session.get(AdminUser, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such user")

    changed: list[str] = []
    if payload.full_name is not None:
        user.full_name = payload.full_name
        changed.append("full_name")
    if payload.role is not None:
        await _guard_last_admin(session, user, new_role=payload.role)
        user.role = payload.role
        changed.append("role")
    if payload.is_active is not None:
        if not payload.is_active:
            await _guard_last_admin(session, user, deactivating=True)
        user.is_active = payload.is_active
        changed.append("is_active")
    if payload.password:
        user.password_hash = hash_password(payload.password)
        user.token_epoch += 1  # kicks that user's existing sessions
        changed.append("password")

    await session.commit()
    await session.refresh(user)
    await audit(session, request, admin, "user.updated", user.email, {"fields": changed})
    return user


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: int, admin: RequireAdmin, request: Request, session: SessionDep
) -> None:
    user = await session.get(AdminUser, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such user")
    if user.id == admin.id:
        raise HTTPException(status.HTTP_409_CONFLICT, "You cannot delete your own account")
    await _guard_last_admin(session, user, deactivating=True)

    email = user.email
    await session.delete(user)
    await session.commit()
    await audit(session, request, admin, "user.deleted", email)


async def _guard_last_admin(
    session, user: AdminUser, *, new_role: Role | None = None, deactivating: bool = False
) -> None:
    """Refuse the change that would leave nobody able to administer the panel."""
    if user.role is not Role.admin:
        return
    if new_role is Role.admin:
        return
    remaining = await session.scalar(
        select(func.count())
        .select_from(AdminUser)
        .where(AdminUser.role == Role.admin, AdminUser.is_active, AdminUser.id != user.id)
    )
    if not remaining:
        action = "deactivate" if deactivating else "demote"
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Cannot {action} the last active administrator — promote someone else first",
        )
