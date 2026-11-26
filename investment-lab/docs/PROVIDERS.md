# Providers, quotas, licensing and costs

What was **actually observed** during implementation (2026-11-26, from the build environment) is marked ✅/❌.
Quotas and prices below are my best understanding and **must be re-verified by the owner** on each vendor's
pricing page before relying on them.

## Market data

| Provider | Adapter | Observed | Notes |
|---|---|---|---|
| Demo (synthetic) | `DemoMarketProvider` | ✅ | Deterministic generated series (`SYN01…`, `SYNBM`), with a split, dividends, a delisting, a halt and earnings events. **Not real data.** |
| Tiingo EOD | `TiingoProvider` | not tested (no key) | **Recommended.** Raw OHLCV plus `divCash` and `splitFactor`, so dividends and splits are explicit. The free tier has historically allowed about 1,000 req/day and 500 symbols/month for personal use. Check commercial licensing. No earnings events. |
| Alpha Vantage | `AlphaVantageProvider` | ❌ with the `demo` key (only exact sample URLs are allowed); response format verified for `TIME_SERIES_DAILY` | Raw daily bars, DIVIDENDS, SPLITS, EARNINGS. The free tier (~25 req/day) is too small for a 40-symbol universe. Earnings estimates are **not certified point-in-time**, so event backtests are labeled limited. |
| Stooq | `StooqProvider` | ❌ connection reset from this environment | Provider-adjusted prices (dividends not credited separately). Unreliable for automated use. |

The system **refuses to mix adjustment conventions** for an instrument (ingestion skips and reports it).
Benchmark performance is **price-only** (the label is shown in the UI).

## Research sources

| Source | Observed | Requirement |
|---|---|---|
| OpenAlex works API | ✅ real results (e.g. *“Rest in Peace Post-Earnings Announcement Drift”*); a live research run stored 175 sources / 156 evidence records | Free, CC0 metadata; optional `OPENALEX_MAILTO` for the polite pool |
| Crossref REST | ✅ real results | Free; optional mailto |
| SEC EDGAR full-text search | reachable (HTTP 200) | `SEC_USER_AGENT` with contact info (SEC fair-access policy, ≤10 req/s) |
| Reddit (official API) | not tested | OAuth app credentials; social posts are **leads only** |
| X API | not tested | Paid API tier; leads only |
| SSRN / institutional letters / paywalled news | not integrated | No compliant free API; covered indirectly via Crossref/OpenAlex DOIs. Listed as *unavailable* in coverage. |

## AI model

Anthropic Claude via the official SDK (`ANTHROPIC_API_KEY`, default model `claude-opus-5-5`, effort `medium`,
server-side refusal fallback enabled). Without a key, the lab runs in **template mode**: deterministic keyword
evidence extraction plus a library of five falsifiable rule templates. This is weaker. Keyword matching labels many
topical papers as "supporting", so the PRD's richer evidence assessment really needs the LLM.

## Estimated monthly operating cost (single user)

| Item | Estimate |
|---|---|
| Claude (Opus 5.5 at $4/$20 per M tokens): weekly research of ~10–15 calls, ~0.1–0.2M input / ~30k output tokens | ≈ $1–2 per run, capped by `RESEARCH_MAX_COST_USD` (default $2) → **≈ $5–9/month** including monthly reviews and occasional Q&A |
| Market data: Tiingo (personal free tier) / paid tier for commercial or broader use | $0 – ~$30/month |
| Alpha Vantage premium (only if you want event-driven earnings data) | ~$50/month |
| Hosting: web service + background worker + managed Postgres (e.g. Render/Railway/Fly) | ≈ $20–40/month |
| Email (SMTP, e.g. a transactional provider's free tier) | $0 |
| **Typical total** | **≈ $25–50/month**, or ≈ $75–100 with paid earnings data |
