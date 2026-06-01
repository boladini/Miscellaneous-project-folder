"""Shared helpers for the Curiosity Pipeline scripts."""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PIPELINE = ROOT / "pipeline"
DIGESTS = ROOT / "digests"
WORK = ROOT / "work"
FEEDBACK_INBOX = ROOT / "feedback" / "inbox.md"

SOURCES_JSON = PIPELINE / "sources.json"
FEEDBACK_JSON = PIPELINE / "feedback.json"

USER_AGENT = (
    "Mozilla/5.0 (compatible; CuriosityPipeline/1.0; +personal-reading-digest)"
)


def today_str() -> str:
    return date.today().isoformat()


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_json(path: Path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON in {path}: {exc}")


def save_json(path: Path, data) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


_slug_strip = re.compile(r"[^a-z0-9]+")


def slugify(text: str, max_len: int = 60) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    text = _slug_strip.sub("-", text.lower()).strip("-")
    if len(text) > max_len:
        text = text[:max_len].rsplit("-", 1)[0] or text[:max_len]
    return text or "untitled"


def word_count(text: str) -> int:
    return len(re.findall(r"\b\w[\w'-]*\b", text or ""))


def http_get(url: str, timeout: int = 25):
    """Lazy import requests so lib stays import-cheap; returns Response or None."""
    import requests

    try:
        resp = requests.get(
            url, headers={"User-Agent": USER_AGENT}, timeout=timeout, allow_redirects=True
        )
        resp.raise_for_status()
        return resp
    except Exception:
        return None
