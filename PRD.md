# PRD: Airbnb Price Quote Agent

**Status:** Draft v1.0
**Owner:** TBD
**Last updated:** 2026-04-19

---

## 1. Summary

An AI agent that, given a city and a date range, queries Airbnb listings and returns price statistics (mean, median, min, max, P25/P75) for two listing segments:

1. **Superhost** listings
2. **Guest Favorite** listings

The agent exposes a conversational interface (chat) and a REST API. Results are returned as structured JSON plus a human-readable summary.

---

## 2. Goals & Non-Goals

### Goals
- Accept a natural-language or structured request: `{city, check_in, check_out, guests?, filters?}`.
- Return **per-night price** statistics for Superhost-only and Guest-Favorite-only listings.
- Return the same stats for the **combined set** and an **all-listings** baseline.
- Complete a single-city query in ≤ 30 seconds (P95).
- Cache results for 24h to reduce cost and rate-limit exposure.

### Non-Goals (v1)
- Booking or reservation actions.
- Multi-city comparisons in a single call (queue separate calls).
- Amenity-level filtering beyond Superhost / Guest Favorite.
- Historical pricing or forecasting.
- Mobile app (API + web chat only).

---

## 3. Users & Use Cases

| User | Use case |
|---|---|
| Traveler | "What's a fair nightly budget for Lisbon June 10–14?" |
| Host / investor | Benchmark pricing against Superhost/Guest-Favorite comps. |
| Analyst | Pull daily price distributions into a spreadsheet via API. |

---

## 4. Functional Requirements

### 4.1 Input
- `city` (string, required) — free-text; geocoded to Airbnb `place_id`.
- `check_in` (ISO date, required).
- `check_out` (ISO date, required; must be > `check_in`; max stay 28 nights).
- `guests` (int, default 2).
- `currency` (ISO 4217, default `USD`).
- `max_listings` (int, default 300, cap 1000).

### 4.2 Output (JSON)
```json
{
  "query": { "city": "Lisbon, PT", "check_in": "2026-06-10", "check_out": "2026-06-14", "nights": 4, "guests": 2, "currency": "USD" },
  "fetched_at": "2026-04-19T14:22:10Z",
  "segments": {
    "superhost":      { "n": 142, "mean": 186.40, "median": 172.00, "min": 78, "max": 612, "p25": 138, "p75": 220, "stdev": 61.2 },
    "guest_favorite": { "n":  98, "mean": 201.10, "median": 188.50, "min": 92, "max": 540, "p25": 150, "p75": 238, "stdev": 58.7 },
    "both_badges":    { "n":  61, "mean": 210.50, "median": 195.00, "min": 95, "max": 540, "p25": 158, "p75": 245, "stdev": 59.1 },
    "all_listings":   { "n": 300, "mean": 165.30, "median": 150.00, "min": 42, "max": 780, "p25": 110, "p75": 205, "stdev": 72.9 }
  },
  "notes": ["Prices are per-night, exclusive of cleaning and service fees.", "Outliers >3σ excluded from mean."],
  "cache": { "hit": false, "ttl_seconds": 86400 }
}
```

### 4.3 Summary (LLM-generated)
A 2–3 sentence natural-language summary for chat UX, e.g.
> "In Lisbon for 4 nights starting Jun 10, Superhost listings run ~$172/night (median), with 50% of rooms between $138–$220. Guest-Favorite listings skew higher at ~$189."

### 4.4 Statistics
- **Mean:** arithmetic mean of per-night nightly rate, with values > μ+3σ excluded.
- **Median, Min, Max, P25, P75:** computed on the raw set (no outlier removal).
- Per-night rate = `total_before_taxes / nights`.
- If segment `n < 5`, return stats with a `low_sample` warning.

---

## 5. System Architecture

```
┌───────────┐    ┌───────────────┐    ┌─────────────────┐
│ Chat / UI │───▶│  API Gateway  │───▶│  Agent Runtime  │
└───────────┘    └───────────────┘    │  (Claude 4.7)   │
                                      └────────┬────────┘
                                               │ tools
                    ┌──────────────────────────┼───────────────────────┐
                    ▼                          ▼                       ▼
            ┌──────────────┐         ┌──────────────────┐      ┌──────────────┐
            │ Geocoder     │         │ Airbnb Fetcher   │      │ Stats Engine │
            │ (Mapbox)     │         │ (scraper+proxy)  │      │ (numpy)      │
            └──────────────┘         └────────┬─────────┘      └──────────────┘
                                              ▼
                                    ┌──────────────────┐
                                    │ Redis (24h TTL)  │
                                    └──────────────────┘
```

### 5.1 Components
- **API Gateway** — FastAPI; auth via API key; rate-limit 60 req/min/key.
- **Agent Runtime** — Claude Opus 4.7 via Anthropic SDK with tool use. System prompt enforces segment definitions and output schema.
- **Geocoder** — Mapbox Places API → normalized city + bounding box.
- **Airbnb Fetcher** — Pagination of Airbnb search results; rotating residential proxy pool; headless browser fallback (Playwright) for JS-rendered pages.
- **Stats Engine** — Pure Python (`numpy`, `statistics`) service.
- **Cache** — Redis keyed by `sha256(city|check_in|check_out|guests|currency)`; 24h TTL.
- **Storage** — Postgres for query logs and per-listing snapshots (for audit / reruns).

### 5.2 Agent Tools (Claude tool use)
| Tool | Input | Output |
|---|---|---|
| `geocode_city` | `{name}` | `{place_id, bbox, display_name}` |
| `search_airbnb` | `{place_id, check_in, check_out, guests, filters, cursor}` | `{listings[], next_cursor}` |
| `compute_stats` | `{prices[]}` | stats object |
| `summarize` | `{query, stats}` | short text |

Agent loop: geocode → paginate search (Superhost filter + Guest-Favorite filter + unfiltered) until `max_listings` or no cursor → dedupe → compute stats → summarize → return.

---

## 6. Data Source Strategy (CRITICAL)

Airbnb has no public API and ToS prohibits scraping. The PRD documents three implementation paths; **pick one before build**:

| Option | Pros | Cons | Legal risk |
|---|---|---|---|
| **A. Direct scrape** (Playwright + proxies) | Cheap, real-time | Breaks on layout changes; ToS violation | High |
| **B. Partner API** (AirDNA, Airbtics, MarketMinder) | Legal, stable | $200–$2k/mo; may not expose Guest Favorite badge | Low |
| **C. Airbnb Partner Program** | Fully legal, stable | Requires approval; limited access | None |

**Recommendation:** Start with Option B (AirDNA) for MVP legality; evaluate Option C for scale. Option A only for internal research with legal sign-off.

---

## 7. Non-Functional Requirements

- **Latency:** P50 ≤ 10s, P95 ≤ 30s per query (with cache miss).
- **Availability:** 99.0% (single-region acceptable for MVP).
- **Cost:** ≤ $0.15 per uncached query (LLM + proxy + data).
- **Concurrency:** Support 20 simultaneous queries.
- **Observability:** Structured logs (JSON), tracing via OpenTelemetry, Prometheus metrics (`queries_total`, `cache_hit_ratio`, `fetch_latency_ms`, `llm_tokens_total`).
- **Security:** API keys hashed at rest; proxy creds in secrets manager; no PII stored.

---

## 8. API Spec

### `POST /v1/price-quote`
**Request**
```json
{ "city": "Lisbon", "check_in": "2026-06-10", "check_out": "2026-06-14", "guests": 2, "currency": "USD" }
```
**Response** — see §4.2.
**Errors**
- `400 INVALID_DATES` — check_out ≤ check_in, or range > 28 nights.
- `404 CITY_NOT_FOUND` — geocoder returned no match.
- `429 RATE_LIMITED`
- `502 UPSTREAM_UNAVAILABLE` — Airbnb / data provider failure.
- `504 TIMEOUT`

### `GET /v1/price-quote/{id}`
Fetch a prior query by ID (from query log).

---

## 9. Agent Prompt (System)

```
You are a price-research agent for short-term rentals. For each user request:
1. Parse city + date range + guest count. Reject ambiguous inputs by asking one clarifying question.
2. Call geocode_city, then search_airbnb three times (filter=superhost, filter=guest_favorite, filter=none).
3. De-duplicate listings by listing_id across segments.
4. Call compute_stats per segment on per-night nightly rates.
5. Return the exact JSON schema in §4.2 plus a ≤3-sentence plain-English summary.
Never invent listings or prices. If n<5 for a segment, include a low_sample warning.
```

---

## 10. Milestones

| Wk | Deliverable |
|---|---|
| 1 | Data-source decision; contracts signed if Option B/C. |
| 2 | Fetcher + geocoder + cache (no LLM). CLI smoke test. |
| 3 | Stats engine; API gateway; auth; rate limit. |
| 4 | Claude agent with tool use; end-to-end JSON response. |
| 5 | Chat UI (Next.js); observability; load test. |
| 6 | Beta launch (10 users); iterate. |
| 8 | GA. |

---

## 11. Open Questions

1. Which data source (§6) — needs legal + budget decision by Wk 1.
2. Do we need per-night breakdown (weekend vs weekday) in v1? **Default: no.**
3. Should we expose an MCP server in addition to REST? **Default: yes, v1.1.**
4. Currency conversion source — FX daily snapshot or live? **Default: daily.**

---

## 12. Success Metrics

- ≥ 80% of queries return with `n ≥ 20` for Superhost segment.
- Cache hit ratio ≥ 40% after first month.
- P95 latency ≤ 30s.
- User CSAT ≥ 4.2 / 5 after 100 queries.
