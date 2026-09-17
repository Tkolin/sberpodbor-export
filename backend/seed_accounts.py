"""Import the legacy accounts.json into the encrypted ats_accounts table.

    docker compose run --rm -v ./accounts.json:/legacy/accounts.json:ro \
        backend python seed_accounts.py --src /legacy/accounts.json

Existing emails are skipped, never overwritten — re-running is safe. Credentials are
encrypted with SP_ENCRYPTION_KEY on the way in and are never readable through the API.
Delete accounts.json afterwards: once seeded, the panel is the only place they need to live.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from sqlalchemy import select

from app.core.security import mask_proxy, secret_box
from app.db import SessionLocal
from app.models import AtsAccount


async def seed(path: Path, activate: bool) -> int:
    entries = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(entries, dict):
        entries = [entries]

    box = secret_box()
    added = skipped = 0

    async with SessionLocal() as session:
        for entry in entries:
            email = str(entry["email"]).lower()
            if await session.scalar(select(AtsAccount).where(AtsAccount.email == email)):
                print(f"  пропуск  {email} — уже настроен")
                skipped += 1
                continue
            session.add(
                AtsAccount(
                    label=entry.get("label"),
                    email=email,
                    password_enc=box.encrypt(entry["password"]),
                    proxy_enc=box.encrypt(entry.get("proxy")),
                    is_active=activate,
                )
            )
            print(f"  добавлен {email} через {mask_proxy(entry.get('proxy')) or 'без прокси'}")
            added += 1
        await session.commit()

    print(f"\nДобавлено: {added}, пропущено: {skipped}")
    if added and not activate:
        print("Аккаунты выключены — включите их в админке, когда будете готовы начать обход.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Seed ATS accounts from accounts.json")
    ap.add_argument("--src", default="/legacy/accounts.json")
    ap.add_argument(
        "--inactive",
        action="store_true",
        help="add them switched off, so the scheduler does not start crawling immediately",
    )
    args = ap.parse_args()

    path = Path(args.src)
    if not path.exists():
        print(f"Файл не найден: {path}", file=sys.stderr)
        return 1
    return asyncio.run(seed(path, activate=not args.inactive))


if __name__ == "__main__":
    sys.exit(main())
