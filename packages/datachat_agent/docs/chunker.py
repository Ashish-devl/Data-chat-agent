"""Split page text into ~800-token passages that never cross a page and remember their heading."""

import re
from dataclasses import dataclass

CHARS_PER_TOKEN = 4  # a close-enough estimate for English business text
CHUNK_TOKENS = 800
OVERLAP_TOKENS = 100
_NUMBERED = re.compile(r"^(\d+(\.\d+)*[.)]?|[A-Z][.)])\s+\S")


@dataclass
class Chunk:
    page: int
    index: int
    text: str
    heading: str | None


def is_heading(line: str) -> bool:
    line = line.strip()
    if not (3 <= len(line) <= 80) or line.endswith((".", ",", ";", ":")):
        return False
    words = line.split()
    if len(words) > 10:
        return False
    if _NUMBERED.match(line):
        return True
    letters = [c for c in line if c.isalpha()]
    if letters and all(c.isupper() for c in letters) and len(letters) >= 3:
        return True
    caps = sum(1 for w in words if w[0].isupper() or not w[0].isalpha())
    return len(words) >= 2 and caps == len(words)


def chunk_pages(
    pages: list[str], chunk_tokens: int = CHUNK_TOKENS, overlap_tokens: int = OVERLAP_TOKENS
) -> list[Chunk]:
    size, overlap = chunk_tokens * CHARS_PER_TOKEN, overlap_tokens * CHARS_PER_TOKEN
    chunks: list[Chunk] = []
    heading: str | None = None  # carries over to the next page until a new one appears

    for page_number, text in enumerate(pages, start=1):
        lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
        current: list[str] = []
        current_heading = heading
        length = 0

        for line in lines:
            for piece in _split_long(line, size):
                if is_heading(piece):
                    heading = piece.strip()
                    if not current:
                        current_heading = heading
                # Never leave a heading (or a tiny scrap) alone in its own passage.
                if length + len(piece) + 1 > size and length > size // 4:
                    _emit(chunks, page_number, current, current_heading)
                    tail: list[str] = []
                    tail_len = 0
                    for prev in reversed(current):
                        if tail_len + len(prev) > overlap:
                            break
                        tail.insert(0, prev)
                        tail_len += len(prev) + 1
                    current, length, current_heading = tail, tail_len, heading
                current.append(piece)
                length += len(piece) + 1
        _emit(chunks, page_number, current, current_heading)
    return chunks


def _emit(chunks: list[Chunk], page: int, lines: list[str], heading: str | None) -> None:
    if lines:
        chunks.append(Chunk(page, len(chunks), "\n".join(lines), heading))


def _split_long(line: str, size: int) -> list[str]:
    """A single line longer than a chunk is cut at sentence or word boundaries."""
    if len(line) <= size:
        return [line]
    parts, rest = [], line
    while len(rest) > size:
        cut = max(rest.rfind(". ", 0, size), rest.rfind(" ", 0, size))
        cut = cut + 1 if cut > size // 2 else size
        parts.append(rest[:cut].strip())
        rest = rest[cut:].strip()
    if rest:
        parts.append(rest)
    return parts
