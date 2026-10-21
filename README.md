# DataChat Agent

An open-source AI agent that answers plain-English questions from your SQL database and your PDFs, and shows the SQL or page each answer came from.

> Status: early development (v0.1, week 4 of 8). Not ready for production use.

## Bring your own model key

DataChat Agent never ships with an LLM key. You use your own, from any OpenAI-compatible provider or Claude:

| Provider | Cost | Get a key |
| --- | --- | --- |
| Groq | Free tier | https://console.groq.com/keys |
| Google Gemini | Free tier | https://aistudio.google.com/apikey |
| OpenRouter (`:free` models) | Free | https://openrouter.ai/keys |
| Ollama | Free, runs locally | https://ollama.com |
| OpenAI, Claude | Paid | Their consoles |

## Quickstart

```bash
git clone https://github.com/Ashish-devl/Data-chat-agent.git
cd Data-chat-agent
cp .env.example .env          # then fill in your LLM settings
pip install -e ".[dev]"
datachat-agent gen-secret           # paste the output into SECRET_KEY in .env
datachat-agent check-llm            # confirms your model key works
```

Start Postgres (with pgvector) and Redis, then the API:

```bash
docker compose up -d db redis
datachat-agent init-db
datachat-agent create-tenant "My company"   # prints an admin API key, once
datachat-agent serve
```

Or run everything in Docker: `docker compose up --build`.

API docs: http://localhost:8000/docs

```bash
curl http://localhost:8000/v1/keys -H "Authorization: Bearer dc_live_..."
```

## Connect a database

DataChat Agent only ever reads. Create a database user that can `SELECT` and nothing else; the
connection test refuses users that can write.

```bash
KEY="Authorization: Bearer dc_live_..."
curl -X POST localhost:8000/v1/connections -H "$KEY" -H "Content-Type: application/json"   -d '{"name": "erp", "dsn": "postgresql://readonly:pass@host:5432/erp"}'
curl -X POST localhost:8000/v1/connections/<id>/test -H "$KEY"   # must say "ok"
curl -X POST localhost:8000/v1/connections/<id>/scan -H "$KEY"   # reads tables and columns
curl localhost:8000/v1/connections/<id>/schema -H "$KEY"
```

Describe or hide columns (hidden columns can never be queried):

```bash
curl -X PATCH localhost:8000/v1/schema-items/<item id> -H "$KEY"   -H "Content-Type: application/json" -d '{"hidden": true}'
```

Teach it your business language and give it worked examples:

```bash
curl -X POST localhost:8000/v1/connections/<id>/terms -H "$KEY" -H "Content-Type: application/json"   -d '{"term": "open PO", "definition": "a purchase order whose status is Open or Partially Received"}'
curl -X POST localhost:8000/v1/connections/<id>/examples -H "$KEY" -H "Content-Type: application/json"   -d '{"question": "Suppliers in Ludhiana", "sql": "SELECT name FROM suppliers WHERE city = '"'"'Ludhiana'"'"'"}'
```

## Ask questions

```bash
curl -X POST localhost:8000/v1/ask -H "$KEY" -H "Content-Type: application/json"   -d '{"question": "Which production orders are delayed?", "session_id": "chat-1"}'
```

```json
{
  "answer": "There are 13 delayed production orders in total ...",
  "route": "database",
  "sql": "SELECT ... LIMIT 500",
  "columns": ["style_no", "qty_behind"],
  "rows": [{"style_no": "26C1740", "qty_behind": 1390}],
  "chart": {"type": "bar", "x": "style_no", "y": "qty_behind"},
  "session_id": "chat-1",
  "latency_ms": 7126
}
```

Send the same `session_id` for follow-ups ("which buyers are those for?"); read a conversation
with `GET /v1/sessions/chat-1`. Pass `connection_id` when you have more than one database, and
`user_context` (e.g. `{"user_company": 7}`) when the connection uses row filters. Keys need the
`ask` scope.

## Upload PDFs

```bash
curl -X POST localhost:8000/v1/documents -H "$KEY" -F "file=@invoice.pdf;type=application/pdf"
curl localhost:8000/v1/documents -H "$KEY"                    # status: queued → ready
curl localhost:8000/v1/documents/<id>/items -H "$KEY"         # line items from its tables
curl -o original.pdf localhost:8000/v1/documents/<id>/file -H "$KEY"
```

PDFs with a text layer up to 20 MB; the same file uploaded twice is stored once. Text is split
into passages per page and searched by meaning and by keyword; tables become line items (item,
qty, unit, rate, amount). Answers from documents cite the file and page:

```json
{
  "answer": "The grand total of invoice KE/26-27/8231 is INR 773,550.00 [1].",
  "route": "documents",
  "citations": [{"ref": 1, "filename": "invoice_KE-26-27-8231.pdf", "page": 1,
                 "url": "/v1/documents/<id>/file#page=1"}]
}
```

One question can use both sources ("Which orders are delayed, and what carton weight does the
manual allow?"). Uploads need a key with the `docs` scope. Scanned PDFs without a text layer are
not supported yet. Generate made-up sample PDFs with `python eval/datasets/make_pdfs.py out/`.

Processing runs in the background of the API by default. With Docker, a separate `worker`
service does it (`JOBS_BACKEND=arq`), and it also re-scans every database schema nightly.

Try the SQL step alone from the command line:

```bash
datachat-agent ask-sql <connection id> "Which production orders are delayed?"
```

### Safety: three separate layers

1. The database user must be read-only (checked on test and before every scan).
2. Every query is parsed: one `SELECT` only, allowed tables only, no hidden columns, no admin
   functions, `LIMIT 500` at most.
3. Queries run in a read-only transaction with a 10-second timeout.

Per-user row filters (`"row_filters": {"orders": "company_id = :user_company"}`) are wrapped around
every use of the table; a question without the user's context is refused.

## Install as a package

```bash
pip install datachat-agent      # once released; then `import datachat_agent`
```

## Embed in your FastAPI app

```python
from datachat_agent.server.app import router as datachat_router

app.include_router(datachat_router, prefix="/ai")
```

## Development

```bash
docker compose up -d db redis
docker compose --profile dev up -d mysql     # optional: MySQL tests
pytest                                       # unit + integration tests
ruff check . && ruff format --check .
```

Integration tests create a throwaway `datachat_test` database and load `erp_demo`, a made-up
garment-factory ERP (`eval/datasets/erp_demo.py`), into Postgres and MySQL. They are skipped when
the databases are not running. Load the sample yourself to try the API:

```bash
python eval/datasets/erp_demo.py postgresql://datachat:datachat@localhost:5432/postgres
# then connect with: postgresql://erp_readonly:erp_readonly@localhost:5432/erp_demo
```

After changing `server/models.py`, generate a migration with
`datachat-agent make-migration "what changed"`.

## Licence

MIT
