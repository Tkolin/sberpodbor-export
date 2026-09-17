"""Sync worker: a singleton loop that drains the run queue and keeps data fresh.

Only one worker may crawl at a time — the ATS allows a single active token per account,
so a second concurrent crawler would knock the first one out. That is enforced with a
Postgres advisory lock rather than by convention.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
from datetime import UTC, datetime

from sqlalchemy import func, select, text

from app.config import settings
from app.db import SessionLocal
from app.models import AtsAccount, MediaFile, MediaStatus, SyncKind, SyncRun, SyncStatus
from app.scraper import media as media_mod
from app.scraper import phases
from app.scraper.runner import run_sync

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger("worker")

LOCK_KEY = 0x5B3_0DB0  # arbitrary, stable
_stopping = asyncio.Event()


async def _claim_next_run(session) -> SyncRun | None:
    """Take the oldest queued run, skipping rows another worker already grabbed."""
    row = await session.scalar(
        select(SyncRun)
        .where(SyncRun.status == SyncStatus.queued)
        .order_by(SyncRun.queued_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    return row


async def _enqueue_if_idle(session, kind: SyncKind, reason: str) -> None:
    busy = await session.scalar(
        select(SyncRun.id).where(SyncRun.status.in_([SyncStatus.queued, SyncStatus.running])).limit(1)
    )
    if busy:
        return
    session.add(SyncRun(kind=kind, triggered_by=f"scheduler:{reason}"))
    await session.commit()
    log.info("queued %s run (%s)", kind.value, reason)


FAILURE_BACKOFF_S = 300


async def scheduler_tick() -> None:
    """Decide what, if anything, needs doing right now.

    Priority: finish what is missing (details, then activity), then sweep for new records
    on an interval, then spend leftover time re-checking the oldest data.
    """
    async with SessionLocal() as session:
        # Without an account there is nothing to crawl with, and queueing anyway would
        # spin failed runs every tick forever.
        has_account = await session.scalar(
            select(AtsAccount.id).where(AtsAccount.is_active).limit(1)
        )
        if not has_account:
            log.info("no active ATS accounts — nothing to schedule")
            return

        # After a failure, wait before trying the same thing again; otherwise a broken
        # proxy or a blocked account turns into a tight retry loop.
        last_failed = await session.scalar(
            select(SyncRun.finished_at)
            .where(SyncRun.status == SyncStatus.failed)
            .order_by(SyncRun.finished_at.desc())
            .limit(1)
        )
        if last_failed and (datetime.now(UTC) - last_failed).total_seconds() < FAILURE_BACKOFF_S:
            return

        sizes = await phases.queue_sizes(session)

        if sizes["details_pending"]:
            await _enqueue_if_idle(session, SyncKind.details, f"{sizes['details_pending']} profiles missing details")
            return
        if sizes["activity_pending"]:
            await _enqueue_if_idle(session, SyncKind.activity, f"{sizes['activity_pending']} candidates missing activity")
            return

        media_left = await session.scalar(
            select(func.count()).select_from(MediaFile).where(
                MediaFile.status.in_([MediaStatus.pending, MediaStatus.failed]),
                MediaFile.attempts < media_mod.MAX_ATTEMPTS,
            )
        )
        if media_left:
            await _enqueue_if_idle(session, SyncKind.media, f"{media_left} файлов не скачано")
            return

        last_sweep = await session.scalar(
            select(SyncRun.finished_at)
            .where(SyncRun.kind.in_([SyncKind.sweep, SyncKind.full]), SyncRun.status == SyncStatus.done)
            .order_by(SyncRun.finished_at.desc())
            .limit(1)
        )
        due = (
            last_sweep is None
            or (datetime.now(UTC) - last_sweep).total_seconds() >= settings.sweep_interval_min * 60
        )
        if due:
            await _enqueue_if_idle(session, SyncKind.sweep, "interval")
            return

        if settings.rolling_recheck_enabled and sizes["recheck_due"]:
            await _enqueue_if_idle(session, SyncKind.rolling, f"{sizes['recheck_due']} records past re-check age")


async def _reclaim_orphans() -> None:
    """Fail runs left in 'running' by a worker that died.

    Holding the advisory lock proves no other worker is executing anything, so a row still
    marked running is stale by definition. Left alone it deadlocks the whole service: both
    the scheduler and the manual trigger refuse to start while any run looks active, so a
    single container restart would stop all syncing forever.
    """
    async with SessionLocal() as session:
        stale = list((await session.scalars(
            select(SyncRun).where(SyncRun.status == SyncStatus.running)
        )).all())
        for run in stale:
            run.status = SyncStatus.failed
            run.error = "Прервано: воркер был перезапущен. Работа возобновится с места остановки."
            run.finished_at = datetime.now(UTC)
        if stale:
            await session.commit()
            log.warning("reclaimed %s orphaned run(s): %s",
                        len(stale), ", ".join(str(r.id) for r in stale))


async def worker_loop() -> None:
    while not _stopping.is_set():
        try:
            async with SessionLocal() as session:
                async with session.begin():
                    run = await _claim_next_run(session)
                    if run is not None:
                        run.status = SyncStatus.running
                        run.started_at = datetime.now(UTC)
                if run is not None:
                    log.info("starting run %s (%s)", run.id, run.kind.value)
                    await run_sync(session, run)
                    log.info("run %s finished: %s", run.id, run.status.value)
                    continue
            await scheduler_tick()
        except Exception:  # noqa: BLE001 - the loop must outlive any single failure
            log.exception("worker iteration failed")
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(_stopping.wait(), timeout=30)


async def main() -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, _stopping.set)

    # Hold the singleton lock for the life of the process on a dedicated connection.
    async with SessionLocal() as guard:
        got = await guard.scalar(text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_KEY})
        if not got:
            log.error("another sync worker already holds the lock — exiting")
            return
        log.info("sync worker started (lane concurrency=%s, interval=%sms)",
                 settings.lane_concurrency, settings.min_interval_ms)
        await _reclaim_orphans()
        try:
            await worker_loop()
        finally:
            await guard.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY})
            log.info("sync worker stopped")


if __name__ == "__main__":
    asyncio.run(main())
