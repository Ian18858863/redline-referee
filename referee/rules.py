"""Deterministic comparison of two versions of the same agreement.

No model. Clauses are aligned, then each aligned pair is diffed word by word.
Every difference becomes a Hunk with exact quotes from both versions and a
class: substantive | administrative | formatting | spelling.

This layer exists so that the product never depends on a model noticing that
30 became 90. Clocked Compare is run next to it and the two are cross-labelled.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from .textdoc import Clause, TextDoc, squash

TOKEN = re.compile(r"\$?\d[\d,]*(?:\.\d+)?%?|[A-Za-z]+(?:['’-][A-Za-z]+)*|[^\sA-Za-z0-9]")
NUMBER_WORDS = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
                "sixteen seventeen eighteen nineteen twenty thirty forty fifty sixty seventy eighty ninety hundred "
                "thousand and").split()
_NW = "|".join(NUMBER_WORDS)
SPELLED = re.compile(rf"(?:\b(?:{_NW})\b[\s-]*)+\(\s*(\d[\d,]*)\s*\)", re.I)
UNITS = r"(?:business\s+)?(?:hour|day|week|month|year)s?"
DURATION = re.compile(rf"(\d[\d,]*)\s*[-\s]?\s*({UNITS})\b", re.I)
MONEY = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?")
PERCENT = re.compile(r"\d+(?:\.\d+)?\s?%")
NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
ADDRESSISH = re.compile(r"\b(street|st\.|avenue|ave\.|road|rd\.|suite|floor|row|boulevard|blvd|drive|lane|p\.?o\.? box|\d{5}(?:-\d{4})?)\b", re.I)
CONTEXT_TOKENS = 4


@dataclass
class Hunk:
    clause: str
    title: str
    cls: str                       # substantive | administrative | formatting | spelling
    kind: str                      # duration | money | percent | number | wording | added | removed | address | format | typo
    v1_quote: str
    v2_quote: str
    v1_changed: str
    v2_changed: str
    summary: str
    v1_page: int = 0
    v2_page: int = 0
    why: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def substantive(self) -> bool:
        return self.cls == "substantive"


def tokens(text: str) -> list[tuple[str, int, int]]:
    return [(m.group(0), m.start(), m.end()) for m in TOKEN.finditer(text)]


def normalise(s: str) -> str:
    s = SPELLED.sub(lambda m: m.group(1), s)
    s = s.lower().replace("&", " and ").replace("e-mail", "email")
    s = re.sub(r"[^a-z0-9$%.]+", "", s)
    return re.sub(r"\.(?!\d)", "", s)


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


def align(a: TextDoc, b: TextDoc) -> list[tuple[Clause | None, Clause | None]]:
    pairs: list[tuple[Clause | None, Clause | None]] = []
    used: set[int] = set()
    for ca in a.clauses:
        best, best_score = None, 0.0
        for j, cb in enumerate(b.clauses):
            if j in used:
                continue
            title = SequenceMatcher(None, ca.title.lower(), cb.title.lower()).ratio()
            if ca.number == cb.number and title >= 0.6:
                score = 2.0 + title
            else:
                body = SequenceMatcher(None, ca.text[:600], cb.text[:600]).quick_ratio()
                score = 0.5 * title + body if (title >= 0.75 or body >= 0.72) else 0.0
            if score > best_score:
                best, best_score = j, score
        if best is not None and best_score >= 0.9:
            used.add(best)
            pairs.append((ca, b.clauses[best]))
        else:
            pairs.append((ca, None))
    for j, cb in enumerate(b.clauses):
        if j not in used:
            pairs.append((None, cb))
    return pairs


def _snippet(text: str, toks, i1: int, i2: int) -> tuple[str, str]:
    """(quote with context, exactly-changed text) for token range [i1, i2)."""
    if not toks:
        return "", ""
    lo = max(0, i1 - CONTEXT_TOKENS)
    hi = min(len(toks), i2 + CONTEXT_TOKENS)
    quote = text[toks[lo][1]:toks[hi - 1][2]] if hi > lo else ""
    changed = text[toks[i1][1]:toks[i2 - 1][2]] if i2 > i1 else ""
    return quote, changed


def diff_clause(ca: Clause, cb: Clause) -> list[Hunk]:
    if squash(ca.text) == squash(cb.text):
        return []
    ta, tb = tokens(ca.text), tokens(cb.text)
    sm = SequenceMatcher(None, [t[0] for t in ta], [t[0] for t in tb], autojunk=False)
    ops = [op for op in sm.get_opcodes()]
    # merge changes separated by at most two unchanged tokens
    groups: list[list[int]] = []
    for tag, i1, i2, j1, j2 in ops:
        if tag == "equal":
            continue
        if groups and i1 - groups[-1][1] <= 2 and j1 - groups[-1][3] <= 2:
            groups[-1][1], groups[-1][3] = i2, j2
        else:
            groups.append([i1, i2, j1, j2])
    hunks = []
    for i1, i2, j1, j2 in groups:
        q1, c1 = _snippet(ca.text, ta, i1, i2)
        q2, c2 = _snippet(cb.text, tb, j1, j2)
        if not c1 and ta:                      # pure insertion: quote the surrounding words in v1
            q1, _ = _snippet(ca.text, ta, max(0, i1 - 1), min(len(ta), i1 + 1))
        if not c2 and tb:
            q2, _ = _snippet(cb.text, tb, max(0, j1 - 1), min(len(tb), j1 + 1))
        hunks.append(_classify(ca, cb, q1, q2, c1, c2))
    return hunks


def _classify(ca: Clause, cb: Clause, q1: str, q2: str, c1: str, c2: str) -> Hunk:
    base = dict(clause=cb.number or ca.number, title=cb.title or ca.title, v1_quote=q1, v2_quote=q2,
                v1_changed=c1, v2_changed=c2, v1_page=ca.page, v2_page=cb.page)
    if normalise(q1) == normalise(q2) or normalise(c1) == normalise(c2):
        return Hunk(cls="formatting", kind="format", summary="Formatting only", why="Same words and numbers once formatting is ignored.", **base)
    w1, w2 = c1.strip(), c2.strip()
    if (w1.isalpha() and w2.isalpha() and min(len(w1), len(w2)) >= 5 and w1[0].lower() == w2[0].lower()
            and _lev(w1.lower(), w2.lower()) <= 2):
        return Hunk(cls="spelling", kind="typo", summary=f"“{w1}” → “{w2}”",
                    why="One word, same first letter, at most two letters apart: treated as a spelling fix. Compare can overrule this.", **base)
    v1, v2 = _values(q1), _values(q2)
    if re.search(r"notice", cb.title, re.I) and (ADDRESSISH.search(c1) or ADDRESSISH.search(c2)):
        return Hunk(cls="administrative", kind="address", summary="Notice address updated",
                    why="Contact details inside a notices clause.", **base)
    for kind in ("money", "percent", "duration", "number"):
        if v1[kind] != v2[kind]:
            old = ", ".join(x for x in v1[kind] if x not in v2[kind]) or ", ".join(v1[kind]) or "none"
            new = ", ".join(x for x in v2[kind] if x not in v1[kind]) or ", ".join(v2[kind]) or "none"
            return Hunk(cls="substantive", kind=kind, summary=f"{old} → {new}",
                        why=f"A {kind} value differs between the two versions.", extra={"old": old, "new": new}, **base)
    return Hunk(cls="substantive", kind="wording", summary="Wording changed",
                why="The words differ and the difference is not just formatting or spelling.", **base)


def compare_documents(a: TextDoc, b: TextDoc) -> list[Hunk]:
    hunks: list[Hunk] = []
    for ca, cb in align(a, b):
        if ca is None and cb is not None:
            hunks.append(Hunk(cb.number, cb.title, "substantive", "added", "", cb.text[:400], "", cb.text[:400],
                              "Clause added", 0, cb.page, "This clause exists only in the returned version."))
        elif cb is None and ca is not None:
            hunks.append(Hunk(ca.number, ca.title, "substantive", "removed", ca.text[:400], "", ca.text[:400], "",
                              "Clause removed", ca.page, 0, "This clause exists only in the version we sent."))
        elif ca is not None and cb is not None:
            if ca.title.strip().lower() != cb.title.strip().lower():
                cls = "formatting" if normalise(ca.title) == normalise(cb.title) else "substantive"
                hunks.append(Hunk(cb.number, cb.title, cls, "format" if cls == "formatting" else "wording",
                                  ca.title, cb.title, ca.title, cb.title, "Heading changed", ca.page, cb.page,
                                  "The clause heading differs."))
            hunks.extend(_merge_rewrites(diff_clause(ca, cb), ca, cb))
    return hunks


def _merge_rewrites(hunks: list[Hunk], ca: Clause, cb: Clause) -> list[Hunk]:
    """Several substantive hunks in one clause that together rewrite most of it read better as one."""
    subs = [h for h in hunks if h.cls == "substantive"]
    changed = sum(len(h.v1_changed) + len(h.v2_changed) for h in subs)
    if len(subs) >= 2 and changed >= 0.5 * (len(ca.text) + len(cb.text)) / 2:
        others = [h for h in hunks if h.cls != "substantive"]
        merged = Hunk(cb.number, cb.title, "substantive", "wording", _sentence(ca.text, subs[0].v1_changed, subs[-1].v1_changed),
                      _sentence(cb.text, subs[0].v2_changed, subs[-1].v2_changed), ca.text, cb.text,
                      "Clause rewritten", ca.page, cb.page, "Most of this clause was replaced.")
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
