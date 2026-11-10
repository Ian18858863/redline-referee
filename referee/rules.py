"""Deterministic comparison of two versions of the same agreement.

No model. Clauses are aligned, then each aligned pair is diffed word by word.
Every difference becomes a Hunk with exact quotes from both versions and a
class: substantive | administrative | formatting | spelling.

This layer exists so that the product never depends on a model noticing that
30 became 90. Clocked Compare is run next to it and the two are cross-labelled.

Rules of thumb the code keeps to:
  * a letter, digit, currency sign, comparison sign or plus/minus of any script is never "formatting";
  * a one-word edit is a spelling fix only when the old word is not an English word and the new one is;
  * clauses are matched by what they say, not by their number, so an inserted, deleted or moved clause
    is reported as that, and a clause that only changed its number as "renumbered".
"""
from __future__ import annotations

import gzip
import re
import unicodedata
from dataclasses import dataclass, field, replace
from difflib import SequenceMatcher
from pathlib import Path

from .textdoc import Clause, TextDoc, squash

_CJK = r"぀-ヿ㐀-䶿一-鿿豈-﫿"
_LETTER = rf"(?:(?![{_CJK}])[^\W\d_])"
_CUR = r"$€£¥₹₩₽₺₫฿"
_SIGN = rf"(?:(?<![\w.,)])[-+−±](?=[{_CUR}]?\d))"
TOKEN = re.compile(rf"{_SIGN}?[{_CUR}]?\d[\d,]*(?:\.\d+)?%?|{_LETTER}+(?:['’-]{_LETTER}+)*|[{_CJK}]|\S")
NUMBER_WORDS = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
                "sixteen seventeen eighteen nineteen twenty thirty forty fifty sixty seventy eighty ninety hundred "
                "thousand and").split()
_NW = "|".join(NUMBER_WORDS)
SPELLED = re.compile(rf"(?:\b(?:{_NW})\b[\s-]*)+\(\s*(\d[\d,]*)\s*\)", re.I)
_SPELLED_PAIR = re.compile(rf"(?P<words>(?:\b(?:{_NW})\b[\s-]*)+)\(\s*(?P<digits>\d[\d,]*)\s*\)", re.I)
_NWC = "|".join(w for w in NUMBER_WORDS if w != "and")
_WORD_NUMBER = re.compile(rf"\b(?:{_NWC})\b(?:(?:[\s-]+and)?[\s-]+(?:{_NWC})\b)*", re.I)
UNITS = r"(?:business\s+)?(?:hour|day|week|month|year)s?"
DURATION = re.compile(rf"(\d[\d,]*)\s*[-\s]?\s*({UNITS})\b", re.I)
_ISO = r"(?:USD|EUR|GBP|JPY|CNY|RMB|CHF|CAD|AUD|INR|HKD|SGD)"
_NUM = r"\d(?:[\d,]*\d)?(?:\.\d+)?"                 # 184,000 and 1.5, without a comma that only ends a phrase
MONEY = re.compile(rf"{_SIGN}?[{_CUR}]\s?{_NUM}|\b{_ISO}\s?{_NUM}|\d(?:[\d.,]*\d)?\s?(?:[{_CUR}]|{_ISO}\b)")
PERCENT = re.compile(rf"{_SIGN}?\d+(?:[.,]\d+)?\s?%")
NUMBER = re.compile(rf"{_SIGN}?{_NUM}")
ADDRESSISH = re.compile(r"\b(street|st\.|avenue|ave\.|road|rd\.|suite|floor|row|boulevard|blvd|drive|lane|p\.?o\.? box|\d{5}(?:-\d{4})?)\b", re.I)
# A street name is not enough to exempt a mixed address/notice-effect rewrite.
_NOTICE_EFFECT = re.compile(r"\b(?:shall|must|may|can|cannot|without|unless|not|no|only|waiv\w*|"
                            r"consent|effective|deemed|receipt|received|delivery|delivered|"
                            r"registered|certified|electronic|business\s+days?)\b", re.I)
_EMAIL_WORD = re.compile(r"\be-?mail\b", re.I)
CONTEXT_TOKENS = 4
CONTEXT_TOKENS_CJK = 10              # one token is one character there
SENTENCE_CAP = 1200                  # several sentences sent as one passage stop at a sentence end near this length
_DASHES = "-‐‑‒–—−"
_SYMBOLS = set("<>≤≥=≠≈±+×÷")
_CJK_NUM = re.compile(r"[〇零一二三四五六七八九十百千万亿两壹贰叁肆伍陆柒捌玖拾佰仟萬億]+")
_HAS_CJK = re.compile(rf"[{_CJK}]")


@dataclass
class Hunk:
    clause: str                    # display label of the clause (v2's when there is one)
    title: str
    cls: str                       # substantive | administrative | formatting | spelling
    kind: str                      # duration | money | percent | number | symbol | wording | added | removed | address
    #                                | format | typo | renumbered | moved | header
    v1_quote: str
    v2_quote: str
    v1_changed: str
    v2_changed: str
    summary: str
    v1_page: int = 0
    v2_page: int = 0
    why: str = ""
    extra: dict = field(default_factory=dict)
    v1_sentence: str = ""          # the whole sentence(s) of v1 that hold the change (sent to Verify)
    v2_sentence: str = ""
    v1_uid: str | None = None      # Clause.uid in v1; None when the clause was added
    v2_uid: str | None = None      # Clause.uid in v2; None when the clause was removed
    located: bool = True           # False when a quote cannot be found verbatim on the page (no box is drawn)
    v1_located: bool = True
    v2_located: bool = True
    capped: bool = False           # True when a multi-sentence passage was cut at a sentence end (see sentences())

    @property
    def substantive(self) -> bool:
        return self.cls == "substantive"


def tokens(text: str) -> list[tuple[str, int, int]]:
    return [(m.group(0), m.start(), m.end()) for m in TOKEN.finditer(text)]


# ------------------------------------------------------------ number words

_SMALL = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen "
                                     "fourteen fifteen sixteen seventeen eighteen nineteen".split())}
_TENS = {w: 10 * i for i, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split(), start=2)}


def words_value(phrase: str) -> int | None:
    """"thirty-six" → 36, "one hundred and twenty" → 120. None when it is not a number in words."""
    total = cur = 0
    seen = False
    for w in re.split(r"[\s-]+", phrase.strip().lower()):
        if not w or w == "and":
            continue
        if w in _SMALL:
            cur += _SMALL[w]
        elif w in _TENS:
            cur += _TENS[w]
        elif w == "hundred":
            cur = max(cur, 1) * 100
        elif w == "thousand":
            total += max(cur, 1) * 1000
            cur = 0
        else:
            return None
        seen = True
    return total + cur if seen else None


def _digits_value(s: str) -> int | None:
    try:
        return int(s.replace(",", ""))
    except ValueError:
        return None


def _fold_spelled(s: str) -> str:
    """"thirty (30)" → "30", but only when the words and the digits say the same number."""
    def sub(m):
        return m.group("digits") if words_value(m.group("words")) == _digits_value(m.group("digits")) else m.group(0)
    return _SPELLED_PAIR.sub(sub, s)


def _word_numbers(s: str) -> list[tuple[int, str]]:
    out = []
    for m in _WORD_NUMBER.finditer(s):
        v = words_value(m.group(0))
        if v is not None:
            out.append((v, m.group(0)))
    return out


# -------------------------------------------------------------- normalising

_THOUSANDS = re.compile(r"(?<![\d.,])\d{1,3}(?:,\d{3})+(?![\d,])")


def normalise(s: str) -> str:
    """What is left of a passage once only formatting is ignored.

    Dropped: case, whitespace, quotes, brackets, sentence punctuation, hyphens inside words, thousands commas.
    Kept: every letter and digit of every script, currency signs, %, comparison and maths signs, a minus or plus
    in front of a number, and the decimal mark (so 1,5 is neither 15 nor 1.5).
    """
    s = unicodedata.normalize("NFKC", s)
    s = _fold_spelled(s)
    s = s.casefold().replace("&", " and ").replace("e-mail", "email")
    s = _THOUSANDS.sub(lambda m: m.group(0).replace(",", ""), s)
    out = []
    n = len(s)
    for i, ch in enumerate(s):
        if ch.isalnum():
            out.append(ch)
            continue
        if ch.isspace():
            continue
        prev = s[i - 1] if i else " "
        nxt = s[i + 1] if i + 1 < n else " "
        if ch in _DASHES:
            signs = nxt.isdigit() or nxt in _CUR or (nxt == "." and i + 2 < n and s[i + 2].isdigit())
            if signs and (prev.isspace() or prev in "([{" or prev.isdigit()):
                out.append("-")            # a minus sign, or a range between two numbers
            continue
        if ch in ".,/":
            if prev.isdigit() and nxt.isdigit():
                out.append(ch)
            continue
        cat = unicodedata.category(ch)
        if ch in "%‰°" or ch in _SYMBOLS or cat in ("Sc", "Sm") or cat[0] in "LNM":
            out.append(ch)
    return "".join(out)


def _values(s: str) -> dict[str, list[str]]:
    s = SPELLED.sub(lambda m: m.group(1), s)
    money = [squash(m) for m in MONEY.findall(s)]
    pct = [m.replace(" ", "") for m in PERCENT.findall(s)]
    dur = [f"{n} {u.lower().rstrip('s')}s" if n != "1" else f"1 {u.lower().rstrip('s')}" for n, u in DURATION.findall(s)]
    rest = MONEY.sub(" ", PERCENT.sub(" ", DURATION.sub(" ", s)))
    nums = NUMBER.findall(rest)
    return {"money": money, "percent": pct, "duration": dur, "number": nums}


def _lev(a: str, b: str) -> int:
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


# --------------------------------------------------------------- dictionary

_WORDS: set[str] | None = None
_EXTRA_WORDS = set((
    "email emails website websites online internet software database databases username login logins upload uploads "
    "download downloads licensor licensors licensee licensees lessor lessee sublicense sublicensee sublicensor "
    "indemnitor indemnitee payor payer payee obligor obligee assignor assignee transferor transferee grantor grantee "
    "franchisor franchisee mortgagor mortgagee bailor bailee pledgor pledgee promisor promisee offeror offeree "
    "optionor optionee consignor consignee guarantor subprocessor subprocessors processor controller subcontractor "
    "subcontractors affiliate affiliates deliverable deliverables onboarding uptime downtime timeline workflow "
    "minimum maximum minimal maximal").split())
_STOP = set("the of and to in or by any shall will this that for with be is not may its each as on at are an".split())


def _load_words() -> set[str]:
    """The bundled English word list (front-coded, gzip). Empty when the file is missing: then nothing is 'spelling'."""
    global _WORDS
    if _WORDS is None:
        words: set[str] = set()
        path = Path(__file__).with_name("words_en.txt.gz")
        try:
            prev = ""
            with gzip.open(path, "rt", encoding="ascii") as f:
                for line in f:
                    line = line.rstrip("\n")
                    if not line:
                        continue
                    prev = prev[:ord(line[0]) - 48] + line[1:]
                    words.add(prev)
        except OSError:
            words = set()
        _WORDS = words | _EXTRA_WORDS if words else set()
    return _WORDS


def is_english_word(word: str) -> bool:
    """Dictionary word, simple inflections included (plural, -ed, -ing, -ly, -er, -est)."""
    w = word.lower()
    words = _load_words()
    if not words or not w.isascii() or not w.isalpha():
        return False
    if w in words:
        return True
    stems = []
    if w.endswith("ies"):
        stems.append(w[:-3] + "y")
    if w.endswith("es"):
        stems.append(w[:-2])
    if w.endswith("s") and not w.endswith("ss"):
        stems.append(w[:-1])
    if w.endswith("ied"):
        stems.append(w[:-3] + "y")
    if w.endswith("ed"):
        stems += [w[:-2], w[:-1]] + ([w[:-3]] if len(w) > 4 and w[-3] == w[-4] else [])
    if w.endswith("ing"):
        stems += [w[:-3], w[:-3] + "e"] + ([w[:-4]] if len(w) > 5 and w[-4] == w[-5] else [])
    if w.endswith("ly"):
        stems += [w[:-2], w[:-3] + "le"] + ([w[:-3] + "y"] if w.endswith("ily") else [])
    if w.endswith("er"):
        stems += [w[:-2], w[:-1]]
    if w.endswith("est"):
        stems += [w[:-3], w[:-2]]
    return any(len(s) >= 3 and s in words for s in stems)


def is_english(text: str) -> bool:
    """Is this passage English? Decided by function words, which no other language shares in bulk."""
    ws = re.findall(r"[^\W\d_]+", text[:30000])
    if len(ws) < 8:
        return False
    low = [w.lower() for w in ws]
    if sum(1 for w in low if not w.isascii()) > 0.06 * len(low):
        return False
    return sum(1 for w in low if w in _STOP) >= 0.12 * len(low) and low.count("the") >= 0.015 * len(low)


def _role_swap(a: str, b: str) -> bool:
    """Lessor / Lessee, Employer / Employee: the two ends of one relationship."""
    a, b = a.lower(), b.lower()
    ends = ("or", "er", "ee")
    return (a[-2:] in ends and b[-2:] in ends and a[-2:] != b[-2:] and a[:-2] == b[:-2] and "ee" in (a[-2:], b[-2:]))


# ---------------------------------------------------------------- alignment

def _match_key(c: Clause) -> str:
    return normalise(c.text if c.kind in ("paragraph", "preamble") else f"{c.title} {c.text}")


def _words_of(c: Clause) -> list[str]:
    return [t.casefold() for t, _, _ in tokens(c.text) if t[0].isalnum() or len(t) > 1 or t in _SYMBOLS]


def _titled(c: Clause) -> bool:
    return c.kind in ("numbered", "appendix", "heading") and bool(c.title.strip())


def _tail_start(c: Clause) -> int | None:
    """Where a trailing table (a signature block, as a rule) begins in the last clause of a document."""
    start = None
    for s, e, page, row, header in reversed(c.spans):
        if row < 0:
            break
        start = s
    return start if start else None


def _views(a: TextDoc, b: TextDoc) -> tuple[list[Clause], list[Clause]]:
    """The clauses to compare. A signature block hangs off whichever clause comes last; when the last clause
    is not the same clause in both versions and the block itself is unchanged, it is set aside."""
    ca, cb = list(a.clauses), list(b.clauses)
    if ca and cb:
        ta, tb = _tail_start(ca[-1]), _tail_start(cb[-1])
        if ta and tb and normalise(ca[-1].text[ta:]) == normalise(cb[-1].text[tb:]) \
                and (len(ca) - 1, len(cb) - 1) not in _match(ca, cb):
            ca[-1] = replace(ca[-1], text=ca[-1].text[:ta].rstrip(), spans=[x for x in ca[-1].spans if x[0] < ta])
            cb[-1] = replace(cb[-1], text=cb[-1].text[:tb].rstrip(), spans=[x for x in cb[-1].spans if x[0] < tb])
    return ca, cb


def _match(ac: list[Clause], bc: list[Clause]) -> list[tuple[int, int]]:
    """Pairs (index in a, index in b) of clauses that are the same clause. Best pairs first, each clause once."""
    pairs: list[tuple[int, int]] = []
    used_a: set[int] = set()
    used_b: set[int] = set()
    # 1. clauses that read the same once formatting is ignored, taken in order
    by_key: dict[str, list[int]] = {}
    keys_b = [_match_key(c) for c in bc]
    for j, k in enumerate(keys_b):
        by_key.setdefault(k, []).append(j)
    keys_a = [_match_key(c) for c in ac]
    for i, c in enumerate(ac):                      # the same text under the same label first (boilerplate repeats)
        js = by_key.get(keys_a[i]) or []
        j = next((j for j in js if bc[j].number == c.number), None)
        if j is not None:
            js.remove(j)
            pairs.append((i, j))
            used_a.add(i)
            used_b.add(j)
    for i, c in enumerate(ac):
        js = by_key.get(keys_a[i])
        if js and i not in used_a:
            j = js.pop(0)
            pairs.append((i, j))
            used_a.add(i)
            used_b.add(j)
    # 2. the rest: real similarity of the words, every candidate pair scored, best first
    rest_a = [i for i in range(len(ac)) if i not in used_a]
    rest_b = [j for j in range(len(bc)) if j not in used_b]
    if rest_a and rest_b:
        wa = {i: _words_of(ac[i]) for i in rest_a}
        wb = {j: _words_of(bc[j]) for j in rest_b}
        sa = {i: set(w) for i, w in wa.items()}
        sb = {j: set(w) for j, w in wb.items()}
        ta = {i: normalise(ac[i].title) for i in rest_a}
        tb = {j: normalise(bc[j].title) for j in rest_b}
        na, nb = max(1, len(ac) - 1), max(1, len(bc) - 1)
        scored: list[tuple[float, float, int, int]] = []
        for i in rest_a:
            ca = ac[i]
            cands = []
            for j in rest_b:
                cb = bc[j]
                union = len(sa[i] | sb[j])
                jac = len(sa[i] & sb[j]) / union if union else 0.0
                title = 0.0
                if _titled(ca) and _titled(cb):
                    title = 1.0 if ta[i] == tb[j] else SequenceMatcher(None, ta[i], tb[j]).ratio()
                both_pre = ca.kind == "preamble" and cb.kind == "preamble"
                if jac >= 0.2 or title >= 0.6 or both_pre:
                    cands.append((jac, title, both_pre, j))
            cands.sort(key=lambda x: (-max(x[0], x[1], float(x[2])), abs(i / na - x[3] / nb)))
            for jac, title, both_pre, j in cands[:8]:
                cb = bc[j]
                body = SequenceMatcher(None, wa[i], wb[j], autojunk=False).ratio() if (wa[i] or wb[j]) else 1.0
                score = body
                if title >= 0.9:
                    score = max(score, 0.6 + 0.4 * body)        # same heading: the same clause, however much was rewritten
                elif title >= 0.6 and ca.number == cb.number:
                    score = max(score, 0.55 + 0.4 * body)
                if both_pre:
                    score = max(score, 0.6)
                if score >= 0.5:
                    scored.append((score, abs(i / na - j / nb), i, j))
        scored.sort(key=lambda x: (-x[0], x[1], x[2], x[3]))
        for _, _, i, j in scored:
            if i not in used_a and j not in used_b:
                pairs.append((i, j))
                used_a.add(i)
                used_b.add(j)
    return sorted(pairs)


def align(a: TextDoc, b: TextDoc) -> list[tuple[Clause | None, Clause | None]]:
    """[(v1 clause, v2 clause)] in v1 order; (clause, None) was removed, (None, clause) was added."""
    ac, bc = _views(a, b)
    matched = dict(_match(ac, bc))
    pairs: list[tuple[Clause | None, Clause | None]] = []
    for i, ca in enumerate(ac):
        pairs.append((ca, bc[matched[i]] if i in matched else None))
    taken = set(matched.values())
    for j, cb in enumerate(bc):
        if j not in taken:
            pairs.append((None, cb))
    return pairs


def _moved(order: list[int]) -> set[int]:
    """Positions (in `order`) that are not part of the longest increasing run: those clauses moved."""
    if not order:
        return set()
    best = [1] * len(order)
    back = [-1] * len(order)
    for i in range(len(order)):
        for j in range(i):
            if order[j] < order[i] and best[j] + 1 > best[i]:
                best[i], back[i] = best[j] + 1, j
    k = max(range(len(order)), key=lambda i: (best[i], -i))
    keep = set()
    while k >= 0:
        keep.add(k)
        k = back[k]
    return set(range(len(order))) - keep


# ----------------------------------------------------------------- sentences

_BOUNDARY = re.compile(r"(?<!\bInc)(?<!\bLtd)(?<!\bCo)(?<!\bCorp)(?<!\bNo)(?<!U\.S)[.;]\s+(?=[^\W\d_a-z]|[\"“(])"
                       r"|(?<=[。！？；])(?=.)")


def sentences(text: str) -> list[str]:
    """Every sentence of a passage, whole. Use it to send a long clause in batches."""
    cuts = [0] + [m.end() for m in _BOUNDARY.finditer(text)] + [len(text)]
    return [s for s in (text[a:b].strip() for a, b in zip(cuts, cuts[1:])) if s]


def sentence_at(text: str, a: int, b: int) -> str:
    """The whole sentence(s) of text covering characters [a, b). Never cut short."""
    cuts = [0] + [m.end() for m in _BOUNDARY.finditer(text)] + [len(text)]
    start = max(c for c in cuts if c <= a)
    end = min((c for c in cuts if c >= max(b, a + 1)), default=len(text))
    return text[start:end].strip()


def _passage(text: str) -> tuple[str, bool]:
    """A clause as one passage: all of it, or its leading sentences up to SENTENCE_CAP. (passage, was it cut)"""
    if len(text) <= SENTENCE_CAP:
        return text, False
    out = ""
    for s in sentences(text):
        if out and len(out) + 1 + len(s) > SENTENCE_CAP:
            break
        out = f"{out} {s}" if out else s
    return out, len(out) < len(text.strip())


def _head(text: str, limit: int = 400) -> str:
    """The opening of a clause as a quote: whole words, at most `limit` characters."""
    if len(text) <= limit:
        return text
    cut = text.rfind(" ", 0, limit + 1)
    return text[:cut if cut > limit // 2 else limit]


def _span(toks, i1: int, i2: int, text: str) -> tuple[int, int]:
    if not toks:
        return 0, 0
    if i2 > i1:
        return toks[i1][1], toks[i2 - 1][2]
    pos = toks[i1][1] if i1 < len(toks) else len(text)
    return pos, pos


def _snippet(text: str, toks, i1: int, i2: int, context: int = CONTEXT_TOKENS) -> tuple[str, str]:
    """(quote with context, exactly-changed text) for token range [i1, i2)."""
    if not toks:
        return "", ""
    lo = max(0, i1 - context)
    hi = min(len(toks), i2 + context)
    quote = text[toks[lo][1]:toks[hi - 1][2]] if hi > lo else ""
    changed = text[toks[i1][1]:toks[i2 - 1][2]] if i2 > i1 else ""
    return quote, changed


def _row(c: Clause, a: int, b: int) -> tuple[int, int, str] | None:
    """(start, end, header) of the table row that holds characters [a, b) of the clause, if they sit in one row."""
    hit = None
    for s, e, page, row, header in c.spans:
        if row >= 0 and s <= a and max(b, a + 1) <= e + 1:
            hit = (page, row, header)
            break
    if hit is None:
        for s, e, page, row, header in c.spans:
            if row >= 0 and s <= a <= e:
                hit = (page, row, header)
                break
    if hit is None:
        return None
    cells = [(s, e) for s, e, page, row, header in c.spans if (page, row) == hit[:2]]
    start, end = min(s for s, _ in cells), max(e for _, e in cells)
    if not (start <= a and b <= end):
        return None
    return start, end, hit[2]


def _page_at(c: Clause, pos: int) -> int:
    for s, e, page, row, header in c.spans:
        if s <= pos <= e:
            return page
    return c.page


def _small(ca: Clause, cb: Clause, ta, tb, part, english) -> bool:
    """Is this single edit, taken alone, a spelling fix or formatting?"""
    i1, i2, j1, j2 = part
    q1, c1 = _snippet(ca.text, ta, i1, i2)
    q2, c2 = _snippet(cb.text, tb, j1, j2)
    return _classify(ca, cb, q1, q2, c1, c2, english).cls in ("spelling", "formatting")


def diff_clause(ca: Clause, cb: Clause, english: bool | None = None) -> list[Hunk]:
    if squash(ca.text) == squash(cb.text):
        return []
    if english is None:
        english = is_english(ca.text + " " + cb.text)
    ta, tb = tokens(ca.text), tokens(cb.text)
    sm = SequenceMatcher(None, [t[0] for t in ta], [t[0] for t in tb], autojunk=False)
    ops = [op for op in sm.get_opcodes()]
    # merge changes separated by at most two unchanged tokens
    groups: list[list[int]] = []
    parts: list[list[tuple[int, int, int, int]]] = []
    for tag, i1, i2, j1, j2 in ops:
        if tag == "equal":
            continue
        if groups and i1 - groups[-1][1] <= 2 and j1 - groups[-1][3] <= 2:
            groups[-1][1], groups[-1][3] = i2, j2
            parts[-1].append((i1, i2, j1, j2))
        else:
            groups.append([i1, i2, j1, j2])
            parts.append([(i1, i2, j1, j2)])
    # neighbouring edits that are each a spelling fix or formatting on their own stay separate
    final: list[tuple[int, int, int, int]] = []
    for g, ps in zip(groups, parts):
        if len(ps) > 1 and all(i2 - i1 <= 1 and j2 - j1 <= 1 and _small(ca, cb, ta, tb, p, english) for p in ps
                               for i1, i2, j1, j2 in [p]) and any(i2 > i1 and j2 > j1 for i1, i2, j1, j2 in ps):
            final.extend(ps)
        else:
            final.append(tuple(g))
    hunks = []
    for i1, i2, j1, j2 in final:
        near = "".join(t[0] for t in ta[max(0, i1 - 3):i2 + 3] + tb[max(0, j1 - 3):j2 + 3])
        ctx = CONTEXT_TOKENS_CJK if _HAS_CJK.search(near) else CONTEXT_TOKENS
        q1, c1 = _snippet(ca.text, ta, i1, i2, ctx)
        q2, c2 = _snippet(cb.text, tb, j1, j2, ctx)
        if not c1 and ta:                      # pure insertion: quote the surrounding words in v1
            q1, _ = _snippet(ca.text, ta, max(0, i1 - 1), min(len(ta), i1 + 1), ctx)
        if not c2 and tb:
            q2, _ = _snippet(cb.text, tb, max(0, j1 - 1), min(len(tb), j1 + 1), ctx)
        s1, s2 = _span(ta, i1, i2, ca.text), _span(tb, j1, j2, cb.text)
        r1, r2 = _row(ca, *s1), _row(cb, *s2)
        extra = {}
        if r1 and r2:                          # a table cell: the row is the readable unit
            q1, q2 = ca.text[r1[0]:r1[1]], cb.text[r2[0]:r2[1]]
            extra = {"table_row": {"v1": q1, "v2": q2, "header": r2[2] or r1[2]}}
        h = _classify(ca, cb, q1, q2, c1, c2, english)
        h.v1_page, h.v2_page = _page_at(ca, s1[0]), _page_at(cb, s2[0])
        if extra:
            h.extra = {**h.extra, **extra}
            h.v1_sentence, h.v2_sentence = q1, q2
        else:
            h.v1_sentence = sentence_at(ca.text, *s1)
            h.v2_sentence = sentence_at(cb.text, *s2)
        hunks.append(h)
    return hunks


def _classify(ca: Clause, cb: Clause, q1: str, q2: str, c1: str, c2: str, english: bool | None = None) -> Hunk:
    base = dict(clause=cb.number or ca.number, title=cb.title or ca.title, v1_quote=q1, v2_quote=q2,
                v1_changed=c1, v2_changed=c2, v1_page=ca.page, v2_page=cb.page)
    if normalise(q1) == normalise(q2) or normalise(c1) == normalise(c2):
        return Hunk(cls="formatting", kind="format", summary="Formatting only", why="Same words and numbers once formatting is ignored.", **base)
    w1, w2 = c1.strip(), c2.strip()
    one_word = (w1.isalpha() and w2.isalpha() and min(len(w1), len(w2)) >= 5 and w1[0].lower() == w2[0].lower()
                and _lev(w1.lower(), w2.lower()) <= 2)
    real_words = ""
    if one_word:
        if english is None:
            english = is_english(ca.text + " " + cb.text)
        numberish = words_value(w1) is not None or words_value(w2) is not None
        if (english and w1.isascii() and w2.isascii() and not numberish and not _role_swap(w1, w2)
                and not is_english_word(w1) and is_english_word(w2)):
            return Hunk(cls="spelling", kind="typo", summary=f"“{w1}” → “{w2}”",
                        why="One word, same first letter, at most two letters apart: treated as a spelling fix. Compare can overrule this.", **base)
        if english and is_english_word(w1) and is_english_word(w2):
            real_words = f"“{w1}” and “{w2}” are both real words: a change of wording, not a spelling fix."
    v1, v2 = _values(q1), _values(q2)
    same_terms = all(v1[k] == v2[k] for k in ("money", "percent", "duration"))
    if (same_terms and re.search(r"notice", cb.title, re.I) and (ADDRESSISH.search(c1) or ADDRESSISH.search(c2))
            and not _NOTICE_EFFECT.search(c1 + " " + c2)
            and len(_EMAIL_WORD.findall(c1)) == len(_EMAIL_WORD.findall(c2))):
        return Hunk(cls="administrative", kind="address", summary="Notice address updated",
                    why="Contact details inside a notices clause.", **base)
    for kind in ("money", "percent", "duration", "number"):
        if v1[kind] != v2[kind]:
            old = ", ".join(x for x in v1[kind] if x not in v2[kind]) or ", ".join(v1[kind]) or "none"
            new = ", ".join(x for x in v2[kind] if x not in v1[kind]) or ", ".join(v2[kind]) or "none"
            return Hunk(cls="substantive", kind=kind, summary=f"{old} → {new}",
                        why=f"A {kind} value differs between the two versions.", extra={"old": old, "new": new}, **base)
    n1, n2 = _word_numbers(c1), _word_numbers(c2)
    if n1 and n2 and [v for v, _ in n1] != [v for v, _ in n2]:
        old, new = ", ".join(p for _, p in n1), ", ".join(p for _, p in n2)
        unit = re.search(rf"{re.escape(n2[-1][1])}\s*(?:\(\s*\d[\d,]*\s*\)\s*)?[-\s]?\s*({UNITS})\b", q2, re.I)
        kind = "duration" if unit else "number"
        if unit:
            old, new = f"{old} {unit.group(1).lower()}", f"{new} {unit.group(1).lower()}"
        return Hunk(cls="substantive", kind=kind, summary=f"{old} → {new}",
                    why="A number written in words differs between the two versions.", extra={"old": old, "new": new}, **base)
    k1, k2 = _CJK_NUM.findall(c1), _CJK_NUM.findall(c2)
    if k1 and k2 and k1 != k2:
        a1, a2 = _CJK_NUM.findall(q1), _CJK_NUM.findall(q2)
        old = "、".join(x for x in a1 if x not in a2) or "、".join(k1)
        new = "、".join(x for x in a2 if x not in a1) or "、".join(k2)
        return Hunk(cls="substantive", kind="number", summary=f"{old} → {new}",
                    why="A number differs between the two versions.", extra={"old": old, "new": new}, **base)
    y1 = sorted(ch for ch in c1 if ch in _SYMBOLS or unicodedata.category(ch) == "Sc")
    y2 = sorted(ch for ch in c2 if ch in _SYMBOLS or unicodedata.category(ch) == "Sc")
    if y1 != y2 and (y1 or y2) and not any(ch.isalnum() for ch in c1 + c2):
        return Hunk(cls="substantive", kind="symbol", summary=f"“{w1}” → “{w2}”",
                    why="A comparison, sign or currency symbol differs between the two versions.", **base)
    why = real_words or "The words differ and the difference is not just formatting or spelling."
    extra = {"one_word_swap": [w1, w2]} if real_words else {}     # one real word for another: not a rewording to clear
    if c1 and c2 and len(c1) <= 44 and len(c2) <= 44:
        return Hunk(cls="substantive", kind="wording", summary=f"“{c1.strip()}” → “{c2.strip()}”", why=why, extra=extra, **base)
    return Hunk(cls="substantive", kind="wording", summary="Wording changed", why=why, extra=extra, **base)


_PAGE_NO = re.compile(r"^(?:page\s*)?[-–—\s]*#(?:\s*(?:of|/)\s*#)?[-–—\s]*$")


_TERMISH = re.compile(r"\b(shall|must|will|may|agree[sd]?|subject to|liable|liability|fees?|surcharge|penalt(?:y|ies)|"
                      r"interest|terminat\w*|governed|waive[sd]?|indemnif\w*|payable|binding)\b", re.I)


def _plain_label(text: str) -> bool:
    """A running header or footer that only labels the document (a title, a draft stamp, a date)."""
    v = _values(text)
    return not (v["money"] or v["percent"] or v["duration"]) and len(text.split()) <= 12 and not _TERMISH.search(text)


def _margin_hunks(a: TextDoc, b: TextDoc) -> list[Hunk]:
    """Running headers and footers are not clause text. A label that differs (draft stamp, date) is said once, as an
    administrative note. A header or footer that states a term is a change like any other."""
    def texts(d: TextDoc) -> list[tuple[str, int]]:
        seen, out = set(), []
        for m in getattr(d, "margins", []):
            masked = re.sub(r"\d+", "#", m.text).lower()
            if _PAGE_NO.match(masked) or m.text in seen:
                continue
            seen.add(m.text)
            out.append((m.text, m.page))
        return out
    ma, mb = texts(a), texts(b)
    only_a = [m for m in ma if m[0] not in {t for t, _ in mb}]
    only_b = [m for m in mb if m[0] not in {t for t, _ in ma}]
    hunks: list[Hunk] = []
    terms_a = [m for m in only_a if not _plain_label(m[0])]
    terms_b = [m for m in only_b if not _plain_label(m[0])]
    for qb, pb in terms_b:                       # a term in the margin of the returned copy
        best = max(only_a, key=lambda m: SequenceMatcher(None, m[0], qb).ratio(), default=None)
        qa, pa = best if best and SequenceMatcher(None, best[0], qb).ratio() >= 0.5 else ("", 0)
        v1, v2 = _values(qa), _values(qb)
        kind = next((k for k in ("money", "percent", "duration", "number") if v1[k] != v2[k]), "wording") if qa else "added"
        hunks.append(Hunk("header", "Header / footer", "substantive", kind, qa, qb, qa, qb,
                          "Header or footer text differs" if qa else "Header or footer text added", pa, pb,
                          "A running header or footer of the returned copy states a term.", v1_sentence=qa, v2_sentence=qb))
        only_a = [m for m in only_a if m[0] != qa]
        terms_a = [m for m in terms_a if m[0] != qa]
    for qa, pa in terms_a:                       # a term that was in our margin and is gone
        hunks.append(Hunk("header", "Header / footer", "substantive", "removed", qa, "", qa, "",
                          "Header or footer text removed", pa, 0,
                          "A running header or footer of the version we sent stated a term.", v1_sentence=qa))
    only_a = [m for m in only_a if _plain_label(m[0])]
    only_b = [m for m in only_b if _plain_label(m[0])]
    if only_a or only_b:
        qa, pa = only_a[0] if only_a else ("", 0)
        qb, pb = only_b[0] if only_b else ("", 0)
        hunks.append(Hunk("header", "Header / footer", "administrative", "header", qa, qb, qa, qb, "Header or footer text differs",
                          pa, pb, "Running headers and footers are not part of any clause. They differ between the two files.",
                          extra={"v1": [t for t, _ in only_a], "v2": [t for t, _ in only_b]}))
    return hunks


def _heading_quote(c: Clause) -> str:
    return c.heading or (f"{c.number} {c.title}".strip() if c.kind != "paragraph" else _head(c.text, 80))


def compare_documents(a: TextDoc, b: TextDoc) -> list[Hunk]:
    hunks: list[Hunk] = []
    english = is_english(a.full_text) and is_english(b.full_text)
    ac, bc = _views(a, b)
    matched = _match(ac, bc)
    non_pre = [(i, j) for i, j in matched if ac[i].kind != "preamble"]
    moved_pos = _moved([j for _, j in non_pre])
    moved = {non_pre[k] for k in moved_pos}
    by_a = dict(matched)
    taken = set(by_a.values())

    def tag(hs: list[Hunk], ca: Clause | None, cb: Clause | None) -> list[Hunk]:
        for h in hs:
            h.v1_uid, h.v2_uid = (ca.uid or None) if ca else None, (cb.uid or None) if cb else None
        return hs

    run: list[tuple[Clause, Clause]] = []

    def flush_run():
        if run:
            ca, cb = run[0]
            more = f" (and the {len(run) - 1} clause{'s' if len(run) > 2 else ''} after it)" if len(run) > 1 else ""
            hunks.extend(tag([Hunk(cb.number, cb.title, "formatting", "renumbered", _heading_quote(ca), _heading_quote(cb),
                                   ca.number, cb.number, f"Renumbered {ca.number} → {cb.number}{more}", ca.page, cb.page,
                                   "Same clause under a new number: a clause was added, removed or moved elsewhere.",
                                   extra={"renumbered": [[x.uid, x.number, y.uid, y.number] for x, y in run]})], ca, cb))
            run.clear()

    for i, ca in enumerate(ac):
        if i not in by_a:
            flush_run()
            text, cut = _passage(ca.text)
            quote = _head(ca.text) or _heading_quote(ca)
            hunks.extend(tag([Hunk(ca.number, ca.title, "substantive", "removed", quote, "", quote, "",
                                   "Clause removed", ca.page, 0, "This clause exists only in the version we sent.",
                                   v1_sentence=text or quote, capped=cut)], ca, None))
            continue
        j = by_a[i]
        cb = bc[j]
        numbered = ca.kind in ("numbered", "appendix") and cb.kind in ("numbered", "appendix")
        if (i, j) in moved:
            flush_run()
            label = f": {ca.number} → {cb.number}" if numbered and ca.number != cb.number else ""
            hunks.extend(tag([Hunk(cb.number, cb.title, "formatting", "moved", _heading_quote(ca), _heading_quote(cb),
                                   ca.number, cb.number, f"Clause moved{label}", ca.page, cb.page,
                                   "The same clause sits at a different place in the returned version.",
                                   extra={"moved": [ca.uid, ca.number, cb.uid, cb.number]})], ca, cb))
        elif numbered and ca.number != cb.number:
            run.append((ca, cb))
        else:
            flush_run()
        found: list[Hunk] = []
        if (ca.kind not in ("paragraph", "preamble") and cb.kind not in ("paragraph", "preamble")
                and ca.title.strip().lower() != cb.title.strip().lower()):
            h = _classify(ca, cb, ca.title, cb.title, ca.title, cb.title, english)
            if h.cls == "formatting" or h.kind == "wording":
                h.summary, h.why = "Heading changed", "The clause heading differs."
            found.append(h)
        found.extend(_merge_rewrites(diff_clause(ca, cb, english), ca, cb))
        hunks.extend(tag(found, ca, cb))
    flush_run()
    for j, cb in enumerate(bc):
        if j not in taken:
            text, cut = _passage(cb.text)
            quote = _head(cb.text) or _heading_quote(cb)
            hunks.extend(tag([Hunk(cb.number, cb.title, "substantive", "added", "", quote, "", quote,
                                   "Clause added", 0, cb.page, "This clause exists only in the returned version.",
                                   v2_sentence=text or quote, capped=cut)], None, cb))
    hunks.extend(_margin_hunks(a, b))
    for h in hunks:
        h.v1_located = not h.v1_quote or _can_locate(a, h.v1_quote)
        h.v2_located = not h.v2_quote or _can_locate(b, h.v2_quote)
        h.located = h.v1_located and h.v2_located
    return hunks


def _can_locate(doc: TextDoc, quote: str) -> bool:
    check = getattr(doc, "can_locate", None)
    return bool(check(quote)) if check and getattr(doc, "streams", None) else True


def _merge_rewrites(hunks: list[Hunk], ca: Clause, cb: Clause) -> list[Hunk]:
    """Several substantive hunks in one clause that together rewrite most of it read better as one."""
    subs = [h for h in hunks if h.cls == "substantive"]
    changed = sum(len(h.v1_changed) + len(h.v2_changed) for h in subs)
    if len(subs) >= 2 and changed >= 0.5 * (len(ca.text) + len(cb.text)) / 2 and not any("table_row" in h.extra for h in subs):
        others = [h for h in hunks if h.cls != "substantive"]
        s1, cut1 = _passage(ca.text)
        s2, cut2 = _passage(cb.text)
        merged = Hunk(cb.number, cb.title, "substantive", "wording", _sentence(ca.text, subs[0].v1_changed, subs[-1].v1_changed),
                      _sentence(cb.text, subs[0].v2_changed, subs[-1].v2_changed), ca.text, cb.text,
                      "Clause rewritten", ca.page, cb.page, "Most of this clause was replaced.",
                      v1_sentence=s1, v2_sentence=s2, capped=cut1 or cut2)
        kinds = sorted({h.kind for h in subs if h.kind != "wording"})
        if kinds:
            merged.extra = {"value_kinds": kinds}      # numbers changed inside the rewrite: callers must not clear it
        return others + [merged]
    return hunks


def _sentence(text: str, first: str, last: str) -> str:
    i = text.find(first) if first else 0
    j = text.rfind(last) + len(last) if last else len(text)
    if i < 0 or j <= i:
        return text[:400]
    start = max(text.rfind(". ", 0, i) + 2, 0) if text.rfind(". ", 0, i) >= 0 else 0
    end = text.find(". ", j)
    end = len(text) if end < 0 else end + 1
    return text[start:end][:600]
