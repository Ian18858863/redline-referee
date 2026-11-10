"""The review: two PDFs and a cover note in, one decision with evidence out.

Order of work
  1. Rule layer (no model): align clauses, diff, classify, find hidden text.
  2. Gate. Clocked Compare on the two whole PDFs answers one question: are these two versions of one
     agreement? If not, the review stops there: nothing else is sent. When the gate passes, its pairing of
     passages is kept only as the marked copies (an optional layer), never as a list of changes.
  3. Clocked, in parallel: Compare on each clause whose meaning is in question (our text against theirs),
     Verify on each changed sentence against the other version's PDF (both directions), Guard on the cover
     note and the returned PDF, Extract on the key terms of both versions.
     Formatting and administrative differences are settled by the rules and are not sent.
  4. The readings side by side (rules, Verify, Compare); code decides, and where they disagree the stricter
     reading wins. One exception, for wording only: a rewording is cleared when Verify supports it in both
     directions and Compare does not object (a one-word swap needs Compare to say "same" outright).
     A value that differs is never cleared.
  5. Verify the cover note, statement by statement, against v2 and the list of changes.
  6. For every open change, one at a time: Rerank the playbook, then Decide the route from what Rerank kept.
  7. Code sets the status.

Every clause is identified by its uid pair (v1 uid, v2 uid), never by its display number: appendices that
restart numbering, clauses renumbered after an insertion and duplicate numbers all keep their own identity.

Every API response is checked before it is used. A response that is empty, of the wrong type, missing what
the step needs, or carrying a probability outside 0..1 is a failed step with a named error; a failed step
makes the review incomplete and is never read as a pass. Nothing is cut to fit a limit: long lists go out
in several requests, and a passage too long for one request is reported as not read.
"""
from __future__ import annotations

import base64
import bisect
import json
import math
import os
import re

import pymupdf
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
from pathlib import Path

from . import prompts as P
from .gateway import PRODUCT_NAME, Gateway, StepError, pdf_b64
from .rules import Hunk, _values, align, compare_documents, normalise, sentences
from .textdoc import TextDoc, content_box, locate, read_pdf, remap_rects, render_pages, squash

ROOT = Path(__file__).resolve().parents[1]

# Admission limits of the API (docs: Limits & usage). Nothing is cut to fit them.
VERIFY_MAX_CLAIMS = 30               # explicit claims per Verify request
VERIFY_ANSWER_CHARS = 20000          # characters of "answer" per Verify request
SOURCE_CHARS = 200000                # source characters per Verify or Compare request
GUARD_CHARS = 60000                  # characters of text per Guard request
RERANK_QUERY_CHARS = 4000
PASSAGE_CAP = 1200                   # a long clause is read in passages of whole sentences of about this length
LOCATE_MAX = 20000                   # a quote longer than this is not looked for on the page
READERS = ("verify", "compare")

HIDDEN_WORDS = {
    "tiny": "text too small to read on paper",
    "white": "text the same colour as the page",
    "invisible": "text set to be invisible",
    "low-contrast": "text almost the same colour as the page",
    "covered": "text covered by an image or a shape",
    "off-page": "text outside the page",
}


def _nz(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def _n(count: int, word: str, plural: str | None = None) -> str:
    """"1 change", "3 changes"."""
    return f"{count} {word if count == 1 else (plural or word + 's')}"


def _names(names: list[str]) -> str:
    return " and ".join(names)


def _verb(names: list[str], verb: str) -> str:
    return verb if len(names) != 1 else verb + "s"


def _is_num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


# ---------------------------------------------------------------- cover note
_ABBREV = set("inc ltd co corp llc plc gmbh no nos mr ms mrs dr prof vs e.g i.e approx sec art cl para p.s ps u.s u.k st etc jr sr est".split())
_COMPANY = set("inc ltd co corp llc plc gmbh".split())
_SENT_GAP = re.compile(r"(?<=[.!?])[\"”’')\]]*\s+")
_SALUTE = re.compile(r"^(?:hi|hello|hey|dear|greetings|good (?:morning|afternoon|evening)|to whom it may concern)\b"
                     r"(?:\s+(?:mr|ms|mrs|dr|prof)\.)?"
                     r"(?:(?:\s+[^\s,:;.!?—–-]+){0,4}\s*[,:;!]|(?:\s+[^\s,:;.!?—–-]+){0,2}\s*(?:\.(?=\s|$)|$))\s*", re.I)
_SIGN = (r"(?i:thanks(?: again| so much| very much| a lot)?|thank you(?: very much| again| so much)?|many thanks|"
         r"best(?: regards| wishes)?|kind regards|warm regards|with regards|regards|sincerely(?: yours)?|"
         r"yours (?:sincerely|faithfully|truly)|cheers|respectfully|cordially|all the best)")
_SIGNOFF = re.compile(rf"^{_SIGN}\s*(?:[,.!]\s*)?$|^{_SIGN}\s*,\s*[A-Z][\w.'’-]*(?:\s+[A-Z][\w.'’-]*){{0,3}}$")
_THANKS = re.compile(r"^(?:thanks|thank you|many thanks)\b", re.I)
_THANKS_THEN = re.compile(r"^(?:thanks|thank you|many thanks)\s*[,!–—-]\s*(?=\S+\s+\S+\s+\S)", re.I)
_BULLET = re.compile(r"^(?:[-*•·–—▪◦]|\(?\d{1,2}[.)]|\(?[a-z]\))\s+(?=\S)")
_TO_AN_AI = re.compile(
    r"\b(?:AI|A\.I\.|LLMs?|GPT|ChatGPT|Copilot)\b|"
    r"(?i:\b(?:artificial intelligence|language model|chat ?bot|automated (?:\w+ ){0,2}(?:reviewer|review|system|tool|assistant|agent)|"
    r"review(?:er)? tool)\b)|"
    r"(?i:\b(?:ignore|disregard|override)\b.{0,40}\b(?:instructions?|rules|checks?|review)\b)")


def _note_sentences(text: str) -> list[str]:
    """Sentences of a paragraph. "Inc." before a lower-case word, "No. 5", "J. Smith" and "P.S." do not end one."""
    out, start = [], 0
    for m in _SENT_GAP.finditer(text):
        before = text[start:m.start()]
        last = (before.split() or [""])[-1]
        nxt = text[m.end():m.end() + 1]
        if last.endswith("."):
            w = last[:-1].lower().lstrip("(\"'“‘")
            if (len(w) == 1 and w.isalpha()) or (w in _ABBREV and not (w in _COMPANY and nxt.isupper())):
                continue
        out.append(text[start:m.end()].strip())
        start = m.end()
    out.append(text[start:].strip())
    return [s for s in out if s]


def _pieces(sentence: str) -> list[tuple[str, str]]:
    """A semicolon list as its items: [(text as written, the claim sent)]. A lead-in before a colon
    ("We changed the following:") is put in front of every item it introduces."""
    if ";" not in sentence:
        return [(sentence, sentence)]
    lead, body = "", sentence
    m = re.match(r"^([^;]{3,200}?:)\s+(?=\S)", sentence)
    if m:
        lead, body = m.group(1), sentence[m.end():]
    parts = [p.strip() for p in body.split(";") if p.strip()]
    if len(parts) < 2 or any(len(p.split()) < 2 for p in parts):
        return [(sentence, sentence)]
    out = []
    for p in parts:
        core = re.sub(r"^(?:and|or)\s+", "", p, flags=re.I)
        out.append((p, f"{lead} {core}" if lead else core))
    return out


def split_note(letter: str) -> dict:
    """Every sentence of the cover note, sorted into what happens to it.

    claims        statements that assert something: each is checked by Verify (`sent` holds the text sent,
                  which differs from `claims` only for a list item that needs its lead-in)
    instructions  text addressed to an AI or a review tool: left to Guard, which reads the whole note
    skipped       salutations, sign-offs, signature lines and questions, with the reason

    A paragraph is not dropped because it opens with "Hi", and a sentence does not need a full stop.
    Bullets and semicolon lists are split into one statement each.
    """
    claims: list[str] = []
    sent: list[str] = []
    instructions: list[dict] = []
    skipped: list[dict] = []

    def take(text: str, claim: str):
        text, claim = text.strip(), claim.strip()
        bare = re.sub(r"^(?:P\.?\s?S\.?|N\.?B\.?)\s*[:.,-]?\s*", "", text)
        if not bare:
            return
        if re.search(r"\?[\"”’')\]]*$", bare):
            skipped.append({"text": text, "why": "question"})
        elif _TO_AN_AI.search(bare):
            instructions.append({"text": text, "claim": claim,
                                 "why": "Addressed to an AI or a review tool: an instruction, not a statement about the agreement."})
        elif _THANKS.match(bare) and not _THANKS_THEN.match(bare):
            skipped.append({"text": text, "why": "courtesy"})
        elif len(re.findall(r"\w+", bare)) < 2:
            skipped.append({"text": text, "why": "too short to state anything"})
        else:
            m = _THANKS_THEN.match(text)
            if m:
                text = text[m.end():]
                claim = claim[m.end():] if _THANKS_THEN.match(claim) else claim
            claims.append(text)
            sent.append(claim)

    signed, first = False, True
    every: list[list[str]] = []                # ["text"|"bullet", content], over the whole note
    body = (letter or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    for para in re.split(r"\n[ \t]*\n", body):
        lines = [squash(l) for l in para.split("\n") if l.strip()]
        if not lines:
            continue
        if first:
            first = False
            m = _SALUTE.match(lines[0])
            if m:
                skipped.append({"text": m.group(0).strip(), "why": "salutation"})
                rest = lines[0][m.end():].strip()
                lines = ([rest] if rest else []) + lines[1:]
        blocks: list[list[str]] = []
        in_signature = False
        for line in lines:
            if in_signature:
                skipped.append({"text": line, "why": "signature"})
            elif _SIGNOFF.match(line):
                skipped.append({"text": line, "why": "sign-off"})
                signed = in_signature = True
            elif signed and not re.search(r"[.!?:;][\"”’')\]]*$", line) and len(line.split()) <= 8 and not _BULLET.match(line):
                skipped.append({"text": line, "why": "signature"})
            elif _BULLET.match(line):
                blocks.append(["bullet", _BULLET.sub("", line, count=1)])
            elif blocks and blocks[-1][0] == "bullet" and not re.search(r"[.!?;:][\"”’')\]]*$", blocks[-1][1]):
                blocks[-1][1] += " " + line                    # a wrapped bullet
            elif blocks and blocks[-1][0] == "text":
                blocks[-1][1] += " " + line
            else:
                blocks.append(["text", line])
        every += blocks
    lead = ""
    for n, (kind, content) in enumerate(every):
        if kind == "text":
            lead = ""
            sents = _note_sentences(content)
            bullets_next = n + 1 < len(every) and every[n + 1][0] == "bullet"
            if sents and sents[-1].endswith(":") and bullets_next:     # introduces the bullets that follow
                lead = sents.pop()
            for s in sents:
                for text, claim in _pieces(s):
                    take(text, claim)
        else:
            for s in _note_sentences(content):
                for text, claim in _pieces(s):
                    take(text, f"{lead} {claim}" if lead else claim)
    return {"claims": claims, "sent": sent, "instructions": instructions, "skipped": skipped}


def split_claims(letter: str) -> list[str]:
    """The statements of the note that assert something, all of them, as written."""
    return split_note(letter)["claims"]


# ----------------------------------------------------- checking API responses
class BadResponse(Exception):
    """An API answer that cannot be used. The text names what was wrong, in plain words."""


def _bad(what: str):
    raise BadResponse(what)


def _obj(resp, what: str = "the response") -> dict:
    if not isinstance(resp, dict):
        _bad(f"{what} was not an object")
    if not resp:
        _bad(f"{what} was empty")
    return resp


def _prob(x, what: str, required: bool = False):
    if x is None:
        if required:
            _bad(f"the response had no {what}")
        return None
    if not _is_num(x) or not 0 <= x <= 1:
        _bad(f"{what} was not a probability between 0 and 1")
    return x


def _offset(x, what: str):
    if x is None:
        return None
    if not isinstance(x, int) or isinstance(x, bool) or x < 0:
        _bad(f"{what} was not a character position")
    return x


def _text(x, what: str, none: str | None = "") -> str | None:
    if x is None:
        return none
    if not isinstance(x, str):
        _bad(f"{what} was not text")
    return x


def _pdf_objects(raw, ids: list[str]) -> dict[str, dict]:
    if raw is None:
        return {}
    if isinstance(raw, dict) and raw and all(isinstance(v, dict) and k in ids for k, v in raw.items()):
        return dict(raw)                                  # keyed by document id
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list) or any(not isinstance(o, dict) for o in raw):
        _bad("the marked copies came back in a form that could not be read")
    out: dict[str, dict] = {}
    for i, obj in enumerate(raw):
        name = next((obj[k] for k in ("document", "id", "name") if isinstance(obj.get(k), str)), None)
        out[name if name in ids else ids[i] if i < len(ids) else str(i)] = obj
    return out


def _page_of(pdf_obj: dict | None, start) -> int | None:
    try:
        offs, sel = pdf_obj["page_offsets"], pdf_obj["selected"]
        page = sel[bisect.bisect_right(offs, start) - 1]
        return page if isinstance(page, int) and not isinstance(page, bool) else None
    except Exception:
        return None


def _pair0(resp) -> dict:
    pairs = _obj(resp).get("pairs")
    if not isinstance(pairs, list) or not pairs:
        _bad("the response had no pair of documents")
    pair = pairs[0]
    if not isinstance(pair, dict):
        _bad("the pair of documents was not an object")
    if not isinstance(pair.get("relation"), str) or not pair["relation"]:
        _bad("the response did not say how the two documents relate")
    for rel in ("conflicts", "duplicates"):
        if pair.get(rel) is not None and not isinstance(pair[rel], list):
            _bad(f"the list of {rel} was not a list")
    probs = pair.get("probabilities")
    if probs is not None and not isinstance(probs, dict):
        _bad("the probabilities were not an object")
    return pair


def check_gate(resp) -> dict:
    """Compare on the two whole PDFs, used as a gate: relation and `related` are what the step needs.
    The passages it paired are counted, not read as changes; the marked copies are an optional layer."""
    pair = _pair0(resp)
    related = _prob(pair.get("related"), "measure of how related the two files are", required=True)
    probs = pair.get("probabilities") or {}
    for k, v in probs.items():
        _prob(v, f"probability of “{k}”")
    pdfs = _pdf_objects(resp.get("pdf"), ["v1", "v2"])
    marked = {}
    for k, v in pdfs.items():
        m = v.get("marked")
        marked[k] = m if isinstance(m, dict) else {}
    return {"verdict": resp.get("verdict") if isinstance(resp.get("verdict"), str) else None,
            "relation": pair["relation"], "related": related, "probabilities": probs,
            "conflicts": len(pair.get("conflicts") or []), "same_passages": len(pair.get("duplicates") or []),
            "marked": marked, "pdf": pdfs}


def check_clause_compare(resp, v1_text: str, v2_text: str) -> dict:
    """Compare on one clause: our text against theirs. Offsets index the clause texts we sent."""
    pair = _pair0(resp)
    relation = pair["relation"]
    probs = pair.get("probabilities") or {}
    probability = _prob(probs.get(relation), f"probability of “{relation}”")
    related = _prob(pair.get("related"), "measure of how related the two texts are")
    limit = 4 * (max(len(v1_text), len(v2_text)) + 100)
    findings = []
    for rel in ("conflicts", "duplicates"):
        for f in pair.get(rel) or []:
            if not isinstance(f, dict):
                _bad("a finding was not an object")
            confidence = _prob(f.get("confidence"), "confidence of a finding")
            kind = _text(f.get("kind"), "kind of a finding", None)
            raw = {}
            for s in ("a", "b"):
                x = f.get(s)
                if not isinstance(x, dict):
                    _bad("a finding did not carry its two passages")
                raw[s] = x
            by_doc = {x.get("document"): x for x in raw.values() if isinstance(x.get("document"), str)}
            a, b = by_doc.get("v1") or raw["a"], by_doc.get("v2") or raw["b"]

            def side(x: dict, text: str) -> dict:
                quote = _text(x.get("quote"), "quote of a finding")
                if len(quote) > limit:
                    _bad("a finding quoted more text than was sent")
                start, end = _offset(x.get("start"), "start of a quote"), _offset(x.get("end"), "end of a quote")
                ok = None if start is None or end is None else text[start:end] == quote
                return {"quote": quote, "start": start, "end": end, "aligned": ok, "page": None}
            findings.append({"relation": "conflict" if rel == "conflicts" else "duplicate", "kind": kind,
                             "confidence": confidence, "v1": side(a, v1_text), "v2": side(b, v2_text)})
    return {"relation": relation, "probability": probability, "related": related, "findings": findings}


def check_verify(resp, claims: list[str]) -> list[dict]:
    """Verify: one judgment per claim sent, in the order sent. A count that differs, a claim that comes back
    with other words, or a claim without a verdict is not matched by position: the whole answer is refused."""
    listed = _obj(resp).get("claims")
    if not isinstance(listed, list) or not listed:
        _bad("the response had no claims")
    if len(listed) != len(claims):
        _bad(f"the response had {_n(len(listed), 'claim')} for the {len(claims)} sent, so verdicts cannot be matched to statements")
    out = []
    for c, text in zip(listed, claims):
        if not isinstance(c, dict):
            _bad("a claim came back in a form that could not be read")
        back = c.get("claim")
        if back is not None and (not isinstance(back, str) or squash(back) != squash(text)):
            _bad("a claim came back with different words, so its verdict cannot be matched to a statement")
        verdict = c.get("verdict")
        if not isinstance(verdict, str) or not verdict:
            _bad("a claim came back without a verdict")
        item = {"verdict": verdict}
        for k in ("supported", "contradicted", "not_stated", "not_a_claim"):
            item[k] = _prob(c.get(k), f"probability “{k}” of a claim")
        ev = c.get("evidence")
        item["evidence"], item["evidence_unreadable"] = None, False
        if isinstance(ev, dict):
            try:
                item["evidence"] = {"document": _text(ev.get("document"), "document", None),
                                    "quote": _text(ev.get("quote"), "quote"),
                                    "start": _offset(ev.get("start"), "start"), "end": _offset(ev.get("end"), "end"),
                                    "confidence": _prob(ev.get("confidence"), "confidence")}
            except BadResponse:
                item["evidence_unreadable"] = True
        elif ev is not None:
            item["evidence_unreadable"] = True
        out.append(item)
    return out


def check_guard(resp) -> dict:
    injection = _prob(_obj(resp).get("injection"), "injection probability", required=True)
    verdict = _text(resp.get("verdict"), "verdict", None)
    threshold = _prob(resp.get("threshold"), "threshold")
    evidence = resp.get("evidence")
    if evidence is None:
        evidence = []
    if not isinstance(evidence, list):
        _bad("the evidence was not a list")
    ev = []
    for e in evidence:
        if not isinstance(e, dict):
            _bad("a piece of evidence was not an object")
        ev.append({"quote": _text(e.get("quote"), "quote of the evidence"), "start": _offset(e.get("start"), "start of the evidence"),
                   "confidence": _prob(e.get("confidence"), "confidence of the evidence")})
    pdf = resp.get("pdf")
    return {"injection": injection, "verdict": verdict, "threshold": 0.5 if threshold is None else threshold,
            "category": _text(resp.get("category"), "category", None), "severity": _text(resp.get("severity"), "severity", None),
            "evidence": ev, "pdf": pdf if isinstance(pdf, dict) else None}


def check_extract(resp) -> dict:
    fields = _obj(resp).get("fields")
    if not isinstance(fields, dict) or not fields:
        _bad("the response had no fields")
    pdf = resp.get("pdf")
    if pdf is not None and not isinstance(pdf, dict):
        _bad("the page map came back in a form that could not be read")
    out = {}
    for name in P.EXTRACT_FIELDS:
        f = fields.get(name)
        if f is None:
            f = {}
        if not isinstance(f, dict):
            _bad(f"the field “{name}” was not an object")
        val = f.get("value")
        if isinstance(val, list) and all(isinstance(x, str) for x in val):
            val = "; ".join(val) or None
        elif val is not None and not isinstance(val, str):
            _bad(f"the value of “{name}” was not text")
        start, end = _offset(f.get("start"), f"start of “{name}”"), _offset(f.get("end"), f"end of “{name}”")
        if start is None or end is None:
            start = end = None
        out[name] = {"value": val, "start": start, "end": end, "confidence": _prob(f.get("confidence"), f"confidence of “{name}”")}
    return {"fields": out, "pdf": pdf}


def check_rerank(resp, n_documents: int) -> list[dict]:
    results = _obj(resp).get("results")
    if not isinstance(results, list) or not results:
        _bad("the response had no results")
    rows = []
    for row in results:
        if not isinstance(row, dict):
            _bad("a result was not an object")
        pid, index = row.get("id"), row.get("index")
        if not isinstance(pid, str):
            pid = None
        if not (isinstance(index, int) and not isinstance(index, bool) and 0 <= index < n_documents):
            index = None
        if pid is None and index is None:
            _bad("a result did not point at any passage that was sent")
        action = row.get("action")
        if not isinstance(action, str) or not action:
            _bad("a result carried no action")
        rows.append({"id": pid, "index": index, "action": action, "reason": _text(row.get("reason"), "reason", None),
                     "relevance_score": _prob(row.get("relevance_score"), "relevance of a passage"),
                     "document_score": _prob(row.get("document_score"), "score of a passage")})
    return rows


def check_decide(resp) -> dict:
    answers = _obj(resp).get("answers")
    if not isinstance(answers, dict) or not answers:
        _bad("the response had no answers")
    out = {}
    for key in ("desk_may_accept", "desk_may_counter"):
        a = answers.get(key)
        if not isinstance(a, dict) or a.get("noul") is None:
            _bad(f"the answer to “{key}” was missing")
        out[key] = _prob(a.get("noul"), f"answer to “{key}”")
    fav, mat = answers.get("favors"), answers.get("materiality")
    fav = fav if isinstance(fav, dict) else {}
    mat = mat if isinstance(mat, dict) else {}
    out["favors"] = fav.get("choice") if isinstance(fav.get("choice"), str) else None
    conf = fav.get("confidence")
    out["favors_confidence"] = conf if _is_num(conf) and 0 <= conf <= 1 else None
    score = mat.get("score")
    out["materiality"] = score if _is_num(score) else None
    legend = mat.get("legend") or P.DECIDE_QUESTIONS["materiality"]["criteria"]
    label = None
    try:
        if isinstance(legend, dict):                      # the API returns {"0": "...", "1": "..."}
            legend = [legend[k] for k in sorted(legend, key=lambda k: int(k))]
        if out["materiality"] is not None and isinstance(legend, list) and legend:
            label = str(legend[min(len(legend) - 1, max(0, round(out["materiality"])))]).split(":")[0]
    except (ValueError, TypeError, OverflowError):
        label = None
    out["materiality_label"] = label
    return out


# ----------------------------------------------- what goes to Compare and Verify
def _key(h: Hunk) -> tuple:
    """Identity of the clause a difference sits in: its uid pair. A header or footer has none."""
    return (h.v1_uid, h.v2_uid) if (h.v1_uid or h.v2_uid) else (None, None, h.clause)


def _settled(h: Hunk) -> bool:
    """Formatting and administrative differences are settled by the rules and are not sent to the API."""
    return h.cls in ("formatting", "administrative")


def clauses_to_compare(hunks: list[Hunk], view1: dict, view2: dict) -> dict[tuple, dict]:
    """(v1 uid, v2 uid) -> {label, v1, v2} for every clause whose meaning is in question.
    Formatting and notice addresses are settled by the rules; added or removed clauses have no pair."""
    out: dict[tuple, dict] = {}
    for h in hunks:
        key = (h.v1_uid, h.v2_uid)
        if _settled(h) or not h.v1_uid or not h.v2_uid or key in out:
            continue
        ca, cb = view1.get(h.v1_uid), view2.get(h.v2_uid)
        if ca is not None and cb is not None and ca.text and cb.text:
            out[key] = {"label": cb.number or ca.number, "v1": ca.text, "v2": cb.text}
    return out


def passages(text: str, cap: int = PASSAGE_CAP) -> list[str]:
    """A long clause as passages of whole sentences, each about `cap` characters. All of it, nothing cut."""
    if len(text) <= cap:
        return [text] if text else []
    out, cur = [], ""
    for s in sentences(text):
        if cur and len(cur) + 1 + len(s) > cap:
            out.append(cur)
            cur = s
        else:
            cur = f"{cur} {s}" if cur else s
    if cur:
        out.append(cur)
    return out


def hunk_claims(h: Hunk, view1: dict | None = None, view2: dict | None = None) -> tuple[list[str], list[str]]:
    """What Verify is asked about one difference: (sentences of v1, sentences of v2). Normally one each.
    When the rule layer sent only the opening of a long clause (`capped`), the whole clause goes, in passages."""
    out = []
    for sentence, uid, view in ((h.v1_sentence, h.v1_uid, view1), (h.v2_sentence, h.v2_uid, view2)):
        claims = [sentence] if sentence else []
        if sentence and h.capped and view and uid in view:
            whole = passages(view[uid].text)
            if whole and whole[0] == sentence:
                claims = whole
        out.append(claims)
    return out[0], out[1]


def xverify_claims(hunks: list[Hunk], view1: dict | None = None, view2: dict | None = None) -> tuple[list[str], list[str]]:
    """Sentences worth a reading of their meaning, all of them (they go out thirty at a time).
    Formatting and notice addresses are settled by the rules."""
    s1, s2 = [], []
    for h in hunks:
        if _settled(h):
            continue
        c1, c2 = hunk_claims(h, view1, view2)
        s1 += [c for c in c1 if c not in s1]
        s2 += [c for c in c2 if c not in s2]
    return s1, s2


def batches(claims: list[str], max_claims: int | None = None, max_chars: int | None = None) -> tuple[list[list[str]], list[str]]:
    """Claims in requests of at most `max_claims` claims and `max_chars` characters, in order.
    -> (batches, claims too long to send at all). Thirty or fewer short claims are one batch, as written."""
    max_claims, max_chars = max_claims or VERIFY_MAX_CLAIMS, max_chars or VERIFY_ANSWER_CHARS
    out, cur, size, too_long = [], [], 0, []
    for c in claims:
        if len(c) > max_chars:
            too_long.append(c)
            continue
        add = len(c) + (1 if cur else 0)
        if cur and (len(cur) >= max_claims or size + add > max_chars):
            out.append(cur)
            cur, size, add = [], 0, len(c)
        cur.append(c)
        size += add
    if cur:
        out.append(cur)
    return out, too_long


def _overlaps(f: dict, h: Hunk) -> bool:
    c2, c1 = _nz(h.v2_changed), _nz(h.v1_changed)
    return bool((c2 and c2 in _nz(f["v2"]["quote"])) or (c1 and c1 in _nz(f["v1"]["quote"])))


def _covers(f: dict, h: Hunk) -> bool:
    """Does this Compare finding sit on the passage that holds the hunk? Shared text within the clause."""
    if _overlaps(f, h):
        return True
    for quote, sentence in ((f["v2"]["quote"], h.v2_sentence), (f["v1"]["quote"], h.v1_sentence)):
        a, b = _nz(quote), _nz(sentence)
        if a and b:
            m = SequenceMatcher(None, a, b, autojunk=False).find_longest_match(0, len(a), 0, len(b))
            if m.size >= min(40, max(9, int(0.6 * min(len(a), len(b))))):
                return True
    return False


def _cmp_view(f: dict, relation: str, cc: dict) -> dict:
    return {"relation": relation, "kind": f["kind"], "confidence": f["confidence"],
            "clause_relation": cc.get("relation"), "clause_probability": cc.get("probability"),
            "v1_quote": squash(f["v1"]["quote"]) or None, "v2_quote": squash(f["v2"]["quote"]) or None,
            "aligned": f["v1"]["aligned"] is not False and f["v2"]["aligned"] is not False}


def _clause_view(relation: str, cc: dict) -> dict:
    return {"relation": relation, "kind": None, "confidence": cc["probability"], "clause_relation": cc["relation"],
            "clause_probability": cc["probability"], "v1_quote": None, "v2_quote": None, "aligned": True}


def _judgment(j: dict | None) -> dict | None:
    return None if j is None else {k: j.get(k) for k in ("verdict", "supported", "contradicted", "not_stated")}


def _worst(js: list[dict | None]) -> dict | None:
    """Of several passages of one clause, the reading to show: the one furthest from "supported"."""
    got = [j for j in js if j]
    if not got:
        return None

    def rank(j):
        v = j["verdict"]
        return (0, -(j.get("contradicted") or 0)) if v == "contradicted" else (1, 0) if v == "not_stated" else \
               (2, 0) if v != "supported" else (3, j.get("supported") or 0)
    view = _judgment(min(got, key=rank))
    if len(js) > 1:
        view["passages"] = len(js)
    return view


def _values_stated(text: str, other: str | None) -> bool:
    """Every amount, percentage and period of `text` also occurs somewhere in `other`."""
    if other is None:
        return True
    mine, theirs = _values(text), _values(other)
    return all(set(mine[k]) <= set(theirs[k]) for k in ("money", "percent", "duration"))


def cross_label(hunks: list[Hunk], ccmp: dict | None = None, xv: dict | None = None, *,
                ignore_readers=frozenset(), claims: dict | None = None, sources: tuple[str, str] | None = None,
                not_read: set[str] | frozenset = frozenset()) -> list[dict]:
    """Put the readings side by side for every difference, then let code decide what it is.

    rules    exact: what text differs and whether a value moved. Cannot read meaning. Formatting and
             administrative differences are settled here and go to no other reader.
    Compare  reads the clause as we sent it against the clause as returned: conflicting or same,
             with the sentences that conflict and the sentences that say the same.
             `ccmp` maps (v1 uid, v2 uid) to its checked answer, {"failed": True} or {"not_read": True}.
    Verify   reads each changed sentence of one version against the other version's PDF, in both
             directions: supported, contradicted or not stated. `xv` is {"v1": {sentence: judgment},
             "v2": {...}}; `claims` maps id(hunk) to the sentences sent for it when they are not the default.

    The stricter reading wins:
      * a spelling fix becomes a change when Compare or Verify reads a different meaning;
      * a value that differs (amount, rate, period, number, sign) is never cleared;
      * an added or removed clause is held unless Verify reads the other version as stating the same
        obligation with the same values (then it is listed as moved).
    The one way a reader loosens the rules: a change of wording is cleared when Verify reads the same meaning
    in both directions and Compare does not read a conflict. A one-word swap between two real words needs
    more: Compare must read the passage as the same outright; its silence, a missing answer or a failed call
    is not consent.

    `ignore_readers` ("verify", "compare") decides as if that reader had not answered.
    `not_read` holds the sentences that were too long to send: a change that rests on one is marked "not read".
    """
    ignore = set(ignore_readers or ())
    use_c = ccmp is not None and "compare" not in ignore
    use_v = xv is not None and "verify" not in ignore
    xv = xv or {}
    in_question = Counter((h.v1_uid, h.v2_uid) for h in hunks if h.cls in ("substantive", "spelling"))
    labelled = []
    for h in hunks:
        key, settled = (h.v1_uid, h.v2_uid), _settled(h)
        # -- Compare's reading of this clause
        cc = (ccmp or {}).get(key) if use_c and h.v1_uid and h.v2_uid and not settled else None
        cview, strong, c_rel, c_answered = None, False, "not_asked", False
        if cc is not None and cc.get("not_read"):
            cview = {"relation": "not_read", "note": "This clause is longer than Compare reads in one request. It was not sent, and it was not cut."}
        elif cc is not None and cc.get("failed"):
            c_rel = "silent"
            cview = {"relation": "silent", "failed": True, "clause_relation": None, "clause_probability": None,
                     "note": "Compare did not give a usable answer for this clause."}
        elif cc is not None:
            c_answered = True
            findings = cc["findings"]
            hit = sorted((f for f in findings if f["relation"] == "conflict" and _covers(f, h)),
                         key=lambda f: -(f["confidence"] or 0))
            strong_hits = [f for f in hit if (f["confidence"] or 0) >= P.CONFLICT_CONFIDENCE_FLOOR]
            same = [f for f in findings if f["relation"] == "duplicate" and _covers(f, h)
                    and f["kind"] in ("restated", "verbatim") and (f["confidence"] or 0) >= P.RESTATED_CONFIDENCE_FLOOR]
            sure = (cc["probability"] or 0) >= P.RESTATED_CONFIDENCE_FLOOR
            whole_clause_conflict = cc["relation"] == "conflicting" and sure and in_question.get(key) == 1
            if strong_hits:
                cview, strong, c_rel = _cmp_view(strong_hits[0], "conflict", cc), True, "conflict"
            elif whole_clause_conflict:
                any_conflict = [f for f in findings if f["relation"] == "conflict"]
                cview = _cmp_view(any_conflict[0], "conflict", cc) if any_conflict else _clause_view("conflict", cc)
                strong, c_rel = True, "conflict"
            elif same:
                cview, c_rel = _cmp_view(same[0], "same", cc), "same"
            elif cc["relation"] == "same" and sure:
                cview, c_rel = _clause_view("same", cc), "same"
            else:
                c_rel = "silent"
                cview = {"relation": "silent", "clause_relation": cc["relation"], "clause_probability": cc["probability"],
                         "note": "Compare read this clause but did not place this passage as a conflict or as the same."}
        # -- Verify's reading of this sentence
        c1, c2 = (claims or {}).get(id(h)) or ([h.v1_sentence] if h.v1_sentence else [], [h.v2_sentence] if h.v2_sentence else [])
        one_sided = h.cls == "substantive" and h.kind in ("added", "removed") and not (c1 and c2)
        vview, v_rel, changed, both_same = None, "not_asked", False, False
        if use_v and not settled and (c1 or c2):
            j1 = [(xv.get("v1") or {}).get(t) for t in c1]
            j2 = [(xv.get("v2") or {}).get(t) for t in c2]

            def same_all(js):
                return bool(js) and all(j and j["verdict"] == "supported" and (j["supported"] or 0) >= P.MEANING_SAME_FLOOR for j in js)
            contradicted = [j for j in j2 + j1 if j and j["verdict"] == "contradicted" and (j["contradicted"] or 0) >= P.MEANING_CHANGED_FLOOR]
            if one_sided:
                js = j2 or j1
                absent = contradicted or [j for j in js if j and j["verdict"] == "not_stated"]
                v_rel = "same" if same_all(js) else "changed" if absent else "unclear"
                changed = v_rel == "changed"
            else:
                changed, both_same = bool(contradicted), same_all(j2) and same_all(j1)
                v_rel = "changed" if changed else "same" if both_same else "unclear"
            vview = {"relation": v_rel, "returned_against_sent": _worst(j2), "sent_against_returned": _worst(j1)}
            if any(j is None for j in j1 + j2):
                vview["partial"] = True                       # a passage that was sent has no reading
            if any(t in not_read for t in c1 + c2):
                vview.update(not_read=True, note="A passage of this change is longer than Verify reads in one request. "
                                                 "It was not sent, and it was not cut.")
        # -- code decides
        cls, kind, promoted, explained = h.cls, h.kind, False, False
        swap, value_kinds = bool(h.extra.get("one_word_swap")), h.extra.get("value_kinds")
        decided: dict[str, str] = {}
        if one_sided:
            other = None if sources is None else sources[0] if h.kind == "added" else sources[1]
            if v_rel == "same" and _values_stated(" ".join(c2 or c1), other):
                cls, kind = "reworded", "moved"
                decided["verify"] = "cleared"
        elif h.cls == "substantive" and h.kind == "wording" and not value_kinds and both_same:
            if strong:
                decided["compare"] = "vetoed"
            elif swap and c_rel != "same":
                if c_answered:
                    decided["compare"] = "withheld"
            else:
                cls = "reworded"
                decided["verify"] = "cleared"
                if swap:
                    decided["compare"] = "consented"
        elif h.cls == "spelling":
            explained = any(o is not h and o.cls == "substantive" and _key(o) == _key(h) and o.v2_sentence == h.v2_sentence
                            for o in hunks)
            if (changed or strong) and not explained:
                cls, promoted = "substantive", True
                if changed and not strong:
                    decided["verify"] = "held_alone"
                if strong and not changed:
                    decided["compare"] = "held_alone"
        confirms = [n for n, ok in (("Verify", changed), ("Compare", strong)) if ok]
        opinions = {"changed" if h.cls == "substantive" else "same"}
        opinions |= {"changed"} if v_rel == "changed" else {"same"} if v_rel == "same" else set()
        opinions |= {"changed"} if c_rel == "conflict" else {"same"} if c_rel == "same" else set()
        readers = {"rules": h.cls, "verify": v_rel, "compare": c_rel, "agree": len(opinions) == 1}
        why = _why(h, cls, v_rel, c_rel, promoted=promoted, explained=explained, swap=swap, one_sided=one_sided,
                   value_kinds=value_kinds)
        labelled.append({"hunk": h, "cls": cls, "kind": kind, "why": why, "compare": cview, "verify": vview,
                         "promoted": promoted, "explained": explained, "confirmed_by": confirms, "readers": readers,
                         "decided": decided, "cleared_with_compare": cls == "reworded" and swap,
                         "found_by": "clocked" if promoted else "both" if (cls == "substantive" and confirms) else "rules"})
    return labelled


def _why(h: Hunk, cls: str, v: str, c: str, *, promoted: bool, explained: bool, swap: bool, one_sided: bool, value_kinds) -> str:
    """One or two sentences: what the rules saw, what the readers said, and which reading won."""
    base = h.why.replace(" Compare can overrule this.", "").strip()
    said_changed = [n for n, ok in (("Verify", v == "changed"), ("Compare", c == "conflict")) if ok]
    said_same = [n for n, ok in (("Verify", v == "same"), ("Compare", c == "same")) if ok]
    if _settled(h):
        return f"{base} Settled by the rules: formatting and administrative differences are not sent to the API."
    if v == "not_asked" and c == "not_asked":
        return f"{base} No Clocked reader gave a reading of this one, so it rests on the rules."
    if h.cls == "spelling":
        if promoted:
            return (f"The rule layer took this for a spelling fix. {_names(said_changed)} {_verb(said_changed, 'read')} "
                    "the two versions as saying different things. The stricter reading wins, so it is held as a change.")
        if said_changed and explained:
            return (f"{base} {_names(said_changed)} {_verb(said_changed, 'read')} a change in this sentence. They read "
                    "whole sentences, and this one also holds another change, which is held on its own: their reading "
                    "is of that change, so the spelling fix is not promoted.")
        if said_same:
            return f"{base} {_names(said_same)} {_verb(said_same, 'read')} the same meaning in both versions."
        return f"{base} Neither Verify nor Compare read a change of meaning."
    never = value_kinds or h.kind not in ("wording", "added", "removed")
    if cls == "reworded" and one_sided:
        return ("The rule layer found no clause with this text in the other version. Verify reads the other version as "
                "stating the same obligation, with the same amounts and periods, so it is listed as moved, not as "
                f"{h.kind}.")
    if cls == "reworded":
        cmp_part = "Compare reads the passage as the same" if c == "same" else "Compare does not read a conflict"
        tail = (" A one-word swap is cleared only on that explicit agreement." if swap else "")
        return ("The words changed, so the rule layer read a change. Verify reads each version's sentence as supported "
                f"by the other version, and {cmp_part}. The readers disagree with the rules here; for a change of "
                f"wording, and only then, their agreement clears it as a faithful rewording.{tail}")
    parts = [base]
    if value_kinds:
        parts.append("A value changed inside the rewritten passage, and a value that differs is never cleared.")
    if one_sided and v == "changed":
        parts.append("Verify does not find this obligation in the other version.")
    elif said_changed and not said_same:
        parts.append(f"{_names(said_changed)} also {_verb(said_changed, 'read')} a change of meaning.")
    elif said_changed and said_same:
        parts.append(f"{_names(said_changed)} {_verb(said_changed, 'read')} a change, {_names(said_same)} "
                     f"{_verb(said_same, 'read')} the same meaning. The stricter reading wins, so it is held.")
    elif said_same:
        if value_kinds:
            parts.append(f"{_names(said_same)} {_verb(said_same, 'read')} the same meaning; the stricter reading wins "
                         "and it is held all the same.")
        elif never:
            parts.append(f"{_names(said_same)} {_verb(said_same, 'read')} the same meaning, but a value that differs is "
                         "never cleared: the stricter reading wins.")
        elif swap:
            parts.append("One real word was swapped for another. That is cleared only when Verify supports it in both "
                         "directions and Compare reads the passage as the same outright; without both, the stricter "
                         "reading wins and it is held.")
        else:
            parts.append(f"{_names(said_same)} {_verb(said_same, 'read')} the same meaning, but a rewording is cleared "
                         "only when Verify supports it in both directions and Compare does not object. The stricter "
                         "reading wins, so it is held.")
    else:
        parts.append("Neither Verify nor Compare placed it either way, so the rule layer's reading stands.")
    return " ".join(parts)


# ------------------------------------------------------------------ changes
_ORDER = {"substantive": 0, "reworded": 1, "administrative": 2, "spelling": 3, "formatting": 4}
_STOPWORDS = set("the of and to in or by any shall will this that for with be is not may its each as on at are an "
                 "from must has have been was were which such all other under".split())
HEADER_LABEL = "Page header or footer"


def _sig(text: str) -> set[str]:
    return {t.casefold() for t in re.findall(r"[^\W_]+", text or "") if len(t) >= 3 and t.casefold() not in _STOPWORDS}


def _same_edit(a: Hunk, b: Hunk) -> bool:
    """Two differences of one clause that are one change: edits inside the same sentence (the sentence is what
    Verify reads and what a route is about), or the same replacement made in two places (a state named in the
    governing-law sentence and again in the venue sentence). Anything else is a change of its own."""
    if a.kind in ("added", "removed") or b.kind in ("added", "removed"):
        return False
    if (a.v2_sentence and a.v2_sentence == b.v2_sentence) or (a.v1_sentence and a.v1_sentence == b.v1_sentence):
        return True
    if a.kind != b.kind or "table_row" in a.extra or "table_row" in b.extra:
        return False
    if a.extra.get("old") and a.extra.get("new"):
        return a.extra.get("old") == b.extra.get("old") and a.extra.get("new") == b.extra.get("new")
    return bool(_sig(a.v1_changed) & _sig(b.v1_changed)) and bool(_sig(a.v2_changed) & _sig(b.v2_changed))


def _find(doc: TextDoc, quote: str, page: int | None = None, within: str = "") -> list[dict]:
    if not quote or len(quote) > LOCATE_MAX or doc.path is None:
        return []
    try:
        return locate(doc.path, quote, page, within=within if len(within) <= LOCATE_MAX else "")
    except Exception:
        return []


def _side(doc: TextDoc, quote: str, changed: str, page: int, within: str = "") -> dict:
    # The quote carries a few words of context on each side, so it is the less ambiguous thing to look for.
    found = _find(doc, quote, page, within)
    if not found and changed and len(squash(changed)) >= 12:
        found = _find(doc, changed, page, within)
    side = {"quote": quote, "changed": changed, "page": found[0]["page"] if found else (page or None),
            "rects": found[0]["rects"] if found else [], "located": bool(found)}
    if len(found) > 1:                           # the quote runs over a page break
        side["continued"] = found[1:]
    return side


def _absent() -> dict:
    return {"quote": "", "changed": "", "page": None, "rects": [], "located": False, "absent": True}


def _part(it: dict, v1: TextDoc, v2: TextDoc, view1: dict, view2: dict) -> dict:
    h: Hunk = it["hunk"]
    part = {"summary": h.summary, "kind": h.kind}
    for key, doc, view, uid, quote, changed, page, sentence in (
            ("v1", v1, view1, h.v1_uid, h.v1_quote, h.v1_changed, h.v1_page, h.v1_sentence),
            ("v2", v2, view2, h.v2_uid, h.v2_quote, h.v2_changed, h.v2_page, h.v2_sentence)):
        whole_clause = h.kind in ("added", "removed") and uid in view
        if h.kind in ("added", "removed") and not quote:
            part[key] = _absent()
            continue
        side = None
        if whole_clause and view[uid].text:      # an added or removed clause is quoted whole
            side = _side(doc, view[uid].text, view[uid].text, page)
            if not side["located"]:
                side = None
        part[key] = side or _side(doc, quote, changed, page, sentence)
    if h.kind == "renumbered":
        part["renumbered"] = [{"v1": a_no, "v2": b_no, "uid": [a_uid, b_uid],
                               "title": getattr(view2.get(b_uid), "title", "")}
                              for a_uid, a_no, b_uid, b_no in h.extra.get("renumbered") or []]
    if h.extra.get("table_row"):
        part["table_row"] = h.extra["table_row"]
    if h.capped:
        part["capped"] = True
    return part


def build_changes(labelled: list[dict], v1: TextDoc, v2: TextDoc, view1: dict | None = None, view2: dict | None = None) -> list[dict]:
    """One card per change. A held change is one difference (or one replacement made in several places of a
    clause), so two separate changes in one clause are two cards, each with its own route. Differences that
    are not held are listed per clause and class."""
    view1 = view1 if view1 is not None else {c.uid: c for c in v1.clauses}
    view2 = view2 if view2 is not None else {c.uid: c for c in v2.clauses}
    pos1 = {c.uid: i for i, c in enumerate(v1.clauses)}
    pos2 = {c.uid: i for i, c in enumerate(v2.clauses)}
    groups: list[list[dict]] = []
    index: dict = {}
    for n, it in enumerate(labelled):
        h, key = it["hunk"], _key(it["hunk"])
        it["n"] = n
        if it["cls"] == "substantive":
            g = next((g for g in groups if g[0]["cls"] == "substantive" and _key(g[0]["hunk"]) == key
                      and any(_same_edit(o["hunk"], h) for o in g)), None)
        else:
            g = index.get((key, it["cls"], it["kind"] in ("moved", "renumbered")))
        if g is None:
            g = []
            groups.append(g)
            if it["cls"] != "substantive":
                index[(key, it["cls"], it["kind"] in ("moved", "renumbered"))] = g
        g.append(it)
    other_text = Counter(_key(it["hunk"]) for it in labelled if it["kind"] not in ("moved", "renumbered"))
    changes = []
    for items in groups:
        first = items[0]
        h0: Hunk = first["hunk"]
        cls = first["cls"]
        parts = [_part(it, v1, v2, view1, view2) for it in items]
        rank = {"conflict": 0, "silent": 1, "not_read": 1, "same": 2}
        cmp_views = sorted((it["compare"] for it in items if it["compare"]), key=lambda v: rank.get(v["relation"], 9))
        vrank = {"changed": 0, "unclear": 1, "same": 2}
        ver_views = sorted((it["verify"] for it in items if it["verify"]), key=lambda v: vrank.get(v["relation"], 9))
        found = {it["found_by"] for it in items}
        confirmed = sorted({n for it in items for n in it["confirmed_by"]}, reverse=True)
        promoted = any(it["promoted"] for it in items)
        names = (["Rules"] if not promoted else []) + confirmed
        summaries = list(dict.fromkeys(p["summary"] for p in parts))
        if cmp_views and cmp_views[0].get("aligned") and cmp_views[0]["relation"] in ("conflict", "same"):
            cv = dict(cmp_views[0])
            for key, doc, page in (("v1", v1, h0.v1_page), ("v2", v2, h0.v2_page)):
                q = cv.get(f"{key}_quote")
                spot = _find(doc, q, page) if q and len(q) >= 12 else []
                cv[key] = {"quote": q, "page": spot[0]["page"] if spot else None, "rects": spot[0]["rects"] if spot else []}
            cmp_views[0] = cv
        n1 = getattr(view1.get(h0.v1_uid), "number", None)
        n2 = getattr(view2.get(h0.v2_uid), "number", None)
        header = h0.clause == "header" and not (h0.v1_uid or h0.v2_uid)
        kinds = {it["kind"] for it in items}
        summary = "; ".join(summaries[:3]) + (" …" if len(summaries) > 3 else "")
        if first["kind"] == "moved" and h0.kind == "moved":               # the rule layer matched the same clause elsewhere
            same_text = other_text.get(_key(h0), 0) == 0
            summary = ("Clause moved, same text" if same_text else "Clause moved") + (f": {n1} → {n2}" if n1 and n2 and n1 != n2 else "")
        elif first["kind"] == "moved":                                    # held as added or removed until Verify read it
            summary = "Clause moved, same meaning"
        elif header and cls == "administrative":
            summary = "Page header or footer differs"
        agree = all(it["readers"]["agree"] for it in items)
        lead = min(items, key=lambda it: (vrank.get(it["readers"]["verify"], 3), rank.get(it["readers"]["compare"], 3)))
        if cls == "substantive":
            label = " + ".join(names)
        elif cls == "reworded":
            label = "Rules, cleared by Verify and Compare" if any(it["cleared_with_compare"] for it in items) else "Rules, cleared by Verify"
        else:
            label = "Rules"
        changes.append({
            "clause": h0.clause, "clause_v1": n1, "clause_v2": n2,
            "clause_label": HEADER_LABEL if header else f"{n1} → {n2}" if n1 and n2 and n1 != n2 else h0.clause,
            "uid": {"v1": h0.v1_uid, "v2": h0.v2_uid},
            "title": HEADER_LABEL if header else h0.title, "cls": cls, "blocking": cls == "substantive",
            "kind": next(iter(kinds)) if len(kinds) == 1 else "mixed",
            "summary": summary,
            "why": first["why"],
            "found_by": "clocked" if "clocked" in found else "both" if "both" in found else "rules",
            "found_by_label": label,
            "promoted": promoted,
            "readers": {**lead["readers"], "agree": agree},
            "parts": parts, "compare": cmp_views[0] if cmp_views else None, "verify": ver_views[0] if ver_views else None,
            "playbook": None, "decide": None, "route": None, "route_reason": None, "claims": [],
            "same_sentence_as": None,
            "not_read": any((it["compare"] or {}).get("relation") == "not_read" or (it["verify"] or {}).get("not_read") for it in items),
            "_items": items, "_order": (_ORDER.get(cls, 9), pos2.get(h0.v2_uid, pos1.get(h0.v1_uid, 10 ** 6) if not header else 10 ** 6), first["n"]),
        })
    changes.sort(key=lambda c: c["_order"])
    for i, c in enumerate(changes, 1):
        c["id"] = f"c{i}"
    # A spelling or formatting edit inside a sentence that also holds a held change is shown with that change too.
    held = [c for c in changes if c["blocking"]]
    for c in changes:
        if c["cls"] not in ("spelling", "formatting") or c["kind"] in ("moved", "renumbered"):
            continue
        for it, part in zip(c["_items"], list(c["parts"])):
            h = it["hunk"]
            host = next((x for x in held for o in x["_items"]
                         if _key(o["hunk"]) == _key(h) and ((h.v2_sentence and o["hunk"].v2_sentence == h.v2_sentence)
                                                            or (h.v1_sentence and o["hunk"].v1_sentence == h.v1_sentence))), None)
            if host is None:
                continue
            part["same_sentence_as"] = host["id"]
            host["parts"].append({**part, "same_sentence": True, "from": c["id"], "cls": c["cls"]})
            if c["same_sentence_as"] is None:
                c["same_sentence_as"] = host["id"]
                c["why"] += (f" That change is {host['id']} ({host['summary']})." if it["explained"] and it["cls"] == "spelling" else
                             f" The same sentence also holds change {host['id']} ({host['summary']}), which is held on its own.")
    for c in changes:
        c["decided_by"] = {k: v for it in c.pop("_items") for k, v in it["decided"].items()}   # which reader's answer set the class
        c.pop("_order")
    return changes


# ------------------------------------------------------------------- ledger
def _cl(label: str, title: str) -> str:
    return f"clause {label} ({title})" if title else f"clause {label}"


def _clause_name(c) -> str:
    if c.kind == "paragraph":
        return f"Paragraph {c.number.lstrip('¶ ').strip() or c.number}"
    if c.kind == "heading":
        return f"Section “{c.title}”" if c.title else f"Section {c.number}"
    return f"Clause {c.number} ({c.title})" if c.title else f"Clause {c.number}"


def build_ledger(changes: list[dict], v2: TextDoc | None = None) -> tuple[str, list[tuple[int, int, str]]]:
    """The list of differences Verify checks the cover note against, one line each, in plain sentences.
    Returns the text and, per line, (start, end, change id)."""
    lines, spans, pos = [], [], 0

    def add(text: str, cid: str | None):
        nonlocal pos
        lines.append(text)
        spans.append((pos, pos + len(text), cid or ""))
        pos += len(text) + 1

    def is_header(c):
        return c.get("clause") == "header" and not any((c.get("uid") or {}).values())

    named, seen = [], set()
    for c in changes:
        if not c["blocking"]:
            continue
        ident = tuple((c.get("uid") or {}).get(k) for k in ("v1", "v2"))
        ident = ident if any(ident) else (c["clause"],)
        if ident in seen:
            continue
        seen.add(ident)
        kind = c["parts"][0]["kind"] if c["parts"] else c["kind"]
        named.append("the page header or footer" if is_header(c) else
                     _cl(c["clause"], c["title"]) + (", added" if kind == "added" else ", removed" if kind == "removed" else ""))
    if named:
        add(f"The returned copy changes {len(named)} commercial or legal term{'' if len(named) == 1 else 's'} of the agreement we sent: "
            + "; ".join(named) + ".", None)
    else:
        add("The returned copy does not change any commercial or legal term of the agreement we sent. The only "
            "differences are the ones listed below.", None)
    renumbered: dict[str, str] = {}                # v2 uid -> change id, for clauses that only changed their number
    for c in changes:
        name = _cl(c["clause"], c["title"])
        for p in c["parts"]:
            if p.get("same_sentence"):             # listed once, under its own class
                continue
            a, b = squash(p["v1"]["quote"]), squash(p["v2"]["quote"])
            kind = p.get("kind")
            if c["cls"] == "substantive" and is_header(c):
                add(f"Substantive change in the page header or footer: {p['summary']}. We sent: \"{a}\". "
                    f"The returned copy says: \"{b}\".", c["id"])
            elif c["cls"] == "substantive" and kind == "added":
                add(f"Substantive change: {name} was added. It is in the returned copy and not in the agreement "
                    f"we sent. The returned copy says: \"{b}\"" + ("" if b.endswith((".", "!", "?")) else "."), c["id"])
            elif c["cls"] == "substantive" and kind == "removed":
                add(f"Substantive change: {name} of the agreement we sent was removed. It is not in the returned "
                    f"copy. We sent: \"{a}\"" + ("" if a.endswith((".", "!", "?")) else "."), c["id"])
            elif c["cls"] == "substantive":
                add(f"Substantive change in {name}: {p['summary']}. We sent: \"{a}\". "
                    f"The returned copy says: \"{b}\".", c["id"])
            elif c["cls"] == "reworded" and kind in ("added", "removed"):
                add(f"{name[0].upper()}{name[1:]} stands at a different place or in other words in the returned copy; "
                    "both versions state the same obligation.", c["id"])
            elif c["cls"] == "reworded":
                add(f"Clause {c['clause']} ({c['title']}) was reworded without changing its meaning. We sent: \"{a}\". "
                    f"The returned copy says: \"{b}\".", c["id"])
            elif c["cls"] == "administrative" and (kind == "header" or is_header(c)):
                add(f"The page header or footer differs between the two files: \"{a}\" became \"{b}\". It is not part "
                    "of any clause.", c["id"])
            elif c["cls"] == "administrative":
                add(f"Administrative change in clause {c['clause']} ({c['title']}): the notice address was updated. "
                    f"We sent: \"{a}\". The returned copy says: \"{b}\".", c["id"])
            elif c["cls"] == "spelling":
                add(f"Spelling corrected in clause {c['clause']} ({c['title']}): {p['summary']}. No change of meaning.", c["id"])
            elif kind == "renumbered":
                for r in p.get("renumbered") or []:
                    if r["uid"][1]:
                        renumbered[r["uid"][1]] = c["id"]
                if not p.get("renumbered"):
                    add(f"Renumbering only in {name}: {p['summary']}.", c["id"])
            elif kind == "moved":
                add(f"{name[0].upper()}{name[1:]} was moved to a different place in the returned copy"
                    + (f" (numbered {c.get('clause_v1')} in the agreement we sent)" if c.get("clause_v1") and c.get("clause_v1") != c.get("clause_v2") else "")
                    + ". Moving it changes no words or numbers.", c["id"])
            else:
                add(f"Formatting only in clause {c['clause']} ({c['title']}): \"{a}\" became \"{b}\". Same words and numbers.", c["id"])
    if v2 is not None:
        touched = {(c.get("uid") or {}).get("v2") for c in changes if not (c["kind"] == "renumbered" and c["cls"] == "formatting")}
        held = {(c.get("uid") or {}).get("v2") for c in changes if c["blocking"]}
        old_no = {r["uid"][1]: r["v1"] for c in changes for p in c["parts"] for r in p.get("renumbered") or []}
        for c in v2.clauses:
            if c.number == "0" or c.kind == "preamble":
                continue
            if c.uid in renumbered and c.uid not in touched:
                add(f"{_clause_name(c)} was numbered {old_no.get(c.uid)} in the agreement we sent. Apart from its number "
                    "it reads exactly as the agreement we sent.", renumbered[c.uid])
            elif c.uid in renumbered:
                add(f"{_clause_name(c)} was numbered {old_no.get(c.uid)} in the agreement we sent.", renumbered[c.uid])
            elif c.uid not in touched:
                add(f"{_clause_name(c)} is unchanged: the returned copy reads exactly as the agreement we sent.", None)
            elif c.uid not in held:
                # only spelling, formatting, an address or a faithful rewording: say so in a sentence of its own, so that
                # a true statement such as "service credits are unchanged" has something to be supported by
                add(f"{_clause_name(c)}: no commercial or legal term of this clause changed. The only differences in it "
                    "are spelling, formatting, an address or wording that keeps the same meaning.", None)
    return "\n".join(lines), spans


# ------------------------------------------------------------------- review
def review(v1_path: str | Path, v2_path: str | Path, letter: str, gw: Gateway, out_dir: str | Path, *,
           case: dict | None = None, playbook_path: str | Path | None = None, concurrency: int | None = None,
           route: bool = True, terms: bool = True, ignore_readers=frozenset()) -> dict:
    """Review a returned copy against the version we sent. Raises only when a PDF cannot be read at all.

    route=False skips Rerank and Decide, terms=False skips Extract (used by the evaluation to save credits).
    ignore_readers: any of "verify", "compare". The decision is made as if that reader had not answered:
    its reading of the clauses and sentences is still requested (the same requests as a full run, so a
    recording replays) but is not used to classify a difference, and with "verify" the cover note is taken
    at its word (not sent). The gate is not a reader and stays on.
    """
    ignore = frozenset(ignore_readers or ())
    if ignore - set(READERS):
        raise ValueError(f"ignore_readers takes {', '.join(READERS)}; got {sorted(ignore - set(READERS))}")
    letter = letter if isinstance(letter, str) else ""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    v1, v2 = read_pdf(v1_path, "v1"), read_pdf(v2_path, "v2")
    errors: list[dict] = []
    warnings: list[dict] = []
    workers = concurrency or int(os.environ.get("CLOCKED_CONCURRENCY", "3"))

    def fail(endpoint: str, step: str, message: str, kind: str):
        errors.append({"endpoint": endpoint, "step": step, "message": message, "status": None, "kind": kind})

    def stage(source: str, step: str, fn, default):
        """Run one stage of our own code. If it breaks, say which one and carry on with the rest."""
        try:
            return fn()
        except Exception as exc:
            fail(source, step, f"This step failed inside the application ({type(exc).__name__}), so its result is "
                 "missing from the review.", "internal")
            return default

    def run(endpoint: str, request: dict, label: str, role: str | None = None, check=None):
        """Call one endpoint and check the answer. A failed call or an unusable answer is recorded, and None returned."""
        name = PRODUCT_NAME.get(endpoint, endpoint)
        try:
            resp = gw.call(endpoint, request, label)
        except StepError as e:
            errors.append(e.as_dict())
            return None
        except Exception as exc:
            fail(name, label, f"{name} ({role or label}): the call could not be made ({type(exc).__name__}).", "internal")
            return None
        if check is None:
            return resp
        try:
            return check(resp)
        except BadResponse as e:
            fail(name, label, f"{name} ({role or label}): {e}", "bad_response")
        except Exception as exc:
            fail(name, label, f"{name} ({role or label}): the response could not be read ({type(exc).__name__})", "bad_response")
        return None

    hunks = stage("Rules", "compare the two texts", lambda: compare_documents(v1, v2), None)
    rules_ran = hunks is not None
    hunks = hunks or []
    playbook = stage("Rerank", "read the playbook", lambda: json.loads(
        Path(playbook_path or ROOT / "data" / "playbook.json").read_text(encoding="utf-8")), {"topics": []})
    b1, b2 = pdf_b64(v1_path), pdf_b64(v2_path)

    # ---- 1. the gate: are these two versions of one agreement?
    # Asked for the relation only (no passages, no kinds): measured at 1 to 3 credits against about 45 with passages,
    # with the same answer. The passages are not needed here; each differing clause is read on its own below.
    gate = run("compare", {"documents": [{"id": "v1", "pdf": b1}, {"id": "v2", "pdf": b2}],
                           "instructions": P.GATE_INSTRUCTIONS, "evidence": "none", "kinds": False},
               "compare v1 v2", "whole documents", check_gate)
    wrong_pair = gate is not None and (gate["relation"] == "unrelated" or gate["related"] < P.SAME_AGREEMENT_FLOOR)

    res: dict = {}
    changes: list[dict] = []
    ccmp: dict[tuple, dict] = {}
    clause_pairs: dict[tuple, dict] = {}
    term_rows: dict = {}
    guard: list[dict] = []
    note = {"text": letter, "claims": [], "grounded": None, "verdict": None, "ran": False,
            "statements": 0, "checked": 0, "instructions": [], "skipped": []}

    if not wrong_pair:
        aligned = stage("Rules", "align the clauses", lambda: align(v1, v2), [])
        view1 = {ca.uid: ca for ca, _ in aligned if ca is not None}
        view2 = {cb.uid: cb for _, cb in aligned if cb is not None}

        # ---- 2. Clocked, in parallel
        jobs: dict[str, tuple] = {
            "guard_v2": ("injection", {"pdf": b2, "source": "content", "instructions": P.GUARD_INSTRUCTIONS,
                                       "return_pdf": "box"}, "guard returned copy", "returned copy", check_guard),
        }
        if terms:
            for key, b, who in (("v1", b1, "version we sent"), ("v2", b2, "returned copy")):
                jobs[f"terms_{key}"] = ("extract", {"pdf": b, "fields": P.EXTRACT_FIELDS, "return_text": True, "return_pdf": "box"},
                                        f"extract terms {key}", who, check_extract)
        clause_pairs = clauses_to_compare(hunks, view1, view2)
        labels = Counter(p["label"] for p in clause_pairs.values())
        for key, p in clause_pairs.items():       # Compare, clause by clause: our text against theirs
            step = f"compare clause {p['label']}" + (f" [{key[0]} / {key[1]}]" if labels[p["label"]] > 1 else "")
            if len(p["v1"]) + len(p["v2"]) > SOURCE_CHARS:
                ccmp[key] = {"not_read": True}
                fail("Compare", step, f"Compare (clause {p['label']}): the clause is longer than Compare reads in one request "
                     f"({len(p['v1']) + len(p['v2']):,} characters). It was not sent, and it was not cut.", "too_long")
                continue
            jobs[f"cc|{key[0]}|{key[1]}"] = (
                "compare", {"documents": [{"id": "v1", "text": p["v1"]}, {"id": "v2", "text": p["v2"]}],
                            "instructions": P.COMPARE_CLAUSE_INSTRUCTIONS, "evidence": "all"},
                step, f"clause {p['label']}", lambda r, p=p: check_clause_compare(r, p["v1"], p["v2"]))
        hclaims = {id(h): hunk_claims(h, view1, view2) for h in hunks if not _settled(h)}
        s1, s2 = xverify_claims(hunks, view1, view2)
        xv_batches: dict[str, list[list[str]]] = {}
        unread: set[str] = set()
        # sentences of the returned copy are read against the PDF we sent, and the other way round
        for side, claims_, pdf, step, role in (("v2", s2, b1, "verify returned sentences against v1", "returned sentences against the version we sent"),
                                               ("v1", s1, b2, "verify sent sentences against v2", "sent sentences against the returned copy")):
            groups, too_long = batches(claims_)
            xv_batches[side] = groups
            unread |= set(too_long)
            for c in too_long:
                fail("Verify", step, f"Verify ({role}): one passage is longer than Verify reads in one request "
                     f"({len(c):,} characters). It was not sent, and it was not cut; the change it belongs to was not read.", "too_long")
            for i, group in enumerate(groups):
                jobs[f"xv|{side}|{i}"] = (
                    "grounding", {"pdf": pdf, "answer": " ".join(group), "claims": group,
                                  "instructions": P.XVERIFY_INSTRUCTIONS, "threshold": P.MEANING_CHANGED_FLOOR,
                                  "evidence": "all"},
                    step + (f" ({i + 1} of {len(groups)})" if len(groups) > 1 else ""), role,
                    lambda r, group=group: check_verify(r, group))
        note_chunks = _chunks(letter, GUARD_CHARS) if letter.strip() else []
        for i, chunk in enumerate(note_chunks):
            jobs[f"guard_note|{i}"] = ("injection", {"text": chunk, "source": "content", "instructions": P.GUARD_INSTRUCTIONS},
                                       "guard cover note" + (f" ({i + 1} of {len(note_chunks)})" if len(note_chunks) > 1 else ""),
                                       "cover note", check_guard)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = {k: pool.submit(run, *v) for k, v in jobs.items()}
            res = {k: f.result() for k, f in futs.items()}

        for key in clause_pairs:
            if key not in ccmp:
                ccmp[key] = res.get(f"cc|{key[0]}|{key[1]}") or {"failed": True}
        xv: dict[str, dict] = {"v1": {}, "v2": {}}
        for side, groups in xv_batches.items():
            for i, group in enumerate(groups):
                for text, j in zip(group, res.get(f"xv|{side}|{i}") or []):
                    xv[side][text] = j

        # ---- 3. the readings side by side; code decides
        labelled = stage("Rules", "decide what each difference is", lambda: cross_label(
            hunks, ccmp, xv, ignore_readers=ignore, claims=hclaims, sources=(v1.full_text, v2.full_text), not_read=unread), None)
        if labelled is None:                      # never show an unjudged difference as settled: fall back to the rules alone
            labelled = stage("Rules", "list the differences the rules found", lambda: cross_label(hunks, None, None), [])
        changes = stage("Rules", "list the changes", lambda: build_changes(labelled, v1, v2, view1, view2), [])
        term_rows = stage("Extract", "read the key terms", lambda: _terms(res.get("terms_v1"), res.get("terms_v2"), v1, v2), {})
        guard = stage("Guard", "read Guard's answers", lambda: _guard(
            [res.get(f"guard_note|{i}") for i in range(len(note_chunks))], res.get("guard_v2"), v2, letter), [])

        # ---- 4. Verify the cover note, statement by statement
        note = stage("Verify", "check the cover note", lambda: _note(letter, changes, v2, guard, run, ignore, fail, workers), note)

        # ---- 5. Rerank the playbook, then Decide, for each open change on its own
        fee = ((term_rows.get("annual_fee") or {}).get("v1") or {}).get("value")
        deal = {"annual_fee": fee, "initial_term": ((term_rows.get("initial_term") or {}).get("v1") or {}).get("value")}
        deal = {k: v for k, v in deal.items() if v}
        open_changes = [c for c in changes if c["blocking"]] if route else []

        def route_one(c: dict):
            try:
                c["playbook"] = _rerank(c, playbook, fee, run)
                c["decide"] = _decide(c, run, deal)
            except Exception as exc:
                fail("Decide", f"route {c['clause']}", f"Routing this change failed inside the application ({type(exc).__name__}).", "internal")
            _final_route(c)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(route_one, open_changes))
        for c in changes:
            if c["cls"] == "administrative":
                c["route"] = "accept"
                c["route_reason"] = ("Administrative: a page header or footer is not part of any clause. Nothing to negotiate."
                                     if c["title"] == HEADER_LABEL else "Administrative: acknowledge and update the account record.")

    # ---- 6. code sets the status
    errors.sort(key=lambda e: (str(e.get("endpoint")), str(e.get("step")), str(e.get("message"))))
    for doc, name in ((v1, "the version we sent"), (v2, "the returned copy")):
        if doc.ocr_pages:
            warnings.append({"source": "Rules", "text": f"{_pages(doc.ocr_pages)} of {name} {'was' if len(doc.ocr_pages) == 1 else 'were'} "
                             "read through an OCR text layer over a scanned image. Quotes there are what the OCR read."})
    if note.get("text", "").strip() and not note["statements"] and not note["instructions"] and not wrong_pair:
        warnings.append({"source": "Cover note", "text": "The cover note holds no statement to check."})
    for row in note.get("unreadable_evidence") or []:
        warnings.append({"source": "Verify", "text": row})
    crop = stage("Rules", "measure the pages", lambda: content_box([v1.path, v2.path]), None)
    docs = stage("Rules", "render the pages", lambda: _assets(out, v1, v2, res, gate, crop, warnings), {})
    if wrong_pair:
        status, headline = "blocked", "Not the same agreement"
        reasons = [{"level": "block", "source": "Compare", "text":
                    f"Compare reads these two files as {gate['relation']} (related {gate['related']:.0%}): they are not two "
                    "versions of one agreement. The review stopped there and nothing else was sent. Check that the right "
                    "file was uploaded."}]
    else:
        status, headline, reasons = stage("Rules", "set the status", lambda: _status(
            changes, note, guard, v1, v2, errors, rules_ran=rules_ran, gate=gate), (None, None, None))
        if status is None:                        # the status itself could not be worked out: say so, never guess "ready"
            status, headline = "incomplete", "Review incomplete"
            reasons = [{"level": "incomplete", "source": e["endpoint"], "text": e["message"]} for e in errors]
    judged_with = len(errors)                     # a step of our own that breaks after this point must not leave a pass standing
    decided = stage("Rules", "sum up what each endpoint decided", lambda: _decided(
        changes, note, guard, reasons, wrong_pair, deal if not wrong_pair else {}), None) or _decided([], {"claims": []}, [], [], wrong_pair, {})
    result = {
        "schema": 1,
        "case": case or {"id": "upload", "title": "Uploaded agreement", "description": ""},
        "mode": "replay" if gw.audit and all(a["replayed"] for a in gw.audit) else "live" if gw.audit else "rules-only",
        "status": status, "headline": headline, "reasons": reasons,
        "documents": docs,
        "changes": changes,
        "compare": {
            "document": None if gate is None else {
                "verdict": gate["verdict"], "relation": gate["relation"], "related": gate["related"],
                "conflicts": gate["conflicts"], "same_passages": gate["same_passages"],
                "marked": {k: {kk: vv for kk, vv in m.items() if kk != "document"} for k, m in gate["marked"].items()}},
            "gate": {"asked": True, "answered": gate is not None, "passed": None if gate is None else not wrong_pair,
                     "stopped": wrong_pair, "floor": P.SAME_AGREEMENT_FLOOR,
                     "note": "The whole-document Compare is a gate: it answers whether the two files are versions of one "
                             "agreement. It is asked for the relation only, which costs one to three credits; each differing clause is read on its own."},
            "clauses": [{"clause": clause_pairs[key]["label"], "uid": list(key), "relation": cc["relation"], "probability": cc["probability"],
                         "conflicts": sum(f["relation"] == "conflict" for f in cc["findings"]),
                         "same": sum(f["relation"] == "duplicate" for f in cc["findings"])}
                        for key, cc in ccmp.items() if "findings" in cc],
            "clauses_asked": len(clause_pairs),
            "clauses_not_read": sum(1 for cc in ccmp.values() if cc.get("not_read")),
            "unaligned": sum(1 for cc in ccmp.values() for f in cc.get("findings") or [] for k in ("v1", "v2") if f[k]["aligned"] is False),
            "kinds": sorted({f["kind"] for cc in ccmp.values() for f in cc.get("findings") or [] if f["relation"] == "conflict" and f["kind"]}),
        },
        "note": note, "guard": guard, "terms": list(term_rows.values()),
        "hidden_text": [{"text": h.text, "page": h.page, "reason": h.reason, "reason_text": HIDDEN_WORDS.get(h.reason, "hidden text")}
                        for h in v2.hidden],
        "unread_pages": {"v1": list(v1.pages_without_text), "v2": list(v2.pages_without_text)},
        "ocr_pages": {"v1": list(v1.ocr_pages), "v2": list(v2.ocr_pages)},
        "ignored_readers": sorted(ignore),
        "warnings": warnings,
        "method": [
            "Code sets the status; no model does.",
            "Formatting and administrative differences are settled by the rules and are not sent to the API.",
            "Where the readings disagree, the stricter one wins. A value that differs is never cleared.",
            "A rewording is cleared only when Verify supports it in both directions and Compare does not object; "
            "a one-word swap between two real words also needs Compare to read the passage as the same.",
        ],
        "ablation": stage("Rules", "say what each endpoint changed", lambda: _ablation(changes, note, status, decided, wrong_pair), []),
        "endpoints": stage("Rules", "sum up the endpoints", lambda: _endpoint_summary(gw, changes, decided), []),
        "errors": errors,
        "audit": _audit(gw),
        "playbook_topics": [{"id": t["id"], "title": t["title"]} for t in playbook.get("topics") or []
                            if isinstance(t, dict) and "id" in t and "title" in t],
    }
    if crop:
        stage("Rules", "place the boxes on the cropped pages", lambda: remap_rects(result, crop), None)
    if len(errors) > judged_with and result["status"] in ("ready", "changes"):
        result["status"], result["headline"] = "incomplete", "Review incomplete"
        result["reasons"] = [x for x in reasons if x["level"] != "ok"] + [
            {"level": "incomplete", "source": e["endpoint"], "text": f"{e['step']}: {e['message']}"} for e in errors[judged_with:]]
    result = _clean(result)
    (out / "review.json").write_text(json.dumps(result, indent=1, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    return result


def _pages(pages: list[int]) -> str:
    return f"Page {pages[0]}" if len(pages) == 1 else "Pages " + ", ".join(str(p) for p in pages[:-1]) + f" and {pages[-1]}"


def _chunks(text: str, limit: int) -> list[str]:
    """Text in pieces of at most `limit` characters, broken at paragraph ends, then line ends, then spaces."""
    if len(text) <= limit:
        return [text]
    out, rest = [], text
    while len(rest) > limit:
        cut = max(rest.rfind("\n\n", 0, limit), rest.rfind("\n", 0, limit), rest.rfind(" ", 0, limit))
        cut = cut if cut > limit // 2 else limit
        out.append(rest[:cut])
        rest = rest[cut:]
    return out + ([rest] if rest.strip() else [])


def _clean(obj):
    """NaN and infinity are not JSON: a number that is neither finite nor meaningful becomes null."""
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    return obj


def _audit(gw: Gateway) -> dict:
    total = sum(int(a["credits"] or 0) for a in gw.audit)
    by = Counter()
    for a in gw.audit:
        by[a["endpoint"]] += int(a["credits"] or 0)
    return {"requests": gw.audit, "credits": total, "calls": len(gw.audit),
            "by_endpoint_share": {name: round(by[name] / total, 4) if total else 0.0 for name in sorted(by)}}


# --------------------------------------------------------------- sub-steps
def _locate_at(doc: TextDoc, pdf_obj: dict | None, start, end, value: str) -> list[dict]:
    """Place a value by where the API read it, not by the first place the same characters occur.
    The words around the offset pick the line; the value is then boxed on that line only."""
    if len(value) > LOCATE_MAX:
        return []
    text = (pdf_obj or {}).get("text")
    page = _page_of(pdf_obj, start)
    if not isinstance(text, str) or start is None or text[start:end] != value:
        return _find(doc, value, page) if value and len(squash(value)) >= 8 else []
    lo = max(text.rfind("\n", 0, start) + 1, start - 40)
    hi = text.find("\n", end)
    hi = min(len(text) if hi < 0 else hi, end + 40)
    line = _find(doc, text[lo:hi], page)
    if not line:
        return []
    pdf = pymupdf.open(doc.path)
    try:
        pg = pdf[line[0]["page"] - 1]
        w, h = pg.rect.width, pg.rect.height
        boxes = [[r.x0 / w, r.y0 / h, r.x1 / w, r.y1 / h] for r in pg.search_for(squash(value))]
    finally:
        pdf.close()
    inside = [[round(v, 5) for v in bx] for bx in boxes
              if any(bx[1] >= r[1] - 0.004 and bx[3] <= r[3] + 0.004 and bx[0] >= r[0] - 0.004 and bx[2] <= r[2] + 0.004
                     for r in line[0]["rects"])]
    return [{"page": line[0]["page"], "rects": inside or line[0]["rects"]}]


def _same_term(a: str | None, b: str | None) -> bool:
    """Two extracted values state the same term: same numbers, and one wording contains the other."""
    na, nb = normalise(a or ""), normalise(b or "")
    if na == nb:
        return True
    return bool(na and nb) and _values(a or "") == _values(b or "") and (na in nb or nb in na)


def _terms(r1: dict | None, r2: dict | None, v1: TextDoc, v2: TextDoc) -> dict:
    out = {}
    if not r1 and not r2:
        return out
    for name, label in P.TERM_LABELS.items():
        row = {"field": name, "label": label}
        for key, resp, doc in (("v1", r1, v1), ("v2", r2, v2)):
            f = ((resp or {}).get("fields") or {}).get(name) or {}
            val = f.get("value")
            pdf_obj = (resp or {}).get("pdf")
            try:
                found = _locate_at(doc, pdf_obj, f.get("start"), f.get("end"), val) if val else []
            except Exception:
                found = []
            aligned = None
            if val and isinstance((pdf_obj or {}).get("text"), str) and f.get("start") is not None:
                aligned = pdf_obj["text"][f["start"]:f["end"]] == val
            row[key] = {"value": squash(val) if val else val, "confidence": f.get("confidence"), "read": resp is not None,
                        "aligned": aligned, "page": found[0]["page"] if found else _page_of(pdf_obj, f.get("start")),
                        "rects": found[0]["rects"] if found else []}
        a, b = row["v1"]["value"], row["v2"]["value"]
        row["changed"] = None if (not row["v1"]["read"] or not row["v2"]["read"]) else not _same_term(a, b)
        out[name] = row
    return out


def _guard(note_resps: list[dict | None], v2_resp: dict | None, v2: TextDoc, letter: str) -> list[dict]:
    out = []
    targets = [("Returned copy", [v2_resp])]
    if letter.strip():
        targets.insert(0, ("Cover note", note_resps or [None]))
    for target, resps in targets:
        if any(r is None for r in resps):            # one unread piece of the note means the note was not screened
            out.append({"target": target, "ran": False, "flagged": None})
            continue
        ev = []
        for resp in resps:
            for e in resp["evidence"]:
                item = {"quote": squash(e["quote"]), "confidence": e["confidence"], "page": None, "rects": []}
                if target == "Returned copy":
                    found = _find(v2, e["quote"], _page_of(resp.get("pdf"), e["start"]))
                    if found:
                        item.update(page=found[0]["page"], rects=found[0]["rects"])
                ev.append(item)
        top = max(resps, key=lambda r: r["injection"])
        flagged = any(r["injection"] >= r["threshold"] or (r["verdict"] or "benign").strip().casefold() != "benign" for r in resps)
        out.append({"target": target, "ran": True, "injection": top["injection"], "verdict": top["verdict"],
                    "flagged": flagged, "category": top["category"], "severity": top["severity"], "evidence": ev})
    return out


def _note(letter: str, changes: list[dict], v2: TextDoc, guard: list[dict], run, ignore, fail, workers: int) -> dict:
    """Verify the cover note: every statement, thirty to a request, against v2 and the list of changes."""
    parts = split_note(letter)
    note = {"text": letter, "claims": [], "grounded": None, "verdict": None, "ran": False,
            "statements": 0, "checked": 0, "instructions": [], "skipped": parts["skipped"]}
    texts, sent, kinds = list(parts["claims"]), list(parts["sent"]), ["statement"] * len(parts["claims"])
    guard_flagged = any(g["target"] == "Cover note" and g.get("flagged") for g in guard)
    for ins in parts["instructions"]:
        if guard_flagged:                         # Guard has it: an instruction to an AI is not a statement to verify
            note["instructions"].append({"text": ins["text"], "why": ins["why"], "left_to": "Guard"})
        else:                                     # Guard did not flag the note, so the sentence is read as a statement
            texts.append(ins["text"])
            sent.append(ins["claim"])
            kinds.append("instruction")
    note["statements"] = len(texts)
    if not texts:
        return note
    ledger, spans = build_ledger(changes, v2)
    items = []
    for i, text in enumerate(texts):
        start = letter.find(text)
        item = {"text": text, "start": start if start >= 0 else None, "end": start + len(text) if start >= 0 else None,
                "verdict": "not_checked", "p": {k: None for k in ("supported", "contradicted", "not_stated", "not_a_claim")},
                "evidence": None, "change_ids": []}
        if sent[i] != text:
            item["sent_as"] = sent[i]
        if kinds[i] != "statement":
            item["kind"] = kinds[i]
        items.append(item)
    note["claims"] = items
    if "verify" in ignore:
        note["ignored"] = True                    # ablation: the note is taken at its word
        return note
    if len(v2.full_text) + len(ledger) > SOURCE_CHARS:
        fail("Verify", "verify cover note", "Verify (cover note): the returned copy and the list of changes together are "
             f"longer than Verify reads in one request ({len(v2.full_text) + len(ledger):,} characters). They were not "
             "cut, so the cover note was not checked.", "too_long")
        return note
    groups, too_long = batches(sent)
    for c in too_long:
        fail("Verify", "verify cover note", f"Verify (cover note): one statement is longer than Verify reads in one "
             f"request ({len(c):,} characters). It was not sent, and it was not cut.", "too_long")
    index, at = [], 0                             # positions of each batch's claims in `texts`
    long_set = set(too_long)
    order = [i for i, c in enumerate(sent) if c not in long_set]
    for g in groups:
        index.append(order[at:at + len(g)])
        at += len(g)

    def ask(n: int):
        g = groups[n]
        return run("grounding", {"documents": [{"id": "v2", "text": v2.full_text}, {"id": "changes", "text": ledger}],
                                 "answer": " ".join(g), "claims": g,
                                 "instructions": P.VERIFY_INSTRUCTIONS, "threshold": P.VERIFY_THRESHOLD,
                                 "evidence": "all"},
                   "verify cover note" + (f" ({n + 1} of {len(groups)})" if len(groups) > 1 else ""), "cover note",
                   lambda r: (check_verify(r, g), r))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        answers = list(pool.map(ask, range(len(groups))))
    unreadable = 0
    for n, ans in enumerate(answers):
        if ans is None:
            continue
        judged, raw = ans
        note["ran"] = True
        if len(groups) == 1:
            g_, v_ = raw.get("grounded"), raw.get("verdict")
            note["grounded"] = g_ if _is_num(g_) else None
            note["verdict"] = v_ if isinstance(v_, str) else None
        for i, j in zip(index[n], judged):
            unreadable += bool(j["evidence_unreadable"])
            _claim(items[i], i, j, ledger, spans, v2, changes)
            note["checked"] += 1
    if len(groups) > 1 and note["ran"]:
        note["verdict"] = "contradicted" if any(c["verdict"] == "contradicted" for c in items) else None
    if unreadable:
        note["unreadable_evidence"] = [f"Verify returned evidence for {_n(unreadable, 'statement')} of the cover note in a form "
                                       "that could not be read. The verdicts stand; the quotes are not shown."]
    return note


def _clause_of(doc: TextDoc, quote: str):
    """The clause of `doc` that holds this quote (longest shared run of text)."""
    q = squash(quote)
    if not q or len(q) > LOCATE_MAX:
        return None
    best, best_len = None, 0
    for c in doc.clauses:
        body = f"{c.number}. {c.title} {c.text}"
        if q in body:
            return c
        m = SequenceMatcher(None, body, q, autojunk=False).find_longest_match(0, len(body), 0, len(q))
        if m.size > best_len:
            best, best_len = c, m.size
    return best if best_len >= min(40, 0.6 * len(q)) else None


def _claim(item: dict, i: int, j: dict, ledger: str, spans, v2: TextDoc, changes: list[dict]) -> None:
    """Fill one statement of the note with Verify's judgment and tie it to the change its evidence points at."""
    item["verdict"] = j["verdict"]
    item["p"] = {k: j.get(k) for k in ("supported", "contradicted", "not_stated", "not_a_claim")}
    ev = j["evidence"]
    if ev and ev.get("quote"):
        view = {"document": ev["document"], "quote": squash(ev["quote"]), "confidence": ev["confidence"],
                "page": None, "rects": [], "aligned": None}
        start, end = ev["start"], ev["end"]
        placed = start is not None and end is not None
        if ev["document"] == "changes":
            view["aligned"] = ledger[start:end] == ev["quote"] if placed else None
            for a, b, cid in spans:
                if placed and a <= start < b and cid and cid not in item["change_ids"]:
                    item["change_ids"].append(cid)
        elif ev["document"] == "v2":
            view["aligned"] = v2.full_text[start:end] == ev["quote"] if placed else None
            found = _find(v2, ev["quote"])
            if found:
                view.update(page=found[0]["page"], rects=found[0]["rects"])
            cl = _clause_of(v2, ev["quote"])
            if cl is not None:
                item["change_ids"] += [ch["id"] for ch in changes
                                       if ch["blocking"] and (ch.get("uid") or {}).get("v2") == cl.uid and ch["id"] not in item["change_ids"]]
        item["evidence"] = view
    for cid in item["change_ids"]:
        ch = next((x for x in changes if x["id"] == cid), None)
        if ch is not None and i not in ch["claims"]:
            ch["claims"].append(i)


def _passages(playbook: dict) -> list[dict]:
    return [{"id": p["id"], "title": p["title"], "text": p["text"], "metadata": {"topic": t["title"]}}
            for t in playbook["topics"] for p in t["passages"]]


def _query(c: dict) -> str:
    part = c["parts"][0]
    kind = part.get("kind")
    name = "the page header or footer" if c["title"] == HEADER_LABEL else _cl(c["clause"], c["title"])
    if kind == "added":
        q = (f"The customer added a clause that is not in the agreement we sent: {name}. "
             f"It says: \"{squash(part['v2']['quote'])}\". How should the deal desk respond?")
    elif kind == "removed":
        q = (f"The customer removed {name} of the agreement we sent. "
             f"It said: \"{squash(part['v1']['quote'])}\". How should the deal desk respond?")
    else:
        q = (f"The customer changed clause {c['clause']} ({c['title']}): {c['summary']}. We sent: \"{squash(part['v1']['quote'])}\". "
             f"They returned: \"{squash(part['v2']['quote'])}\". How should the deal desk respond?")
    if len(q) > RERANK_QUERY_CHARS:               # the query is a question, not evidence: name the clause instead of quoting it
        verb = {"added": "added", "removed": "removed"}.get(kind, "changed")
        q = f"The customer {verb} {name}: {c['summary']}. How should the deal desk respond?"
    return q


def _rerank(c: dict, playbook: dict, fee: str | None, run) -> dict:
    passages_ = _passages(playbook)
    by_id = {p["id"]: p for p in passages_}
    topic_of = {p["id"]: t for t in playbook["topics"] for p in t["passages"]}
    query = _query(c)
    if not passages_:
        return {"status": "none", "passages": [], "skipped": [], "expanded": False, "query": query, "read": 0}
    context = {"our_role": "Provider (we sent the original agreement)", "annual_fee_in_our_version": fee or "unknown"}
    rows = run("rerank", {"query": query, "context": context, "documents": passages_,
                          "instructions": P.RERANK_INSTRUCTIONS}, f"rerank {c['clause']}", f"clause {c['clause']}",
               lambda r: check_rerank(r, len(passages_)))
    if rows is None:
        return {"status": "error", "passages": [], "skipped": [], "expanded": False, "query": query}
    used, skipped, expand_topics = [], [], []
    for row in rows:
        p = by_id.get(row["id"]) or (passages_[row["index"]] if row["index"] is not None else None)
        if not p:
            continue
        view = {"id": p["id"], "title": p["title"], "text": p["text"], "action": row["action"],
                "reason": row["reason"], "relevance": row["relevance_score"], "document_score": row["document_score"]}
        if row["action"] == "use" and (row["relevance_score"] or 0) >= P.RERANK_USE_FLOOR:
            used.append(view)
        elif row["action"] == "expand":
            expand_topics.append(topic_of[p["id"]]["id"])
        elif row["reason"] == "different_case":
            skipped.append(view)
    expanded = False
    if not used and expand_topics:                         # the right topic, the answer is in a neighbouring passage
        expanded = True
        tid = expand_topics[0]
        siblings = [p for p in passages_ if topic_of[p["id"]]["id"] == tid]
        rows2 = run("rerank", {"query": query, "context": context, "documents": siblings,
                               "instructions": P.RERANK_INSTRUCTIONS}, f"rerank {c['clause']} expand", f"clause {c['clause']}, neighbouring passages",
                    lambda r: check_rerank(r, len(siblings)))
        for row in rows2 or []:
            p = by_id.get(row["id"]) or (siblings[row["index"]] if row["index"] is not None else None)
            if p and row["action"] == "use" and (row["relevance_score"] or 0) >= P.RERANK_USE_FLOOR:
                used.append({"id": p["id"], "title": p["title"], "text": p["text"], "action": "use", "reason": None,
                             "relevance": row["relevance_score"], "document_score": row["document_score"]})
    return {"status": "guidance" if used else "none", "passages": used[:3], "skipped": skipped[:3],
            "expanded": expanded, "query": query, "read": len(passages_)}


def _route_from_answers(may_accept: float | None, may_counter: float | None) -> tuple[str | None, float]:
    """Turn two yes/no readings of the playbook into a route and how sure it is.
    A probability between 1 - floor and floor is not an answer; the route is then None."""
    hi, lo = P.ROUTE_CONFIDENCE_FLOOR, 1 - P.ROUTE_CONFIDENCE_FLOOR
    if may_accept is None or may_counter is None:
        return None, 0.0
    if may_accept >= hi:
        return "accept", may_accept
    if may_accept <= lo and may_counter >= hi:
        return "counter", min(1 - may_accept, may_counter)
    if may_accept <= lo and may_counter <= lo:
        return "escalate", min(1 - may_accept, 1 - may_counter)
    return None, max(min(may_accept, 1 - may_accept), min(may_counter, 1 - may_counter))


def _decide(c: dict, run, deal: dict | None = None) -> dict | None:
    part = c["parts"][0]
    kind = part.get("kind")
    guidance = (c["playbook"] or {}).get("passages") or []
    cmpv = c.get("compare") or {}
    if kind == "added":
        change, we_sent, they = ("The Customer added this clause. It is not in the agreement we sent.",
                                 "Nothing: the clause is new.", squash(part["v2"]["quote"]))
    elif kind == "removed":
        change, we_sent, they = ("The Customer removed this clause from the agreement we sent.",
                                 squash(part["v1"]["quote"]), "Nothing: the clause was removed.")
    else:
        change = "The Customer changed this term: " + c["summary"].replace(" → ", " became ")
        we_sent = cmpv.get("v1_quote") or squash(part["v1"]["quote"])
        they = cmpv.get("v2_quote") or squash(part["v2"]["quote"])
    state = {
        "clause": f"{c['clause']} {c['title']}",
        "change": change,
        "we_sent": we_sent,
        "they_returned": they,
        "deal_facts_from_our_version": deal or {},
        "playbook": [f"{p['title']}: {p['text']}" for p in guidance] or "The playbook has no passage that covers this change.",
    }
    a = run("decide", {"state": state, "instructions": P.DECIDE_INSTRUCTIONS, "questions": P.DECIDE_QUESTIONS},
            f"decide {c['clause']}", f"clause {c['clause']}", check_decide)
    if a is None:
        return None
    may_accept, may_counter = a["desk_may_accept"], a["desk_may_counter"]
    route, confidence = _route_from_answers(may_accept, may_counter)
    return {"route": route, "route_confidence": confidence, "desk_may_accept": may_accept, "desk_may_counter": may_counter,
            "favors": a["favors"], "favors_confidence": a["favors_confidence"],
            "materiality": a["materiality"], "materiality_label": a["materiality_label"]}


def _final_route(c: dict) -> None:
    """Code decides the route. Decide's answer is followed only when the playbook has guidance and Decide is
    sure; in every other case the change goes to a person."""
    pb, d = c["playbook"] or {}, c["decide"]
    if pb.get("status") == "error" or d is None or not pb:
        c["route"], c["route_reason"] = "escalate", "A routing step failed, so a person decides."
    elif pb.get("status") == "none":
        c["route"], c["route_reason"] = "escalate", "The playbook has no guidance for this change, so it goes to Legal."
    elif not d.get("route"):
        c["route"], c["route_reason"] = "escalate", "Decide was not sure how the playbook applies, so a person decides."
    else:
        c["route"] = d["route"]
        c["route_reason"] = {"accept": "The playbook lets the deal desk accept this as written.",
                             "counter": "The playbook does not allow this as written, but gives the deal desk a term to send back.",
                             "escalate": "The playbook reserves this for an approver outside the deal desk."}.get(d["route"], "")


def _status(changes, note, guard, v1: TextDoc, v2: TextDoc, errors, *, rules_ran: bool = True,
            gate: dict | None = None) -> tuple[str, str, list[dict]]:
    reasons = []
    for g in guard:
        if g.get("flagged"):
            reasons.append({"level": "block", "source": "Guard", "text": f"{g['target']}: text addressed to an AI reviewer "
                            f"({g.get('category') or 'injection'}, probability {g.get('injection') or 0:.0%})."})
    if v2.hidden:
        pages = sorted({h.page for h in v2.hidden})
        kinds = list(dict.fromkeys(HIDDEN_WORDS.get(h.reason, "hidden text") for h in v2.hidden))
        reasons.append({"level": "block", "source": "Rules", "text": "The returned copy contains hidden text on "
                        f"{_pages(pages).lower()}: {', '.join(kinds)}."})
    bad = [c for c in note["claims"] if c["verdict"] == "contradicted"]
    if bad:
        reasons.append({"level": "block", "source": "Verify", "text": f"{_n(len(bad), 'statement')} in the cover note "
                        f"{'is' if len(bad) == 1 else 'are'} contradicted by the documents."})
    open_changes = [c for c in changes if c["blocking"]]
    if open_changes:
        esc = sum(c["route"] == "escalate" for c in open_changes)
        who = [n for n in ("Rules", "Verify", "Compare") if any(n in c["found_by_label"].split(" + ") for c in open_changes)]
        reasons.append({"level": "open", "source": " + ".join(who) or "Rules", "text": f"{_n(len(open_changes), 'substantive change')} "
                        "to resolve" + (f", {esc} of them for another approver." if 0 < esc < len(open_changes) else
                                        ", all for another approver." if esc > 1 else ", for another approver." if esc else ".")})
    unread = False
    for doc, name in ((v1, "the version we sent"), (v2, "the returned copy")):
        if doc.pages_without_text:
            unread = True
            many = len(doc.pages_without_text) > 1
            reasons.append({"level": "incomplete", "source": "Rules", "text":
                            f"{_pages(doc.pages_without_text)} of {name} {'have' if many else 'has'} no text layer (an image "
                            f"only), so {'they were' if many else 'it was'} not read. The review does not cover "
                            f"{'those pages' if many else 'that page'}."})
    for e in errors:
        text = e["message"] if e.get("kind") in ("bad_response", "too_long") else f"{e['step']}: {e['message']}"
        reasons.append({"level": "incomplete", "source": e["endpoint"], "text": text})
    if any(r["level"] == "block" for r in reasons):
        return "blocked", "Do not sign", reasons
    if errors or unread or not rules_ran:
        return "incomplete", "Review incomplete", reasons
    if open_changes:
        return "changes", "Changes to resolve before signing", reasons
    # what "ready" rests on, said from the checks that actually ran
    held = ["No substantive change"]
    if note["claims"] and note["ran"]:
        held.append(f"no statement of the cover note is contradicted ({note['checked']} checked)")
    elif note["claims"]:
        held.append("the cover note was not checked (Verify left out)")
    if any(g.get("ran") for g in guard):
        held.append("nothing addressed to an AI")
    if gate is not None:
        held.append("the two files read as versions of one agreement")
    return "ready", "Ready to sign", reasons + [{"level": "ok", "source": "All checks", "text": ", ".join(held) + "."}]


def _decided(changes, note, guard, reasons, wrong_pair: bool, deal: dict) -> dict:
    """Per endpoint: the outcomes of this review that depended on its answer, and which ones."""
    opened = [c for c in changes if c["blocking"]]
    by = lambda who, what: [c["id"] for c in changes if (c.get("decided_by") or {}).get(who) == what]
    bad = [i for i, c in enumerate(note["claims"]) if c["verdict"] == "contradicted"]
    blockers = {r["source"] for r in reasons if r["level"] == "block"}
    routed = [c for c in opened if c.get("playbook")]
    with_g = [c["id"] for c in routed if c["playbook"].get("status") == "guidance"]
    without = [c["id"] for c in routed if c["playbook"].get("status") == "none"]
    followed = [c["id"] for c in routed if c.get("decide") and c["decide"].get("route") and c["route"] == c["decide"]["route"]
                and c["playbook"].get("status") == "guidance"]
    not_followed = [c["id"] for c in routed if c["id"] not in followed]
    verify = {"held_alone": by("verify", "held_alone"), "cleared": by("verify", "cleared"), "note_statements_contradicted": bad,
              "only_blocker": blockers == {"Verify"}}
    compare = {"held_alone": by("compare", "held_alone"), "vetoed_clearing": by("compare", "vetoed") + by("compare", "withheld"),
               "consented_to_clearing": by("compare", "consented"), "gate_stopped_the_review": wrong_pair}
    guard_only = bool(blockers) and blockers == {"Guard"}
    out = {
        "Verify": {"count": len(verify["held_alone"]) + len(verify["cleared"]) + len(bad), "detail": verify},
        "Compare": {"count": len(compare["held_alone"]) + len(compare["vetoed_clearing"]) + len(compare["consented_to_clearing"]) + int(wrong_pair),
                    "detail": compare},
        "Rerank": {"count": len(with_g) + len(without), "detail": {"with_guidance": with_g, "without_guidance": without,
                                                                    "expanded": [c["id"] for c in routed if c["playbook"].get("expanded")]}},
        "Decide": {"count": len(followed), "detail": {"routes_followed": followed, "routes_not_followed": not_followed}},
        "Extract": {"count": len(deal) if routed else 0, "detail": {"deal_facts_given_to_routing": sorted(deal) if routed else []}},
        "Guard": {"count": int(guard_only), "detail": {"flagged": [g["target"] for g in guard if g.get("flagged")], "only_blocker": guard_only}},
    }
    return out


def _ablation(changes, note, status, decided: dict, wrong_pair: bool) -> list[dict]:
    """What this review would have said without each endpoint, from what its answers decided here."""
    opened = [c for c in changes if c["blocking"]]
    v, c_ = decided["Verify"]["detail"], decided["Compare"]["detail"]
    confirmed_c = [c for c in opened if (c["compare"] or {}).get("relation") == "conflict"]
    no_guidance = decided["Rerank"]["detail"]["without_guidance"]
    skipped = sum(len((c["playbook"] or {}).get("skipped") or []) for c in opened)
    verify_text = []
    if v["held_alone"]:
        verify_text.append(f"{_n(len(v['held_alone']), 'change')} of meaning that only Verify read would pass as "
                           f"{'a spelling fix' if len(v['held_alone']) == 1 else 'spelling fixes'}.")
    if v["cleared"]:
        verify_text.append(f"{_n(len(v['cleared']), 'faithful rewording')} would be sent to negotiation.")
    bad = v["note_statements_contradicted"]
    if note["ran"] and bad:
        verify_text.append(f"The cover note would be taken at its word: {_n(len(bad), 'false statement')} would not be caught"
                           + (f", and this review would read “{'changes to resolve' if opened else 'ready to sign'}” instead of "
                              "“do not sign”." if status == "blocked" and v["only_blocker"] else "."))
    elif note["ran"]:
        verify_text.append(f"The {_n(note['checked'], 'statement')} of the cover note would go unchecked; here none is contradicted.")
    compare_text = []
    if wrong_pair:
        compare_text.append("Nothing would have stopped this review: the two files are not versions of one agreement, and "
                            "every clause would have been listed as removed or added.")
    if c_["held_alone"]:
        compare_text.append(f"{_n(len(c_['held_alone']), 'change')} of meaning that only Compare read would pass as "
                            f"{'a spelling fix' if len(c_['held_alone']) == 1 else 'spelling fixes'}.")
    if c_["vetoed_clearing"]:
        compare_text.append(f"{_n(len(c_['vetoed_clearing']), 'change')} that Verify read as the same meaning would have been cleared.")
    if c_["consented_to_clearing"]:
        compare_text.append(f"{_n(len(c_['consented_to_clearing']), 'one-word swap')} could not have been cleared: that needs Compare's explicit agreement.")
    if confirmed_c and not wrong_pair:
        compare_text.append(f"{len(confirmed_c)} of {_n(len(opened), 'open change')} would rest on one reading of the clause instead of two.")
    if not wrong_pair:
        compare_text.append("Nothing would check that the two files are versions of one agreement.")
    g = decided["Guard"]["detail"]
    guard_rows = [] if not g["flagged"] else [{
        "endpoint": "Guard", "changes": [], "text":
        f"{_names(g['flagged'])}: the text addressed to an AI reviewer would not be flagged. "
        + ("Nothing else blocks this review, so it would not read “do not sign”." if g["only_blocker"] else
           "Another check blocks this review as well, so the status would be the same.")}]
    return guard_rows + [
        {"endpoint": "Verify", "text": " ".join(verify_text) or "Not run.",
         "changes": v["held_alone"] + v["cleared"], "claims": bad},
        {"endpoint": "Compare", "text": " ".join(compare_text),
         "changes": c_["held_alone"] + c_["vetoed_clearing"] + c_["consented_to_clearing"]},
        {"endpoint": "Rerank", "text": "No change is open in this review, so there was nothing to look up." if not opened else
         f"{_n(len(opened), 'change')} would reach the reviewer with no playbook answer attached"
         + (f"; {_n(skipped, 'passage')} written for a different deal size would be {'a candidate' if skipped == 1 else 'candidates'} to quote" if skipped else "")
         + (f"; {_n(len(no_guidance), 'change')} with no guidance would not be sent to Legal automatically." if no_guidance else "."),
         "changes": no_guidance},
    ]


def _endpoint_summary(gw: Gateway, changes, decided: dict) -> list[dict]:
    expanded = decided["Rerank"]["detail"]["expanded"]
    effects = {
        "Verify": "Reads meaning, in two places. Across versions: each sentence the rules class as a change or as a spelling "
                  "fix is read against the other version's PDF, in both directions. A contradiction confirms a change or "
                  "promotes a spelling fix; support in both directions clears a faithful rewording, unless Compare objects. "
                  "On the cover note: each statement is checked against the returned copy and the list of changes, and a "
                  "contradicted one blocks signing. Formatting and administrative differences are not sent.",
        "Compare": "Two jobs. As a gate, on the two whole PDFs: are they versions of one agreement? If not, the review "
                   "stops. Clause by clause, for clauses whose meaning is in question: a conflict confirms a change, "
                   "promotes a spelling fix and vetoes clearing a rewording; a one-word swap is cleared only if Compare "
                   "reads the passage as the same. The marked copies it returns are an optional layer on the pages.",
        "Rerank": "Picks the playbook passages for each open change and skips those written for other deal sizes; none "
                  "found means Legal." + (f" In this review it was asked a second time, for the neighbouring passages of a "
                                          f"topic, for {_n(len(expanded), 'change')}." if expanded else ""),
        "Decide": "Answers two yes/no questions about each open change from the passages Rerank kept; code turns them "
                  "into a route, and an unsure answer means a person decides.",
        "Extract": "Reads the key terms of both versions for the side-by-side table. The annual fee and the initial term "
                   "of our version are given to Rerank and Decide as deal facts.",
        "Guard": "Flags text addressed to an AI reviewer, in the cover note and in the returned PDF; a hit blocks signing.",
    }
    total = sum(int(a["credits"] or 0) for a in gw.audit)
    out = []
    for name in ("Verify", "Compare", "Rerank", "Decide", "Extract", "Guard"):
        rows = [a for a in gw.audit if a["endpoint"] == name]
        credits = sum(int(a["credits"] or 0) for a in rows)
        out.append({"name": name, "path": rows[0]["path"] if rows else None, "calls": len(rows),
                    "credits": credits, "credits_share": round(credits / total, 4) if total else 0.0,
                    "model_ms": sum(int(float(a["model_ms"] or 0)) for a in rows), "effect": effects[name],
                    "decided": decided[name]["count"], "decided_detail": decided[name]["detail"]})
    return out


def _assets(out: Path, v1: TextDoc, v2: TextDoc, res: dict, gate: dict | None, crop=None, warnings: list | None = None) -> dict:
    """Write the PDFs and page images. `pages` are the clean source pages (the UI draws the changes on them
    from exact quote positions). `layers` are the copies Clocked marked, rendered as returned: optional, so a
    layer that cannot be written is noted and left out."""
    docs = {}
    for key, doc in (("v1", v1), ("v2", v2)):
        src = out / f"{key}.pdf"
        src.write_bytes(Path(doc.path).read_bytes())
        entry = {"name": "The version we sent" if key == "v1" else "The copy they returned", "pages_count": doc.pages,
                 "source_pdf": src.name, "marked_pdf": None, "marked_by": None, "extra_pdfs": [],
                 "pages": render_pages(src, out, key, crop=crop), "layers": [],
                 "pages_without_text": list(doc.pages_without_text), "ocr_pages": list(doc.ocr_pages)}
        marked = ((gate or {}).get("marked") or {}).get(key) or {}
        if marked.get("document"):
            mp = out / f"{key}-compare-highlight.pdf"
            try:
                mp.write_bytes(base64.b64decode(marked["document"], validate=True))
                pages = render_pages(mp, out, f"{key}-compare", crop=crop)
                entry.update(marked_pdf=mp.name, marked_by="Clocked Compare: every passage it paired with the other version",
                             marked_spans=marked.get("marked"), spans=marked.get("spans"))
                entry["layers"].append({"id": "compare", "label": "Passages paired by Compare",
                                        "by": "Clocked Compare, return_pdf=highlight", "pdf": mp.name,
                                        "spans": marked.get("marked"), "pages": pages})
            except Exception:
                mp.unlink(missing_ok=True)
                entry["marked_error"] = "The marked copy Compare returned could not be opened, so that layer is left out."
                if warnings is not None:
                    warnings.append({"source": "Compare", "text": f"{entry['name']}: {entry['marked_error']}"})
        elif marked.get("error"):
            entry["marked_error"] = str(marked["error"])
        for lid, label, rkey in (("terms", "Key terms boxed by Extract", f"terms_{key}"),
                                 ("guard", "Guard evidence boxed", "guard_v2" if key == "v2" else "")):
            m = (((res.get(rkey) or {}).get("pdf") or {}) if rkey else {})
            m = m.get("marked") if isinstance(m, dict) else None
            if isinstance(m, dict) and m.get("document") and m.get("marked"):
                name = f"{key}-{lid}-box.pdf"
                try:
                    (out / name).write_bytes(base64.b64decode(m["document"], validate=True))
                    pages = render_pages(out / name, out, f"{key}-{lid}", crop=crop)
                    entry["extra_pdfs"].append({"label": label, "file": name})
                    entry["layers"].append({"id": lid, "label": label, "by": f"Clocked {'Extract' if lid == 'terms' else 'Guard'}, return_pdf=box",
                                            "pdf": name, "spans": m.get("marked"), "pages": pages})
                except Exception:
                    (out / name).unlink(missing_ok=True)
                    if warnings is not None:
                        warnings.append({"source": "Extract" if lid == "terms" else "Guard",
                                         "text": f"{entry['name']}: the boxed copy that came back could not be opened, so that layer is left out."})
        docs[key] = entry
    return docs
