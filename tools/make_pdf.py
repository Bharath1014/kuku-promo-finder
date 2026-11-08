"""Render SOLUTION.md to a compact PDF (needs: pip install reportlab).

  python tools/make_pdf.py SOLUTION.md Solution.pdf

Supports the small Markdown subset the doc uses: # / ## headings, paragraphs,
- bullets, 1. numbered lists, ``` blocks, **bold**, *italic*, `code`.
Lines starting with *DRAFT are skipped.
"""
import os
import re
import sys
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import ListFlowable, ListItem, Paragraph, Preformatted, SimpleDocTemplate, Spacer

FONT_DIR = os.path.join(os.environ.get("WINDIR", "C:/Windows"), "Fonts")


def register_fonts():
    try:
        pdfmetrics.registerFont(TTFont("Body", os.path.join(FONT_DIR, "arial.ttf")))
        pdfmetrics.registerFont(TTFont("Body-Bold", os.path.join(FONT_DIR, "arialbd.ttf")))
        pdfmetrics.registerFont(TTFont("Body-Italic", os.path.join(FONT_DIR, "ariali.ttf")))
        pdfmetrics.registerFont(TTFont("Body-BoldItalic", os.path.join(FONT_DIR, "arialbi.ttf")))
        pdfmetrics.registerFont(TTFont("Mono", os.path.join(FONT_DIR, "consola.ttf")))
        from reportlab.lib.fonts import addMapping
        addMapping("Body", 0, 0, "Body"); addMapping("Body", 1, 0, "Body-Bold")
        addMapping("Body", 0, 1, "Body-Italic"); addMapping("Body", 1, 1, "Body-BoldItalic")
        return "Body", "Mono"
    except Exception:
        return "Helvetica", "Courier"


def inline(text: str, mono: str) -> str:
    parts = re.split(r"(`[^`]+`)", text)
    out = []
    for p in parts:
        if p.startswith("`") and p.endswith("`"):
            out.append(f'<font face="{mono}" size="8.3">{escape(p[1:-1])}</font>')
        else:
            s = escape(p)
            s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
            s = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", s)
            out.append(s)
    return "".join(out)


def build(md_path: str, pdf_path: str, base: float = 10.2):
    body, mono = register_fonts()
    lead = base * 1.28
    st = {
        "h1": ParagraphStyle("h1", fontName=body, fontSize=base + 6, leading=base + 9, spaceAfter=4,
                             textColor=colors.HexColor("#1a1a1a"), alignment=TA_LEFT),
        "h2": ParagraphStyle("h2", fontName=body, fontSize=base + 2, leading=base + 4, spaceBefore=6,
                             spaceAfter=2, textColor=colors.HexColor("#0b4f6c")),
        "p": ParagraphStyle("p", fontName=body, fontSize=base, leading=lead, spaceAfter=3),
        "li": ParagraphStyle("li", fontName=body, fontSize=base, leading=lead),
        "pre": ParagraphStyle("pre", fontName=mono, fontSize=base - 1.6, leading=base + 1,
                              backColor=colors.HexColor("#f2f4f5"), borderPadding=4, spaceBefore=2, spaceAfter=6),
    }
    for k in ("h1", "h2"):
        st[k].fontName = body + "-Bold" if body == "Body" else "Helvetica-Bold"

    lines = open(md_path, encoding="utf-8").read().splitlines()
    story, i = [], 0

    def item_flow(t):
        # an item is a string, or [text, [sub-bullets]]
        if isinstance(t, list):
            text, subs = t
            sub = ListFlowable([ListItem(Paragraph(inline(s, mono), st["li"]), leftIndent=10) for s in subs],
                               bulletType="bullet", bulletFontName=body, bulletFontSize=base - 2,
                               leftIndent=10, spaceBefore=1)
            return ListItem([Paragraph(inline(text, mono), st["li"]), sub], leftIndent=12)
        return ListItem(Paragraph(inline(t, mono), st["li"]), leftIndent=12)

    def flush_list(items, ordered):
        if not items:
            return
        flow = [item_flow(t) for t in items]
        story.append(ListFlowable(flow, bulletType="1" if ordered else "bullet", start=1 if ordered else None,
                                  bulletFontName=body, bulletFontSize=base - 1 if not ordered else base,
                                  leftIndent=12, spaceBefore=0, spaceAfter=3))

    para, items, ordered = [], [], False
    while i < len(lines):
        ln = lines[i].rstrip()
        if ln.startswith("*DRAFT"):
            i += 1
            continue
        if ln.startswith("```"):
            if para: story.append(Paragraph(inline(" ".join(para), mono), st["p"])); para = []
            flush_list(items, ordered); items = []
            j = i + 1
            block = []
            while j < len(lines) and not lines[j].startswith("```"):
                block.append(lines[j]); j += 1
            story.append(Preformatted("\n".join(block), st["pre"]))
            i = j + 1
            continue
        m_b = re.match(r"^\s*-\s+(.*)", ln)
        m_n = re.match(r"^\s*\d+\.\s+(.*)", ln)
        if ln.startswith("#"):
            if para: story.append(Paragraph(inline(" ".join(para), mono), st["p"])); para = []
            flush_list(items, ordered); items = []
            level = len(ln) - len(ln.lstrip("#"))
            story.append(Paragraph(inline(ln.lstrip("# ").strip(), mono), st["h1" if level == 1 else "h2"]))
        elif m_b and items and ordered and ln.startswith(("  ", "\t")):
            # indented bullet under a numbered step: nest it
            last = items[-1]
            if not isinstance(last, list):
                items[-1] = last = [last, []]
            last[1].append(m_b.group(1))
        elif m_b or m_n:
            if para: story.append(Paragraph(inline(" ".join(para), mono), st["p"])); para = []
            now_ordered = bool(m_n)
            if items and now_ordered != ordered:
                flush_list(items, ordered); items = []
            ordered = now_ordered
            items.append((m_n or m_b).group(1))
        elif not ln.strip():
            if para: story.append(Paragraph(inline(" ".join(para), mono), st["p"])); para = []
            flush_list(items, ordered); items = []
        else:
            if items and lines[i].startswith(("  ", "\t")):
                if isinstance(items[-1], list):
                    items[-1][1][-1] += " " + ln.strip()
                else:
                    items[-1] += " " + ln.strip()   # continuation of a list item
            else:
                flush_list(items, ordered); items = []
                para.append(ln.strip())
        i += 1
    if para: story.append(Paragraph(inline(" ".join(para), mono), st["p"]))
    flush_list(items, ordered)

    doc = SimpleDocTemplate(pdf_path, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
                            topMargin=12 * mm, bottomMargin=12 * mm,
                            title="Kuku AI Generalist - Problem 1: Find the Promos")
    doc.build(story)


if __name__ == "__main__":
    md, pdf = sys.argv[1], sys.argv[2]
    base = float(sys.argv[3]) if len(sys.argv) > 3 else 10.2
    build(md, pdf, base)
    from pypdf import PdfReader
    print(f"{pdf}: {len(PdfReader(pdf).pages)} pages")
