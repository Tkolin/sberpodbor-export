from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select

from app.api.routes import accounts, auth, data, exports, media, stats, sync, users
from app.config import settings
from app.core.security import hash_password
from app.db import SessionLocal
from app.models import AdminUser, Role

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s"
)
log = logging.getLogger("api")


async def bootstrap_admin() -> None:
    """Create the first administrator, once, if the table is empty."""
    if not settings.bootstrap_admin_password:
        return
    async with SessionLocal() as session:
        existing = await session.scalar(select(func.count()).select_from(AdminUser))
        if existing:
            return
        session.add(
            AdminUser(
                email=settings.bootstrap_admin_email.lower(),
                password_hash=hash_password(settings.bootstrap_admin_password),
                full_name="Bootstrap admin",
                role=Role.admin,
            )
        )
        await session.commit()
        log.warning(
            "Created bootstrap admin %s — change the password and clear "
            "SP_BOOTSTRAP_ADMIN_PASSWORD from the environment.",
            settings.bootstrap_admin_email,
        )


@asynccontextmanager
async def lifespan(_: FastAPI):
    await bootstrap_admin()
    yield


app = FastAPI(
    title="Сбер Подбор Export",
    version="2.0.0",
    summary="Admin API over the exported Сбер Подбор dataset",
    lifespan=lifespan,
)

if settings.cors_origin_list:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(accounts.router)
app.include_router(sync.router)
app.include_router(data.router)
app.include_router(exports.router)
app.include_router(media.router)
app.include_router(stats.router)


@app.get("/health", tags=["meta"])
async def health() -> dict[str, str]:
    return {"status": "ok"}
