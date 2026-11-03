from eval.scoring import facts_present, grade, is_refusal, rows_match, summarise


def test_rows_match_ignores_order_extra_columns_and_number_format():
    truth = [{"name": "Orbit Labels", "sum": 272558.5}, {"name": "Apex", "sum": 10}]
    model = [
        {"supplier_id": 9, "supplier": "Apex", "total": "10.00"},
        {"supplier_id": 5, "supplier": "Orbit Labels", "total": 272558.50},
    ]
    assert rows_match(model, truth)


def test_rows_match_rejects_wrong_values_and_row_counts():
    truth = [{"line_no": 4, "count": 4}, {"line_no": 1, "count": 2}]
    assert not rows_match([{"l": 4, "c": 4}, {"l": 1, "c": 3}], truth)
    assert not rows_match([{"l": 4, "c": 4}], truth)
    # duplicate values must be counted, not collapsed (line 4 with 4 orders)
    assert not rows_match([{"l": 4, "c": 1}, {"l": 1, "c": 2}], truth)


def test_facts_present_normalises_numbers_and_allows_alternatives():
    answer = "The grand total is INR 7,73,550.00, due by 30 August 2026."
    assert facts_present(answer, ["773550", ["30-08-2026", "30 August 2026"]])
    assert not facts_present(answer, ["773551"])
    assert facts_present("Use AQL 2.5 for MAJOR defects", ["aql 2.5", "major"])


def test_refusal_detection():
    assert is_refusal("I don't know. The connected data doesn't contain that.", [], [])
    assert is_refusal("No data source is connected yet, so I can't answer that.", [], [])
    assert not is_refusal("There are 6 buyers.", [{"n": 6}], [])
    assert not is_refusal("I don't know exactly, but here are rows", [{"n": 6}], [])


def answer(**kw):
    base = {"answer": "", "rows": [], "citations": [], "tables_considered": [], "latency_ms": 1000}
    return {**base, **kw}


def test_grade_database_question():
    q = {"kind": "database", "sql": "x", "tables": ["buyers"]}
    g = grade(q, answer(rows=[{"count": 6}], tables_considered=["public.buyers"]), [{"c": 6}])
    assert g == {"sql_ok": True, "schema_ok": True}


def test_grade_document_question_and_citation_precision():
    q = {"kind": "documents", "expect": ["773550"], "cite": [["inv.pdf", 1]]}
    a = answer(
        answer="Total INR 773,550 [1] [2]",
        citations=[{"filename": "inv.pdf", "page": 1}, {"filename": "other.pdf", "page": 3}],
    )
    assert grade(q, a, None) == {"facts_ok": True, "citations": 2, "citations_ok": 1}


def test_summary_and_targets():
    results = [
        {"grade": {"sql_ok": True, "schema_ok": True}, "answer": answer(latency_ms=2000)},
        {"grade": {"sql_ok": False, "schema_ok": True}, "answer": answer(latency_ms=6000)},
        {"grade": {"facts_ok": True, "citations": 2, "citations_ok": 2}, "answer": answer()},
        {"grade": {"refused": True}, "answer": answer(latency_ms=3000)},
    ]
    s = summarise(results)
    assert s["sql_accuracy"]["value"] == 0.5 and not s["sql_accuracy"]["met"]
    assert s["schema_recall"]["met"] and s["citation_precision"]["value"] == 1.0
    assert s["median_latency_s"]["value"] == 2.5 and s["median_latency_s"]["met"]
