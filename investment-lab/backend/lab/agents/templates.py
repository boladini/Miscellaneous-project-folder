"""Research topics and deterministic strategy templates.

Templates encode well-documented, falsifiable hypotheses as whitelisted rules. They are only
instantiated when the research run retrieves supporting evidence for the topic; disconfirming
queries are always run as well. Thresholds are fixed ex-ante (not fitted to backtests).
"""
from __future__ import annotations

TOPICS = {
    "reversal": {
        "family": "contrarian",
        "supporting": ["short-term return reversal stocks", "investor overreaction stock returns reversal"],
        "disconfirming": ["short-term reversal transaction costs profitability", "reversal strategy fails liquidity"],
        "keywords": ["revers", "overreact", "contrarian", "loser"],
    },
    "momentum": {
        "family": "growth",
        "supporting": ["time-series momentum stocks trend following", "price momentum returns persistence"],
        "disconfirming": ["momentum crash", "momentum profits decline after costs"],
        "keywords": ["momentum", "trend", "persist"],
    },
    "pead": {
        "family": "event_driven",
        "supporting": ["post-earnings announcement drift", "earnings surprise stock price drift"],
        "disconfirming": ["post-earnings announcement drift decline disappearance", "earnings drift arbitrage costs"],
        "keywords": ["earnings", "drift", "surprise", "announcement"],
    },
    "asymmetric": {
        "family": "asymmetric",
        "supporting": ["52-week high stock returns", "skewness stock returns lottery"],
        "disconfirming": ["lottery stocks underperform skewness", "distressed stocks returns anomaly"],
        "keywords": ["52-week", "skew", "high", "distress", "lottery"],
    },
    "lowvol": {
        "family": "ai_generated",
        "supporting": ["low volatility anomaly stock returns", "low-risk anomaly betting against beta"],
        "disconfirming": ["low volatility anomaly crowding valuation", "low volatility anomaly explanation"],
        "keywords": ["volatil", "low-risk", "beta", "risk"],
    },
}


def _c(ind, op, value, **params):
    d = {"indicator": ind, "op": op, "value": value, "params": params}
    return d


TEMPLATES = {
    "reversal": {
        "schema_version": 1, "name": "Liquid-stock short-term reversal", "family": "contrarian", "horizon": "swing",
        "universe": {"asset_types": ["stock"], "market": "US", "min_price": 5, "min_avg_dollar_volume_20d": 5_000_000},
        "required_data": ["daily_ohlcv", "instrument_metadata"],
        "entry": {"all": [_c("return_nd", "lte", -0.08, n=5), _c("sma_ratio", "gte", -0.20, n=200), _c("rsi", "lte", 35, n=14)]},
        "ranking": {"indicator": "return_nd", "params": {"n": 5}, "direction": "ascending"},
        "allocation": {"max_experiment_fraction": 0.20, "max_position_fraction": 0.05, "max_positions": 4},
        "exit": {"any": [_c("return_since_entry", "gte", 0.08), _c("return_since_entry", "lte", -0.10),
                         _c("sessions_held", "gte", 20)]},
        "holding": {"min_sessions": 1, "max_sessions": 20},
        "execution": {"slippage_bps": 10, "order_expiry_sessions": 3},
        "costs": {"stress_slippage_bps": 30},
        "thesis_invalidation": [{"metric": "avg_trade_return", "op": "lt", "value": -0.01, "min_closed_trades": 15,
                                 "description": "Average closed-trade return below -1% after 15 trades"}],
        "hypothesis": "Liquid stocks that fall sharply over 5 sessions (and are not in long-term collapse) partially "
                      "rebound within 20 sessions, net of 10 bps per-side costs.",
        "prediction": {"metric": "avg_trade_return", "direction": "positive", "threshold": 0.0, "horizon_sessions": 20},
    },
    "momentum": {
        "schema_version": 1, "name": "Trend persistence with breadth filter", "family": "growth", "horizon": "long_term",
        "universe": {"asset_types": ["stock"], "market": "US", "min_price": 5, "min_avg_dollar_volume_20d": 5_000_000},
        "required_data": ["daily_ohlcv", "instrument_metadata"],
        "entry": {"all": [_c("return_nd", "gte", 0.15, n=126), _c("sma_cross", "gte", 0.0, fast=50, slow=200),
                          _c("dist_from_high", "gte", -0.10, n=252)]},
        "ranking": {"indicator": "return_nd", "params": {"n": 126}, "direction": "descending"},
        "allocation": {"max_experiment_fraction": 0.25, "max_position_fraction": 0.06, "max_positions": 4},
        "exit": {"any": [_c("sma_ratio", "lte", -0.05, n=50), _c("drawdown_from_peak", "lte", -0.18),
                         _c("sessions_held", "gte", 189)]},
        "holding": {"min_sessions": 5, "max_sessions": 189},
        "execution": {"slippage_bps": 10, "order_expiry_sessions": 3},
        "costs": {"stress_slippage_bps": 30},
        "thesis_invalidation": [{"metric": "excess_vs_benchmark", "op": "lt", "value": -0.10, "min_closed_trades": 8,
                                 "description": "Trails the benchmark by more than 10 points after 8 trades"}],
        "hypothesis": "Stocks with strong 6-month returns trading near their 1-year high continue to outperform over the "
                      "following months, until the trend breaks.",
        "prediction": {"metric": "excess_vs_benchmark", "direction": "positive", "threshold": 0.0, "horizon_sessions": 189},
    },
    "pead": {
        "schema_version": 1, "name": "Post-earnings drift after positive surprises", "family": "event_driven", "horizon": "swing",
        "universe": {"asset_types": ["stock"], "market": "US", "min_price": 5, "min_avg_dollar_volume_20d": 5_000_000},
        "required_data": ["daily_ohlcv", "instrument_metadata", "earnings_events"],
        "entry": {"all": [_c("earnings_surprise_pct", "gte", 0.05, within_sessions=3), _c("return_nd", "gte", 0.0, n=3)]},
        "ranking": {"indicator": "earnings_surprise_pct", "params": {"within_sessions": 3}, "direction": "descending"},
        "allocation": {"max_experiment_fraction": 0.20, "max_position_fraction": 0.05, "max_positions": 4},
        "exit": {"any": [_c("return_since_entry", "lte", -0.08), _c("sessions_held", "gte", 40)]},
        "holding": {"min_sessions": 1, "max_sessions": 40},
        "execution": {"slippage_bps": 10, "order_expiry_sessions": 3},
        "costs": {"stress_slippage_bps": 30},
        "catalyst": {"type": "earnings", "binary": False, "description": "Entry only AFTER a public earnings release."},
        "thesis_invalidation": [{"metric": "avg_trade_return", "op": "lt", "value": -0.01, "min_closed_trades": 12,
                                 "description": "Average trade below -1% after 12 trades"}],
        "hypothesis": "After a public positive earnings surprise, prices continue drifting up over the next 40 sessions.",
        "prediction": {"metric": "avg_trade_return", "direction": "positive", "threshold": 0.0, "horizon_sessions": 40},
    },
    "asymmetric": {
        "schema_version": 1, "name": "Deep-drawdown recovery (bounded asymmetric bet)", "family": "asymmetric",
        "horizon": "long_term",
        "universe": {"asset_types": ["stock"], "market": "US", "min_price": 5, "min_avg_dollar_volume_20d": 5_000_000},
        "required_data": ["daily_ohlcv", "instrument_metadata"],
        "entry": {"all": [_c("dist_from_high", "lte", -0.35, n=252), _c("sma_ratio", "gte", 0.0, n=50),
                          _c("return_nd", "gte", 0.05, n=20)]},
        "ranking": {"indicator": "dist_from_high", "params": {"n": 252}, "direction": "ascending"},
        "allocation": {"max_experiment_fraction": 0.10, "max_position_fraction": 0.025, "max_positions": 4},
        "exit": {"any": [_c("return_since_entry", "gte", 0.40), _c("return_since_entry", "lte", -0.15),
                         _c("sessions_held", "gte", 252)]},
        "holding": {"min_sessions": 5, "max_sessions": 252},
        "execution": {"slippage_bps": 15, "order_expiry_sessions": 3},
        "costs": {"stress_slippage_bps": 45},
        "thesis_invalidation": [{"metric": "experiment_return", "op": "lt", "value": -0.25, "min_closed_trades": 4,
                                 "description": "Loses more than 25% of budget after 4 trades"}],
        "hypothesis": "Stocks far below their 1-year high that regain their 50-day average offer right-skewed payoffs: "
                      "many small losses, occasional large recoveries. A -15% stop is NOT a guaranteed loss limit (gaps).",
        "prediction": {"metric": "avg_trade_return", "direction": "positive", "threshold": 0.0, "horizon_sessions": 252},
    },
    "lowvol": {
        "schema_version": 1, "name": "Low-volatility trend blend", "family": "ai_generated", "horizon": "long_term",
        "universe": {"asset_types": ["stock", "etf"], "market": "US", "min_price": 5, "min_avg_dollar_volume_20d": 5_000_000},
        "required_data": ["daily_ohlcv", "instrument_metadata"],
        "entry": {"all": [_c("volatility", "lte", 0.28, n=60), _c("sma_ratio", "gte", 0.02, n=100),
                          {"indicator": "rsi", "op": "between", "value": 45, "value2": 70, "params": {"n": 14}}]},
        "ranking": {"indicator": "volatility", "params": {"n": 60}, "direction": "ascending"},
        "allocation": {"max_experiment_fraction": 0.25, "max_position_fraction": 0.08, "max_positions": 3},
        "exit": {"any": [_c("sma_ratio", "lte", -0.03, n=100), _c("drawdown_from_peak", "lte", -0.12),
                         _c("sessions_held", "gte", 200)]},
        "holding": {"min_sessions": 5, "max_sessions": 200},
        "execution": {"slippage_bps": 10, "order_expiry_sessions": 3},
        "costs": {"stress_slippage_bps": 30},
        "thesis_invalidation": [{"metric": "excess_vs_benchmark", "op": "lt", "value": -0.08, "min_closed_trades": 6,
                                 "description": "Trails benchmark by >8 points after 6 trades"}],
        "hypothesis": "Combining low trailing volatility with a positive medium-term trend yields benchmark-like returns "
                      "with shallower drawdowns.",
        "prediction": {"metric": "excess_vs_benchmark", "direction": "above", "threshold": -0.02, "horizon_sessions": 200},
    },
}

CARD_TEXT = {
    "reversal": {"why_now": "Signals appear whenever liquid names drop sharply; the strategy only acts on fresh, finalized data.",
                 "downside": "Falling knives: losses can exceed the -10% exit because exits fill at the next open after a close trigger.",
                 "cadence": "A few trades per month when markets are volatile; none in calm markets."},
    "momentum": {"why_now": "Uses current 6-month trends; evaluated daily after the close.",
                 "downside": "Momentum crashes: sharp reversals in rebounds can hit several positions at once.",
                 "cadence": "Low turnover: positions held for months; a handful of trades per quarter."},
    "pead": {"why_now": "Acts within 3 sessions of each public earnings release in the universe.",
             "downside": "Drift may be absent or reversed; event gaps can skip past the -8% exit.",
             "cadence": "Clusters around earnings seasons (4 per year)."},
    "asymmetric": {"why_now": "Acts only when deeply discounted names show a stabilising trend.",
                   "downside": "Most bets may lose up to the stop (or more on gaps); the thesis depends on rare large winners.",
                   "cadence": "Infrequent; small positions held up to a year."},
    "lowvol": {"why_now": "Selects the calmest trending instruments at each evaluation.",
               "downside": "Can lag sharply in strong rallies led by high-volatility stocks.",
               "cadence": "Low turnover; a few trades per quarter."},
}
