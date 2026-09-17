"""Turns a SyncRun row into an actual crawl and keeps its progress fresh."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.security import secret_box
from app.models import AtsAccount, SyncKind, SyncRun, SyncStatus
from app.scraper import media as media_mod
from app.scraper import phases
from app.scraper.client import ApiClient, Lane

log = logging.getLogger("scraper.runner")


async def build_lanes(session: AsyncSession) -> list[Lane]:
    box = secret_box()
    accounts = list(
        (await session.scalars(select(AtsAccount).where(AtsAccount.is_active).order_by(AtsAccount.id))).all()
    )
    lanes: list[Lane] = []
    for i, acc in enumerate(accounts):
        lanes.append(
            Lane(
                index=i,
                account_id=acc.id,
                email=acc.email,
                password=box.decrypt(acc.password_enc) or "",
                proxy=box.decrypt(acc.proxy_enc),
            )
        )
    return lanes


async def _flush_lane_stats(session: AsyncSession, lanes: list[Lane]) -> None:
    for lane in lanes:
        await session.execute(
            update(AtsAccount)
            .where(AtsAccount.id == lane.account_id)
            .values(
                requests_ok=AtsAccount.requests_ok + lane.requests_ok,
                requests_failed=AtsAccount.requests_failed + lane.requests_failed,
                last_error=lane.last_error,
                last_ok_at=datetime.now(UTC) if lane.requests_ok else AtsAccount.last_ok_at,
            )
        )
        lane.requests_ok = 0
        lane.requests_failed = 0
    await session.commit()


async def run_sync(session: AsyncSession, run: SyncRun) -> None:
    # The media phase only touches the public CDN, so it must not be blocked on ATS accounts.
    needs_api = run.kind is not SyncKind.media

    lanes = await build_lanes(session)
    if needs_api and not lanes:
        run.status = SyncStatus.failed
        run.error = "Нет активных аккаунтов ATS — добавьте хотя бы один в админке."
        run.finished_at = datetime.now(UTC)
        await session.commit()
        return

    client = ApiClient(lanes) if lanes else None
    run.status = SyncStatus.running
    run.started_at = datetime.now(UTC)
    await session.commit()

    stats: dict[str, int] = {}

    async def progress(done: int, total: int | None = None) -> None:
        run.processed = done
        if total:
            run.total = total
        await session.commit()

    try:
        kind = run.kind

        if kind in (SyncKind.sweep, SyncKind.full):
            stats |= await phases.sweep_users(client, session)
            stats |= await phases.sweep_vacancies(client, session)
            stats |= await phases.sweep_statuses(client, session)
            stats |= await phases.sweep_profiles(client, session, on_progress=progress)
            # Register newly-seen photos/resume files so the scheduler can queue the
            # download phase; without this the media queue would stay empty forever.
            stats |= await media_mod.discover(session)

        if kind in (SyncKind.details, SyncKind.full):
            ids = await phases.pending_detail_profiles(session)
            run.total = len(ids)
            await session.commit()
            stats |= {f"details_{k}": v for k, v in
                      (await phases.sync_details(client, session, ids, on_progress=progress)).items()}

        if kind in (SyncKind.activity, SyncKind.full):
            ids = await phases.pending_activity_candidates(session)
            run.total = len(ids)
            await session.commit()
            stats |= {f"activity_{k}": v for k, v in
                      (await phases.sync_activity(client, session, ids, on_progress=progress)).items()}

        if kind in (SyncKind.media, SyncKind.full):
            stats |= await media_mod.discover(session)
            items = await media_mod.pending(session)
            run.total = len(items)
            await session.commit()
            stats |= {f"media_{k}": v for k, v in
                      (await media_mod.download_all(session, items, on_progress=progress)).items()}

        if kind is SyncKind.rolling:
            ids = await phases.stale_activity_candidates(session, settings.rolling_batch_size)
            run.total = len(ids)
            await session.commit()
            if ids:
                stats |= {f"rolling_{k}": v for k, v in
                          (await phases.sync_activity(client, session, ids, on_progress=progress)).items()}

        run.status = SyncStatus.done
        run.deferred = sum(v for k, v in stats.items() if k.endswith("deferred"))
        run.stats = stats
    except Exception as exc:  # noqa: BLE001 - surfaced on the run row for the UI
        log.exception("sync run %s failed", run.id)
        run.status = SyncStatus.failed
        run.error = f"{type(exc).__name__}: {exc}"
        run.stats = stats
    finally:
        run.finished_at = datetime.now(UTC)
        await session.commit()
        if lanes:
            await _flush_lane_stats(session, lanes)
        if client is not None:
            await client.aclose()
