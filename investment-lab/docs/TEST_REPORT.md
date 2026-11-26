# Test report — 2026-11-26

## Automated (CI-equivalent, run locally on PostgreSQL 16.15, Python 3.13)

`cd backend && python -m pytest -q` → **68 passed, 0 failed** (≈61 s). The suite uses deterministic fixtures and
makes no external network calls.

| File | Tests | Covers |
|---|---|---|
| `test_pure.py` | 29 | calendar (holidays, early close, DST, never-past-open), schema rejection (vague, look-ahead, code/SQL injection, caps), indicator math, look-ahead guards, risk limits, fill model, SSRF/URL safety, HTML sanitisation |
| `test_engine.py` | 26 | AC1, AC3–AC6, AC9, AC11: approvals/rejections/expiry/idempotency, fills, exits, gap-through-stop, split + dividend idempotency, missing data/delisting, 4-thread concurrency, gap-up resize, simultaneous proposals, pause, breaker, user close, benchmark shadow, catch-up without retroactive signals, scheduler idempotency + lease recovery, dead-letter, notification dedupe, export/reset archival, learning + versioned modification with legacy rules, invalidation, tenant isolation |
| `test_backtest_research.py` | 7 | AC7 (known trade + fill timing, prefix invariance, stress costs, not-backtestable, metric fixtures, undefined metrics); AC2/AC10 (provenance, unsafe URLs dropped, honest abstention, fabricated citations rejected, invalid model output quarantined, prompt-injection text delimited and never acted on) |
| `test_api.py` | 6 | auth/401 structured errors, CSRF, every route, decision over HTTP (idempotent), Q&A cannot change rules, settings/policy versions, manual-job dedupe, cross-tenant 404s, cron secret, full synthetic demo end-to-end |

Other automated checks: `ruff` (F, E9) clean; `tsc -b && vite build` clean; Alembic upgrade → downgrade → upgrade on
an empty DB; `scripts/check_bundle_secrets.sh` clean.

## Manual / environment verifications

| Check | Result |
|---|---|
| Playwright screenshots of all routes at 1366×900 and 390×844 | ✅ no console errors except the expected pre-login 401; no horizontal overflow (after fixes) |
| Docker image build (multi-stage) and container stack (Postgres + API + worker) | ✅ the build needed the sandbox's proxy CA injected through a temporary derived Dockerfile; the committed Dockerfile is unchanged |
| Worker runs research with no browser open; restart does not duplicate jobs | ✅ |
| Backup → restore into a fresh DB | ✅ proposals, ledger (sum $50,000.00), 34,682 bars and evidence counts identical |
| Live research retrieval (OpenAlex, Crossref) | ✅ 40 queries, 175 sources, 156 evidence records; 0 proposals because market data was not configured (correct abstention) |
| Live market data (Tiingo / Alpha Vantage / Stooq) | ❌ not observed: no keys; Alpha Vantage rejects the `demo` key on parameterised calls; Stooq unreachable |
| Live Claude calls | ❌ not observed: no `ANTHROPIC_API_KEY` (structured-output paths tested with a scripted fake) |
| Real approval followed by a live-data fill | ❌ not observed. Deterministic fill proof is in `test_ac4_*` |

## Bugs found and fixed during verification
* psycopg `rowcount` is unreliable for `INSERT … ON CONFLICT DO NOTHING` (it reported −55 and broke "is new" checks). Replaced with `RETURNING` everywhere.
* The backtest compared experiment capital with the benchmark on diluted whole-portfolio NAV, which inflated Sharpe and gave an unfair comparison. Metrics are now on allocated capital with the benchmark sized to match.
* In LLM mode, records the model judged irrelevant fell back to keyword extraction. Fallback is now limited to records the model never assessed.
* `.env.example` inline comments broke Docker `--env-file`. Comments are now on their own lines.
* Phone-width overflow from long rule strings and wide badges.
