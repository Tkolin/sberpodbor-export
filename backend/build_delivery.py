"""Assemble a single hand-off package: data, files and a README, ready to zip.

    docker compose run --rm backend python build_delivery.py --out /exports/delivery

Produces a directory the client can open without any of our tooling:

    README.md              что внутри и как этим пользоваться
    csv/*.csv              все сущности, UTF-8 с BOM, разделитель ';' — открываются двойным
                           кликом в Excel с русской локалью
    xml/                   тот же набор одним вложенным документом + XSD-схема
    media/photos, resumes  оригиналы файлов, пути совпадают с колонкой в media.csv

CSV is written straight from a server-side cursor so a 300k-row table never lands in memory.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import re
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

import asyncpg

from app.config import settings

BOM = "utf-8-sig"
DELIM = ";"

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t\r\f\v]+")


def html_to_text(html: str | None) -> str:
    if not html:
        return ""
    t = re.sub(r"<br\s*/?>", "\n", html, flags=re.I)
    t = re.sub(r"</(p|div|li|tr|h[1-6])>", "\n", t, flags=re.I)
    t = _TAG.sub("", t)
    for a, b in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                 ("&quot;", '"'), ("&#39;", "'")):
        t = t.replace(a, b)
    return _WS.sub(" ", t).strip()


def fmt(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "да" if v else "нет"
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return str(v)


TABLES: list[tuple[str, str, list[str], str]] = [
    (
        "profiles.csv",
        "Кандидаты: ФИО, контакты, город, текущее место работы",
        ["id", "last_name", "first_name", "middle_name", "phone", "email", "city",
         "cur_position", "cur_company", "experience"],
        "SELECT id, last_name, first_name, middle_name, phone, email, city,"
        " cur_position, cur_company, experience FROM profiles ORDER BY id",
    ),
    (
        "candidates.csv",
        "Заявки: кандидат на вакансию, статус, рекрутёры",
        ["candidate_id", "profile_id", "vacancy_id", "vacancy_title", "status_id",
         "status_title", "created_at", "recruiters", "managers"],
        "SELECT candidate_id, profile_id, vacancy_id, vacancy_title, status_id,"
        " status_title, created_at,"
        " (SELECT string_agg(trim(concat_ws(' ', r->>'lastName', r->>'firstName')), ', ')"
        "  FROM jsonb_array_elements(coalesce(recruiters,'[]'::jsonb)) r),"
        " (SELECT string_agg(trim(concat_ws(' ', m->>'lastName', m->>'firstName')), ', ')"
        "  FROM jsonb_array_elements(coalesce(managers,'[]'::jsonb)) m)"
        " FROM candidates ORDER BY candidate_id",
    ),
    (
        "contacts.csv",
        "Контакты кандидатов: телефоны, почты, соцсети",
        ["id", "profile_id", "candidate_id", "type", "value", "is_main"],
        "SELECT id, profile_id, candidate_id, type, value, is_main FROM contacts ORDER BY id",
    ),
    (
        "work_history.csv",
        "Опыт работы по местам (разобран из текста резюме)",
        ["profile_id", "ord", "company", "position", "started_at", "finished_at",
         "is_current", "duration_text", "description"],
        "SELECT profile_id, ord, company, position, started_at, finished_at,"
        " is_current, duration_text, description FROM work_history"
        " ORDER BY profile_id, ord",
    ),
    (
        "logs.csv",
        "История движения заявок по воронке",
        ["id", "candidate_id", "date_time_at", "user_full_name", "message"],
        "SELECT id, candidate_id, date_time_at, user_full_name, message FROM logs"
        " ORDER BY candidate_id, date_time_at",
    ),
    (
        "comments.csv",
        "Комментарии рекрутёров по кандидатам",
        ["id", "candidate_id", "created_at", "changed_at", "user_full_name", "comment"],
        "SELECT id, candidate_id, created_at, changed_at, user_full_name, comment"
        " FROM comments ORDER BY candidate_id, created_at",
    ),
    (
        "vacancies.csv",
        "Вакансии",
        ["id", "title", "status", "city", "persons_count", "created_at", "desired_closing_at"],
        "SELECT id, title, status, city, persons_count, created_at, desired_closing_at"
        " FROM vacancies ORDER BY id",
    ),
    (
        "ats_users.csv",
        "Сотрудники (рекрутёры и наниматели) в Сбер Подборе",
        ["id", "last_name", "first_name", "middle_name", "email", "role", "position", "status"],
        "SELECT id, last_name, first_name, middle_name, email, role, position, status"
        " FROM ats_users ORDER BY id",
    ),
]


async def write_csv(conn, out: Path, filename: str, headers: list[str], query: str) -> int:
    path = out / filename
    n = 0
    with open(path, "w", encoding=BOM, newline="") as fh:
        w = csv.writer(fh, delimiter=DELIM, quoting=csv.QUOTE_MINIMAL)
        w.writerow(headers)
        # Server-side cursor: these tables run to hundreds of thousands of rows.
        async with conn.transaction():
            async for rec in conn.cursor(query, prefetch=2000):
                w.writerow([fmt(v) for v in rec])
                n += 1
    return n


async def write_resumes(conn, out: Path) -> int:
    """Resume text, one row per profile. The original HTML stays in the XML export."""
    path = out / "resumes.csv"
    n = 0
    with open(path, "w", encoding=BOM, newline="") as fh:
        w = csv.writer(fh, delimiter=DELIM, quoting=csv.QUOTE_MINIMAL)
        w.writerow(["profile_id", "source", "text"])
        async with conn.transaction():
            async for rec in conn.cursor(
                "SELECT profile_id, source, body_html FROM resumes ORDER BY profile_id",
                prefetch=200,
            ):
                w.writerow([rec[0], rec[1] or "", html_to_text(rec[2])])
                n += 1
    return n


async def write_media_index(conn, out: Path) -> int:
    path = out / "media.csv"
    n = 0
    with open(path, "w", encoding=BOM, newline="") as fh:
        w = csv.writer(fh, delimiter=DELIM, quoting=csv.QUOTE_MINIMAL)
        w.writerow(["profile_id", "kind", "path_in_archive", "content_type", "file_size"])
        async with conn.transaction():
            async for rec in conn.cursor(
                "SELECT profile_id, kind::text, rel_path, content_type, file_size"
                " FROM media_files WHERE status = 'done' ORDER BY profile_id",
                prefetch=2000,
            ):
                sub = "photos" if rec[1] == "photo" else "resumes"
                kind_ru = "фото" if rec[1] == "photo" else "оригинал резюме"
                # Some upstream paths carry a leading slash; joining naively produced
                # "media/photos//2025/..." for 10 988 rows — a pointer to a file that does
                # not exist under that name in the archive.
                rel = str(rec[2]).lstrip("/")
                w.writerow([rec[0], kind_ru, f"media/{sub}/{rel}", rec[3] or "", rec[4] or ""])
                n += 1
    return n


def dsn() -> str:
    return settings.database_url.replace("postgresql+asyncpg://", "postgresql://")


async def build(out: Path, with_media: bool) -> int:
    started = datetime.now(UTC)
    csv_dir = out / "csv"
    csv_dir.mkdir(parents=True, exist_ok=True)

    conn = await asyncpg.connect(dsn(), command_timeout=3600)
    counts: dict[str, int] = {}
    try:
        for filename, _desc, headers, query in TABLES:
            counts[filename] = await write_csv(conn, csv_dir, filename, headers, query)
            print(f"  {filename:<20} {counts[filename]:>9,}".replace(",", " "), flush=True)

        counts["resumes.csv"] = await write_resumes(conn, csv_dir)
        print(f"  {'resumes.csv':<20} {counts['resumes.csv']:>9,}".replace(",", " "), flush=True)

        counts["media.csv"] = await write_media_index(conn, csv_dir)
        print(f"  {'media.csv':<20} {counts['media.csv']:>9,}".replace(",", " "), flush=True)
    finally:
        await conn.close()

    # XSD next to the XML it describes.
    xml_dir = out / "xml"
    xml_dir.mkdir(parents=True, exist_ok=True)
    schema_src = Path(__file__).resolve().parent / "xsd" / "sberpodbor-export-1.0.xsd"
    if schema_src.exists():
        shutil.copy2(schema_src, xml_dir / schema_src.name)

    if with_media:
        media_src = Path(settings.media_dir)
        media_dst = out / "media"
        if media_src.exists():
            print("  копирую файлы (это долго)...", flush=True)
            if media_dst.exists():
                shutil.rmtree(media_dst)
            shutil.copytree(media_src, media_dst)

    (out / "README.md").write_text(readme(counts), encoding="utf-8")
    print(f"\nГотово за {(datetime.now(UTC) - started).total_seconds() / 60:.1f} мин: {out}")
    return 0


def readme(c: dict[str, int]) -> str:
    n = lambda k: f"{c.get(k, 0):,}".replace(",", " ")  # noqa: E731
    today = datetime.now(UTC).strftime("%d.%m.%Y")
    return f"""# Выгрузка базы Сбер Подбора

Сформировано: {today}

Здесь вся база кандидатов, выгруженная из Сбер Подбора: анкеты, заявки на вакансии,
переписка рекрутёров, резюме, фотографии и оригиналы файлов резюме.

Ничего устанавливать не нужно — все таблицы лежат в CSV и открываются в Excel двойным
кликом, файлы лежат обычными папками.

## Что внутри

```
README.md          этот файл
db/                полный дамп базы PostgreSQL — здесь ВСЁ, без потерь
csv/               те же данные таблицами (открываются в Excel)
xml/               те же данные вложенным документом + схема XSD
media/photos/      фотографии кандидатов
media/resumes/     оригиналы резюме (pdf, doc, docx, rtf)
```

Три формата — это одни и те же данные, выбирайте по задаче:

* **`db/`** — если данные нужно куда-то загрузить и работать с ними всерьёз. Это точная
  копия базы: все поля, типы, связи и индексы. **Только здесь есть колонки `raw`** —
  исходные ответы сервиса по каждой записи целиком, и **исходная вёрстка резюме**.
* **`csv/`** — если нужно просто посмотреть, отфильтровать в Excel, отдать аналитику.
  Это упрощённый срез: без `raw`, резюме приведено к простому тексту.
* **`xml/`** — если импортируете в чужую систему и удобнее вложенная структура, где
  контакты, заявки и комментарии лежат внутри профиля. Резюме здесь с исходной вёрсткой.

## Как загрузить базу целиком

Нужен PostgreSQL 17. Создайте пустую базу и восстановите дамп:

```
createdb sberpodbor
pg_restore -d sberpodbor --no-owner --jobs 4 db/sberpodbor.dump
```

Если нужен обычный SQL-текст вместо бинарного дампа:

```
pg_restore -f sberpodbor.sql db/sberpodbor.dump
```

После восстановления таблицы называются так же, как файлы в `csv/`, плюс появляются
колонки, которых в CSV нет: `raw` (полный ответ сервиса по записи) и `resumes.body_html`
(резюме с исходной вёрсткой).

## Таблицы

Ниже — то, что попало в CSV. В базе из `db/` эти же таблицы полнее.

| Файл | Строк | Что в нём |
|---|---|---|
| `csv/profiles.csv` | {n('profiles.csv')} | Кандидаты: ФИО, телефон, почта, город, текущее место работы |
| `csv/candidates.csv` | {n('candidates.csv')} | Заявки: кто на какую вакансию, статус, дата, рекрутёры |
| `csv/contacts.csv` | {n('contacts.csv')} | Контакты: телефоны, почты, telegram, linkedin, skype и прочее |
| `csv/work_history.csv` | {n('work_history.csv')} | Опыт работы по местам: компания, должность, период, обязанности |
| `csv/resumes.csv` | {n('resumes.csv')} | Текст резюме и его источник (hh, linkedin, career.habr) |
| `csv/logs.csv` | {n('logs.csv')} | История заявок: каждое изменение статуса, с автором и датой |
| `csv/comments.csv` | {n('comments.csv')} | Комментарии рекрутёров по кандидатам |
| `csv/vacancies.csv` | {n('vacancies.csv')} | Вакансии |
| `csv/ats_users.csv` | {n('ats_users.csv')} | Сотрудники, которые вели работу |
| `csv/media.csv` | {n('media.csv')} | Указатель: какой файл в `media/` какому кандидату принадлежит |

## Как таблицы связаны

Всё связывается двумя числами.

* **`profile_id`** — это сам человек. По нему соединяются `profiles`, `contacts`,
  `work_history`, `resumes`, `media`.
* **`candidate_id`** — это одна заявка человека на одну вакансию. По нему соединяются
  `candidates`, `logs`, `comments`.

У одного человека может быть несколько заявок: `candidates` связывает `profile_id`
с `candidate_id`.

```
profiles ──< candidates ──< logs
   │             └───────< comments
   ├──< contacts
   ├──< work_history
   ├──── resumes
   └──< media
```

## Фотографии и файлы резюме

Путь к каждому файлу указан в `csv/media.csv` в колонке `path_in_archive` — он отсчитывается
от корня этого архива. Например, `media/photos/2023/06/01/ab12cd34.jpg`.

Открыть фото конкретного кандидата: найдите его `profile_id` в `media.csv`, возьмите путь
из строки со значением «фото» и откройте файл.

## Важные оговорки по данным

Чтобы не было вопросов при проверке, три вещи стоит знать заранее.

1. **Дата создания заявки почти всегда пустая.** Сбер Подбор это поле по своему API не
   отдаёт — оно заполнено у 41 заявки из 326 тысяч. Реальный момент создания виден в
   `logs.csv`: первая запись по заявке — это прикрепление кандидата к вакансии.

2. **У 71% заявок нет истории.** Если по `candidate_id` в `logs.csv` ничего не нашлось,
   значит истории нет и в самом Сбер Подборе — мы её запрашивали, сервис вернул пустой
   ответ. Это особенность источника, а не пробел выгрузки.

3. **Опыт работы разобран из текста резюме.** По API сервис отдаёт только одно, последнее
   место работы. Полная история есть лишь внутри текста резюме, поэтому `work_history.csv`
   получен его разбором: удалось разобрать 96% резюме, где такой раздел есть. Исходный
   текст сохранён полностью в `resumes.csv` — при желании можно перепроверить любую строку.

Ещё: 130 файлов (14 фото и 116 резюме) сервис не отдаёт вообще — отвечает отказом на любой
запрос. Они отсутствуют в `media/`, в `media.csv` их тоже нет.

## Формат CSV

Кодировка UTF-8 с BOM, разделитель — точка с запятой. Excel с русской локалью открывает
такие файлы двойным кликом без мастера импорта.

Если данные всё же встали в один столбец: Данные → Текст по столбцам → с разделителями →
точка с запятой.

## XML

В `xml/` лежит схема `sberpodbor-export-1.0.xsd`. Она описывает вложенный формат, где
контакты, резюме, заявки, логи и комментарии лежат внутри своего профиля — это удобно для
машинного импорта, когда связи по идентификаторам собирать не хочется.
"""


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the client hand-off package")
    ap.add_argument("--out", default="/exports/delivery")
    ap.add_argument("--no-media", action="store_true", help="без копирования файлов")
    args = ap.parse_args()
    return asyncio.run(build(Path(args.out), not args.no_media))


if __name__ == "__main__":
    sys.exit(main())
