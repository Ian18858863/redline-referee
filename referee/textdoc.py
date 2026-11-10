"""Read a text-layer PDF into clauses, and find where a quote sits on a page.

Deterministic, no model involved. Used by the rule layer and to place evidence
returned by Clocked back onto the page images.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

HEADING = re.compile(r"^(?:section\s+|article\s+)?(\d{1,2}(?:\.\d{1,2}){0,3})[.)]?\s+([A-Z][^\n]{0,90})$", re.I)
INLINE = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,3})[.)]?\s+([A-Z][A-Za-z ,&/'-]{2,60})\.\s+(\S.*)$")
MIN_TEXT_CHARS_PER_PAGE = 40          # same rule Clocked's pdf_mode="auto" documents


class NoTextLayer(ValueError):
    """Raised for scanned PDFs: nothing here can be quoted character by character."""


@dataclass
class Clause:
    number: str
    title: str
    text: str
    page: int                      # 1-based page where the clause starts


@dataclass
class HiddenSpan:
    text: str
    page: int
    reason: str                    # "tiny" | "white"
    rect: tuple[float, float, float, float]


@dataclass
class TextDoc:
    name: str
    path: Path | None
    pages: int
    page_sizes: list[tuple[float, float]]
    clauses: list[Clause]
    full_text: str
    hidden: list[HiddenSpan] = field(default_factory=list)

    def clause(self, number: str) -> Clause | None:
        return next((c for c in self.clauses if c.number == number), None)


def squash(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _page_lines(page) -> tuple[list[dict], list[HiddenSpan]]:
    lines, hidden = [], []
    data = page.get_text("dict", flags=pymupdf.TEXT_PRESERVE_WHITESPACE)
    for block in data.get("blocks", []):
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            text = "".join(s["text"] for s in spans)
            if not text.strip():
                continue
            sizes = [s["size"] for s in spans if s["text"].strip()]
            colours = {s["color"] for s in spans if s["text"].strip()}
            reason = ""
            if sizes and max(sizes) < 4.0:
                reason = "tiny"
            elif colours and colours <= {0xFFFFFF}:
                reason = "white"
            if reason:
                hidden.append(HiddenSpan(squash(text), page.number + 1, reason, tuple(line["bbox"])))
            lines.append({"text": text.rstrip(), "bbox": line["bbox"], "hidden": bool(reason)})
    lines.sort(key=lambda l: (round(l["bbox"][1], 1), l["bbox"][0]))
    return lines, hidden


def read_pdf(path: str | Path, name: str = "") -> TextDoc:
    path = Path(path)
    doc = pymupdf.open(path)
    try:
        per_page, hidden_all, sizes = [], [], []
        for page in doc:
            lines, hidden = _page_lines(page)
            per_page.append(lines)
            hidden_all.extend(hidden)
            sizes.append((page.rect.width, page.rect.height))
        n_pages = len(per_page)
        chars = sum(len(re.sub(r"\s", "", l["text"])) for lines in per_page for l in lines)
        if n_pages == 0 or chars / max(n_pages, 1) < MIN_TEXT_CHARS_PER_PAGE:
            raise NoTextLayer(
                f"{path.name} has no usable text layer (about {chars // max(n_pages, 1)} characters per page). "
                "Scanned copies cannot be quoted character by character, so they are not reviewed.")

        # drop running headers / footers: the same line (digits masked) on most pages
        def key(t: str) -> str:
            return re.sub(r"\d+", "#", squash(t)).lower()
        counts: dict[str, int] = {}
        for lines in per_page:
            for k in {key(l["text"]) for l in lines}:
                counts[k] = counts.get(k, 0) + 1
        repeated = {k for k, c in counts.items() if n_pages >= 2 and c >= max(2, round(0.6 * n_pages))}

        clauses: list[Clause] = []
        current: Clause | None = None
        buf: list[str] = []
        hidden_merged: list[HiddenSpan] = _merge_hidden(hidden_all)

        def flush():
            nonlocal current, buf
            if current is not None:
                current.text = _join(buf)
                if current.text or current.title:
                    clauses.append(current)
            current, buf = None, []

        current = Clause("0", "Preamble", "", 1)
        for pno, lines in enumerate(per_page, start=1):
            for l in lines:
                t = l["text"].strip()
                if key(t) in repeated or l["hidden"]:
                    continue
                m = HEADING.match(t)
                mi = INLINE.match(t)
                if m and len(t) <= 70 and not t.rstrip().endswith((",", ";")) and _looks_like_title(m.group(2)):
                    flush()
                    current = Clause(m.group(1), m.group(2).strip().rstrip("."), "", pno)
                elif mi:
                    flush()
                    current = Clause(mi.group(1), mi.group(2).strip(), "", pno)
                    buf.append(mi.group(3))
                else:
                    buf.append(t)
        flush()
        full = "\n".join(f"{c.number}. {c.title}\n{c.text}" if c.number != "0" else c.text for c in clauses)
        return TextDoc(name or path.stem, path, n_pages, sizes, clauses, full, hidden_merged)
    finally:
        doc.close()


def _looks_like_title(s: str) -> bool:
    words = s.strip().rstrip(".").split()
    if not 1 <= len(words) <= 8:
        return False
    if s.strip().endswith("."):
        return len(words) <= 6
    return True


def _join(lines: list[str]) -> str:
    out = ""
    for t in lines:
        if not out:
            out = t
        elif out.endswith("-") and t[:1].islower() and not out.endswith(" -"):
            out = out + t                      # keep hyphenated compounds such as thirty-six
        else:
            out = out + " " + t
    return squash(out)


def _merge_hidden(spans: list[HiddenSpan]) -> list[HiddenSpan]:
    merged: list[HiddenSpan] = []
    for s in spans:
        if merged and merged[-1].page == s.page and merged[-1].reason == s.reason and abs(s.rect[1] - merged[-1].rect[3]) < 6:
            p = merged[-1]
            merged[-1] = HiddenSpan(squash(p.text + " " + s.text), p.page, p.reason,
                                    (min(p.rect[0], s.rect[0]), p.rect[1], max(p.rect[2], s.rect[2]), s.rect[3]))
        else:
            merged.append(s)
    return merged


# ---- locating quotes on pages ----------------------------------------------

def locate(pdf_path: str | Path, quote: str, hint_page: int | None = None) -> list[dict]:
    """Return [{page, rects:[[x0,y0,x1,y1] as fractions of the page]}] for an exact quote.

    The quote must be found verbatim (whitespace-insensitive). If it is not found,
    the result is empty; callers report that instead of drawing an approximate box.
    """
    quote = squash(quote)
    if len(quote) < 3:
        return []
    doc = pymupdf.open(pdf_path)
    try:
        order = list(range(len(doc)))
        if hint_page and 1 <= hint_page <= len(doc):
            order.remove(hint_page - 1)
            order.insert(0, hint_page - 1)
        for i in order:
            page = doc[i]
            quads = page.search_for(quote, quads=False)
            if quads:
                w, h = page.rect.width, page.rect.height
                rects = [[round(r.x0 / w, 5), round(r.y0 / h, 5), round(r.x1 / w, 5), round(r.y1 / h, 5)] for r in quads]
                return [{"page": i + 1, "rects": rects}]
        return []
    finally:
        doc.close()


def render_pages(pdf_path: str | Path, out_dir: str | Path, prefix: str, dpi: int = 120) -> list[dict]:
    """Rasterise every page (annotations included) and return [{image,width,height}]."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open(pdf_path)
    pages = []
    try:
        for i, page in enumerate(doc, start=1):
            pix = page.get_pixmap(dpi=dpi, annots=True)
            name = f"{prefix}-p{i}.png"
            pix.save(out_dir / name)
            pages.append({"image": name, "width": pix.width, "height": pix.height})
    finally:
        doc.close()
    return pages
