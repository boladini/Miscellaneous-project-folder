"""Deterministic rule interpreter shared by backtests and forward paper trading."""
from __future__ import annotations

from typing import Any

from .indicators import BarView, PositionContext, compute
from .strategy_schema import AllGroup, AnyGroup, Condition, NotGroup, Op


def _cmp(op: Op, x: float, v: float, v2: float | None) -> bool:
    if op == Op.lt:
        return x < v
    if op == Op.lte:
        return x <= v
    if op == Op.gt:
        return x > v
    if op == Op.gte:
        return x >= v
    if op == Op.between:
        return v <= x <= (v2 if v2 is not None else v)
    raise ValueError(op)


def evaluate(node: Any, view: BarView, pos: PositionContext | None = None) -> tuple[bool, dict]:
    """Return (result, trace). Missing indicator values make a condition False and are flagged."""
    if isinstance(node, Condition):
        v = view.lagged(node.lag) if node.lag else view
        x = compute(node.indicator, node.params, v, pos)
        if x is None:
            return False, {"indicator": node.indicator.value, "params": node.params, "lag": node.lag,
                           "value": None, "missing": True, "result": False}
        r = _cmp(node.op, x, node.value, node.value2)
        return r, {"indicator": node.indicator.value, "params": node.params, "lag": node.lag, "op": node.op.value,
                   "threshold": node.value, "threshold2": node.value2, "value": round(x, 8), "result": r}
    if isinstance(node, AllGroup):
        traces, ok = [], True
        for c in node.all:
            r, t = evaluate(c, view, pos)
            traces.append(t)
            ok = ok and r
        return ok, {"all": traces, "result": ok}
    if isinstance(node, AnyGroup):
        traces, ok = [], False
        for c in node.any:
            r, t = evaluate(c, view, pos)
            traces.append(t)
            ok = ok or r
        return ok, {"any": traces, "result": ok}
    if isinstance(node, NotGroup):
        r, t = evaluate(node.not_, view, pos)
        missing = has_missing(t)
        return (False if missing else not r), {"not": t, "result": (False if missing else not r)}
    raise ValueError(f"unknown node {type(node)}")


def has_missing(trace: dict) -> bool:
    if trace.get("missing"):
        return True
    for k in ("all", "any"):
        if k in trace and any(has_missing(t) for t in trace[k]):
            return True
    if "not" in trace:
        return has_missing(trace["not"])
    return False


def first_true_reason(trace: dict) -> str:
    """Human-readable reason for an exit trigger."""
    if "indicator" in trace and trace.get("result"):
        return f"{trace['indicator']}={trace['value']} {trace.get('op')} {trace.get('threshold')}"
    for k in ("any", "all"):
        if k in trace:
            parts = [first_true_reason(t) for t in trace[k] if t.get("result")]
            parts = [p for p in parts if p]
            if parts:
                return "; ".join(parts) if k == "all" else parts[0]
    return ""
