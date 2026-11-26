"""Research retrieval providers. Public/licensed sources only; no paywall or auth bypass.

All fetched text is untrusted data: it is sanitised (HTML stripped, length-capped) and never
interpreted as instructions. Each record keeps provider + retrieval id for provenance.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from ..config import get_settings
from .http import ProviderError, get

MAX_ABSTRACT = 4000
TAG_RE = re.compile(r"<[^>]+>")
CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def sanitize_text(s: str | None, limit: int = MAX_ABSTRACT) -> str:
    if not s:
        return ""
    s = TAG_RE.sub(" ", s)
    s = html.unescape(s)
    s = CTRL_RE.sub("", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:limit]


@dataclass
class RetrievedDoc:
    url: str
    title: str
    source_type: str  # peer_reviewed|preprint|filing|news|social|synthetic|other
    provider: str
    retrieval_id: str
    published_at: date | None = None
    authors: str | None = None
    publisher: str | None = None
    abstract: str = ""
    cited_by_count: int | None = None
    synthetic: bool = False
    query: str = ""
    extra: dict = field(default_factory=dict)


class ResearchProvider:
    name = "base"
    channel = "literature"

    def available(self) -> tuple[bool, str]:
        return True, "ok"

    def search(self, query: str, since: date | None = None, limit: int = 8) -> list[RetrievedDoc]:
        raise NotImplementedError


def _openalex_abstract(inv: dict | None) -> str:
    if not inv:
        return ""
    pos: list[tuple[int, str]] = []
    for word, idxs in inv.items():
        for i in idxs:
            pos.append((i, word))
    return " ".join(w for _, w in sorted(pos))


class OpenAlexProvider(ResearchProvider):
    """OpenAlex works API (CC0 metadata; no key; polite pool via mailto)."""
    name = "openalex"
    channel = "literature"

    def search(self, query, since=None, limit=8):
        params = {"search": query, "per-page": min(limit, 25),
                  "select": "id,doi,title,publication_date,authorships,primary_location,cited_by_count,"
                            "abstract_inverted_index,type"}
        filters = ["has_abstract:true"]
        if since:
            filters.append(f"from_publication_date:{since.isoformat()}")
        params["filter"] = ",".join(filters)
        mailto = get_settings().openalex_mailto
        if mailto:
            params["mailto"] = mailto
        data = get("https://api.openalex.org/works", params=params).json()
        out = []
        for w in data.get("results", []):
            title = sanitize_text(w.get("title"), 500)
            if not title:
                continue
            loc = w.get("primary_location") or {}
            src = loc.get("source") or {}
            venue = src.get("display_name")
            stype = "preprint" if (w.get("type") == "preprint" or (src.get("type") == "repository")) else "peer_reviewed"
            url = w.get("doi") or loc.get("landing_page_url") or w["id"]
            pub = w.get("publication_date")
            authors = ", ".join(a.get("author", {}).get("display_name", "") for a in (w.get("authorships") or [])[:6])
            out.append(RetrievedDoc(
                url=url, title=title, source_type=stype, provider=self.name, retrieval_id=w["id"],
                published_at=date.fromisoformat(pub) if pub else None, authors=sanitize_text(authors, 400),
                publisher=sanitize_text(venue, 300) if venue else None,
                abstract=sanitize_text(_openalex_abstract(w.get("abstract_inverted_index"))),
                cited_by_count=w.get("cited_by_count"), query=query))
        return out


class CrossrefProvider(ResearchProvider):
    name = "crossref"
    channel = "literature"

    def search(self, query, since=None, limit=8):
        params = {"query": query, "rows": min(limit, 20), "select": "DOI,title,author,published,container-title,abstract,URL,type,is-referenced-by-count"}
        if since:
            params["filter"] = f"from-pub-date:{since.isoformat()}"
        mailto = get_settings().openalex_mailto
        if mailto:
            params["mailto"] = mailto
        data = get("https://api.crossref.org/works", params=params).json()
        out = []
        for it in data.get("message", {}).get("items", []):
            title = sanitize_text((it.get("title") or [""])[0], 500)
            if not title or not it.get("DOI"):
                continue
            parts = (it.get("published") or {}).get("date-parts", [[None]])[0]
            pub = None
            if parts and parts[0]:
                pub = date(parts[0], (parts[1] if len(parts) > 1 else 1), (parts[2] if len(parts) > 2 else 1))
            authors = ", ".join(f"{a.get('given', '')} {a.get('family', '')}".strip() for a in (it.get("author") or [])[:6])
            out.append(RetrievedDoc(
                url=f"https://doi.org/{it['DOI']}", title=title,
                source_type="preprint" if it.get("type") == "posted-content" else "peer_reviewed",
                provider=self.name, retrieval_id=f"doi:{it['DOI']}", published_at=pub, authors=sanitize_text(authors, 400),
                publisher=sanitize_text((it.get("container-title") or [""])[0], 300) or None,
                abstract=sanitize_text(it.get("abstract")), cited_by_count=it.get("is-referenced-by-count"), query=query))
        return out


class SecEdgarProvider(ResearchProvider):
    """SEC EDGAR full-text search. SEC requires a descriptive User-Agent with contact info
    (set SEC_USER_AGENT); fair-access limit 10 req/s."""
    name = "sec_edgar"
    channel = "filings"

    def available(self):
        ua = get_settings().sec_user_agent
        return (bool(ua), "SEC_USER_AGENT not configured (SEC fair-access policy requires contact info)")

    def search(self, query, since=None, limit=8):
        params = {"q": f'"{query}"', "forms": "10-K,10-Q,8-K"}
        if since:
            params.update({"dateRange": "custom", "startdt": since.isoformat(), "enddt": date.today().isoformat()})
        data = get("https://efts.sec.gov/LATEST/search-index", params=params,
                   headers={"User-Agent": get_settings().sec_user_agent}).json()
        out = []
        for h in data.get("hits", {}).get("hits", [])[:limit]:
            s = h.get("_source", {})
            adsh = s.get("adsh", "")
            ciks = s.get("ciks") or [""]
            cik = str(ciks[0]).lstrip("0")
            fid = h.get("_id", "")
            fname = fid.split(":", 1)[1] if ":" in fid else ""
            url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{adsh.replace('-', '')}/{fname}" if cik and adsh else ""
            if not url:
                continue
            fd = s.get("file_date")
            out.append(RetrievedDoc(
                url=url, title=sanitize_text(f"{(s.get('display_names') or [''])[0]} {s.get('form', '')} filed {fd}", 400),
                source_type="filing", provider=self.name, retrieval_id=f"edgar:{fid}",
                published_at=date.fromisoformat(fd) if fd else None,
                publisher="SEC EDGAR", abstract="", query=query, extra={"form": s.get("form")}))
        return out


class RedditProvider(ResearchProvider):
    """Reddit via official OAuth API (application-only). Social claims are leads, never evidence of profitability."""
    name = "reddit"
    channel = "social"

    def available(self):
        s = get_settings()
        return (bool(s.reddit_client_id and s.reddit_client_secret), "REDDIT_CLIENT_ID/SECRET not configured")

    def search(self, query, since=None, limit=8):
        import httpx  # token exchange uses POST

        s = get_settings()
        with httpx.Client(timeout=s.http_timeout_seconds) as c:
            tok = c.post("https://www.reddit.com/api/v1/access_token", data={"grant_type": "client_credentials"},
                         auth=(s.reddit_client_id, s.reddit_client_secret),
                         headers={"User-Agent": "investment-lab/1.0"}).json().get("access_token")
        if not tok:
            raise ProviderError("reddit auth failed", retryable=False)
        data = get("https://oauth.reddit.com/search", params={"q": query, "limit": limit, "sort": "relevance", "t": "year"},
                   headers={"Authorization": f"Bearer {tok}", "User-Agent": "investment-lab/1.0"}).json()
        out = []
        for ch in data.get("data", {}).get("children", []):
            d = ch.get("data", {})
            out.append(RetrievedDoc(
                url=f"https://www.reddit.com{d.get('permalink', '')}", title=sanitize_text(d.get("title"), 300),
                source_type="social", provider=self.name, retrieval_id=f"reddit:{d.get('name')}",
                published_at=datetime.fromtimestamp(d.get("created_utc", 0), timezone.utc).date(),
                authors=d.get("author"), publisher=f"r/{d.get('subreddit')}", abstract=sanitize_text(d.get("selftext"), 1500),
                query=query))
        return out


class XProvider(ResearchProvider):
    name = "x"
    channel = "social"

    def available(self):
        return (bool(get_settings().x_bearer_token), "X_BEARER_TOKEN not configured (paid API tier required)")

    def search(self, query, since=None, limit=8):
        data = get("https://api.x.com/2/tweets/search/recent", params={"query": f"{query} -is:retweet lang:en",
                                                                        "max_results": max(10, min(limit, 100)),
                                                                        "tweet.fields": "created_at,author_id"},
                   headers={"Authorization": f"Bearer {get_settings().x_bearer_token}"}).json()
        out = []
        for t in data.get("data", []):
            out.append(RetrievedDoc(url=f"https://x.com/i/web/status/{t['id']}", title=sanitize_text(t.get("text"), 280),
                                    source_type="social", provider=self.name, retrieval_id=f"x:{t['id']}",
                                    published_at=date.fromisoformat(t["created_at"][:10]) if t.get("created_at") else None,
                                    abstract=sanitize_text(t.get("text"), 600), query=query))
        return out


class DemoResearchProvider(ResearchProvider):
    """Explicitly synthetic sources for demo mode. URLs use the reserved .invalid TLD and every record
    is flagged synthetic so it can never be mistaken for real research."""
    name = "demo-synthetic"
    channel = "literature"

    LIBRARY = {
        "reversal": ("Synthetic study: short-horizon losers in the demo universe rebound", "supporting",
                     "In the synthetic demo dataset, stocks with large 5-day declines show partial reversal over the "
                     "following weeks; the effect weakens after accounting for trading costs."),
        "reversal_against": ("Synthetic note: reversal profits vanish after costs in illiquid names", "disconfirming",
                             "In this synthetic note, reversal profits concentrate in illiquid names and fall sharply "
                             "after transaction costs; results are sensitive to the cost assumption."),
        "momentum": ("Synthetic study: intermediate-horizon trend persistence", "supporting",
                     "Synthetic series with positive autocorrelation display trend persistence measurable with moving-"
                     "average filters; drawdowns cluster in stress regimes."),
        "momentum_against": ("Synthetic note: momentum crashes during rebounds", "disconfirming",
                             "Synthetic trend strategies suffer sharp losses during market rebounds following stress."),
        "pead": ("Synthetic study: drift after earnings surprises", "supporting",
                 "In synthetic data, prices continue to drift in the direction of the earnings surprise for "
                 "several weeks after the announcement."),
        "pead_against": ("Synthetic note: post-earnings drift has declined over time", "disconfirming",
                         "This synthetic note reports the drift is smaller in recent periods and concentrated in "
                         "smaller firms."),
        "lowvol": ("Synthetic study: low-volatility names deliver similar returns with smaller drawdowns", "supporting",
                   "Synthetic low-volatility portfolios show comparable returns with lower drawdowns."),
        "asymmetric": ("Synthetic study: deep drawdowns with stabilising trend offer skewed payoffs", "supporting",
                       "Synthetic names far below their 1-year high that regain their 50-day average show right-skewed "
                       "subsequent returns, with frequent small losses."),
    }

    def search(self, query, since=None, limit=8):
        q = query.lower()
        keys = [k for k in self.LIBRARY if k.split("_")[0] in q.replace("-", "").replace(" ", "")
                or any(t in q for t in DEMO_QUERY_HINTS.get(k, ()))]
        out = []
        for k in keys[:limit]:
            title, stance, abstract = self.LIBRARY[k]
            out.append(RetrievedDoc(
                url=f"https://example.invalid/synthetic/{k}", title=f"[SYNTHETIC] {title}", source_type="synthetic",
                provider=self.name, retrieval_id=f"synthetic:{k}", published_at=date(2024, 1, 15),
                authors="Demo Generator", publisher="Synthetic Demo Library (not real research)",
                abstract=abstract, synthetic=True, query=query, extra={"stance_hint": stance}))
        return out


DEMO_QUERY_HINTS = {
    "reversal": ("reversal", "overreaction"),
    "reversal_against": ("reversal", "transaction cost"),
    "momentum": ("momentum", "trend"),
    "momentum_against": ("momentum crash", "momentum"),
    "pead": ("post-earnings", "earnings announcement drift"),
    "pead_against": ("post-earnings", "drift decline"),
    "lowvol": ("low volatility", "low-volatility"),
    "asymmetric": ("52-week high", "skew", "asymmetric"),
}


def get_research_providers() -> list[ResearchProvider]:
    if get_settings().is_demo:
        return [DemoResearchProvider()]
    return [OpenAlexProvider(), CrossrefProvider(), SecEdgarProvider(), RedditProvider(), XProvider()]
