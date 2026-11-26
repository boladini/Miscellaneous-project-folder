# Implementation checklist (FR-01 – FR-11, acceptance criteria)

Legend: ✅ implemented + automated test · ☑️ implemented, verified manually or by inspection only · 🟡 partial ·
⛔ blocked on an owner credential/access · ⏸ deferred (not built)

## Functional requirements

### FR-01 Autonomous discovery & research
| Item | Status | Where / evidence |
|---|---|---|
| Literature retrieval (OpenAlex, Crossref) | ✅ / ☑️ live | `providers/research.py`; a live run stored 175 real sources |
| SEC EDGAR filings | 🟡 ⛔ | Adapter built; needs `SEC_USER_AGENT` |
| Reddit / X via permitted APIs | 🟡 ⛔ | Adapters built; need credentials; shown as *unavailable* in coverage |
| SSRN, investor letters, financial news | ⏸ | No compliant free API; DOIs via Crossref/OpenAlex only |
| Provenance (URL, title, author, dates, retrieval id, source type, claim, limitations, excerpt) | ✅ | `sources`, `evidence` tables; research tests |
| URL safety + claim-support verification (verbatim excerpt) | ✅ | `assert_safe_url`, `claim_supported`; tests |
| Query / model / job id / prompt version recorded | ✅ | `research_runs`, `llm_calls` |
| Dedupe by thesis + rule fingerprint; revisit rejected ideas only with new evidence | ✅ ☑️ | `agents/research.py` (duplicate rule verified by worker run) |
| Social claims are leads; 13F treated as lagged | 🟡 | Social → `background` with a limitation note; 13F not ingested (⏸) |

### FR-02 Strategy generation & ranking
| Item | Status |
|---|---|
| Five families with templates (contrarian, growth, asymmetric, event-driven, AI-generated) | ✅ |
| Versioned declarative schema: universe, data, filters, entry, ranking, sizing, timing, exits, holding, costs, risk budget, catalyst, invalidation, data policy | ✅ `strategy_schema.py` |
| Whitelisted operators/indicators, ranges, units, nesting depth, immutable hash, warmups | ✅ tests |
| Reject vague rules / arbitrary code / SQL | ✅ tests |
| Scoring 25/20/20/15/10/10 with stored components and rationale | ✅ |
| Hard gates before score; score never overrides a gate | ✅ |
| Exploratory label with tighter limits when validation is not robust | ✅ (allocation halved) |
| LLM strategy agent with citation restriction | ✅ (FakeLLM tests) · live Claude run ⛔ no key |

### FR-03 Approval inbox & AI interview
| Item | Status |
|---|---|
| ≤3 ranked cards with all required fields; expandable evidence/backtest/rules/risk/critique/score | ✅ UI ☑️ screenshots |
| One-click Yes/No; Yes shows the authorization statement | ✅ |
| Idempotent, transactional decision with scope hash; statuses pending/approved/rejected/expired/blocked/withdrawn | ✅ tests |
| 7-day validity; revalidation at approval (budget/constraints) | ✅ |
| Expire on *material change in evidence or prices* | 🟡 constraint/budget changes block; price/evidence drift does not auto-expire |
| Optional Q&A with citations, cannot change rules | ✅ (deterministic fallback without LLM) |
| Rejected/expired searchable; notification with deep link | ✅ |

### FR-04 Paper trading & execution
| Item | Status |
|---|---|
| Deterministic engine separate from LLM; intents → risk → orders → fills | ✅ |
| Exchange calendar: holidays, early closes, DST | ✅ tests |
| Whole-share market orders; buy/sell; cash; no shorts/leverage/negative cash | ✅ tests |
| Next-eligible-open fills + adverse slippage; never a past open; fill time recorded separately from processing time | ✅ tests |
| No data → no fill; expiry after 3 eligible sessions | ✅ test |
| Decimal money, append-only ledger, locking, idempotency keys, reservations | ✅ tests |
| Splits, cash dividends, delisting/halts (unresolved valuation) | ✅ tests |
| Symbol changes / mergers | 🟡 flagged for manual reconciliation (fills suspended) |
| Fractional shares setting | ⏸ schema flag exists; engine sizes whole shares |
| Partial exits | 🟡 ledger/lot logic supports partial sells; no rule type emits them yet |
| Intraday mode | ⏸ (optional in PRD) |

### FR-05 Risk management
| Item | Status |
|---|---|
| Ticker 10% / ETF 25% / sector 20% / experiment 30% / binary 5% / min cash 10% / min price $5 / ADV $5M / stale quotes | ✅ tests |
| Consolidated ticker exposure across experiments | ✅ |
| 15% drawdown circuit breaker; resume only by explicit approval | ✅ test |
| Global + portfolio pause; exits continue | ✅ tests |
| Server-side enforcement in the execution transaction | ✅ |
| Exposure drift recorded (no forced sells on appreciation) | 🟡 exposure shown per snapshot; no dedicated drift log |

### FR-06 Market & event data
| Item | Status |
|---|---|
| Provider interfaces: bars, metadata, corporate actions, events, calendar | ✅ |
| Tiingo / Alpha Vantage / Stooq adapters | 🟡 ⛔ coded; real fetch not verified (keys missing, Stooq unreachable) |
| Store provider, timestamps, adjustment basis, quality | ✅ |
| Retries with backoff + jitter, quota tracking, health panel, stale badges | ✅ ☑️ |
| Never silently switch adjustment conventions | ✅ ingestion guard |
| FDA / official event sources | ⏸ |
| Point-in-time constituents | ⛔ not available free; the limitation is labeled on every backtest |

### FR-07 Backtesting
✅ same interpreter + execution model; 5-year window; 60/20/20 chronological partitions; base vs stress costs; parameter
sensitivity; return/CAGR/vol/Sharpe(rf=0)/max DD/trades/win rate/turnover/exposure/excess; manifest (dataset
fingerprint, rule hash, code version); not-backtestable instead of random results; prefix-invariance (no look-ahead)
test. 🟡 artifacts stored in Postgres JSON rather than object storage (fine at this scale).

### FR-08 Monitoring, alerts, journal
| Item | Status |
|---|---|
| Daily marking, signals, exits, activity recorded even with no trades | ✅ |
| Thesis statuses + pre-approved invalidation rules; profitable trade ≠ validated hypothesis | ✅ test |
| Alerts: proposal, fill, exit, drawdown, thesis, stale data, job failure, completion; deduped; data timestamp; "PAPER TRADING" | ✅ tests |
| Email delivery (SMTP, retries, separate from trade transactions) | 🟡 implemented; not tested against a real SMTP server |
| Batched routine-fill digest email | ⏸ (digest is a severity label in-app) |

### FR-09 Analytics & benchmark
✅ NAV = cash + marked positions; P&L identity reconciliation; matched-inception price-only shadow benchmark;
leaderboard with date/family filters that never ranks insufficient history as zero; equity, drawdown and comparison
charts from stored snapshots only; attribution by experiment, ticker and holding period; CSV/JSON export.

### FR-10 Learning & adjustment
✅ monthly review vs pre-registered prediction and backtest expectation; minimum-sample verdicts; structured lessons;
pause/retire/modify become proposals; modifications create immutable versions with a diff; legacy holdings keep
original rules (test); bounded learning influence on ranking (±15 points); candidate history.

### FR-11 Settings & administration
| Item | Status |
|---|---|
| Secure login (bcrypt, signed HttpOnly cookie, CSRF, rate limits), tenant-scoped data | ✅ tests |
| Managed auth / OIDC | ⏸ single-owner password login instead (documented deviation) |
| Timezone, schedules, risk defaults (new policy versions), notification prefs, min score | ✅ |
| Credentials only in server env | ✅ (bundle check in CI) |
| Run history, provider quota, next runs, actionable errors; manual research/refresh with rate limits; emergency pause | ✅ |
| Export; reset with typed confirmation + archival | ✅ tests |

## Acceptance criteria

| # | Status | Evidence |
|---|---|---|
| 1 Five $10k portfolios + benchmark baselines, no fake history | ✅ | `test_ac1_*`; dashboard screenshot shows an empty-state chart |
| 2 Research without browser; traceable sources; 0–3 proposals; bad citations and invalid rules rejected | ✅ | worker container run; research tests; live OpenAlex/Crossref run |
| 3 One-click approve/reject; rejected/expired never order; idempotent; no past-open fill | ✅ | `test_ac3_*` |
| 4 Fixture buy at modeled price/cost; no signal → no order; no data → no fill | ✅ | `test_ac4_*`, `test_no_data_*` |
| 5 Exit + P&L reconciliation; gap-through-stop; split, dividend, missing quote, delisting | ✅ | `test_ac5_*` |
| 6 Concurrency, reservations, simultaneous proposals, pause, breaker, isolation | ✅ | `test_ac6_*` |
| 7 Eligible observations only; look-ahead rejected; metrics verified; stress costs; missing history; undefined metrics | ✅ | `test_backtest_*`, `test_metric_*` |
| 8 Every route interactive with stored data; phone + desktop; persistence across relogin | ✅ ☑️ | API route test; Playwright screenshots at 1366px and 390px with no overflow |
| 9 Monitoring + monthly learning records; changes need approval; legacy rules | ✅ | `test_ac9_*`, invalidation test |
| 10 Stale/failed visible; deduped alerts; no secrets in bundle; tenant isolation; prompt injection; malicious URLs | ✅ | tests + `scripts/check_bundle_secrets.sh` |
| 11 Export; backup/restore; restart resumes without duplicates | ✅ ☑️ | export test; pg_dump/pg_restore with matching counts; worker container restart |
| 12 Credentialed live demonstration | 🟡 ⛔ | live research retrieval ✅; live market data, live Claude, real approval → live fill **not observed** (keys missing) |

**Not done: deployment.** No hosting credentials were provided, so the app has **not** been deployed. Instructions
are in the README.
