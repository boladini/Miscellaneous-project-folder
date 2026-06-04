#!/usr/bin/env python3
"""Fetch an article, extract its full text, and render a clean, readable PDF.

Extraction via trafilatura (readability-style main-content extraction); typesetting via
fpdf2 with DejaVu fonts (full Unicode). One PDF per article, distraction-free, so the
reader reads the original offline. Prints a JSON result to stdout.

Usage:
    python scripts/save_article_pdf.py --url URL --out digests/2026-06-01/slug.pdf \\
        [--title T] [--author A] [--source S]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

import lib

FONT_DIRS = [
    os.environ.get("CURIOSITY_FONT_DIR", ""),
    "/usr/share/fonts/truetype/dejavu",
    "/usr/share/fonts/dejavu",
    "/Library/Fonts",
]


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def find_font(*names: str) -> str | None:
    for d in FONT_DIRS:
        if not d:
            continue
        for n in names:
            p = Path(d) / n
            if p.exists():
                return str(p)
    return None


def soft_break(text: str, n: int = 45) -> str:
    """Insert zero-width spaces into very long tokens so fpdf2 can wrap them."""
    return re.sub(rf"(\S{{{n}}})(?=\S)", "\\1​", text)


def clean_inline(line: str) -> str:
    line = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", line)          # images
    line = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", line)      # links -> text
    line = re.sub(r"\*\*(.+?)\*\*", r"\1", line)              # bold
    line = re.sub(r"`(.+?)`", r"\1", line)                    # inline code
    line = re.sub(r"\*(.+?)\*", r"\1", line)                  # italic *
    line = re.sub(r"(?<!\w)_(.+?)_(?!\w)", r"\1", line)       # italic _
    return line.strip()


def extract(url: str, html: str | None):
    """Return (markdown_body, metadata_dict). Lazy-imports trafilatura."""
    import trafilatura

    downloaded = html
    if downloaded is None:
        downloaded = trafilatura.fetch_url(url)
    if downloaded is None:
        resp = lib.http_get(url)
        downloaded = resp.text if resp is not None else None
    if downloaded is None:
        return None, {}

    body = trafilatura.extract(
        downloaded,
        output_format="markdown",
        include_comments=False,
        include_tables=True,
        favor_precision=True,
        url=url,
    )
    meta = {}
    try:
        md = trafilatura.extract_metadata(downloaded, default_url=url)
        if md is not None:
            meta = md.as_dict() if hasattr(md, "as_dict") else dict(md.__dict__)
    except Exception:
        meta = {}
    return body, meta


def build_pdf(out_path, title, byline, body_md, source_url, ok):
    from fpdf import FPDF

    serif = find_font("DejaVuSerif.ttf")
    serif_bold = find_font("DejaVuSerif-Bold.ttf")
    sans = find_font("DejaVuSans.ttf")
    sans_bold = find_font("DejaVuSans-Bold.ttf")
    body_font, head_font = "Body", "Head"
    body_file = serif or sans
    bold_file = serif_bold or sans_bold or body_file
    head_file = sans_bold or serif_bold or bold_file
    if not body_file:
        raise SystemExit("No usable TTF font found (set CURIOSITY_FONT_DIR).")

    class ArticlePDF(FPDF):
        def footer(self):
            self.set_y(-14)
            self.set_font(body_font, "", 8)
            self.set_text_color(140)
            self.cell(0, 8, f"Curiosity Pipeline  ·  page {self.page_no()}", align="C")
            self.set_text_color(0)

    pdf = ArticlePDF(format="Letter", unit="mm")
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.set_margins(20, 18, 20)
    pdf.add_font(body_font, "", body_file)
    pdf.add_font(body_font, "B", bold_file)
    pdf.add_font(head_font, "B", head_file)
    pdf.add_page()

    epw = pdf.epw  # effective page width

    def para(text, font=body_font, style="", size=11.5, h=6.4, gap=2.4, align="J", color=0):
        if not text:
            return
        pdf.set_font(font, style, size)
        pdf.set_text_color(color)
        pdf.multi_cell(epw, h, soft_break(text), align=align, new_x="LMARGIN", new_y="NEXT")
        pdf.ln(gap)
        pdf.set_text_color(0)

    # Title block
    para(title, font=head_font, style="B", size=20, h=9, gap=1.5, align="L")
    if byline:
        para(byline, font=body_font, style="", size=10.5, h=6, gap=1.0, align="L", color=110)
    y = pdf.get_y() + 1
    pdf.set_draw_color(200)
    pdf.line(pdf.l_margin, y, pdf.l_margin + epw, y)
    pdf.ln(5)

    if not ok or not body_md:
        para(
            "The full text could not be extracted automatically. Read the original at "
            "the link below.",
            style="", size=11.5,
        )
        para(source_url, font=body_font, size=10.5, color=80, align="L")
        pdf.output(str(out_path))
        return

    list_re = re.compile(r"^\s*([-*+]|\d+\.)\s+(.*)$")
    hr_re = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
    for raw in body_md.split("\n"):
        line = raw.rstrip()
        if not line.strip():
            continue
        if hr_re.match(line):
            yy = pdf.get_y() + 1
            pdf.set_draw_color(220)
            pdf.line(pdf.l_margin + 30, yy, pdf.l_margin + epw - 30, yy)
            pdf.ln(5)
            continue
        if line.lstrip().startswith("#"):
            level = len(line) - len(line.lstrip("#"))
            text = clean_inline(line.lstrip("#").strip())
            size = {1: 16, 2: 14, 3: 12.5}.get(level, 12)
            pdf.ln(1.5)
            para(text, font=head_font, style="B", size=size, h=size * 0.5, gap=2.0, align="L")
            continue
        if line.lstrip().startswith(">"):
            text = clean_inline(re.sub(r"^\s*>\s?", "", line))
            pdf.set_x(pdf.l_margin + 6)
            pdf.set_font(body_font, "", 11)
            pdf.set_text_color(90)
            pdf.multi_cell(epw - 6, 6.2, soft_break(text), align="L", new_x="LMARGIN", new_y="NEXT")
            pdf.set_text_color(0)
            pdf.ln(2.2)
            continue
        m = list_re.match(line)
        if m:
            text = clean_inline(m.group(2))
            pdf.set_x(pdf.l_margin + 5)
            pdf.set_font(body_font, "", 11.5)
            pdf.multi_cell(epw - 5, 6.2, soft_break("•  " + text), align="L", new_x="LMARGIN", new_y="NEXT")
            pdf.ln(1.4)
            continue
        para(clean_inline(line))

    pdf.output(str(out_path))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--title", default="")
    ap.add_argument("--author", default="")
    ap.add_argument("--source", default="")
    ap.add_argument("--min-words", type=int, default=1200)
    ap.add_argument("--html-file", default="", help="optional local HTML instead of fetching")
    args = ap.parse_args()

    html = None
    if args.html_file:
        html = Path(args.html_file).read_text(encoding="utf-8", errors="ignore")

    body_md, meta = extract(args.url, html)
    title = args.title or (meta.get("title") if meta else "") or "Untitled"
    author = args.author or (meta.get("author") if meta else "") or ""
    source = args.source or (meta.get("sitename") if meta else "") or ""
    date = (meta.get("date") if meta else "") or ""
    wc = lib.word_count(body_md or "")
    ok = bool(body_md) and wc >= 50

    byline = "  ·  ".join([p for p in [author, source, date] if p])
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    build_pdf(out_path, title, byline, body_md, args.url, ok)

    if ok and wc < args.min_words:
        log(f"WARNING: extracted only {wc} words (< {args.min_words}); verify it isn't truncated/paywalled.")

    result = {
        "ok": ok,
        "path": str(out_path),
        "title": title,
        "author": author,
        "source": source,
        "word_count": wc,
        "url": args.url,
    }
    print(json.dumps(result, ensure_ascii=False))
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
