# DataChat Agent

An open-source AI agent that answers plain-English questions from your SQL database and your PDFs, and shows the SQL or page each answer came from.

> Status: early development (v0.1, week 1 of 8). Not ready for production use.

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
pytest
ruff check .
```

## Licence

MIT
