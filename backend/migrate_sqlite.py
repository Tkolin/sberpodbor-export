"""One-off import of the legacy SQLite export into Postgres.

Run once, after `alembic upgrade head`:

    docker compose run --rm backend python migrate_sqlite.py --src /legacy/sberpodbor.db

Design notes:

* Uses asyncpg COPY rather than INSERT. At 2.4M rows the difference is minutes versus hours.
* The SQLite file is opened read-only and never written to; it stays the source of truth
  until the verification pass at the end reports every table matching.
* done_profiles / done_candidates become timestamps on the rows themselves, so the new
  scheduler inherits "already crawled" instead of re-fetching 266k profiles from scratch.
* Idempotent at table granularity: a table that already holds rows is skipped unless
  --truncate is given, so a half-finished import can be resumed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from datetime import UTC, datetime
from typing import Any, Callable, Iterable, Iterator

import asyncpg

from app.config import settings

BATCH = 5000
BATCH_HEAVY = 500  # resumes/comments carry big text blobs

# jsonb binary wire format = one version byte, then the JSON text. Spelled with bytes()
# rather than an escape so it stays visible in source and survives copy/paste.
_JSONB_BINARY_VERSION = bytes([1])


def parse_dt(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def as_json(value: Any) -> str | None:
    """Pass JSON text through untouched; wrap anything else so JSONB accepts it."""
    if value is None or value == "":
        return None
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    text = str(value)
    try:
        json.loads(text)
    except (ValueError, TypeError):
        return json.dumps(text, ensure_ascii=False)
    return text


def as_bool(value: Any) -> bool | None:
    return None if value is None else bool(value)


class Table:
    def __init__(
        self,
        *,
        name: str,
        source: str,
        columns: list[str],
        select: str,
        transform: Callable[[sqlite3.Row], tuple],
        batch: int = BATCH,
    ) -> None:
        self.name = name
        self.source = source
        self.columns = columns
        self.select = select
        self.transform = transform
        self.batch = batch


def build_tables(synced_at: datetime) -> list[Table]:
    """Order matters: resumes references profiles."""
    return [
        Table(
            name="profiles",
            source="profiles",
            columns=[
                "id", "first_name", "last_name", "middle_name", "phone", "email", "city",
                "cur_position", "cur_company", "experience", "raw",
                "first_seen_at", "last_seen_at", "details_synced_at",
            ],
            select=(
                "SELECT p.id, p.first_name, p.last_name, p.middle_name, p.phone, p.email, p.city,"
                " p.cur_position, p.cur_company, p.experience, p.raw,"
                " (SELECT 1 FROM done_profiles d WHERE d.profile_id = p.id) AS done"
                " FROM profiles p"
            ),
            transform=lambda r: (
                r["id"], r["first_name"], r["last_name"], r["middle_name"], r["phone"],
                r["email"], r["city"], r["cur_position"], r["cur_company"], r["experience"],
                as_json(r["raw"]), synced_at, synced_at,
                synced_at if r["done"] else None,
            ),
        ),
        Table(
            name="vacancies",
            source="vacancies",
            columns=["id", "title", "status", "city", "persons_count",
                     "desired_closing_at", "created_at", "raw", "last_seen_at"],
            select="SELECT * FROM vacancies",
            transform=lambda r: (
                r["id"], r["title"], r["status"], r["city"], r["persons_count"],
                parse_dt(r["desired_closing_at"]), parse_dt(r["created_at"]),
                as_json(r["raw"]), synced_at,
            ),
        ),
        Table(
            name="ats_users",
            source="users",
            columns=["id", "first_name", "last_name", "middle_name", "email",
                     "role", "position", "status", "raw"],
            select="SELECT * FROM users",
            transform=lambda r: (
                r["id"], r["first_name"], r["last_name"], r["middle_name"], r["email"],
                r["role"], r["position"], r["status"], as_json(r["raw"]),
            ),
        ),
        Table(
            name="candidates",
            source="candidates",
            columns=[
                "candidate_id", "profile_id", "vacancy_id", "vacancy_title", "status_id",
                "status_title", "created_at", "recruiters", "managers",
                "first_seen_at", "last_seen_at", "activity_synced_at",
            ],
            select=(
                "SELECT c.*, (SELECT 1 FROM done_candidates d"
                "   WHERE d.candidate_id = c.candidate_id) AS done"
                " FROM candidates c"
            ),
            transform=lambda r: (
                r["candidate_id"], r["profile_id"], r["vacancy_id"], r["vacancy_title"],
                r["status_id"], r["status_title"], parse_dt(r["created_at"]),
                as_json(r["recruiters"]), as_json(r["managers"]),
                synced_at, synced_at, synced_at if r["done"] else None,
            ),
        ),
        Table(
            name="resumes",
            source="resumes",
            columns=["profile_id", "body_html", "source", "raw", "updated_at"],
            select="SELECT * FROM resumes",
            transform=lambda r: (
                r["profile_id"], r["body_html"], r["source"], as_json(r["raw"]), synced_at,
            ),
            batch=BATCH_HEAVY,
        ),
        Table(
            name="contacts",
            source="contacts",
            columns=["id", "candidate_id", "profile_id", "type", "value", "is_main"],
            select="SELECT * FROM contacts",
            transform=lambda r: (
                r["id"], r["candidate_id"], r["profile_id"], r["type"], r["value"],
                as_bool(r["is_main"]),
            ),
        ),
        Table(
            name="logs",
            source="logs",
            columns=["id", "candidate_id", "date_time_at", "message", "user_full_name"],
            select="SELECT * FROM logs",
            transform=lambda r: (
                r["id"], r["candidate_id"], parse_dt(r["date_time_at"]), r["message"],
                r["user_full_name"],
            ),
        ),
        Table(
            name="comments",
            source="comments",
            columns=["id", "candidate_id", "comment", "created_at", "changed_at",
                     "user_id", "user_full_name", "raw"],
            select="SELECT * FROM comments",
            transform=lambda r: (
                r["id"], r["candidate_id"], r["comment"], parse_dt(r["created_at"]),
                parse_dt(r["changed_at"]), r["user_id"], r["user_full_name"], as_json(r["raw"]),
            ),
            batch=BATCH_HEAVY,
        ),
        Table(
            name="meta",
            source="meta",
            columns=["k", "v"],
            select="SELECT k, v FROM meta",
            transform=lambda r: (r["k"], r["v"]),
        ),
    ]


def chunks(rows: Iterable[tuple], size: int) -> Iterator[list[tuple]]:
    batch: list[tuple] = []
    for row in rows:
        batch.append(row)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def pg_dsn() -> str:
    """SQLAlchemy URL -> plain libpq DSN for asyncpg."""
    return settings.database_url.replace("postgresql+asyncpg://", "postgresql://")


async def migrate(src: str, *, truncate: bool, only: set[str] | None) -> int:
    synced_at = datetime.now(UTC)
    sq = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    sq.row_factory = sqlite3.Row

    conn = await asyncpg.connect(pg_dsn())
    # copy_records_to_table speaks the BINARY protocol, so a text codec is never consulted.
    # jsonb's binary wire format is a single version byte (0x01) followed by the JSON text —
    # which lets the already-valid source JSON through without a parse/re-encode round trip.
    await conn.set_type_codec(
        "jsonb",
        encoder=lambda v: _JSONB_BINARY_VERSION + v.encode("utf-8"),
        decoder=lambda v: v[1:].decode("utf-8"),
        schema="pg_catalog",
        format="binary",
    )

    problems = 0
    try:
        for table in build_tables(synced_at):
            if only and table.name not in only:
                continue

            existing = await conn.fetchval(f'SELECT count(*) FROM "{table.name}"')
            if existing and not truncate:
                print(f"  {table.name:<12} пропуск — уже {existing:,} строк (--truncate чтобы перезалить)")
                continue
            if existing and truncate:
                await conn.execute(f'TRUNCATE "{table.name}" CASCADE')
                print(f"  {table.name:<12} очищена ({existing:,} строк)")

            src_total = sq.execute(f"SELECT count(*) FROM {table.source}").fetchone()[0]
            cursor = sq.execute(table.select)
            copied = 0
            for batch in chunks((table.transform(r) for r in cursor), table.batch):
                await conn.copy_records_to_table(
                    table.name, records=batch, columns=table.columns
                )
                copied += len(batch)
                if copied % 50_000 < table.batch:
                    print(f"  {table.name:<12} {copied:,} / {src_total:,}", flush=True)

            got = await conn.fetchval(f'SELECT count(*) FROM "{table.name}"')
            mark = "OK " if got == src_total else "РАСХОЖДЕНИЕ"
            if got != src_total:
                problems += 1
            print(f"  {mark} {table.name:<12} SQLite {src_total:,} -> PG {got:,}")

        print("\n=== сверка ===")
        checks = {
            "profiles": "SELECT count(*) FROM profiles",
            "candidates": "SELECT count(*) FROM candidates",
            "resumes": "SELECT count(*) FROM resumes",
            "contacts": "SELECT count(*) FROM contacts",
            "logs": "SELECT count(*) FROM logs",
            "comments": "SELECT count(*) FROM comments",
            "vacancies": "SELECT count(*) FROM vacancies",
            "ats_users": "SELECT count(*) FROM ats_users",
        }
        for label, q in checks.items():
            pg_n = await conn.fetchval(q)
            sq_table = "users" if label == "ats_users" else label
            sq_n = sq.execute(f"SELECT count(*) FROM {sq_table}").fetchone()[0]
            flag = "OK " if pg_n == sq_n else "!!!"
            if pg_n != sq_n:
                problems += 1
            print(f"  {flag} {label:<12} {sq_n:,} -> {pg_n:,}")

        synced_p = await conn.fetchval("SELECT count(*) FROM profiles WHERE details_synced_at IS NOT NULL")
        synced_c = await conn.fetchval("SELECT count(*) FROM candidates WHERE activity_synced_at IS NOT NULL")
        done_p = sq.execute("SELECT count(*) FROM done_profiles").fetchone()[0]
        done_c = sq.execute("SELECT count(*) FROM done_candidates").fetchone()[0]
        for label, a, b in (("details_synced", done_p, synced_p), ("activity_synced", done_c, synced_c)):
            flag = "OK " if a == b else "!!!"
            if a != b:
                problems += 1
            print(f"  {flag} {label:<12} {a:,} -> {b:,}")

        # ANALYZE now, or the first dashboard query plans against empty statistics.
        print("\nANALYZE ...", flush=True)
        await conn.execute("ANALYZE")
    finally:
        await conn.close()
        sq.close()

    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description="Import the legacy SQLite export into Postgres")
    ap.add_argument("--src", default="/legacy/sberpodbor.db", help="path to sberpodbor.db")
    ap.add_argument("--truncate", action="store_true", help="wipe target tables first")
    ap.add_argument("--only", help="comma-separated table names")
    args = ap.parse_args()

    only = {t.strip() for t in args.only.split(",")} if args.only else None
    started = datetime.now(UTC)
    print(f"Импорт {args.src} -> Postgres\n")
    problems = asyncio.run(migrate(args.src, truncate=args.truncate, only=only))
    elapsed = (datetime.now(UTC) - started).total_seconds()

    if problems:
        print(f"\nЗАВЕРШЕНО С РАСХОЖДЕНИЯМИ: {problems}. SQLite не тронут — разберитесь до переключения.")
        return 1
    print(f"\nГОТОВО за {elapsed / 60:.1f} мин. Все таблицы сошлись.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
