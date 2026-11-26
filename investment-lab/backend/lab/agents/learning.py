"""Monthly learning review and proposal Q&A.

The learning agent compares realised results with each experiment's pre-registered prediction and
its backtest expectation, separates noise from repeatable signal (minimum sample sizes), stores a
structured lesson, and turns recommendations into PROPOSALS (pause / retire / modify). Nothing is
changed without a fresh approval; modifications create a new immutable version and legacy holdings
stay under their original rules until exit.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..backtest import full_evaluation
from ..models import (BacktestRun, Evidence, Experiment, Lesson, Order, Portfolio, Proposal, QAMessage, Source,
                      StrategyVersion, ValuationSnapshot)
from ..providers.llm import LLMBudget, get_llm
from ..strategy_schema import StrategySpec, canonical_hash, describe_node
from ..config import get_settings
from ..services.core import audit, current_policy, notify
from ..services.engine import experiment_metrics
from ..services.market_data import load_dataset
from ..services.proposals import create_proposal

LEARNING_PROMPT_VERSION = "learning-2026-11-26.v1"
MIN_TRADES_FOR_VERDICT = 10

LESSON_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["lesson", "competing_explanation", "confidence"],
                 "properties": {"lesson": {"type": "string"}, "competing_explanation": {"type": "string"},
                                "confidence": {"type": "string", "enum": ["low", "medium", "high"]}}}


def _cost_drag(s: Session, exp: Experiment) -> float | None:
    rows = s.execute(select(func.sum(Order.slippage_amount), func.sum(Order.gross_amount)).where(
        Order.experiment_id == exp.id, Order.status == "filled")).one()
    slip, gross = rows
    if not gross:
        return None
    return float(slip / gross)


def review_experiment(s: Session, exp: Experiment, period: str, llm=None, budget=None) -> Lesson | None:
    if s.scalars(select(Lesson).where(Lesson.experiment_id == exp.id, Lesson.period == period)).first():
        return None
    ver = s.get(StrategyVersion, exp.approved_version_id)
    spec = StrategySpec.model_validate(ver.rule_json)
    m = experiment_metrics(s, exp)
    bt = s.scalars(select(BacktestRun).where(BacktestRun.strategy_version_id == ver.id, BacktestRun.status == "completed")
                   .order_by(BacktestRun.created_at.desc())).first()
    expected = ((bt.metrics or {}).get("metrics", {}).get("validation", {}) if bt else {}) or {}
    pred = spec.prediction
    snap = s.scalars(select(ValuationSnapshot).where(ValuationSnapshot.portfolio_id == exp.portfolio_id)
                     .order_by(ValuationSnapshot.session.desc())).first()
    p = s.get(Portfolio, exp.portfolio_id)
    bench_ret = float(snap.benchmark_nav / p.starting_cash - 1) if snap and snap.benchmark_nav else None
    realized = {"avg_trade_return": m["avg_trade_return"], "win_rate": m["win_rate"],
                "excess_vs_benchmark": (m["return_on_budget"] - bench_ret) if (m["return_on_budget"] is not None and bench_ret is not None) else None}
    observed = realized.get(pred.metric)
    n = m["closed_trades"]
    drag = _cost_drag(s, exp)
    metrics = {"closed_trades": n, "open_positions": m["open_positions"], "total_pnl": str(m["total_pnl"]),
               "return_on_budget": m["return_on_budget"], "realized": realized, "predicted": pred.model_dump(),
               "backtest_validation_expectation": {k: expected.get(k) for k in ("avg_trade_return", "win_rate", "excess_return")},
               "cost_drag_fraction_of_gross": drag, "benchmark_return": bench_ret}
    if exp.thesis_status == "invalidated":
        outcome, rec = "invalidated", "retire"
        lesson = "A pre-approved invalidation rule triggered; the hypothesis failed its own stated test."
    elif n < MIN_TRADES_FOR_VERDICT:
        outcome, rec = "inconclusive", "continue"
        lesson = (f"Only {n} closed trades — too few to distinguish signal from noise (minimum {MIN_TRADES_FOR_VERDICT}). "
                  "No change recommended; avoid declaring a winner or loser.")
    else:
        met = observed is not None and observed > pred.threshold
        if met:
            outcome, rec = "supported", "continue"
            lesson = f"Realised {pred.metric} {observed:.4f} exceeds the pre-registered threshold {pred.threshold} over {n} trades."
        else:
            outcome = "challenged"
            if drag is not None and m["avg_trade_return"] is not None and abs(m["avg_trade_return"]) > 0 and \
                    drag * 2 > abs(m["avg_trade_return"]) * 0.5:
                rec = "modify"
                lesson = (f"Realised {pred.metric} {observed} misses the threshold; costs (~{drag:.2%} per side) consume a "
                          "large share of the gross edge. A lower-turnover version is proposed.")
            else:
                rec = "pause"
                lesson = f"Realised {pred.metric} {observed} misses the pre-registered threshold over {n} trades."
    competing = ("Results over a few months are dominated by market regime and noise; a benchmark-relative shortfall may "
                 "reflect sector concentration rather than the hypothesis.")
    confidence = "low" if n < 30 else "medium"
    provider = "deterministic"
    if llm is not None:
        doc = {"experiment": exp.name, "hypothesis": spec.hypothesis, "metrics": metrics, "deterministic_outcome": outcome,
               "recommendation": rec}
        res = llm.structured("learning", "You are the LEARNING agent. Compare realised results with the pre-registered "
                             "hypothesis and benchmark. Distinguish noise from repeatable signal; never declare a winner on few "
                             "trades. Provide a concise lesson and the strongest competing explanation.",
                             "<untrusted_documents>" + json.dumps(doc, default=str) + "</untrusted_documents>", LESSON_SCHEMA, None, budget)
        if res.ok:
            lesson = res.data["lesson"][:2000] + f" [deterministic outcome: {outcome}]"
            competing = res.data["competing_explanation"][:2000]
            confidence = min(confidence, res.data["confidence"], key=["low", "medium", "high"].index)
            provider = f"llm:{res.model}"
    les = Lesson(user_id=exp.user_id, experiment_id=exp.id, period=period, outcome=outcome, recommendation=rec, lesson=lesson,
                 competing_explanation=competing, confidence=confidence, metrics=metrics,
                 evidence_links=list(ver.evidence_ids or [])[:10], provider=provider)
    s.add(les)
    s.flush()
    pr = None
    if rec in ("pause", "retire"):
        pr = create_proposal(s, exp.user_id, ver, p, score=None, components={}, gates={}, rationale={"lesson": lesson},
                             uncertainty={"closed_trades": n}, card={"name": f"{rec.title()}: {exp.name}", "family": spec.family.value,
                                                                     "thesis": lesson, "why_now": "Monthly learning review",
                                                                     "key_counterargument": competing, "kind": rec},
                             budget=exp.budget, exploratory=False, research_run_id=None, backtest_run_id=bt.id if bt else None,
                             critique_id=None, kind=rec, parent_experiment_id=exp.id)
    elif rec == "modify":
        pr = propose_modification(s, exp, spec, lesson)
    if pr:
        les.proposal_id = pr.id
        notify(s, exp.user_id, "proposal", f"Review recommends '{rec}' for {exp.name}", lesson[:300], f"lesson-prop:{les.id}",
               "/approvals", "info")
    audit(s, exp.user_id, "learning_agent", "lesson.create", "lesson", les.id, None, {"outcome": outcome, "rec": rec})
    return les


def propose_modification(s: Session, exp: Experiment, spec: StrategySpec, reason: str, new_spec_data: dict | None = None):
    data = new_spec_data or spec.canonical()
    if new_spec_data is None:  # deterministic lower-turnover revision
        data["holding"]["min_sessions"] = max(data["holding"]["min_sessions"], 5)
        data["allocation"]["max_positions"] = max(1, data["allocation"]["max_positions"] - 1)
        data["name"] = (spec.name + " (lower turnover)")[:120]
    new = StrategySpec.model_validate(data)
    old_ver = s.get(StrategyVersion, exp.approved_version_id)
    n = s.scalar(select(func.max(StrategyVersion.version)).where(StrategyVersion.strategy_id == old_ver.strategy_id)) or 1
    diff = _diff(spec.canonical(), new.canonical())
    ver = s.scalars(select(StrategyVersion).where(StrategyVersion.strategy_id == old_ver.strategy_id,
                                                  StrategyVersion.rule_hash == new.rule_hash())).first()
    if ver is None:
        ver = StrategyVersion(user_id=exp.user_id, strategy_id=old_ver.strategy_id, version=n + 1, rule_json=new.canonical(),
                              rule_hash=new.rule_hash(), thesis=old_ver.thesis, hypothesis=old_ver.hypothesis,
                              evidence_ids=old_ver.evidence_ids, parent_version_id=old_ver.id, diff=diff,
                              generated_by="learning_agent")
        s.add(ver)
        s.flush()
    ds = load_dataset(s)
    pol = current_policy(s, exp.user_id)
    bt = full_evaluation(new, ds, Decimal(10000), pol.limits)
    btr = BacktestRun(user_id=exp.user_id, strategy_version_id=ver.id, rule_hash=ver.rule_hash, status=bt["status"],
                      reason=bt.get("reason"), parameters=bt.get("parameters", {}), dataset_manifest=bt.get("dataset_manifest", {}),
                      code_version=bt.get("code_version", "n/a"), metrics={"metrics": bt.get("metrics", {}),
                                                                            "robustness": bt.get("robustness", {}), "robust": bt.get("robust")},
                      series=bt.get("series", {}), limitations=bt.get("limitations", []), trigger="learning")
    s.add(btr)
    s.flush()
    p = s.get(Portfolio, exp.portfolio_id)
    card = {"name": f"Modify: {new.name}", "family": new.family.value, "thesis": reason, "why_now": "Monthly learning review",
            "entry_summary": describe_node(new.entry), "exit_summary": describe_node(new.exit), "diff": diff,
            "legacy_treatment": "Existing holdings stay under the ORIGINAL approved rules until they exit.",
            "max_allocation": str(exp.budget), "backtest_status": "passed robustness checks" if bt.get("robust") else
            "did not pass robustness checks", "kind": "modification"}
    return create_proposal(s, exp.user_id, ver, p, score=None, components={}, gates={}, rationale={"reason": reason}, uncertainty={},
                           card=card, budget=exp.budget, exploratory=not bt.get("robust"), research_run_id=None,
                           backtest_run_id=btr.id, critique_id=None, kind="modification", parent_experiment_id=exp.id,
                           legacy_treatment="original_rules_until_exit")


def _diff(a: dict, b: dict, path: str = "") -> dict:
    out = {}
    for k in sorted(set(a) | set(b)):
        pa, pb = a.get(k), b.get(k)
        key = f"{path}.{k}" if path else k
        if isinstance(pa, dict) and isinstance(pb, dict):
            out.update(_diff(pa, pb, key))
        elif pa != pb:
            out[key] = {"from": pa, "to": pb}
    return out


def run_monthly(s: Session, user_id: str, period: str | None = None, use_llm: bool = True) -> dict:
    period = period or datetime.now(timezone.utc).strftime("%Y-%m")
    llm = get_llm() if use_llm else None
    cfg = get_settings()
    budget = LLMBudget(cfg.research_max_llm_calls, cfg.research_max_cost_usd)
    exps = s.scalars(select(Experiment).where(Experiment.user_id == user_id,
                                              Experiment.status.in_(("active", "paused", "legacy", "completed", "closed")))).all()
    out = []
    for e in exps:
        if e.status in ("completed", "closed") and e.ended_at and e.ended_at.strftime("%Y-%m") < period and \
                s.scalars(select(Lesson).where(Lesson.experiment_id == e.id)).first():
            continue
        les = review_experiment(s, e, period, llm, budget)
        if les:
            out.append({"experiment": e.id, "outcome": les.outcome, "recommendation": les.recommendation})
    notify(s, user_id, "review", f"Monthly review {period}: {len(out)} lesson(s)", "See the journal for lessons and any proposals.",
           f"monthly:{period}", "/journal", "info")
    return {"period": period, "lessons": out}


# ---------------------------------------------------------------------------------------------
QA_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["answer", "citations", "cannot_answer"],
             "properties": {"answer": {"type": "string"}, "citations": {"type": "array", "items": {"type": "string"}},
                            "cannot_answer": {"type": "boolean"}}}


def answer_question(s: Session, user_id: str, proposal: Proposal, question: str) -> QAMessage:
    question = question.strip()[:1000]
    ver = s.get(StrategyVersion, proposal.strategy_version_id)
    evs = s.scalars(select(Evidence).where(Evidence.id.in_(ver.evidence_ids or []))).all()
    ev_docs = []
    for e in evs:
        src = s.get(Source, e.source_id)
        ev_docs.append({"id": e.id, "stance": e.stance, "claim": e.claim, "excerpt": e.excerpt, "limitations": e.limitations,
                        "title": src.title, "url": src.url})
    bt = s.get(BacktestRun, proposal.backtest_run_id) if proposal.backtest_run_id else None
    llm = get_llm()
    if llm:
        ids = {d["id"] for d in ev_docs}

        def validate(d):
            bad = [c for c in d["citations"] if c not in ids]
            if bad:
                raise ValueError(f"unknown citations {bad}")
            return d

        doc = {"card": proposal.card, "rules": ver.rule_json, "backtest": (bt.metrics if bt else None), "evidence": ev_docs,
               "scope": proposal.scope}
        res = llm.structured("qa", "Answer the user's question about this paper-trading proposal using ONLY the provided "
                             "record. Cite evidence ids. You cannot change rules, scope or settings; say so if asked.",
                             f"<untrusted_documents>{json.dumps(doc, default=str)[:60000]}</untrusted_documents>\nQuestion: {question}",
                             QA_SCHEMA, validate)
        if res.ok:
            msg = QAMessage(user_id=user_id, proposal_id=proposal.id, question=question, answer=res.data["answer"],
                            citations=res.data["citations"], provider=f"llm:{res.model}")
            s.add(msg)
            return msg
    q = question.lower()
    parts = []
    if any(w in q for w in ("risk", "lose", "downside", "worst")):
        parts.append(f"Downside: {proposal.card.get('downside_scenario', 'n/a')}. Max allocation ${proposal.card.get('max_allocation')}.")
    if any(w in q for w in ("evidence", "source", "paper", "research", "why")):
        parts += [f"[{d['id'][:8]}] {d['claim']}" for d in ev_docs[:3]]
    if any(w in q for w in ("exit", "sell", "stop")):
        parts.append(f"Exit rules: {proposal.card.get('exit_summary')}")
    if any(w in q for w in ("entry", "buy", "when")):
        parts.append(f"Entry rules: {proposal.card.get('entry_summary')}")
    if any(w in q for w in ("backtest", "history", "perform", "return")):
        parts.append(f"Backtest headline: {json.dumps(proposal.card.get('backtest_headline'))}")
    if any(w in q for w in ("change", "modify", "edit")):
        parts.append("Q&A cannot change rules or scope. Rule changes require a new proposal and approval.")
    if not parts:
        parts.append("No model is configured, so answers come from stored proposal fields. Ask about risk, evidence, "
                     "entry, exit or backtest.")
    msg = QAMessage(user_id=user_id, proposal_id=proposal.id, question=question, answer=" ".join(parts),
                    citations=[d["id"] for d in ev_docs[:3]] if "evidence" in q or "why" in q else [], provider="deterministic")
    s.add(msg)
    return msg


def _hash(x) -> str:
    return canonical_hash(x)


def _today() -> date:
    return date.today()
