# Database schema

Generated from `backend/lab/models.py` (SQLAlchemy) — migrations in `backend/migrations/` (Alembic).
All timestamps are UTC (`timestamptz`); money/quantities are `NUMERIC(20,6)`. User-owned tables carry `user_id` for tenant isolation.
Immutable by convention and API: `approvals`, `strategy_versions`, `ledger_entries`, filled `orders`. Holdings and NAV reconcile to the ledger.

## `audit_events`
| Column | Type | Null | Key |
|---|---|---|---|
| id | INTEGER | no | PK |
| user_id | VARCHAR(32) | yes |  |
| actor | VARCHAR(64) | no |  |
| action | VARCHAR(64) | no |  |
| entity_type | VARCHAR(32) | no |  |
| entity_id | VARCHAR(64) | yes |  |
| prior_hash | VARCHAR(64) | yes |  |
| new_hash | VARCHAR(64) | yes |  |
| payload | JSON | no |  |
| correlation_id | VARCHAR(64) | yes |  |
| occurred_at | DATETIME | no |  |

## `instruments`
| Column | Type | Null | Key |
|---|---|---|---|
| id | VARCHAR(32) | no | PK |
| namespace | VARCHAR(8) | no |  |
| symbol | VARCHAR(16) | no |  |
| name | VARCHAR(200) | yes |  |
| exchange | VARCHAR(16) | yes |  |
| currency | VARCHAR(3) | no |  |
| type | VARCHAR(8) | no |  |
| sector | VARCHAR(64) | yes |  |
| status | VARCHAR(16) | no |  |
| active_from | DATE | yes |  |
| active_to | DATE | yes |  |
| created_at | DATETIME | no |  |

Unique: (namespace, symbol)

## `llm_calls`
| Column | Type | Null | Key |
|---|---|---|---|
| id | VARCHAR(32) | no | PK |
| user_id | VARCHAR(32) | yes |  |
| research_run_id | VARCHAR(32) | yes |  |
| role | VARCHAR(32) | no |  |
| provider | VARCHAR(32) | no |  |
| model | VARCHAR(64) | yes |  |
| prompt_version | VARCHAR(32) | no |  |
| input_hash | VARCHAR(64) | no |  |
| status | VARCHAR(16) | no |  |
| attempts | INTEGER | no |  |
| input_tokens | INTEGER | no |  |
| output_tokens | INTEGER | no |  |
| cost_usd | FLOAT | no |  |
| output | JSON | yes |  |
| error | TEXT | yes |  |
| created_at | DATETIME | no |  |

## `provider_status`
| Column | Type | Null | Key |
|---|---|---|---|
| name | VARCHAR(64) | no | PK |
| kind | VARCHAR(16) | no |  |
| state | VARCHAR(16) | no |  |
| last_success_at | DATETIME | yes |  |
| last_error_at | DATETIME | yes |  |
| last_error | TEXT | yes |  |
| calls_today | INTEGER | no |  |
| calls_day | DATE | yes |  |
| quota_note | TEXT | yes |  |
| updated_at | DATETIME | no |  |

## `users`
| Column | Type | Null | Key |
|---|---|---|---|
| id | VARCHAR(32) | no | PK |
| email | VARCHAR(320) | no |  |
| password_hash | VARCHAR(200) | no |  |
| timezone | VARCHAR(64) | no |  |
| created_at | DATETIME | no |  |

Unique: (email)

## `corporate_actions`
| Column | Type | Null | Key |
|---|---|---|---|
| id | VARCHAR(32) | no | PK |
| instrument_id | VARCHAR(32) | no | FK→instruments.id |
| type | VARCHAR(24) | no |  |
| ex_date | DATE | no |  |
| pay_date | DATE | yes |  |
| ratio | NUMERIC(20, 10) | yes |  |
| amount | NUMERIC(20, 6) | yes |  |
| known_at | DATETIME | no |  |
| source | VARCHAR(64) | no |  |
| needs_manual_reconciliation | BOOLEAN | no |  |
| payload | JSON | no |  |
| created_at | DATETIME | no |  |

Unique: (instrument_id, type, ex_date)

## `jobs`
| Column | Type | Null | Key |
|---|---|---|---|
| id | VARCHAR(32) | no | PK |
| user_id | VARCHAR(32) | no | FK→users.id |
| type | VARCHAR(32) | no |  |
| schedule_key | VARCHAR(120) | no |  |
| state | VARCHAR(16) | no |  |
| attempts | INTEGER | no |  |
| max_attempts | INTEGER | no |  |
| run_after | DATETIME | no |  |
| lease_owner | VARCHAR(64) | yes |  |
| lease_expires_at | DATETIME | yes |  |
| payload | JSON | no |  |
| result | JSON | yes |  |
| progress | JSON | no |  |
| error | TEXT | yes |  |
| trigger | VARCHAR(16) | no |  |
| started_at | DATETIME | yes |  |
| finished_at | DATETIME | yes |  |
| created_at | DATETIME | no |  |

Unique: (user_id, schedule_key)

## `market_bars`
| Column | Type | Null | Key |
|---|---|---|---|
| id | INTEGER | no | PK |
| instrument_id | VARCHAR(32) | no | FK→instruments.id |
| session | DATE | no |  |
| open | NUMERIC(20, 6) | no |  |
| high | NUMERIC(20, 6) | no |  |
| low | NUMERIC(20, 6) | no |  |
| close | NUMERIC(20, 6) | no |  |
| volume | NUMERIC(24, 2) | no |  |
| provider | VARCHAR(32) | no |  |
| observed_at | DATETIME | no |  |
| received_at | DATETIME | no |  |
| adjustment_basis | VARCHAR(24) | no |  |
| quality | VARCHAR(24) | no |  |
| currency | VARCHAR(3) | no |  |

Unique: (instrument_id, session)

## `market_events`
| Column | Type | Null | Key |
|---|---|---|---|
| id | VARCHAR(32) | no | PK |
| instrument_id | VARCHAR(32) | no | FK→instruments.id |
| type | VARCHAR(24) | no |  |
| event_date | DATE | no |  |
| known_at | DATETIME | no |  |
| source | VARCHAR(64) | no |  |
| payload | JSON | no |  |
| created_at | DATETIME | no |  |

Unique: (instrument_id, type, event_date)

## `notifications`
| Column | Type | Null | Key |
|---|---|---|---|
| id | VARCHAR(32) | no | PK |
| user_id | VARCHAR(32) | no | FK→users.id |
| type | VARCHAR(32) | no |  |
| severity | VARCHAR(12) | no |  |
| title | TEXT | no |  |
| body | TEXT | no |  |
| link | TEXT | yes |  |
| dedupe_key | VARCHAR(200) | no |  |
| data_timestamp | DATETIME | yes |  |
| payload | JSON | no |  |
| delivery_state | VARCHAR(16) | no |  |
| delivery_attempts | INTEGER | no |  |
| read_at | DATETIME | yes |  |
| created_at | DATETIME | no |  |

Unique: (user_id, dedupe_key)

## `portfolios`
| Column | Type | Null | Key |
|---|---|---|---|
| id | VARCHAR(32) | no | PK |
| user_id | VARCHAR(32) | no | FK→users.id |
| family | VARCHAR(32) | no |  |
| name | VARCHAR(120) | no |  |
| generation | INTEGER | no |  |
| inception_at | DATETIME | no |  |
| starting_cash | NUMERIC(20, 6) | no |  |
| status | VARCHAR(16) | no |  |
| paused_reason | TEXT | yes |  |
| circuit_breaker | BOOLEAN | no |  |
| peak_nav | NUMERIC(20, 6) | no |  |
| policy_version | INTEGER | no |  |
| created_at | DATETIME | no |  |

Unique: (user_id, family, generation)

## `risk_policies`
| Column | Type | Null | Key |
|---|---|---|---|
| id | VARCHAR(32) | no | PK |
| user_id | VARCHAR(32) | no | FK→users.id |
| version | INTEGER | no |  |
| limits | JSON | no |  |
| effective_at | DATETIME | no |  |
| created_at | DATETIME | no |  |

Unique: (user_id, version)

## `sources`
| Column | Type | Null | Key |
|---|---|---|---|
| id | VARCHAR(32) | no | PK |
| user_id | VARCHAR(32) | no | FK→users.id |
| url | TEXT | no |  |
| title | TEXT | no |  |
| authors | TEXT | yes |  |
| publisher | TEXT | yes |  |
| published_at | DATE | yes |  |
| retrieved_at | DATETIME | no |  |
| source_type | VARCHAR(32) | no |  |
| provider | VARCHAR(32) | no |  |
| retrieval_id | VARCHAR(200) | no |  |
| url_verified | BOOLEAN | no |  |
| verification_note | TEXT | yes |  |
| abstract | TEXT | yes |  |
| cited_by_count | INTEGER | yes |  |
| synthetic | BOOLEAN | no |  |
| created_at | DATETIME | no |  |

Unique: (user_id, url)

## `strategies`
| Column | Type | Null | Key |
|---|---|---|---|
| id | VARCHAR(32) | no | PK |
| user_id | VARCHAR(32) | no | FK→users.id |
| family | VARCHAR(32) | no |  |
| name | VARCHAR(200) | no |  |
| fingerprint | VARCHAR(64) | no |  |
| thesis_fingerprint | VARCHAR(64) | no |  |
| created_at | DATETIME | no |  |

Unique: (user_id, fingerprint)

## `user_settings`
| Column | Type | Null | Key |
|---|---|---|---|
| user_id | VARCHAR(32) | no | PK |
| data | JSON | no |  |
| updated_at | DATETIME | no |  |
| created_at | DATETIME | no |  |

## `benchmark_accounts`
| Column | Type | Null | Key |
|---|---|---|---|
| id | VARCHAR(32) | no | PK |
| user_id | VARCHAR(32) | no | FK→users.id |
| portfolio_id | VARCHAR(32) | no | FK→portfolios.id |
| symbol | VARCHAR(16) | no |  |
| starting_cash | NUMERIC(20, 6) | no |  |
| inception_at | DATETIME | no |  |
| cash | NUMERIC(20, 6) | no |  |
| shares | NUMERIC(24, 10) | no |  |
| entry_session | DATE | yes |  |
| entry_price | NUMERIC(20, 6) | yes |  |
| basis | VARCHAR(24) | no |  |
| created_at | DATETIME | no |  |

Unique: (portfolio_id)

## `ledger_entries`
| Column | Type | Null | Key |
|---|---|---|---|
| id | INTEGER | no | PK |
| user_id | VARCHAR(32) | no | FK→users.id |
| portfolio_id | VARCHAR(32) | no | FK→portfolios.id |
| experiment_id | VARCHAR(32) | yes |  |
| instrument_id | VARCHAR(32) | yes |  |
| lot_id | VARCHAR(32) | yes |  |
| event_type | VARCHAR(24) | no |  |
| amount | NUMERIC(20, 6) | no |  |
| quantity_delta | NUMERIC(20, 6) | no |  |
| currency | VARCHAR(3) | no |  |
| reference | VARCHAR(64) | yes |  |
| memo | TEXT | yes |  |
| idempotency_key | VARCHAR(200) | no |  |
| occurred_at | DATETIME | no |  |
| session | DATE | yes |  |
| created_at | DATETIME | no |  |

Unique: (idempotency_key)

## `research_runs`
| Column | Type | Null | Key |
|---|---|---|---|
| id | VARCHAR(32) | no | PK |
| user_id | VARCHAR(32) | no | FK→users.id |
| job_id | VARCHAR(32) | yes | FK→jobs.id |
| state | VARCHAR(16) | no |  |
| mode | VARCHAR(8) | no |  |
| llm_provider | VARCHAR(32) | no |  |
| llm_model | VARCHAR(64) | yes |  |
| prompt_version | VARCHAR(32) | no |  |
| queries | JSON | no |  |
| coverage | JSON | no |  |
| briefing | JSON | no |  |
| stats | JSON | no |  |
| cost_usd | FLOAT | no |  |
| error | TEXT | yes |  |
| started_at | DATETIME | no |  |
| finished_at | DATETIME | yes |  |
| created_at | DATETIME | no |  |

## `strategy_versions`
| Column | Type | Null | Key |
|---|---|---|---|
| id | VARCHAR(32) | no | PK |
| user_id | VARCHAR(32) | no | FK→users.id |
| strategy_id | VARCHAR(32) | no | FK→strategies.id |
| version | INTEGER | no |  |
| rule_json | JSON | no |  |
| rule_hash | VARCHAR(64) | no |  |
| thesis | TEXT | no |  |
| hypothesis | JSON | no |  |
| evidence_ids | JSON | no |  |
| parent_version_id | VARCHAR(32) | yes |  |
| diff | JSON | yes |  |
| generated_by | VARCHAR(64) | no |  |
| created_at | DATETIME | no |  |

Unique: (strategy_id, rule_hash); (strategy_id, version)

## `valuation_snapshots`
| Column | Type | Null | Key |
|---|---|---|---|
| id | INTEGER | no | PK |
| user_id | VARCHAR(32) | no | FK→users.id |
| portfolio_id | VARCHAR(32) | no | FK→portfolios.id |
| session | DATE | no |  |
| cash | NUMERIC(20, 6) | no |  |
| reserved_cash | NUMERIC(20, 6) | no |  |
| holdings_value | NUMERIC(20, 6) | no |  |
| nav | NUMERIC(20, 6) | no |  |
| benchmark_nav | NUMERIC(20, 6) | yes |  |
| peak_nav | NUMERIC(20, 6) | no |  |
| drawdown | NUMERIC(12, 8) | no |  |
| quality | VARCHAR(24) | no |  |
| details | JSON | no |  |
| created_at | DATETIME | no |  |

Unique: (portfolio_id, session)

## `backtest_runs`
| Column | Type | Null | Key |
|---|---|---|---|
| id | VARCHAR(32) | no | PK |
| user_id | VARCHAR(32) | no | FK→users.id |
| strategy_version_id | VARCHAR(32) | no | FK→strategy_versions.id |
| rule_hash | VARCHAR(64) | no |  |
| status | VARCHAR(24) | no |  |
| reason | TEXT | yes |  |
| parameters | JSON | no |  |
| dataset_manifest | JSON | no |  |
| code_version | VARCHAR(64) | no |  |
| metrics | JSON | no |  |
| series | JSON | no |  |
| limitations | JSON | no |  |
| trigger | VARCHAR(16) | no |  |
| artifact_uri | TEXT | yes |  |
| created_at | DATETIME | no |  |

## `critiques`
| Column | Type | Null | Key |
|---|---|---|---|
| id | VARCHAR(32) | no | PK |
| user_id | VARCHAR(32) | no | FK→users.id |
| strategy_version_id | VARCHAR(32) | no | FK→strategy_versions.id |
| research_run_id | VARCHAR(32) | yes | FK→research_runs.id |
| items | JSON | no |  |
| summary | TEXT | no |  |
| max_severity | VARCHAR(16) | no |  |
| provider | VARCHAR(32) | no |  |
| created_at | DATETIME | no |  |

## `evidence`
| Column | Type | Null | Key |
|---|---|---|---|
| id | VARCHAR(32) | no | PK |
| user_id | VARCHAR(32) | no | FK→users.id |
| source_id | VARCHAR(32) | no | FK→sources.id |
| research_run_id | VARCHAR(32) | no | FK→research_runs.id |
| topic | VARCHAR(64) | no |  |
| stance | VARCHAR(16) | no |  |
| claim | TEXT | no |  |
| excerpt | TEXT | yes |  |
| limitations | TEXT | no |  |
| quality | FLOAT | no |  |
| quality_label | VARCHAR(16) | no |  |
| claim_supported | BOOLEAN | no |  |
| is_new | BOOLEAN | no |  |
| extraction_method | VARCHAR(32) | no |  |
| created_at | DATETIME | no |  |

## `proposals`
| Column | Type | Null | Key |
|---|---|---|---|
| id | VARCHAR(32) | no | PK |
| user_id | VARCHAR(32) | no | FK→users.id |
| strategy_version_id | VARCHAR(32) | no | FK→strategy_versions.id |
| portfolio_id | VARCHAR(32) | no | FK→portfolios.id |
| research_run_id | VARCHAR(32) | yes | FK→research_runs.id |
| backtest_run_id | VARCHAR(32) | yes | FK→backtest_runs.id |
| critique_id | VARCHAR(32) | yes | FK→critiques.id |
| kind | VARCHAR(16) | no |  |
| parent_experiment_id | VARCHAR(32) | yes |  |
| legacy_treatment | VARCHAR(32) | yes |  |
| score | FLOAT | yes |  |
| component_scores | JSON | no |  |
| gates | JSON | no |  |
| rationale | JSON | no |  |
| uncertainty | JSON | no |  |
| card | JSON | no |  |
| budget | NUMERIC(20, 6) | no |  |
| scope | JSON | no |  |
| scope_hash | VARCHAR(64) | no |  |
| exploratory | BOOLEAN | no |  |
| state | VARCHAR(16) | no |  |
| state_reason | TEXT | yes |  |
| expires_at | DATETIME | no |  |
| decided_at | DATETIME | yes |  |
| rank | INTEGER | yes |  |
| created_at | DATETIME | no |  |

## `approvals`
| Column | Type | Null | Key |
|---|---|---|---|
| id | VARCHAR(32) | no | PK |
| user_id | VARCHAR(32) | no | FK→users.id |
| proposal_id | VARCHAR(32) | no | FK→proposals.id |
| decision | VARCHAR(8) | no |  |
| actor_id | VARCHAR(32) | no |  |
| decided_at | DATETIME | no |  |
| scope_hash | VARCHAR(64) | no |  |
| idempotency_key | VARCHAR(100) | no |  |
| created_at | DATETIME | no |  |

Unique: (idempotency_key); (proposal_id)

## `experiments`
| Column | Type | Null | Key |
|---|---|---|---|
| id | VARCHAR(32) | no | PK |
| user_id | VARCHAR(32) | no | FK→users.id |
| portfolio_id | VARCHAR(32) | no | FK→portfolios.id |
| proposal_id | VARCHAR(32) | no | FK→proposals.id |
| approved_version_id | VARCHAR(32) | no | FK→strategy_versions.id |
| name | VARCHAR(200) | no |  |
| budget | NUMERIC(20, 6) | no |  |
| status | VARCHAR(16) | no |  |
| entries_allowed | BOOLEAN | no |  |
| thesis_status | VARCHAR(16) | no |  |
| approved_at | DATETIME | no |  |
| inception_at | DATETIME | no |  |
| ends_by | DATE | yes |  |
| ended_at | DATETIME | yes |  |
| successor_of | VARCHAR(32) | yes |  |
| status_reason | TEXT | yes |  |
| created_at | DATETIME | no |  |

Unique: (proposal_id)

## `qa_messages`
| Column | Type | Null | Key |
|---|---|---|---|
| id | VARCHAR(32) | no | PK |
| user_id | VARCHAR(32) | no | FK→users.id |
| proposal_id | VARCHAR(32) | no | FK→proposals.id |
| question | TEXT | no |  |
| answer | TEXT | no |  |
| citations | JSON | no |  |
| provider | VARCHAR(32) | no |  |
| created_at | DATETIME | no |  |

## `lessons`
| Column | Type | Null | Key |
|---|---|---|---|
| id | VARCHAR(32) | no | PK |
| user_id | VARCHAR(32) | no | FK→users.id |
| experiment_id | VARCHAR(32) | no | FK→experiments.id |
| period | VARCHAR(16) | no |  |
| outcome | VARCHAR(24) | no |  |
| recommendation | VARCHAR(16) | no |  |
| lesson | TEXT | no |  |
| competing_explanation | TEXT | no |  |
| confidence | VARCHAR(16) | no |  |
| metrics | JSON | no |  |
| evidence_links | JSON | no |  |
| proposal_id | VARCHAR(32) | yes |  |
| provider | VARCHAR(32) | no |  |
| created_at | DATETIME | no |  |

Unique: (experiment_id, period)

## `orders`
| Column | Type | Null | Key |
|---|---|---|---|
| id | VARCHAR(32) | no | PK |
| user_id | VARCHAR(32) | no | FK→users.id |
| portfolio_id | VARCHAR(32) | no | FK→portfolios.id |
| experiment_id | VARCHAR(32) | no | FK→experiments.id |
| instrument_id | VARCHAR(32) | no | FK→instruments.id |
| lot_id | VARCHAR(32) | yes |  |
| side | VARCHAR(4) | no |  |
| quantity | NUMERIC(20, 6) | no |  |
| status | VARCHAR(16) | no |  |
| reason | TEXT | no |  |
| status_reason | TEXT | yes |  |
| signal_session | DATE | no |  |
| eligible_session | DATE | no |  |
| expires_after_session | DATE | no |  |
| reserved_cash | NUMERIC(20, 6) | no |  |
| reference_price | NUMERIC(20, 6) | yes |  |
| slippage_bps | NUMERIC(10, 4) | no |  |
| fill_price | NUMERIC(20, 6) | yes |  |
| fill_session | DATE | yes |  |
| fill_at | DATETIME | yes |  |
| bar_observed_at | DATETIME | yes |  |
| processed_at | DATETIME | yes |  |
| recorded_late | BOOLEAN | no |  |
| fees | NUMERIC(20, 6) | no |  |
| slippage_amount | NUMERIC(20, 6) | no |  |
| gross_amount | NUMERIC(20, 6) | yes |  |
| idempotency_key | VARCHAR(200) | no |  |
| requested_at | DATETIME | no |  |
| created_at | DATETIME | no |  |

Unique: (idempotency_key)

## `position_lots`
| Column | Type | Null | Key |
|---|---|---|---|
| id | VARCHAR(32) | no | PK |
| user_id | VARCHAR(32) | no | FK→users.id |
| portfolio_id | VARCHAR(32) | no | FK→portfolios.id |
| experiment_id | VARCHAR(32) | no | FK→experiments.id |
| strategy_version_id | VARCHAR(32) | no |  |
| instrument_id | VARCHAR(32) | no | FK→instruments.id |
| open_order_id | VARCHAR(32) | no |  |
| quantity | NUMERIC(20, 6) | no |  |
| original_quantity | NUMERIC(20, 6) | no |  |
| cost_basis | NUMERIC(20, 6) | no |  |
| entry_price | NUMERIC(20, 6) | no |  |
| opened_at | DATETIME | no |  |
| opened_session | DATE | no |  |
| peak_close | NUMERIC(20, 6) | yes |  |
| status | VARCHAR(16) | no |  |
| realized_pnl | NUMERIC(20, 6) | no |  |
| dividends | NUMERIC(20, 6) | no |  |
| closed_at | DATETIME | yes |  |
| closed_session | DATE | yes |  |
| exit_price | NUMERIC(20, 6) | yes |  |
| exit_reason | TEXT | yes |  |
| created_at | DATETIME | no |  |

Unique: (open_order_id)

## `signals`
| Column | Type | Null | Key |
|---|---|---|---|
| id | VARCHAR(32) | no | PK |
| experiment_id | VARCHAR(32) | no | FK→experiments.id |
| instrument_id | VARCHAR(32) | no | FK→instruments.id |
| session | DATE | no |  |
| kind | VARCHAR(8) | no |  |
| observed_at | DATETIME | no |  |
| rules_evaluated | JSON | no |  |
| result | BOOLEAN | no |  |
| idempotency_key | VARCHAR(200) | no |  |
| created_at | DATETIME | no |  |

Unique: (idempotency_key)

## `thesis_reviews`
| Column | Type | Null | Key |
|---|---|---|---|
| id | VARCHAR(32) | no | PK |
| user_id | VARCHAR(32) | no | FK→users.id |
| experiment_id | VARCHAR(32) | no | FK→experiments.id |
| session | DATE | no |  |
| kind | VARCHAR(16) | no |  |
| status | VARCHAR(16) | no |  |
| summary | TEXT | no |  |
| observations | JSON | no |  |
| evidence_links | JSON | no |  |
| confidence | VARCHAR(16) | no |  |
| counterargument | TEXT | yes |  |
| provider | VARCHAR(32) | no |  |
| created_at | DATETIME | no |  |

Unique: (experiment_id, session, kind)
