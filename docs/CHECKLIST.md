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
- [ ] F12 Verified against a real Postgres (waiting on Docker Desktop install)
- [x] F13 Project renamed to DataChat Agent: `pip install datachat-agent`, `import datachat_agent`, CLI `datachat-agent` (`datachat` is taken on PyPI)
- [ ] F14 Alembic migrations instead of `create_all` (needed before the first release)
- [ ] F15 GitHub Actions: pytest + ruff on every push (moved up from week 7)

## Week 2 — SQL core
- [ ] S1 `POST /v1/connections` (dialect, DSN, schema allowlist); DSN encrypted
- [ ] S2 `POST /v1/connections/{id}/test`: connects and **refuses a user that can write** (fails closed)
- [ ] S3 PostgreSQL (psycopg 3) and MySQL (PyMySQL) drivers via SQLAlchemy
- [ ] S4 `POST /v1/connections/{id}/scan`: tables, columns, types, PK/FK, row counts, up to 5 sample values per text column, skipping hidden columns
- [ ] S5 `GET /v1/connections/{id}/schema`
- [ ] S6 `PATCH /v1/schema-items/{id}`: description, hidden flag
- [ ] S7 Embeddings module: local sentence-transformers (default) or OpenAI; `EMBED_DIM` checked
- [ ] S8 Schema retriever: vector top 5–8 tables + FK expansion
- [ ] S9 SQL generator: question + compact schema + semantic layer + dialect + few-shot pairs + history
- [ ] S10 Validator (sqlglot): parses; single SELECT (CTEs ok); no DML/DDL, `pg_sleep`, `COPY`, `dblink`, file functions; allowlisted tables only; no hidden columns; LIMIT ≤ 500 added
- [ ] S11 Executor: read-only transaction, `statement_timeout` 10 s, row-filter templates wrapped around the query, 5 MB response cap
- [ ] S12 Validator test suite: 60+ reject/allow cases
- [ ] S13 Gap: `GET/PATCH/DELETE /v1/connections/{id}` and `GET /v1/connections` (the console needs them)

## Week 3 — Agent
- [ ] A1 LangGraph graph: route → retrieve → generate SQL → validate → run → answer
- [ ] A2 Router: database, documents or both
- [ ] A3 Repair loop: one retry with the DB error, then a clear failure message
- [ ] A4 Fixed tool set: `list_tables`, `describe_table`, `run_sql`, `search_documents`, `get_document_items`
- [ ] A5 Session memory so follow-ups work; `GET /v1/sessions/{id}`
- [ ] A6 Says "I don't know" when neither source has the answer
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
- [ ] E1 Sample databases: Chinook or Northwind + an 8-table mini-ERP for a garment factory (made-up data)
- [ ] E2 20 sample PDFs: invoices, a buyer manual, a price list (made-up)
- [ ] E3 100 questions with expected answers: 60 database, 30 documents, 10 both
- [ ] E4 `eval/run_eval.py` reporting all six metrics; score in the README
- [ ] E5 Targets: SQL accuracy 80%, schema recall 95%, document accuracy 85%, citation precision 90%, refusal 90%, median latency < 4 s
- [ ] E6 Executor tests: timeout fires, LIMIT applied, row filter applied, write fails on a read-only user
- [ ] E7 Tenancy tests: tenant A's key can't see tenant B's connections, documents, logs or keys
- [ ] E8 Auth tests: revoked key, wrong scope, publishable key limits
- [ ] E9 Feedback → evaluation set export

## Week 8 — Launch (checkpoint: evaluation targets met)
- [ ] L1 Admin console (React + Vite): connections, documents, semantic layer, keys, users, query log, usage
- [ ] L2 Console login: email + password (decided 2026-10-08): `users` table (tenant, email, password hash, role owner/member), sessions, password reset by CLI
- [ ] L3 Nightly schema re-scan (scheduled job)
- [ ] L4 README quickstart for all five ways to use it; API reference; screenshots
- [ ] L5 Demo video
- [ ] L6 Deploy: API + Postgres + Redis on Railway, console on Vercel
- [ ] L7 Release v1.0.0: tag, PyPI, npm, Docker image, GitHub release notes
- [ ] L8 **Checkpoint:** every box above ticked and evaluation targets met

## Out of scope for v1 (from the plan)
Writing to customer databases · databases other than PostgreSQL/MySQL · OCR for scanned PDFs ·
billing · hosted multi-tenant service with per-tenant LLM keys (columns exist, feature later) ·
PDFs over 20 MB / S3 storage.
