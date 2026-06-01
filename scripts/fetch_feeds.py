#!/usr/bin/env python3
"""Pull recent items from the subscribed feeds in sources.json into a candidate pool.

Pure stdlib + requests (no feedparser, so it installs cleanly anywhere). Tolerant by
design: a broken feed never aborts the run, and a wrong feed_url is auto-rediscovered
from the source's homepage.

Usage:
    python scripts/fetch_feeds.py --days 4 --out work/candidates.json
"""
from __future__ import annotations

import argparse
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import lib

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")
FEED_LINK_RE = re.compile(
    r'<link[^>]+(?:type=["\']application/(?:rss\+xml|atom\+xml)["\'][^>]*'
    r'href=["\']([^"\']+)["\']|href=["\']([^"\']+)["\'][^>]*'
    r'type=["\']application/(?:rss\+xml|atom\+xml)["\'])',
    re.I,
)
COMMON_SUFFIXES = ["/feed", "/feed/", "/rss", "/rss/", "/feed.xml", "/atom.xml", "/index.xml"]


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def clean_text(raw: str | None, limit: int = 500) -> str:
    if not raw:
        return ""
    text = WS_RE.sub(" ", TAG_RE.sub(" ", raw)).strip()
    return text[:limit]


def parse_date(value: str | None):
    if not value:
        return None
    value = value.strip()
    try:
        return parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        pass
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _first_child_text(item, names) -> str | None:
    for child in item:
        if localname(child.tag) in names:
            if child.text and child.text.strip():
                return child.text.strip()
    return None


def _link_from_item(item) -> str | None:
    # Atom: <link rel="alternate" href="..."/> ; RSS: <link>text</link>
    alt = None
    for child in item:
        if localname(child.tag) != "link":
            continue
        href = child.attrib.get("href")
        if href:
            rel = (child.attrib.get("rel") or "alternate").lower()
            if rel == "alternate":
                return href
            alt = alt or href
        elif child.text and child.text.strip():
            return child.text.strip()
    return alt


def parse_feed(xml_bytes: bytes) -> list[dict]:
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []
    entries: list[dict] = []
    # RSS items live under channel/item; Atom entries directly under feed.
    items = [el for el in root.iter() if localname(el.tag) in ("item", "entry")]
    for item in items:
        title = _first_child_text(item, {"title"})
        url = _link_from_item(item)
        if not title or not url:
            continue
        author = _first_child_text(item, {"creator", "author", "dc:creator"})
        if not author:
            for child in item:
                if localname(child.tag) == "author":
                    author = _first_child_text(child, {"name"}) or author
        date_raw = _first_child_text(item, {"pubdate", "published", "updated", "date"})
        summary = _first_child_text(item, {"description", "summary", "content", "encoded"})
        entries.append(
            {
                "title": clean_text(title, 300),
                "url": url.strip(),
                "author": clean_text(author, 120) or None,
                "published": (parse_date(date_raw).isoformat() if parse_date(date_raw) else None),
                "_published_dt": parse_date(date_raw),
                "summary": clean_text(summary, 600),
            }
        )
    return entries


def discover_feed(site_url: str) -> str | None:
    resp = lib.http_get(site_url)
    if resp is not None:
        m = FEED_LINK_RE.search(resp.text)
        if m:
            href = m.group(1) or m.group(2)
            if href:
                if href.startswith("/"):
                    base = re.match(r"(https?://[^/]+)", site_url)
                    href = (base.group(1) if base else site_url.rstrip("/")) + href
                return href
    base = site_url.rstrip("/")
    for suffix in COMMON_SUFFIXES:
        candidate = base + suffix
        r = lib.http_get(candidate)
        if r is not None and parse_feed(r.content):
            return candidate
    return None


def fetch_source(source: dict, discover: bool) -> list[dict]:
    feed_url = source.get("feed_url")
    entries: list[dict] = []
    if feed_url:
        resp = lib.http_get(feed_url)
        if resp is not None:
            entries = parse_feed(resp.content)
    if not entries and discover and source.get("site_url"):
        found = discover_feed(source["site_url"])
        if found:
            log(f"  discovered feed for {source['id']}: {found}")
            source["feed_url"] = found  # surfaced in candidates meta, not persisted
            resp = lib.http_get(found)
            if resp is not None:
                entries = parse_feed(resp.content)
    return entries


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=4, help="lookback window in days")
    ap.add_argument("--out", default=str(lib.WORK / "candidates.json"))
    ap.add_argument("--limit-per-feed", type=int, default=8)
    ap.add_argument("--only", default="", help="comma-separated source ids to restrict to")
    ap.add_argument("--no-discover", action="store_true", help="disable feed auto-discovery")
    args = ap.parse_args()

    data = lib.load_json(lib.SOURCES_JSON)
    if not data:
        log(f"Could not read {lib.SOURCES_JSON}")
        return 1
    sources = data.get("sources", [])
    only = {s.strip() for s in args.only.split(",") if s.strip()}
    if only:
        sources = [s for s in sources if s["id"] in only]

    cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)
    seen: set[str] = set()
    candidates: list[dict] = []
    stats = {"feeds_ok": 0, "feeds_empty": 0, "kept": 0}

    for src in sources:
        if src.get("feed_url") is None and (args.no_discover or not src.get("site_url")):
            continue
        entries = fetch_source(src, discover=not args.no_discover)
        if not entries:
            stats["feeds_empty"] += 1
            log(f"[empty] {src['id']}")
            continue
        stats["feeds_ok"] += 1
        kept_here = 0
        for e in sorted(entries, key=lambda x: (x["_published_dt"] is not None, x["_published_dt"] or datetime.min.replace(tzinfo=timezone.utc)), reverse=True):
            if kept_here >= args.limit_per_feed:
                break
            dt = e["_published_dt"]
            if dt is not None:
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if dt < cutoff:
                    continue
            key = re.sub(r"[#?].*$", "", e["url"]).rstrip("/").lower()
            title_key = re.sub(r"[^a-z0-9]+", "", e["title"].lower())
            if key in seen or title_key in seen:
                continue
            seen.add(key)
            seen.add(title_key)
            candidates.append(
                {
                    "id": lib.slugify(e["title"]),
                    "title": e["title"],
                    "url": e["url"],
                    "author": e["author"] or src.get("author"),
                    "source_id": src["id"],
                    "source": src["name"],
                    "domain": src.get("domain"),
                    "priority": src.get("priority"),
                    "weight": src.get("weight", 1.0),
                    "subscribed": src.get("subscribed", True),
                    "published": e["published"],
                    "summary": e["summary"],
                }
            )
            kept_here += 1
            stats["kept"] += 1

    out = {
        "generated": lib.now_iso(),
        "lookback_days": args.days,
        "stats": stats,
        "candidates": candidates,
    }
    lib.save_json(args.out, out)
    log(
        f"Wrote {stats['kept']} candidates from {stats['feeds_ok']} feeds "
        f"({stats['feeds_empty']} empty/unreachable) -> {args.out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
