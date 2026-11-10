"""The review: two PDFs and a cover note in, one decision with evidence out.

Order of work
  1. Rule layer (no model): align clauses, diff, classify, find hidden text.
  2. Clocked, in parallel: Compare (v1 vs v2, marked PDFs), Guard (note, v2), Extract (key terms of each).
  3. Cross-label rules and Compare. Compare can overrule the rule layer both ways:
     a "spelling fix" it reads as a conflict becomes a change; a rewording it reads
     as the same statement stops being one.
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
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
from pathlib import Path

from . import prompts as P
from .gateway import PRODUCT_NAME, Gateway, StepError, pdf_b64
from .rules import Hunk, compare_documents, normalise
from .textdoc import TextDoc, locate, read_pdf, render_pages, squash

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


def _overlaps(f: dict, h: Hunk) -> bool:
    c2, c1 = _nz(h.v2_changed), _nz(h.v1_changed)
    return bool((c2 and c2 in _nz(f["v2"]["quote"])) or (c1 and c1 in _nz(f["v1"]["quote"])))


def cross_label(hunks: list[Hunk], cmp: dict | None) -> list[dict]:
    """Attach Compare's reading to every rule hunk and let it overrule the rule layer where it may."""
    findings = (cmp or {}).get("findings", [])
    conflicts = [f for f in findings if f["relation"] == "conflict"]
    dups = [f for f in findings if f["relation"] == "duplicate"]
    labelled = []
    for h in hunks:
        here = [f for f in conflicts if h.clause in (f["v2"]["clause"], f["v1"]["clause"])]
        hit = sorted((f for f in here if _overlaps(f, h)), key=lambda f: -(f["confidence"] or 0))
        item = {"hunk": h, "cls": h.cls, "found_by": "rules", "compare": None, "why": h.why}
        if cmp is None:
            item["found_by"] = "rules"
        elif h.cls == "substantive":
            strong = [f for f in hit if (f["confidence"] or 0) >= P.CONFLICT_CONFIDENCE_FLOOR]
            same = [f for f in dups if f["v2"]["clause"] == h.clause and _overlaps(f, h)
                    and f["kind"] in ("restated", "verbatim") and (f["confidence"] or 0) >= P.RESTATED_CONFIDENCE_FLOOR]
            if strong:
                item.update(found_by="both", compare=_cmp_view(strong[0], "conflict"))
            elif h.kind == "wording" and same and not hit:
                item.update(cls="reworded", found_by="both", compare=_cmp_view(same[0], "restated"),
                            why="The words changed, but Compare reads both versions as the same statement.")
            elif here and (here[0]["confidence"] or 0) >= P.CONFLICT_CONFIDENCE_FLOOR:
                item.update(found_by="both", compare=_cmp_view(here[0], "conflict"))
            else:
                item["compare"] = {"relation": "silent", "note": "Compare did not flag this passage. The rule layer keeps it open."}
        elif h.cls == "spelling":
            explained = any(o.cls == "substantive" and o.clause == h.clause and any(_overlaps(f, o) for f in hit) for o in hunks)
            strong = [f for f in hit if (f["confidence"] or 0) >= P.CONFLICT_CONFIDENCE_FLOOR]
            if strong and not explained:
                item.update(cls="substantive", found_by="compare", compare=_cmp_view(strong[0], "conflict"),
                            why="The rule layer took this for a spelling fix. Compare reads the two versions as conflicting, so it is treated as a change.")
        elif hit:
            item["compare"] = _cmp_view(hit[0], "conflict")
            item["compare"]["note"] = "Compare also flagged this passage; the rule layer has identified what it is."
        labelled.append(item)
    return labelled


def _cmp_view(f: dict, relation: str) -> dict:
    return {"relation": relation, "kind": f["kind"], "confidence": f["confidence"],
            "v1_quote": squash(f["v1"]["quote"]), "v2_quote": squash(f["v2"]["quote"]),
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
        cmp_views = [it["compare"] for it in items if it["compare"]]
        found = {it["found_by"] for it in items}
        summaries = list(dict.fromkeys(p["summary"] for p in parts))
        changes.append({
            "clause": clause, "title": first["hunk"].title, "cls": cls, "blocking": cls == "substantive",
            "kind": first["hunk"].kind if len({i["hunk"].kind for i in items}) == 1 else "mixed",
            "summary": "; ".join(summaries[:3]) + (" …" if len(summaries) > 3 else ""),
            "why": first["why"],
            "found_by": "compare" if "compare" in found else "both" if "both" in found else "rules",
            "parts": parts, "compare": cmp_views[0] if cmp_views else None,
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
    found = locate(doc.path, changed, page) if changed and len(squash(changed)) >= 12 else []
    if not found and quote:
        found = locate(doc.path, quote, page)
    return {"quote": quote, "changed": changed, "page": found[0]["page"] if found else (page or None),
            "rects": found[0]["rects"] if found else [], "located": bool(found)}


# ------------------------------------------------------------------- ledger
def build_ledger(changes: list[dict]) -> tuple[str, list[tuple[int, int, str]]]:
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
    return "\n".join(lines), spans


# ------------------------------------------------------------------- review
def review(v1_path: str | Path, v2_path: str | Path, letter: str, gw: Gateway, out_dir: str | Path, *,
           case: dict | None = None, playbook_path: str | Path | None = None, concurrency: int | None = None) -> dict:
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
        "terms_v1": ("extract", {"pdf": b1, "fields": P.EXTRACT_FIELDS, "return_pdf": "box"}, "extract terms v1"),
        "terms_v2": ("extract", {"pdf": b2, "fields": P.EXTRACT_FIELDS, "return_pdf": "box"}, "extract terms v2"),
    }
    if letter.strip():
        jobs["guard_note"] = ("injection", {"text": letter, "source": "content", "instructions": P.GUARD_INSTRUCTIONS},
                              "guard cover note")
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {k: pool.submit(run, *v) for k, v in jobs.items()}
        res = {k: f.result() for k, f in futs.items()}

    cmp = parse_compare(res["compare"], v1, v2) if res.get("compare") else None
    changes = build_changes(cross_label(hunks, cmp), v1, v2)
    terms = _terms(res.get("terms_v1"), res.get("terms_v2"), v1, v2)
    guard = _guard(res.get("guard_note"), res.get("guard_v2"), v2, letter)

    # ---- Verify the cover note
    ledger, ledger_spans = build_ledger(changes)
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
    fee = ((terms.get("annual_fee") or {}).get("v1") or {}).get("value")
    open_changes = [c for c in changes if c["blocking"]]

    def route_one(c: dict):
        c["playbook"] = _rerank(c, playbook, fee, run)
        c["decide"] = _decide(c, run)
        _final_route(c)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(route_one, open_changes))
    for c in changes:
        if c["cls"] == "administrative":
            c["route"], c["route_reason"] = "accept", "Administrative: acknowledge and update the account record."

    docs = _assets(out, v1, v2, res, cmp)
    status, headline, reasons = _status(changes, note, guard, v2, errors)
    result = {
        "schema": 1,
        "case": case or {"id": "upload", "title": "Uploaded agreement", "description": ""},
        "mode": "replay" if gw.audit and all(a["replayed"] for a in gw.audit) else "live" if gw.audit else "rules-only",
        "status": status, "headline": headline, "reasons": reasons,
        "documents": docs,
        "changes": changes,
        "compare": None if cmp is None else {
            "verdict": cmp["verdict"], "relation": cmp["relation"], "related": cmp["related"],
            "conflicts": sum(f["relation"] == "conflict" for f in cmp["findings"]),
            "same_passages": sum(f["relation"] == "duplicate" for f in cmp["findings"]),
            "unaligned": sum(1 for f in cmp["findings"] for s in ("v1", "v2") if f[s]["aligned"] is False),
            "marked": {k: {kk: vv for kk, vv in m.items() if kk != "document"} for k, m in cmp["marked"].items()},
            "unplaced": [_cmp_view(f, "conflict") for f in cmp["findings"] if f["relation"] == "conflict"
                         and not any(f["v2"]["clause"] == c["clause"] or f["v1"]["clause"] == c["clause"] for c in changes)],
        },
        "note": note, "guard": guard, "terms": list(terms.values()),
        "hidden_text": [{"text": h.text, "page": h.page, "reason": h.reason} for h in v2.hidden],
        "ablation": _ablation(changes, note, status),
        "endpoints": _endpoint_summary(gw, changes, note, guard, cmp),
        "errors": errors,
        "audit": {"requests": gw.audit, "credits": sum(int(a["credits"] or 0) for a in gw.audit),
                  "calls": len(gw.audit)},
        "playbook_topics": [{"id": t["id"], "title": t["title"]} for t in playbook["topics"]],
    }
    (out / "review.json").write_text(json.dumps(result, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return result


# --------------------------------------------------------------- sub-steps
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
            found = locate(doc.path, val, _page_of((resp or {}).get("pdf"), f.get("start"))) if val else []
            row[key] = {"value": val, "confidence": f.get("confidence"), "read": resp is not None,
                        "page": found[0]["page"] if found else None, "rects": found[0]["rects"] if found else []}
        a, b = row["v1"]["value"], row["v2"]["value"]
        row["changed"] = None if (not row["v1"]["read"] or not row["v2"]["read"]) else (
            normalise(a or "") != normalise(b or ""))
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
        if row.get("action") == "use":
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
            if p and row.get("action") == "use":
                used.append({"id": p["id"], "title": p["title"], "text": p["text"], "action": "use", "reason": None,
                             "relevance": row.get("relevance_score"), "document_score": row.get("document_score")})
    return {"status": "guidance" if used else "none", "passages": used[:3], "skipped": skipped[:3],
            "expanded": expanded, "query": query, "read": len(passages)}


def _decide(c: dict, run) -> dict | None:
    part = c["parts"][0]
    guidance = (c["playbook"] or {}).get("passages") or []
    state = {
        "clause": f"{c['clause']} {c['title']}", "change": c["summary"],
        "we_sent": squash(part["v1"]["quote"]), "they_returned": squash(part["v2"]["quote"]),
        "playbook": [f"{p['title']}: {p['text']}" for p in guidance] or "The playbook has no passage that covers this change.",
    }
    r = run("decide", {"state": state, "instructions": P.DECIDE_INSTRUCTIONS, "questions": P.DECIDE_QUESTIONS},
            f"decide {c['clause']}")
    if r is None:
        return None
    a = r.get("answers") or {}
    route, fav, mat = a.get("route") or {}, a.get("favors") or {}, a.get("materiality") or {}
    legend = mat.get("legend") or P.DECIDE_QUESTIONS["materiality"]["criteria"]
    score = mat.get("score")
    label = None
    if isinstance(score, (int, float)) and legend:
        label = str(legend[min(len(legend) - 1, max(0, round(score)))]).split(":")[0]
    return {"route": route.get("choice"), "route_confidence": route.get("confidence"),
            "route_probabilities": route.get("probabilities"),
            "favors": fav.get("choice"), "favors_confidence": fav.get("confidence"),
            "materiality": score, "materiality_label": label}


def _final_route(c: dict) -> None:
    """Code decides. A model answer can be followed or tightened, never loosened."""
    pb, d = c["playbook"] or {}, c["decide"]
    if pb.get("status") == "error" or d is None:
        c["route"], c["route_reason"] = "escalate", "A routing step failed, so a person decides."
    elif pb.get("status") == "none":
        c["route"], c["route_reason"] = "escalate", "The playbook has no guidance for this change, so it goes to Legal."
    elif not d.get("route") or (d.get("route_confidence") or 0) < P.ROUTE_CONFIDENCE_FLOOR:
        c["route"], c["route_reason"] = "escalate", "Decide was not confident about the route, so a person decides."
    else:
        c["route"] = d["route"]
        c["route_reason"] = {"accept": "Within what the playbook lets the deal desk accept.",
                             "counter": "The playbook gives a position to send back.",
                             "escalate": "The playbook reserves this for another approver."}.get(d["route"], "")


def _status(changes, note, guard, v2: TextDoc, errors) -> tuple[str, str, list[dict]]:
    reasons = []
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
        reasons.append({"level": "open", "source": "Compare + rules", "text": f"{len(open_changes)} substantive change(s) "
                        f"to resolve" + (f", {esc} of them for another approver." if esc else ".")})
    for e in errors:
        reasons.append({"level": "incomplete", "source": e["endpoint"], "text": f"{e['step']}: {e['message']}"})
    if any(r["level"] == "block" for r in reasons):
        return "blocked", "Do not sign", reasons
    if errors:
        return "incomplete", "Review incomplete", reasons
    if open_changes:
        return "changes", "Changes to resolve before signing", reasons
    return "ready", "Ready to sign", reasons + [{"level": "ok", "source": "All checks", "text":
                                                 "No substantive change, the cover note holds up, nothing addressed to an AI."}]


def _ablation(changes, note, status) -> list[dict]:
    promoted = [c for c in changes if c["found_by"] == "compare" and c["blocking"]]
    cleared = [c for c in changes if c["cls"] == "reworded"]
    bad = [c for c in note["claims"] if c["verdict"] == "contradicted"]
    opened = [c for c in changes if c["blocking"]]
    no_guidance = [c for c in opened if (c["playbook"] or {}).get("status") == "none"]
    skipped = sum(len((c["playbook"] or {}).get("skipped") or []) for c in opened)
    return [
        {"endpoint": "Compare", "text": (f"{len(promoted)} change(s) of meaning would pass as spelling fixes. " if promoted else "")
         + (f"{len(cleared)} faithful rewording(s) would be sent to negotiation. " if cleared else "")
         + "The two PDFs would come back unmarked.", "changes": [c["id"] for c in promoted + cleared]},
        {"endpoint": "Verify", "text": (f"The cover note would be taken at its word: {len(bad)} false statement(s) would not be caught"
                                        + (", and this review would read “changes to resolve” instead of “do not sign”." if status == "blocked" and bad else ".")
                                        if note["ran"] else "Not run."), "claims": [i for i, c in enumerate(note["claims"]) if c["verdict"] == "contradicted"]},
        {"endpoint": "Rerank", "text": f"{len(opened)} change(s) would reach the reviewer with no playbook answer attached"
         + (f"; {skipped} passage(s) written for a different deal size would be candidates to quote" if skipped else "")
         + (f"; {len(no_guidance)} change(s) with no guidance would not be sent to Legal automatically." if no_guidance else "."),
         "changes": [c["id"] for c in no_guidance]},
    ]


def _endpoint_summary(gw: Gateway, changes, note, guard, cmp) -> list[dict]:
    effects = {
        "Compare": "Marks both PDFs; confirms rule findings; overrules the rule layer on meaning.",
        "Verify": "Checks each statement of the cover note; a contradicted one blocks signing.",
        "Rerank": "Picks the playbook passages for each change and skips those for other deal sizes; none found means Legal.",
        "Decide": "Routes each change using the passages Rerank kept; low confidence means a person decides.",
        "Extract": "Reads the key terms of both versions for the side-by-side table and the deal size given to Rerank.",
        "Guard": "Flags text addressed to an AI reviewer; a hit blocks signing.",
    }
    out = []
    for name in ("Compare", "Verify", "Rerank", "Decide", "Extract", "Guard"):
        rows = [a for a in gw.audit if a["endpoint"] == name]
        out.append({"name": name, "path": rows[0]["path"] if rows else None, "calls": len(rows),
                    "credits": sum(int(a["credits"] or 0) for a in rows),
                    "model_ms": sum(int(a["model_ms"] or 0) for a in rows), "effect": effects[name]})
    return out


def _assets(out: Path, v1: TextDoc, v2: TextDoc, res: dict, cmp: dict | None) -> dict:
    docs = {}
    for key, doc in (("v1", v1), ("v2", v2)):
        src = out / f"{key}.pdf"
        src.write_bytes(Path(doc.path).read_bytes())
        entry = {"name": "The version we sent" if key == "v1" else "The copy they returned", "pages_count": doc.pages,
                 "source_pdf": src.name, "marked_pdf": None, "marked_by": None, "extra_pdfs": []}
        marked = ((cmp or {}).get("marked") or {}).get(key) or {}
        render_from = src
        if marked.get("document"):
            mp = out / f"{key}-compare-highlight.pdf"
            mp.write_bytes(base64.b64decode(marked["document"]))
            entry.update(marked_pdf=mp.name, marked_by="Clocked Compare, return_pdf=highlight",
                         marked_spans=marked.get("marked"), spans=marked.get("spans"))
            render_from = mp
        elif marked.get("error"):
            entry["marked_error"] = marked["error"]
        for label, rkey in (("Key terms boxed by Extract", f"terms_{key}"), ("Guard evidence boxed", "guard_v2" if key == "v2" else "")):
            m = (((res.get(rkey) or {}).get("pdf") or {}) if rkey else {})
            m = m.get("marked") if isinstance(m, dict) else None
            if m and m.get("document"):
                name = f"{key}-{'terms' if 'terms' in rkey else 'guard'}-box.pdf"
                (out / name).write_bytes(base64.b64decode(m["document"]))
                entry["extra_pdfs"].append({"label": label, "file": name})
        entry["pages"] = render_pages(render_from, out, key)
        docs[key] = entry
    return docs
