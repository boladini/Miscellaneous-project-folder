#!/usr/bin/env python3
"""Apply Star/Skip/More feedback to source weights.

Reads feedback/inbox.md, resolves each signal's article to its source via the per-digest
scores.json files, nudges that source's weight in sources.json, logs the action to
pipeline/feedback.json, and clears resolved lines from the inbox (unresolved lines are
kept so they can be corrected).

    STAR  +0.20   "more like this"
    MORE  +0.30   "go deeper" (also a hint to queue more from this author/topic)
    SKIP  -0.20   "not interesting"
Weights are clamped to [0.1, 3.0].

Usage: python scripts/apply_feedback.py [--dry-run]
"""
from __future__ import annotations

import argparse
import re
import sys

import lib

DELTAS = {"STAR": 0.20, "MORE": 0.30, "SKIP": -0.20}
CLAMP_LO, CLAMP_HI = 0.1, 3.0
MARKER = "## Pending signals (edit below this line)"
SIGNAL_RE = re.compile(r"^\s*(STAR|MORE|SKIP)\s+(.+?)\s*$", re.I)
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def norm_url(u: str) -> str:
    return re.sub(r"[#?].*$", "", (u or "")).rstrip("/").lower()


def build_index() -> dict:
    """Map article id / 'date/id' / url -> article record (with date)."""
    index: dict[str, dict] = {}
    for scores in sorted(lib.DIGESTS.glob("*/scores.json")):
        data = lib.load_json(scores, default={}) or {}
        date = data.get("date") or scores.parent.name
        for art in data.get("articles", []):
            art = {**art, "date": date}
            aid = art.get("id", "")
            if aid:
                index.setdefault(aid, art)
                index.setdefault(f"{date}/{aid}", art)
            if art.get("url"):
                index.setdefault(norm_url(art["url"]), art)
    return index


def parse_inbox(text: str):
    pending = text.split(MARKER, 1)[1] if MARKER in text else text
    pending = HTML_COMMENT_RE.sub("", pending)
    signals = []
    for raw in pending.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = SIGNAL_RE.match(line)
        if m:
            signals.append((m.group(1).upper(), m.group(2).strip(), raw))
    return signals


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--inbox", default=str(lib.FEEDBACK_INBOX))
    args = ap.parse_args()

    inbox_path = lib.FEEDBACK_INBOX if args.inbox == str(lib.FEEDBACK_INBOX) else args.inbox
    inbox_text = ""
    try:
        inbox_text = open(inbox_path, encoding="utf-8").read()
    except FileNotFoundError:
        log(f"No inbox at {inbox_path}; nothing to do.")
        return 0

    signals = parse_inbox(inbox_text)
    if not signals:
        log("No pending feedback signals.")
        return 0

    index = build_index()
    sources = lib.load_json(lib.SOURCES_JSON)
    by_id = {s["id"]: s for s in sources.get("sources", [])}
    feedback = lib.load_json(lib.FEEDBACK_JSON, default={"version": 1, "processed": []})
    feedback.setdefault("processed", [])

    resolved_raw: list[str] = []
    unresolved: list[str] = []
    applied = 0

    for signal, token, raw in signals:
        art = index.get(token) or index.get(norm_url(token)) or index.get(token.split("/")[-1])
        if not art:
            unresolved.append(raw)
            log(f"[unresolved] {signal} {token} — no matching article id/url found.")
            continue
        src = by_id.get(art.get("source_id"))
        if not src:
            unresolved.append(raw)
            log(f"[unresolved] {signal} {token} — source '{art.get('source_id')}' not in sources.json.")
            continue
        before = float(src.get("weight", 1.0))
        after = round(min(CLAMP_HI, max(CLAMP_LO, before + DELTAS[signal])), 3)
        src["weight"] = after
        feedback["processed"].append(
            {
                "time": lib.now_iso(),
                "signal": signal,
                "article_id": art.get("id"),
                "article_title": art.get("title"),
                "source_id": src["id"],
                "source": src.get("name"),
                "author": art.get("author"),
                "domains": art.get("domains") or ([art.get("domain")] if art.get("domain") else []),
                "top_trigger": art.get("top_trigger"),
                "weight_before": before,
                "weight_after": after,
            }
        )
        applied += 1
        resolved_raw.append(raw)
        log(f"[{signal}] {art.get('title','?')[:60]} → {src['id']} weight {before} → {after}")

    if args.dry_run:
        log(f"DRY RUN: would apply {applied} signal(s); {len(unresolved)} unresolved.")
        return 0

    lib.save_json(lib.SOURCES_JSON, sources)
    lib.save_json(lib.FEEDBACK_JSON, feedback)

    # Rewrite inbox: keep header + instructions, drop resolved lines, keep unresolved.
    header = inbox_text.split(MARKER, 1)[0] if MARKER in inbox_text else inbox_text
    new_pending = ""
    if unresolved:
        new_pending = "\n" + "\n".join(unresolved) + "\n"
    footer = f"\n<!-- last applied {lib.now_iso()}: {applied} signal(s) -->\n"
    open(inbox_path, "w", encoding="utf-8").write(header + MARKER + "\n" + new_pending + footer)

    log(f"Applied {applied} signal(s); {len(unresolved)} kept as unresolved.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
