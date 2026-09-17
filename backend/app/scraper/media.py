"""Mirror candidate photos and original resume files to local disk.

media.sberpodbor.ru is a plain CDN: no token, no anti-abuse throttling, and unrelated to the
one-session-per-account rule that governs the API crawl. So this runs at its own, much higher
concurrency and can never knock a lane's session out — which is why it is a separate phase
rather than part of the details crawl.

Downloads are written to a .part file and renamed on completion, so an interrupted run never
leaves a truncated file that looks finished.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import UTC, datetime
from typing import NamedTuple
from pathlib import Path

import httpx
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import MediaFile, MediaKind, MediaStatus, Profile, Resume

log = logging.getLogger("scraper.media")

MAX_ATTEMPTS = 3


def media_root() -> Path:
    return Path(settings.media_dir)


def local_path(kind: MediaKind, rel_path: str) -> Path:
    sub = "photos" if kind is MediaKind.photo else "resumes"
    # Anchor under the root: rel_path comes from upstream JSON and must not escape it.
    root = (media_root() / sub).resolve()
    target = (root / rel_path.lstrip("/")).resolve()
    if not target.is_relative_to(root):
        raise ValueError(f"suspicious media path: {rel_path!r}")
    return target


async def discover(session: AsyncSession) -> dict[str, int]:
    """Register every photo / resume file referenced in the stored API responses.

    Idempotent: rows already known are left alone, so this can run after every sweep to pick
    up newly-seen media without touching what is already downloaded.
    """
    photos = (
        await session.execute(
            select(
                Profile.id,
                Profile.raw["attributes"]["avatar"]["filePath"].astext,
                Profile.raw["attributes"]["avatar"]["fileLinks"]["original"].astext,
            ).where(Profile.raw["attributes"]["avatar"]["filePath"].astext.is_not(None))
        )
    ).all()

    files = (
        await session.execute(
            select(
                Resume.profile_id,
                Resume.raw["data"]["attributes"]["file"]["filePath"].astext,
                Resume.raw["data"]["attributes"]["file"]["fileLinks"]["fileLink"].astext,
                Resume.raw["data"]["attributes"]["file"]["fileSize"].astext,
            ).where(Resume.raw["data"]["attributes"]["file"]["filePath"].astext.is_not(None))
        )
    ).all()

    rows: list[dict] = []
    for pid, rel, url in photos:
        if rel and url:
            rows.append(dict(profile_id=pid, kind=MediaKind.photo, source_url=url, rel_path=rel))
    for pid, rel, url, size in files:
        if rel and url:
            rows.append(dict(
                profile_id=pid, kind=MediaKind.resume_file, source_url=url, rel_path=rel,
                file_size=int(size) if size and size.isdigit() else None,
            ))

    # Postgres caps a statement at 32767 bind parameters. A row here costs up to 8 (the
    # explicit columns plus the status/attempts defaults SQLAlchemy fills in), so batches
    # of 5000 silently blew the limit and lost every full batch of resume files.
    added = 0
    batch = 2000
    for i in range(0, len(rows), batch):
        chunk = rows[i:i + batch]
        stmt = insert(MediaFile).values(chunk).on_conflict_do_nothing(
            index_elements=["profile_id", "kind"]
        )
        result = await session.execute(stmt)
        added += result.rowcount or 0
        await session.commit()

    return {"media_seen": len(rows), "media_new": added}


class PendingItem(NamedTuple):
    """Just what the downloader needs.

    Loading 186k ORM objects would park them all in the session's identity map, which costs
    memory and slows every later flush for no benefit — nothing here mutates the objects.
    """

    id: int
    kind: MediaKind
    source_url: str
    rel_path: str
    file_size: int | None
    attempts: int


async def pending(session: AsyncSession, limit: int | None = None) -> list[PendingItem]:
    q = (
        select(
            MediaFile.id, MediaFile.kind, MediaFile.source_url,
            MediaFile.rel_path, MediaFile.file_size, MediaFile.attempts,
        )
        .where(
            MediaFile.status.in_([MediaStatus.pending, MediaStatus.failed]),
            MediaFile.attempts < MAX_ATTEMPTS,
        )
        .order_by(MediaFile.id)
    )
    if limit:
        q = q.limit(limit)
    return [PendingItem(*row) for row in (await session.execute(q)).all()]


async def download_all(
    session: AsyncSession, items: list[PendingItem], *, on_progress=None
) -> dict[str, int]:
    """Fixed worker pool over a queue, with batched status writes.

    The obvious shape — gather() every item behind a semaphore — measured 12x slower than the
    CDN can actually serve (60 files/min against 720 at the same concurrency): 186k coroutines
    all contend on one semaphore, and each holds its slot through a serialised per-file UPDATE.
    A bounded pool plus batched writes keeps the sockets busy instead of the event loop.
    """
    stats = {"downloaded": 0, "skipped": 0, "failed": 0, "gone": 0, "bytes": 0}
    if not items:
        return stats

    queue: asyncio.Queue = asyncio.Queue()
    for item in items:
        queue.put_nowait(item)

    pending_writes: list[dict] = []
    write_lock = asyncio.Lock()
    done = 0

    async def flush() -> None:
        nonlocal pending_writes
        # The ENTIRE flush stays inside the lock. An AsyncSession wraps a single connection
        # and is not safe for concurrent use: committing from two workers at once raises
        # "commit() is already in progress" and wedges the run — which is exactly what
        # stalled the first full download at 94%. One batched executemany per 250 files is
        # short enough that holding the lock costs nothing measurable.
        async with write_lock:
            if not pending_writes:
                return
            batch, pending_writes = pending_writes, []
            try:
                # SQLAlchemy's "ORM bulk UPDATE by primary key": each dict carries `id` plus
                # the columns to set, and the whole batch goes out as one executemany.
                await session.execute(update(MediaFile), batch)
                await session.commit()
            except Exception:  # noqa: BLE001
                # Without the rollback a single bad row leaves the transaction aborted and
                # every later statement fails, which silently wedges the run. The files are
                # on disk either way; their rows stay pending and are picked up next time.
                await session.rollback()
                log.exception("media status batch failed (%s rows) — rolled back", len(batch))

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(settings.media_timeout_s),
        follow_redirects=True,
        limits=httpx.Limits(
            max_connections=settings.media_concurrency,
            max_keepalive_connections=settings.media_concurrency,
        ),
        headers={"user-agent": "sberpodbor-export/2.0"},
    ) as client:

        async def worker() -> None:
            nonlocal done
            while True:
                try:
                    item = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                try:
                    await handle(item)
                except Exception:  # noqa: BLE001
                    # One bad item must not kill the pool: if a worker escapes, gather()
                    # unwinds, the httpx client closes underneath the survivors, and every
                    # remaining file fails with "client has been closed" — which is exactly
                    # how 5 669 downloads were lost.
                    log.exception("media worker error on item %s", item.id)

        async def handle(item: PendingItem) -> None:
            nonlocal done
            target = local_path(item.kind, item.rel_path)
            if target.exists() and target.stat().st_size > 0:
                # Adopt what a previous run already fetched instead of re-downloading.
                values = dict(
                    status=MediaStatus.done,
                    file_size=target.stat().st_size,
                    downloaded_at=datetime.now(UTC),
                    error=None,
                    content_type=None,
                    sha256=None,
                    attempts=item.attempts,
                )
                stats["skipped"] += 1
            else:
                values = await _download_one(client, item, target, stats)

            values["id"] = item.id
            values.setdefault("content_type", None)
            values.setdefault("sha256", None)
            values.setdefault("file_size", item.file_size)
            values.setdefault("downloaded_at", None)
            values.setdefault("error", None)
            async with write_lock:
                pending_writes.append(values)
                ready = len(pending_writes) >= 250

            done += 1
            if ready:
                await flush()
                if on_progress:
                    await on_progress(done, len(items))

        await asyncio.gather(*(worker() for _ in range(settings.media_concurrency)))

    await flush()
    if on_progress:
        await on_progress(done, len(items))
    return stats


async def _download_one(
    client: httpx.AsyncClient, item: PendingItem, target: Path, stats: dict
) -> dict:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".part")
    digest = hashlib.sha256()
    size = 0
    try:
        async with client.stream("GET", item.source_url) as r:
            # 404 and 403 are both final: the file is either gone or withheld, and the
            # service answers the same way however the request is dressed up (checked with
            # and without Referer/User-Agent). Retrying them just burns attempts.
            if r.status_code in (404, 403):
                stats["gone"] += 1
                reason = ("404 — файла больше нет на стороне сервиса" if r.status_code == 404
                          else "403 — сервис не отдаёт этот файл")
                return dict(status=MediaStatus.gone, attempts=item.attempts + 1, error=reason)
            r.raise_for_status()
            # Some responses carry a Content-Type with a long parameter list, which
            # overflowed varchar(128) and aborted the whole transaction — taking the run
            # with it. Only the media type matters here.
            content_type = (r.headers.get("content-type") or "").split(";")[0].strip()[:128] or None
            with open(tmp, "wb") as fh:
                async for chunk in r.aiter_bytes(64 * 1024):
                    fh.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
    except Exception as exc:  # noqa: BLE001 - recorded on the row, retried next run
        tmp.unlink(missing_ok=True)
        stats["failed"] += 1
        return dict(
            status=MediaStatus.failed,
            attempts=item.attempts + 1,
            error=f"{type(exc).__name__}: {exc}"[:500],
        )

    # Rename only once the body is complete, so a killed run leaves no half file.
    tmp.replace(target)
    stats["downloaded"] += 1
    stats["bytes"] += size
    return dict(
        status=MediaStatus.done,
        file_size=size,
        content_type=content_type,
        sha256=digest.hexdigest(),
        attempts=item.attempts + 1,
        downloaded_at=datetime.now(UTC),
        error=None,
    )


async def media_stats(session: AsyncSession) -> dict:
    rows = (
        await session.execute(
            select(MediaFile.kind, MediaFile.status, func.count(), func.sum(MediaFile.file_size))
            .group_by(MediaFile.kind, MediaFile.status)
        )
    ).all()
    out: dict = {"photo": {}, "resume_file": {}, "total_bytes": 0}
    for kind, status, cnt, total in rows:
        out[kind.value][status.value] = cnt
        if status is MediaStatus.done and total:
            out["total_bytes"] += int(total)
    for k in ("photo", "resume_file"):
        out[k]["total"] = sum(out[k].values())
    return out
