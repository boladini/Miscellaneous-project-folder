#!/usr/bin/env python3
"""Network-free smoke test for the Curiosity Pipeline plumbing.

Validates feed parsing, text helpers, PDF rendering, and feedback application using
local fixtures only (no egress), so it is deterministic and CI-safe.

Run: python scripts/tests/test_smoke.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))
FIXTURES = Path(__file__).resolve().parent / "fixtures"

import lib  # noqa: E402
import fetch_feeds as ff  # noqa: E402
import save_article_pdf as sap  # noqa: E402

passed = 0
failed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}  {detail}")


def test_helpers():
    print("helpers:")
    check("slugify basic", lib.slugify("The Ladder of Emergence!") == "the-ladder-of-emergence")
    check("slugify unicode", lib.slugify("Café — Déjà Vu") == "cafe-deja-vu", lib.slugify("Café — Déjà Vu"))
    check("word_count", lib.word_count("one two three four") == 4)


def test_feed_parsing():
    print("feed parsing:")
    rss = (FIXTURES / "sample_rss.xml").read_bytes()
    atom = (FIXTURES / "sample_atom.xml").read_bytes()
    r = ff.parse_feed(rss)
    a = ff.parse_feed(atom)
    check("rss item count", len(r) == 2, str(len(r)))
    check("rss title", r[0]["title"] == "The Ladder of Emergence", r[0]["title"])
    check("rss link", r[0]["url"] == "https://example.com/ladder-of-emergence", r[0]["url"])
    check("rss author (dc:creator)", r[0]["author"] == "A. Sample Author", str(r[0]["author"]))
    check("rss date parsed", r[0]["published"] is not None, str(r[0]["published"]))
    check("rss summary stripped of html", "<p>" not in (r[0]["summary"] or ""))
    check("atom link via href", a[0]["url"] == "https://atom.example.com/feedback-loops", a[0]["url"])
    check("atom author via name", a[0]["author"] == "B. Atom Writer", str(a[0]["author"]))


def test_pdf_rendering():
    print("pdf rendering:")
    html = (FIXTURES / "sample_article.html").read_text(encoding="utf-8")
    body, meta = sap.extract("https://example.com/ladder-of-emergence", html)
    wc = lib.word_count(body or "")
    check("extracted body present", bool(body), "no body")
    check("word count > 800", wc > 800, f"wc={wc}")
    check("title from metadata", bool((meta or {}).get("title")), str(meta.get("title") if meta else None))
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "sample.pdf"
        sap.build_pdf(out, meta.get("title") or "Untitled",
                      "A. Sample Author  ·  Fixture Review  ·  2026-05-28",
                      body, "https://example.com/ladder-of-emergence", ok=True)
        size = out.stat().st_size if out.exists() else 0
        check("pdf written", out.exists(), "missing")
        check("pdf non-trivial size", size > 5000, f"{size} bytes")
        check("pdf magic header", out.read_bytes()[:4] == b"%PDF", "bad header")


def test_feedback():
    print("feedback application:")
    import apply_feedback as af
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        # Minimal sources.json + feedback.json + a digest scores.json + inbox.
        sources = {"version": 1, "sources": [
            {"id": "intrinsic-perspective", "name": "The Intrinsic Perspective", "weight": 1.0},
            {"id": "quanta", "name": "Quanta Magazine", "weight": 1.0},
        ]}
        (td / "pipeline").mkdir()
        (td / "digests" / "2026-06-01").mkdir(parents=True)
        lib.save_json(td / "pipeline" / "sources.json", sources)
        lib.save_json(td / "pipeline" / "feedback.json", {"version": 1, "processed": []})
        lib.save_json(td / "digests" / "2026-06-01" / "scores.json", {
            "date": "2026-06-01",
            "articles": [
                {"id": "ladder-of-emergence", "title": "The Ladder of Emergence",
                 "url": "https://example.com/ladder-of-emergence", "source_id": "quanta",
                 "author": "A. Sample Author", "domains": ["physics-systems"],
                 "top_trigger": "reframed_idea"},
            ],
        })
        inbox = td / "feedback" / "inbox.md"
        inbox.parent.mkdir()
        inbox.write_text(af.MARKER + "\nSTAR 2026-06-01/ladder-of-emergence\nSKIP https://nope.example.com/x\n")
        # Point lib paths at the temp tree.
        lib.SOURCES_JSON = td / "pipeline" / "sources.json"
        lib.FEEDBACK_JSON = td / "pipeline" / "feedback.json"
        lib.DIGESTS = td / "digests"
        lib.FEEDBACK_INBOX = inbox
        sys.argv = ["apply_feedback.py", "--inbox", str(inbox)]
        af.main()
        new_sources = lib.load_json(lib.SOURCES_JSON)
        wq = {s["id"]: s["weight"] for s in new_sources["sources"]}
        check("STAR raised quanta weight", abs(wq["quanta"] - 1.2) < 1e-6, str(wq["quanta"]))
        fb = lib.load_json(lib.FEEDBACK_JSON)
        check("feedback logged", len(fb["processed"]) == 1, str(len(fb["processed"])))
        remaining = inbox.read_text()
        check("unresolved SKIP kept", "nope.example.com" in remaining, "lost unresolved line")
        check("resolved STAR cleared", "ladder-of-emergence" not in remaining.split(af.MARKER, 1)[1], "not cleared")


def main() -> int:
    test_helpers()
    test_feed_parsing()
    test_pdf_rendering()
    test_feedback()
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
