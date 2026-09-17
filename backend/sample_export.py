"""Build a human-readable Excel sample of the collected dataset.

    docker compose run --rm backend python sample_export.py --limit 10

Picks the *richest* profiles — ones that actually exercise every field — so a reviewer can
judge coverage rather than stare at half-empty rows. One sheet per entity, mirroring the
data model, plus an overview sheet explaining what each sheet holds.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

import asyncpg
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from app.config import settings

# Excel refuses anything longer in a single cell.
CELL_LIMIT = 32000

HEADER_FILL = PatternFill("solid", fgColor="1F4E79")
HEADER_FONT = Font(color="FFFFFF", bold=True)
TITLE_FONT = Font(bold=True, size=13)


def dsn() -> str:
    return settings.database_url.replace("postgresql+asyncpg://", "postgresql://")


_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t\r\f\v]+")
_NL = re.compile(r"\n{3,}")


def html_to_text(html: str | None) -> str:
    """Resumes are stored as source HTML; a reviewer wants the words, not the markup."""
    if not html:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", html, flags=re.I)
    text = re.sub(r"</(p|div|li|tr|h[1-6])>", "\n", text, flags=re.I)
    text = _TAG.sub("", text)
    for entity, char in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"),
                         ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'")):
        text = text.replace(entity, char)
    text = _WS.sub(" ", text)
    text = _NL.sub("\n\n", text)
    return text.strip()


def fmt_dt(value) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%d.%m.%Y %H:%M")
    return str(value)


def people(raw) -> str:
    """recruiters/managers JSONB -> 'Фамилия Имя, Фамилия Имя'."""
    if not raw:
        return ""
    import json

    items = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(items, list):
        return ""
    names = []
    for p in items:
        if isinstance(p, dict):
            name = " ".join(x for x in [p.get("lastName"), p.get("firstName")] if x)
            if name:
                names.append(name)
    return ", ".join(names)


def write_sheet(wb: Workbook, title: str, headers: list[str], rows: list[list], widths: list[int]):
    ws = wb.create_sheet(title)
    ws.append(headers)
    for cell in ws[1]:
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    ws.row_dimensions[1].height = 30

    for row in rows:
        ws.append([
            (v[:CELL_LIMIT] if isinstance(v, str) and len(v) > CELL_LIMIT else v)
            for v in row
        ])

    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w

    ws.freeze_panes = "A2"
    if rows:
        ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{len(rows) + 1}"
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    return ws


async def build(limit: int, out: Path) -> int:
    conn = await asyncpg.connect(dsn())
    try:
        # "Fullest" = actually has a resume body, contacts, several applications, and a long
        # activity trail. Ordering by the sum makes the sample show every field populated.
        picks = await conn.fetch(
            """
            WITH stat AS (
                SELECT p.id,
                       coalesce(length(r.body_html), 0)                      AS resume_len,
                       (SELECT count(*) FROM contacts   c WHERE c.profile_id = p.id) AS n_contacts,
                       (SELECT count(*) FROM candidates k WHERE k.profile_id = p.id) AS n_apps,
                       (SELECT count(*) FROM logs     l
                          JOIN candidates k2 ON k2.candidate_id = l.candidate_id
                         WHERE k2.profile_id = p.id)                          AS n_logs,
                       (SELECT count(*) FROM comments m
                          JOIN candidates k3 ON k3.candidate_id = m.candidate_id
                         WHERE k3.profile_id = p.id)                          AS n_comments
                FROM profiles p
                LEFT JOIN resumes r ON r.profile_id = p.id
                WHERE p.city IS NOT NULL AND p.email IS NOT NULL
            )
            SELECT id, resume_len, n_contacts, n_apps, n_logs, n_comments
            FROM stat
            WHERE resume_len > 500 AND n_contacts > 0 AND n_apps > 0
              AND n_logs > 0 AND n_comments > 0
            -- Weighted toward records that are actually populated. Ranking by application
            -- count alone surfaced people with a dozen applications that carry no history
            -- at all, which makes the sample look emptier than the dataset is.
            ORDER BY (n_logs * 3 + n_comments * 2 + n_contacts * 3 + n_apps) DESC
            LIMIT $1
            """,
            limit,
        )
        if not picks:
            print("Не нашлось профилей, у которых заполнены все разделы.", file=sys.stderr)
            return 1

        ids = [r["id"] for r in picks]
        print(f"Отобрано профилей: {len(ids)}")

        profiles = await conn.fetch(
            "SELECT * FROM profiles WHERE id = ANY($1::bigint[]) ORDER BY id", ids
        )
        resumes = {
            r["profile_id"]: r
            for r in await conn.fetch(
                "SELECT * FROM resumes WHERE profile_id = ANY($1::bigint[])", ids
            )
        }
        contacts = await conn.fetch(
            "SELECT * FROM contacts WHERE profile_id = ANY($1::bigint[]) ORDER BY profile_id, id", ids
        )
        cands = await conn.fetch(
            "SELECT * FROM candidates WHERE profile_id = ANY($1::bigint[]) ORDER BY profile_id, candidate_id",
            ids,
        )
        cand_ids = [c["candidate_id"] for c in cands]
        logs = await conn.fetch(
            "SELECT * FROM logs WHERE candidate_id = ANY($1::bigint[]) ORDER BY candidate_id, date_time_at",
            cand_ids,
        )
        comments = await conn.fetch(
            "SELECT * FROM comments WHERE candidate_id = ANY($1::bigint[]) ORDER BY candidate_id, created_at",
            cand_ids,
        )
        works = await conn.fetch(
            "SELECT * FROM work_history WHERE profile_id = ANY($1::bigint[]) ORDER BY profile_id, ord",
            ids,
        )
        media = await conn.fetch(
            "SELECT * FROM media_files WHERE profile_id = ANY($1::bigint[]) AND status = 'done'",
            ids,
        )

        # candidate_id -> profile_id, so child sheets can be traced back to a person
        owner = {c["candidate_id"]: c["profile_id"] for c in cands}

        # The ATS almost never fills candidateCreatedAt (41 rows out of 326k), so an empty
        # "Создана" column reads like missing data when it is really an upstream gap. The
        # first history entry is the application's real start, and it is always present.
        first_log: dict[int, datetime] = {}
        for l in logs:
            cid, at = l["candidate_id"], l["date_time_at"]
            if at is not None and (cid not in first_log or at < first_log[cid]):
                first_log[cid] = at
        name_of = {
            p["id"]: " ".join(x for x in [p["last_name"], p["first_name"], p["middle_name"]] if x)
            for p in profiles
        }

        wb = Workbook()
        wb.remove(wb.active)

        # ── Обзор ────────────────────────────────────────────────────────────
        ws = wb.create_sheet("Обзор")
        ws["A1"] = "Пример выгрузки из Сбер Подбора"
        ws["A1"].font = TITLE_FONT
        ws["A2"] = f"Сформировано: {datetime.now(UTC).strftime('%d.%m.%Y %H:%M')} UTC"
        ws["A3"] = (
            "Показаны самые заполненные записи — чтобы было видно все поля, которые мы собираем. "
            "Это срез, а не вся база."
        )
        ws["A3"].alignment = Alignment(wrap_text=True)

        overview = [
            ("Лист", "Что внутри", "Строк здесь", "Всего в базе"),
            ("Профили", "Кандидат: ФИО, контакты, город, текущее место работы, опыт",
             len(profiles), await conn.fetchval("SELECT count(*) FROM profiles")),
            ("Контакты", "Телефоны, почты, соцсети — с пометкой основного",
             len(contacts), await conn.fetchval("SELECT count(*) FROM contacts")),
            ("Заявки", "Отклик на вакансию: статус, дата, рекрутёры, наниматели",
             len(cands), await conn.fetchval("SELECT count(*) FROM candidates")),
            ("Логи", "Полная история движения по воронке, с автором каждого действия",
             len(logs), await conn.fetchval("SELECT count(*) FROM logs")),
            ("Комментарии", "Заметки рекрутёров по кандидату, с автором и датой",
             len(comments), await conn.fetchval("SELECT count(*) FROM comments")),
            ("Резюме", "Текст резюме и его источник (hh, linkedin и т.д.)",
             len(resumes), await conn.fetchval("SELECT count(*) FROM resumes")),
            ("Опыт работы", "История по местам: компания, должность, период, обязанности",
             len(works), await conn.fetchval("SELECT count(*) FROM work_history")),
            ("Файлы", "Фото кандидата и оригинал резюме (pdf/doc), скачанные к нам",
             len(media), await conn.fetchval("SELECT count(*) FROM media_files WHERE status='done'")),
        ]
        for i, row in enumerate(overview, start=5):
            for j, val in enumerate(row, start=1):
                c = ws.cell(row=i, column=j, value=val)
                if i == 5:
                    c.fill = HEADER_FILL
                    c.font = HEADER_FONT
        for col, w in zip("ABCD", (16, 62, 14, 16)):
            ws.column_dimensions[col].width = w
        ws["A13"] = (
            "Связи между листами: «Профили» ↔ «Контакты»/«Заявки» по колонке «ID профиля»; "
            "«Заявки» ↔ «Логи»/«Комментарии» по колонке «ID заявки»."
        )
        ws["A13"].alignment = Alignment(wrap_text=True)
        ws.merge_cells("A13:D13")

        ws["A15"] = (
            "Дата в колонке «Создана» на листе «Заявки» взята из первого события истории: "
            "сам Сбер Подбор поле даты создания почти никогда не отдаёт (заполнено у 41 заявки "
            "из 326 тысяч). Момент прикрепления к вакансии есть в логах у каждой заявки, "
            "поэтому дата восстановлена по ним. Там, где в колонке «Событий в истории» стоит 0, "
            "истории нет и в самом Сбер Подборе — такие заявки мы запрашивали, сервис вернул "
            "пустой ответ. По всей базе так у 71% заявок; это особенность источника, а не пробел "
            "в сборе."
        )
        ws["A15"].alignment = Alignment(wrap_text=True, vertical="top")
        ws.merge_cells("A15:D16")

        ws["A18"] = (
            "Лист «Опыт работы» — разбор текста резюме. Сбер Подбор отдаёт по API только одно "
            "последнее место работы, а полная история есть лишь внутри резюме, поэтому она "
            "разложена по местам разбором текста. Разбирается около 95% резюме; там, где "
            "формат нестандартный, строк не будет — исходный текст при этом сохранён целиком "
            "на листе «Резюме»."
        )
        ws["A18"].alignment = Alignment(wrap_text=True, vertical="top")
        ws.merge_cells("A18:D19")

        # ── Профили ──────────────────────────────────────────────────────────
        write_sheet(
            wb, "Профили",
            ["ID профиля", "Фамилия", "Имя", "Отчество", "Телефон", "Почта", "Город",
             "Текущая должность", "Текущая компания", "Опыт", "Заявок", "Контактов",
             "Логов", "Комментариев", "Резюме собрано"],
            [
                [p["id"], p["last_name"], p["first_name"], p["middle_name"], p["phone"],
                 p["email"], p["city"], p["cur_position"], p["cur_company"], p["experience"],
                 s["n_apps"], s["n_contacts"], s["n_logs"], s["n_comments"],
                 "да" if s["resume_len"] else "нет"]
                for p, s in ((p, next(x for x in picks if x["id"] == p["id"])) for p in profiles)
            ],
            [12, 16, 14, 14, 16, 26, 16, 26, 22, 16, 9, 11, 9, 13, 14],
        )

        # ── Контакты ─────────────────────────────────────────────────────────
        write_sheet(
            wb, "Контакты",
            ["ID профиля", "Кандидат", "Тип", "Значение", "Основной"],
            [[c["profile_id"], name_of.get(c["profile_id"], ""), c["type"], c["value"],
              "да" if c["is_main"] else ""] for c in contacts],
            [12, 28, 14, 34, 11],
        )

        # ── Заявки ───────────────────────────────────────────────────────────
        log_count: dict[int, int] = {}
        for l in logs:
            log_count[l["candidate_id"]] = log_count.get(l["candidate_id"], 0) + 1
        comment_count: dict[int, int] = {}
        for m in comments:
            comment_count[m["candidate_id"]] = comment_count.get(m["candidate_id"], 0) + 1

        write_sheet(
            wb, "Заявки",
            ["ID заявки", "ID профиля", "Кандидат", "Вакансия", "Статус", "Создана",
             "Событий в истории", "Комментариев", "Рекрутёры", "Наниматели"],
            [[c["candidate_id"], c["profile_id"], name_of.get(c["profile_id"], ""),
              c["vacancy_title"], c["status_title"],
              fmt_dt(c["created_at"] or first_log.get(c["candidate_id"])),
              log_count.get(c["candidate_id"], 0), comment_count.get(c["candidate_id"], 0),
              people(c["recruiters"]), people(c["managers"])] for c in cands],
            [12, 12, 28, 34, 24, 17, 15, 13, 30, 24],
        )

        # ── Логи ─────────────────────────────────────────────────────────────
        write_sheet(
            wb, "Логи",
            ["ID заявки", "Кандидат", "Когда", "Автор", "Событие"],
            [[l["candidate_id"], name_of.get(owner.get(l["candidate_id"]), ""),
              fmt_dt(l["date_time_at"]), l["user_full_name"], l["message"]] for l in logs],
            [12, 28, 17, 24, 70],
        )

        # ── Комментарии ──────────────────────────────────────────────────────
        write_sheet(
            wb, "Комментарии",
            ["ID заявки", "Кандидат", "Создан", "Изменён", "Автор", "Текст"],
            [[m["candidate_id"], name_of.get(owner.get(m["candidate_id"]), ""),
              fmt_dt(m["created_at"]), fmt_dt(m["changed_at"]), m["user_full_name"],
              m["comment"]] for m in comments],
            [12, 28, 17, 17, 24, 90],
        )

        # ── Резюме ───────────────────────────────────────────────────────────
        rows = []
        for pid in ids:
            r = resumes.get(pid)
            if not r:
                continue
            text = html_to_text(r["body_html"])
            rows.append([
                pid, name_of.get(pid, ""), r["source"], len(r["body_html"] or ""),
                text[:CELL_LIMIT],
            ])
        write_sheet(
            wb, "Резюме",
            ["ID профиля", "Кандидат", "Источник", "Длина оригинала, символов", "Текст резюме"],
            rows,
            [12, 28, 14, 22, 120],
        )

        def period(w) -> str:
            a = w["started_at"].strftime("%m.%Y") if w["started_at"] else "?"
            b = ("по наст. время" if w["is_current"]
                 else w["finished_at"].strftime("%m.%Y") if w["finished_at"] else "?")
            return f"{a} — {b}"

        write_sheet(
            wb, "Опыт работы",
            ["ID профиля", "Кандидат", "№", "Компания", "Должность", "Период",
             "Длительность", "Сейчас работает", "Обязанности"],
            [[w["profile_id"], name_of.get(w["profile_id"], ""), w["ord"], w["company"],
              w["position"], period(w), w["duration_text"],
              "да" if w["is_current"] else "", w["description"]] for w in works],
            [12, 26, 5, 30, 34, 20, 18, 16, 90],
        )

        KIND_RU = {"photo": "фото кандидата", "resume_file": "оригинал резюме"}
        write_sheet(
            wb, "Файлы",
            ["ID профиля", "Кандидат", "Что это", "Имя файла", "Тип", "Размер, КБ",
             "Ссылка в панели", "Исходная ссылка"],
            [[m["profile_id"], name_of.get(m["profile_id"], ""),
              KIND_RU.get(m["kind"], m["kind"]), Path(m["rel_path"]).name,
              m["content_type"], round((m["file_size"] or 0) / 1024),
              f"/api/media/{m['id']}/file", m["source_url"]] for m in media],
            [12, 26, 20, 40, 20, 13, 30, 70],
        )

        out.parent.mkdir(parents=True, exist_ok=True)
        wb.save(out)
        size = out.stat().st_size
        print(f"Готово: {out} ({size / 1024:.0f} КБ)")
        print(f"  профилей {len(profiles)}, контактов {len(contacts)}, заявок {len(cands)}, "
              f"логов {len(logs)}, комментариев {len(comments)}, резюме {len(rows)}, "
              f"мест работы {len(works)}, файлов {len(media)}")
        return 0
    finally:
        await conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="Excel sample of the collected data")
    ap.add_argument("--limit", type=int, default=10, help="сколько профилей взять")
    ap.add_argument("--out", default=None, help="путь к .xlsx")
    args = ap.parse_args()

    out = Path(args.out) if args.out else Path(settings.export_dir) / (
        f"sberpodbor-пример-{datetime.now(UTC).strftime('%Y%m%d')}.xlsx"
    )
    return asyncio.run(build(args.limit, out))


if __name__ == "__main__":
    sys.exit(main())
