"""Credentialed smoke test of external providers. Reports exactly what was observed — never claims
success for a provider that was not actually contacted."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from . import market_calendar as cal
from .config import get_settings
from .providers.llm import AnthropicLLM
from .providers.market import AlphaVantageProvider, StooqProvider, TiingoProvider
from .providers.research import CrossrefProvider, OpenAlexProvider, RedditProvider, SecEdgarProvider, XProvider


def _try(fn):
    t0 = datetime.now(timezone.utc)
    try:
        out = fn()
        return {"ok": True, "observed_at": t0.isoformat(), **out}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "observed_at": t0.isoformat(), "error": f"{type(e).__name__}: {str(e)[:300]}"}


def run_smoke(symbol: str = "IBM") -> dict:
    cfg = get_settings()
    end = cal.latest_finalized_session(datetime.now(timezone.utc))
    start = end - timedelta(days=14)
    res: dict = {"expected_latest_session": end.isoformat()}
    for name, p in [("tiingo", TiingoProvider()), ("alphavantage", AlphaVantageProvider()), ("stooq", StooqProvider())]:
        ok, why = p.configured()
        if not ok:
            res[name] = {"ok": False, "skipped": why}
            continue

        def f(p=p):
            bars = p.daily_bars(symbol, start, end)
            return {"bars": len(bars), "last_session": bars[-1].session.isoformat() if bars else None,
                    "last_close": bars[-1].close if bars else None, "adjustment_basis": p.adjustment_basis}
        res[name] = _try(f)
    for name, p in [("openalex", OpenAlexProvider()), ("crossref", CrossrefProvider()), ("sec_edgar", SecEdgarProvider()),
                    ("reddit", RedditProvider()), ("x", XProvider())]:
        ok, why = p.available()
        if not ok:
            res[name] = {"ok": False, "skipped": why}
            continue

        def g(p=p):
            docs = p.search("post-earnings announcement drift", since=date(2015, 1, 1), limit=3)
            return {"results": len(docs), "sample": [{"title": d.title[:120], "url": d.url, "id": d.retrieval_id} for d in docs[:2]]}
        res[name] = _try(g)
    if cfg.anthropic_api_key:
        llm = AnthropicLLM()
        res["anthropic"] = _try(lambda: (lambda r: {"status": r.status, "model": r.model, "tokens": [r.input_tokens, r.output_tokens]})(
            llm.structured("smoke", "Return JSON.", "Return {\"ok\": true}.", {"type": "object", "additionalProperties": False,
                                                                              "required": ["ok"], "properties": {"ok": {"type": "boolean"}}},
                           max_tokens=256)))
    else:
        res["anthropic"] = {"ok": False, "skipped": "ANTHROPIC_API_KEY not set"}
    return res
