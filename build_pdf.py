"""Render PRD.md to PRD.pdf using reportlab."""
import re
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    Preformatted, PageBreak, KeepTogether,
)
from reportlab.lib.enums import TA_LEFT

SRC = "PRD.md"
OUT = "PRD.pdf"

styles = getSampleStyleSheet()
body = ParagraphStyle("body", parent=styles["BodyText"], fontSize=10, leading=14, spaceAfter=6)
h1 = ParagraphStyle("h1", parent=styles["Heading1"], fontSize=20, leading=24, spaceBefore=12, spaceAfter=10, textColor=colors.HexColor("#111827"))
h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontSize=14, leading=18, spaceBefore=14, spaceAfter=8, textColor=colors.HexColor("#1f2937"))
h3 = ParagraphStyle("h3", parent=styles["Heading3"], fontSize=11, leading=15, spaceBefore=10, spaceAfter=6, textColor=colors.HexColor("#374151"))
code_style = ParagraphStyle("code", parent=styles["Code"], fontSize=8, leading=11, leftIndent=10, backColor=colors.HexColor("#f3f4f6"))
bullet = ParagraphStyle("bullet", parent=body, leftIndent=18, bulletIndent=6)


def inline_md(text: str) -> str:
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r"`([^`]+)`", r'<font face="Courier" size="9">\1</font>', text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<i>\1</i>", text)
    return text


def parse_table(lines):
    rows = []
    for ln in lines:
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        rows.append(cells)
    if len(rows) >= 2 and re.match(r"^[-:\s|]+$", "|".join(rows[1])):
        rows.pop(1)
    return rows


def build_table(rows):
    data = [[Paragraph(inline_md(c), body) for c in row] for row in rows]
    ncols = max(len(r) for r in rows)
    col_w = (LETTER[0] - 1.5 * inch) / ncols
    t = Table(data, colWidths=[col_w] * ncols, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e5e7eb")),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#9ca3af")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


def render():
    with open(SRC) as f:
        src = f.read()

    story = []
    lines = src.splitlines()
    i = 0
    while i < len(lines):
        ln = lines[i]

        if ln.startswith("```"):
            j = i + 1
            buf = []
            while j < len(lines) and not lines[j].startswith("```"):
                buf.append(lines[j])
                j += 1
            story.append(Preformatted("\n".join(buf), code_style))
            story.append(Spacer(1, 6))
            i = j + 1
            continue

        if ln.startswith("|") and i + 1 < len(lines) and re.match(r"^\|[-:\s|]+\|$", lines[i + 1]):
            j = i
            tbl_lines = []
            while j < len(lines) and lines[j].startswith("|"):
                tbl_lines.append(lines[j])
                j += 1
            rows = parse_table(tbl_lines)
            story.append(build_table(rows))
            story.append(Spacer(1, 8))
            i = j
            continue

        if ln.startswith("### "):
            story.append(Paragraph(inline_md(ln[4:]), h3))
        elif ln.startswith("## "):
            story.append(Paragraph(inline_md(ln[3:]), h2))
        elif ln.startswith("# "):
            story.append(Paragraph(inline_md(ln[2:]), h1))
        elif ln.strip() == "---":
            story.append(Spacer(1, 6))
        elif ln.startswith("- ") or ln.startswith("* "):
            story.append(Paragraph(inline_md(ln[2:]), bullet, bulletText="\u2022"))
        elif re.match(r"^\d+\.\s", ln):
            story.append(Paragraph(inline_md(re.sub(r"^\d+\.\s", "", ln)), bullet, bulletText=ln.split(".")[0] + "."))
        elif ln.startswith("> "):
            quote = ParagraphStyle("quote", parent=body, leftIndent=20, textColor=colors.HexColor("#4b5563"), fontName="Helvetica-Oblique")
            story.append(Paragraph(inline_md(ln[2:]), quote))
        elif ln.strip() == "":
            story.append(Spacer(1, 4))
        else:
            story.append(Paragraph(inline_md(ln), body))
        i += 1

    doc = SimpleDocTemplate(
        OUT, pagesize=LETTER,
        leftMargin=0.75 * inch, rightMargin=0.75 * inch,
        topMargin=0.75 * inch, bottomMargin=0.75 * inch,
        title="PRD: Airbnb Price Quote Agent",
    )
    doc.build(story)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    render()
