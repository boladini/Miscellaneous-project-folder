# API reference (v1)

Interactive OpenAPI docs: `/api/v1/docs` · schema: `/api/v1/openapi.json`.

Auth: session cookie from `POST /api/v1/auth/login`; every mutating request must send `X-CSRF-Token` equal to the `lab_csrf` cookie. Errors: `{"error": {"code", "message", "correlation_id"}}`. Every response carries `x-correlation-id`. Jobs return `202` + `job_id`; poll `GET /api/v1/jobs/{id}`.

| Method | Path | Summary |
|---|---|---|
| GET | `/api/v1/health` | health |
| POST | `/api/v1/auth/login` | login |
| POST | `/api/v1/auth/logout` | logout |
| GET | `/api/v1/auth/me` | me |
| GET | `/api/v1/dashboard` | dashboard |
| GET | `/api/v1/portfolios` | portfolios |
| GET | `/api/v1/portfolios/{pid}` | portfolio |
| GET | `/api/v1/portfolios/{pid}/series` | portfolio_series |
| POST | `/api/v1/portfolios/{pid}/pause` | pause |
| POST | `/api/v1/portfolios/{pid}/resume` | resume |
| GET | `/api/v1/proposals` | proposals |
| GET | `/api/v1/proposals/{prid}` | proposal |
| POST | `/api/v1/proposals/{prid}/decision` | decision |
| POST | `/api/v1/proposals/{prid}/qa` | ask |
| GET | `/api/v1/experiments/{eid}` | experiment |
| GET | `/api/v1/experiments/{eid}/trades` | experiment_trades |
| GET | `/api/v1/experiments/{eid}/reviews` | experiment_reviews |
| POST | `/api/v1/experiments/{eid}/close` | close_exp |
| GET | `/api/v1/research/runs` | research_runs |
| GET | `/api/v1/research/runs/{rid}` | research_run |
| GET | `/api/v1/evidence` | evidence |
| GET | `/api/v1/research/rejected` | rejected_ideas |
| GET | `/api/v1/backtests` | backtests |
| GET | `/api/v1/backtests/{bid}` | backtest |
| POST | `/api/v1/backtests` | backtest_create |
| GET | `/api/v1/journal` | journal |
| GET | `/api/v1/notifications` | notifications |
| POST | `/api/v1/notifications/{nid}/read` | notif_read |
| POST | `/api/v1/notifications/read-all` | notif_read_all |
| POST | `/api/v1/jobs/research` | job_research |
| POST | `/api/v1/jobs/market-refresh` | job_refresh |
| GET | `/api/v1/jobs` | jobs_list |
| GET | `/api/v1/jobs/{jid}` | job_get |
| GET | `/api/v1/settings` | settings_get |
| PATCH | `/api/v1/settings` | settings_patch |
| POST | `/api/v1/admin/global-pause` | global_pause |
| POST | `/api/v1/admin/reset` | admin_reset |
| GET | `/api/v1/export` | export |
| GET | `/api/v1/system/status` | system_status |
| POST | `/internal/cron/tick` | cron_tick |

`POST /internal/cron/tick` requires header `X-Cron-Secret: $CRON_SECRET` (for managed cron; never public).
`POST /api/v1/proposals/{id}/decision` body: `{"decision": "yes"|"no", "idempotency_key": str, "expected_scope_hash": str}`. Response reflects actual state (e.g. *Approved — awaiting eligible market data/signal*), never an optimistic fill.
