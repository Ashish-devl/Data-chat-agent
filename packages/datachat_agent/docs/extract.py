"""Read a PDF's text page by page and its tables, with pdfplumber (MIT licensed)."""

import datetime as dt
import io
import re
from dataclasses import dataclass, field

import pdfplumber

MIN_TEXT_CHARS = 20  # less than this in the whole file means no text layer (a scan)


class NoTextLayerError(ValueError):
    pass


@dataclass
class ExtractedTable:
    page: int
    rows: list[list[str]]


@dataclass
class ExtractedPDF:
    pages: list[str]  # text of page 1..N
    tables: list[ExtractedTable] = field(default_factory=list)
    doc_date: dt.date | None = None

    @property
    def page_count(self) -> int:
        return len(self.pages)


def extract_pdf(data: bytes) -> ExtractedPDF:
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        pages: list[str] = []
        tables: list[ExtractedTable] = []
        for number, page in enumerate(pdf.pages, start=1):
            pages.append(page.extract_text() or "")
            for table in page.extract_tables():
                rows = [[_cell(c) for c in row] for row in table if any(row)]
                if len(rows) >= 2:
                    tables.append(ExtractedTable(number, rows))
    if sum(len(p.strip()) for p in pages) < MIN_TEXT_CHARS:
        raise NoTextLayerError(
            "The PDF has no text layer (it looks like a scan). OCR is not supported yet."
        )
    return ExtractedPDF(pages=pages, tables=tables, doc_date=find_document_date(pages[0]))


def _cell(value) -> str:
    return re.sub(r"\s+", " ", str(value)).strip() if value is not None else ""


_MONTHS = {
    m: i
    for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"],
        start=1,
    )
}
_DATE_PATTERNS = [
    (re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b"), "ymd"),
    (re.compile(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})\b"), "dmy"),
    (re.compile(r"\b(\d{1,2})[-\s]([A-Za-z]{3})[A-Za-z]*[-\s,]+(\d{4})\b"), "dMy"),
]


def _parse(match: re.Match, kind: str) -> dt.date | None:
    try:
        if kind == "ymd":
            y, m, d = map(int, match.groups())
        elif kind == "dmy":
            d, m, y = map(int, match.groups())
        else:
            d, mon, y = match.groups()
            d, y, m = int(d), int(y), _MONTHS.get(mon[:3].lower(), 0)
        return dt.date(y, m, d)
    except (ValueError, TypeError):
        return None


def find_document_date(text: str) -> dt.date | None:
    """The document's own date: one on a line mentioning 'date' first, else the first date."""
    lines = text.splitlines()
    ordered = [ln for ln in lines if "date" in ln.lower()] + lines
    for line in ordered:
        for pattern, kind in _DATE_PATTERNS:
            for match in pattern.finditer(line):
                parsed = _parse(match, kind)
                if parsed:
                    return parsed
    return None
