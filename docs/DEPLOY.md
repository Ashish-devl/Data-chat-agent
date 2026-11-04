# Deploying the hosted version

The hosted version runs on **Railway** (API, background worker, Postgres with pgvector, Redis).
The admin console is already served by the API at `/console`; putting it on **Vercel** as well is
optional. Allow about 30 minutes the first time. Prices change; check Railway's and Vercel's
current plans (Railway's Hobby plan is enough to start).

## 1. Railway project

1. Sign in at https://railway.com with GitHub and create a **New Project → Deploy from GitHub
   repo → `Ashish-devl/Data-chat-agent`**. Railway builds the `Dockerfile`. Name this service
   **api**.
2. In the project, **+ New → Template → search "pgvector"** and add a Postgres-with-pgvector
   database. (DataChat Agent needs the `vector` extension; plain Postgres may not have it.)
3. **+ New → Database → Redis.**

## 2. Settings for the `api` service

Variables (Service → Variables). The `${{...}}` references link to the databases you just added;
use the names Railway gave them.

| Variable | Value |
| --- | --- |
| `DATABASE_URL` | `${{pgvector.DATABASE_URL}}` |
| `REDIS_URL` | `${{Redis.REDIS_URL}}` |
| `SECRET_KEY` | output of `datachat-agent gen-secret` (keep a copy: losing it makes stored database passwords unreadable) |
| `LLM_PROVIDER`, `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY` | your model, as in `.env` |
| `LLM_FALLBACK_MODELS`, `LLM_REASONING_EFFORT` | as in `.env` (optional) |
| `JOBS_BACKEND` | `arq` |
| `RATE_LIMIT_BACKEND` | `redis` |
| `BLOCK_PRIVATE_NETWORKS` | `true` if other companies will connect their databases |
| `CORS_ALLOW_ORIGINS` | `*`, or your console and website addresses |
| `MODELS_CACHE_DIR` | `/models` |

Then:

- **Settings → Networking → Generate Domain** gives the public address, e.g.
  `datachat-production.up.railway.app`.
- **Settings → Deploy → Healthcheck Path**: `/v1/health`.
- Add a **Volume** mounted at `/models` so the embedding model downloads once, not on every deploy.

## 3. The `worker` service

**+ New → GitHub repo → the same repository**, name it **worker**, then:

- **Settings → Deploy → Custom Start Command**: `datachat-agent worker`
- **Variables**: the same as the `api` service (Railway can copy them: *Shared Variables*).
- Mount the same kind of volume at `/models`.

The worker processes uploaded PDFs, background scans and the nightly schema re-scan at 02:00.

## 4. First account

In the `api` service: **⋯ → Shell** (or the Railway CLI, `railway run`):

```bash
datachat-agent create-tenant "Your company" --owner-email you@example.com
```

It prints an admin API key and your console password, once. Sign in at
`https://<your-domain>/console`.

## 5. A demo database (optional)

For a public demo, load the sample garment ERP into a **separate** Railway Postgres:

```bash
python eval/datasets/erp_demo.py "<that database's public URL with a superuser>"
```

Connect it in the console with `postgresql://erp_readonly:erp_readonly@<public host>:<port>/erp_demo`.
Use the database's *public* address: with `BLOCK_PRIVATE_NETWORKS=true` the API refuses
Railway's private network on purpose.

## 6. Console on Vercel (optional)

1. https://vercel.com → **Add New → Project → import the repository**.
2. **Root Directory**: `console`. Vercel reads `console/vercel.json`.
3. **Environment Variables**: `VITE_API_URL` = `https://<your Railway domain>`.
4. Deploy. If `CORS_ALLOW_ORIGINS` is not `*`, add the Vercel address to it.

## 7. Check it

```bash
curl https://<domain>/v1/health                       # {"status": "ok", ...}
curl -I https://<domain>/widget.js                    # 200
```

Then in the console: connect the demo database, scan it, upload a PDF from
`python eval/datasets/make_pdfs.py out/`, and ask a question on **Try it**.

## Updating

Every push to `main` redeploys both services. Database changes are applied automatically by
`datachat-agent init-db` when the API starts.
