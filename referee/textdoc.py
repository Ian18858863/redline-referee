"""Read a text-layer PDF into clauses, and find where a quote sits on a page.

Deterministic, no model involved. Used by the rule layer and to place evidence
returned by Clocked back onto the page images.

What this module decides, in order:

  reading order   lines are read column by column on multi-column pages, row by row in tables
  hidden text     decided from what is rendered (ink under the words), not from colour alone
  headers/footers lines in the top or bottom band of the page that repeat, or stand apart on short documents
  structure       numbered headings (9 Title, Section 4 - Title, Article IV, § 4, Clause 4, 4.2.1, 第三条),
                  else unnumbered headings, else paragraphs ("¶ 1", "¶ 2")

Nothing a page shows is dropped silently: every visible line ends up in a clause, in
`TextDoc.margins` (headers and footers) or in `TextDoc.hidden`.
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

MIN_TEXT_CHARS_PER_PAGE = 40          # same rule Clocked's pdf_mode="auto" documents
TINY_PT = 4.0                         # text at or under this size cannot be read on paper
BAND = 0.085                          # top and bottom share of the page where headers and footers live
RENDER_DPI = 96                       # resolution of the ink test
INK_DELTA = 24                        # a pixel changed by less than this (of 255) by the text is not ink
_FLAGS = pymupdf.TEXT_PRESERVE_WHITESPACE

# kept for callers that imported them; heading detection no longer relies on these two alone
HEADING = re.compile(r"^(?:(?i:section\s+|article\s+))?(\d{1,2}(?:\.\d{1,2}){0,3})[.)]?\s+([A-Z][^\n]{0,90})$")
INLINE = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,3})[.)]?\s+([A-Z][A-Za-z ,&/'-]{2,60})\.\s+(\S.*)$")


class NoTextLayer(ValueError):
    """Raised for scanned PDFs: nothing here can be quoted character by character."""


@dataclass
class Clause:
    number: str                    # display label: "9", "4.2", "IV", "第三条", "Schedule A 1", "#2", "¶ 3"
    title: str
    text: str
    page: int                      # 1-based page where the clause starts
    uid: str = ""                  # stable id in document order: c001, c002, ...
    kind: str = "numbered"         # preamble | numbered | appendix | heading | paragraph
    heading: str = ""              # the heading as it is written in the document ("" when there is none)
    style: str = ""                # numbering style of a numbered clause: "bare", "section", "article", "§", "cjk", ...
    spans: list = field(default_factory=list, repr=False, compare=False)
    # spans: (start, end, page, row, header) per source line of `text`; row >= 0 for a table row


@dataclass
class HiddenSpan:
    text: str
    page: int
    reason: str                    # "tiny" | "white" | "invisible" | "low-contrast" | "covered" | "off-page"
    rect: tuple[float, float, float, float]


@dataclass
class MarginLine:
    text: str
    page: int
    where: str                     # "top" | "bottom"
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
    margins: list[MarginLine] = field(default_factory=list)       # running headers and footers, kept out of clauses
    pages_without_text: list[int] = field(default_factory=list)   # pages with images but no usable text layer
    ocr_pages: list[int] = field(default_factory=list)            # pages whose text is an invisible layer over an image
    structure: str = "numbered"                                   # numbered | headings | paragraphs
    streams: list[str] = field(default_factory=list, repr=False, compare=False)   # per page: every word, in reading order
    edges: list[tuple[str, str]] = field(default_factory=list, repr=False, compare=False)
    # edges: per page, the same text without its bottom band / without its top band (for quotes over a page break)

    def clause(self, number: str) -> Clause | None:
        """First clause with this display label. Labels can repeat (appendices); use by_uid for identity."""
        return next((c for c in self.clauses if c.number == number), None)

    def by_uid(self, uid: str | None) -> Clause | None:
        return next((c for c in self.clauses if c.uid == uid), None) if uid else None

    def can_locate(self, quote: str, hint_page: int | None = None) -> bool:
        """True when `locate` would find this quote verbatim (whitespace-insensitive). No file access."""
        q = squash(quote)
        if len(q) < 3 or not self.streams:
            return False
        for strict in (True, False):
            pat = _quote_re(q, strict)
            if any(pat.search(s) for s in self.streams):
                return True
            if any(_straddles(pat, a[0], b[1]) is not None for a, b in zip(self.edges, self.edges[1:])):
                return True
        return False


def squash(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _nospace(s: str) -> str:
    return re.sub(r"\s+", "", s)


_LATIN = "0-9A-Za-z\u00c0-\u024f"
_QUOTE_RES: dict[tuple[str, bool], re.Pattern] = {}


def _quote_re(q: str, strict: bool = True) -> re.Pattern:
    """A quote as a pattern: the same characters in the same case. Whitespace may differ in amount, and a line
    break may fall inside a word of the quote, but a space in the quote is a space on the page ("2 4" is not
    "2024"). Strict: the quote starts and ends at a word boundary ("30 days" is not found inside "130 days")."""
    pat = _QUOTE_RES.get((q, strict))
    if pat is None:
        body = "".join(r"\s+" if ch == " " else re.escape(ch) + r"\s*" for ch in q)
        if body.endswith(r"\s*"):
            body = body[:-3]
        head = rf"(?<![{_LATIN}])" if strict and re.match(rf"[{_LATIN}]", q[0]) else ""
        tail = rf"(?![{_LATIN}])" if strict and re.match(rf"[{_LATIN}]", q[-1]) else ""
        if len(_QUOTE_RES) > 4000:
            _QUOTE_RES.clear()
        pat = _QUOTE_RES[(q, strict)] = re.compile(head + body + tail)
    return pat


def _straddles(pat: re.Pattern, a: str, b: str) -> tuple[int, int, int] | None:
    """(start, end, len(a) + 1) of a match that begins on page text a and ends on page text b."""
    for m in pat.finditer(a + " " + b):
        if m.start() < len(a) and m.end() > len(a) + 1:
            return m.start(), m.end(), len(a) + 1
    return None


# ---------------------------------------------------------------- page lines

class _Line:
    __slots__ = ("text", "full", "bbox", "real", "page", "block", "first", "nblock", "size", "bold", "hidden", "margin",
                 "row", "table", "header", "words", "key")

    def __init__(self, text, full, bbox, page, block, first, nblock, size, bold, hidden):
        self.text = text            # visible text
        self.full = full            # text as extracted, hidden parts included (what locate sees)
        self.bbox = bbox            # box in the frame where the text runs left to right (used for layout)
        self.real = bbox            # box in the page's own coordinates (used for drawing)
        self.page = page
        self.block = block
        self.first = first          # first line of its block
        self.nblock = nblock        # lines in its block
        self.size = size
        self.bold = bold
        self.hidden = hidden        # reason when the whole line is hidden
        self.margin = ""
        self.row = -1               # id of the table row this line is a cell of
        self.table = False
        self.header = ""            # text of the header row of the table
        self.words = None
        self.key = (page, block, 0)


def _lum(color: int) -> float:
    return (0.2126 * ((color >> 16) & 255) + 0.7152 * ((color >> 8) & 255) + 0.0722 * (color & 255)) / 255


class _Ink:
    """Two renderings of one page, with and without its text. Text that changes no pixel is not visible."""

    def __init__(self, doc, page):
        self.ok = False
        self.turn = page.rotation_matrix if page.rotation else None
        try:
            self.scale = RENDER_DPI / 72
            m = pymupdf.Matrix(self.scale, self.scale)
            self.a = page.get_pixmap(matrix=m, colorspace=pymupdf.csRGB, alpha=False, annots=False)
            bare = pymupdf.open()
            try:
                bare.insert_pdf(doc, from_page=page.number, to_page=page.number)
                p = bare[0]
                if p.rotation:
                    p.remove_rotation()            # same picture, but boxes and page now share one frame
                p.add_redact_annot(p.rect + (-40, -40, 40, 40), fill=False)
                p.apply_redactions(images=0, graphics=0, text=0)
                if p.get_text().strip():
                    return
                self.b = p.get_pixmap(matrix=m, colorspace=pymupdf.csRGB, alpha=False, annots=False)
            finally:
                bare.close()
            self.ok = (self.a.width, self.a.height, self.a.n) == (self.b.width, self.b.height, self.b.n) and self.a.n == 3
            self.ma, self.mb = self.a.samples_mv, self.b.samples_mv
        except Exception:                      # an unusual page: fall back to what the text attributes say
            self.ok = False

    def _box(self, bbox):
        s, w, h = self.scale, self.a.width, self.a.height
        if self.turn is not None:
            r = pymupdf.Rect(bbox) * self.turn
            r.normalize()
            bbox = (r.x0, r.y0, r.x1, r.y1)
        x0, y0 = max(0, int(bbox[0] * s)), max(0, int(bbox[1] * s))
        x1, y1 = min(w, int(bbox[2] * s) + 1), min(h, int(bbox[3] * s) + 1)
        return x0, y0, x1, y1

    def visible(self, bbox) -> bool:
        x0, y0, x1, y1 = self._box(bbox)
        if x1 <= x0 or y1 <= y0:
            return False
        stride, ma, mb = self.a.stride, self.ma, self.mb
        for y in range(y0, y1):
            lo, hi = y * stride + 3 * x0, y * stride + 3 * x1
            ra, rb = ma[lo:hi], mb[lo:hi]
            if ra != rb:
                for p, q in zip(ra, rb):
                    if p - q >= INK_DELTA or q - p >= INK_DELTA:
                        return True
        return False

    def background(self, bbox) -> int:
        x0, y0, x1, y1 = self._box(bbox)
        if x1 <= x0 or y1 <= y0:
            return 0xFFFFFF
        i = ((y0 + y1) // 2) * self.b.stride + 3 * ((x0 + x1) // 2)
        r, g, b = self.mb[i], self.mb[i + 1], self.mb[i + 2]
        return (r << 16) | (g << 8) | b


_PLAIN_OPS = {"fill-text", "stroke-text", "stroke-path"}


def _overtyped(page, log) -> list[tuple[str, tuple[float, float, float, float]]]:
    """Words under an opaque box that has newer text typed over it: [(text, box)].

    The ink test cannot see these: the spot is inked, but by the newer words. Drawing order can."""
    texts = [(i, pymupdf.Rect(b)) for i, (kind, b) in enumerate(log) if kind.endswith("-text")]
    fills = [i for i, (kind, _) in enumerate(log) if kind in ("fill-path", "fill-image")]
    if not texts or not any(texts[0][0] < f < texts[-1][0] for f in fills):
        return []
    covers: list[tuple[int, pymupdf.Rect]] = [(i, pymupdf.Rect(b)) for i, (kind, b) in enumerate(log) if kind == "fill-image"]
    for d in page.get_drawings():
        opacity = d.get("fill_opacity")
        if d.get("fill") is None or (opacity is not None and opacity < 0.99):
            continue
        for it in d.get("items", []):
            if it[0] == "re":
                covers.append((d.get("seqno", -1), pymupdf.Rect(it[1])))
            elif it[0] == "qu":
                covers.append((d.get("seqno", -1), it[1].rect))
    covers = [(c, r) for c, r in covers if any(i > c and tr.intersects(r) for i, tr in texts)]
    out: list[tuple[str, tuple[float, float, float, float]]] = []
    if not covers:
        return out
    for sp in page.get_texttrace():
        over = [r for c, r in covers if c > sp["seqno"] and r.intersects(sp["bbox"])]
        if not over:
            continue
        run: list[tuple[str, tuple]] = []
        for u, _, _, bb in list(sp.get("chars", [])) + [(32, 0, None, (0, 0, 0, 0))]:
            mid = pymupdf.Point((bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2)
            if u != 32 and any(r.contains(mid) for r in over):
                run.append((chr(u), bb))
                continue
            if run:
                out.append(("".join(ch for ch, _ in run), (min(b[0] for _, b in run), min(b[1] for _, b in run),
                                                         max(b[2] for _, b in run), max(b[3] for _, b in run))))
                run = []
    return out


def _page_layout(doc, page, want_words: bool = False):
    """(lines in reading order, hidden spans, facts about the page)."""
    pno = page.number + 1
    tp = page.get_textpage(flags=_FLAGS)
    data = page.get_text("dict", textpage=tp)
    raw = []                                           # (block index, line index, line dict, [span facts])
    total = invisible = 0
    light = False
    for bi, block in enumerate(data.get("blocks", [])):
        for li, line in enumerate(block.get("lines", [])):
            spans = [s for s in line.get("spans", [])]
            if not "".join(s["text"] for s in spans).strip():
                continue
            raw.append((bi, li, line, spans))
            for s in spans:
                n = len(s["text"].strip())
                if not n:
                    continue
                total += n
                alpha = s.get("alpha", 255)
                if alpha == 0:
                    invisible += n
                elif alpha < 255 or _lum(s["color"]) > 0.5:
                    light = True
    try:
        log = page.get_bboxlog()
    except Exception:
        log = []
    try:
        images = page.get_image_info()
    except Exception:
        images = []
    # text and drawing boxes come in the page's unrotated coordinates; page.rect is the page as displayed
    frame = pymupdf.Rect(0, 0, page.rect.height, page.rect.width) if page.rotation in (90, 270) else pymupdf.Rect(page.rect)
    page_area = max(1.0, frame.width * frame.height)
    image_cover = max((abs(pymupdf.Rect(im["bbox"]) & frame) / page_area for im in images
                       if pymupdf.Rect(im["bbox"]).intersects(frame)), default=0.0)
    ocr = total > 0 and invisible >= 0.6 * total and image_cover >= 0.5
    need_ink = (light or any(kind not in _PLAIN_OPS for kind, _ in log)) and not ocr
    need_words = want_words or need_ink
    words_by: dict[tuple[int, int], list] = {}
    if need_words:
        for w in page.get_text("words", textpage=tp):
            words_by.setdefault((w[5], w[6]), []).append(w)
    ink = _Ink(doc, page) if need_ink else None
    if ink is not None and not ink.ok:
        ink = None
    try:
        typed_over = _overtyped(page, log) if need_ink else []
    except Exception:
        typed_over = []

    def under_newer_text(w) -> bool:
        t = _nospace(w[4])
        return any(abs(box[0] - w[0]) < 1.5 and abs((box[1] + box[3]) - (w[1] + w[3])) < 6 and t.startswith(text)
                   and 2 * len(text) >= len(t) for text, box in typed_over)

    def reason_of(span, bbox) -> str:
        if span["size"] <= TINY_PT:
            return "tiny"
        if span.get("alpha", 255) == 0:
            return "" if ocr else "invisible"
        if ink is None:
            return "white" if (need_ink and span["color"] == 0xFFFFFF) else ""
        if ink.visible(bbox):
            return ""
        bg, col = ink.background(bbox), span["color"]
        if _lum(col) >= 0.85 and _lum(bg) >= 0.85:
            return "white"
        if max(abs(((col >> k) & 255) - ((bg >> k) & 255)) for k in (16, 8, 0)) < 2 * INK_DELTA:
            return "low-contrast"
        return "covered"

    lines, hidden = [], []
    blocks_n: dict[int, int] = {}
    for bi, li, line, spans in raw:
        blocks_n[bi] = blocks_n.get(bi, 0) + 1
    seen_block: set[int] = set()
    for bi, li, line, spans in raw:
        full = "".join(s["text"] for s in spans)
        live = [s for s in spans if s["text"].strip()]
        words = words_by.get((bi, li), [])
        if (ink is not None or typed_over) and words:
            # word by word, so that a patch laid over part of a line is seen
            pieces = []
            for w in words:
                cx = (w[0] + w[2]) / 2
                sp = next((s for s in live if s["bbox"][0] - 0.5 <= cx <= s["bbox"][2] + 0.5), live[0])
                pieces.append((w[4], tuple(w[:4]), reason_of(sp, w[:4]) or ("covered" if typed_over and under_newer_text(w) else "")))
            by_word = True
        else:
            pieces = [(s["text"], tuple(s["bbox"]), reason_of(s, s["bbox"])) for s in live]
            by_word = False
        reasons = [r for _, _, r in pieces]
        if pieces and all(reasons):
            hidden.append(HiddenSpan(squash(full), pno, reasons[0], tuple(line["bbox"])))
            text, why = full.rstrip(), reasons[0]
        elif any(reasons):
            run: list = []
            for t, bb, r in pieces + [("", None, "")]:
                if r and (not run or run[-1][2] == r):
                    run.append((t, bb, r))
                    continue
                if run:
                    box = (min(x[1][0] for x in run), min(x[1][1] for x in run), max(x[1][2] for x in run), max(x[1][3] for x in run))
                    hidden.append(HiddenSpan(squash((" " if by_word else "").join(x[0] for x in run)), pno, run[0][2], box))
                run = [(t, bb, r)] if r else []
            text, why = (" " if by_word else "").join(t for t, _, r in pieces if not r).rstrip(), ""
        else:
            text, why = full.rstrip(), ""
        ln = _Line(text, full, tuple(line["bbox"]), pno, bi, bi not in seen_block, blocks_n[bi],
                   max((s["size"] for s in live), default=0.0),
                   bool(live) and all(s["flags"] & 16 or "bold" in s["font"].lower() for s in live), why)
        seen_block.add(bi)
        ln.key = (pno, bi, li)
        if want_words:
            ln.words = [(w[0], w[1], w[2], w[3], w[4]) for w in words]
        lines.append(ln)

    # text drawn outside the page box never reaches the text layer above, but it is in the file
    try:
        outside = [b for kind, b in log if kind.endswith("-text") and not pymupdf.Rect(b).is_empty
                   and not pymupdf.Rect(b).intersects(frame)]
        if outside:
            for sp in page.get_texttrace():
                r = pymupdf.Rect(sp["bbox"])
                if r.is_empty or r.intersects(frame):
                    continue
                t = squash("".join(chr(c[0]) for c in sp.get("chars", [])))
                if t:
                    hidden.append(HiddenSpan(t, pno, "off-page", tuple(r)))
    except Exception:
        pass

    # lay the page out in the frame where its text runs left to right (a sideways scan reads top to bottom)
    weight: dict[tuple[int, int], int] = {}
    for bi, li, line, spans in raw:
        d = line.get("dir", (1.0, 0.0))
        k = (int(round(d[0])), int(round(d[1])))
        weight[k] = weight.get(k, 0) + len(line.get("spans", [{}])[0].get("text", "")) + 1
    way = max(weight, key=weight.get) if weight else (1, 0)
    height = frame.height
    if way != (1, 0):
        W, H = frame.width, frame.height
        for l in lines:
            x0, y0, x1, y1 = l.real
            if way == (0, 1):
                l.bbox = (y0, W - x1, y1, W - x0)
            elif way == (0, -1):
                l.bbox = (H - y1, x0, H - y0, x1)
            else:
                l.bbox = (W - x1, H - y1, W - x0, H - y0)
        height = W if way[0] == 0 else H
    ordered = _order(lines)
    _mark_rows(ordered)
    facts = {"chars": total, "images": bool(images), "ocr": ocr, "height": height}
    return ordered, hidden, facts


# ------------------------------------------------------------- reading order

def _yx(l: _Line):
    return (round(l.bbox[1], 1), l.bbox[0])


def _order(lines: list[_Line], depth: int = 0) -> list[_Line]:
    """Top to bottom, left to right; column by column where a zone of the page is set in columns."""
    base = sorted(lines, key=_yx)
    if depth > 3 or len(base) < 12:
        return base
    g = _gutter(base)
    if g is None:
        return base
    xmin, xmax = min(l.bbox[0] for l in base), max(l.bbox[2] for l in base)
    out: list[_Line] = []
    zone: list[_Line] = []

    def flush():
        if not zone:
            return
        left = [l for l in zone if l.bbox[2] <= g + 1]
        right = [l for l in zone if l.bbox[0] >= g - 1]
        if len(left) + len(right) == len(zone) and _columnar(left, right, xmax - xmin):
            out.extend(_order(left, depth + 1))
            out.extend(_order(right, depth + 1))
        else:
            out.extend(zone)
        zone.clear()

    for l in base:
        if l.bbox[0] < g - 1 and l.bbox[2] > g + 1:      # a line across the gutter ends the zone
            flush()
            out.append(l)
        else:
            zone.append(l)
    flush()
    return out


def _gutter(base: list[_Line]) -> float | None:
    xmin, xmax = min(l.bbox[0] for l in base), max(l.bbox[2] for l in base)
    width = xmax - xmin
    if width < 200:
        return None
    starts: dict[int, int] = {}
    for l in base:
        k = int(round(l.bbox[0] / 3.0))
        starts[k] = starts.get(k, 0) + 1
    best, best_n = None, 0
    need = max(5, int(0.1 * len(base)))
    for k in sorted(starts):
        n = starts[k] + starts.get(k + 1, 0)
        x = k * 3.0
        if n < need or x < xmin + 0.25 * width or x > xmax - 0.15 * width:
            continue
        g = x - 4.0
        left = sum(1 for l in base if l.bbox[2] <= g + 1)
        crossing = sum(1 for l in base if l.bbox[0] < g - 1 and l.bbox[2] > g + 1)
        if left >= 5 and crossing <= 0.5 * len(base) and min(left, n) > best_n:
            best, best_n = g, min(left, n)
    return best


def _columnar(left: list[_Line], right: list[_Line], width: float) -> bool:
    def full(side: list[_Line]) -> bool:
        if not side:
            return False
        w = max(l.bbox[2] for l in side) - min(l.bbox[0] for l in side)
        ws = sorted(l.bbox[2] - l.bbox[0] for l in side)
        return w >= 0.25 * width and ws[len(ws) // 2] >= 0.6 * w
    if len(left) < 6 or not full(left):
        return False
    return len(right) <= 2 or full(right)


def _mark_rows(ordered: list[_Line]) -> None:
    """Lines that share a baseline are cells of one row; two or more such rows in a run are a table."""
    rows: list[list[_Line]] = []
    for l in ordered:
        if rows:
            p = rows[-1][-1]
            overlap = min(p.bbox[3], l.bbox[3]) - max(p.bbox[1], l.bbox[1])
            if overlap > 0.5 * min(p.bbox[3] - p.bbox[1], l.bbox[3] - l.bbox[1]) and l.bbox[0] >= p.bbox[2] - 1:
                rows[-1].append(l)
                continue
        rows.append([l])
    i, rid = 0, 0
    while i < len(rows):
        if len(rows[i]) < 2:
            i += 1
            continue
        j = i
        # a table runs on while multi-cell rows keep coming; single lines between them are wrapped cells
        while j + 1 < len(rows) and (len(rows[j + 1]) >= 2 or (j + 2 < len(rows) and len(rows[j + 2]) >= 2
                                                              and _is_cell(rows[j + 1][0], rows[i]))):
            j += 1
        if j > i:
            header = squash(" ".join(c.text for c in rows[i]))
            for r in rows[i:j + 1]:
                for c in r:
                    c.row, c.table, c.header = rid, True, header
                rid += 1
        else:
            for c in rows[i]:
                c.row = rid
            rid += 1
        i = j + 1


def _is_cell(line: _Line, row: list[_Line]) -> bool:
    """A lone line inside a table is a wrapped cell when it sits under one cell and is no wider than it."""
    width = max(c.bbox[2] for c in row) - min(c.bbox[0] for c in row)
    return (line.bbox[2] - line.bbox[0]) < 0.6 * width


# --------------------------------------------------------- headers / footers

def _in_band(l: _Line, height: float, where: str) -> bool:
    mid = (l.bbox[1] + l.bbox[3]) / 2
    return mid < BAND * height if where == "top" else mid > (1 - BAND) * height


def _mask(t: str) -> str:
    return re.sub(r"\d+", "#", squash(t)).lower()


def _mark_margins(per_page: list[list[_Line]], heights: list[float]) -> None:
    """Flag running headers and footers by position, then by repetition (or isolation on short documents)."""
    n = len(per_page)
    sizes = sorted(l.size for lines in per_page for l in lines if not l.hidden)
    body_size = sizes[len(sizes) // 2] if sizes else 10.0
    cands: list[tuple[_Line, str, bool]] = []          # (line, band, stands apart from the body)
    counts: dict[tuple[str, str], int] = {}
    for lines, h in zip(per_page, heights):
        vis = sorted((l for l in lines if not l.hidden), key=lambda l: l.bbox[1])
        top = [l for l in vis if (l.bbox[1] + l.bbox[3]) / 2 < BAND * h]
        bottom = [l for l in vis if (l.bbox[1] + l.bbox[3]) / 2 > (1 - BAND) * h]
        if len(top) + len(bottom) == len(vis):          # a page with nothing but band lines has no body to protect
            continue
        seen: set[tuple[str, str]] = set()
        for band, group in (("top", top), ("bottom", bottom)):
            inner = [l for l in vis if l not in top and l not in bottom]
            edge = (min(l.bbox[1] for l in inner) if band == "top" else max(l.bbox[3] for l in inner)) if inner else None
            apart = False
            for l in (sorted(group, key=lambda l: -l.bbox[3]) if band == "top" else sorted(group, key=lambda l: l.bbox[1])):
                if edge is not None and not apart:
                    gap = (edge - l.bbox[3]) if band == "top" else (l.bbox[1] - edge)
                    apart = gap >= 11.0
                    if not apart:                        # part of the body that happens to reach into the band
                        edge = l.bbox[1] if band == "top" else l.bbox[3]
                cands.append((l, band, apart))
                seen.add((band, _mask(l.text)))
        for k in seen:
            counts[k] = counts.get(k, 0) + 1
    need = max(2, (n + 1) // 2)
    for l, band, apart in cands:
        repeated = n >= 2 and counts.get((band, _mask(l.text)), 0) >= need
        alone = n <= 2 and apart and l.size <= 1.15 * body_size and len(l.text) <= 120
        if repeated or alone:
            l.margin = band


# ------------------------------------------------------------------ headings

_ROMAN = r"M{0,3}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})"
_KW = r"section|sec\.|article|art\.|clause|paragraph|part|chapter|§{1,2}"
_NUMBERED = re.compile(
    rf"^(?:(?P<kw>(?i:{_KW}))\s*)?(?P<num>\d{{1,4}}(?:\.\d{{1,3}}){{0,4}}|(?=[IVXLCDM]){_ROMAN})(?![A-Za-z0-9])"
    rf"(?P<sep>[.)、:：]|\s*[-–—]\s)?\s*(?P<rest>.*)$")
_CJK_ART = re.compile(r"^第\s*(?P<num>[一二三四五六七八九十百千零〇两\d]+)\s*(?P<unit>[条條章节節款项項])[\s:：、.]*(?P<rest>.*)$")
_CJK_LIST = re.compile(r"^(?P<num>[一二三四五六七八九十]{1,3})\s*、\s*(?P<rest>.*)$")
_APPENDIX = re.compile(r"^(?P<kw>(?i:schedule|exhibit|annex|annexure|appendix|attachment|addendum))\s+"
                       r"(?P<id>[A-Z]|\d{1,2}|[IVX]{1,4})(?![A-Za-z0-9])[\s:.\-–—]*(?P<rest>.*)$")
_INLINE_TITLE = re.compile(r"^(?P<title>[^\W\d_a-z][^\W\d_ ,&/'’-]*(?:[ ,&/'’-]+[^\W\d_]+){0,7})\s*[.:：]\s+(?P<body>\S.*)$")
_NOT_TITLE_FIRST = set((
    "day days week weeks month months year years hour hours minute minutes business calendar working percent per "
    "january february march april may june july august september october november december "
    "jan feb mar apr jun jul aug sep sept oct nov dec").split())
_ENDS_SENTENCE = re.compile(r"[.:;!?。：；！？][\"'”’)\]]*$")
_CJK_DIGITS = {c: i for i, c in enumerate("零一二三四五六七八九")} | {"〇": 0, "两": 2}


def _cjk_int(s: str) -> int:
    if s.isdigit():
        return int(s)
    total, cur = 0, 0
    for ch in s:
        if ch in _CJK_DIGITS:
            cur = _CJK_DIGITS[ch]
        elif ch == "十":
            total += (cur or 1) * 10
            cur = 0
        elif ch == "百":
            total += (cur or 1) * 100
            cur = 0
        elif ch == "千":
            total += (cur or 1) * 1000
            cur = 0
    return total + cur


def _roman_int(s: str) -> int:
    vals = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    total = 0
    for a, b in zip(s, s[1:] + " "):
        total += -vals[a] if b in vals and vals[b] > vals[a] else vals[a]
    return total


def _title_ok(s: str) -> bool:
    s = s.strip()
    if not s or len(s) > 90:
        return False
    ch = s[0]
    if ch.islower() or not (ch.isalpha() or ch in "\"“'‘([«"):
        return False
    words = s.rstrip(".").split()
    if not 1 <= len(words) <= 8:
        return False
    if s.endswith((",", ";", "，", "；", "。")):
        return False
    if s.endswith(".") and len(words) > 6:
        return False
    return words[0].lower().strip("\"“'‘([«.,") not in _NOT_TITLE_FIRST


def _looks_like_title(s: str) -> bool:
    words = s.strip().rstrip(".").split()
    if not 1 <= len(words) <= 8:
        return False
    if s.strip().endswith("."):
        return len(words) <= 6
    return True


class _Cand:
    __slots__ = ("i", "style", "value", "number", "title", "body", "strong", "raw", "accepted")

    def __init__(self, i, style, value, number, title, body, strong, raw):
        self.i, self.style, self.value, self.number, self.title, self.body = i, style, value, number, title, body
        self.strong, self.raw, self.accepted = strong, raw, False


def _candidate(i: int, t: str) -> _Cand | None:
    """A line that could open a numbered clause: own-line heading, inline heading, or numbered paragraph."""
    m = _CJK_ART.match(t)
    if m:
        rest = m.group("rest").strip()
        label = f"第{m.group('num')}{m.group('unit')}"
        if not rest or (len(rest) <= 24 and not re.search(r"[，。；,;]", rest)):
            return _Cand(i, "cjk" + m.group("unit"), (_cjk_int(m.group("num")),), label, rest, "", True, t)
        return _Cand(i, "cjk" + m.group("unit"), (_cjk_int(m.group("num")),), label, "", rest, True, t)
    m = _CJK_LIST.match(t)
    if m:
        rest = m.group("rest").strip()
        if rest and len(rest) <= 24 and not re.search(r"[，。；,;]", rest):
            return _Cand(i, "cjk、", (_cjk_int(m.group("num")),), m.group("num"), rest, "", False, t)
        return None
    m = _NUMBERED.match(t)
    if not m or not m.group("num"):
        return None
    kw, num, sep, rest = (m.group("kw") or ""), m.group("num"), (m.group("sep") or ""), m.group("rest").strip()
    roman = not num[0].isdigit()
    if roman and not kw and sep.strip() not in (".", ")"):
        return None                                   # a bare roman numeral needs "IV." or "IV)" to be a label
    if not kw and not sep and rest and t[m.end("num"):m.end("num") + 1] not in (" ", "\t", " "):
        return None
    value = (_roman_int(num),) if roman else tuple(int(x) for x in num.split("."))
    style = (kw.lower().rstrip(".") or "bare") if kw else "bare"
    style = {"sec": "section", "art": "article", "§§": "§"}.get(style, style) + ("/roman" if roman else "")
    strong = bool(kw)
    if not rest:
        return _Cand(i, style, value, num, "", "", strong, t) if kw else None
    limit = 90 if kw else 70
    if len(t) <= limit and _title_ok(rest):
        return _Cand(i, style, value, num, rest.rstrip(".").strip() if not rest.endswith("..") else rest, "", strong, t)
    mi = _INLINE_TITLE.match(rest)
    if mi and _title_ok(mi.group("title")) and len(mi.group("title")) <= 61:
        return _Cand(i, style, value, num, mi.group("title").strip(), mi.group("body"), strong, t)
    first = rest[0]
    if (first.isupper() or first in "\"“(") and (sep.strip() in (".", ")") or len(value) >= 2 or kw):
        return _Cand(i, style, value, num, "", rest, strong, t)      # a numbered paragraph without a title
    return None


def _fits(a: tuple, b: tuple) -> bool:
    """Does number b plausibly follow number a in an outline? (4 → 4.1, 4.2 → 4.3, 4.4 → 5, 3 → 4.1; small gaps allowed)"""
    for k in range(min(len(a), len(b)) + 1):
        if a[:k] != b[:k] or k >= len(b):
            break
        tail_ok = all(x <= 1 for x in b[k + 1:])
        if k < len(a):
            if 1 <= b[k] - a[k] <= 3 and tail_ok:
                return True
        elif b[k] <= 2 and tail_ok:
            return True
    return False


# --------------------------------------------------------------- read a file

def read_pdf(path: str | Path, name: str = "") -> TextDoc:
    path = Path(path)
    doc = pymupdf.open(path)
    try:
        per_page, hidden_all, sizes, heights = [], [], [], []
        no_text, ocr_pages, chars = [], [], 0
        for page in doc:
            lines, hidden, facts = _page_layout(doc, page)
            per_page.append(lines)
            hidden_all.extend(hidden)
            sizes.append((page.rect.width, page.rect.height))
            heights.append(facts["height"])
            n = sum(len(_nospace(l.full)) for l in lines)
            chars += n
            if facts["images"] and sum(len(_nospace(l.text)) for l in lines if not l.hidden) < MIN_TEXT_CHARS_PER_PAGE:
                no_text.append(page.number + 1)
            if facts["ocr"]:
                ocr_pages.append(page.number + 1)
        n_pages = len(per_page)
        if n_pages == 0 or chars / max(n_pages, 1) < MIN_TEXT_CHARS_PER_PAGE:
            raise NoTextLayer(
                f"{path.name} has no usable text layer (about {chars // max(n_pages, 1)} characters per page). "
                "Scanned copies cannot be quoted character by character, so they are not reviewed.")

        _mark_margins(per_page, heights)
        margins = [MarginLine(squash(l.text), l.page, l.margin, l.real) for lines in per_page for l in lines
                   if l.margin and not l.hidden]
        body = [l for lines in per_page for l in lines if not l.margin and not l.hidden and l.text.strip()]
        body = _merge_labels(body)
        clauses, structure = _build_clauses(body)
        for i, c in enumerate(clauses, start=1):
            c.uid = f"c{i:03d}"
        full = "\n".join(_as_text(c) for c in clauses)
        streams = [" ".join(squash(l.full) for l in lines) for lines in per_page]
        edges = [(" ".join(squash(l.full) for l in lines if not _in_band(l, h, "bottom")),
                  " ".join(squash(l.full) for l in lines if not _in_band(l, h, "top"))) for lines, h in zip(per_page, heights)]
        return TextDoc(name or path.stem, path, n_pages, sizes, clauses, full, _merge_hidden(hidden_all), margins,
                       no_text, ocr_pages, structure, streams, edges)
    finally:
        doc.close()


def _as_text(c: Clause) -> str:
    if c.kind in ("preamble", "paragraph"):
        return c.text
    if c.kind == "numbered" and c.style == "bare":
        return f"{c.number}. {c.title}\n{c.text}"
    return f"{c.heading}\n{c.text}"


def _merge_labels(body: list[_Line]) -> list[_Line]:
    """"4." and "Fees" set as two pieces of one line (a tab stop) are one heading line."""
    out: list[_Line] = []
    i = 0
    label = re.compile(rf"^(?:(?i:{_KW})\s*)?(?:\d{{1,4}}(?:\.\d{{1,3}}){{0,4}}|{_ROMAN})[.)]?$")
    while i < len(body):
        l = body[i]
        if (i + 1 < len(body) and not l.table and l.row >= 0 and body[i + 1].row == l.row and body[i + 1].page == l.page
                and (i + 2 >= len(body) or body[i + 2].row != l.row) and label.match(l.text.strip())):
            nxt = body[i + 1]
            m = _Line(l.text.strip() + " " + nxt.text.strip(), l.full + " " + nxt.full,
                      (l.bbox[0], min(l.bbox[1], nxt.bbox[1]), nxt.bbox[2], max(l.bbox[3], nxt.bbox[3])),
                      l.page, l.block, l.first, l.nblock, max(l.size, nxt.size), l.bold and nxt.bold, "")
            m.key = l.key
            m.real = (min(l.real[0], nxt.real[0]), min(l.real[1], nxt.real[1]), max(l.real[2], nxt.real[2]), max(l.real[3], nxt.real[3]))
            out.append(m)
            i += 2
            continue
        out.append(l)
        i += 1
    return out


def _mid_sentence(body: list[_Line], i: int) -> bool:
    """True when line i continues the sentence of the line before it (so it cannot be a heading)."""
    if i == 0:
        return False
    prev, cur = body[i - 1], body[i]
    if cur.first or prev.table:
        return False
    if _ENDS_SENTENCE.search(prev.text.strip()):
        return False
    j = i - 1
    while j > 0 and body[j - 1].page == prev.page and body[j - 1].block == prev.block:
        j -= 1
    k = i
    while k + 1 < len(body) and body[k + 1].page == prev.page and body[k + 1].block == prev.block:
        k += 1
    group = body[j:k + 1]
    left, right = min(l.bbox[0] for l in group), max(l.bbox[2] for l in group)
    return prev.bbox[2] >= right - 0.2 * (right - left)       # the line before runs to the margin: it wrapped


def _accept_numbered(body: list[_Line]) -> tuple[dict[int, _Cand], dict[int, tuple[str, str]]]:
    """Which lines open a numbered clause, and which open an appendix. Numbering has to make sense as a sequence."""
    cands: list[_Cand] = []
    appendix: dict[int, tuple[str, str]] = {}
    for i, l in enumerate(body):
        t = l.text.strip()
        if l.table:
            continue
        ma = _APPENDIX.match(t)
        if ma and len(t) <= 80 and not _mid_sentence(body, i) and (not ma.group("rest") or _title_ok(ma.group("rest"))):
            appendix[i] = (f"{ma.group('kw').capitalize()} {ma.group('id')}", ma.group("rest").strip().rstrip("."))
            continue
        c = _candidate(i, t)
        if c is not None and not _mid_sentence(body, i):
            cands.append(c)
    by_style: dict[str, list[_Cand]] = {}
    for c in cands:
        by_style.setdefault(c.style, []).append(c)
    pos = {id(c): k for cs in by_style.values() for k, c in enumerate(cs)}
    cand_lines = {c.i for c in cands}

    def has_body(c: _Cand) -> bool:
        return bool(c.body) or (c.i + 1 < len(body) and (c.i + 1) not in cand_lines and (c.i + 1) not in appendix)

    def next_fits(c: _Cand, look: int = 2) -> bool:
        same = by_style[c.style]
        k = pos[id(c)]
        return any(_fits(c.value, n.value) for n in same[k + 1:k + 1 + look])

    last: dict[str, tuple] = {}
    fresh: set[str] = set()                 # styles allowed to restart: another heading came in between
    events = sorted([(c.i, c) for c in cands] + [(i, None) for i in appendix], key=lambda e: e[0])
    for i, c in events:
        if c is None:
            fresh |= set(last)
            continue
        prev = last.get(c.style)
        if prev is None:
            ok = (c.strong or c.value[0] <= 2 or next_fits(c) or (has_body(c) and next_fits(c, 6))
                  or (len(cands) == 1 and c.i == 0 and bool(c.title)))
        elif _fits(prev, c.value):
            ok = True
        elif c.style in fresh and c.value[0] <= 2 and all(x <= 1 for x in c.value[1:]):
            ok = True
        elif c.value[0] <= 1 and all(x <= 1 for x in c.value[1:]):
            ok = has_body(c) and next_fits(c)         # numbering starts again (an appendix without a heading)
        else:
            ok = has_body(c) and next_fits(c) and c.value > prev
        if ok:
            c.accepted = True
            last[c.style] = c.value
            fresh.discard(c.style)
            fresh |= {s for s in last if s != c.style}
    accepted = {c.i: c for c in cands if c.accepted}
    return accepted, appendix


def _is_plain_heading(body: list[_Line], i: int, body_bold: bool) -> bool:
    """An unnumbered heading: a short line in capitals or Title Case that stands alone above body text."""
    l = body[i]
    t = l.text.strip()
    if l.table or len(t) > 70 or len(t) < 3 or i + 1 >= len(body):
        return False
    if t.endswith((".", ",", ";", ":", "?", "!", "。", "，")) or not any(ch.isalpha() for ch in t):
        return False
    words = t.split()
    if len(words) > 8 or words[0][0].islower() or words[0][0].isdigit():
        return False
    letters = [ch for ch in t if ch.isalpha()]
    caps = len(letters) >= 3 and all(ch.isupper() for ch in letters if ch.lower() != ch.upper())
    cased = [w for w in words if w[0].isalpha() and w[0].lower() != w[0].upper()]
    small = {"of", "and", "the", "to", "in", "for", "or", "a", "an", "on", "by", "with", "de", "la", "el", "y", "und", "der", "die", "das", "des", "du", "et"}
    titled = bool(cased) and all(w[0].isupper() or w.lower() in small for w in cased)
    uncased = not cased                                    # scripts without capitals: layout has to carry it
    alone = l.first and l.nblock == 1
    strong_layout = (l.bold and not body_bold) or alone
    if i > 0 and not _ENDS_SENTENCE.search(body[i - 1].text.strip()) and not (l.first or body[i - 1].table):
        return False
    nxt = body[i + 1]
    if nxt.table:
        return strong_layout and (caps or titled or uncased)
    if len(nxt.text.strip()) < 25 and not _ENDS_SENTENCE.search(nxt.text.strip()):
        return False                                       # what follows is not body text
    if caps:
        return strong_layout or bool(i == 0 or _ENDS_SENTENCE.search(body[i - 1].text.strip()))
    return (titled or uncased) and strong_layout and len(words) <= 7


_CJK_CHAR = re.compile(r"[\u3000-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef]")


def _join_spans(lines: list[tuple[str, _Line | None]]) -> tuple[str, list]:
    """Join source lines into one text and remember which characters came from which line."""
    out = ""
    spans = []
    for t, l in lines:
        t = squash(t)
        if not t:
            continue
        if not out:
            start, out = 0, t
        elif out.endswith("-") and t[:1].islower() and not out.endswith(" -"):
            start, out = len(out), out + t           # keep hyphenated compounds such as thirty-six
        elif _CJK_CHAR.match(out[-1]) and _CJK_CHAR.match(t[0]):
            start, out = len(out), out + t           # Chinese and Japanese lines wrap without a space
        else:
            start, out = len(out) + 1, out + " " + t
        if l is not None:
            spans.append((start, len(out), l.page, l.row if l.table else -1, l.header if l.table else ""))
    return out, spans


def _build_clauses(body: list[_Line]) -> tuple[list[Clause], str]:
    accepted, appendix = _accept_numbered(body)
    lone = next(iter(accepted.values())) if len(accepted) == 1 else None
    if len(accepted) >= 2 or (lone is not None and lone.title and (lone.value[0] <= 1 or lone.i == 0) and body[lone.i].first):
        return _numbered_clauses(body, accepted, appendix), "numbered"
    bolds = sorted(l.bold for l in body)
    body_bold = bool(bolds) and bolds[len(bolds) // 2]
    heads = [i for i in range(len(body)) if _is_plain_heading(body, i, body_bold)]
    heads = [i for k, i in enumerate(heads) if not (k + 1 < len(heads) and heads[k + 1] == i + 1)]
    if len(heads) >= 2:
        return _headed_clauses(body, heads), "headings"
    return _paragraph_clauses(body), "paragraphs"


def _numbered_clauses(body, accepted, appendix) -> list[Clause]:
    clauses: list[Clause] = []
    current = Clause("0", "Preamble", "", 1, kind="preamble")
    buf: list[tuple[str, _Line | None]] = []
    prefix = ""
    seen_numbers: set[str] = set()

    def flush():
        nonlocal current, buf
        if current is not None:
            current.text, current.spans = _join_spans(buf)
            if current.text or current.heading or (current.title and current.kind != "preamble"):
                clauses.append(current)
        current, buf = None, []

    i = 0
    while i < len(body):
        l = body[i]
        t = l.text.strip()
        if i in appendix:
            flush()
            label, rest = appendix[i]
            prefix = label
            seen_numbers = set()
            current = Clause(label, rest, "", l.page, kind="appendix", heading=squash(t))
        elif i in accepted:
            c = accepted[i]
            flush()
            title, heading = c.title, squash(c.raw if not c.body else c.raw[:len(c.raw) - len(c.body)])
            if not title and not c.body and c.strong and i + 1 < len(body) and (i + 1) not in accepted \
                    and (i + 1) not in appendix and not body[i + 1].table and i + 2 < len(body):
                nt = body[i + 1].text.strip()
                if len(nt) <= 70 and _title_ok(nt) and not _ENDS_SENTENCE.search(nt):
                    title, heading = nt, heading + " " + squash(nt)      # "Article IV" with its title on the next line
                    i += 1
            number = c.number
            if prefix and (number in seen_numbers or c.value[0] <= 1 or f"{prefix} " in " ".join(seen_numbers)):
                number = f"{prefix} {c.number}"
            if prefix:
                seen_numbers.add(number)
            current = Clause(number, title, "", l.page, kind="numbered", heading=heading, style=c.style)
            if c.body:
                buf.append((c.body, l))
        else:
            buf.append((t, l))
        i += 1
    flush()
    return clauses


def _headed_clauses(body, heads) -> list[Clause]:
    clauses: list[Clause] = []
    hs = set(heads)
    current = Clause("0", "Preamble", "", 1, kind="preamble")
    buf: list = []
    n = 0
    for i, l in enumerate(body):
        if i in hs:
            if current is not None:
                current.text, current.spans = _join_spans(buf)
                if current.text or current.kind != "preamble":
                    clauses.append(current)
            n += 1
            t = squash(l.text)
            current, buf = Clause(f"#{n}", t.rstrip(":"), "", l.page, kind="heading", heading=t), []
        else:
            buf.append((l.text.strip(), l))
    current.text, current.spans = _join_spans(buf)
    if current.text or current.kind != "preamble":
        clauses.append(current)
    return clauses


_SENT_END = re.compile(r"(?<=[.!?;])\s+(?=[^\W\d_a-z\"“(])|(?<=[。！？；])")


def _paragraph_clauses(body) -> list[Clause]:
    """No headings to go by: one clause per paragraph, labelled by position."""
    paras: list[list[_Line]] = []
    right = max((l.bbox[2] for l in body), default=0.0)
    left = min((l.bbox[0] for l in body), default=0.0)
    for i, l in enumerate(body):
        new = not paras
        if not new:
            p = body[i - 1]
            h = max(1.0, p.bbox[3] - p.bbox[1])
            ended = bool(_ENDS_SENTENCE.search(p.text.strip()))
            if l.table and p.table and l.page == p.page:
                new = False
            elif l.table != p.table:
                new = True
            elif l.page != p.page:
                new = ended
            elif l.row >= 0 and l.row == p.row:
                new = False
            elif l.bbox[1] - p.bbox[3] > 0.45 * h:                      # a visible gap
                new = True
            elif l.bbox[1] < p.bbox[1] - h:                             # next column
                new = ended
            else:
                short = p.bbox[2] < right - 0.18 * (right - left)
                new = ended and (short or l.bbox[0] - p.bbox[0] > 6)
        if new:
            paras.append([l])
        else:
            paras[-1].append(l)
    clauses: list[Clause] = []
    for lines in paras:
        text, spans = _join_spans([(l.text.strip(), l) for l in lines])
        if not text:
            continue
        pieces = [(text, spans)]
        if len(text) > 1500 and not lines[0].table:                    # a wall of text: cut at sentence ends
            pieces, start = [], 0
            cuts = [m.end() for m in _SENT_END.finditer(text)] + [len(text)]
            for cut in cuts:
                if cut - start >= 500 or cut == len(text):
                    if cut > start:
                        seg = text[start:cut].rstrip()
                        pieces.append((seg, [(max(a, start) - start, min(b, start + len(seg)) - start, pg, row, hd)
                                             for a, b, pg, row, hd in spans if b > start and a < start + len(seg)]))
                    start = cut
        for seg, sp in pieces:
            words = seg.split()
            title = " ".join(words[:6]) + (" …" if len(words) > 6 else "")
            page = sp[0][2] if sp else lines[0].page
            clauses.append(Clause(f"¶ {len(clauses) + 1}", title, seg, page, kind="paragraph", spans=sp))
    return clauses


def _join(lines: list[str]) -> str:
    return _join_spans([(t, None) for t in lines])[0]


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

_LAYOUTS: dict[tuple, list] = {}
_LAYOUTS_LOCK = threading.Lock()


def _page_index(pdf_path: Path, doc, i: int):
    """(text, [(start, end, line, word box or None)], page height) for page i: its words in reading order."""
    try:
        st = pdf_path.stat()
        key = (str(pdf_path), st.st_mtime_ns, st.st_size)
    except OSError:
        key = (str(pdf_path), 0, 0)
    with _LAYOUTS_LOCK:
        pages = _LAYOUTS.get(key)
        if pages is None:
            if len(_LAYOUTS) >= 8:
                _LAYOUTS.pop(next(iter(_LAYOUTS)), None)
            pages = _LAYOUTS[key] = [None] * len(doc)
    if pages[i] is None:
        lines, _, facts = _page_layout(doc, doc[i], want_words=True)
        pages[i] = _index_lines(lines) + (facts["height"],)
    return pages[i]


def _index_lines(lines: list[_Line]):
    parts, index, pos = [], [], 0
    for l in lines:
        words = l.words or []
        if words and "".join(_nospace(w[4]) for w in words) == _nospace(l.full):
            units = [(w[4], (w[0], w[1], w[2], w[3])) for w in words if w[4].strip()]
        else:
            units = [(squash(l.full), None)]
        for text, box in units:
            if not text:
                continue
            index.append((pos, pos + len(text), l, box))
            parts.append(text)
            pos += len(text) + 1
    return " ".join(parts), index


def _boxes(index, start: int, end: int) -> list[tuple[float, float, float, float]]:
    """One box per line for characters [start, end) of an indexed page text."""
    per_line: dict[tuple, list[float]] = {}
    order = []
    for a, b, line, box in index:
        if b <= start or a >= end:
            continue
        if box is None:
            x0, y0, x1, y1 = line.real
        else:
            n = max(1, b - a)
            fa, fb = (max(start, a) - a) / n, (min(end, b) - a) / n
            x0, x1 = box[0] + fa * (box[2] - box[0]), box[0] + fb * (box[2] - box[0])
            y0, y1 = box[1], box[3]
        cur = per_line.get(line.key)
        if cur is None:
            per_line[line.key] = [x0, y0, x1, y1]
            order.append(line.key)
        else:
            cur[0], cur[1], cur[2], cur[3] = min(cur[0], x0), min(cur[1], y0), max(cur[2], x1), max(cur[3], y1)
    return [tuple(per_line[k]) for k in order]


def _fractions(rects, page) -> list[list[float]]:
    """Boxes as fractions of the page as it is displayed (a rotated page is rendered rotated)."""
    w, h = page.rect.width, page.rect.height
    out = []
    for r in rects:
        r = pymupdf.Rect(r)
        if page.rotation:
            r = r * page.rotation_matrix
            r.normalize()
        out.append([round(r.x0 / w, 5), round(r.y0 / h, 5), round(r.x1 / w, 5), round(r.y1 / h, 5)])
    return out


def locate(pdf_path: str | Path, quote: str, hint_page: int | None = None, within: str = "") -> list[dict]:
    """Return [{page, rects:[[x0,y0,x1,y1] as fractions of the page]}] for an exact quote.

    The quote must be found verbatim: same characters, same case, whitespace ignored, and not starting or
    ending inside a word or number. If it is not found, the result is empty; callers report that instead
    of drawing an approximate box. A quote that runs over a page break comes back as two entries, one per page.
    `within` (optional, e.g. the sentence or clause the quote was taken from) keeps only the occurrences
    inside that passage when the same words appear more than once on a page.
    """
    q = squash(quote)
    if len(q) < 3:
        return []
    pdf_path = Path(pdf_path)
    doc = pymupdf.open(pdf_path)
    try:
        # a clause can run over a page break, so look on the hinted page, then the following pages, then the rest
        order = list(range(len(doc)))
        if hint_page and 1 <= hint_page <= len(doc):
            order = list(range(hint_page - 1, len(doc))) + list(range(0, hint_page - 1))
        # whole words first; a passage cut in the middle of a word (a character window) is looked for after that
        for strict in (True, False):
            found = _locate(doc, pdf_path, q, _quote_re(q, strict), order, squash(within))
            if found:
                return found
        return []
    finally:
        doc.close()


def _locate(doc, pdf_path: Path, q: str, pat: re.Pattern, order: list[int], within: str) -> list[dict]:
    ctx = _quote_re(within, False) if within and q in within else None
    for i in order:
        text, index, _ = _page_index(pdf_path, doc, i)
        hits = [(m.start(), m.end()) for m in pat.finditer(text)]
        if not hits:
            continue
        if ctx is not None:
            zones = [(m.start(), m.end()) for m in ctx.finditer(text)]
            hits = [h for h in hits if any(a <= h[0] and h[1] <= b for a, b in zones)] or hits
        page = doc[i]
        mine = [pymupdf.Rect(r) for a, b in hits for r in _boxes(index, a, b)]
        rects = mine
        try:                                   # MuPDF's own search gives tighter boxes; keep those that agree
            found = [r for r in page.search_for(q, quads=False)
                     if any(abs(r & m) > 0.3 * min(abs(r), abs(m)) for m in mine if abs(r) and abs(m))]
            if found and all(any(abs(f & m) > 0 for f in found) for m in mine):
                rects = found
        except Exception:
            pass
        return [{"page": i + 1, "rects": _fractions(rects, page)}]
    # not on any one page: the quote may straddle a page break (headers and footers sit in between)
    for i in order:
        if i + 1 >= len(doc):
            continue
        _, ia, ha = _page_index(pdf_path, doc, i)
        _, ib, hb = _page_index(pdf_path, doc, i + 1)
        la = [l for l in dict.fromkeys(e[2] for e in ia) if not _in_band(l, ha, "bottom")]
        lb = [l for l in dict.fromkeys(e[2] for e in ib) if not _in_band(l, hb, "top")]
        ta, xa = _index_lines(la)
        tb, xb = _index_lines(lb)
        hit = _straddles(pat, ta, tb)
        if hit is None:
            continue
        start, end, off = hit
        return [{"page": i + 1, "rects": _fractions(_boxes(xa, start, len(ta)), doc[i])},
                {"page": i + 2, "rects": _fractions(_boxes(xb, 0, end - off), doc[i + 1])}]
    return []


def content_box(pdf_paths: list[str | Path], pad: float = 22.0) -> tuple[float, float, float, float] | None:
    """One crop box, as page fractions, that holds the text of every page of every document.

    Margins carry nothing to read; cropping them makes two pages side by side legible.
    Returns None when page sizes differ, in which case pages are shown whole.
    """
    size, box = None, None
    for path in pdf_paths:
        doc = pymupdf.open(path)
        try:
            for page in doc:
                wh = (round(page.rect.width), round(page.rect.height))
                if size is None:
                    size = wh
                elif wh != size:
                    return None
                for b in page.get_text("blocks"):
                    if not str(b[4]).strip():
                        continue
                    r = pymupdf.Rect(b[:4])
                    box = r if box is None else box | r
        finally:
            doc.close()
    if size is None or box is None:
        return None
    w, h = size
    x0, y0, x1, y1 = max(0, box.x0 - pad), max(0, box.y0 - pad), min(w, box.x1 + pad), min(h, box.y1 + pad)
    if (x1 - x0) > 0.97 * w and (y1 - y0) > 0.97 * h:
        return None
    return (x0 / w, y0 / h, x1 / w, y1 / h)


def remap_rects(obj, crop: tuple[float, float, float, float]):
    """Rewrite every 'rects' list (fractions of the full page) into fractions of the cropped page, in place."""
    cx0, cy0, cx1, cy1 = crop
    cw, ch = cx1 - cx0, cy1 - cy0
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "rects" and isinstance(v, list):
                obj[k] = [[round((r[0] - cx0) / cw, 5), round((r[1] - cy0) / ch, 5),
                           round((r[2] - cx0) / cw, 5), round((r[3] - cy0) / ch, 5)] for r in v]
            else:
                remap_rects(v, crop)
    elif isinstance(obj, list):
        for v in obj:
            remap_rects(v, crop)


def render_pages(pdf_path: str | Path, out_dir: str | Path, prefix: str, dpi: int = 132,
                 crop: tuple[float, float, float, float] | None = None) -> list[dict]:
    """Rasterise every page (annotations included) and return [{image,width,height}]."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open(pdf_path)
    pages = []
    try:
        for i, page in enumerate(doc, start=1):
            clip = None
            if crop:
                w, h = page.rect.width, page.rect.height
                clip = pymupdf.Rect(crop[0] * w, crop[1] * h, crop[2] * w, crop[3] * h)
            pix = page.get_pixmap(dpi=dpi, annots=True, clip=clip)
            name = f"{prefix}-p{i}.png"
            pix.save(out_dir / name)
            pages.append({"image": name, "width": pix.width, "height": pix.height})
    finally:
        doc.close()
    return pages
