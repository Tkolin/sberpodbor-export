"""Parse per-job work history out of every stored resume into the work_history table.

    docker compose run --rm backend python parse_work_history.py

Re-runnable: a profile's rows are replaced wholesale, so re-parsing after a parser change
just overwrites. Reads resumes in id order and commits per batch, so an interrupted run
resumes by simply starting again (use --only-new to skip profiles already parsed).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime

import asyncpg

from app.config import settings
from app.services.work_history import parse_work_history

BATCH = 2000
COLUMNS = [
    "profile_id", "ord", "company", "position",
    "started_at", "finished_at", "is_current", "duration_text", "description",
]


def dsn() -> str:
    return settings.database_url.replace("postgresql+asyncpg://", "postgresql://")


async def run(only_new: bool, limit: int | None) -> int:
    conn = await asyncpg.connect(dsn(), command_timeout=300)
    started = datetime.now(UTC)

    where = "r.body_html IS NOT NULL"
    if only_new:
        where += " AND NOT EXISTS (SELECT 1 FROM work_history w WHERE w.profile_id = r.profile_id)"

    total = await conn.fetchval(f"SELECT count(*) FROM resumes r WHERE {where}")
    print(f"Резюме к разбору: {total:,}".replace(",", " "))

    processed = parsed = jobs_total = 0
    last_id = 0

    try:
        while True:
            rows = await conn.fetch(
                f"""SELECT r.profile_id, r.body_html FROM resumes r
                    WHERE {where} AND r.profile_id > $1
                    ORDER BY r.profile_id LIMIT $2""",
                last_id, BATCH,
            )
            if not rows:
                break
            last_id = rows[-1]["profile_id"]

            records: list[tuple] = []
            touched: list[int] = []
            for r in rows:
                pid = r["profile_id"]
                touched.append(pid)
                jobs = parse_work_history(r["body_html"])
                if jobs:
                    parsed += 1
                    jobs_total += len(jobs)
                for j in jobs:
                    records.append((
                        pid, j.order, j.company, j.position,
                        j.started_at, j.finished_at, j.is_current,
                        j.duration_text, j.description,
                    ))

            async with conn.transaction():
                # Replace rather than upsert: a re-parse can produce a different number of
                # entries, and stale rows from a previous parser version must not survive.
                await conn.execute(
                    "DELETE FROM work_history WHERE profile_id = ANY($1::bigint[])", touched
                )
                if records:
                    await conn.copy_records_to_table(
                        "work_history", records=records, columns=COLUMNS
                    )

            processed += len(rows)
            if processed % 20_000 < BATCH:
                pct = processed / max(total, 1) * 100
                print(f"  {processed:,} / {total:,} ({pct:.0f}%), мест: {jobs_total:,}"
                      .replace(",", " "), flush=True)

            if limit and processed >= limit:
                break

        elapsed = (datetime.now(UTC) - started).total_seconds()
        print(f"\nГотово за {elapsed / 60:.1f} мин")
        print(f"  резюме обработано : {processed:,}".replace(",", " "))
        print(f"  из них разобрано  : {parsed:,} ({parsed / max(processed, 1) * 100:.1f}%)"
              .replace(",", " "))
        print(f"  мест работы всего : {jobs_total:,}".replace(",", " "))
        print(f"  в среднем на резюме: {jobs_total / max(parsed, 1):.1f}")

        await conn.execute("ANALYZE work_history")
    finally:
        await conn.close()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Parse work history from stored resumes")
    ap.add_argument("--only-new", action="store_true", help="пропустить уже разобранные профили")
    ap.add_argument("--limit", type=int, default=None, help="остановиться после N резюме")
    args = ap.parse_args()
    return asyncio.run(run(args.only_new, args.limit))


if __name__ == "__main__":
    sys.exit(main())
