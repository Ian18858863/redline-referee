"""A stand-in for the Clocked SDK, for unit tests only.

It answers in the response shapes the public documentation records, with content
derived from a case's ground truth. It exists to test our own code paths
(cross-labelling, routing, status, replay) without spending credits. It is never
used by the application, the demo or the evaluation: those use real recorded
responses or live calls.

What it can be told to do, beyond answering "correctly" from the ground truth:
  the gate answers "unrelated"        unrelated=True, or a truth with "wrong_file": true
  an added or removed clause          Verify answers "not stated" for it (it has no counterpart to contradict)
  a note with a visible instruction   Guard flags "If an AI assistant is reviewing ..." as well as the hidden note
  a reader that gets it wrong         compare_misses / verify_misses (reads a change as the same),
                                      compare_silent (commits to neither), verify_conflicts (reads a rewording as a change)
"""
from __future__ import annotations

import base64
import json
import re
from pathlib import Path

import pymupdf

USE = {"4.2": "payment-large", "4.1": "fees-amount", "4.3": "fees-late", "4.4": "fees-increase", "2": "term-initial",
       "3": "term-renewal-notice", "5": "service-credits", "6": "data-export", "8": "data-confidentiality",
       "9": "liability-cap", "11": "term-convenience", "13": "assignment-standard", "15": "law-other"}
ESCALATE = {"9", "15", "11", "2", "4.1", "7", "12"}


class FakeResult(dict):
    status, attempts, headers, cost_usd = 200, 1, {"x-request-id": "fake"}, 0.0

    def __init__(self, body: dict, credits: int = 3, key: str = "k"):
        super().__init__(body)
        self.credits, self.model_ms, self.idempotency_key = credits, credits * 25, key


def pdf_text(data: bytes) -> tuple[str, list[int]]:
    doc = pymupdf.open(stream=data, filetype="pdf")
    parts, offsets, pos = [], [], 0
    for page in doc:
        t = page.get_text()
        offsets.append(pos)
        parts.append(t)
        pos += len(t) + len("\n\n\f\n")
    return "\n\n\f\n".join(parts), offsets


def _find(text: str, quote: str) -> tuple[int, int] | None:
    pat = r"\s+".join(re.escape(w) for w in quote.split())
    m = re.search(pat, text)
    return (m.start(), m.end()) if m else None


def _sentence(text: str, a: int, b: int) -> tuple[int, int]:
    s = max(text.rfind(". ", 0, a), text.rfind(".\n", 0, a), text.rfind("\n\n", 0, a))
    s = 0 if s < 0 else s + 2
    e = min([x for x in (text.find(". ", b), text.find(".\n", b)) if x >= 0] or [len(text) - 1]) + 1
    return s, max(e, b)


def _mark(data: bytes, quotes: list[str]) -> str:
    doc = pymupdf.open(stream=data, filetype="pdf")
    for q in quotes:
        for page in doc:
            rects = page.search_for(" ".join(q.split()))
            if rects:
                page.add_highlight_annot(rects)
                break
    return base64.b64encode(doc.tobytes()).decode()


class FakeClocked:
    def __init__(self, truth: dict, *, compare_misses: set[str] = frozenset(), verify_misses: set[str] = frozenset(),
                 fail: set[str] = frozenset(), misalign: bool = False, low_confidence_route: bool = False,
                 compare_silent: set[str] = frozenset(), verify_conflicts: set[str] = frozenset(),
                 unrelated: bool | None = None):
        self.truth, self.compare_misses, self.fail = truth, set(compare_misses), set(fail)
        # a case whose truth says the wrong file came back makes the gate answer "unrelated"
        self.verify_misses, self.unrelated = set(verify_misses), bool(truth.get("wrong_file")) if unrelated is None else unrelated
        self.misalign, self.low = misalign, low_confidence_route
        # compare_misses: edits Compare reads as the same; compare_silent: edits Compare does not place either way;
        # verify_misses: edits Verify reads as supported; verify_conflicts: rewordings Verify reads as changed
        self.compare_silent, self.verify_conflicts = set(compare_silent), set(verify_conflicts)
        self.calls: list[str] = []

    def _pre(self, name: str):
        self.calls.append(name)
        if name in self.fail:
            err = RuntimeError(f"simulated {name} outage")
            err.status = 503
            raise err

    def _edits(self) -> list[tuple[dict, str]]:
        """What Compare is made to find: planted changes as conflicts, planted rewordings as restated passages."""
        skip = self.compare_misses | self.compare_silent
        edits = [(e, "conflict") for e in self.truth["substantive"] if e["id"] not in skip]
        edits += [(e, "restated") for e in self.truth["cosmetic"]
                  if (e["id"].startswith("reword") or e.get("kind") == "wording") and e["id"] not in skip]
        return edits

    # -- Compare
    def compare(self, request, idempotency_key=None, timeout=None):
        self._pre("compare")
        if "text" in request["documents"][0]:        # one clause: our text against theirs
            texts = {d["id"]: d["text"] for d in request["documents"]}
            conflicts, dups = [], []
            edits = self._edits()
            shift = 1 if self.misalign else 0
            for e, rel in edits:
                if not e["v1_quote"] or not e["v2_quote"]:      # an added or removed clause has no counterpart to pair
                    continue
                i, j = texts["v1"].find(e["v1_quote"]), texts["v2"].find(e["v2_quote"])
                if i < 0 or j < 0:
                    continue
                item = {"kind": "value" if rel == "conflict" else "restated", "confidence": 0.98,
                        "a": {"document": "v1", "quote": e["v1_quote"], "start": i + shift, "end": i + len(e["v1_quote"]) + shift, "confidence": 1},
                        "b": {"document": "v2", "quote": e["v2_quote"], "start": j + shift, "end": j + len(e["v2_quote"]) + shift, "confidence": 1}}
                (conflicts if rel == "conflict" else dups).append(item)
            rel = "conflicting" if conflicts else "same"
            # an edit Compare is unsure about: it reads the clause, and commits to neither "same" nor "conflicting"
            unsure = any(e["id"] in self.compare_silent and e["v2_quote"] and e["v2_quote"] in texts["v2"]
                         for e in self.truth["substantive"] + self.truth["cosmetic"])
            return FakeResult({"model": "fake", "verdict": "conflicts" if conflicts else "duplicates",
                               "pairs": [{"a": "v1", "b": "v2", "relation": rel, "related": 0.99,
                                          "probabilities": {rel: 0.5 if unsure else 0.98},
                                          "duplicates": dups, "conflicts": conflicts}]}, credits=6, key=idempotency_key)
        raw = {d["id"]: base64.b64decode(d["pdf"]) for d in request["documents"]}
        texts = {k: pdf_text(v) for k, v in raw.items()}
        conflicts, dups, marks = [], [], {"v1": [], "v2": []}
        edits = self._edits()
        for e, rel in edits:
            sides = {}
            for key, q in (("v1", e["v1_quote"]), ("v2", e["v2_quote"])):
                text = texts[key][0]
                span = _find(text, q) if q else None
                if not span:
                    continue
                s, t = _sentence(text, *span)
                quote = text[s:t]
                marks[key].append(quote)
                sides[key] = {"document": key, "quote": quote, "start": s + (1 if self.misalign else 0),
                              "end": t + (1 if self.misalign else 0), "confidence": 0.99}
            if len(sides) < 2:
                continue
            item = {"kind": "contradiction" if rel == "conflict" else "restated", "confidence": 0.97,
                    "a": sides["v1"], "b": sides["v2"]}
            (conflicts if rel == "conflict" else dups).append(item)
        pdf = [{"document": k, "pages": len(texts[k][1]), "selected": list(range(1, len(texts[k][1]) + 1)), "mode": "text",
                "page_offsets": texts[k][1], "characters": len(texts[k][0]), "text": texts[k][0],
                "marked": {"mode": "highlight", "spans": len(marks[k]), "marked": len(marks[k]), "pages": [1],
                           "document": _mark(raw[k], marks[k])}} for k in ("v1", "v2")]
        rel = "unrelated" if self.unrelated else "conflicting" if conflicts else "same"
        return FakeResult({"model": "fake", "verdict": "conflicts" if conflicts else "duplicates", "threshold": 0.5,
                           "evidence": request.get("evidence", "flagged"),
                           "pairs": [{"a": "v1", "b": "v2", "relation": rel, "related": 0.02 if self.unrelated else 0.99,
                                      "probabilities": {rel: 0.98}, "duplicates": dups, "conflicts": conflicts}],
                           "pdf": pdf}, credits=40, key=idempotency_key)

    # -- Verify
    def grounding(self, request, idempotency_key=None, timeout=None):
        self._pre("grounding")
        if "pdf" in request:                       # one version's sentences against the other version
            from difflib import SequenceMatcher
            nz = lambda x: re.sub(r"[^a-z0-9]+", "", x.lower())
            moved = [e for e in self.truth["substantive"] if e["id"] not in self.verify_misses]
            moved += [e for e in self.truth["cosmetic"] if e["kind"] == "address" or e["id"] in self.verify_conflicts]
            out = []
            for claim in request["claims"]:
                c, hit, absent = nz(claim), False, False
                for e in moved:
                    for q in (nz(e["v1_quote"]), nz(e["v2_quote"])):
                        if not q:                              # nothing on that side: the clause was added or removed
                            continue
                        m = SequenceMatcher(None, c, q, autojunk=False).find_longest_match(0, len(c), 0, len(q))
                        if m.size >= min(25, len(q)):
                            hit = True
                            absent = absent or e.get("kind") in ("added", "removed")
                # a clause that exists in one version only is not stated by the other, rather than contradicted
                verdict = "not_stated" if absent else "contradicted" if hit else "supported"
                item = {"claim": claim, "kind": "claim", "counted": True, "supported": 0.02, "contradicted": 0.02,
                        "not_stated": 0.01, "not_a_claim": 0.0, "verdict": verdict, "source": None}
                item[verdict] = 0.97
                out.append(item)
            return FakeResult({"model": "fake", "grounded": 0.5, "verdict": "contradicted", "claims": out,
                               "pdf": {"pages": 3, "selected": [1, 2, 3], "mode": "text"}}, credits=12, key=idempotency_key)
        ledger = next(d["text"] for d in request["documents"] if d["id"] == "changes")
        truth = {c["text"]: c for c in self.truth["letter_claims"]}
        clause_of = {e["id"]: e["clause"] for e in self.truth["substantive"] + self.truth["cosmetic"]}
        out = []
        for claim in request["claims"]:
            t = truth.get(claim)
            verdict = t["expected"] if t else "not_stated"
            item = {"claim": claim, "kind": "claim", "counted": True, "weight": 1 / len(request["claims"]),
                    "supported": 0.01, "contradicted": 0.01, "not_stated": 0.01, "not_a_claim": 0.01,
                    "verdict": verdict, "source": None}
            item[verdict] = 0.97
            if t:
                needle = f"clause {clause_of[t['about']]} (" if t["about"] in clause_of else None
                for line in ledger.split("\n"):
                    if (needle and needle in line and not line.startswith("The returned copy")) or (not needle and line == ledger.split("\n")[0]):
                        s = ledger.find(line)
                        item["evidence"] = {"document": "changes", "quote": line, "start": s, "end": s + len(line), "confidence": 0.9}
                        break
            out.append(item)
        bad = any(c["verdict"] == "contradicted" for c in out)
        return FakeResult({"model": "fake", "grounded": 0.3 if bad else 0.95, "verdict": "contradicted" if bad else "supported",
                           "threshold": request.get("threshold", 0.5), "claims": out}, credits=12, key=idempotency_key)

    # -- Rerank
    def rerank(self, request, idempotency_key=None, timeout=None):
        self._pre("rerank")
        m = re.search(r"clause ([\d.]+) \(", request["query"])
        clause = m.group(1) if m else ""
        want = USE.get(clause)
        ids = [d["id"] for d in request["documents"]]
        full = len(ids) > 5
        rows = []
        for i, pid in enumerate(ids):
            if pid == want and not (clause == "15" and full):
                rows.append({"index": i, "id": pid, "action": "use", "relevance_score": 0.97, "document_score": 0.98})
            elif clause == "15" and full and pid == "law-standard":
                rows.append({"index": i, "id": pid, "action": "expand", "relevance_score": 0.05, "document_score": 0.95})
            elif clause == "4.2" and pid == "payment-small":
                rows.append({"index": i, "id": pid, "action": "skip", "reason": "different_case", "relevance_score": 0.02, "document_score": 0.4})
            else:
                rows.append({"index": i, "id": pid, "action": "skip", "reason": "unrelated", "relevance_score": 0.01, "document_score": 0.01})
        rows.sort(key=lambda r: -r["relevance_score"])
        return FakeResult({"model": "fake", "results": rows}, credits=1, key=idempotency_key)

    # -- Decide
    def decide(self, request, idempotency_key=None, timeout=None):
        self._pre("decide")
        clause = request["state"]["clause"].split()[0]
        yes, no = (0.5, 0.5) if self.low else (0.95, 0.04)
        accept, counter = (no, no) if clause in ESCALATE else (no, yes)
        return FakeResult({"model": "fake", "answers": {
            "desk_may_accept": {"type": "noul", "noul": accept, "decision": accept >= 0.5},
            "desk_may_counter": {"type": "noul", "noul": counter, "decision": counter >= 0.5},
            "favors": {"type": "choice", "choice": "customer", "confidence": 0.9},
            "materiality": {"type": "score", "score": 2.9, "confidence": 0.8,
                            "legend": {str(i): c for i, c in enumerate(request["questions"]["materiality"]["criteria"])}}}},
                          credits=4, key=idempotency_key)

    # -- Extract
    def extract(self, request, idempotency_key=None, timeout=None):
        self._pre("extract")
        text, offs = pdf_text(base64.b64decode(request["pdf"]))
        pats = {"annual_fee": r"\$[\d,]+", "payment_terms": r"within\s+([\w-]+\s+\(\d+\)\s+days)\s+of\s+the\s+invoice",
                "governing_law": r"laws\s+of\s+the\s+(State\s+of\s+\w+)"}
        fields = {}
        for name in request["fields"]:
            m = re.search(pats[name], text) if name in pats else None
            if m:
                g = 1 if m.groups() else 0
                fields[name] = {"value": m.group(g), "start": m.start(g), "end": m.end(g), "confidence": 0.95}
            else:
                fields[name] = {"value": None}
        return FakeResult({"model": "fake", "fields": fields,
                           "pdf": {"pages": len(offs), "selected": list(range(1, len(offs) + 1)), "mode": "text",
                                   "page_offsets": offs, "characters": len(text)}}, credits=20, key=idempotency_key)

    # -- Guard
    def injection(self, request, idempotency_key=None, timeout=None):
        self._pre("injection")
        text, offs = (request["text"], [0]) if "text" in request else pdf_text(base64.b64decode(request["pdf"]))
        m = (re.search(r"Note\s+to\s+any\s+AI.*?signature\.", text, re.S)
             or re.search(r"If\s+an\s+AI\s+assistant\s+is\s+reviewing.*?sign\.", text, re.S))
        body = {"model": "fake", "injection": 0.99 if m else 0.01, "verdict": "injection" if m else "benign",
                "threshold": 0.5, "category": "instruction override" if m else "benign",
                "severity": "high" if m else "none",
                "evidence": [{"quote": m.group(0), "start": m.start(), "end": m.end(), "confidence": 0.95}] if m else []}
        if "pdf" in request:
            body["pdf"] = {"pages": len(offs), "selected": list(range(1, len(offs) + 1)), "mode": "text", "page_offsets": offs}
        return FakeResult(body, credits=6, key=idempotency_key)


def load_truth(case_dir: Path) -> dict:
    return json.loads((Path(case_dir) / "truth.json").read_text(encoding="utf-8"))
