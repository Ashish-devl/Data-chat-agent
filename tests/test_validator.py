import pytest

from datachat_agent.sql.validator import (
    SchemaPolicy,
    SQLValidationError,
    TableRef,
    validate_sql,
)

ORDERS = TableRef("public", "orders")
CUSTOMERS = TableRef("public", "customers")
EMPLOYEES = TableRef("public", "employees")
SALES_ORDERS = TableRef("sales", "orders")
POLICY = SchemaPolicy(
    tables={ORDERS, CUSTOMERS, EMPLOYEES},
    hidden_columns={EMPLOYEES: {"salary", "phone"}},
)


def ok(sql: str, dialect: str = "postgresql", policy: SchemaPolicy = POLICY, **kw):
    return validate_sql(sql, dialect, policy, **kw)


def refused(sql: str, dialect: str = "postgresql", policy: SchemaPolicy = POLICY, **kw) -> str:
    with pytest.raises(SQLValidationError) as e:
        validate_sql(sql, dialect, policy, **kw)
    return str(e.value)


# --- Must be REFUSED --------------------------------------------------------------------

REJECT_PG = [
    # Writes and DDL
    "INSERT INTO orders (id) VALUES (1)",
    "UPDATE orders SET status = 'Closed'",
    "DELETE FROM orders",
    "DROP TABLE orders",
    "TRUNCATE orders",
    "ALTER TABLE orders ADD COLUMN x int",
    "CREATE TABLE x (id int)",
    "CREATE TABLE x AS SELECT * FROM orders",
    "CREATE INDEX i ON orders (id)",
    "CREATE VIEW v AS SELECT * FROM orders",
    "MERGE INTO orders o USING customers c ON o.id = c.id WHEN MATCHED THEN DELETE",
    "GRANT ALL ON orders TO public",
    # Multiple statements, including ones hidden after comments
    "SELECT 1; DROP TABLE orders",
    "SELECT * FROM orders; DELETE FROM orders",
    "SELECT 1 -- harmless\n; DELETE FROM orders",
    "SELECT 1; SELECT 2",
    # Writes hidden inside a SELECT
    "WITH d AS (DELETE FROM orders RETURNING *) SELECT * FROM d",
    "WITH u AS (UPDATE orders SET status = 'x' RETURNING id) SELECT id FROM u",
    "WITH i AS (INSERT INTO orders (id) VALUES (1) RETURNING id) SELECT id FROM i",
    "SELECT * INTO new_table FROM orders",
    "SELECT * FROM orders FOR UPDATE",
    "SELECT * FROM orders FOR SHARE",
    # Commands, session and transaction control
    "COPY orders TO '/tmp/out.csv'",
    "COPY orders FROM '/tmp/in.csv'",
    "SET ROLE postgres",
    "SET statement_timeout = 0",
    "BEGIN",
    "COMMIT",
    "EXPLAIN ANALYZE DELETE FROM orders",
    "VACUUM orders",
    "CALL some_procedure()",
    "DO $$ BEGIN DELETE FROM orders; END $$",
    "LISTEN x",
    # Dangerous functions
    "SELECT pg_sleep(10)",
    "SELECT id FROM orders WHERE pg_sleep(1) IS NOT NULL",
    "SELECT pg_read_file('/etc/passwd')",
    "SELECT pg_ls_dir('.')",
    "SELECT lo_import('/etc/passwd')",
    "SELECT dblink('host=evil', 'select 1')",
    "SELECT * FROM dblink('host=x', 'select 1') AS t(a int)",
    "SELECT pg_terminate_backend(123)",
    "SELECT set_config('statement_timeout', '0', false)",
    "SELECT current_setting('data_directory')",
    "SELECT nextval('orders_id_seq')",
    "SELECT query_to_xml('select * from employees', true, true, '')",
    "SELECT pg_advisory_lock(1)",
    "SELECT txid_current()",
    # Tables outside the allowlist, system catalogs, table functions
    "SELECT * FROM secrets",
    "SELECT * FROM pg_catalog.pg_user",
    "SELECT * FROM pg_shadow",
    "SELECT * FROM information_schema.tables",
    "SELECT * FROM sales.orders",
    "SELECT * FROM generate_series(1, 10)",
    "SELECT o.id FROM orders o JOIN secrets s ON s.id = o.id",
    "SELECT id FROM orders WHERE id IN (SELECT id FROM secrets)",
    "SELECT * FROM otherdb.public.orders",
    # Hidden columns
    "SELECT salary FROM employees",
    "SELECT e.salary FROM employees e",
    "SELECT name FROM employees ORDER BY salary DESC",
    "SELECT AVG(salary) FROM employees",
    "SELECT * FROM employees",
    "SELECT e.* FROM employees e",
    "SELECT * FROM orders o JOIN employees e ON e.id = o.id",
    "SELECT * FROM (SELECT * FROM employees) x",
    "SELECT name FROM employees WHERE phone LIKE '9%'",
    "SELECT o.id, e.phone FROM orders o JOIN employees e ON e.id = o.id",
    # Not SQL / empty
    "",
    "hello world",
    "SELEC * FROM orders",
]

REJECT_MYSQL = [
    "SELECT SLEEP(5)",
    "SELECT BENCHMARK(1000000, MD5('x'))",
    "SELECT LOAD_FILE('/etc/passwd')",
    "SELECT * FROM orders INTO OUTFILE '/tmp/x'",
    "SELECT GET_LOCK('x', 10)",
    "LOAD DATA INFILE '/tmp/x' INTO TABLE orders",
    "REPLACE INTO orders (id) VALUES (1)",
    "INSERT INTO orders VALUES (1)",
    "DELETE FROM orders",
    "USE mysql",
    "SHOW GRANTS",
    "SELECT * FROM mysql.user",
    "HANDLER orders OPEN",
]


@pytest.mark.parametrize("sql", REJECT_PG)
def test_postgres_rejects(sql):
    refused(sql)


@pytest.mark.parametrize("sql", REJECT_MYSQL)
def test_mysql_rejects(sql):
    refused(sql, "mysql")


# --- Must be ALLOWED -------------------------------------------------------------------

ALLOW_PG = [
    "SELECT id, status FROM orders",
    "SELECT * FROM orders",
    "SELECT * FROM public.orders",
    "select count(*) from orders where status <> 'Closed'",
    "SELECT o.id, c.name FROM orders o JOIN customers c ON c.id = o.id",
    "SELECT name, title FROM employees",
    "SELECT e.name FROM employees e WHERE e.title = 'Manager'",
    "WITH recent AS (SELECT * FROM orders) SELECT COUNT(*) FROM recent",
    "SELECT status, COUNT(*) FROM orders GROUP BY status HAVING COUNT(*) > 1",
    "SELECT id FROM orders UNION SELECT id FROM customers",
    "SELECT id FROM orders WHERE id IN (SELECT id FROM customers)",
    "SELECT date_trunc('month', created_at) m, SUM(total) FROM orders GROUP BY 1 ORDER BY 1",
    "SELECT id, ROW_NUMBER() OVER (ORDER BY total DESC) FROM orders",
    "SELECT COALESCE(status, 'unknown') FROM orders",
    "SELECT id FROM orders LIMIT 10 OFFSET 20",
    "SELECT 1",
    "SELECT COUNT(*) FROM employees",
    "SELECT title, COUNT(*) FROM employees GROUP BY title",
    "SELECT o.*, e.name FROM orders o JOIN employees e ON e.id = o.id",
    "SELECT * FROM orders /* a comment */",
    "SELECT * FROM orders WHERE note = 'DROP TABLE orders; DELETE'",
]


@pytest.mark.parametrize("sql", ALLOW_PG)
def test_postgres_allows(sql):
    ok(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT `id`, status FROM orders",
        "SELECT DATE_FORMAT(created_at, '%Y-%m') m, SUM(total) FROM orders GROUP BY m",
        "SELECT o.id FROM orders o JOIN customers c ON c.id = o.id LIMIT 5",
    ],
)
def test_mysql_allows(sql):
    ok(sql, "mysql")


# --- LIMIT ---------------------------------------------------------------------------------


def test_limit_added_when_missing():
    q = ok("SELECT id FROM orders")
    assert q.limit == 500 and "LIMIT 500" in q.sql


def test_small_limit_kept():
    assert ok("SELECT id FROM orders LIMIT 10").limit == 10


def test_large_limit_capped():
    q = ok("SELECT id FROM orders LIMIT 100000")
    assert q.limit == 500 and "100000" not in q.sql


def test_fetch_first_capped():
    q = ok("SELECT id FROM orders FETCH FIRST 9999 ROWS ONLY")
    assert q.limit == 500


def test_union_gets_outer_limit():
    assert ok("SELECT id FROM orders UNION SELECT id FROM customers").sql.endswith("LIMIT 500")


def test_comments_are_removed():
    assert "comment" not in ok("SELECT id FROM orders /* comment */").sql


# --- Table resolution ----------------------------------------------------------------------


def test_ambiguous_unqualified_table_is_refused():
    policy = SchemaPolicy(tables={ORDERS, SALES_ORDERS})
    assert "not available" in refused("SELECT * FROM orders", policy=policy)
    assert ok("SELECT * FROM sales.orders", policy=policy).tables == {"sales.orders"}


def test_tables_reported():
    q = ok("SELECT o.id FROM orders o JOIN customers c ON c.id = o.id")
    assert q.tables == {"public.orders", "public.customers"}


def test_cte_name_is_not_a_table():
    ok("WITH secrets AS (SELECT id FROM orders) SELECT id FROM secrets")


# --- Row filters ---------------------------------------------------------------------------

FILTERED = SchemaPolicy(
    tables={ORDERS, CUSTOMERS},
    row_filters={"orders": "company_id = :user_company"},
)


def test_row_filter_wraps_table():
    q = ok("SELECT COUNT(*) FROM orders", policy=FILTERED, user_context={"user_company": 7})
    assert "company_id = 7" in q.sql
    assert "(SELECT * FROM public.orders WHERE company_id = 7) AS orders" in q.sql


def test_row_filter_keeps_alias_and_applies_in_subqueries():
    q = ok(
        "SELECT o.id FROM orders o WHERE o.id IN (SELECT id FROM orders)",
        policy=FILTERED,
        user_context={"user_company": "ACME"},
    )
    assert q.sql.count("company_id = 'ACME'") == 2
    assert ") AS o" in q.sql


def test_row_filter_string_value_is_escaped():
    q = ok("SELECT id FROM orders", policy=FILTERED, user_context={"user_company": "x' OR '1'='1"})
    assert "'x'' OR ''1''=''1'" in q.sql


def test_row_filter_fails_closed_without_context():
    assert "user_company" in refused("SELECT id FROM orders", policy=FILTERED)


def test_row_filter_not_applied_to_other_tables():
    assert "company_id" not in ok("SELECT id FROM customers", policy=FILTERED).sql


def test_row_filter_in_mysql():
    q = ok("SELECT id FROM orders", "mysql", FILTERED, user_context={"user_company": 3})
    assert q.sql == (
        "SELECT id FROM (SELECT * FROM public.orders WHERE company_id = 3) AS orders LIMIT 500"
    )
