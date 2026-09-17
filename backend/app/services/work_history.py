"""Extract per-job work history out of the resume HTML.

Сбер Подбор normalises every imported resume (hh, linkedin, career.habr, …) into the same
flat markup, so one parser covers all sources:

    <h2>Опыт работы</h2>
    <p><strong>Компания</strong></p>          ← may be wrapped in <a href="...">
    <p><strong>Должность</strong></p>
    <p>февраль 2018 - август 2019 (1 год 7 месяцев)</p>
    <p>описание…</p>                          ← zero or more
    <p><br></p>                               ← optional separator
    …next entry…
    <h2>Образование</h2>                      ← section ends here

There are no classes or data attributes to key on, so the parser walks the section: a pair of
consecutive bold lines opens an entry (company, then position), a date line fills in the
period, and everything else is description. A small minority of resumes are a raw LinkedIn
paste instead — no bold at all, position above "Company · Full-time" — which the same walk
handles via the employment-type marker. Anything that fits neither shape is skipped rather
than guessed at.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from lxml import html as lxml_html

MONTHS = {
    "январь": 1, "января": 1, "февраль": 2, "февраля": 2, "март": 3, "марта": 3,
    "апрель": 4, "апреля": 4, "май": 5, "мая": 5, "июнь": 6, "июня": 6,
    "июль": 7, "июля": 7, "август": 8, "августа": 8, "сентябрь": 9, "сентября": 9,
    "октябрь": 10, "октября": 10, "ноябрь": 11, "ноября": 11, "декабрь": 12, "декабря": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

_MONTH_ALT = "|".join(sorted(MONTHS, key=len, reverse=True))
_PRESENT = r"по\s+настоящее\s+время|настоящее\s+время|present|current|now|н\.?\s*в\.?"

# "февраль 2018 - август 2019 (1 год 7 месяцев)" / "май 2019 - по настоящее время (1 год 8 месяцев)"
DATE_RANGE = re.compile(
    rf"^\s*(?P<m1>{_MONTH_ALT})\s+(?P<y1>\d{{4}})\s*[-–—]{{1,2}}\s*"
    rf"(?:(?P<present>{_PRESENT})|(?P<m2>{_MONTH_ALT})\s+(?P<y2>\d{{4}}))"
    rf"\s*(?:\((?P<dur>[^)]*)\)|[·,]\s*(?P<dur2>[^·]*?))?\s*$",
    re.IGNORECASE,
)

# Headings that end the experience section.
SECTION_END = re.compile(
    r"образован|обучен|навык|skills|education|о себе|about|сертифик|курс|язык|"
    r"портфолио|рекомендац|достижен|дополнительн",
    re.IGNORECASE,
)
SECTION_START = re.compile(r"опыт\s+работы|work\s+experience|^experience$", re.IGNORECASE)

# Raw LinkedIn paste: "Acme Corp · Full-time" on the company line, position on the line above.
EMPLOYMENT = re.compile(
    r"^(?P<company>.+?)\s+·\s+(Full-time|Part-time|Freelance|Internship|Contract|"
    r"Self-employed|Seasonal|Apprenticeship|Полная занятость|Частичная занятость)",
    re.IGNORECASE,
)


@dataclass
class Job:
    company: str | None = None
    position: str | None = None
    started_at: date | None = None
    finished_at: date | None = None       # None + is_current=True means "по настоящее время"
    is_current: bool = False
    duration_text: str | None = None
    description: str | None = None
    order: int = 0


@dataclass
class Block:
    text: str
    bold: bool
    heading: bool


def _blocks(fragment) -> list[Block]:
    """Flatten the resume into block-level chunks, remembering which were fully bold."""
    out: list[Block] = []
    for el in fragment.iter():
        tag = (el.tag if isinstance(el.tag, str) else "").lower()
        if tag not in ("p", "h1", "h2", "h3", "h4", "li", "div"):
            continue
        text = " ".join(el.text_content().split())
        if not text:
            continue
        # Skip containers whose text is just their children's; keep the innermost block.
        if tag == "div" and len(el) and all(
            (c.tag if isinstance(c.tag, str) else "") in ("p", "h1", "h2", "h3", "div", "ul")
            for c in el
        ):
            continue
        bold_text = " ".join(
            " ".join(b.text_content().split()) for b in el.iter("strong", "b")
        ).strip()
        out.append(
            Block(
                text=text,
                bold=bool(bold_text) and bold_text == text,
                heading=tag in ("h1", "h2", "h3", "h4"),
            )
        )
    return out


def _to_date(month: str, year: str) -> date | None:
    m = MONTHS.get(month.strip().lower())
    if not m:
        return None
    try:
        return date(int(year), m, 1)
    except ValueError:
        return None


def parse_work_history(body_html: str | None) -> list[Job]:
    """Return the jobs found in a resume, in the order they appear. Never raises."""
    if not body_html or "<" not in body_html:
        return []
    try:
        fragment = lxml_html.fragment_fromstring(body_html, create_parent="div")
    except Exception:  # noqa: BLE001 - malformed HTML is expected in this dataset
        return []

    blocks = _blocks(fragment)

    # Locate the experience section: from its heading to the next unrelated heading.
    start = None
    for i, b in enumerate(blocks):
        if b.heading and SECTION_START.search(b.text):
            start = i + 1
            break
    if start is None:
        return []

    end = len(blocks)
    for i in range(start, len(blocks)):
        if blocks[i].heading and SECTION_END.search(blocks[i].text):
            end = i
            break
    section = blocks[start:end]

    return _walk(section)


def _walk(section: list[Block]) -> list[Job]:
    """Sequential pass.

    Anchoring purely on the date line loses entries that carry no dates at all, and the raw
    LinkedIn paste has no bold text and lists the position before the company. Walking the
    section keeps both shapes working.
    """
    jobs: list[Job] = []
    cur: Job | None = None
    desc: list[str] = []
    bold_run: list[str] = []
    prev_plain: str | None = None

    def flush() -> None:
        nonlocal cur, desc
        if cur is not None:
            text = chr(10).join(d for d in desc if d).strip()
            cur.description = text or None
            if cur.company or cur.position:
                cur.order = len(jobs) + 1
                jobs.append(cur)
        cur, desc[:] = None, []

    def start(company: str | None, position: str | None) -> None:
        nonlocal cur
        flush()
        cur = Job(company=company, position=position)

    for blk in section:
        if blk.bold:
            bold_run.append(blk.text)
            # Two bold lines in a row are the company/position pair of a new entry.
            if len(bold_run) == 2:
                start(bold_run[0], bold_run[1])
            elif len(bold_run) > 2 and cur is not None:
                desc.append(blk.text)
            prev_plain = None
            continue

        bold_run.clear()

        emp = EMPLOYMENT.match(blk.text)
        if emp:
            # Raw-paste entry: the previous plain line was the position.
            start(emp.group("company").strip(), prev_plain)
            prev_plain = None
            continue

        m = DATE_RANGE.match(blk.text)
        if m:
            if cur is None:
                cur = Job()
            cur.started_at = _to_date(m.group("m1"), m.group("y1"))
            if m.group("present"):
                cur.is_current = True
            elif m.group("m2"):
                cur.finished_at = _to_date(m.group("m2"), m.group("y2"))
            dur = (m.group("dur") or m.group("dur2") or "").strip()
            cur.duration_text = dur or None
            prev_plain = None
            continue

        if cur is not None:
            desc.append(blk.text)
        prev_plain = blk.text

    flush()
    return jobs
