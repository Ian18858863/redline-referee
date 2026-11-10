"""Render a synthetic agreement to a text-layer PDF (reportlab)."""
from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .contracts import CUSTOMER, INJECTION_TEXT, PREAMBLE, PROVIDER, TITLE

_TITLE = ParagraphStyle("title", fontName="Times-Bold", fontSize=15, leading=19, alignment=TA_CENTER, spaceAfter=14)
_BODY = ParagraphStyle("body", fontName="Times-Roman", fontSize=10.8, leading=15.2, spaceAfter=7)
_HEAD = ParagraphStyle("head", fontName="Times-Bold", fontSize=10.8, leading=15.2, spaceBefore=6, spaceAfter=2)
_HIDDEN = ParagraphStyle("hidden", fontName="Helvetica", fontSize=1.2, leading=1.6, textColor=colors.white)
_SIGN = ParagraphStyle("sign", fontName="Times-Roman", fontSize=10.5, leading=16)


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Times-Roman", 8.5)
    canvas.setFillColor(colors.HexColor("#666666"))
    canvas.drawString(inch, 0.6 * inch, f"{PROVIDER} / {CUSTOMER}  |  Master Services Agreement")
    canvas.drawRightString(LETTER[0] - inch, 0.6 * inch, f"Page {doc.page}")
    canvas.restoreState()


def render_pdf(clauses: list[tuple[str, str, str]], path: str | Path, *, injection: bool = False,
               injection_after: str = "13") -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(path), pagesize=LETTER, leftMargin=inch, rightMargin=inch,
                            topMargin=0.95 * inch, bottomMargin=0.95 * inch,
                            title="Master Services Agreement", author="Synthetic test document")
    flow = [Paragraph(TITLE, _TITLE), Paragraph(escape(PREAMBLE), _BODY)]
    for num, title, text in clauses:
        flow.append(Paragraph(f"{escape(num)}. {escape(title)}", _HEAD))
        flow.append(Paragraph(escape(text), _BODY))
        if injection and num == injection_after:
            flow.append(Paragraph(escape(INJECTION_TEXT), _HIDDEN))
    flow.append(Spacer(1, 16))
    sig = Table(
        [[Paragraph(f"<b>{escape(PROVIDER)}</b><br/>By: ______________________<br/>Name: Dana Okafor<br/>Title: VP, Deal Desk", _SIGN),
          Paragraph(f"<b>{escape(CUSTOMER)}</b><br/>By: ______________________<br/>Name: Priya Raman<br/>Title: Head of Procurement", _SIGN)]],
        colWidths=[3.2 * inch, 3.2 * inch])
    sig.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    flow.append(sig)
    doc.build(flow, onFirstPage=_footer, onLaterPages=_footer)
    return path
