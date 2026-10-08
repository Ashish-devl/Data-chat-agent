# v1 completion checklist

Every feature, endpoint, table, safety control, test and launch task from the
[implementation plan](https://claude.ai/artifact/2u1mF7qKb4weDmcJzJ1UeY), plus the gaps found while
reviewing it. v1 is done only when every box is ticked. Each week ends by updating this file.

`[x]` done and tested · `[~]` started · `[ ]` not started. The code is in brackets.

## Week 1 — Foundations
- [x] F1 Monorepo layout, `pyproject.toml`, MIT licence, README
- [x] F2 Settings from `.env`; `.env.example`; `.env` git-ignored (bring your own key)
- [x] F3 FastAPI app + include-able `/v1` router (`server/app.py`)
- [x] F4 All 9 tables from the plan: tenants, api_keys, connections, schema_items, documents, document_chunks, document_items, sessions, query_log (`server/models.py`)
- [x] F5 Indexes: HNSW on both embedding columns, GIN on tsv, B-tree on every tenant_id
- [x] F6 API keys: SHA-256 hash, `dc_live_` prefix, shown once, scopes ask/docs/admin, revocable
- [x] F7 `POST /v1/keys`, `GET /v1/keys`, `DELETE /v1/keys/{id}`, `GET /v1/usage`, `GET /v1/health`
- [x] F8 Fernet encryption helper for stored DSNs and LLM keys
- [x] F9 LLM provider switch: OpenAI-compatible (Groq, Gemini, OpenRouter, Ollama, OpenAI) or Claude
- [x] F10 CLI: `serve`, `init-db`, `create-tenant`, `gen-secret`, `check-llm`
- [x] F11 Dockerfile + docker-compose (Postgres with pgvector, Redis, API)
- [x] F12 Verified against real Postgres 17 + pgvector 0.8.7 in Docker: 9 tables, 12 indexes, key create/list/revoke, scope 403, revoked 401, tenant isolation on keys
- [x] F13 Project renamed to DataChat Agent: `pip install datachat-agent`, `import datachat_agent`, CLI `datachat-agent` (`datachat` is taken on PyPI)
- [x] F14 Alembic migrations (`migrations/`, shipped inside the package); `init-db` runs them; up/down/re-up and model-match checked
- [x] F15 GitHub Actions (`.github/workflows/ci.yml`): ruff + format check + all tests on Python 3.11 and 3.12 against Postgres and MySQL services; Docker image build

## Week 2 — SQL core
- [x] S1 `POST /v1/connections` (dialect detected from DSN, schema allowlist, row filters); DSN encrypted; password never returned
- [x] S2 `POST /v1/connections/{id}/test`: refuses superusers, write grants, CREATE on schemas/database (Postgres) and any non-SELECT grant (MySQL); any error fails closed; fix hint in `status_detail`
- [x] S3 PostgreSQL (psycopg 3) and MySQL (PyMySQL) drivers via SQLAlchemy, pooled engines with connect timeouts
- [x] S4 `POST /v1/connections/{id}/scan`: tables + views, columns, types, PK/FK, row counts, up to 5 sample values per text column, skipping hidden items; re-checks read-only first; keeps admin edits on rescan (runs in the request until P4 moves it to a background job)
- [x] S5 `GET /v1/connections/{id}/schema`
- [x] S6 `PATCH /v1/schema-items/{id}`: description (re-embedded), hidden flag (clears sample values; hiding a table clears all its columns' samples)
- [x] S7 Embeddings: local `BAAI/bge-small-en-v1.5` via fastembed (lighter than sentence-transformers: no PyTorch) or any OpenAI-compatible API; wrong `EMBED_DIM` fails with a clear message
- [x] S8 Schema retriever: all tables when ≤ 8, else vector top 5 + FK neighbours up to 8; hidden items never shown to the model
- [~] S9 SQL generator: question + compact schema + business terms + dialect + few-shot pairs + history; `NO_SQL:` reply for unanswerable questions. Tested with a fake model; **still to do: run against a real model with your key**
- [x] S10 Validator (sqlglot): one SELECT (CTEs/UNION ok); no DML/DDL/commands, writes hidden in CTEs, SELECT INTO, FOR UPDATE; denylisted functions (`pg_*`, `sleep`, `dblink`, file, lock, sequence); allowlisted tables only; no table functions; no hidden columns or `SELECT *` over them; LIMIT ≤ 500 enforced
- [x] S11 Executor: read-only transaction (Postgres + MySQL), 10 s timeout (`statement_timeout` / `MAX_EXECUTION_TIME`), row filters wrapped around every use of a table (fail closed without user context), 500-row and 5 MB caps
- [x] S12 Validator test suite: 130+ reject/allow cases
- [x] S13 `GET/PATCH/DELETE /v1/connections/{id}` and `GET /v1/connections`
- [x] S14 `datachat-agent ask-sql` CLI: question → SQL → rows, for demos before `/v1/ask` exists

## Week 3 — Agent
- [ ] A1 LangGraph graph: route → retrieve → generate SQL → validate → run → answer
- [ ] A2 Router: database, documents or both
- [x] A3 Repair loop: one retry with the validator or database error, then a clear failure message; no retry after a timeout (`sql/pipeline.py`)
- [ ] A4 Fixed tool set: `list_tables`, `describe_table`, `run_sql`, `search_documents`, `get_document_items`
- [ ] A5 Session memory so follow-ups work; `GET /v1/sessions/{id}`
- [~] A6 Says "I don't know" when neither source has the answer (SQL side done: `NO_SQL:`; documents side in week 4)
- [ ] A7 `POST /v1/ask`: answer, route, sql, rows, citations, chart hint, session_id, latency_ms
- [ ] A8 Every question written to `query_log` (question, route, SQL, rows, chunks, latency, tokens)
- [ ] A9 Semantic layer: business terms ("active order" = status not Closed) outrank raw column names
- [ ] A10 Gap: table for business terms (`glossary`) and few-shot pairs (`sql_examples`) + their endpoints
- [ ] A11 Gap: `user_context` field on `/v1/ask`, which fills row-filter placeholders such as `:user_company`
- [ ] A12 Prompt-injection guard: retrieved text wrapped as data, fixed system rule

## Week 4 — PDF pipeline (checkpoint: end-to-end demo)
- [ ] P1 `POST /v1/documents` (multipart): PDF only, ≤ 20 MB, deduplicated by SHA-256 (returns the existing document)
- [ ] P2 Bytes stored in `documents.file_bytes`; `GET /v1/documents/{id}/file` downloads the original
- [ ] P3 `GET /v1/documents`, `GET /v1/documents/{id}/items`, `DELETE /v1/documents/{id}`
- [ ] P4 Background jobs (Arq + Redis) for ingestion and schema scans
- [ ] P5 Text per page with PyMuPDF; scanned PDF (no text layer) → status `failed` with a reason
- [ ] P6 Tables with pdfplumber → line items via header synonyms; rows that don't map go to the LLM once with a strict JSON schema
- [ ] P7 Chunker: about 800 tokens, 100 overlap, never across pages, heading kept
- [ ] P8 Embeddings + tsv; status `ready`
- [ ] P9 Hybrid search: vector top 20 + keyword top 20, RRF, top 6
- [ ] P10 `get_document_items` with filters (document, item name, date)
- [ ] P11 Answers cite filename and page
- [ ] P12 Ingestion tests: hash, page count, chunks, items, duplicate upload
- [ ] P13 **Checkpoint:** demo answers the three example questions from the plan end to end

## Week 5 — Both sources
- [ ] B1 One question answered from database + PDFs together
- [ ] B2 `POST /v1/ask/stream` (Server-Sent Events)
- [ ] B3 `POST /v1/feedback` (helpful / wrong) stored on `query_log`
- [ ] B4 Chart hint (bar, line, none)
- [ ] B5 Rate limits: requests per minute per key (Redis) + monthly token cap per tenant
- [ ] B6 Gap: limit columns on `tenants` (rpm, monthly tokens)
- [ ] B7 Optional PII masking before data is sent to the LLM (`MASK_PII=true`)

## Week 6 — Distribution
- [ ] D1 Python library: `DataChat` class (`client.py`): `scan_schema()`, `add_pdf()`, `ask()`
- [ ] D2 Mountable router documented (`app.include_router(router, prefix="/ai")`)
- [ ] D3 JS SDK (`sdk-js/`), published to npm as `datachat-agent`
- [ ] D4 Widget (`widget/`): one script tag, built to a single `widget.js`
- [ ] D5 Publishable `dc_pub_` key: ask-only (no PDF upload from the widget, decided 2026-10-08), with an allowed-domains list
- [ ] D6 Gap: `key_type` and `allowed_domains` columns on `api_keys`; CORS limited to those domains
- [ ] D7 MCP server: `datachat-agent mcp` exposes the same tools
- [ ] D8 CLI: `scan`, `ingest`, `mcp`
- [ ] D9 PyPI publishing (wheel + sdist) from GitHub Actions
- [ ] D10 Docker image published (GitHub Container Registry)

## Week 7 — Evaluation, security, tests
- [~] E1 Sample databases: 8-table garment mini-ERP done for Postgres and MySQL with read-only and writer users (`eval/datasets/erp_demo.py`); Chinook or Northwind still to add
- [ ] E2 20 sample PDFs: invoices, a buyer manual, a price list (made-up)
- [ ] E3 100 questions with expected answers: 60 database, 30 documents, 10 both
- [ ] E4 `eval/run_eval.py` reporting all six metrics; score in the README
- [ ] E5 Targets: SQL accuracy 80%, schema recall 95%, document accuracy 85%, citation precision 90%, refusal 90%, median latency < 4 s
- [x] E6 Executor tests: timeout fires, LIMIT applied, row filter applied, write fails on a read-only user and inside the read-only transaction even for a writer
- [~] E7 Tenancy tests: keys, connections (every route) and schema items done; documents and logs when they exist
- [ ] E8 Auth tests: revoked key, wrong scope, publishable key limits
- [ ] E9 Feedback → evaluation set export

## Week 8 — Launch (checkpoint: evaluation targets met)
- [ ] L1 Admin console (React + Vite): connections, documents, semantic layer, keys, users, query log, usage
- [ ] L2 Console login: email + password (decided 2026-10-08): `users` table (tenant, email, password hash, role owner/member), sessions, password reset by CLI
- [ ] L3 Nightly schema re-scan (scheduled job)
- [ ] L4 README quickstart for all five ways to use it; API reference; screenshots
- [ ] L5 Demo video
- [ ] L6 Deploy: API + Postgres + Redis on Railway, console on Vercel
- [ ] L9 Hosted mode only: block customer DSNs that point at private or internal network addresses (SSRF guard); self-hosters keep private addresses
- [ ] L7 Release v1.0.0: tag, PyPI, npm, Docker image, GitHub release notes
- [ ] L8 **Checkpoint:** every box above ticked and evaluation targets met

## Out of scope for v1 (from the plan)
Writing to customer databases · databases other than PostgreSQL/MySQL · OCR for scanned PDFs ·
billing · hosted multi-tenant service with per-tenant LLM keys (columns exist, feature later) ·
PDFs over 20 MB / S3 storage.
