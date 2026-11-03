# DataChat Agent

An open-source AI agent that answers plain-English questions from your SQL database and your PDFs, and shows the SQL or page each answer came from.

> Status: early development (v0.1, week 6 of 8). Not ready for production use.

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

### Streaming

`POST /v1/ask/stream` takes the same body and answers as Server-Sent Events, so a chat window can
show the answer while it is being written:

```text
event: meta    data: {"session_id": "chat-1", "route": "database"}
event: data    data: {"sql": "SELECT ...", "rows": [...], "row_count": 13}
event: token   data: {"text": "There are 13 "}
event: token   data: {"text": "delayed orders ..."}
event: done    data: { ...the same body /v1/ask returns... }
```

### Feedback

```bash
curl -X POST localhost:8000/v1/feedback -H "$KEY" -H "Content-Type: application/json"   -d '{"query_id": "<from the answer>", "rating": "wrong", "comment": "Should include line 7"}'
```

### Limits and privacy

- Each API key may make `DEFAULT_RATE_LIMIT_PER_MINUTE` requests a minute (60 by default; 429 with
  `Retry-After` beyond that). Use `RATE_LIMIT_BACKEND=redis` when several servers share the load.
- `DEFAULT_MONTHLY_TOKEN_LIMIT` caps model tokens per account per month. Operators set per-account
  limits with `datachat-agent set-limits <tenant id> --per-minute 30 --monthly-tokens 500000` and
  see them with `datachat-agent list-tenants`. `GET /v1/usage` shows an account its own use.
- `MASK_PII=true` replaces emails, phone numbers, card, Aadhaar and PAN numbers with placeholders
  before anything reaches the model; the real values are put back into the answer.

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

## Five ways to use it

### 1. Python library (no server)

```bash
pip install datachat-agent
```

```python
from datachat_agent import DataChat

with DataChat("postgresql://readonly:secret@host/erp") as dc:
    dc.scan_schema()  # refuses a user that can write
    dc.hide("employees", "salary")  # never shown to the model, never queryable
    dc.add_term("open PO", "status is Open or Partially Received")
    dc.add_pdf("invoice_8231.pdf")
    print(dc.ask("What is the total of invoice KE/26-27/8231?").answer)
```

It stores what it learns in its own Postgres with pgvector (`DATABASE_URL`, or
`DataChat(..., storage_url=...)`). `ask` returns `answer`, `sql`, `rows`, `citations` and `chart`;
`aask`, `ascan_schema` and `aadd_pdf` are the async versions.

### 2. Inside your FastAPI app

```python
from datachat_agent import mount

mount(app, prefix="/ai")  # serves /ai/v1/ask, /ai/v1/documents, ... and /ai/widget.js
```

Run `datachat-agent init-db` once to create its tables.

### 3. Self-hosted server (Docker)

`docker compose up --build` starts the API, a background worker, Postgres with pgvector and
Redis. Configure it with `.env`.

### 4. HTTP API, JavaScript SDK and chat widget

The API is described above and at `/docs` on a running server. From JavaScript:

```js
import { DataChatClient } from "datachat-agent";   // npm install datachat-agent
const dc = new DataChatClient({ baseUrl: "https://api.example.com", apiKey: "dc_live_..." });
const a = await dc.ask("Which production orders are delayed?");
```

For a web page, create a **publishable** key that works only on your websites and can only ask:

```bash
curl -X POST localhost:8000/v1/keys -H "$KEY" -H "Content-Type: application/json"   -d '{"name": "website", "type": "publishable", "allowed_domains": ["erp.example.com"]}'
```

then add one line to the page:

```html
<script src="https://your-datachat-server/widget.js" data-key="dc_pub_..."
        data-title="Ask about our data"></script>
```

Optional: `data-connection`, `data-position="left"`, `data-color="#0f766e"`,
`data-placeholder`. The widget streams answers, shows a small table and the sources, and has
Helpful / Wrong buttons. Publishable keys cannot send `user_context`, so a connection with row
filters refuses widget questions rather than guessing whose rows to show.

### 5. MCP server (Claude Desktop, IDEs)

```bash
datachat-agent mcp --api-key dc_live_...
```

Tools: `list_tables`, `describe_table`, `run_sql`, `search_documents`, `get_document_items` and
`ask`, all read-only. In Claude Desktop's `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "datachat": {
      "command": "datachat-agent",
      "args": ["mcp"],
      "env": {
        "DATACHAT_API_KEY": "dc_live_...",
        "DATABASE_URL": "postgresql://datachat:datachat@localhost:5432/datachat",
        "SECRET_KEY": "...",
        "LLM_BASE_URL": "...", "LLM_MODEL": "...", "LLM_API_KEY": "..."
      }
    }
  }
}
```

### Command line

| Command | Does |
| --- | --- |
| `datachat-agent serve` / `worker` | API server / background worker |
| `datachat-agent init-db` | Create or upgrade tables |
| `datachat-agent create-tenant NAME` | New account and its first admin key |
| `datachat-agent scan CONNECTION_ID` | Re-read a database schema |
| `datachat-agent ingest TENANT_ID FILE...` | Upload and process PDFs |
| `datachat-agent ask-sql CONNECTION_ID "question"` | Try the SQL step |
| `datachat-agent mcp` | MCP server over stdio |
| `datachat-agent list-tenants` / `set-limits` | Usage and limits |
| `datachat-agent check-llm` / `gen-secret` | Test the model / make a SECRET_KEY |

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

JavaScript parts: `cd sdk-js && npm ci && npm test`; `cd widget && npm ci && npm test && npm run build`,
then copy `widget/dist/widget.js` to `packages/datachat_agent/server/static/` (CI checks they
match). Releases: see [docs/RELEASING.md](docs/RELEASING.md).

## Licence

MIT
