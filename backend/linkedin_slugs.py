"""Export every LinkedIn profile slug in the database as a one-column CSV.

    docker compose run --rm backend python linkedin_slugs.py

The stored values are a decade of copy-paste: full URLs, bare 'in/slug', 'linkedin.com/in/x',
old '/pub/name/41/275/69', Cyrillic slugs, percent-encoding, trailing slashes, query strings.

Mixed in with them is a lot that is *not* a personal profile — company pages, /feed/,
profile-settings links, shortened goo.gl links, GitHub and hh.ru URLs, plain e-mail
addresses. A naive "take the last path segment" turns linkedin.com/company/agoda/ into the
slug "agoda", so every value is classified and only real profile slugs are emitted; the
rest is counted and reported rather than silently dropped.

Written UTF-8 with a BOM so Excel opens Cyrillic slugs correctly on a double click.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import re
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote

import asyncpg

from app.config import settings

# '/in/x', 'in/x', 'linkedin.com/in/x', '/pub/name/41/275/69'
SLUG_RE = re.compile(r"(?:^|[/.])(?:in|pub)/([^/?#\s]+)", re.IGNORECASE)

# LinkedIn paths that are not a person.
NON_PROFILE = re.compile(
    r"linkedin\.com/(company|showcase|school|groups|feed|jobs|hp|learning|posts|pulse)"
    r"|/(company|showcase|school|groups|feed|jobs)/"
    r"|profile/(view|edit|preview)"
    r"|public-profile/settings",
    re.IGNORECASE,
)

# A URL that is clearly somewhere else entirely.
OTHER_SITE = re.compile(
    r"^(https?://)?(www\.)?(github|goo\.gl|bit\.ly|clck\.ru|hh\.ru|[a-z]+\.hh\.ru|notion\.so"
    r"|amazinghiring|search\.amazinghiring|t\.me|vk\.com|facebook)",
    re.IGNORECASE,
)

EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", re.IGNORECASE)
# A bare slug: letters (any alphabet), digits, dot, dash, underscore. No slash, no scheme.
BARE_SLUG = re.compile(r"^[\w.\-]{2,100}$", re.UNICODE)
# Looks like a hostname rather than a slug ('foo.com', 'x.ru').
HOSTNAME = re.compile(r"\.(com|ru|org|net|io|co|me|dev|pro|info|biz)$", re.IGNORECASE)


def dsn() -> str:
    return settings.database_url.replace("postgresql+asyncpg://", "postgresql://")


def classify(value: str | None) -> tuple[str | None, str]:
    """Return (slug, reason). slug is None unless reason == 'ok'."""
    if not value or not value.strip():
        return None, "пусто"
    raw = value.strip()
    # A few values were pasted after being URL-encoded twice ("%25D1%2581"), so one pass
    # leaves "%D1%81" behind. Decode until it stops changing, bounded so a literal '%' in
    # a slug cannot loop.
    for _ in range(3):
        decoded = unquote(raw)
        if decoded == raw:
            break
        raw = decoded

    if EMAIL.match(raw):
        return None, "почта, а не ссылка"
    if OTHER_SITE.match(raw):
        return None, "ссылка на другой сайт"
    if NON_PROFILE.search(raw):
        # Company/feed/settings pages: a real URL, but not a person.
        return None, "не профиль человека (компания, лента, настройки)"

    match = SLUG_RE.search(raw)
    if match:
        slug = match.group(1).strip().strip("/")
        return (slug, "ok") if slug else (None, "пустой слаг после /in/")

    # No /in/ marker: accept only if the whole value is a bare identifier.
    candidate = raw.rstrip("/")
    if "/" in candidate or ":" in candidate or "?" in candidate:
        return None, "ссылка без /in/ — не удалось определить профиль"
    if HOSTNAME.search(candidate):
        return None, "похоже на домен"
    if BARE_SLUG.match(candidate):
        return candidate, "ok"
    return None, "не распознано"


async def build(out: Path, with_url: bool, report: Path | None) -> int:
    conn = await asyncpg.connect(dsn())
    try:
        rows = await conn.fetch(
            "SELECT c.value, c.profile_id FROM contacts c WHERE c.type = 'linkedin'"
        )
    finally:
        await conn.close()

    seen: dict[str, str] = {}
    reasons: Counter[str] = Counter()
    rejected: list[tuple[str, str]] = []

    for r in rows:
        slug, reason = classify(r["value"])
        reasons[reason] += 1
        if slug is None:
            rejected.append((r["value"] or "", reason))
            continue
        # LinkedIn slugs are case-insensitive; dedupe on that, keep the original spelling.
        seen.setdefault(slug.casefold(), slug)

    slugs = sorted(seen.values(), key=str.casefold)

    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh, delimiter=";")  # ';' is what Excel expects in a RU locale
        writer.writerow(["linkedin_url" if with_url else "linkedin_slug"])
        for slug in slugs:
            writer.writerow([f"https://www.linkedin.com/in/{slug}" if with_url else slug])

    n = lambda v: f"{v:,}".replace(",", " ")  # noqa: E731
    print(f"Готово: {out} ({out.stat().st_size / 1024:.0f} КБ)")
    print(f"  контактов linkedin в базе : {n(len(rows))}")
    print(f"  распознано профилей       : {n(reasons['ok'])}")
    print(f"  уникальных слагов         : {n(len(slugs))}")
    print(f"  дубликатов свёрнуто       : {n(reasons['ok'] - len(slugs))}")
    print("\n  не вошло в выгрузку:")
    for reason, cnt in reasons.most_common():
        if reason != "ok":
            print(f"    {n(cnt):>6}  {reason}")

    if report and rejected:
        with open(report, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh, delimiter=";")
            w.writerow(["значение_в_базе", "почему_не_вошло"])
            w.writerows(sorted(rejected, key=lambda x: (x[1], x[0])))
        print(f"\n  список исключённых: {report}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="One-column CSV of all LinkedIn profile slugs")
    ap.add_argument("--out", default=None)
    ap.add_argument("--urls", action="store_true", help="полные ссылки вместо голых слагов")
    ap.add_argument("--report", action="store_true", help="дополнительно CSV с исключёнными значениями")
    args = ap.parse_args()

    stamp = datetime.now(UTC).strftime("%Y%m%d")
    name = "linkedin-urls" if args.urls else "linkedin-slugs"
    out = Path(args.out) if args.out else Path(settings.export_dir) / f"{name}-{stamp}.csv"
    report = Path(settings.export_dir) / f"linkedin-исключённые-{stamp}.csv" if args.report else None
    return asyncio.run(build(out, args.urls, report))


if __name__ == "__main__":
    sys.exit(main())
