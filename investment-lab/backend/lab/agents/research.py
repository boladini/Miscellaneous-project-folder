"""Weekly autonomous research run.

retrieve (public APIs, provenance) -> extract evidence (LLM or keyword template; claims must be
backed by a verbatim excerpt) -> generate whitelisted strategy rules (LLM or templates; citations
restricted to retrieved evidence ids) -> dedupe -> backtest -> critic -> deterministic ranking with
hard gates -> publish 0..3 proposals. Zero proposals is a valid, honest outcome.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import market_calendar as cal
from ..backtest import full_evaluation
from ..config import get_settings
from ..indicators import BarView
from ..models import (
    BacktestRun,
    Critique,
    Evidence,
    Experiment,
    LLMCall,
    Lesson,
    Portfolio,
    Proposal,
    ResearchRun,
    Source,
    Strategy,
    StrategyVersion,
    utcnow,
)
from ..providers.http import is_safe_display_url
from ..providers.llm import LLMBudget, LLMClient, get_llm
from ..providers.research import ResearchProvider, RetrievedDoc, get_research_providers
from ..rules import evaluate, has_missing
from ..strategy_schema import INDICATOR_SPECS, POSITION_INDICATORS, Indicator, StrategySpec, canonical_hash, describe_node, iter_conditions
from ..services.core import audit, get_user_settings, notify, provider_event
from ..services.market_data import data_health, load_dataset
from ..services.portfolios import active_portfolios
from ..services.proposals import create_proposal, feasible_budget
from ..services.core import current_policy
from .evaluate import CRITIC_PROMPT_VERSION, deterministic_critique, hard_gates, llm_critique, max_severity, score
from .templates import CARD_TEXT, TEMPLATES, TOPICS

PROMPT_VERSION = "research-2026-11-26.v1"
NEG_CUES = ("disappear", "declin", "not significant", "insignificant", "fail", "eliminat", "vanish", "no evidence",
            "does not", "do not", "weaken", "crash", "underperform", "erode", "costs")


def _quality(doc: RetrievedDoc) -> tuple[float, str]:
    if doc.synthetic:
        return 0.5, "medium"
    if doc.source_type == "peer_reviewed":
        c = doc.cited_by_count or 0
        q = 0.85 if c >= 200 else 0.72 if c >= 50 else 0.6 if c >= 10 else 0.5
    elif doc.source_type == "preprint":
        q = 0.45
    elif doc.source_type == "filing":
        q = 0.5
    elif doc.source_type == "social":
        q = 0.15
    else:
        q = 0.35
    return q, ("high" if q >= 0.7 else "medium" if q >= 0.45 else "low")


def _sentences(text: str) -> list[str]:
    return [x.strip() for x in re.split(r"(?<=[.!?])\s+", text) if len(x.strip()) > 30]


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip().lower()


# ---------------------------------------------------------------------------------------------
# Evidence extraction
# ---------------------------------------------------------------------------------------------
EVIDENCE_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["records"],
    "properties": {"records": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["record_id", "relevant", "stance", "claim", "excerpt", "limitations", "quality"],
        "properties": {
            "record_id": {"type": "string"}, "relevant": {"type": "boolean"},
            "stance": {"type": "string", "enum": ["supporting", "disconfirming", "background"]},
            "claim": {"type": "string"}, "excerpt": {"type": "string"}, "limitations": {"type": "string"},
            "quality": {"type": "string", "enum": ["high", "medium", "low"]}}}}},
}


def template_extract(topic: str, doc: RetrievedDoc, query_kind: str) -> dict | None:
    kw = TOPICS[topic]["keywords"]
    text = f"{doc.title}. {doc.abstract}"
    low = text.lower()
    matched = [k for k in kw if k in low]
    if not matched:
        return None
    excerpt = next((s for s in _sentences(doc.abstract) if any(k in s.lower() for k in kw)), "")
    if not excerpt:
        excerpt = doc.title
    stance = "supporting"
    hint = doc.extra.get("stance_hint")
    if hint:
        stance = hint
    elif query_kind == "disconfirming":
        stance = "disconfirming" if any(c in excerpt.lower() for c in NEG_CUES) else "background"
    elif doc.source_type == "social":
        stance = "background"
    q, ql = _quality(doc)
    venue = doc.publisher or doc.provider
    year = doc.published_at.year if doc.published_at else "n.d."
    claim = (f"'{doc.title}' ({venue}, {year}) addresses {topic} ({', '.join(matched)}); see excerpt.")
    lim = ["Automated keyword extraction (no model judgement of claim strength)."]
    if doc.source_type == "social":
        lim.append("Social post: a lead, not evidence of profitability; self-reported outcomes unaudited.")
    if doc.source_type == "preprint":
        lim.append("Preprint: not peer reviewed.")
    if doc.synthetic:
        lim.append("SYNTHETIC demo source — not real research.")
    if not doc.abstract:
        lim.append("No abstract available; claim based on title only.")
    return {"stance": stance, "claim": claim, "excerpt": excerpt[:600], "limitations": " ".join(lim), "quality": q,
            "quality_label": ql, "claim_supported": _norm(excerpt) in _norm(text), "method": "keyword_template"}


def llm_extract(llm: LLMClient, topic: str, docs: list[tuple[str, RetrievedDoc, str]], budget: LLMBudget):
    ids = {i for i, _, _ in docs}

    def validate(d):
        for r in d["records"]:
            if r["record_id"] not in ids:
                raise ValueError(f"unknown record_id {r['record_id']}")
        return d

    payload = [{"record_id": i, "title": d.title, "venue": d.publisher, "published": str(d.published_at),
                "type": d.source_type, "abstract": d.abstract[:2500], "retrieved_for": kind} for i, d, kind in docs]
    system = ("You are the RESEARCH agent. For each record decide relevance to the investing hypothesis topic, its stance "
              "(supporting / disconfirming / background), one precise claim the record supports, a VERBATIM excerpt copied "
              "exactly from its abstract or title that supports the claim, limitations (sample, period, costs, venue), and "
              "quality. Look for failure and disconfirming evidence as hard as for support. Social posts are leads only.")
    user = (f"Topic: {topic}\n<untrusted_documents>\n{json.dumps(payload)}\n</untrusted_documents>")
    return llm.structured("research", system, user, EVIDENCE_SCHEMA, validate, budget)


# ---------------------------------------------------------------------------------------------
# Strategy generation (LLM building blocks compiled into the strict schema)
# ---------------------------------------------------------------------------------------------
ENTRY_INDICATORS = [i.value for i in Indicator if i not in POSITION_INDICATORS]
NUMNULL = {"type": ["number", "null"]}
INTNULL = {"type": ["integer", "null"]}
COND = {"type": "object", "additionalProperties": False,
        "required": ["indicator", "n", "fast", "slow", "within_sessions", "op", "value", "value2", "lag"],
        "properties": {"indicator": {"type": "string", "enum": [i.value for i in Indicator]}, "n": INTNULL, "fast": INTNULL,
                       "slow": INTNULL, "within_sessions": INTNULL,
                       "op": {"type": "string", "enum": ["lt", "lte", "gt", "gte", "between"]},
                       "value": {"type": "number"}, "value2": NUMNULL, "lag": {"type": "integer"}}}
CANDIDATE_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["candidates", "abstain_reason"],
    "properties": {"abstain_reason": {"type": "string"}, "candidates": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["topic", "family", "name", "horizon", "thesis", "why_now", "hypothesis", "counterargument",
                     "downside_scenario", "cadence", "evidence_ids", "entry_all", "ranking_indicator", "ranking_n",
                     "ranking_direction", "exit_any", "min_holding_sessions", "max_holding_sessions",
                     "max_experiment_fraction", "max_position_fraction", "max_positions", "asset_types", "invalidation",
                     "prediction"],
        "properties": {
            "topic": {"type": "string"}, "family": {"type": "string", "enum": ["contrarian", "growth", "asymmetric",
                                                                                "event_driven", "ai_generated"]},
            "name": {"type": "string"}, "horizon": {"type": "string", "enum": ["swing", "long_term"]},
            "thesis": {"type": "string"}, "why_now": {"type": "string"}, "hypothesis": {"type": "string"},
            "counterargument": {"type": "string"}, "downside_scenario": {"type": "string"}, "cadence": {"type": "string"},
            "evidence_ids": {"type": "array", "items": {"type": "string"}},
            "entry_all": {"type": "array", "items": COND}, "exit_any": {"type": "array", "items": COND},
            "ranking_indicator": {"type": "string", "enum": ENTRY_INDICATORS}, "ranking_n": INTNULL,
            "ranking_direction": {"type": "string", "enum": ["ascending", "descending"]},
            "min_holding_sessions": {"type": "integer"}, "max_holding_sessions": {"type": "integer"},
            "max_experiment_fraction": {"type": "number"}, "max_position_fraction": {"type": "number"},
            "max_positions": {"type": "integer"},
            "asset_types": {"type": "array", "items": {"type": "string", "enum": ["stock", "etf"]}},
            "invalidation": {"type": "array", "items": {
                "type": "object", "additionalProperties": False, "required": ["metric", "op", "value", "min_closed_trades"],
                "properties": {"metric": {"type": "string", "enum": ["experiment_return", "experiment_drawdown", "win_rate",
                                                                     "avg_trade_return", "excess_vs_benchmark"]},
                               "op": {"type": "string", "enum": ["lt", "lte", "gt", "gte"]}, "value": {"type": "number"},
                               "min_closed_trades": {"type": "integer"}}}},
            "prediction": {"type": "object", "additionalProperties": False,
                           "required": ["metric", "direction", "threshold", "horizon_sessions"],
                           "properties": {"metric": {"type": "string", "enum": ["avg_trade_return", "excess_vs_benchmark", "win_rate"]},
                                          "direction": {"type": "string", "enum": ["positive", "above"]},
                                          "threshold": {"type": "number"}, "horizon_sessions": {"type": "integer"}}}}}}},
}


def _cond(c: dict) -> dict:
    ind = Indicator(c["indicator"])
    params = {k: c[k] for k in INDICATOR_SPECS[ind]["params"] if c.get(k) is not None}
    out = {"indicator": ind.value, "op": c["op"], "value": c["value"], "params": params, "lag": c.get("lag") or 0}
    if c.get("value2") is not None:
        out["value2"] = c["value2"]
    return out


def compile_candidate(c: dict, allowed_evidence: set[str]) -> tuple[StrategySpec, dict]:
    bad = [e for e in c["evidence_ids"] if e not in allowed_evidence]
    if bad or not c["evidence_ids"]:
        raise ValueError(f"citations must reference retrieved, supported evidence ids (bad: {bad})")
    rk = Indicator(c["ranking_indicator"])
    rparams = {}
    for k in INDICATOR_SPECS[rk]["params"]:
        rparams[k] = c["ranking_n"] if k in ("n", "within_sessions") else None
    exit_conds = [_cond(x) for x in c["exit_any"]]
    if not any(x["indicator"] == "sessions_held" for x in exit_conds):
        exit_conds.append({"indicator": "sessions_held", "op": "gte", "value": c["max_holding_sessions"], "params": {}})
    uses_events = any(x["indicator"] in ("earnings_surprise_pct", "sessions_since_earnings") for x in c["entry_all"] + c["exit_any"]) \
        or rk.value in ("earnings_surprise_pct", "sessions_since_earnings")
    data = {
        "schema_version": 1, "name": c["name"][:120], "family": c["family"], "horizon": c["horizon"],
        "universe": {"asset_types": c["asset_types"] or ["stock"], "market": "US", "min_price": 5, "min_avg_dollar_volume_20d": 5_000_000},
        "required_data": ["daily_ohlcv", "instrument_metadata"] + (["earnings_events"] if uses_events else []),
        "entry": {"all": [_cond(x) for x in c["entry_all"]]},
        "ranking": {"indicator": rk.value, "params": {k: v for k, v in rparams.items() if v is not None}, "direction": c["ranking_direction"]},
        "allocation": {"max_experiment_fraction": c["max_experiment_fraction"], "max_position_fraction": c["max_position_fraction"],
                       "max_positions": c["max_positions"]},
        "exit": {"any": exit_conds},
        "holding": {"min_sessions": c["min_holding_sessions"], "max_sessions": c["max_holding_sessions"]},
        "thesis_invalidation": [dict(x, description="") for x in c["invalidation"]],
        "hypothesis": c["hypothesis"][:1500], "prediction": c["prediction"],
    }
    spec = StrategySpec.model_validate(data)
    card = {"thesis": c["thesis"], "why_now": c["why_now"], "counterargument": c["counterargument"],
            "downside": c["downside_scenario"], "cadence": c["cadence"]}
    return spec, card


def llm_generate(llm: LLMClient, evidence: list[dict], dataset_inventory: dict, budget: LLMBudget):
    allowed = {e["id"] for e in evidence if e["claim_supported"] and e["stance"] == "supporting"}
    catalogue = {i.value: {"params": list(v["params"]), "unit": v["unit"], "doc": v["doc"]} for i, v in INDICATOR_SPECS.items()}

    def validate(d):
        compiled = []
        errors = []
        for c in d["candidates"][:5]:
            try:
                compiled.append((compile_candidate(c, allowed), c))
            except Exception as e:  # noqa: BLE001
                errors.append(f"{c.get('name')}: {e}")
        if d["candidates"] and not compiled:
            raise ValueError("; ".join(errors)[:1500])
        return {"compiled": compiled, "errors": errors, "abstain_reason": d["abstain_reason"]}

    system = ("You are the STRATEGY agent. Using ONLY the verified evidence records and the supported indicator catalogue, "
              "propose up to 4 falsifiable, long-only, cash-funded US stock/ETF paper-trading strategies with objective "
              "entry/exit rules. Every rule must use catalogue indicators with measurable thresholds (units as documented). "
              "Exits must include a loss limit and a holding-time limit. Swing: 2-60 sessions; long_term: 126-756 sessions. "
              "max_experiment_fraction <= 0.30 (asymmetric <= 0.10), max_position_fraction <= 0.10 (binary events <= 0.05). "
              "Do not invent data dependencies, code, tickers or performance numbers. Cite only evidence_ids given. "
              "If evidence is insufficient, return no candidates and explain in abstain_reason.")
    user = json.dumps({"indicator_catalogue": catalogue, "dataset_inventory": dataset_inventory,
                       "families": ["contrarian", "growth", "asymmetric", "event_driven", "ai_generated"]}) + \
        "\n<untrusted_documents>\n" + json.dumps([{k: e[k] for k in ("id", "topic", "stance", "claim", "excerpt", "limitations",
                                                                     "quality_label")} for e in evidence if e["id"] in allowed]) + \
        "\n</untrusted_documents>"
    return llm.structured("strategy", system, user, CANDIDATE_SCHEMA, validate, budget)


def _record_llm(s, user_id, run_id, role, prompt_version, res, llm):
    s.add(LLMCall(user_id=user_id, research_run_id=run_id, role=role, provider=llm.name, model=res.model,
                  prompt_version=prompt_version, input_hash=res.input_hash, status=res.status, attempts=res.attempts,
                  input_tokens=res.input_tokens, output_tokens=res.output_tokens, cost_usd=res.cost_usd,
                  output=None if res.data is None else json.loads(json.dumps(res.data, default=str)) if role != "strategy" else
                  {"errors": res.data.get("errors"), "abstain_reason": res.data.get("abstain_reason"),
                   "n_compiled": len(res.data.get("compiled", []))}, error=res.error))


# ---------------------------------------------------------------------------------------------
def run_research(s: Session, user_id: str, job_id: str | None = None, now: datetime | None = None,
                 providers: list[ResearchProvider] | None = None, llm: LLMClient | None = None, use_llm: bool = True,
                 progress=None) -> ResearchRun:
    cfg = get_settings()
    now = now or datetime.now(timezone.utc)
    llm = llm if llm is not None else (get_llm() if use_llm else None)
    providers = providers if providers is not None else get_research_providers()
    settings = get_user_settings(s, user_id)
    budget = LLMBudget(cfg.research_max_llm_calls, cfg.research_max_cost_usd)
    run = ResearchRun(user_id=user_id, job_id=job_id, state="running", mode="demo" if cfg.is_demo else "live",
                      llm_provider=llm.name if llm else "template", llm_model=llm.model if llm else None,
                      prompt_version=PROMPT_VERSION, queries=[], coverage={}, briefing={}, stats={})
    s.add(run)
    s.flush()

    def prog(msg, pct):
        if progress:
            progress({"step": msg, "pct": pct})

    # 1) retrieval with provenance and coverage report
    prog("retrieving sources", 5)
    coverage = {}
    retrieved: dict[str, list[tuple[RetrievedDoc, str]]] = {t: [] for t in TOPICS}
    queries = []
    for p in providers:
        ok, why = p.available()
        if not ok:
            coverage[p.name] = {"channel": p.channel, "status": "unavailable", "reason": why}
            provider_event(s, f"research:{p.name}", "research", False, why, calls=0, state="unconfigured")
            continue
        n_ok = n_err = 0
        errs = []
        for topic, t in TOPICS.items():
            for kind in ("supporting", "disconfirming"):
                for q in t[kind][:1] if p.channel != "literature" else t[kind]:
                    try:
                        docs = p.search(q, limit=5)
                        n_ok += 1
                        queries.append({"provider": p.name, "query": q, "topic": topic, "kind": kind, "results": len(docs)})
                        retrieved[topic] += [(d, kind) for d in docs]
                    except Exception as e:  # noqa: BLE001 — record, continue
                        n_err += 1
                        errs.append(f"{q}: {str(e)[:160]}")
        coverage[p.name] = {"channel": p.channel, "status": "ok" if not n_err else ("partial" if n_ok else "failed"),
                            "queries_ok": n_ok, "queries_failed": n_err, "errors": errs[:5]}
        provider_event(s, f"research:{p.name}", "research", n_err == 0 or n_ok > 0, "; ".join(errs[:3]), calls=n_ok + n_err)
    coverage["llm"] = {"status": "ok" if llm else "not_configured",
                       "note": (f"{llm.name}:{llm.model}" if llm else "Deterministic keyword extraction + rule templates "
                                "(set ANTHROPIC_API_KEY for model-based extraction, generation and critique)")}
    run.queries, run.coverage = queries, coverage

    # 2) sources + evidence
    prog("extracting evidence", 25)
    known_urls = set(s.scalars(select(Source.url).where(Source.user_id == user_id)).all())
    evidence_by_topic: dict[str, list[dict]] = {t: [] for t in TOPICS}
    n_sources = 0
    for topic, items in retrieved.items():
        seen = set()
        batch: list[tuple[str, RetrievedDoc, str]] = []
        for d, kind in items:
            if d.url in seen or not is_safe_display_url(d.url):
                continue
            seen.add(d.url)
            src = s.scalars(select(Source).where(Source.user_id == user_id, Source.url == d.url)).first()
            if src is None:
                src = Source(user_id=user_id, url=d.url, title=d.title, authors=d.authors, publisher=d.publisher,
                             published_at=d.published_at, source_type=d.source_type, provider=d.provider,
                             retrieval_id=d.retrieval_id, url_verified=not d.synthetic,
                             verification_note=("Synthetic demo record; not a real publication." if d.synthetic else
                                                f"Metadata record retrieved from {d.provider} API ({d.retrieval_id}) at "
                                                f"{now.isoformat()}; URL taken from that record."),
                             abstract=d.abstract, cited_by_count=d.cited_by_count, synthetic=d.synthetic)
                s.add(src)
                s.flush()
                n_sources += 1
            batch.append((src.id, d, kind))
        extracted: dict[str, dict] = {}
        judged: set[str] = set()  # records the model assessed (relevant or not); others fall back to keyword extraction
        if llm and batch:
            for i in range(0, len(batch), 10):
                chunk = batch[i:i + 10]
                res = llm_extract(llm, topic, chunk, budget)
                _record_llm(s, user_id, run.id, "research", PROMPT_VERSION, res, llm)
                if res.ok:
                    for r in res.data["records"]:
                        judged.add(r["record_id"])
                        if not r["relevant"]:
                            continue
                        doc = next(d for sid, d, _ in chunk if sid == r["record_id"])
                        text = f"{doc.title}. {doc.abstract}"
                        q, ql = {"high": (0.8, "high"), "medium": (0.55, "medium"), "low": (0.3, "low")}[r["quality"]]
                        q = min(q, _quality(doc)[0] + 0.1)  # model cannot inflate beyond source-type ceiling
                        extracted[r["record_id"]] = {"stance": r["stance"], "claim": r["claim"][:800], "excerpt": r["excerpt"][:800],
                                                     "limitations": r["limitations"][:800], "quality": q, "quality_label": ql,
                                                     "claim_supported": bool(r["excerpt"]) and _norm(r["excerpt"]) in _norm(text),
                                                     "method": f"llm:{llm.model}"}
        for sid, d, kind in batch:
            ex = extracted.get(sid) if sid in judged else template_extract(topic, d, kind)
            if not ex:
                continue
            ev = Evidence(user_id=user_id, source_id=sid, research_run_id=run.id, topic=topic, stance=ex["stance"],
                          claim=ex["claim"], excerpt=ex["excerpt"], limitations=ex["limitations"], quality=ex["quality"],
                          quality_label=ex["quality_label"], claim_supported=ex["claim_supported"],
                          is_new=d.url not in known_urls, extraction_method=ex["method"])
            s.add(ev)
            s.flush()
            evidence_by_topic[topic].append({"id": ev.id, "source_id": sid, "topic": topic, "stance": ev.stance, "claim": ev.claim,
                                             "excerpt": ev.excerpt, "limitations": ev.limitations, "quality": ev.quality,
                                             "quality_label": ev.quality_label, "claim_supported": ev.claim_supported,
                                             "url_verified": not d.synthetic, "synthetic": d.synthetic, "is_new": ev.is_new,
                                             "url": d.url, "title": d.title,
                                             "published_at": d.published_at.isoformat() if d.published_at else None})

    # 3) candidates
    prog("generating candidate rules", 45)
    ds = load_dataset(s)
    health = data_health(s)
    inventory = {"provider": ds.provider, "adjustment_basis": ds.adjustment_basis, "synthetic": ds.synthetic,
                 "instruments": len(ds.instruments), "has_earnings_events": any(d.events for d in ds.instruments.values()),
                 "datasets": ["daily_ohlcv", "instrument_metadata"] + (["earnings_events"] if any(d.events for d in ds.instruments.values()) else [])}
    candidates: list[tuple[str, StrategySpec, dict, str]] = []  # topic, spec, card, generated_by
    all_ev = [e for v in evidence_by_topic.values() for e in v]
    topic_notes: dict[str, str] = {}
    if llm and all_ev:
        res = llm_generate(llm, all_ev, inventory, budget)
        _record_llm(s, user_id, run.id, "strategy", PROMPT_VERSION, res, llm)
        if res.ok:
            for (spec, card), raw in res.data["compiled"]:
                candidates.append((raw.get("topic") if raw.get("topic") in TOPICS else _topic_for(spec), spec, card, f"llm:{llm.model}"))
            if res.data["errors"]:
                topic_notes["llm_rejected_rules"] = "; ".join(res.data["errors"])[:800]
            if not res.data["compiled"]:
                topic_notes["llm_abstained"] = res.data["abstain_reason"][:500]
        else:
            topic_notes["llm_strategy_failed"] = f"{res.status}: {res.error}"
    if not candidates:
        for topic, evs in evidence_by_topic.items():
            sup = [e for e in evs if e["stance"] == "supporting" and e["claim_supported"]]
            if not sup:
                topic_notes[topic] = "abstained: no supported supporting evidence retrieved"
                continue
            spec = StrategySpec.model_validate(TEMPLATES[topic])
            card = {"thesis": spec.hypothesis, **{k: v for k, v in CARD_TEXT[topic].items()}}
            card["counterargument"] = next((e["claim"] for e in evs if e["stance"] == "disconfirming" and e["claim_supported"]),
                                           "No disconfirming evidence retrieved this run — absence of evidence is not support.")
            candidates.append((topic, spec, card, "template"))

    # 4) dedupe, backtest, critique, rank
    prog("validating candidates", 60)
    active = s.scalars(select(Experiment).where(Experiment.user_id == user_id, Experiment.status.in_(("active", "paused")))).all()
    active_versions = [s.get(StrategyVersion, e.approved_version_id) for e in active]
    active_families = [v.rule_json["family"] for v in active_versions]
    active_sets = [{c.indicator.value for c in iter_conditions(StrategySpec.model_validate(v.rule_json).entry)} for v in active_versions]
    ports = {p.family: p for p in active_portfolios(s, user_id)}
    policy = current_policy(s, user_id)
    evaluated, rejected = [], []
    for topic, spec, card, gen in candidates:
        evs = evidence_by_topic.get(topic) or all_ev
        thesis_fp = canonical_hash({"family": spec.family.value, "entry": sorted(c.indicator.value for c in iter_conditions(spec.entry))})
        fp = canonical_hash({"thesis": thesis_fp, "rule": spec.rule_hash()})
        strat = s.scalars(select(Strategy).where(Strategy.user_id == user_id, Strategy.fingerprint == fp)).first()
        duplicate, dup_note = False, ""
        if strat:
            vers = s.scalars(select(StrategyVersion).where(StrategyVersion.strategy_id == strat.id)).all()
            props = s.scalars(select(Proposal).where(Proposal.strategy_version_id.in_([v.id for v in vers]))).all()
            if any(p.state == "pending" for p in props):
                duplicate, dup_note = True, "a pending proposal for these rules already exists"
            elif any(e.approved_version_id in [v.id for v in vers] for e in active):
                duplicate, dup_note = True, "an active experiment already runs these rules"
            elif any(p.state in ("rejected", "expired") for p in props):
                new_ev = [e for e in evs if e["is_new"] and e["claim_supported"]]
                if new_ev:
                    dup_note = f"previously {props[-1].state}; revisited because {len(new_ev)} new evidence record(s) were retrieved"
                else:
                    duplicate, dup_note = True, f"previously {props[-1].state} and no materially new evidence"
        if duplicate:
            rejected.append({"name": spec.name, "topic": topic, "reason": f"duplicate: {dup_note}"})
            continue
        if strat is None:
            strat = Strategy(user_id=user_id, family=spec.family.value, name=spec.name, fingerprint=fp, thesis_fingerprint=thesis_fp)
            s.add(strat)
            s.flush()
        ver = s.scalars(select(StrategyVersion).where(StrategyVersion.strategy_id == strat.id,
                                                      StrategyVersion.rule_hash == spec.rule_hash())).first()
        ev_ids = [e["id"] for e in evs if e["claim_supported"]]
        if ver is None:
            n = len(s.scalars(select(StrategyVersion.id).where(StrategyVersion.strategy_id == strat.id)).all())
            ver = StrategyVersion(user_id=user_id, strategy_id=strat.id, version=n + 1, rule_json=spec.canonical(),
                                  rule_hash=spec.rule_hash(), thesis=card.get("thesis", spec.hypothesis),
                                  hypothesis={"text": spec.hypothesis, "prediction": spec.prediction.model_dump()},
                                  evidence_ids=ev_ids, generated_by=gen)
            s.add(ver)
            s.flush()
        bt = full_evaluation(spec, ds, Decimal(10000), policy.limits)
        btr = BacktestRun(user_id=user_id, strategy_version_id=ver.id, rule_hash=ver.rule_hash, status=bt["status"],
                          reason=bt.get("reason"), parameters=bt.get("parameters", {}), dataset_manifest=bt.get("dataset_manifest", {}),
                          code_version=bt.get("code_version", "n/a"), metrics={"metrics": bt.get("metrics", {}),
                                                                                "robustness": bt.get("robustness", {}),
                                                                                "robust": bt.get("robust", False)},
                          series=bt.get("series", {}), limitations=bt.get("limitations", []), trigger="research")
        s.add(btr)
        s.flush()
        items = deterministic_critique(spec, bt, evs, ds.synthetic, not cfg.is_demo)
        summary = "Deterministic checklist critique."
        provider = "deterministic"
        if llm:
            litems, lsum, lres = llm_critique(llm, spec, bt, evs, budget)
            _record_llm(s, user_id, run.id, "critic", CRITIC_PROMPT_VERSION, lres, llm)
            if lres.ok:
                items = litems + items
                summary, provider = lsum, f"llm:{llm.model}+deterministic"
        crit = Critique(user_id=user_id, strategy_version_id=ver.id, research_run_id=run.id, items=items, summary=summary,
                        max_severity=max_severity(items), provider=provider)
        s.add(crit)
        s.flush()
        portfolio = ports.get(spec.family.value)
        signalling = _currently_signalling(spec, ds)
        track = _family_track(s, user_id, spec.family.value)
        total, comps, rationale = score(spec, bt, evs, currently_signalling=signalling, active_families=active_families,
                                        active_entry_sets=active_sets, family_track=track, today=now.date())
        robust = bool(bt.get("robust"))
        frac = spec.allocation.max_experiment_fraction * (1.0 if robust else 0.5)
        budget_amt = feasible_budget(s, portfolio, frac, policy.limits) if portfolio else Decimal(0)
        med_price = _median_price(spec, ds)
        budget_ok = portfolio is not None and budget_amt >= max(Decimal(250), Decimal(str(med_price or 0)) * 2)
        gates = hard_gates(evidence=evs, bt=bt, demo=cfg.is_demo, duplicate=False, budget_ok=budget_ok,
                           data_ok=not (health["stale"] and len(health["stale"]) > len(ds.instruments) // 2))
        n_trades = ((bt.get("metrics") or {}).get("full") or {}).get("trade_count") or 0
        exploratory = (not gates["credible_validation"]) and bt.get("status") == "completed" and n_trades >= 10
        gates_for_pub = {k: v for k, v in gates.items() if not (exploratory and k == "credible_validation")}
        eligible = all(gates_for_pub.values()) and total >= float(settings.get("min_score", 70))
        reason = None
        if not all(gates_for_pub.values()):
            reason = "hard gate failed: " + ", ".join(k for k, v in gates_for_pub.items() if not v)
        elif total < float(settings.get("min_score", 70)):
            reason = f"score {total} below minimum {settings.get('min_score', 70)}"
        rec = {"topic": topic, "spec": spec, "card": card, "version": ver, "bt": bt, "btr": btr, "crit": crit, "score": total,
               "components": comps, "rationale": rationale, "gates": gates, "exploratory": exploratory, "budget": budget_amt,
               "portfolio": portfolio, "evidence": evs, "dup_note": dup_note, "signalling": signalling}
        if eligible:
            evaluated.append(rec)
        else:
            rejected.append({"name": spec.name, "topic": topic, "reason": reason, "score": total, "gates": gates,
                             "backtest_run_id": btr.id, "strategy_version_id": ver.id})

    # 5) publish up to N ranked proposals (zero is valid)
    prog("publishing proposals", 85)
    evaluated.sort(key=lambda r: (-r["score"], r["exploratory"]))
    max_n = int(settings.get("max_proposals_per_run", 3))
    published = []
    used_portfolios = set()
    for r in evaluated:
        if len(published) >= max_n:
            rejected.append({"name": r["spec"].name, "topic": r["topic"], "reason": "ranked below the weekly proposal limit",
                             "score": r["score"]})
            continue
        if r["portfolio"].id in used_portfolios:
            r["budget"] = feasible_budget(s, r["portfolio"], r["spec"].allocation.max_experiment_fraction *
                                          (0.5 if r["exploratory"] else 1.0), policy.limits)
        used_portfolios.add(r["portfolio"].id)
        spec, bt = r["spec"], r["bt"]
        full = (bt.get("metrics") or {}).get("full", {})
        sup = [e for e in r["evidence"] if e["stance"] == "supporting" and e["claim_supported"]]
        card = {
            "name": spec.name + (" (exploratory)" if r["exploratory"] else ""), "family": spec.family.value,
            "thesis": r["card"].get("thesis") or spec.hypothesis, "why_now": r["card"].get("why_now", ""),
            "horizon": f"{spec.horizon.value.replace('_', '-')}: up to {spec.holding.max_sessions} sessions per position",
            "max_allocation": str(r["budget"]), "entry_summary": describe_node(spec.entry), "exit_summary": describe_node(spec.exit),
            "ranking_summary": f"rank by {spec.ranking.indicator.value} {spec.ranking.direction}",
            "evidence_quality": r["components"]["evidence_quality"], "evidence_count": len(sup),
            "key_counterargument": r["card"].get("counterargument") or (r["crit"].items[0]["issue"] if r["crit"].items else ""),
            "downside_scenario": r["card"].get("downside", ""), "cadence": r["card"].get("cadence", ""),
            "backtest_status": ("passed robustness checks" if bt.get("robust") else
                                "EXPLORATORY — did not pass robustness checks; tighter allocation"),
            "backtest_headline": {k: full.get(k) for k in ("total_return", "excess_return", "sharpe", "max_drawdown", "trade_count", "win_rate")},
            "data_freshness": health.get("latest_stored_session"), "benchmark": ds.benchmark.symbol if ds.benchmark else None,
            "limitations": bt.get("limitations", []), "synthetic": ds.synthetic, "revisit_note": r["dup_note"] or None,
            "currently_signalling": r["signalling"],
        }
        uncertainty = {"closed_trades_in_backtest": full.get("trade_count"), "sessions": full.get("sessions"),
                       "note": "Score is a ranking heuristic, not a probability of success.",
                       "missing_data": [s_ for s_, d in ds.instruments.items() if not d.bars][:10],
                       "holdout": (bt.get("metrics") or {}).get("holdout")}
        pr = create_proposal(s, user_id, r["version"], r["portfolio"], score=r["score"], components=r["components"],
                             gates=r["gates"], rationale=r["rationale"], uncertainty=uncertainty, card=card, budget=r["budget"],
                             exploratory=r["exploratory"], research_run_id=run.id, backtest_run_id=r["btr"].id,
                             critique_id=r["crit"].id, validity_days=int(settings.get("proposal_validity_days", 7)),
                             rank=len(published) + 1)
        published.append(pr)
    if published:
        notify(s, user_id, "proposal", f"{len(published)} new strategy proposal(s) awaiting your decision",
               "Review the thesis, evidence and rules, then answer Yes or No.", f"proposals:{run.id}", "/approvals", "urgent")
    run.briefing = {
        "headline": (f"{len(published)} proposal(s) published" if published else "No qualified proposals this run — "
                     "nothing met the evidence, validation and score thresholds"),
        "published": [p.id for p in published], "rejected": rejected, "notes": topic_notes,
        "topics": {t: {"supporting": sum(1 for e in v if e["stance"] == "supporting" and e["claim_supported"]),
                       "disconfirming": sum(1 for e in v if e["stance"] == "disconfirming" and e["claim_supported"]),
                       "background": sum(1 for e in v if e["stance"] == "background")} for t, v in evidence_by_topic.items()},
        "data": {"provider": ds.provider, "synthetic": ds.synthetic, "health": health},
    }
    run.stats = {"sources_new": n_sources, "evidence": len(all_ev), "candidates": len(candidates), "published": len(published),
                 "llm_calls": budget.calls}
    run.cost_usd = round(budget.cost, 4)
    run.state = "succeeded"
    run.finished_at = utcnow()
    audit(s, user_id, "research_agent", "research.complete", "research_run", run.id, None, run.stats)
    prog("done", 100)
    return run


def _topic_for(spec: StrategySpec) -> str:
    for t, v in TOPICS.items():
        if v["family"] == spec.family.value:
            return t
    return "reversal"


def _currently_signalling(spec: StrategySpec, ds) -> bool:
    for sym, d in ds.instruments.items():
        if d.type not in spec.universe.asset_types or not d.bars:
            continue
        t = d.bars[-1].session
        view = BarView(d.bars, t, cal.data_ready_utc(t), [e for e in d.events if e.known_at <= cal.data_ready_utc(t)])
        ok, trace = evaluate(spec.entry, view)
        if ok and not has_missing(trace):
            return True
    return False


def _median_price(spec: StrategySpec, ds) -> float | None:
    px = sorted(d.bars[-1].close for d in ds.instruments.values() if d.bars and d.type in spec.universe.asset_types)
    return px[len(px) // 2] if px else None


def _family_track(s: Session, user_id: str, family: str) -> float:
    """Bounded learning influence on ranking: +5 per supported, -10 per invalidated lesson (clamped to ±15)."""
    rows = s.execute(select(Lesson.outcome).join(Experiment, Experiment.id == Lesson.experiment_id)
                     .join(Portfolio, Portfolio.id == Experiment.portfolio_id)
                     .where(Lesson.user_id == user_id, Portfolio.family == family)).all()
    adj = sum(5 if r[0] == "supported" else -10 if r[0] == "invalidated" else 0 for r in rows)
    return max(-15.0, min(15.0, float(adj)))


def _unused(_d: date) -> None:  # keeps type imports referenced for static analysers
    return None
