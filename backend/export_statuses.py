"""Выгрузка справочника статусов подбора в CSV.

    docker compose run --rm backend python export_statuses.py

Adds the live application count per status, since "which statuses are actually in use"
is the first thing anyone asks when looking at the list.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import sys
from datetime import UTC, datetime
from pathlib import Path

import asyncpg

from app.config import settings

GROUP_ORDER = {"start": 0, "middle": 1, "positive": 2, "negative": 3}


def dsn() -> str:
    return settings.database_url.replace("postgresql+asyncpg://", "postgresql://")


async def build(out: Path) -> int:
    conn = await asyncpg.connect(dsn())
    try:
        rows = await conn.fetch(
            """
            SELECT s.id, s.title, s.group_code, s.group_title, s.sort_order,
                   s.background_color, s.text_color, s.legal_time_limit,
                   (SELECT count(*) FROM candidates c WHERE c.status_id = s.id) AS used
            FROM candidate_statuses s
            ORDER BY s.sort_order NULLS LAST, s.id
            """
        )
    finally:
        await conn.close()

    if not rows:
        print("Справочник пуст — сначала выполните развёртку (sweep).", file=sys.stderr)
        return 1

    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow([
            "id", "название", "группа", "группа_название", "порядок",
            "заявок_с_этим_статусом", "цвет_фона", "цвет_текста", "срок_дней",
        ])
        for r in rows:
            w.writerow([
                r["id"], r["title"], r["group_code"], r["group_title"], r["sort_order"],
                r["used"], r["background_color"], r["text_color"], r["legal_time_limit"],
            ])

    by_group: dict[str, int] = {}
    unused = 0
    for r in rows:
        by_group[r["group_title"] or "—"] = by_group.get(r["group_title"] or "—", 0) + 1
        if not r["used"]:
            unused += 1

    print(f"Готово: {out} ({out.stat().st_size} б)")
    print(f"  статусов всего   : {len(rows)}")
    for g, n in sorted(by_group.items(), key=lambda x: GROUP_ORDER.get(x[0], 9)):
        print(f"    {g:<12} {n}")
    print(f"  не используется  : {unused}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="CSV со справочником статусов подбора")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    out = Path(args.out) if args.out else Path(settings.export_dir) / (
        f"статусы-подбора-{datetime.now(UTC).strftime('%Y%m%d')}.csv"
    )
    return asyncio.run(build(out))


if __name__ == "__main__":
    sys.exit(main())
