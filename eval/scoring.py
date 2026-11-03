"""How each answer is graded, and the six numbers the plan sets targets for."""

import re
import statistics
from collections import Counter
from typing import Any

TARGETS = {
    "sql_accuracy": 0.80,
    "schema_recall": 0.95,
    "document_accuracy": 0.85,
    "citation_precision": 0.90,
    "refusal_correctness": 0.90,
    "median_latency_s": 4.0,  # lower is better
}
LABELS = {
    "sql_accuracy": "SQL execution accuracy",
    "schema_recall": "Schema retrieval recall",
    "document_accuracy": "Document answer accuracy",
    "citation_precision": "Citation precision",
    "refusal_correctness": "Refusal correctness",
    "median_latency_s": "Median latency (s)",
}

DONT_KNOW = re.compile(
    r"\b(i don'?t know|i do not know|no data source|doesn'?t contain|does not contain|not "
    r"(available|included|found|mentioned)|no (information|data|record))\b",
    re.I,
)


def _value(v: Any) -> str:
    if isinstance(v, bool) or v is None:
        return str(v)
    if isinstance(v, (int, float)):
        return f"{round(float(v), 2):g}"
    text = str(v).strip()
    try:
        return f"{round(float(text), 2):g}"
    except ValueError:
        return text.lower()


def rows_match(actual: list[dict], expected: list[dict]) -> bool:
    """Same rows in any order; each expected row's values all appear in its matching row.

    Extra columns the model chose to add (an id, a label) are fine; missing or wrong values,
    and extra or missing rows, are not. Numbers are compared to two decimals.
    """
    if len(actual) != len(expected):
        return False
    remaining = [Counter(_value(v) for v in r.values()) for r in actual]
    for row in expected:
        want = Counter(_value(v) for v in row.values())
        hit = next((i for i, have in enumerate(remaining) if not (want - have)), None)
        if hit is None:
            return False
        remaining.pop(hit)
    return True


def normalise(text: str) -> str:
    t = text.lower().replace(" ", " ").replace("\xa0", " ")
    t = re.sub(r"(?<=\d),(?=\d)", "", t)  # 4,94,400.00 -> 494400.00
    return re.sub(r"\s+", " ", t)


def facts_present(answer: str, expect: list) -> bool:
    """Every expected fact appears; a list inside `expect` means any one of its forms."""
    text = normalise(answer)
    for fact in expect:
        options = fact if isinstance(fact, list) else [fact]
        if not any(normalise(str(o)) in text for o in options):
            return False
    return True


def is_refusal(answer: str, rows: list, citations: list) -> bool:
    return bool(DONT_KNOW.search(answer or "")) and not rows


def grade(q: dict, a: dict, truth_rows: list[dict] | None) -> dict[str, Any]:
    """Per-question verdicts; a key is absent when that measure does not apply."""
    g: dict[str, Any] = {}
    kind = q["kind"]
    if kind == "unanswerable":
        g["refused"] = is_refusal(a["answer"], a["rows"], a["citations"])
        return g
    if q.get("sql") is not None:
        g["sql_ok"] = truth_rows is not None and rows_match(a["rows"], truth_rows)
    if q.get("tables"):
        seen = {t.split(".")[-1].lower() for t in a.get("tables_considered") or []}
        # A question answered without SQL never had tables chosen; score it as a miss.
        g["schema_ok"] = set(q["tables"]) <= seen
    if q.get("expect"):
        g["facts_ok"] = facts_present(a["answer"], q["expect"])
    if q.get("cite"):
        allowed = {(f, p) for f, p in q["cite"]}
        cited = [(c["filename"], c["page"]) for c in a["citations"]]
        g["citations"] = len(cited)
        g["citations_ok"] = sum(1 for c in cited if c in allowed)
    return g


def summarise(results: list[dict]) -> dict[str, Any]:
    def ratio(key: str) -> tuple[float | None, int]:
        vals = [r["grade"][key] for r in results if key in r["grade"]]
        return (sum(vals) / len(vals) if vals else None), len(vals)

    sql, n_sql = ratio("sql_ok")
    schema, n_schema = ratio("schema_ok")
    facts, n_facts = ratio("facts_ok")
    refused, n_refused = ratio("refused")
    cited = sum(r["grade"].get("citations", 0) for r in results)
    cited_ok = sum(r["grade"].get("citations_ok", 0) for r in results)
    latencies = [r["answer"]["latency_ms"] / 1000 for r in results if r["answer"].get("latency_ms")]
    metrics = {
        "sql_accuracy": (sql, n_sql),
        "schema_recall": (schema, n_schema),
        "document_accuracy": (facts, n_facts),
        "citation_precision": (cited_ok / cited if cited else None, cited),
        "refusal_correctness": (refused, n_refused),
        "median_latency_s": (statistics.median(latencies) if latencies else None, len(latencies)),
    }
    return {
        name: {
            "value": value,
            "n": n,
            "target": TARGETS[name],
            "met": value is not None
            and (value <= TARGETS[name] if name == "median_latency_s" else value >= TARGETS[name]),
        }
        for name, (value, n) in metrics.items()
    }


def table(summary: dict[str, Any]) -> str:
    lines = ["| Metric | Result | Target | Met | Questions |", "| --- | --- | --- | --- | --- |"]
    for name, m in summary.items():
        v, t = m["value"], m["target"]
        if name == "median_latency_s":
            shown, goal = ("–" if v is None else f"{v:.1f} s"), f"under {t:.0f} s"
        else:
            shown, goal = ("–" if v is None else f"{v:.0%}"), f"{t:.0%}"
        lines.append(
            f"| {LABELS[name]} | {shown} | {goal} | {'yes' if m['met'] else 'no'} | {m['n']} |"
        )
    return "\n".join(lines)
