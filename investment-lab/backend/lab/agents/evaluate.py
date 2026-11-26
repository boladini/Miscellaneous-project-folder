"""Critic (deterministic checklist + optional independent LLM pass) and ranking service.

Ranking uses FIXED weights and deterministic hard gates. A score is not a probability of success,
and a numeric score can never override a failed validation gate.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date

from ..strategy_schema import Indicator, StrategySpec, iter_conditions

WEIGHTS = {"evidence_quality": 0.25, "risk_adjusted_potential": 0.20, "historical_robustness": 0.20,
           "current_applicability": 0.15, "downside_controls": 0.10, "diversification": 0.10}
SEVERITY_ORDER = ["low", "medium", "high", "critical"]
CRITIC_PROMPT_VERSION = "critic-2026-11-26.v1"


def clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def deterministic_critique(spec: StrategySpec, bt: dict, evidence: list[dict], synthetic: bool, live: bool) -> list[dict]:
    items = []
    m = (bt or {}).get("metrics", {})
    full = m.get("full", {})
    if bt.get("status") != "completed":
        items.append({"issue": f"Not backtestable with current data: {bt.get('reason')}", "category": "data",
                      "severity": "critical", "claim_refs": [], "mitigation": "Abstain until point-in-time data is available."})
        return items
    n = full.get("trade_count") or 0
    if n < 30:
        items.append({"issue": f"Only {n} closed backtest trades; estimates are noisy.", "category": "sample_size",
                      "severity": "high" if n < 15 else "medium", "claim_refs": [],
                      "mitigation": "Treat as exploratory; require more forward observations before conclusions."})
    st = m.get("stress_cost", {})
    if st and (st.get("total_return") is None or st.get("total_return") <= 0):
        items.append({"issue": f"Returns do not survive {spec.costs.stress_slippage_bps:g} bps stress costs.",
                      "category": "costs", "severity": "high", "claim_refs": [],
                      "mitigation": "Reduce turnover or restrict to the most liquid names."})
    hold = m.get("holdout", {})
    if hold.get("excess_return") is not None and hold["excess_return"] < 0:
        items.append({"issue": "Strategy trailed the benchmark in the held-out period.", "category": "regime",
                      "severity": "medium", "claim_refs": [], "mitigation": "Monitor benchmark-relative results closely."})
    trades = (bt.get("series") or {}).get("trades") or []
    by_year: dict[str, float] = defaultdict(float)
    for t in trades:
        by_year[t["exit_session"][:4]] += t["pnl"]
    tot = sum(v for v in by_year.values() if v > 0)
    if tot > 0 and max(by_year.values()) / tot > 0.6 and len(by_year) > 1:
        yr = max(by_year, key=by_year.get)
        items.append({"issue": f"Over 60% of gains came from {yr}; results may be regime-specific.", "category": "regime",
                      "severity": "medium", "claim_refs": [], "mitigation": "Check performance outside that year."})
    if live:
        items.append({"issue": "Backtest universe is today's list of large liquid names (survivorship bias).",
                      "category": "survivorship", "severity": "medium", "claim_refs": [],
                      "mitigation": "Discount historical returns; rely on forward paper results."})
    if synthetic:
        items.append({"issue": "All prices and evidence are SYNTHETIC demo data.", "category": "data", "severity": "high",
                      "claim_refs": [], "mitigation": "Configure live data and research providers for meaningful results."})
    dis = [e for e in evidence if e["stance"] == "disconfirming" and e.get("claim_supported")]
    for e in dis[:2]:
        items.append({"issue": f"Disconfirming evidence: {e['claim'][:220]}", "category": "evidence", "severity": "medium",
                      "claim_refs": [e["id"]], "mitigation": "Weigh against supporting evidence; monitor invalidation rule."})
    items.append({"issue": "Published, well-known effects may be crowded and decay after publication.", "category": "crowding",
                  "severity": "low", "claim_refs": [], "mitigation": "Compare forward results with backtest expectations."})
    if any(c.indicator in (Indicator.return_since_entry, Indicator.drawdown_from_peak) for c in iter_conditions(spec.exit)):
        items.append({"issue": "Loss exits are evaluated on closes and fill at the next open; gaps can exceed the stop.",
                      "category": "execution", "severity": "low", "claim_refs": [],
                      "mitigation": "Position sizes are capped; the stop is not a guaranteed loss limit."})
    return items


def max_severity(items: list[dict]) -> str:
    if not items:
        return "low"
    return max((i["severity"] for i in items), key=SEVERITY_ORDER.index)


CRITIC_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["items", "summary"],
    "properties": {
        "summary": {"type": "string"},
        "items": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["issue", "category", "severity", "claim_refs", "mitigation"],
            "properties": {
                "issue": {"type": "string"},
                "category": {"type": "string", "enum": ["evidence", "confounding", "survivorship", "look_ahead", "regime",
                                                        "tail_risk", "crowding", "costs", "execution", "data", "sample_size"]},
                "severity": {"type": "string", "enum": SEVERITY_ORDER},
                "claim_refs": {"type": "array", "items": {"type": "string"}},
                "mitigation": {"type": "string"}}}}},
}


def llm_critique(llm, spec: StrategySpec, bt: dict, evidence: list[dict], budget) -> tuple[list[dict], str, object]:
    ids = {e["id"] for e in evidence}

    def validate(d):
        for it in d["items"]:
            bad = [r for r in it["claim_refs"] if r not in ids]
            if bad:
                raise ValueError(f"unknown claim_refs {bad}")
        return d

    metrics = (bt or {}).get("metrics", {})
    doc = {"strategy_rules": spec.canonical(), "backtest_status": bt.get("status"), "metrics": metrics,
           "limitations": bt.get("limitations"), "evidence": [{k: e[k] for k in ("id", "stance", "claim", "limitations", "quality_label")}
                                                              for e in evidence]}
    system = ("You are the CRITIC agent. Independently identify weak evidence, confounding, survivorship or look-ahead "
              "bias, regime sensitivity, tail risk, crowding, cost and execution limitations. Be specific and severe "
              "where warranted. Reference evidence only by the given ids. You are a separate model pass, not a human reviewer.")
    res = llm.structured("critic", system, "<untrusted_documents>\n" + json.dumps(doc, default=str)[:60000]
                         + "\n</untrusted_documents>", CRITIC_SCHEMA, validate, budget)
    if not res.ok:
        return [], "", res
    return res.data["items"], res.data["summary"], res


def score(spec: StrategySpec, bt: dict, evidence: list[dict], *, currently_signalling: bool, active_families: list[str],
          active_entry_sets: list[set], family_track: float = 0.0, today: date | None = None) -> tuple[float, dict, dict]:
    today = today or date.today()
    sup = sorted([e for e in evidence if e["stance"] == "supporting" and e.get("claim_supported")],
                 key=lambda e: -e["quality"])
    dis = [e for e in evidence if e["stance"] == "disconfirming" and e.get("claim_supported")]
    ev = sum(e["quality"] for e in sup[:3]) / max(1, min(3, len(sup))) * 100 if sup else 0.0
    if len({e.get("source_id") for e in sup}) >= 2:
        ev += 10
    if any(e["quality"] >= 0.6 for e in dis):
        ev -= 15
    ev = clamp(ev)
    top_q = ", ".join("%.2f" % e["quality"] for e in sup[:3]) or "n/a"
    rationale = {"evidence_quality": f"{len(sup)} supported supporting claims (top quality {top_q}); {len(dis)} disconfirming."}
    m = (bt or {}).get("metrics", {})
    full, hold = m.get("full", {}), m.get("holdout", {})
    sharpe = full.get("sharpe")
    if bt.get("status") == "completed" and sharpe is not None:
        rap = clamp((sharpe + 0.25) / 1.5 * 100)
        if (full.get("excess_return") or 0) > 0:
            rap = clamp(rap + 10)
        rationale["risk_adjusted_potential"] = f"Full-period Sharpe {sharpe:.2f} (rf=0); excess vs benchmark {full.get('excess_return')}"
    else:
        rap = 0.0
        rationale["risk_adjusted_potential"] = "No completed backtest."
    rob = bt.get("robustness") or {}
    hr = 100.0 * sum(rob.values()) / len(rob) if rob else 0.0
    rationale["historical_robustness"] = f"{sum(rob.values())}/{len(rob)} robustness checks passed: " + \
        ", ".join(f"{k}={'pass' if v else 'fail'}" for k, v in rob.items()) if rob else "No robustness checks."
    newest = max((e.get("published_at") for e in sup if e.get("published_at")), default=None)
    recent = bool(newest and (today - date.fromisoformat(newest)).days < 3650)
    ca = 40 * ((hold.get("total_return") or -1) > 0) + 30 * currently_signalling + 30 * recent + family_track
    ca = clamp(ca)
    rationale["current_applicability"] = (f"holdout return {'positive' if (hold.get('total_return') or -1) > 0 else 'not positive'}; "
                                          f"{'instruments currently meet entry rules' if currently_signalling else 'no current signals'}; "
                                          f"newest supporting evidence {newest or 'n/a'}; track-record adj {family_track:+.0f}")
    exit_conds = list(iter_conditions(spec.exit))
    has_loss_exit = any(c.indicator in (Indicator.return_since_entry, Indicator.drawdown_from_peak) and c.op.value in ("lt", "lte")
                        for c in exit_conds) or any(c.indicator == Indicator.sma_ratio for c in exit_conds)
    mdd = full.get("max_drawdown")
    dc = 40 * has_loss_exit + 20 + (40 * clamp(1 - abs(mdd) / 0.4, 0, 1) if mdd is not None else 0)
    rationale["downside_controls"] = f"loss exit {'present' if has_loss_exit else 'absent'}; time limit present; backtest max DD {mdd}"
    entry_set = {c.indicator.value for c in iter_conditions(spec.entry)}
    div = 100.0
    if spec.family.value in active_families:
        div -= 50
    if any(entry_set == s for s in active_entry_sets):
        div -= 30
    rationale["diversification"] = f"family already active: {spec.family.value in active_families}; same entry indicators active: " \
                                   f"{any(entry_set == s for s in active_entry_sets)}"
    comps = {"evidence_quality": round(ev, 1), "risk_adjusted_potential": round(rap, 1), "historical_robustness": round(hr, 1),
             "current_applicability": round(ca, 1), "downside_controls": round(dc, 1), "diversification": round(clamp(div), 1)}
    total = round(sum(WEIGHTS[k] * v for k, v in comps.items()), 1)
    return total, comps, rationale


def hard_gates(*, evidence: list[dict], bt: dict, demo: bool, duplicate: bool, budget_ok: bool, data_ok: bool) -> dict:
    sup = [e for e in evidence if e["stance"] == "supporting" and e.get("claim_supported")]
    verifiable = any(e.get("url_verified") or (demo and e.get("synthetic")) for e in sup)
    return {
        "verifiable_evidence": verifiable,
        "executable_rules": True,
        "data_available": data_ok and bt.get("status") == "completed",
        "feasible_risk_budget": budget_ok,
        "no_look_ahead": True,
        "not_duplicate": not duplicate,
        "credible_validation": bool(bt.get("robust")),
    }
