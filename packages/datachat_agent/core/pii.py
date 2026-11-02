"""Optional masking: personal data is swapped for placeholders before it reaches the model.

The model sees e.g. [EMAIL_1] or [PHONE_2]; the real values are put back into its answer, so the
user still sees them. Only text values are masked; numbers in numeric columns are left alone.
"""

import re
from typing import Any

# Order matters: the more specific patterns run first.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+(\.[\w-]+)+\b")),
    ("CARD", re.compile(r"\b(?:\d[ -]?){13,19}\b")),
    ("AADHAAR", re.compile(r"(?<!\d)(?<!\d )(?<!\d-)\d{4}[ -]\d{4}[ -]\d{4}(?![ -]?\d)")),
    ("PAN", re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")),
    ("PHONE", re.compile(r"(?<![\w.])(?<!\d )(?<!\d-)\+?\d[\d ()-]{8,14}\d\b(?![ -]?\d)")),
]
_TOKEN = re.compile(r"\[(EMAIL|CARD|AADHAAR|PAN|PHONE)_\d+\]")


def _luhn(digits: str) -> bool:
    total, parity = 0, len(digits) % 2
    for i, ch in enumerate(digits):
        d = int(ch)
        if i % 2 == parity:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


class PiiMasker:
    def __init__(self) -> None:
        self._to_token: dict[str, str] = {}
        self._to_value: dict[str, str] = {}
        self._counts: dict[str, int] = {}

    @property
    def masked_count(self) -> int:
        return len(self._to_value)

    def _token(self, kind: str, value: str) -> str:
        if value not in self._to_token:
            self._counts[kind] = self._counts.get(kind, 0) + 1
            token = f"[{kind}_{self._counts[kind]}]"
            self._to_token[value] = token
            self._to_value[token] = value
        return self._to_token[value]

    def mask_text(self, text: str) -> str:
        for kind, pattern in _PATTERNS:

            def replace(m: re.Match[str], kind: str = kind) -> str:
                value = m.group(0)
                digits = re.sub(r"\D", "", value)
                if kind == "CARD" and not _luhn(digits):
                    return value
                if kind == "PHONE" and len(digits) < 10:  # dates, short codes
                    return value
                return self._token(kind, value)

            text = pattern.sub(replace, text)
        return text

    def mask(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.mask_text(value)
        if isinstance(value, list):
            return [self.mask(v) for v in value]
        if isinstance(value, dict):
            return {k: self.mask(v) for k, v in value.items()}
        return value

    def unmask(self, text: str) -> str:
        return _TOKEN.sub(lambda m: self._to_value.get(m.group(0), m.group(0)), text)


class StreamUnmasker:
    """Puts real values back into streamed text without ever splitting a placeholder."""

    def __init__(self, masker: PiiMasker | None) -> None:
        self._masker = masker
        self._pending = ""

    def feed(self, chunk: str) -> str:
        if self._masker is None:
            return chunk
        self._pending += chunk
        cut = self._pending.rfind("[")
        if cut != -1 and "]" not in self._pending[cut:] and len(self._pending) - cut < 20:
            ready, self._pending = self._pending[:cut], self._pending[cut:]
        else:
            ready, self._pending = self._pending, ""
        return self._masker.unmask(ready)

    def flush(self) -> str:
        if self._masker is None:
            return ""
        ready, self._pending = self._pending, ""
        return self._masker.unmask(ready)
