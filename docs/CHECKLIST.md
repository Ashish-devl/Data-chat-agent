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
- [x] S9 SQL generator: question + compact schema + business terms + dialect + few-shot pairs + history; `NO_SQL:` reply for unanswerable questions. Real-model check 2026-10-21 (Gemini 3.6 Flash, sample ERP): 5/5 data questions correct and 3/3 correct refusals (hidden salary, weather, delete), in two runs
- [x] S15 Model reliability: per-call timeout and retries, backup models (`LLM_FALLBACK_MODELS`), and a clear error instead of a crash when the model is down
- [x] S10 Validator (sqlglot): one SELECT (CTEs/UNION ok); no DML/DDL/commands, writes hidden in CTEs, SELECT INTO, FOR UPDATE; denylisted functions (`pg_*`, `sleep`, `dblink`, file, lock, sequence); allowlisted tables only; no table functions; no hidden columns or `SELECT *` over them; LIMIT ≤ 500 enforced
- [x] S11 Executor: read-only transaction (Postgres + MySQL), 10 s timeout (`statement_timeout` / `MAX_EXECUTION_TIME`), row filters wrapped around every use of a table (fail closed without user context), 500-row and 5 MB caps
- [x] S12 Validator test suite: 130+ reject/allow cases
- [x] S13 `GET/PATCH/DELETE /v1/connections/{id}` and `GET /v1/connections`
- [x] S14 `datachat-agent ask-sql` CLI: question → SQL → rows, for demos before `/v1/ask` exists

## Week 3 — Agent
- [x] A1 LangGraph graph (`agent/graph.py`): route → database and/or documents → answer; SQL step runs retrieve → generate → validate → run → repair
- [x] A2 Router (`agent/router.py`): database, documents, both or none; asks the model only when both sources exist, falls back to both on failure
- [x] A3 Repair loop: one retry with the validator or database error, then a clear failure message; no retry after a timeout (`sql/pipeline.py`)
- [x] A4 Fixed tool set (`agent/tools.py`): `list_tables`, `describe_table`, `run_sql`, `search_documents`, `get_document_items`; all read-only, hidden items left out
- [x] A5 Sessions: the caller names the conversation (`session_id`, unique per tenant); the last 4 turns feed the SQL and answer prompts; follow-ups tested with a real model; `GET /v1/sessions/{id}`
- [x] A6 Says "I don't know" when neither source has the answer (database and documents; checked with a real model)
- [x] A7 `POST /v1/ask`: answer, route, sql, columns, rows, row_count, truncated, citations (empty until week 4), chart hint, session_id, latency_ms, tokens, query_id; 503 with a clear error when the model is down; data still returned if only the answer step fails
- [x] A8 Every question written to `query_log` with session, connection, API key, route, SQL, rows, latency, tokens and error
- [x] A9 Business terms go into every SQL prompt with the rule that they override assumptions; a term named in the question also steers table retrieval
- [x] A10 `glossary_terms` and `sql_examples` tables (migration 0002) + endpoints: `GET/POST /v1/connections/{id}/terms`, `PATCH/DELETE /v1/terms/{id}`, `GET/POST /v1/connections/{id}/examples`, `DELETE /v1/examples/{id}`; example SQL must pass the validator; the 3 closest examples are shown to the model
- [x] A11 `user_context` on `/v1/ask` fills row-filter placeholders (fails closed when missing)
- [x] A12 Prompt-injection guard: query results go inside a `<data>` block that cannot be closed from inside, with a fixed rule that data is never instructions; schema text has the same rule; tools cannot write

## Week 4 — PDF pipeline (checkpoint: end-to-end demo)
- [x] P1 `POST /v1/documents` (multipart): PDF only (checked by content, 415), ≤ 20 MB (`MAX_PDF_MB`, 413), deduplicated per tenant by SHA-256 (200 with the existing document)
- [x] P2 Bytes stored in `documents.file_bytes`; `GET /v1/documents/{id}/file` returns the original byte for byte
- [x] P3 `GET /v1/documents`, `GET /v1/documents/{id}`, `GET /v1/documents/{id}/items`, `POST /v1/documents/{id}/reprocess`, `DELETE /v1/documents/{id}` (removes passages and items)
- [x] P4 Background jobs: `JOBS_BACKEND` sync / inline / arq; `datachat-agent worker` (arq + Redis, checked on Windows); `worker` service in docker-compose; `POST /scan?wait=false` runs scans in the background
- [x] P5 Text per page with **pdfplumber** (MIT; PyMuPDF dropped because it is AGPL); no text layer → status `failed` with a reason
- [x] P6 Tables with pdfplumber → line items via header synonyms (Description/Particulars/Item, Qty/Quantity, Rate/Price, Amount/Value/Total…); total rows skipped; unmapped tables go to the model once with a strict JSON format (max 3 per document)
- [x] P7 Chunker: about 800 tokens, 100 overlap, never across pages, section heading kept (migration 0003)
- [x] P8 Embeddings + tsv; status `ready`; the document's printed date is stored (`doc_date`)
- [x] P9 Hybrid search: vector top 20 + keyword top 20 (any-word match), reciprocal rank fusion, top 6
- [x] P10 `get_document_items` with filters: document (file name or any text in it, e.g. an invoice number), item name, date range
- [x] P11 Answers cite [n] → file name, page, section, snippet and a download link to that page
- [x] P12 Ingestion tests: hash, page count, date, chunks, items, header variants, duplicate upload, scanned PDF, reprocess, delete, tenancy, scopes
- [x] P13 **Checkpoint (2026-10-21, Gemini 3.6 Flash):** invoice total (INR 773,550, cited), manual on carton marking (page 2 cited), highest Cotton S/J rate across invoices (Shree Ganesh Knits, 438/kg, cited), a question needing database + manual (both parts answered), and an unknown question ("I don't know")

## Week 5 — Both sources
- [x] B1 One question answered from database + PDFs together (router → SQL for the database part → document search → one cited answer; checked with a real model)
- [x] B2 `POST /v1/ask/stream` (Server-Sent Events): `meta` → `data` (SQL + rows) → `token`… → `done` (same body as `/ask`); shares the answer step with `/ask` so both always agree; checked live with Gemini (it streams in a few large chunks)
- [x] B3 `POST /v1/feedback` (helpful / wrong + optional comment), stored on `query_log` with time; can be changed; tenant-isolated
- [x] B4 Chart hint (bar, line, none), pulled forward to week 3
- [x] B5 Rate limits: requests per minute per API key (memory or Redis, 429 + Retry-After) on every authenticated route; monthly token cap per tenant checked before `/ask` and `/ask/stream`; `GET /v1/usage` shows limits and tokens left
- [x] B6 `tenants.rate_limit_per_minute` and `monthly_token_limit` (migration 0004), server defaults in `.env`; operators set them with `datachat-agent set-limits` and see them with `list-tenants`
- [x] B7 PII masking (`MASK_PII=true`): emails, phone numbers, Luhn-valid card numbers, Aadhaar and PAN become placeholders in rows, passages, line items, history and schema samples; real values restored in the answer, also when streamed

- [x] B8 Token counting fixed for streamed replies (Gemini repeats the running total on every chunk)
- [x] B9 `LLM_REASONING_EFFORT`: measured 2026-11-03 on Gemini 3.6 Flash with `none`: answers in 2.4–4.1 s (was 15–27 s) with 1,700–2,600 tokens (was ~7,000), still correct
- [x] B10 Answers no longer call a "top N" selection a total: the query's own LIMIT is stated as a fact in the data (checked live 2026-11-03)

## Week 6 — Distribution
- [x] D1 Python library: `DataChat` class (`client.py`): `scan_schema()`, `add_pdf()`, `ask()`, `hide()`, `add_term()`, `add_example()`, `documents()`, async twins; own event loop and engine, so it also works inside async code
- [x] D2 `mount(app, prefix="/ai")` adds the API (and `/widget.js`) to any FastAPI app; tested
- [~] D3 JS SDK (`sdk-js/`): built, 9 tests, checked live. **npm publishing waits for your npm account** (release skips npm until `PUBLISH_NPM=true`)
- [x] D4 Widget (`widget/`): one script tag, Preact (10 KB gzipped instead of ~45 KB with React), shadow DOM, streaming, table, sources, feedback, dark mode, keyboard access; served by every server at `/widget.js`; 5 tests
- [x] D5 Publishable `dc_pub_` key: ask-only (no PDF upload, no session history), checked against its allowed websites via the browser's Origin header, cannot send `user_context` (row-filtered connections refuse it), its conversations kept apart, feedback only on its own answers
- [x] D6 `key_type` and `allowed_domains` on `api_keys` (migration 0005), wildcard subdomains; CORS for browsers (`CORS_ALLOW_ORIGINS`)
- [x] D7 MCP server (`datachat-agent mcp`, MCP SDK 2.x): `list_tables`, `describe_table`, `run_sql`, `search_documents`, `get_document_items`, `ask`; secret keys only; rate and token limits; checked over real stdio with Gemini
- [x] D8 CLI: `scan`, `ingest`, `mcp` (plus `worker`, `list-tenants`, `set-limits` from earlier weeks)
- [x] D9 PyPI publishing by trusted publishing: **0.1.0 published 2026-11-03** (`pip install datachat-agent` checked in a clean environment)
- [x] D10 Docker image on GitHub Container Registry: **`ghcr.io/ashish-devl/datachat-agent:0.1.0` published**, pulls without logging in

## Week 7 — Evaluation, security, tests
- [x] E1 Sample databases: garment mini-ERP (Postgres + MySQL, made up) and Chinook (public, MIT, 11 tables; pinned v1.4.5 download checked by SHA-256, `eval/datasets/chinook.py`)
- [x] E2 20 sample PDFs (made up): 9 invoices (incl. a 2-page table, GST rows, a USD export invoice), 2 buyer manuals, a supply contract, a PO, a packing list, a test report, a credit note, a delivery challan, 2 price lists, an image-only scan
- [x] E3 100 questions (`eval/datasets/questions.json`): 60 database (40 ERP + 20 Chinook), 30 documents, 10 both; 7 of them unanswerable. Every correct query checked to return rows with no tie for first place
- [~] E4 `python -m eval.run_eval`: loads data, asks all 100 through the real model, grades, saves after each question, stops cleanly on a spent daily quota and continues with `--resume`; scoring unit-tested. **Full run waiting for Gemini's daily quota**
- [ ] E5 Targets: SQL accuracy 80%, schema recall 95%, document accuracy 85%, citation precision 90%, refusal 90%, median latency < 4 s (note: free Gemini took 3–21 s per question on 2026-10-21; the < 4 s target needs a faster model or the paid tier)
- [x] E6 Executor tests: timeout fires, LIMIT applied, row filter applied, write fails on a read-only user and inside the read-only transaction even for a writer
- [x] E7 Tenancy tests: keys, connections (every route), schema items, terms, examples, sessions and documents (every route); same file or session key in two tenants stays separate
- [x] E8 Auth tests: revoked key (every route), wrong scope, publishable key limits (website check, ask-only, no user_context, private sessions, own feedback only), MCP key checks
- [x] E9 Feedback → evaluation set: `datachat-agent export-feedback TENANT_ID [--rating wrong|helpful|all]` writes candidate questions with the SQL and answer given

- [x] E10 PDF line items: units in brackets no longer confuse columns ("Unit Price (USD)" is a rate); tax, discount and freight rows are skipped (found by the new sample PDFs)
- [x] E11 Backup models no longer get `LLM_REASONING_EFFORT` (Gemini 3.5 Flash Lite rejects it)

## Week 8 — Launch (checkpoint: evaluation targets met)
- [x] L1 Admin console (`console/`, React 19 + Vite, 78 KB gzipped): overview, databases (test, scan, hide/describe columns, business terms, worked examples), documents (upload, status, line items, download, delete), try-it chat (streaming), query log (search, rating and error filters, SQL and answer), API keys (secret and website keys, shown once, widget snippet), users, account; members see only their pages; served at `/console` by every server; 7 tests
- [x] L2 Console login: `users` and `console_sessions` (migration 0006); scrypt passwords with strength rules; 12-hour revocable session tokens (hash stored); same reply for unknown email and wrong password; 5 attempts per email per minute; owner/member roles; at least one active owner kept; password change signs out other devices; `create-user`, `reset-password`, `create-tenant --owner-email`; `GET /v1/query-log`; 12 tests
- [x] L3 Nightly schema re-scan: arq cron at 02:00 re-reads every working connection (`server/worker.py`)
- [~] L4 README: all five ways to use it, console, CLI, widget, MCP, evaluation; `docs/API.md` generated from the server's OpenAPI description with every endpoint described (CI fails if it is out of date). **Screenshots still to add** (taken while recording the demo)
- [~] L5 Demo video: script, setup and scene list ready (`docs/DEMO.md`). **Recording is yours**
- [~] L6 Deploy: `railway.json`, `console/vercel.json`, Dockerfile honours `PORT` (checked), step-by-step `docs/DEPLOY.md`. **Needs your Railway (and optionally Vercel) account**
- [x] L9 Hosted mode (`BLOCK_PRIVATE_NETWORKS=true`): connection strings to localhost, private, link-local, carrier-grade NAT and other non-public addresses refused (incl. cloud metadata 169.254.169.254 and IPv6 equivalents); options that redirect the host refused; connections pinned to the checked address against DNS rebinding; 22 tests
- [ ] L7 Release v1.0.0: tag, PyPI, npm, Docker image, GitHub release notes
- [ ] L8 **Checkpoint:** every box above ticked and evaluation targets met

## Out of scope for v1 (from the plan)
Writing to customer databases · databases other than PostgreSQL/MySQL · OCR for scanned PDFs ·
billing · hosted multi-tenant service with per-tenant LLM keys (columns exist, feature later) ·
PDFs over 20 MB / S3 storage.
