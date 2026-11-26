# Autonomous AI Investment Laboratory

A personal experimental fund run by AI agents, using **paper trading only**. The agents research investing ideas each
week, turn the credible ones into falsifiable rule sets, backtest and critique them, and put up to three proposals in
your inbox. You answer **Yes** or **No**. Approved strategies then trade hypothetically and automatically using
recorded market data, under strict risk limits. A background worker runs everything, whether or not the website is
open. Monthly reviews compare results with each strategy's pre-registered prediction and propose changes, which
also need your approval.

> **No real money.** There is no brokerage integration and no code path that places real orders. Demo mode uses
> clearly labeled **synthetic** data.

* Architecture: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
* Requirements checklist: [`docs/CHECKLIST.md`](docs/CHECKLIST.md)
* Test report: [`docs/TEST_REPORT.md`](docs/TEST_REPORT.md)
* API: [`docs/API.md`](docs/API.md) (interactive docs at `/api/v1/docs`)
* Schema: [`docs/DATABASE.md`](docs/DATABASE.md)
* Providers, quotas, costs: [`docs/PROVIDERS.md`](docs/PROVIDERS.md)

## Quick start (Docker, one command)

```bash
cd investment-lab
cp .env.example .env          # set ADMIN_EMAIL, ADMIN_PASSWORD, SECRET_KEY
docker compose up --build     # postgres + api (http://localhost:8000) + worker
```

Sign in with `ADMIN_EMAIL` / `ADMIN_PASSWORD`. The default `LAB_MODE=demo` uses deterministic synthetic prices
(`SYN01…`, benchmark `SYNBM`) and synthetic research sources marked `[SYNTHETIC]`. The worker runs this week's
research on start-up, and proposals appear in **Approvals** within about a minute.

## Local development (without Docker)

Requires Python 3.12+, Node 20+, PostgreSQL 16.

```bash
createdb lab && createdb lab_test                    # role lab/lab, or set DATABASE_URL
cd investment-lab/backend
pip install -r requirements-dev.txt
alembic upgrade head
uvicorn lab.api.app:app --reload --port 8000         # terminal 1: API (+ built SPA)
python -m lab.worker                                 # terminal 2: scheduler + job worker
cd ../frontend && npm install && npm run dev         # terminal 3: SPA with hot reload on :5173 (proxies /api)
```

Run the tests with `cd backend && python -m pytest -q`. They use `TEST_DATABASE_URL`, defaulting to
`postgresql+psycopg://lab:lab@localhost:5432/lab_test`. Build the SPA into `backend/static` with
`cd frontend && npm run build`.

Operator CLI (`python -m lab.cli …`): `bootstrap`, `ingest`, `research`, `daily [YYYY-MM-DD]`,
`monthly [YYYY-MM]`, `tick`, `backtest <strategy_version_id>`, `smoke-live`.

## Demo mode vs live-paper mode

| | `LAB_MODE=demo` | `LAB_MODE=live` |
|---|---|---|
| Prices / events | deterministic synthetic generator | `MARKET_PROVIDER` (Tiingo recommended; Alpha Vantage for earnings events) |
| Research | synthetic library (labeled) | OpenAlex, Crossref, SEC EDGAR, Reddit, X (whichever are configured) |
| Trading | paper | paper ("live" refers to the data only) |
| Data namespace | `instruments.namespace = demo` | `live` (use a **separate database** for each mode) |

Without market-data credentials, live mode still researches. It then **publishes no proposals**, because the rules
cannot be validated, and the health panel says why.

## Configuration

Every option is listed in [`.env.example`](.env.example). Secrets live only in the server environment or your host's
secret store, never in the browser. The settings that matter:

| Variable | Purpose |
|---|---|
| `DATABASE_URL`, `SECRET_KEY`, `ADMIN_EMAIL`, `ADMIN_PASSWORD`, `COOKIE_SECURE=true` (behind HTTPS) | core |
| `LAB_MODE`, `MARKET_PROVIDER`, `TIINGO_API_KEY`, `ALPHAVANTAGE_API_KEY`, `MARKET_UNIVERSE`, `BENCHMARK_SYMBOL` | data |
| `ANTHROPIC_API_KEY`, `LLM_MODEL` (default `claude-opus-5-5`), `RESEARCH_MAX_LLM_CALLS`, `RESEARCH_MAX_COST_USD` | AI research (template mode without a key) |
| `SEC_USER_AGENT`, `OPENALEX_MAILTO`, `REDDIT_CLIENT_ID/SECRET`, `X_BEARER_TOKEN` | research sources |
| `SMTP_*`, `NOTIFY_EMAIL_TO` | optional email for urgent alerts |
| `CRON_SECRET` | enables `POST /internal/cron/tick` for managed-cron hosting |

The schedule defaults to research on Monday at 07:00 America/Chicago, the daily cycle after 18:30 ET on each trading
day, and the learning review on the first trading Monday of each month. You can change these in Settings.

## Deployment (owner steps)

The app needs **an always-on worker**. Serverless alone is not enough.

1. **Database:** a managed PostgreSQL 16 instance (Render, Railway, Supabase, Neon, RDS…) with daily backups enabled.
   Copy its URL with the `postgresql+psycopg://` scheme.
2. **Web service:** deploy this repo's `investment-lab/Dockerfile` (default command runs migrations, then the API on
   `$PORT`). Set the environment variables from `.env.example`, `LAB_MODE=live`, `COOKIE_SECURE=true`, and a long
   random `SECRET_KEY`. Health check: `/api/v1/health`.
3. **Worker:** the same image, with start command `sh -c "alembic upgrade head && exec python -m lab.worker"`, the
   same environment, and always on.
   *Alternative:* skip the worker and point a managed cron at `POST https://<host>/internal/cron/tick` every
   10 minutes with header `X-Cron-Secret: $CRON_SECRET`. Each call runs due jobs for up to about 45 seconds.
   Long research runs suit a real worker better.
4. **HTTPS:** your host terminates TLS. Keep `COOKIE_SECURE=true`.
5. **Backups:** use the managed DB's snapshots, or run `scripts/backup.sh` daily (pg_dump). Test restores with
   `scripts/restore.sh` into an empty database. Targets: RPO 24h, RTO 4h.
6. **Smoke test:** `python -m lab.cli smoke-live` on the worker. It reports exactly which providers responded.

Example on Render: one *Web Service* plus one *Background Worker* from the same repo/Dockerfile, plus a *PostgreSQL*
instance. CI is in `.github/workflows/investment-lab-ci.yml` (lint, migrations up/down, tests, build, bundle secret
scan, dependency audit).

## Troubleshooting

| Symptom | Fix |
|---|---|
| "No daily cycle has completed yet" | The worker isn't running, or the first session close after inception hasn't arrived yet. |
| Live mode publishes nothing | Expected without market-data keys. Check Settings → System health → Market provider. |
| "provider_mismatch" in ingestion | You changed providers with a different adjustment basis. Use a new database (new inception) instead of mixing. |
| Proposal decision returns `blocked` | The portfolio's risk budget changed since the proposal was made. Wait for a fresh proposal. |
| Alpha Vantage "demo key" error | Get a real key. The demo key only serves exact sample URLs. |

## What the lab will not do

It won't place real trades, use margin, sell short, trade options or crypto, invent prices, citations or
performance, change approved rules silently, or claim live success it didn't observe. A week with zero proposals
is a valid outcome.
