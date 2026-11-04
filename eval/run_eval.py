"""Run the 100-question evaluation against the real model and score the six targets.

    python -m eval.run_eval                   # load data, ask everything, write the report
    python -m eval.run_eval --resume          # continue where a previous run stopped
    python -m eval.run_eval --only doc-03,erp-14 --skip-load
    python -m eval.run_eval --redo doc-03,erp-14  # ask these again, keep the other answers

Uses the LLM_* settings from .env. Answers are saved after every question to
eval/results/answers.json, so a run stopped by a provider's daily limit continues next time
with --resume. The summary goes to eval/results/summary.md and is printed.
"""

import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eval import scoring  # noqa: E402

QUESTIONS = ROOT / "eval" / "datasets" / "questions.json"
RESULTS = ROOT / "eval" / "results"
PDF_DIR = Path.home() / ".cache" / "datachat-agent" / "eval-pdfs"
ERP = "postgresql://erp_readonly:erp_readonly@{host}/erp_demo"
CHINOOK = "postgresql://chinook_readonly:chinook_readonly@{host}/chinook"
TERMS = [
    ("open PO", "a purchase order whose status is Open or Partially Received"),
    ("late order", "a production order whose status is Delayed"),
]


def load_data(admin_url: str) -> None:
    from eval.datasets import chinook, erp_demo

    erp_demo.load(admin_url)
    chinook.load(admin_url)


def make_pdfs() -> list[Path]:
    from eval.datasets.make_pdfs import make_all
    from eval.datasets.more_pdfs import make_more

    return sorted({**make_all(PDF_DIR), **make_more(PDF_DIR)}.values())


def _remove_old_eval_accounts() -> None:
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    from datachat_agent.config import get_settings

    url = make_url(get_settings().database_url).set(drivername="postgresql+psycopg")
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM tenants WHERE name LIKE 'library:eval-%'"))
    engine.dispose()


def setup(host: str, fresh: bool):
    """Two library instances: the garment ERP with all PDFs, and Chinook on its own."""
    from datachat_agent import DataChat

    if fresh:
        _remove_old_eval_accounts()
    erp = DataChat(ERP.format(host=host), name="eval-erp")
    chn = DataChat(CHINOOK.format(host=host), name="eval-chinook")
    print("Scanning schemas ...", flush=True)
    erp.scan_schema()
    chn.scan_schema()
    for table, column in [("employees", "salary_inr"), ("employees", "phone")]:
        erp.hide(table, column)  # what a careful admin would hide
    for term, definition in TERMS:
        try:
            erp.add_term(term, definition)
        except Exception:  # noqa: BLE001 - already added on a resumed run
            pass
    print("Uploading PDFs ...", flush=True)
    for pdf in make_pdfs():
        info = erp.add_pdf(pdf)
        if info.created:
            print(f"  {pdf.name}: {info.status}{' - ' + info.error if info.error else ''}")
    return {"erp": erp, "chinook": chn}


def truth_rows(q: dict, host: str) -> list[dict] | None:
    from datachat_agent.sql.connector import get_engine
    from datachat_agent.sql.executor import execute_readonly

    if not q.get("sql"):
        return None
    dsn = (CHINOOK if q["set"] == "chinook" else ERP).format(host=host)
    return execute_readonly(get_engine(dsn), "postgresql", q["sql"]).rows


class QuotaExhausted(RuntimeError):
    pass


def ask(dc, question: str, retries: int, wait: float) -> dict:
    for attempt in range(retries + 1):
        a = dc.ask(question)
        unavailable = a.error and any(
            w in a.error for w in ("overloaded", "rate limit", "did not respond", "quota")
        )
        if not unavailable or attempt == retries:
            return {
                "answer": a.answer,
                "route": a.route,
                "sql": a.sql,
                "rows": a.rows,
                "citations": [{"filename": c["filename"], "page": c["page"]} for c in a.citations],
                "tables_considered": a.tables_considered,
                "tokens": a.tokens,
                "latency_ms": a.latency_ms,
                "error": a.error,
                "attempts": attempt + 1,
            }
        if "quota" in a.error:
            # A daily quota does not come back in a minute: stop and resume another time.
            raise QuotaExhausted(a.error)
        print(f"    model unavailable ({a.error}); waiting {wait:.0f}s", flush=True)
        time.sleep(wait)
    raise AssertionError("unreachable")


def model_line() -> str:
    """Which model answered: scores belong to a model, not to DataChat Agent alone."""
    from datachat_agent.config import get_settings

    s = get_settings()
    backups = f" (backups: {', '.join(s.fallback_models)})" if s.fallback_models else ""
    return f"Model: {s.llm_model}{backups}."


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument(
        "--admin-url",
        default="postgresql://datachat:datachat@localhost:5432/postgres",
        help="Postgres superuser URL, used only to load the sample databases",
    )
    ap.add_argument("--db-host", default="localhost:5432")
    ap.add_argument("--skip-load", action="store_true", help="sample databases already loaded")
    ap.add_argument("--resume", action="store_true", help="keep earlier answers, ask the rest")
    ap.add_argument("--only", default="", help="comma-separated question ids")
    ap.add_argument(
        "--redo",
        default="",
        help="comma-separated question ids to ask again; every other saved answer is kept",
    )
    ap.add_argument("--delay", type=float, default=4.0, help="seconds between questions")
    ap.add_argument("--retries", type=int, default=2)
    ap.add_argument("--retry-wait", type=float, default=60.0)
    args = ap.parse_args()

    questions = json.loads(QUESTIONS.read_text(encoding="utf-8"))["questions"]
    if args.only:
        wanted = set(args.only.split(","))
        questions = [q for q in questions if q["id"] in wanted]
    RESULTS.mkdir(parents=True, exist_ok=True)
    answers_path = RESULTS / "answers.json"
    done: dict[str, dict] = {}
    redo = {i for i in args.redo.split(",") if i}
    if redo:
        args.resume = True
    if args.resume and answers_path.exists():
        done = {r["id"]: r for r in json.loads(answers_path.read_text(encoding="utf-8"))}
        for i in redo:
            done.pop(i, None)

    if not args.skip_load and not args.resume:
        print("Loading sample databases ...")
        load_data(args.admin_url)
    instances = setup(args.db_host, fresh=not args.resume and not args.skip_load)

    stopped = None
    try:
        for i, q in enumerate(questions, 1):
            if q["id"] in done and done[q["id"]]["answer"].get("error") is None:
                continue
            dc = instances["chinook" if q["set"] == "chinook" else "erp"]
            try:
                a = ask(dc, q["question"], args.retries, args.retry_wait)
            except QuotaExhausted as e:
                stopped = str(e)
                break
            g = scoring.grade(q, a, truth_rows(q, args.db_host))
            done[q["id"]] = {
                "id": q["id"],
                "kind": q["kind"],
                "question": q["question"],
                "answer": a,
                "grade": g,
            }
            marks = " ".join(f"{k}={v}" for k, v in g.items())
            print(
                f"[{i:3}/{len(questions)}] {q['id']:8} {a['latency_ms'] / 1000:5.1f}s {marks}",
                flush=True,
            )
            answers_path.write_text(
                json.dumps(list(done.values()), indent=1, default=str), encoding="utf-8"
            )
            time.sleep(args.delay)
    finally:
        for dc in instances.values():
            dc.close()

    if stopped:
        left = sum(1 for q in questions if q["id"] not in done)
        print(f"\nStopped: {stopped}\n{left} questions left. Run again later with --resume.")
    results = [done[q["id"]] for q in questions if q["id"] in done]
    summary = scoring.summarise(results)
    report = (
        f"# Evaluation results\n\nRun {dt.date.today().isoformat()}, "
        f"{len(results)} questions. {model_line()}\n\n{scoring.table(summary)}\n"
    )
    misses = [
        r for r in results if not all(v for k, v in r["grade"].items() if isinstance(v, bool))
    ]
    if misses:
        lines = []
        for r in misses:
            failed = ", ".join(k for k, v in r["grade"].items() if v is False)
            lines.append(f"- **{r['id']}** {r['question']} — {failed}")
        report += "\n## Misses\n\n" + "\n".join(lines) + "\n"
    (RESULTS / "summary.md").write_text(report, encoding="utf-8")
    (RESULTS / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print("\n" + report)


if __name__ == "__main__":
    main()
