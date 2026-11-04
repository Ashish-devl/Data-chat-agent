import pytest

from datachat_agent.sql.generator import parse_response


@pytest.mark.parametrize(
    "reply",
    [
        "NO_SQL: there is no ticket data",
        "**NO_SQL:** there is no ticket data",
        "```sql\nNO_SQL: there is no ticket data\n```",
        "I can't answer that. NO_SQL: there is no ticket data",
        "no_sql: there is no ticket data",
    ],
)
def test_refusals_in_any_wrapping_are_not_run(reply):
    result = parse_response(reply)
    assert result.sql is None
    assert result.reason == "there is no ticket data"


@pytest.mark.parametrize(
    ("reply", "sql"),
    [
        ("```sql\nSELECT 1\n```", "SELECT 1"),
        ("SELECT 1;", "SELECT 1"),
        ("```\nWITH a AS (SELECT 1) SELECT * FROM a\n```", "WITH a AS (SELECT 1) SELECT * FROM a"),
        ("```sql\n-- buyers\nSELECT COUNT(*) FROM b\n```", "-- buyers\nSELECT COUNT(*) FROM b"),
        ("(SELECT 1) UNION (SELECT 2)", "(SELECT 1) UNION (SELECT 2)"),
    ],
)
def test_queries_are_kept(reply, sql):
    assert parse_response(reply).sql == sql


def test_prose_is_never_run_as_sql():
    result = parse_response("The schema has no information about concerts.")
    assert result.sql is None and "concerts" in result.reason


def test_empty_reply():
    assert parse_response("  ").reason == "empty response"
