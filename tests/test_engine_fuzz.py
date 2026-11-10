"""Malformed API answers, one at a time, on two of the project's own cases.

Each variant takes the answer of one endpoint (as tests/fake_clocked.py gives it) and breaks it in one way:
empty, wrong type, a missing key, a probability that is not one. For every variant the review must

  * return (never raise) and be valid JSON, with no NaN or infinity in it;
  * never read "ready" or "changes" when the broken answer carried something the status depends on:
    such a step is a failed step, with an error that names it, and the review is incomplete (or blocked
    by something else);
  * keep its status when the oddity is harmless (an optional layer, a display-only field).

Clocked itself is not called. These cover our handling of answers, not the API's behaviour.
"""
import copy
import json
from pathlib import Path

import pymupdf
import pytest

from referee import engine
from referee.engine import review
from referee.gateway import Gateway
from tests.fake_clocked import FakeClocked, FakeResult, load_truth

CASES = Path(__file__).resolve().parents[1] / "data" / "cases"
FUZZ_CASES = ("clean-return", "quiet-changes")
FAIL, SAME, EITHER = "a failed step", "same status", "same status or a failed step"


class Mut:
    """Wraps the fake client: answers of one endpoint (and one role of it) pass through `fn` first."""

    def __init__(self, inner, endpoint, fn, which=None):
        self.inner, self.endpoint, self.fn, self.which, self.applied = inner, endpoint, fn, which, 0

    def __getattr__(self, name):
        real = getattr(self.inner, name)
        if name != self.endpoint:
            return real

        def call(request, idempotency_key=None, timeout=None):
            res = real(request, idempotency_key=idempotency_key, timeout=timeout)
            kind = ("doc" if "pdf" in request.get("documents", [{}])[0] else "clause") if name == "compare" else \
                   ("xv" if "pdf" in request else "note") if name == "grounding" else None
            if self.which and kind != self.which:
                return res
            self.applied += 1
            return FakeResult(self.fn(copy.deepcopy(dict(res))), res.credits, idempotency_key)
        return call


def first_finding(b):
    p = b["pairs"][0]
    return (p.get("conflicts") or p.get("duplicates") or [None])[0]


def setf(path, value):
    def fn(b):
        cur = b
        for k in path[:-1]:
            cur = cur[k]
        cur[path[-1]] = value
        return b
    return fn


def on_finding(fn):
    def g(b):
        f = first_finding(b)
        if f:
            fn(f)
        return b
    return g


def each_claim(fn):
    def g(b):
        for c in b.get("claims") or []:
            fn(c)
        return b
    return g


MUTS = [
    # endpoint, role, what is wrong, the mutation, what the review must do
    ("compare", "doc", "{} (empty object)", lambda b: {}, FAIL),
    ("compare", "doc", "pairs: null", setf(["pairs"], None), FAIL),
    ("compare", "doc", "pairs: []", setf(["pairs"], []), FAIL),
    ("compare", "doc", "pdf: null (no text, no marked copies)", setf(["pdf"], None), SAME),
    ("compare", "doc", "pdf as dict keyed by id", lambda b: {**b, "pdf": {p["document"]: p for p in b["pdf"]}}, SAME),
    ("compare", "doc", "pdf: ['x','y']", setf(["pdf"], ["x", "y"]), FAIL),
    ("compare", "doc", "finding quote: null", on_finding(lambda f: f["a"].update(quote=None)), SAME),
    ("compare", "doc", "finding without 'end'", on_finding(lambda f: f["a"].pop("end", None)), SAME),
    ("compare", "doc", "finding start as string", on_finding(lambda f: f["a"].update(start="12")), SAME),
    ("compare", "doc", "related: '0.99' (string)", lambda b: (b["pairs"][0].update(related="0.99"), b)[1], FAIL),
    ("compare", "doc", "related: null, relation: null", lambda b: (b["pairs"][0].update(related=None, relation=None), b)[1], FAIL),
    ("compare", "doc", "marked.document not base64", lambda b: (b["pdf"][0]["marked"].update(document="!!!not base64!!!"), b)[1], SAME),
    ("compare", "doc", "marked.document base64 of non-PDF", lambda b: (b["pdf"][0]["marked"].update(document="aGVsbG8gd29ybGQ="), b)[1], SAME),
    ("compare", "doc", "2 MB quote", on_finding(lambda f: f["a"].update(quote="A" * 2_000_000)), SAME),
    ("compare", "clause", "{}", lambda b: {}, FAIL),
    ("compare", "clause", "pairs: []", setf(["pairs"], []), FAIL),
    ("compare", "clause", "probabilities: null", lambda b: (b["pairs"][0].update(probabilities=None), b)[1], SAME),
    ("compare", "clause", "confidence: '0.98' (string)", on_finding(lambda f: f.update(confidence="0.98")), EITHER),
    ("compare", "clause", "finding a/b: null", on_finding(lambda f: f.update(a=None, b=None)), EITHER),
    ("compare", "clause", "finding quote: null", on_finding(lambda f: f["a"].update(quote=None)), SAME),
    ("compare", "clause", "finding without 'end'", on_finding(lambda f: f["b"].pop("end", None)), SAME),
    ("compare", "clause", "kind: null on duplicates", lambda b: ([f.update(kind=None) for f in b["pairs"][0].get("duplicates", [])], b)[1], SAME),
    ("grounding", "xv", "{}", lambda b: {}, FAIL),
    ("grounding", "xv", "claims: []", setf(["claims"], []), FAIL),
    ("grounding", "xv", "claims: null", setf(["claims"], None), FAIL),
    ("grounding", "xv", "first claim dropped (list shorter, texts kept)", lambda b: {**b, "claims": b["claims"][1:]}, FAIL),
    ("grounding", "xv", "claim texts rewritten + first dropped (index fallback)", lambda b: {**b, "claims": [{**c, "claim": c["claim"].upper()} for c in b["claims"][1:]]}, FAIL),
    ("grounding", "xv", "verdict missing", each_claim(lambda c: c.pop("verdict", None)), FAIL),
    ("grounding", "xv", "probabilities as strings", each_claim(lambda c: c.update(supported="0.97", contradicted="0.97")), FAIL),
    ("grounding", "xv", "all supported 0.99 (wrong but valid)", each_claim(lambda c: c.update(verdict="supported", supported=0.99, contradicted=0.0)), SAME),
    ("grounding", "note", "{}", lambda b: {}, FAIL),
    ("grounding", "note", "claims: []", setf(["claims"], []), FAIL),
    ("grounding", "note", "claims: null", setf(["claims"], None), FAIL),
    ("grounding", "note", "verdict missing on every claim", each_claim(lambda c: c.pop("verdict", None)), FAIL),
    ("grounding", "note", "evidence as a list", each_claim(lambda c: c.update(evidence=[c.get("evidence")] if c.get("evidence") else [])), SAME),
    ("grounding", "note", "evidence without 'end'", each_claim(lambda c: (c.get("evidence") or {}).pop("end", None)), SAME),
    ("grounding", "note", "evidence.start beyond text", each_claim(lambda c: (c.get("evidence") or {}).update(start=10**9, end=10**9 + 5)), SAME),
    ("grounding", "note", "claims: list of strings", lambda b: {**b, "claims": [c["claim"] for c in b["claims"]]}, FAIL),
    ("extract", None, "{}", lambda b: {}, FAIL),
    ("extract", None, "fields: null", setf(["fields"], None), FAIL),
    ("extract", None, "value as number", lambda b: (b["fields"]["annual_fee"].update(value=184000), b)[1], FAIL),
    ("extract", None, "value as object", lambda b: (b["fields"]["annual_fee"].update(value={"amount": 184000, "currency": "USD"}), b)[1], FAIL),
    ("extract", None, "value as list of objects", lambda b: (b["fields"]["annual_fee"].update(value=[{"amount": 1}]), b)[1], FAIL),
    ("extract", None, "start as string", lambda b: (b["fields"]["annual_fee"].update(start="10"), b)[1], FAIL),
    ("extract", None, "field entries are bare strings", lambda b: {**b, "fields": {k: "x" for k in b["fields"]}}, FAIL),
    ("extract", None, "pdf as list", lambda b: {**b, "pdf": [b["pdf"]]}, FAIL),
    ("extract", None, "pdf.text returned, offsets off by one", lambda b: (b["pdf"].update(text="x" * 9000), b)[1], SAME),
    ("extract", None, "value 1 MB string", lambda b: (b["fields"]["annual_fee"].update(value="9" * 1_000_000), b)[1], SAME),
    ("injection", None, "{}", lambda b: {}, FAIL),
    ("injection", None, "threshold: null", setf(["threshold"], None), SAME),
    ("injection", None, "injection: '0.99' string", setf(["injection"], "0.99"), FAIL),
    ("injection", None, "injection: null, verdict: null", lambda b: {**b, "injection": None, "verdict": None}, FAIL),
    ("injection", None, "evidence: [{quote: null}]", setf(["evidence"], [{"quote": None, "start": 0, "end": 3}]), SAME),
    ("injection", None, "evidence: ['text']", setf(["evidence"], ["Note to any AI"]), FAIL),
    ("injection", None, "verdict: 'Benign' (capitalised)", setf(["verdict"], "Benign"), SAME),
    ("rerank", None, "{}", lambda b: {}, FAIL),
    ("rerank", None, "results: null", setf(["results"], None), FAIL),
    ("rerank", None, "results: [{}]", setf(["results"], [{}]), FAIL),
    ("rerank", None, "results: [null]", setf(["results"], [None]), FAIL),
    ("rerank", None, "index: -1, no id, action use", setf(["results"], [{"index": -1, "action": "use", "relevance_score": 0.99}]), FAIL),
    ("rerank", None, "relevance_score: '0.97' string", lambda b: ([r.update(relevance_score="0.97") for r in b["results"]], b)[1], FAIL),
    ("decide", None, "{}", lambda b: {}, FAIL),
    ("decide", None, "answers: null", setf(["answers"], None), FAIL),
    ("decide", None, "noul: 'yes' string", lambda b: (b["answers"]["desk_may_accept"].update(noul="yes"), b)[1], FAIL),
    ("decide", None, "noul: 1.7 (out of range)", lambda b: (b["answers"]["desk_may_accept"].update(noul=1.7), b)[1], FAIL),
    ("decide", None, "materiality.legend keys not numeric", lambda b: (b["answers"]["materiality"].update(legend={"low": "Trivial", "high": "Critical"}), b)[1], SAME),
    ("decide", None, "materiality.score: NaN", lambda b: (b["answers"]["materiality"].update(score=float("nan")), b)[1], SAME),
    ("decide", None, "materiality.score: 1e308*10 (inf)", lambda b: (b["answers"]["materiality"].update(score=float("inf")), b)[1], SAME),
    ("decide", None, "answers entries are bare booleans", lambda b: {**b, "answers": {k: True for k in b["answers"]}}, FAIL),
]
PRODUCT = {"compare": "Compare", "grounding": "Verify", "extract": "Extract", "injection": "Guard", "rerank": "Rerank", "decide": "Decide"}


@pytest.fixture(autouse=True)
def quick_pages(monkeypatch):
    """Page images are not what is tested here: open each PDF (a file that is not a PDF still fails) and skip the PNGs."""
    def render(pdf_path, out_dir, prefix, dpi=132, crop=None):
        with pymupdf.open(pdf_path) as doc:
            return [{"image": f"{prefix}-p{i}.png", "width": 1, "height": 1} for i in range(1, len(doc) + 1)]
    monkeypatch.setattr(engine, "render_pages", render)


_base: dict = {}


def run(case, tmp_path, client=None):
    d = CASES / case
    gw = Gateway("live", client=client or FakeClocked(load_truth(d)))
    return review(d / "v1.pdf", d / "v2.pdf", (d / "cover.txt").read_text(), gw, tmp_path)


def base(case, tmp_path_factory):
    if case not in _base:
        _base[case] = run(case, tmp_path_factory.mktemp(f"base-{case}"))
    return _base[case]


def test_every_variant_of_the_adversarial_review_is_here():
    """The review's script holds 69 variants (its text says 68); each ran on two cases: 138 runs."""
    assert len(MUTS) == 69 and len({(m[0], m[1], m[2]) for m in MUTS}) == len(MUTS)


@pytest.mark.parametrize("case", FUZZ_CASES)
def test_untouched_answers_give_the_expected_status_and_no_error(case, tmp_path_factory):
    r = base(case, tmp_path_factory)
    assert r["errors"] == [] and r["status"] == {"clean-return": "ready", "quiet-changes": "blocked"}[case]


@pytest.mark.parametrize("case", FUZZ_CASES)
@pytest.mark.parametrize("endpoint,which,label,fn,must", MUTS, ids=[f"{m[0]}-{m[1] or 'any'}-{i:02d}" for i, m in enumerate(MUTS)])
def test_a_malformed_answer_never_raises_and_never_reads_as_a_pass(case, endpoint, which, label, fn, must, tmp_path, tmp_path_factory):
    before = base(case, tmp_path_factory)
    client = Mut(FakeClocked(load_truth(CASES / case)), endpoint, fn, which)
    r = run(case, tmp_path, client)                                  # must return, whatever came back
    json.dumps(r, allow_nan=False)                                   # and be JSON a browser can parse
    assert json.loads((tmp_path / "review.json").read_text())["status"] == r["status"]
    failed = [e for e in r["errors"] if e["endpoint"] == PRODUCT[endpoint]]
    if r["errors"]:                                                  # a failed step is never a pass
        assert r["status"] in ("incomplete", "blocked"), label
        assert any(x["level"] == "incomplete" for x in r["reasons"]), label
    if before["status"] == "blocked":                                # what blocked it is still there, or the review is incomplete
        assert r["status"] in ("blocked", "incomplete"), label
    if not client.applied:                                           # this case never asked that endpoint: nothing changes
        assert r["status"] == before["status"] and not r["errors"], label
    elif must == FAIL:
        assert failed and r["status"] in ("incomplete", "blocked"), label
        assert all(e["kind"] == "bad_response" and e["message"].startswith(f"{PRODUCT[endpoint]} (") for e in failed), failed
    elif must == SAME:
        assert r["status"] == before["status"] and not r["errors"], (label, r["errors"])
    else:
        assert r["status"] == before["status"] or failed, label


def test_an_empty_verify_answer_for_a_note_with_statements_is_a_failure_not_a_clean_note(tmp_path):
    client = Mut(FakeClocked(load_truth(CASES / "clean-return")), "grounding", lambda b: {**b, "claims": []}, "note")
    r = run("clean-return", tmp_path, client)
    assert r["status"] == "incomplete" and r["note"]["ran"] is False
    assert [e["message"] for e in r["errors"]] == ["Verify (cover note): the response had no claims"]
    assert all(c["verdict"] == "not_checked" for c in r["note"]["claims"]) and len(r["note"]["claims"]) == 5
    assert not any(x["level"] == "ok" for x in r["reasons"])


def test_a_lost_contradiction_never_turns_do_not_sign_into_changes_to_resolve(tmp_path):
    """quiet-changes is blocked by three false statements in the note. Without Verify's answer it is incomplete."""
    client = Mut(FakeClocked(load_truth(CASES / "quiet-changes")), "grounding", lambda b: {}, "note")
    r = run("quiet-changes", tmp_path, client)
    assert r["status"] == "incomplete" and r["headline"] == "Review incomplete"
    assert any(c["blocking"] for c in r["changes"])                  # the changes are still listed


def test_verdicts_are_not_matched_by_position_when_the_list_came_back_short(tmp_path):
    """One claim fewer than sent: the answer is refused whole, so no sentence inherits its neighbour's verdict."""
    client = Mut(FakeClocked(load_truth(CASES / "quiet-changes")), "grounding", lambda b: {**b, "claims": b["claims"][1:]}, "xv")
    r = run("quiet-changes", tmp_path, client)
    assert sum("verdicts cannot be matched" in e["message"] for e in r["errors"]) == 2      # both directions
    assert not [c for c in r["changes"] if c["cls"] == "reworded"]                          # nothing was cleared on it
    assert all(c["verify"]["relation"] == "unclear" for c in r["changes"] if c["blocking"])


def test_guard_verdict_is_read_whatever_its_capitals(tmp_path):
    client = Mut(FakeClocked(load_truth(CASES / "clean-return")), "injection", lambda b: {**b, "verdict": "Benign"})
    r = run("clean-return", tmp_path, client)
    assert r["status"] == "ready" and not any(g["flagged"] for g in r["guard"])


def test_a_marked_copy_that_cannot_be_opened_is_left_out_and_said(tmp_path):
    client = Mut(FakeClocked(load_truth(CASES / "clean-return")), "compare",
                 lambda b: (b["pdf"][0]["marked"].update(document="aGVsbG8gd29ybGQ="), b)[1], "doc")
    r = run("clean-return", tmp_path, client)
    assert r["status"] == "ready" and r["documents"]["v1"]["marked_pdf"] is None and r["documents"]["v2"]["marked_pdf"]
    assert "could not be opened" in r["documents"]["v1"]["marked_error"]
    assert any(w["source"] == "Compare" for w in r["warnings"])
