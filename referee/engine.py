"""The review: two PDFs and a cover note in, one decision with evidence out.

Order of work
  1. Rule layer (no model): align clauses, diff, classify, find hidden text.
  2. Clocked, in parallel: Compare (v1 vs v2, marked PDFs), Guard (note, v2), Extract (key terms of each).
     and Verify across versions: every changed sentence of one version against the other PDF.
  3. Three readings side by side (rules, Verify, Compare); code decides. A "spelling fix" that
     Verify or Compare reads as a different meaning becomes a change; a rewording that Verify
     reads as the same meaning in both directions, with no objection from Compare, stops being one.
  4. Verify the cover note, claim by claim, against v2 and the list of changes.
  5. For every open change: Rerank the playbook, then Decide the route using what Rerank kept.
  6. Code, not a model, sets the final status and can only tighten it.
"""
from __future__ import annotations

import base64
import bisect
import json
import os
import re

import pymupdf
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
from pathlib import Path

from . import prompts as P
from .gateway import PRODUCT_NAME, Gateway, StepError, pdf_b64
from .rules import Hunk, align, compare_documents, normalise
from .textdoc import TextDoc, content_box, locate, read_pdf, remap_rects, render_pages, squash

ROOT = Path(__file__).resolve().parents[1]
SENTENCE = re.compile(r"(?<=[.!?])\s+")


def _nz(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


# ---------------------------------------------------------------- cover note
def split_claims(letter: str) -> list[str]:
    """Sentences of the note that assert something. Greetings, sign-offs and questions are left out."""
    claims: list[str] = []
    for para in re.split(r"\n\s*\n", letter.strip()):
        para = squash(para)
        if not para or re.match(r"^(hi|hello|dear|thanks|thank you|best|regards|sincerely)\b", para, re.I) and len(para) < 60:
            continue
        for s in SENTENCE.split(para):
            s = s.strip()
            if len(s) >= 12 and s.endswith(".") and not re.match(r"^(thanks|best|regards)\b", s, re.I):
                claims.append(s)
    return claims[:30]


# ------------------------------------------------------------------ Compare
def _pdf_objects(resp: dict, ids: list[str]) -> dict[str, dict]:
    raw = resp.get("pdf") or []
    if isinstance(raw, dict):
        raw = [raw]
    out: dict[str, dict] = {}
    for i, obj in enumerate(raw):
        name = next((obj[k] for k in ("document", "id", "name") if isinstance(obj.get(k), str)), None)
        out[name if name in ids else ids[i] if i < len(ids) else str(i)] = obj
    return out


def _page_of(pdf_obj: dict | None, start) -> int | None:
    try:
        offs, sel = pdf_obj["page_offsets"], pdf_obj["selected"]
        return sel[bisect.bisect_right(offs, start) - 1]
    except Exception:
        return None


def _aligned(pdf_obj: dict | None, passage: dict) -> bool | None:
    """True when text[start:end] == quote, counted in code points. None when the text was not returned."""
    text = (pdf_obj or {}).get("text")
    if not isinstance(text, str) or passage.get("start") is None:
        return None
    return text[passage["start"]:passage["end"]] == passage.get("quote")


def _clause_of(doc: TextDoc, quote: str) -> str | None:
    q = squash(quote)
    if not q:
        return None
    best, best_len = None, 0
    for c in doc.clauses:
        body = f"{c.number}. {c.title} {c.text}"
        if q in body:
            return c.number
        m = SequenceMatcher(None, body, q, autojunk=False).find_longest_match(0, len(body), 0, len(q))
        if m.size > best_len:
            best, best_len = c.number, m.size
    return best if best_len >= min(40, 0.6 * len(q)) else None


def parse_compare(resp: dict, v1: TextDoc, v2: TextDoc) -> dict:
    pdfs = _pdf_objects(resp, ["v1", "v2"])
    findings = []
    for pair in resp.get("pairs", []):
        for rel in ("conflicts", "duplicates"):
            for f in pair.get(rel) or []:
                sides = {(f.get(s) or {}).get("document"): f.get(s) or {} for s in ("a", "b")}
                a, b = sides.get("v1") or f.get("a") or {}, sides.get("v2") or f.get("b") or {}
                findings.append({
                    "relation": "conflict" if rel == "conflicts" else "duplicate",
                    "kind": f.get("kind"), "confidence": f.get("confidence"),
                    "v1": {"quote": a.get("quote", ""), "start": a.get("start"), "end": a.get("end"),
                           "page": _page_of(pdfs.get("v1"), a.get("start")), "aligned": _aligned(pdfs.get("v1"), a),
                           "clause": _clause_of(v1, a.get("quote", ""))},
                    "v2": {"quote": b.get("quote", ""), "start": b.get("start"), "end": b.get("end"),
                           "page": _page_of(pdfs.get("v2"), b.get("start")), "aligned": _aligned(pdfs.get("v2"), b),
                           "clause": _clause_of(v2, b.get("quote", ""))},
                })
    pair0 = (resp.get("pairs") or [{}])[0]
    marked = {k: (v.get("marked") or {}) for k, v in pdfs.items()}
    return {"verdict": resp.get("verdict"), "relation": pair0.get("relation"), "related": pair0.get("related"),
            "probabilities": pair0.get("probabilities"), "findings": findings, "marked": marked}


def parse_clause_compare(resp: dict, clause: str, v1_text: str, v2_text: str) -> dict:
    """Compare on one clause: our text against theirs. Offsets index the clause texts we sent."""
    pair = (resp.get("pairs") or [{}])[0]
    relation = pair.get("relation")
    findings = []
    for rel in ("conflicts", "duplicates"):
        for f in pair.get(rel) or []:
            sides = {(f.get(s) or {}).get("document"): f.get(s) or {} for s in ("a", "b")}
            a, b = sides.get("v1") or f.get("a") or {}, sides.get("v2") or f.get("b") or {}

            def side(x: dict, text: str) -> dict:
                ok = None if x.get("start") is None else text[x["start"]:x["end"]] == x.get("quote")
                return {"quote": x.get("quote", ""), "start": x.get("start"), "end": x.get("end"), "aligned": ok,
                        "clause": clause, "page": None}
            findings.append({"relation": "conflict" if rel == "conflicts" else "duplicate", "kind": f.get("kind"),
                             "confidence": f.get("confidence"), "v1": side(a, v1_text), "v2": side(b, v2_text)})
    return {"clause": clause, "relation": relation, "probability": (pair.get("probabilities") or {}).get(relation),
            "related": pair.get("related"), "findings": findings}


def clauses_to_compare(hunks: list[Hunk], v1: TextDoc, v2: TextDoc) -> dict[str, tuple[str, str]]:
    """Clause number -> (our text, their text) for every clause whose meaning is in question.
    Formatting and notice addresses are settled by the rules; added or removed clauses have no pair."""
    pairs = {(cb.number if cb else ca.number): (ca, cb) for ca, cb in align(v1, v2)}
    out: dict[str, tuple[str, str]] = {}
    for h in hunks:
        if h.cls in ("formatting", "administrative") or h.clause in out:
            continue
        ca, cb = pairs.get(h.clause, (None, None))
        if ca is not None and cb is not None and ca.text and cb.text:
            out[h.clause] = (ca.text, cb.text)
    return out


def _overlaps(f: dict, h: Hunk) -> bool:
    c2, c1 = _nz(h.v2_changed), _nz(h.v1_changed)
    return bool((c2 and c2 in _nz(f["v2"]["quote"])) or (c1 and c1 in _nz(f["v1"]["quote"])))


def _covers(f: dict, h: Hunk) -> bool:
    """Does this Compare finding sit on the passage that holds the hunk? Same clause, shared text."""
    if h.clause not in (f["v2"]["clause"], f["v1"]["clause"]):
        return False
    if _overlaps(f, h):
        return True
    for quote, sentence in ((f["v2"]["quote"], h.v2_sentence), (f["v1"]["quote"], h.v1_sentence)):
        a, b = _nz(quote), _nz(sentence)
        if a and b:
            m = SequenceMatcher(None, a, b, autojunk=False).find_longest_match(0, len(a), 0, len(b))
            if m.size >= min(40, max(9, int(0.6 * min(len(a), len(b))))):
                return True
    return False


def parse_xverify(resp: dict | None, claims: list[str]) -> dict | None:
    """Verify, one version's sentences against the other version: {sentence: judgment}."""
    if resp is None:
        return None
    out, listed = {}, resp.get("claims") or []
    by_text = {squash(c.get("claim", "")): c for c in listed}
    for i, text in enumerate(claims):
        c = by_text.get(squash(text)) or (listed[i] if i < len(listed) else None)
        if c:
            out[text] = {"verdict": c.get("verdict"), "supported": c.get("supported"),
                         "contradicted": c.get("contradicted"), "not_stated": c.get("not_stated")}
    return out


def xverify_claims(hunks: list[Hunk]) -> tuple[list[str], list[str]]:
    """Sentences worth a reading of their meaning. Formatting and notice addresses are settled by the rules."""
    s1, s2 = [], []
    for h in hunks:
        if h.cls in ("formatting", "administrative"):
            continue
        if h.v1_sentence and h.v1_sentence not in s1:
            s1.append(h.v1_sentence)
        if h.v2_sentence and h.v2_sentence not in s2:
            s2.append(h.v2_sentence)
    return s1[:30], s2[:30]


def cross_label(hunks: list[Hunk], ccmp: dict[str, dict] | None, xv: dict | None = None) -> list[dict]:
    """Put three readings side by side for every difference, then let code decide what it is.

    rules    exact: what text differs and whether numbers moved. Cannot read meaning.
    Compare  reads the clause as we sent it against the clause as returned: conflicting or same,
             with the sentences that conflict and the sentences that say the same.
    Verify   reads each changed sentence of one version against the other version's PDF,
             in both directions: supported or contradicted.

    A "spelling fix" becomes a change when Compare or Verify reads a different meaning.
    A rewording stops being a change only when Verify reads the same meaning in both
    directions and Compare does not read a conflict. Numbers that moved are never cleared.
    """
    xv = xv or {"v1": None, "v2": None}
    in_question = {}
    for h in hunks:
        if h.cls in ("substantive", "spelling"):
            in_question[h.clause] = in_question.get(h.clause, 0) + 1
    labelled = []
    for h in hunks:
        # -- Compare's reading of this clause
        cc = (ccmp or {}).get(h.clause)
        cview, strong = None, []
        if cc is not None:
            findings = cc["findings"]
            hit = sorted((f for f in findings if f["relation"] == "conflict" and _covers(f, h)),
                         key=lambda f: -(f["confidence"] or 0))
            strong = [f for f in hit if (f["confidence"] or 0) >= P.CONFLICT_CONFIDENCE_FLOOR]
            same = [f for f in findings if f["relation"] == "duplicate" and _covers(f, h)
                    and f["kind"] in ("restated", "verbatim") and (f["confidence"] or 0) >= P.RESTATED_CONFIDENCE_FLOOR]
            sure = (cc["probability"] or 0) >= P.RESTATED_CONFIDENCE_FLOOR
            whole_clause_conflict = cc["relation"] == "conflicting" and sure and in_question.get(h.clause) == 1
            if strong:
                cview = _cmp_view(strong[0], "conflict", cc)
            elif whole_clause_conflict:
                any_conflict = [f for f in findings if f["relation"] == "conflict"]
                cview = _cmp_view(any_conflict[0], "conflict", cc) if any_conflict else {
                    "relation": "conflict", "kind": None, "confidence": cc["probability"], "clause_relation": cc["relation"],
                    "clause_probability": cc["probability"], "v1_quote": None, "v2_quote": None, "aligned": True}
                strong = [True]
            elif same:
                cview = _cmp_view(same[0], "same", cc)
            elif cc["relation"] == "same" and sure:
                cview = {"relation": "same", "kind": None, "confidence": cc["probability"], "clause_relation": cc["relation"],
                         "clause_probability": cc["probability"], "v1_quote": None, "v2_quote": None, "aligned": True}
            else:
                cview = {"relation": "silent", "clause_relation": cc["relation"], "clause_probability": cc["probability"],
                         "note": "Compare read this clause but did not place this passage as a conflict or as the same."}
        # -- Verify's reading of this sentence
        r2 = (xv.get("v2") or {}).get(h.v2_sentence) if h.v2_sentence else None
        r1 = (xv.get("v1") or {}).get(h.v1_sentence) if h.v1_sentence else None
        changed = [r for r in (r2, r1) if r and r["verdict"] == "contradicted" and (r["contradicted"] or 0) >= P.MEANING_CHANGED_FLOOR]
        both_same = bool(r2 and r1) and all(r["verdict"] == "supported" and (r["supported"] or 0) >= P.MEANING_SAME_FLOOR for r in (r2, r1))
        if (xv.get("v1") is None and xv.get("v2") is None) or h.cls in ("formatting", "administrative"):
            vview = None
        else:
            vview = {"relation": "changed" if changed else "same" if both_same else "unclear",
                     "returned_against_sent": r2, "sent_against_returned": r1}
        # -- code decides
        cls, why, promoted = h.cls, h.why, False
        if h.cls == "substantive" and h.kind == "wording" and both_same and not strong:
            cls = "reworded"
            why = ("The words changed. Verify reads each version's sentence as supported by the other version, and "
                   "Compare does not read a conflict here, so this is not held as a change.")
        elif h.cls == "spelling":
            explained = any(o is not h and o.cls == "substantive" and o.clause == h.clause and o.v2_sentence == h.v2_sentence
                            for o in hunks)
            if (changed or strong) and not explained:
                cls, promoted = "substantive", True
                who = " and ".join(n for n, ok in (("Compare", strong), ("Verify", changed)) if ok)
                why = (f"The rule layer took this for a spelling fix. {who} read{'s' if ' and ' not in who else ''} the two "
                       "versions as saying different things, so it is held as a change.")
        confirms = [n for n, ok in (("Verify", changed), ("Compare", strong)) if ok]
        labelled.append({"hunk": h, "cls": cls, "why": why, "compare": cview, "verify": vview, "promoted": promoted,
                         "confirmed_by": confirms,
                         "found_by": "compare" if promoted else "both" if (cls == "substantive" and confirms) else "rules"})
    return labelled


def _cmp_view(f: dict, relation: str, cc: dict | None = None) -> dict:
    return {"relation": relation, "kind": f["kind"], "confidence": f["confidence"],
            "clause_relation": (cc or {}).get("relation"), "clause_probability": (cc or {}).get("probability"),
            "v1_quote": squash(f["v1"]["quote"]) or None, "v2_quote": squash(f["v2"]["quote"]) or None,
            "aligned": f["v1"]["aligned"] is not False and f["v2"]["aligned"] is not False}


# ------------------------------------------------------------------ changes
_ORDER = {"substantive": 0, "reworded": 1, "administrative": 2, "spelling": 3, "formatting": 4}


def build_changes(labelled: list[dict], v1: TextDoc, v2: TextDoc) -> list[dict]:
    groups: dict[tuple[str, str], list[dict]] = {}
    for it in labelled:
        groups.setdefault((it["hunk"].clause, it["cls"]), []).append(it)
    changes = []
    for (clause, cls), items in groups.items():
        parts = []
        for it in items:
            h: Hunk = it["hunk"]
            parts.append({
                "summary": h.summary, "kind": h.kind,
                "v1": _side(v1, h.v1_quote, h.v1_changed, h.v1_page),
                "v2": _side(v2, h.v2_quote, h.v2_changed, h.v2_page),
            })
        first = items[0]
        rank = {"conflict": 0, "silent": 1, "same": 2}
        cmp_views = sorted((it["compare"] for it in items if it["compare"]), key=lambda v: rank.get(v["relation"], 9))
        vrank = {"changed": 0, "unclear": 1, "same": 2}
        ver_views = sorted((it["verify"] for it in items if it["verify"]), key=lambda v: vrank.get(v["relation"], 9))
        found = {it["found_by"] for it in items}
        confirmed = sorted({n for it in items for n in it["confirmed_by"]}, reverse=True)
        promoted = any(it["promoted"] for it in items)
        readers = (["Rules"] if not promoted else []) + confirmed
        summaries = list(dict.fromkeys(p["summary"] for p in parts))
        if cmp_views and cmp_views[0].get("aligned") and cmp_views[0]["relation"] in ("conflict", "same"):
            cv = dict(cmp_views[0])
            for key, doc, page in (("v1", v1, first["hunk"].v1_page), ("v2", v2, first["hunk"].v2_page)):
                q = cv.get(f"{key}_quote")
                spot = locate(doc.path, q, page) if q and len(q) >= 12 else []
                cv[key] = {"quote": q, "page": spot[0]["page"] if spot else None, "rects": spot[0]["rects"] if spot else []}
            cmp_views[0] = cv
        changes.append({
            "clause": clause, "title": first["hunk"].title, "cls": cls, "blocking": cls == "substantive",
            "kind": first["hunk"].kind if len({i["hunk"].kind for i in items}) == 1 else "mixed",
            "summary": "; ".join(summaries[:3]) + (" …" if len(summaries) > 3 else ""),
            "why": first["why"],
            "found_by": "compare" if "compare" in found else "both" if "both" in found else "rules",
            "found_by_label": " + ".join(readers) if cls == "substantive" else "Rules" if cls != "reworded" else "Rules, cleared by Verify",
            "promoted": promoted,
            "parts": parts, "compare": cmp_views[0] if cmp_views else None, "verify": ver_views[0] if ver_views else None,
            "playbook": None, "decide": None, "route": None, "route_reason": None, "claims": [],
        })

    def key(c):
        nums = [int(x) for x in re.findall(r"\d+", c["clause"])] or [999]
        return (_ORDER.get(c["cls"], 9), nums)
    changes.sort(key=key)
    for i, c in enumerate(changes, 1):
        c["id"] = f"c{i}"
    return changes


def _side(doc: TextDoc, quote: str, changed: str, page: int) -> dict:
    # The quote carries a few words of context on each side, so it is the less ambiguous thing to look for.
    found = locate(doc.path, quote, page) if quote else []
    if not found and changed and len(squash(changed)) >= 12:
        found = locate(doc.path, changed, page)
    return {"quote": quote, "changed": changed, "page": found[0]["page"] if found else (page or None),
            "rects": found[0]["rects"] if found else [], "located": bool(found)}


# ------------------------------------------------------------------- ledger
def build_ledger(changes: list[dict], v2: TextDoc | None = None) -> tuple[str, list[tuple[int, int, str]]]:
    lines, spans, pos = [], [], 0

    def add(text: str, cid: str | None):
        nonlocal pos
        lines.append(text)
        spans.append((pos, pos + len(text), cid or ""))
        pos += len(text) + 1

    open_changes = [c for c in changes if c["blocking"]]
    if open_changes:
        add(f"The returned copy changes {len(open_changes)} commercial or legal term(s) of the agreement we sent: "
            + "; ".join(f"clause {c['clause']} ({c['title']})" for c in open_changes) + ".", None)
    else:
        add("The returned copy does not change any commercial or legal term of the agreement we sent. The only "
            "differences are the ones listed below.", None)
    for c in changes:
        for p in c["parts"]:
            a, b = squash(p["v1"]["quote"]), squash(p["v2"]["quote"])
            if c["cls"] == "substantive":
                add(f"Substantive change in clause {c['clause']} ({c['title']}): {p['summary']}. We sent: \"{a}\". "
                    f"The returned copy says: \"{b}\".", c["id"])
            elif c["cls"] == "reworded":
                add(f"Clause {c['clause']} ({c['title']}) was reworded without changing its meaning. We sent: \"{a}\". "
                    f"The returned copy says: \"{b}\".", c["id"])
            elif c["cls"] == "administrative":
                add(f"Administrative change in clause {c['clause']} ({c['title']}): the notice address was updated. "
                    f"We sent: \"{a}\". The returned copy says: \"{b}\".", c["id"])
            elif c["cls"] == "spelling":
                add(f"Spelling corrected in clause {c['clause']} ({c['title']}): {p['summary']}. No change of meaning.", c["id"])
            else:
                add(f"Formatting only in clause {c['clause']} ({c['title']}): \"{a}\" became \"{b}\". Same words and numbers.", c["id"])
    if v2 is not None:
        touched = {c["clause"] for c in changes}
        for c in v2.clauses:
            if c.number != "0" and c.number not in touched:
                add(f"Clause {c.number} ({c.title}) is unchanged: the returned copy reads exactly as the agreement we sent.", None)
    return "\n".join(lines), spans


# ------------------------------------------------------------------- review
def review(v1_path: str | Path, v2_path: str | Path, letter: str, gw: Gateway, out_dir: str | Path, *,
           case: dict | None = None, playbook_path: str | Path | None = None, concurrency: int | None = None,
           route: bool = True, terms: bool = True) -> dict:
    """route=False skips Rerank and Decide, terms=False skips Extract (used by the evaluation to save credits)."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    v1, v2 = read_pdf(v1_path, "v1"), read_pdf(v2_path, "v2")
    hunks = compare_documents(v1, v2)
    playbook = json.loads(Path(playbook_path or ROOT / "data" / "playbook.json").read_text(encoding="utf-8"))
    errors: list[dict] = []
    workers = concurrency or int(os.environ.get("CLOCKED_CONCURRENCY", "3"))

    def run(endpoint: str, request: dict, label: str):
        try:
            return gw.call(endpoint, request, label)
        except StepError as e:
            errors.append(e.as_dict())
            return None

    b1, b2 = pdf_b64(v1_path), pdf_b64(v2_path)
    jobs = {
        "compare": ("compare", {"documents": [{"id": "v1", "pdf": b1}, {"id": "v2", "pdf": b2}],
                                "instructions": P.COMPARE_INSTRUCTIONS, "evidence": "all",
                                "return_text": True, "return_pdf": "highlight"}, "compare v1 v2"),
        "guard_v2": ("injection", {"pdf": b2, "source": "content", "instructions": P.GUARD_INSTRUCTIONS,
                                   "return_pdf": "box"}, "guard returned copy"),
        "terms_v1": ("extract", {"pdf": b1, "fields": P.EXTRACT_FIELDS, "return_text": True, "return_pdf": "box"}, "extract terms v1"),
        "terms_v2": ("extract", {"pdf": b2, "fields": P.EXTRACT_FIELDS, "return_text": True, "return_pdf": "box"}, "extract terms v2"),
    }
    clause_pairs = clauses_to_compare(hunks, v1, v2)
    for num, (ta, tb) in clause_pairs.items():   # Compare, clause by clause: our text against theirs
        jobs[f"cc_{num}"] = ("compare", {"documents": [{"id": "v1", "text": ta}, {"id": "v2", "text": tb}],
                                         "instructions": P.COMPARE_CLAUSE_INSTRUCTIONS, "evidence": "all"},
                             f"compare clause {num}")
    s1, s2 = xverify_claims(hunks)
    if s2:   # sentences of the returned copy, read against the PDF we sent
        jobs["xv_v2"] = ("grounding", {"pdf": b1, "answer": " ".join(s2)[:20000], "claims": s2,
                                       "instructions": P.XVERIFY_INSTRUCTIONS, "threshold": P.MEANING_CHANGED_FLOOR,
                                       "evidence": "all"}, "verify returned sentences against v1")
    if s1:   # and the other way round
        jobs["xv_v1"] = ("grounding", {"pdf": b2, "answer": " ".join(s1)[:20000], "claims": s1,
                                       "instructions": P.XVERIFY_INSTRUCTIONS, "threshold": P.MEANING_CHANGED_FLOOR,
                                       "evidence": "all"}, "verify sent sentences against v2")
    if not terms:
        jobs.pop("terms_v1"), jobs.pop("terms_v2")
    if letter.strip():
        jobs["guard_note"] = ("injection", {"text": letter, "source": "content", "instructions": P.GUARD_INSTRUCTIONS},
                              "guard cover note")
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {k: pool.submit(run, *v) for k, v in jobs.items()}
        res = {k: f.result() for k, f in futs.items()}

    cmp = parse_compare(res["compare"], v1, v2) if res.get("compare") else None
    xv = {"v2": parse_xverify(res.get("xv_v2"), s2) if s2 else {}, "v1": parse_xverify(res.get("xv_v1"), s1) if s1 else {}}
    if xv["v1"] is None and xv["v2"] is None:
        xv = None
    ccmp = {num: parse_clause_compare(res[f"cc_{num}"], num, ta, tb)
            for num, (ta, tb) in clause_pairs.items() if res.get(f"cc_{num}")}
    changes = build_changes(cross_label(hunks, ccmp if (ccmp or not clause_pairs) else None, xv), v1, v2)
    term_rows = _terms(res.get("terms_v1"), res.get("terms_v2"), v1, v2)
    guard = _guard(res.get("guard_note"), res.get("guard_v2"), v2, letter)

    # ---- Verify the cover note
    ledger, ledger_spans = build_ledger(changes, v2)
    claims_text = split_claims(letter)
    note = {"text": letter, "claims": [], "grounded": None, "verdict": None, "ran": False}
    if claims_text:
        vr = run("grounding", {"documents": [{"id": "v2", "text": v2.full_text}, {"id": "changes", "text": ledger}],
                               "answer": " ".join(claims_text), "claims": claims_text,
                               "instructions": P.VERIFY_INSTRUCTIONS, "threshold": P.VERIFY_THRESHOLD,
                               "evidence": "all"}, "verify cover note")
        if vr:
            note.update(ran=True, grounded=vr.get("grounded"), verdict=vr.get("verdict"),
                        claims=_claims(vr, claims_text, letter, ledger, ledger_spans, v2, changes))

    # ---- Rerank the playbook, then Decide, per open change
    fee = ((term_rows.get("annual_fee") or {}).get("v1") or {}).get("value")
    deal = {"annual_fee": fee, "initial_term": ((term_rows.get("initial_term") or {}).get("v1") or {}).get("value")}
    deal = {k: v for k, v in deal.items() if v}
    open_changes = [c for c in changes if c["blocking"]] if route else []

    def route_one(c: dict):
        c["playbook"] = _rerank(c, playbook, fee, run)
        c["decide"] = _decide(c, run, deal)
        _final_route(c)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(route_one, open_changes))
    for c in changes:
        if c["cls"] == "administrative":
            c["route"], c["route_reason"] = "accept", "Administrative: acknowledge and update the account record."

    crop = content_box([v1.path, v2.path])
    docs = _assets(out, v1, v2, res, cmp, crop)
    status, headline, reasons = _status(changes, note, guard, v2, errors, cmp)
    result = {
        "schema": 1,
        "case": case or {"id": "upload", "title": "Uploaded agreement", "description": ""},
        "mode": "replay" if gw.audit and all(a["replayed"] for a in gw.audit) else "live" if gw.audit else "rules-only",
        "status": status, "headline": headline, "reasons": reasons,
        "documents": docs,
        "changes": changes,
        "compare": {
            "document": None if cmp is None else {
                "verdict": cmp["verdict"], "relation": cmp["relation"], "related": cmp["related"],
                "conflicts": sum(f["relation"] == "conflict" for f in cmp["findings"]),
                "same_passages": sum(f["relation"] == "duplicate" for f in cmp["findings"]),
                "marked": {k: {kk: vv for kk, vv in m.items() if kk != "document"} for k, m in cmp["marked"].items()}},
            "clauses": [{"clause": num, "relation": cc["relation"], "probability": cc["probability"],
                         "conflicts": sum(f["relation"] == "conflict" for f in cc["findings"]),
                         "same": sum(f["relation"] == "duplicate" for f in cc["findings"])} for num, cc in ccmp.items()],
            "clauses_asked": len(clause_pairs),
            "unaligned": sum(1 for cc in ccmp.values() for f in cc["findings"] for k in ("v1", "v2") if f[k]["aligned"] is False),
            "kinds": sorted({f["kind"] for cc in ccmp.values() for f in cc["findings"] if f["relation"] == "conflict" and f["kind"]}),
        },
        "note": note, "guard": guard, "terms": list(term_rows.values()),
        "hidden_text": [{"text": h.text, "page": h.page, "reason": h.reason} for h in v2.hidden],
        "ablation": _ablation(changes, note, status),
        "endpoints": _endpoint_summary(gw, changes, note, guard, cmp),
        "errors": errors,
        "audit": {"requests": gw.audit, "credits": sum(int(a["credits"] or 0) for a in gw.audit),
                  "calls": len(gw.audit)},
        "playbook_topics": [{"id": t["id"], "title": t["title"]} for t in playbook["topics"]],
    }
    if crop:
        remap_rects(result, crop)
    (out / "review.json").write_text(json.dumps(result, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return result


# --------------------------------------------------------------- sub-steps
def _locate_at(doc: TextDoc, pdf_obj: dict | None, start, end, value: str) -> list[dict]:
    """Place a value by where the API read it, not by the first place the same characters occur.
    The words around the offset pick the line; the value is then boxed on that line only."""
    text = (pdf_obj or {}).get("text")
    page = _page_of(pdf_obj, start)
    if not isinstance(text, str) or start is None or text[start:end] != value:
        return locate(doc.path, value, page) if value and len(squash(value)) >= 8 else []
    lo = max(text.rfind("\n", 0, start) + 1, start - 40)
    hi = text.find("\n", end)
    hi = min(len(text) if hi < 0 else hi, end + 40)
    line = locate(doc.path, text[lo:hi], page)
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
    from .rules import _values
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
            if isinstance(val, list):
                val = "; ".join(str(x) for x in val)
            pdf_obj = (resp or {}).get("pdf")
            found = _locate_at(doc, pdf_obj, f.get("start"), f.get("end"), val) if val else []
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


def _guard(note_resp: dict | None, v2_resp: dict | None, v2: TextDoc, letter: str) -> list[dict]:
    out = []
    for target, resp in (("Cover note", note_resp), ("Returned copy", v2_resp)):
        if resp is None:
            if target == "Cover note" and not letter.strip():
                continue
            out.append({"target": target, "ran": False, "flagged": None})
            continue
        ev = []
        for e in resp.get("evidence") or []:
            item = {"quote": squash(e.get("quote", "")), "confidence": e.get("confidence"), "page": None, "rects": []}
            if target == "Returned copy":
                found = locate(v2.path, e.get("quote", ""), _page_of(resp.get("pdf"), e.get("start")))
                if found:
                    item.update(page=found[0]["page"], rects=found[0]["rects"])
            ev.append(item)
        p = resp.get("injection") or 0
        out.append({"target": target, "ran": True, "injection": p, "verdict": resp.get("verdict"),
                    "flagged": bool(p >= resp.get("threshold", 0.5)) or resp.get("verdict") not in (None, "benign"),
                    "category": resp.get("category"), "severity": resp.get("severity"), "evidence": ev})
    return out


def _claims(vr: dict, claims_text: list[str], letter: str, ledger: str, spans, v2: TextDoc, changes: list[dict]) -> list[dict]:
    out = []
    by_text = {squash(c.get("claim", "")): c for c in vr.get("claims") or []}
    listed = vr.get("claims") or []
    for i, text in enumerate(claims_text):
        c = by_text.get(squash(text)) or (listed[i] if i < len(listed) else {})
        start = letter.find(text)
        item = {"text": text, "start": start if start >= 0 else None, "end": start + len(text) if start >= 0 else None,
                "verdict": c.get("verdict") or "not_checked",
                "p": {k: c.get(k) for k in ("supported", "contradicted", "not_stated", "not_a_claim")},
                "evidence": None, "change_ids": []}
        ev = c.get("evidence") or None
        if ev and ev.get("quote"):
            view = {"document": ev.get("document"), "quote": squash(ev["quote"]), "confidence": ev.get("confidence"),
                    "page": None, "rects": [], "aligned": None}
            if ev.get("document") == "changes":
                view["aligned"] = ledger[ev["start"]:ev["end"]] == ev["quote"] if ev.get("start") is not None else None
                for a, b, cid in spans:
                    if ev.get("start") is not None and a <= ev["start"] < b and cid:
                        item["change_ids"].append(cid)
            elif ev.get("document") == "v2":
                view["aligned"] = v2.full_text[ev["start"]:ev["end"]] == ev["quote"] if ev.get("start") is not None else None
                found = locate(v2.path, ev["quote"])
                if found:
                    view.update(page=found[0]["page"], rects=found[0]["rects"])
                cl = _clause_of(v2, ev["quote"])
                item["change_ids"] += [ch["id"] for ch in changes if ch["clause"] == cl and ch["blocking"]]
            item["evidence"] = view
        for cid in item["change_ids"]:
            ch = next(x for x in changes if x["id"] == cid)
            if i not in ch["claims"]:
                ch["claims"].append(i)
        out.append(item)
    return out


def _passages(playbook: dict) -> list[dict]:
    return [{"id": p["id"], "title": p["title"], "text": p["text"], "metadata": {"topic": t["title"]}}
            for t in playbook["topics"] for p in t["passages"]]


def _rerank(c: dict, playbook: dict, fee: str | None, run) -> dict:
    passages = _passages(playbook)
    by_id = {p["id"]: p for p in passages}
    topic_of = {p["id"]: t for t in playbook["topics"] for p in t["passages"]}
    part = c["parts"][0]
    query = (f"The customer changed clause {c['clause']} ({c['title']}): {c['summary']}. We sent: \"{squash(part['v1']['quote'])}\". "
             f"They returned: \"{squash(part['v2']['quote'])}\". How should the deal desk respond?")
    context = {"our_role": "Provider (we sent the original agreement)", "annual_fee_in_our_version": fee or "unknown"}
    r = run("rerank", {"query": query, "context": context, "documents": passages,
                       "instructions": P.RERANK_INSTRUCTIONS}, f"rerank {c['clause']}")
    if r is None:
        return {"status": "error", "passages": [], "skipped": [], "expanded": False, "query": query}
    used, skipped, expand_topics = [], [], []
    for row in r.get("results") or []:
        p = by_id.get(row.get("id")) or (passages[row["index"]] if isinstance(row.get("index"), int) and row["index"] < len(passages) else None)
        if not p:
            continue
        view = {"id": p["id"], "title": p["title"], "text": p["text"], "action": row.get("action"),
                "reason": row.get("reason"), "relevance": row.get("relevance_score"), "document_score": row.get("document_score")}
        if row.get("action") == "use" and (row.get("relevance_score") or 0) >= P.RERANK_USE_FLOOR:
            used.append(view)
        elif row.get("action") == "expand":
            expand_topics.append(topic_of[p["id"]]["id"])
        elif row.get("reason") == "different_case":
            skipped.append(view)
    expanded = False
    if not used and expand_topics:                         # the right topic, the answer is in a neighbouring passage
        expanded = True
        tid = expand_topics[0]
        siblings = [p for p in passages if topic_of[p["id"]]["id"] == tid]
        r2 = run("rerank", {"query": query, "context": context, "documents": siblings,
                            "instructions": P.RERANK_INSTRUCTIONS}, f"rerank {c['clause']} expand")
        for row in (r2 or {}).get("results") or []:
            p = by_id.get(row.get("id"))
            if p and row.get("action") == "use" and (row.get("relevance_score") or 0) >= P.RERANK_USE_FLOOR:
                used.append({"id": p["id"], "title": p["title"], "text": p["text"], "action": "use", "reason": None,
                             "relevance": row.get("relevance_score"), "document_score": row.get("document_score")})
    return {"status": "guidance" if used else "none", "passages": used[:3], "skipped": skipped[:3],
            "expanded": expanded, "query": query, "read": len(passages)}


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
    guidance = (c["playbook"] or {}).get("passages") or []
    cmpv = c.get("compare") or {}
    state = {
        "clause": f"{c['clause']} {c['title']}",
        "change": "The Customer changed this term: " + c["summary"].replace(" → ", " became "),
        "we_sent": cmpv.get("v1_quote") or squash(part["v1"]["quote"]),
        "they_returned": cmpv.get("v2_quote") or squash(part["v2"]["quote"]),
        "deal_facts_from_our_version": deal or {},
        "playbook": [f"{p['title']}: {p['text']}" for p in guidance] or "The playbook has no passage that covers this change.",
    }
    r = run("decide", {"state": state, "instructions": P.DECIDE_INSTRUCTIONS, "questions": P.DECIDE_QUESTIONS},
            f"decide {c['clause']}")
    if r is None:
        return None
    a = r.get("answers") or {}
    may_accept, may_counter = (a.get("desk_may_accept") or {}).get("noul"), (a.get("desk_may_counter") or {}).get("noul")
    route, confidence = _route_from_answers(may_accept, may_counter)
    fav, mat = a.get("favors") or {}, a.get("materiality") or {}
    legend = mat.get("legend") or P.DECIDE_QUESTIONS["materiality"]["criteria"]
    score = mat.get("score")
    label = None
    if isinstance(legend, dict):                      # the API returns {"0": "...", "1": "..."}
        legend = [legend[k] for k in sorted(legend, key=lambda k: int(k))]
    if isinstance(score, (int, float)) and legend:
        label = str(legend[min(len(legend) - 1, max(0, round(score)))]).split(":")[0]
    return {"route": route, "route_confidence": confidence, "desk_may_accept": may_accept, "desk_may_counter": may_counter,
            "favors": fav.get("choice"), "favors_confidence": fav.get("confidence"),
            "materiality": score, "materiality_label": label}


def _final_route(c: dict) -> None:
    """Code decides. A model answer can be followed or tightened, never loosened."""
    pb, d = c["playbook"] or {}, c["decide"]
    if pb.get("status") == "error" or d is None:
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


def _status(changes, note, guard, v2: TextDoc, errors, cmp=None) -> tuple[str, str, list[dict]]:
    reasons = []
    wrong_pair = cmp is not None and (cmp.get("relation") == "unrelated" or (cmp.get("related") is not None
                                                                         and cmp["related"] < P.SAME_AGREEMENT_FLOOR))
    if wrong_pair:
        reasons.append({"level": "incomplete", "source": "Compare", "text": "These two files do not read as versions of "
                        f"the same agreement (related {cmp.get('related') or 0:.0%}). Check that the right files were uploaded."})
    for g in guard:
        if g.get("flagged"):
            reasons.append({"level": "block", "source": "Guard", "text": f"{g['target']}: text addressed to an AI reviewer "
                            f"({g.get('category') or 'injection'}, probability {g.get('injection', 0):.0%})."})
    if v2.hidden:
        reasons.append({"level": "block", "source": "Rules", "text": f"The returned copy contains hidden text on page "
                        f"{v2.hidden[0].page} ({'white' if v2.hidden[0].reason == 'white' else 'under 4 pt'})."})
    bad = [c for c in note["claims"] if c["verdict"] == "contradicted"]
    if bad:
        reasons.append({"level": "block", "source": "Verify", "text": f"{len(bad)} statement(s) in the cover note are "
                        "contradicted by the documents."})
    open_changes = [c for c in changes if c["blocking"]]
    if open_changes:
        esc = sum(c["route"] == "escalate" for c in open_changes)
        reasons.append({"level": "open", "source": "Rules + Verify + Compare", "text": f"{len(open_changes)} substantive change(s) "
                        f"to resolve" + (f", {esc} of them for another approver." if esc else ".")})
    for e in errors:
        reasons.append({"level": "incomplete", "source": e["endpoint"], "text": f"{e['step']}: {e['message']}"})
    if any(r["level"] == "block" for r in reasons):
        return "blocked", "Do not sign", reasons
    if errors or wrong_pair:
        return "incomplete", "Review incomplete", reasons
    if open_changes:
        return "changes", "Changes to resolve before signing", reasons
    return "ready", "Ready to sign", reasons + [{"level": "ok", "source": "All checks", "text":
                                                 "No substantive change, the cover note holds up, nothing addressed to an AI."}]


def _ablation(changes, note, status) -> list[dict]:
    opened = [c for c in changes if c["blocking"]]
    promoted_v = [c for c in opened if c["promoted"] and (c["verify"] or {}).get("relation") == "changed"]
    promoted_c_only = [c for c in opened if c["promoted"] and (c["verify"] or {}).get("relation") != "changed"]
    cleared = [c for c in changes if c["cls"] == "reworded"]
    bad = [c for c in note["claims"] if c["verdict"] == "contradicted"]
    confirmed_c = [c for c in opened if (c["compare"] or {}).get("relation") == "conflict"]
    no_guidance = [c for c in opened if (c["playbook"] or {}).get("status") == "none"]
    skipped = sum(len((c["playbook"] or {}).get("skipped") or []) for c in opened)
    verify_text = []
    if promoted_v:
        verify_text.append(f"{len(promoted_v)} change(s) of meaning would pass as spelling fixes.")
    if cleared:
        verify_text.append(f"{len(cleared)} faithful rewording(s) would be sent to negotiation.")
    if note["ran"] and bad:
        verify_text.append(f"The cover note would be taken at its word: {len(bad)} false statement(s) would not be caught"
                           + (", and this review would read “changes to resolve” instead of “do not sign”." if status == "blocked" else "."))
    elif note["ran"]:
        verify_text.append(f"The {len(note['claims'])} statement(s) of the cover note would go unchecked; here they hold up.")
    compare_text = []
    if promoted_c_only:
        compare_text.append(f"{len(promoted_c_only)} change(s) of meaning that only Compare caught would pass as spelling fixes.")
    if opened:
        compare_text.append(f"{len(confirmed_c)} of {len(opened)} change(s) would rest on one reading of the clause instead of two.")
    compare_text.append("Nothing would check that the two files are versions of the same agreement, and there would be "
                        "no paired copies of the PDFs.")
    return [
        {"endpoint": "Verify", "text": " ".join(verify_text) or "Not run.",
         "changes": [c["id"] for c in promoted_v + cleared],
         "claims": [i for i, c in enumerate(note["claims"]) if c["verdict"] == "contradicted"]},
        {"endpoint": "Compare", "text": " ".join(compare_text), "changes": [c["id"] for c in promoted_c_only]},
        {"endpoint": "Rerank", "text": "No change is open in this review, so there was nothing to look up." if not opened else
         f"{len(opened)} change(s) would reach the reviewer with no playbook answer attached"
         + (f"; {skipped} passage(s) written for a different deal size would be candidates to quote" if skipped else "")
         + (f"; {len(no_guidance)} change(s) with no guidance would not be sent to Legal automatically." if no_guidance else "."),
         "changes": [c["id"] for c in no_guidance]},
    ]


def _endpoint_summary(gw: Gateway, changes, note, guard, cmp) -> list[dict]:
    effects = {
        "Verify": "Reads meaning twice. Each changed sentence against the other version: a spelling fix that changes meaning "
                  "becomes a change, a faithful rewording is cleared. Each statement of the cover note: a contradicted one blocks signing.",
        "Compare": "Reads each clause in question as sent against as returned: a conflict confirms a change or overrules "
                   "a spelling fix, and vetoes clearing a rewording. On the two whole PDFs: checks they are versions of one "
                   "agreement and returns the paired, marked copies.",
        "Rerank": "Picks the playbook passages for each change and skips those for other deal sizes; none found means Legal.",
        "Decide": "Routes each change using the passages Rerank kept; low confidence means a person decides.",
        "Extract": "Reads the key terms of both versions for the side-by-side table and the deal size given to Rerank.",
        "Guard": "Flags text addressed to an AI reviewer; a hit blocks signing.",
    }
    out = []
    for name in ("Verify", "Compare", "Rerank", "Decide", "Extract", "Guard"):
        rows = [a for a in gw.audit if a["endpoint"] == name]
        out.append({"name": name, "path": rows[0]["path"] if rows else None, "calls": len(rows),
                    "credits": sum(int(a["credits"] or 0) for a in rows),
                    "model_ms": sum(int(float(a["model_ms"] or 0)) for a in rows), "effect": effects[name]})
    return out


def _assets(out: Path, v1: TextDoc, v2: TextDoc, res: dict, cmp: dict | None, crop=None) -> dict:
    """Write the PDFs and page images. `pages` are the clean source pages (the UI draws the changes on them
    from exact quote positions). `layers` are the copies Clocked marked, rendered as returned."""
    docs = {}
    for key, doc in (("v1", v1), ("v2", v2)):
        src = out / f"{key}.pdf"
        src.write_bytes(Path(doc.path).read_bytes())
        entry = {"name": "The version we sent" if key == "v1" else "The copy they returned", "pages_count": doc.pages,
                 "source_pdf": src.name, "marked_pdf": None, "marked_by": None, "extra_pdfs": [],
                 "pages": render_pages(src, out, key, crop=crop), "layers": []}
        marked = ((cmp or {}).get("marked") or {}).get(key) or {}
        if marked.get("document"):
            mp = out / f"{key}-compare-highlight.pdf"
            mp.write_bytes(base64.b64decode(marked["document"]))
            entry.update(marked_pdf=mp.name, marked_by="Clocked Compare: every passage it paired with the other version",
                         marked_spans=marked.get("marked"), spans=marked.get("spans"))
            entry["layers"].append({"id": "compare", "label": "Passages paired by Compare",
                                    "by": "Clocked Compare, return_pdf=highlight", "pdf": mp.name,
                                    "spans": marked.get("marked"), "pages": render_pages(mp, out, f"{key}-compare", crop=crop)})
        elif marked.get("error"):
            entry["marked_error"] = marked["error"]
        for lid, label, rkey in (("terms", "Key terms boxed by Extract", f"terms_{key}"),
                                 ("guard", "Guard evidence boxed", "guard_v2" if key == "v2" else "")):
            m = (((res.get(rkey) or {}).get("pdf") or {}) if rkey else {})
            m = m.get("marked") if isinstance(m, dict) else None
            if m and m.get("document") and m.get("marked"):
                name = f"{key}-{lid}-box.pdf"
                (out / name).write_bytes(base64.b64decode(m["document"]))
                entry["extra_pdfs"].append({"label": label, "file": name})
                entry["layers"].append({"id": lid, "label": label, "by": f"Clocked {'Extract' if lid == 'terms' else 'Guard'}, return_pdf=box",
                                        "pdf": name, "spans": m.get("marked"), "pages": render_pages(out / name, out, f"{key}-{lid}", crop=crop)})
        docs[key] = entry
    return docs
