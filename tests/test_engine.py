"""Our own logic around the API: the gate, cross-labelling, routing, status, failure and replay.

Clocked itself is replaced by tests/fake_clocked.py here. The real API's behaviour is
measured separately, on recorded live responses (tests/test_recordings.py, eval/).
Malformed answers have their own file (tests/test_engine_fuzz.py).
"""
import json
import re
from pathlib import Path

import pytest
from reportlab.platypus import PageBreak

from referee import prompts as P
from referee.engine import (HIDDEN_WORDS, batches, build_changes, build_ledger, clauses_to_compare, cross_label,
                            hunk_claims, passages, review, split_claims, split_note, xverify_claims)
from referee.gateway import Gateway
from referee.rules import Hunk
from referee.textdoc import Clause, read_pdf
from tests import layout_fixtures as lf
from tests.fake_clocked import FakeClocked, load_truth

CASES = Path(__file__).resolve().parents[1] / "data" / "cases"
EMPTY_TRUTH = {"substantive": [], "cosmetic": [], "letter_claims": []}


def run(case, tmp_path, gw=None, review_kw=None, **fake):
    d = CASES / case
    gw = gw or Gateway("live", client=FakeClocked(load_truth(d), **fake))
    return review(d / "v1.pdf", d / "v2.pdf", (d / "cover.txt").read_text(), gw, tmp_path / case, **(review_kw or {})), gw


class Spy:
    """The fake client, with every request kept: (endpoint, request)."""

    def __init__(self, inner):
        self.inner, self.sent = inner, []

    def __getattr__(self, name):
        real = getattr(self.inner, name)

        def call(request, idempotency_key=None, timeout=None):
            self.sent.append((name, request))
            return real(request, idempotency_key=idempotency_key, timeout=timeout)
        return call


def strings(obj, skip=("requests", "text", "quote", "query", "evidence", "passages", "skipped", "changed", "claims")):
    """Every sentence the application itself wrote into a review (not quotes of the documents or the playbook)."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if k not in skip:
                yield from strings(v, skip)
    elif isinstance(obj, list):
        for v in obj:
            yield from strings(v, skip)


# ------------------------------------------------------------ ground truth
@pytest.mark.parametrize("case", ["clean-return", "quiet-changes", "quiet-changes-injected", "disclosed-changes",
                                  "two-readers", "instruction-in-note", "added-clause", "wrong-file",
                                  "struct-01", "struct-02", "struct-03"])
def test_status_matches_ground_truth(case, tmp_path):
    r, _ = run(case, tmp_path)
    assert r["status"] == load_truth(CASES / case)["expected_status"]
    assert r["errors"] == []


# ------------------------------------------------------------------- gate
def test_the_gate_runs_first_and_a_wrong_file_stops_the_review(tmp_path):
    d = CASES / "wrong-file"
    fake = FakeClocked(load_truth(d))
    r, gw = run("wrong-file", tmp_path, gw=Gateway("live", client=fake))
    assert r["status"] == "blocked" and r["headline"] == "Not the same agreement"
    assert fake.calls == ["compare"] and r["audit"]["calls"] == 1          # nothing was sent after the gate
    assert len(r["reasons"]) == 1 and r["reasons"][0]["source"] == "Compare" and "unrelated" in r["reasons"][0]["text"]
    assert r["changes"] == [] and r["note"]["claims"] == [] and r["guard"] == [] and r["terms"] == []
    assert r["compare"]["gate"] == {**r["compare"]["gate"], "passed": False, "stopped": True}
    assert next(e for e in r["endpoints"] if e["name"] == "Compare")["decided"] == 1


def test_files_that_are_not_two_versions_of_one_agreement_are_not_reviewed_as_such(tmp_path):
    """The same pair that reads "ready" is stopped when Compare says the files are unrelated."""
    r, _ = run("clean-return", tmp_path, unrelated=True)
    assert r["status"] == "blocked" and r["headline"] == "Not the same agreement" and r["changes"] == []
    assert [x["source"] for x in r["reasons"]] == ["Compare"]


def test_a_related_score_under_the_floor_stops_the_review_too(tmp_path):
    class LowRelated(FakeClocked):
        def compare(self, request, idempotency_key=None, timeout=None):
            res = super().compare(request, idempotency_key, timeout)
            if "pdf" in request["documents"][0]:
                res["pairs"][0].update(relation="overlapping", related=P.SAME_AGREEMENT_FLOOR - 0.01)
            return res
    d = CASES / "clean-return"
    r, _ = run("clean-return", tmp_path, gw=Gateway("live", client=LowRelated(load_truth(d))))
    assert r["status"] == "blocked" and r["headline"] == "Not the same agreement" and r["audit"]["calls"] == 1


def test_a_gate_that_fails_does_not_stop_the_review_but_makes_it_incomplete(tmp_path):
    class GateDown(FakeClocked):
        def compare(self, request, idempotency_key=None, timeout=None):
            if "pdf" in request["documents"][0]:
                self.fail = {"compare"}
            try:
                return super().compare(request, idempotency_key, timeout)
            finally:
                self.fail = set()
    d = CASES / "quiet-changes"
    r, gw = run("quiet-changes", tmp_path, gw=Gateway("live", client=GateDown(load_truth(d))))
    assert r["compare"]["document"] is None and r["compare"]["gate"]["answered"] is False
    assert [e["step"] for e in r["errors"]] == ["compare v1 v2"]
    assert any(x["level"] == "incomplete" and x["source"] == "Compare" for x in r["reasons"])
    assert sum(c["blocking"] for c in r["changes"]) == 4 and r["note"]["ran"]          # the rest of the review went on
    assert r["status"] == "blocked"                                                   # the false statements still block
    r2, _ = run("clean-return", tmp_path, gw=Gateway("live", client=GateDown(load_truth(CASES / "clean-return"))))
    assert r2["status"] == "incomplete"                                               # and a clean pair is not "ready"


def test_a_passed_gate_is_not_shown_as_a_list_of_changes(tmp_path):
    r, _ = run("clean-return", tmp_path)
    gate = r["compare"]["gate"]
    assert gate["passed"] is True and gate["stopped"] is False and "relation only" in gate["note"]
    assert r["status"] == "ready" and not any(c["blocking"] for c in r["changes"])
    assert all(r["documents"][k]["layers"][0]["id"] == "compare" for k in ("v1", "v2"))    # kept, as a layer


# ------------------------------------------------- the readers, side by side
def test_verify_clears_a_faithful_rewording(tmp_path):
    r, _ = run("quiet-changes", tmp_path)
    reworded = [c for c in r["changes"] if c["cls"] == "reworded"]
    assert len(reworded) == 1 and not reworded[0]["blocking"] and reworded[0]["clause"] == "4.2"
    assert reworded[0]["verify"]["relation"] == "same"
    assert reworded[0]["readers"] == {"rules": "substantive", "verify": "same", "compare": "same", "agree": False}
    assert "only then" in reworded[0]["why"]                              # says why the rules were overruled
    pay = next(c for c in r["changes"] if c["clause"] == "4.2" and c["blocking"])
    assert pay["found_by"] == "both" and "90" in pay["summary"] and pay["found_by_label"] == "Rules + Verify + Compare"
    assert pay["readers"] == {"rules": "substantive", "verify": "changed", "compare": "conflict", "agree": True}


def test_a_rewording_is_still_cleared_when_compare_does_not_place_it(tmp_path):
    """The current rule for ordinary rewordings: Verify both ways, and no objection from Compare."""
    r, _ = run("quiet-changes", tmp_path, compare_silent={"reword_dispute"})
    c = next(c for c in r["changes"] if c["cls"] == "reworded")
    assert c["compare"]["relation"] == "silent" and c["readers"]["compare"] == "silent"
    assert "does not read a conflict" in c["why"]


def test_one_word_swap_between_real_words_is_held_by_the_rules_and_confirmed_by_the_readers(tmp_path):
    r, _ = run("disclosed-changes", tmp_path)
    c = next(c for c in r["changes"] if "minimum" in c["summary"])
    assert c["blocking"] and not c["promoted"] and c["readers"]["rules"] == "substantive"
    assert c["found_by"] == "both" and c["found_by_label"] == "Rules + Verify + Compare"


@pytest.mark.parametrize("fake,label", [({"compare_misses": {"min_max"}}, "Rules + Verify"),
                                        ({"verify_misses": {"min_max"}}, "Rules + Compare"),
                                        ({"verify_misses": {"min_max"}, "compare_silent": {"min_max"}}, "Rules")])
def test_one_word_swap_stays_held_unless_both_readers_say_same(fake, label, tmp_path):
    """One reader reading "same" is not enough, and Compare's silence is not consent."""
    r, _ = run("disclosed-changes", tmp_path, **fake)
    c = next(c for c in r["changes"] if "minimum" in c["summary"])
    assert c["blocking"] and c["cls"] == "substantive" and c["found_by_label"] == label
    assert "stricter reading wins" in c["why"] and c["readers"]["agree"] is False
    if "compare_silent" in fake:
        assert c["readers"] == {"rules": "substantive", "verify": "same", "compare": "silent", "agree": False}
        compare = next(e for e in r["endpoints"] if e["name"] == "Compare")
        assert compare["decided_detail"]["vetoed_clearing"] == [c["id"]]


def test_one_word_swap_is_cleared_only_when_verify_and_compare_both_read_the_same(tmp_path):
    r, _ = run("disclosed-changes", tmp_path, compare_misses={"min_max"}, verify_misses={"min_max"})
    c = next(c for c in r["changes"] if "minimum" in c["summary"])
    assert c["cls"] == "reworded" and not c["blocking"] and c["found_by_label"] == "Rules, cleared by Verify and Compare"
    assert c["readers"] == {"rules": "substantive", "verify": "same", "compare": "same", "agree": False}
    compare = next(e for e in r["endpoints"] if e["name"] == "Compare")
    assert compare["decided_detail"]["consented_to_clearing"] == [c["id"]]


# -- the rule for a one-word swap, on hand-made readings
def swap_hunk(**extra):
    return Hunk("5", "Service Levels", "substantive", "wording", "up to a maximum of 10%", "up to a minimum of 10%", "maximum", "minimum",
                "“maximum” → “minimum”", 1, 1, "“maximum” and “minimum” are both real words: a change of wording, not a spelling fix.",
                extra={"one_word_swap": ["maximum", "minimum"], **extra},
                v1_sentence="Credits are capped at a maximum of 10% of the monthly fee.",
                v2_sentence="Credits are capped at a minimum of 10% of the monthly fee.", v1_uid="c009", v2_uid="c009")


def reading(verdict="supported", p=0.97):
    return {"verdict": verdict, "supported": p if verdict == "supported" else 0.01,
            "contradicted": p if verdict == "contradicted" else 0.01, "not_stated": p if verdict == "not_stated" else 0.01}


def both_ways(h, verdict="supported", p=0.97):
    return {"v1": {h.v1_sentence: reading(verdict, p)} if h.v1_sentence else {}, "v2": {h.v2_sentence: reading(verdict, p)} if h.v2_sentence else {}}


def clause_answer(relation="same", probability=0.98, findings=()):
    return {"relation": relation, "probability": probability, "related": 0.99, "findings": list(findings)}


def conflict_on(h, confidence=0.95):
    return {"relation": "conflict", "kind": "value", "confidence": confidence,
            "v1": {"quote": h.v1_sentence, "start": 0, "end": len(h.v1_sentence), "aligned": True, "page": None},
            "v2": {"quote": h.v2_sentence, "start": 0, "end": len(h.v2_sentence), "aligned": True, "page": None}}


@pytest.mark.parametrize("compare,cleared", [
    (clause_answer("same", 0.98), True),                 # Compare reads the passage as the same, outright
    (clause_answer("same", 0.55), False),                # not sure enough: silent
    (clause_answer("overlapping", 0.9), False),          # silent
    ({"failed": True}, False),                           # the call failed
    (None, False),                                       # Compare was never asked
])
def test_one_word_swap_needs_compare_to_say_same_outright(compare, cleared):
    h = swap_hunk()
    ccmp = {} if compare is None else {("c009", "c009"): compare}
    it = cross_label([h], ccmp, both_ways(h))[0]
    assert (it["cls"] == "reworded") is cleared
    if not cleared:
        assert it["cls"] == "substantive" and it["readers"]["verify"] == "same" and it["readers"]["compare"] != "same"


def test_one_word_swap_needs_verify_both_ways_at_the_floor():
    h = swap_hunk()
    ccmp = {("c009", "c009"): clause_answer("same", 0.98)}
    assert cross_label([h], ccmp, both_ways(h, p=P.MEANING_SAME_FLOOR))[0]["cls"] == "reworded"
    assert cross_label([h], ccmp, both_ways(h, p=P.MEANING_SAME_FLOOR - 0.01))[0]["cls"] == "substantive"
    one_way = {"v1": {}, "v2": {h.v2_sentence: reading()}}
    assert cross_label([h], ccmp, one_way)[0]["cls"] == "substantive"


def test_a_rewritten_passage_that_carries_a_value_is_never_cleared():
    h = swap_hunk(value_kinds=["percent"])
    h.extra.pop("one_word_swap")
    it = cross_label([h], {("c009", "c009"): clause_answer("same", 0.99)}, both_ways(h))[0]
    assert it["cls"] == "substantive" and "never cleared" in it["why"]


def test_a_sign_or_currency_that_changed_is_never_cleared():
    h = Hunk("5", "Service Levels", "substantive", "symbol", "at least ≥ 99.9%", "at least ≤ 99.9%", "≥", "≤", "“≥” → “≤”", 1, 1,
             "A comparison, sign or currency symbol differs between the two versions.",
             v1_sentence="Availability is ≥ 99.9% each month.", v2_sentence="Availability is ≤ 99.9% each month.", v1_uid="c009", v2_uid="c009")
    it = cross_label([h], {("c009", "c009"): clause_answer("same", 0.99)}, both_ways(h))[0]
    assert it["cls"] == "substantive" and it["found_by"] == "rules" and "never cleared" in it["why"] and it["readers"]["agree"] is False


def test_a_change_whose_sentence_was_too_long_to_send_is_marked_not_read():
    h = swap_hunk()
    it = cross_label([h], {}, {"v1": {}, "v2": {}}, not_read={h.v2_sentence})[0]
    assert it["cls"] == "substantive" and it["verify"]["not_read"] is True and "not cut" in it["verify"]["note"]
    it = cross_label([h], {("c009", "c009"): {"not_read": True}}, both_ways(h))[0]
    assert it["cls"] == "substantive" and it["compare"]["relation"] == "not_read" and it["readers"]["compare"] == "not_asked"


# -- a spelling fix that is not one
def typo_hunk():
    """"asign" is no word and "align" is, one letter apart: a spelling fix to the rules. The sentence meant "assign"."""
    return Hunk("13", "Assignment", "spelling", "typo", "may asign this Agreement", "may align this Agreement", "asign", "align",
                "“asign” → “align”", 2, 2, "One word, same first letter, at most two letters apart: treated as a spelling fix. Compare can overrule this.",
                v1_sentence="Neither party may asign this Agreement without consent.",
                v2_sentence="Neither party may align this Agreement without consent.", v1_uid="c017", v2_uid="c017")


def test_the_rule_layer_takes_a_misspelt_word_fixed_into_the_wrong_word_for_a_spelling_fix():
    from referee.rules import _classify
    ca = Clause("13", "Assignment", "Neither party may asign this Agreement without the prior written consent of the other party.", 2, "c017")
    cb = Clause("13", "Assignment", "Neither party may align this Agreement without the prior written consent of the other party.", 2, "c017")
    h = _classify(ca, cb, "may asign this Agreement", "may align this Agreement", "asign", "align", True)
    assert (h.cls, h.kind) == ("spelling", "typo")


def test_verify_overrules_a_spelling_fix_that_changes_meaning():
    h = typo_hunk()
    it = cross_label([h], {("c017", "c017"): clause_answer("same", 0.98)}, both_ways(h, "contradicted"))[0]
    assert it["cls"] == "substantive" and it["promoted"] and it["found_by"] == "clocked" and it["confirmed_by"] == ["Verify"]
    assert it["decided"] == {"verify": "held_alone"} and it["readers"]["agree"] is False
    assert it["why"].startswith("The rule layer took this for a spelling fix. Verify reads") and "stricter reading wins" in it["why"]


def test_compare_alone_can_also_overrule_a_spelling_fix():
    h = typo_hunk()
    ccmp = {("c017", "c017"): clause_answer("conflicting", 0.97, [conflict_on(h)])}
    it = cross_label([h], ccmp, both_ways(h, "supported"))[0]
    assert it["cls"] == "substantive" and it["promoted"] and it["confirmed_by"] == ["Compare"] and it["decided"] == {"compare": "held_alone"}


def test_with_neither_reader_objecting_a_spelling_fix_stays_one():
    h = typo_hunk()
    it = cross_label([h], {("c017", "c017"): clause_answer("same", 0.98)}, both_ways(h, "supported"))[0]
    assert it["cls"] == "spelling" and not it["promoted"] and it["found_by"] == "rules" and it["readers"]["agree"] is True
    assert "Compare can overrule" not in it["why"]


def test_a_weak_conflict_does_not_overrule_the_rules():
    h = typo_hunk()
    ccmp = {("c017", "c017"): clause_answer("overlapping", 0.5, [conflict_on(h, P.CONFLICT_CONFIDENCE_FLOOR - 0.05)])}
    assert cross_label([h], ccmp, both_ways(h, "supported"))[0]["cls"] == "spelling"


def test_a_promoted_spelling_fix_ends_up_as_an_open_change_found_by_clocked(tmp_path):
    v1, v2 = read_pdf(CASES / "clean-return" / "v1.pdf"), read_pdf(CASES / "clean-return" / "v2.pdf")
    h = typo_hunk()
    changes = build_changes(cross_label([h], {}, both_ways(h, "contradicted")), v1, v2)
    assert [(c["blocking"], c["promoted"], c["found_by"], c["found_by_label"]) for c in changes] == [(True, True, "clocked", "Verify")]


def test_a_typo_next_to_a_real_change_is_not_promoted_and_is_shown_with_that_change(tmp_path):
    r, _ = run("quiet-changes", tmp_path)
    typo = next(c for c in r["changes"] if c["clause"] == "3" and c["cls"] == "spelling")
    held = next(c for c in r["changes"] if c["clause"] == "3" and c["blocking"])
    assert not typo["blocking"] and typo["same_sentence_as"] == held["id"] and typo["parts"][0]["same_sentence_as"] == held["id"]
    extra = [p for p in held["parts"] if p.get("same_sentence")]
    assert len(extra) == 1 and extra[0]["summary"] == "“writen” → “written”" and extra[0]["from"] == typo["id"]
    assert not held["parts"][0].get("same_sentence")                       # the change itself stays first
    assert typo["readers"]["verify"] == "changed" and held["id"] in typo["why"] and "whole sentences" in typo["why"]
    other = next(c for c in r["changes"] if c["clause"] == "5" and c["cls"] == "spelling")
    assert other["same_sentence_as"] is None and all(not p.get("same_sentence") for c in r["changes"] if c["clause"] != "3" for p in c["parts"])
    ledger, _ = build_ledger(r["changes"])
    assert ledger.count("“writen” → “written”") == 1                       # said once to Verify, not twice


def test_when_compare_and_verify_disagree_the_change_stays_open(tmp_path):
    r, _ = run("quiet-changes", tmp_path, compare_misses={"liability_cap"})
    c = next(c for c in r["changes"] if c["clause"] == "9")
    assert c["blocking"] and c["compare"]["relation"] == "same" and c["verify"]["relation"] == "changed"
    assert c["found_by_label"] == "Rules + Verify"
    assert c["readers"] == {"rules": "substantive", "verify": "changed", "compare": "same", "agree": False}
    assert "Verify reads a change, Compare reads the same meaning. The stricter reading wins" in c["why"]


def test_compare_reads_only_the_clauses_in_question(tmp_path):
    r, gw = run("quiet-changes", tmp_path)
    asked = sorted(a["step"] for a in gw.audit if a["step"].startswith("compare clause"))
    assert asked == sorted(f"compare clause {n}" for n in ("3", "4.2", "5", "9", "15"))
    assert r["compare"]["clauses_asked"] == 5 and r["compare"]["document"]["relation"] == "conflicting"


def test_formatting_and_addresses_are_settled_by_the_rules_and_say_so(tmp_path):
    r, _ = run("quiet-changes", tmp_path)
    settled = [c for c in r["changes"] if c["cls"] in ("formatting", "administrative")]
    assert len(settled) == 2
    for c in settled:
        assert c["compare"] is None and c["verify"] is None
        assert c["readers"] == {"rules": c["cls"], "verify": "not_asked", "compare": "not_asked", "agree": True}
        assert c["why"].endswith("not sent to the API.") and c["found_by_label"] == "Rules"


def test_compare_sentences_are_placed_on_both_pages(tmp_path):
    r, _ = run("quiet-changes", tmp_path)
    pay = next(c for c in r["changes"] if c["clause"] == "4.2" and c["blocking"])
    assert pay["compare"]["relation"] == "conflict" and pay["compare"]["v1"]["rects"] and pay["compare"]["v2"]["rects"]


def test_a_number_that_moved_is_never_cleared_even_if_both_readers_miss_it(tmp_path):
    r, _ = run("quiet-changes", tmp_path, compare_misses={"pay_terms"}, verify_misses={"pay_terms"})
    pay = next(c for c in r["changes"] if c["clause"] == "4.2" and "90" in c["summary"])
    assert pay["blocking"] and pay["found_by"] == "rules" and pay["found_by_label"] == "Rules"
    assert pay["readers"]["agree"] is False and "never cleared" in pay["why"]


def test_compare_vetoes_clearing_a_rewording_it_reads_as_a_conflict(tmp_path):
    r, _ = run("quiet-changes-injected", tmp_path, verify_misses={"ip_ownership"})
    c = next(c for c in r["changes"] if c["clause"] == "7")
    assert c["blocking"] and c["cls"] == "substantive" and c["summary"] == "Clause rewritten"
    assert c["readers"] == {"rules": "substantive", "verify": "same", "compare": "conflict", "agree": False}
    assert "Compare reads a change, Verify reads the same meaning. The stricter reading wins" in c["why"]
    compare = next(e for e in r["endpoints"] if e["name"] == "Compare")
    assert c["id"] in compare["decided_detail"]["vetoed_clearing"] and compare["decided"] >= 1


def test_two_readers_one_disagreement_takes_the_stricter_reading(tmp_path):
    """The duty that became an option: Verify reads "same", Compare reads a conflict. It stays open."""
    r, _ = run("two-readers", tmp_path, verify_misses={"hard-b-13"})
    c = next(c for c in r["changes"] if c["clause"] == "10")
    assert c["blocking"] and c["summary"] == "“will” → “may”" and c["found_by_label"] == "Rules + Compare"
    assert c["readers"]["verify"] == "same" and c["readers"]["compare"] == "conflict" and not c["readers"]["agree"]
    assert sorted(x["clause"] for x in r["changes"] if x["cls"] == "reworded") == ["6", "8"]


# --------------------------------------------------- identity of a clause
def test_clauses_are_keyed_by_uid_so_a_repeated_number_keeps_its_own_text():
    def clause(uid, number, text):
        return Clause(number, "Fees", text, 1, uid)

    def hunk(uid):
        return Hunk("1", "Fees", "substantive", "money", "$5,000", "$9,000", "$5,000", "$9,000", "$5,000 → $9,000", 1, 1, "",
                    v1_sentence=f"fee {uid} is $5,000", v2_sentence=f"fee {uid} is $9,000", v1_uid=uid, v2_uid=uid)
    view = {u: clause(u, "1", f"The fee in part {u} is $5,000.") for u in ("c002", "c010")}
    pairs = clauses_to_compare([hunk("c002"), hunk("c010")], view, view)
    assert list(pairs) == [("c002", "c002"), ("c010", "c010")] and pairs[("c010", "c010")]["v1"] == "The fee in part c010 is $5,000."
    labelled = cross_label([hunk("c002"), hunk("c010")], {("c002", "c002"): clause_answer("same", 0.99)}, None)
    assert [it["readers"]["compare"] for it in labelled] == ["same", "not_asked"]      # one answer does not serve both


def test_an_appendix_that_restarts_its_numbering_is_compared_as_itself(tmp_path):
    body, sched = lf.BASE, [("1", "Fees Schedule", "The fee for the Standard tier is $5,000 per month."),
                            ("2", "Support Hours", "Support is available from 9 a.m. to 5 p.m. Eastern Time on business days.")]

    def flow(b, s):
        return lf.clauses_flow(b, "{n}. {t}") + [PageBreak(), lf.P("SCHEDULE A", lf.HEAD)] + lf.clauses_flow(s, "{n}. {t}")
    v1 = lf.build(tmp_path / "v1.pdf", flow(body, sched))
    v2 = lf.build(tmp_path / "v2.pdf", flow(lf.edit(body, "1", "Provider may update", "Provider must not update"),
                                            lf.edit(sched, "1", "$5,000", "$9,000")))
    truth = {**EMPTY_TRUTH, "substantive": [
        {"id": "body", "clause": "1", "kind": "wording", "v1_quote": "Provider may update the Service", "v2_quote": "Provider must not update the Service"},
        {"id": "fee", "clause": "Schedule A 1", "kind": "money", "v1_quote": "is $5,000 per month", "v2_quote": "is $9,000 per month"}]}
    spy = Spy(FakeClocked(truth))
    r = review(v1, v2, "", Gateway("live", client=spy), tmp_path / "out")
    held = [c for c in r["changes"] if c["blocking"]]
    assert [(c["clause"], c["title"]) for c in held] == [("1", "Services"), ("Schedule A 1", "Fees Schedule")]
    assert held[0]["uid"] != held[1]["uid"] and all(c["uid"]["v1"] and c["uid"]["v2"] for c in held)
    clause_texts = [q["documents"][1]["text"] for name, q in spy.sent if name == "compare" and "text" in q["documents"][0]]
    assert len(clause_texts) == 2 and "must not update" in clause_texts[0] and "$9,000" in clause_texts[1]
    assert [c["uid"] for c in r["compare"]["clauses"]] == [[c["uid"]["v1"], c["uid"]["v2"]] for c in held]
    assert all(c["route"] and c["decide"] for c in held)                   # each routed on its own


def test_a_renumbered_clause_shows_both_numbers_and_is_only_formatting(tmp_path):
    r, _ = run("added-clause", tmp_path)
    ren = next(c for c in r["changes"] if c["kind"] == "renumbered")
    assert ren["cls"] == "formatting" and not ren["blocking"] and ren["summary"].startswith("Renumbered 14 → 15")
    assert (ren["clause_v1"], ren["clause_v2"], ren["clause_label"]) == ("14", "15", "14 → 15")
    assert [(x["v1"], x["v2"]) for x in ren["parts"][0]["renumbered"]] == [("14", "15"), ("15", "16"), ("16", "17")]
    notices = next(c for c in r["changes"] if c["cls"] == "administrative")
    assert notices["clause"] == "15" and notices["clause_label"] == "14 → 15" and notices["summary"] == "Notice address updated"
    ledger, _ = build_ledger(r["changes"], read_pdf(CASES / "added-clause" / "v2.pdf"))
    assert "Clause 15 (Notices) was numbered 14 in the agreement we sent." in ledger
    assert "Clause 16 (Governing Law) was numbered 15 in the agreement we sent. Apart from its number it reads exactly" in ledger
    assert "Clause 13 (Assignment) is unchanged" in ledger and "Clause 16 (Governing Law) is unchanged" not in ledger


# ------------------------------------------------ added and removed clauses
def test_an_added_clause_is_a_blocking_change_with_the_whole_clause_as_its_quote(tmp_path):
    r, gw = run("added-clause", tmp_path)
    truth = load_truth(CASES / "added-clause")["substantive"][0]
    c = next(c for c in r["changes"] if c["blocking"])
    assert (c["kind"], c["clause"], c["title"], c["summary"]) == ("added", "14", "Most Favoured Customer", "Clause added")
    assert c["uid"]["v1"] is None and c["uid"]["v2"] and c["clause_v1"] is None
    part = c["parts"][0]
    assert part["v2"]["quote"] == truth["v2_quote"] and part["v2"]["located"] and part["v2"]["rects"]      # the whole clause
    assert part["v1"] == {"quote": "", "changed": "", "page": None, "rects": [], "located": False, "absent": True}
    assert c["verify"]["relation"] == "changed" and c["verify"]["returned_against_sent"]["verdict"] == "not_stated"
    assert c["verify"]["sent_against_returned"] is None and c["compare"] is None and c["found_by_label"] == "Rules + Verify"
    assert "added a clause" in c["playbook"]["query"] and c["decide"] and c["route"] == "escalate"          # routed like any change
    assert sum(a["step"] == "rerank 14" for a in gw.audit) == 1 and sum(a["step"] == "decide 14" for a in gw.audit) == 1
    false = [x for x in r["note"]["claims"] if x["verdict"] == "contradicted"]
    assert "No clause was added or removed." in [x["text"] for x in false] and r["status"] == "blocked"
    assert c["id"] in next(x for x in false if x["text"].startswith("No clause"))["change_ids"]


def test_a_removed_clause_is_a_blocking_change_quoted_from_the_version_we_sent(tmp_path):
    r, _ = run("struct-02", tmp_path)
    c = next(c for c in r["changes"] if c["kind"] == "removed")
    assert c["blocking"] and c["clause"] == "4.3" and c["title"] == "Late Payment" and c["uid"]["v2"] is None
    assert c["parts"][0]["v2"]["absent"] and c["parts"][0]["v1"]["quote"].startswith("Overdue amounts accrue interest")
    assert c["verify"]["sent_against_returned"]["verdict"] == "not_stated" and "removed" in c["playbook"]["query"]
    ledger, _ = build_ledger(r["changes"])
    assert "clause 4.3 (Late Payment), removed" in ledger.split("\n")[0]
    assert "Substantive change: clause 4.3 (Late Payment) of the agreement we sent was removed." in ledger
    ren = next(c for c in r["changes"] if c["kind"] == "renumbered")
    assert ren["clause_label"] == "4.4 → 4.3" and not ren["blocking"]


def test_one_added_and_one_removed_clause_are_two_changes(tmp_path):
    r, _ = run("struct-03", tmp_path)
    assert sorted((c["kind"], c["clause"]) for c in r["changes"] if c["blocking"]) == [("added", "16"), ("duration", "9"), ("removed", "12")]


def test_a_clause_verify_finds_elsewhere_in_the_other_version_is_listed_as_moved(tmp_path):
    """The rule layer saw a clause only in v2. Verify reads v1 as stating the same obligation: moved, not added."""
    r, _ = run("added-clause", tmp_path, verify_misses={"added_mfc"})
    c = next(c for c in r["changes"] if c["clause"] == "14" and c["cls"] == "reworded")
    assert not c["blocking"] and c["kind"] == "moved" and c["summary"] == "Clause moved, same meaning"
    assert c["found_by_label"] == "Rules, cleared by Verify" and "listed as moved" in c["why"] and c["route"] is None
    assert not any(x["blocking"] for x in r["changes"])


def added_hunk(text, capped=False):
    return Hunk("14", "Most Favoured Customer", "substantive", "added", "", text[:400], "", text[:400], "Clause added", 0, 2,
                "This clause exists only in the returned version.", v2_sentence=text, v2_uid="c018", capped=capped)


def test_an_added_clause_with_an_amount_the_other_version_never_states_is_not_cleared_on_verify_alone():
    text = "Provider will refund 40% of the fees for the preceding twelve (12) months."
    h = added_hunk(text)
    supported = {"v1": {}, "v2": {text: reading("supported")}}
    assert cross_label([h], {}, supported, sources=("Fees are due within twelve (12) months. A 40% refund applies.", ""))[0]["cls"] == "reworded"
    held = cross_label([h], {}, supported, sources=("Fees are due within twelve (12) months.", ""))[0]
    assert held["cls"] == "substantive" and held["readers"] == {"rules": "substantive", "verify": "same", "compare": "not_asked", "agree": False}
    unclear = cross_label([h], {}, {"v1": {}, "v2": {text: reading("supported", 0.5)}})[0]
    assert unclear["cls"] == "substantive" and unclear["readers"]["verify"] == "unclear"


def test_a_long_added_clause_goes_to_verify_whole_in_passages():
    sentences_ = [f"Obligation number {i} is that Provider will deliver report {i} to Customer within {i + 3} days of each request." for i in range(40)]
    text = " ".join(sentences_)
    first = passages(text)[0]
    h = added_hunk(first, capped=True)                      # the rule layer kept the opening only
    view = {"c018": Clause("14", "Most Favoured Customer", text, 2, "c018")}
    _, c2 = hunk_claims(h, {}, view)
    assert len(c2) > 2 and " ".join(c2) == text and all(len(c) <= 1300 for c in c2)        # all of it, nothing cut
    assert xverify_claims([h], {}, view)[1] == c2
    some = {"v1": {}, "v2": {c: reading("supported") for c in c2[:-1]}}                      # the last passage has no reading
    it = cross_label([h], {}, some, claims={id(h): ([], c2)})[0]
    assert it["cls"] == "substantive" and it["verify"]["partial"] is True
    every = {"v1": {}, "v2": {c: reading("supported") for c in c2}}
    assert cross_label([h], {}, every, claims={id(h): ([], c2)})[0]["cls"] == "reworded"


# ------------------------------------------------------ nothing is cut short
def test_claims_go_out_thirty_at_a_time_and_none_is_dropped():
    claims = [f"Sentence number {i} of the returned copy." for i in range(65)]
    groups, too_long = batches(claims)
    assert [len(g) for g in groups] == [30, 30, 5] and sum(groups, []) == claims and too_long == []
    assert batches(claims[:30]) == ([claims[:30]], [])                    # thirty or fewer: one request, as written
    by_size, _ = batches(["x" * 9000] * 5)
    assert [len(g) for g in by_size] == [2, 2, 1]                          # the answer may hold 20,000 characters
    assert batches(["short", "y" * 20001]) == ([["short"]], ["y" * 20001])


def test_every_changed_sentence_is_sent_whole_however_many_and_however_long():
    def hunk(i, n=80):
        s = f"Sentence {i} " + "word " * n
        return Hunk(str(i), "T", "substantive", "wording", "a", "b", "a", "b", "Wording changed", 1, 1, "",
                    v1_sentence=s + "old.", v2_sentence=s + "new.", v1_uid=f"c{i:03d}", v2_uid=f"c{i:03d}")
    hunks = [hunk(i) for i in range(41)] + [hunk(99, 700)]
    s1, s2 = xverify_claims(hunks)
    assert len(s1) == len(s2) == 42 and len(s2[-1]) > 3500 and s2[-1].endswith("new.")
    assert [len(g) for g in batches(s2)[0]] == [30, 12]


def test_a_cover_note_with_more_than_thirty_statements_is_checked_in_full(tmp_path):
    d = CASES / "clean-return"
    note = "Hi Dana,\n\n" + " ".join(f"Clause {i} was checked and is fine." for i in range(1, 46)) + "\n\nThanks,\nPriya"
    spy = Spy(FakeClocked(load_truth(d)))
    gw = Gateway("live", client=spy)
    r = review(d / "v1.pdf", d / "v2.pdf", note, gw, tmp_path)
    sent = [q for name, q in spy.sent if name == "grounding" and "documents" in q]
    assert sorted(len(q["claims"]) for q in sent) == [15, 30] and all(q["answer"] == " ".join(q["claims"]) for q in sent)
    assert sorted(a["step"] for a in gw.audit if a["step"].startswith("verify cover note")) == ["verify cover note (1 of 2)", "verify cover note (2 of 2)"]
    assert r["note"]["statements"] == r["note"]["checked"] == len(r["note"]["claims"]) == 45
    assert all(c["verdict"] != "not_checked" for c in r["note"]["claims"]) and r["errors"] == []


def test_a_statement_too_long_for_one_request_is_reported_not_cut(tmp_path):
    d = CASES / "clean-return"
    long = "We confirm that " + "nothing at all " * 1500 + "changed."
    r = review(d / "v1.pdf", d / "v2.pdf", f"We fixed typos.\n\n{long}", Gateway("live", client=FakeClocked(load_truth(d))), tmp_path)
    assert r["status"] == "incomplete" and any(e["kind"] == "too_long" and "not cut" in e["message"] for e in r["errors"])
    verdicts = {c["text"][:15]: c["verdict"] for c in r["note"]["claims"]}
    assert verdicts["We fixed typos."] != "not_checked" and verdicts["We confirm that"] == "not_checked"


def test_a_sentence_too_long_for_verify_is_not_sent_not_cut_and_its_change_is_marked_not_read(tmp_path, monkeypatch):
    from referee import engine
    monkeypatch.setattr(engine, "VERIFY_ANSWER_CHARS", 150)                 # the limit is 20,000; shrunk so a case trips it
    d = CASES / "quiet-changes"
    spy = Spy(FakeClocked(load_truth(d)))
    r = review(d / "v1.pdf", d / "v2.pdf", "", Gateway("live", client=spy), tmp_path)
    sent = [c for name, q in spy.sent if name == "grounding" for c in q["claims"]]
    assert sent and all(len(c) <= 150 for c in sent) and all(len(q["answer"]) <= 150 for name, q in spy.sent if name == "grounding")
    long = [e for e in r["errors"] if e["kind"] == "too_long"]
    assert long and all(e["endpoint"] == "Verify" and "not cut" in e["message"] for e in long)
    unread = [c for c in r["changes"] if c["not_read"]]
    assert unread and all(c["blocking"] or c["cls"] == "spelling" for c in unread) and all(c["verify"]["not_read"] for c in unread)
    assert not [c for c in r["changes"] if c["cls"] == "reworded" and c["not_read"]]       # nothing unread is cleared
    assert any(x["level"] == "incomplete" for x in r["reasons"]) and r["status"] != "changes"


def test_a_clause_too_long_for_compare_is_not_sent_not_cut_and_said(tmp_path, monkeypatch):
    from referee import engine
    monkeypatch.setattr(engine, "SOURCE_CHARS", 300)                        # the limit is 200,000 characters
    d = CASES / "quiet-changes"
    spy = Spy(FakeClocked(load_truth(d)))
    r = review(d / "v1.pdf", d / "v2.pdf", "We fixed typos.", Gateway("live", client=spy), tmp_path)
    clause_texts = [q["documents"] for name, q in spy.sent if name == "compare" and "text" in q["documents"][0]]
    assert all(len(a["text"]) + len(b["text"]) <= 300 for a, b in clause_texts)
    assert r["compare"]["clauses_not_read"] >= 1 and r["compare"]["clauses_not_read"] + len(clause_texts) == r["compare"]["clauses_asked"]
    marked = [c for c in r["changes"] if (c["compare"] or {}).get("relation") == "not_read"]
    assert marked and all(c["not_read"] and c["readers"]["compare"] == "not_asked" for c in marked)
    assert sum(e["kind"] == "too_long" and e["endpoint"] == "Compare" for e in r["errors"]) == r["compare"]["clauses_not_read"]
    assert any(e["kind"] == "too_long" and e["message"].startswith("Verify (cover note)") for e in r["errors"])     # the note's sources too
    assert r["note"]["claims"][0]["verdict"] == "not_checked" and r["status"] == "incomplete"


# ------------------------------------------------- one route for each change
def test_two_separate_changes_in_one_clause_get_a_card_and_a_route_each(tmp_path):
    """Clause 4.2 of hard-a: thirty days became thirty business days, and "before the due date" became "after"."""
    r, gw = run("hard-a", tmp_path)
    cards = [c for c in r["changes"] if c["clause"] == "4.2" and c["blocking"]]
    assert sorted(c["summary"] for c in cards) == ["30 days → 30 business days", "“before” → “after”"]
    assert cards[0]["uid"] == cards[1]["uid"] and cards[0]["id"] != cards[1]["id"]
    assert all(c["playbook"] and c["decide"] and c["route"] for c in cards)
    assert cards[0]["playbook"]["query"] != cards[1]["playbook"]["query"]
    assert sum(a["step"] == "rerank 4.2" for a in gw.audit) == 2 and sum(a["step"] == "decide 4.2" for a in gw.audit) == 2
    assert sum(a["step"] == "compare clause 4.2" for a in gw.audit) == 1               # the clause is still read once
    first = build_ledger(r["changes"])[0].split("\n")[0]
    assert first.count("clause 4.2 (Payment Terms)") == 1                              # named once in the summary line


def test_the_same_replacement_in_two_sentences_of_a_clause_is_one_change(tmp_path):
    """Delaware became Oregon in the governing-law sentence and in the venue sentence: one change, one route."""
    r, gw = run("quiet-changes", tmp_path)
    law = [c for c in r["changes"] if c["clause"] == "15"]
    assert len(law) == 1 and len(law[0]["parts"]) == 2 and sum(a["step"] == "decide 15" for a in gw.audit) == 1


# -------------------------------------------------------------- cover note
def test_contradicted_cover_note_blocks_and_links_to_the_change(tmp_path):
    r, _ = run("quiet-changes", tmp_path)
    bad = [c for c in r["note"]["claims"] if c["verdict"] == "contradicted"]
    assert len(bad) == 3 and r["status"] == "blocked"
    pay = next(c for c in bad if "Payment terms" in c["text"])
    target = next(c for c in r["changes"] if c["id"] in pay["change_ids"])
    assert target["clause"] == "4.2" and pay["evidence"]["aligned"] is True
    assert any(x["text"] == "3 statements in the cover note are contradicted by the documents." for x in r["reasons"])


def test_true_statements_about_untouched_terms_are_supported(tmp_path):
    r, _ = run("quiet-changes", tmp_path)
    fee = next(c for c in r["note"]["claims"] if "annual subscription fee" in c["text"])
    assert fee["verdict"] == "supported"


def test_claims_skip_greetings_questions_and_sign_offs():
    claims = split_claims("Hi Dana,\n\nWe fixed typos. Nothing else changed.\n\nCan you sign by Friday?\n\nThanks,\nPriya")
    assert claims == ["We fixed typos.", "Nothing else changed."]
    n = split_note("Hi Dana,\n\nWe fixed typos. Nothing else changed.\n\nCan you sign by Friday?\n\nThanks,\nPriya")
    assert [s["why"] for s in n["skipped"]] == ["salutation", "sign-off", "signature", "question"] and n["instructions"] == []


@pytest.mark.parametrize("note,claims", [
    ("Hi Dana, no terms were changed at all.", ["no terms were changed at all."]),                       # a paragraph that opens with Hi
    ("We confirm that no commercial terms were changed", ["We confirm that no commercial terms were changed"]),   # no full stop
    ("Hello team - no changes.", ["Hello team - no changes."]),
    ("Brightwater Logistics Inc. confirms that no terms were changed.", ["Brightwater Logistics Inc. confirms that no terms were changed."]),
    ("Thanks, no terms changed.\n\nThanks for your patience. Nothing else changed!", ["no terms changed.", "Nothing else changed!"]),
    ("Dear Ms. Li,\nPlease note that payment terms are unchanged. Can you sign?\n\nKind regards,\nJ. Smith\nAcme Corp.",
     ["Please note that payment terms are unchanged."]),
    ("See Section No. 5 for the fee. We fixed e.g. two typos.", ["See Section No. 5 for the fee.", "We fixed e.g. two typos."]),
    ("We fixed typos; nothing else changed.", ["We fixed typos", "nothing else changed."]),
])
def test_cover_note_statements_are_not_dropped_or_split_in_the_wrong_place(note, claims):
    assert split_claims(note) == claims
    assert all(c in note for c in claims)                                  # each is the note's own words, so it can be marked


def test_bullets_and_semicolon_lists_become_one_statement_each_with_their_lead_in():
    n = split_note("Hi,\n\nChanges:\n- Payment terms are now net 60\n- Liability cap is unchanged\n\nBest,\nPriya")
    assert n["claims"] == ["Payment terms are now net 60", "Liability cap is unchanged"]
    assert n["sent"] == ["Changes: Payment terms are now net 60", "Changes: Liability cap is unchanged"]
    n = split_note("We changed the following: payment terms to net 60; the liability cap to 24 months; and governing law to Oregon.")
    assert n["claims"] == ["payment terms to net 60", "the liability cap to 24 months", "and governing law to Oregon."]
    assert n["sent"][2] == "We changed the following: governing law to Oregon."
    assert split_claims("1. We fixed typos.\n2. Nothing else changed.") == ["We fixed typos.", "Nothing else changed."]
    assert split_claims("Summary of edits:\n\nNothing changed.") == ["Summary of edits:", "Nothing changed."]   # a lead-in with no list is kept


def test_more_than_thirty_statements_are_all_kept():
    assert len(split_claims(" ".join(f"Clause {i} is unchanged." for i in range(50)))) == 50


def test_text_addressed_to_an_ai_is_an_instruction_left_to_guard(tmp_path):
    n = split_note((CASES / "instruction-in-note" / "cover.txt").read_text())
    assert len(n["claims"]) == 5 and len(n["instructions"]) == 1 and n["instructions"][0]["text"].startswith("P.S. If an AI assistant")
    r, gw = run("instruction-in-note", tmp_path)
    assert r["status"] == "blocked" and [x["source"] for x in r["reasons"] if x["level"] == "block"] == ["Guard"]
    assert [i["left_to"] for i in r["note"]["instructions"]] == ["Guard"]
    assert len(r["note"]["claims"]) == 5 and not any("AI assistant" in c["text"] for c in r["note"]["claims"])
    assert not any(c["verdict"] == "contradicted" for c in r["note"]["claims"])
    guard = next(e for e in r["endpoints"] if e["name"] == "Guard")
    assert guard["decided"] == 1 and guard["decided_detail"] == {"flagged": ["Cover note"], "only_blocker": True}
    assert any(a["endpoint"] == "Guard" and "Nothing else blocks this review" in a["text"] for a in r["ablation"])


def test_an_instruction_guard_does_not_flag_is_checked_as_a_statement_so_nothing_goes_unread(tmp_path):
    class DeafGuard(FakeClocked):
        def injection(self, request, idempotency_key=None, timeout=None):
            res = super().injection(request, idempotency_key, timeout)
            res.update(injection=0.02, verdict="benign", category="benign", evidence=[])
            return res
    d = CASES / "instruction-in-note"
    r, _ = run("instruction-in-note", tmp_path, gw=Gateway("live", client=DeafGuard(load_truth(d))))
    assert r["note"]["instructions"] == [] and len(r["note"]["claims"]) == 6
    last = r["note"]["claims"][-1]
    assert last["kind"] == "instruction" and "AI assistant" in last["text"] and last["verdict"] != "not_checked"


def test_a_note_with_nothing_to_check_is_said_not_passed_over(tmp_path):
    d = CASES / "clean-return"
    r = review(d / "v1.pdf", d / "v2.pdf", "Hi Dana,\n\nCan you sign by Friday?\n\nThanks,\nPriya", Gateway("live", client=FakeClocked(load_truth(d))), tmp_path)
    assert r["note"]["statements"] == 0 and any(w["text"] == "The cover note holds no statement to check." for w in r["warnings"])
    ok = next(x for x in r["reasons"] if x["level"] == "ok")
    assert r["status"] == "ready" and "cover note" not in ok["text"]        # "ready" does not claim the note held up


# ------------------------------------------------------- playbook and route
def test_no_playbook_guidance_forces_escalation(tmp_path):
    r, _ = run("quiet-changes-injected", tmp_path)
    ip = next(c for c in r["changes"] if c["clause"] == "7")
    assert ip["playbook"]["status"] == "none" and ip["route"] == "escalate" and "Legal" in ip["route_reason"]


def test_rerank_expand_fetches_the_neighbouring_passage(tmp_path):
    r, gw = run("quiet-changes", tmp_path)
    law = next(c for c in r["changes"] if c["clause"] == "15")
    assert law["playbook"]["expanded"] and law["playbook"]["passages"][0]["id"] == "law-other"
    assert any(a["step"] == "rerank 15 expand" for a in gw.audit)


def test_expand_is_only_claimed_in_a_review_where_it_happened(tmp_path):
    said = lambda r: "asked a second time" in next(e for e in r["endpoints"] if e["name"] == "Rerank")["effect"]
    with_expand, _ = run("quiet-changes", tmp_path)
    without, gw = run("disclosed-changes", tmp_path)
    assert said(with_expand) and not said(without) and not any("expand" in a["step"] for a in gw.audit)
    assert next(e for e in with_expand["endpoints"] if e["name"] == "Rerank")["decided_detail"]["expanded"] == [
        c["id"] for c in with_expand["changes"] if c["clause"] == "15"]
    assert "expand" not in json.dumps([e["effect"] for e in without["endpoints"]] + without["ablation"]).lower()


def test_passages_for_another_deal_size_are_skipped_not_quoted(tmp_path):
    r, _ = run("quiet-changes", tmp_path)
    pay = next(c for c in r["changes"] if c["clause"] == "4.2" and c["blocking"])
    assert [p["id"] for p in pay["playbook"]["passages"]] == ["payment-large"]
    assert [p["id"] for p in pay["playbook"]["skipped"]] == ["payment-small"]


def test_low_confidence_route_is_not_followed(tmp_path):
    r, _ = run("quiet-changes", tmp_path, low_confidence_route=True)
    assert all(c["route"] == "escalate" for c in r["changes"] if c["blocking"])
    decide = next(e for e in r["endpoints"] if e["name"] == "Decide")
    assert decide["decided"] == 0 and len(decide["decided_detail"]["routes_not_followed"]) == 4


# ------------------------------------------------------------ hidden text
def test_injection_blocks_even_when_everything_else_is_disclosed(tmp_path):
    r, _ = run("quiet-changes-injected", tmp_path)
    assert r["status"] == "blocked"
    assert any(x["source"] == "Guard" for x in r["reasons"]) and any(x["source"] == "Rules" for x in r["reasons"])
    assert r["hidden_text"] and r["guard"][-1]["evidence"][0]["rects"]
    hidden = next(x for x in r["reasons"] if x["source"] == "Rules")
    assert hidden["text"] == "The returned copy contains hidden text on page 2: text too small to read on paper."
    assert r["hidden_text"][0]["reason"] == "tiny" and r["hidden_text"][0]["reason_text"] == HIDDEN_WORDS["tiny"]
    guard = next(e for e in r["endpoints"] if e["name"] == "Guard")
    assert guard["decided"] == 0 and guard["decided_detail"]["only_blocker"] is False       # the rules block it as well


@pytest.mark.parametrize("how", ["white", "near_white", "render3", "size4", "offpage", "under_box", "under_image"])
def test_each_way_of_hiding_text_is_named_for_what_it_is(how, tmp_path):
    v1 = lf.build(tmp_path / "v1.pdf", lf.clauses_flow(lf.BASE, "{n}. {t}"))
    v2 = lf.hidden_variant(tmp_path / "v2.pdf", how)
    doc = read_pdf(v2)
    if not doc.hidden:
        pytest.skip(f"the rule layer does not class “{how}” as hidden in this fixture")
    r = review(v1, v2, "", Gateway("live", client=FakeClocked(EMPTY_TRUTH)), tmp_path / "out", route=False, terms=False)
    reason = next(x for x in r["reasons"] if x["source"] == "Rules" and x["level"] == "block")
    assert HIDDEN_WORDS[doc.hidden[0].reason] in reason["text"] and "under 4 pt" not in reason["text"]
    assert set(HIDDEN_WORDS) == {"tiny", "white", "invisible", "low-contrast", "covered", "off-page"}


# ---------------------------------------------------- pages that were not read
def test_a_page_without_a_text_layer_makes_the_review_incomplete_and_names_the_page(tmp_path):
    v1, v2 = lf.mixed_scan(tmp_path / "v1.pdf", 30), lf.mixed_scan(tmp_path / "v2.pdf", 90)        # the change is on the scanned page
    r = review(v1, v2, "", Gateway("live", client=FakeClocked(EMPTY_TRUTH)), tmp_path / "out")
    assert r["status"] == "incomplete" and r["headline"] == "Review incomplete" and r["errors"] == []
    texts = [x["text"] for x in r["reasons"] if x["level"] == "incomplete"]
    assert texts == ["Page 2 of the version we sent has no text layer (an image only), so it was not read. The review does not cover that page.",
                     "Page 2 of the returned copy has no text layer (an image only), so it was not read. The review does not cover that page."]
    assert r["unread_pages"] == {"v1": [2], "v2": [2]} and r["documents"]["v2"]["pages_without_text"] == [2]


def test_pages_read_through_an_ocr_layer_are_noted(tmp_path):
    pdf = lf.ocr_scan(tmp_path / "scan.pdf")
    r = review(pdf, pdf, "", Gateway("live", client=FakeClocked(EMPTY_TRUTH)), tmp_path / "out")
    assert r["ocr_pages"] == {"v1": [1], "v2": [1]} and r["documents"]["v1"]["ocr_pages"] == [1]
    assert sum("OCR text layer" in w["text"] for w in r["warnings"]) == 2 and r["status"] == "ready"


# ------------------------------------------------------- headers and footers
def test_a_header_or_footer_difference_is_labelled_as_one_never_as_a_notice_address(tmp_path):
    v1, v2 = read_pdf(CASES / "clean-return" / "v1.pdf"), read_pdf(CASES / "clean-return" / "v2.pdf")
    label = Hunk("header", "Header / footer", "administrative", "header", "Draft v1 - 2 March", "Draft v2 - 9 March", "Draft v1 - 2 March",
                 "Draft v2 - 9 March", "Header or footer text differs", 1, 1, "Running headers and footers are not part of any clause. They differ between the two files.")
    term = Hunk("header", "Header / footer", "substantive", "money", "", "A late fee of $500 applies", "", "A late fee of $500 applies",
                "Header or footer text added", 0, 1, "A running header or footer of the returned copy states a term.",
                v2_sentence="A late fee of $500 applies")
    changes = build_changes(cross_label([label, term], {}, {"v1": {}, "v2": {}}), v1, v2)
    held, admin = changes
    assert held["blocking"] and admin["cls"] == "administrative" and not admin["blocking"]
    for c in changes:
        assert c["clause"] == "header" and c["title"] == c["clause_label"] == "Page header or footer" and c["uid"] == {"v1": None, "v2": None}
    assert admin["summary"] == "Page header or footer differs" and "not sent to the API" in admin["why"]
    ledger, _ = build_ledger(changes)
    assert "notice address" not in ledger and "The page header or footer differs between the two files" in ledger
    assert "Substantive change in the page header or footer: Header or footer text added." in ledger
    assert ledger.startswith("The returned copy changes 1 commercial or legal term of the agreement we sent: the page header or footer.")


def test_a_moved_clause_is_listed_as_moved_same_text_and_does_not_block(tmp_path):
    v1, v2 = read_pdf(CASES / "clean-return" / "v1.pdf"), read_pdf(CASES / "clean-return" / "v2.pdf")
    a, b = v1.clauses[12], v2.clauses[14]
    moved = Hunk(b.number, b.title, "formatting", "moved", a.heading or a.title, b.heading or b.title, a.number, b.number,
                 f"Clause moved: {a.number} → {b.number}", a.page, b.page, "The same clause sits at a different place in the returned version.",
                 extra={"moved": [a.uid, a.number, b.uid, b.number]}, v1_uid=a.uid, v2_uid=b.uid)
    c, = build_changes(cross_label([moved], {}, None), v1, v2)
    assert c["cls"] == "formatting" and not c["blocking"] and c["summary"] == f"Clause moved, same text: {a.number} → {b.number}"
    assert c["clause_label"] == f"{a.number} → {b.number}" and "moved to a different place" in build_ledger([c])[0]


# ----------------------------------------------- what the review says of itself
OVERCLAIMS = re.compile(r"\(s\)|three times|read (?:it )?thrice|only tighten|never loosen", re.I)


@pytest.mark.parametrize("case,fake", [("quiet-changes", {}), ("quiet-changes-injected", {}), ("disclosed-changes", {}),
                                       ("clean-return", {}), ("added-clause", {}), ("wrong-file", {}), ("instruction-in-note", {}),
                                       ("disclosed-changes", {"fail": {"decide"}}), ("quiet-changes", {"compare_misses": {"liability_cap"}}),
                                       ("clean-return", {"fail": {"grounding"}})])
def test_no_sentence_of_the_review_overclaims_or_dodges_a_plural(case, fake, tmp_path):
    r, _ = run(case, tmp_path, **fake)
    said = [s for s in strings({k: r[k] for k in ("headline", "reasons", "changes", "ablation", "endpoints", "warnings", "compare")})]
    assert said and not [s for s in said if OVERCLAIMS.search(s)]
    assert not re.search(r"\b1 (?:changes|statements|rewordings|passages)\b|\b[2-9] (?:change|statement|rewording|passage)\b(?!s)", " ".join(said))


def test_the_summary_of_each_endpoint_says_what_code_does_with_its_answer(tmp_path):
    r, _ = run("quiet-changes", tmp_path)
    effect = {e["name"]: e["effect"] for e in r["endpoints"]}
    assert "not sent" in effect["Verify"] and "unless Compare objects" in effect["Verify"]
    assert "gate" in effect["Compare"] and "optional layer" in effect["Compare"]
    assert "code turns them into a route" in effect["Decide"]


def test_who_found_a_change_is_rules_both_or_clocked(tmp_path):
    r, _ = run("quiet-changes", tmp_path)
    assert {c["found_by"] for c in r["changes"]} <= {"rules", "both", "clocked"}
    assert all(c["found_by_label"] for c in r["changes"])


# ---------------------------------------------- what each endpoint decided
def test_each_endpoint_reports_its_share_of_credits_and_what_it_decided(tmp_path):
    r, _ = run("quiet-changes", tmp_path)
    share = r["audit"]["by_endpoint_share"]
    assert set(share) == {"Verify", "Compare", "Rerank", "Decide", "Extract", "Guard"} and abs(sum(share.values()) - 1) < 0.001
    by = {e["name"]: e for e in r["endpoints"]}
    assert all(isinstance(e["decided"], int) and e["credits_share"] == share[e["name"]] for e in by.values())
    reworded = [c["id"] for c in r["changes"] if c["cls"] == "reworded"]
    assert by["Verify"]["decided_detail"] == {"held_alone": [], "cleared": reworded, "note_statements_contradicted": [4, 5, 6],
                                              "only_blocker": True}
    assert any(a["endpoint"] == "Verify" and "would read “changes to resolve” instead of “do not sign”" in a["text"] for a in r["ablation"])
    assert by["Verify"]["decided"] == 4 and by["Compare"]["decided"] == 0
    held = [c["id"] for c in r["changes"] if c["blocking"]]
    assert by["Rerank"]["decided"] == 4 and sorted(by["Rerank"]["decided_detail"]["with_guidance"]) == held
    assert by["Decide"]["decided_detail"] == {"routes_followed": held, "routes_not_followed": []} and by["Decide"]["decided"] == 4
    assert by["Extract"]["decided_detail"] == {"deal_facts_given_to_routing": ["annual_fee"]} and by["Guard"]["decided"] == 0


def test_a_route_that_was_not_followed_is_counted_as_such(tmp_path):
    r, _ = run("quiet-changes-injected", tmp_path)
    by = {e["name"]: e for e in r["endpoints"]}
    ip = next(c for c in r["changes"] if c["clause"] == "7")
    assert ip["id"] in by["Rerank"]["decided_detail"]["without_guidance"] and ip["id"] in by["Decide"]["decided_detail"]["routes_not_followed"]


# --------------------------------------------------------- reader ablation
def test_without_verify_rewordings_stay_open_and_the_note_is_taken_at_its_word(tmp_path):
    full, _ = run("quiet-changes", tmp_path / "full")
    r, gw = run("quiet-changes", tmp_path / "ablated", review_kw={"ignore_readers": {"verify"}})
    assert r["ignored_readers"] == ["verify"] and full["ignored_readers"] == []
    assert full["status"] == "blocked" and r["status"] == "changes"                    # the false statements go unchallenged
    assert r["note"]["ran"] is False and r["note"]["ignored"] and all(c["verdict"] == "not_checked" for c in r["note"]["claims"])
    assert not [c for c in r["changes"] if c["cls"] == "reworded"]                    # nothing is cleared without Verify
    assert sum(c["blocking"] for c in r["changes"]) == sum(c["blocking"] for c in full["changes"]) + 1
    assert all(c["verify"] is None and c["readers"]["verify"] == "not_asked" for c in r["changes"])
    assert all("Verify" not in c["found_by_label"] for c in r["changes"]) and r["errors"] == []
    steps = lambda g: sorted(a["step"] for a in g.audit if a["step"].startswith("verify") and "cover note" not in a["step"])
    assert steps(gw) == ["verify returned sentences against v1", "verify sent sentences against v2"]   # same requests as a full run


def test_without_compare_its_veto_is_gone_and_the_gate_stays(tmp_path):
    kw = {"verify_misses": {"ip_ownership"}}
    full, _ = run("quiet-changes-injected", tmp_path / "full", **kw)
    r, _ = run("quiet-changes-injected", tmp_path / "ablated", review_kw={"ignore_readers": {"compare"}}, **kw)
    held = lambda x: sorted(c["clause"] for c in x["changes"] if c["blocking"])
    assert "7" in held(full) and "7" not in held(r)                                   # Compare alone was holding clause 7
    assert "11" in held(r)                                                            # a rewrite that carries a value is held regardless
    assert all(c["compare"] is None and c["readers"]["compare"] == "not_asked" for c in r["changes"])
    assert r["compare"]["gate"]["passed"] is True and r["ignored_readers"] == ["compare"]
    stopped, _ = run("wrong-file", tmp_path / "gate", review_kw={"ignore_readers": {"compare", "verify"}})
    assert stopped["headline"] == "Not the same agreement"                             # the gate is not a reader


def test_a_one_word_swap_is_held_by_the_rules_with_no_reader_at_all(tmp_path):
    r, _ = run("disclosed-changes", tmp_path, review_kw={"ignore_readers": {"verify", "compare"}})
    c = next(c for c in r["changes"] if "minimum" in c["summary"])
    assert c["blocking"] and c["found_by"] == "rules" and c["found_by_label"] == "Rules" and "rests on the rules" in c["why"]


def test_cross_label_can_ignore_a_reader_given_the_same_answers():
    h = swap_hunk()
    h.extra.pop("one_word_swap")
    ccmp, xv = {("c009", "c009"): clause_answer("conflicting", 0.97, [conflict_on(h)])}, both_ways(h)
    assert cross_label([h], ccmp, xv)[0]["cls"] == "substantive"                       # Compare vetoes
    assert cross_label([h], ccmp, xv, ignore_readers={"compare"})[0]["cls"] == "reworded"
    assert cross_label([h], ccmp, xv, ignore_readers={"verify"})[0]["confirmed_by"] == ["Compare"]
    assert cross_label([h], ccmp, xv, ignore_readers={"verify", "compare"})[0]["found_by"] == "rules"


def test_an_unknown_reader_name_is_refused(tmp_path):
    with pytest.raises(ValueError, match="ignore_readers"):
        run("clean-return", tmp_path, review_kw={"ignore_readers": {"guard"}})


# ----------------------------------------------------------------- failure
@pytest.mark.parametrize("step", ["compare", "grounding", "rerank", "decide", "injection", "extract"])  # grounding = Verify
def test_a_failed_step_is_never_reported_as_a_pass(step, tmp_path):
    r, _ = run("clean-return" if step not in ("rerank", "decide") else "disclosed-changes", tmp_path, fail={step})
    assert r["status"] in ("incomplete", "blocked") and r["status"] != "ready"
    assert any(step in e["message"] for e in r["errors"])


def test_failed_routing_step_escalates_the_change(tmp_path):
    r, _ = run("disclosed-changes", tmp_path, fail={"decide"})
    assert all(c["route"] == "escalate" for c in r["changes"] if c["blocking"])


def test_when_a_reader_fails_the_rules_still_hold_what_they_found(tmp_path):
    r, _ = run("quiet-changes", tmp_path, fail={"grounding"})
    assert sum(c["blocking"] for c in r["changes"]) == 5                               # the rewording is not cleared either
    assert all(c["readers"]["verify"] == "unclear" for c in r["changes"] if c["blocking"])
    assert r["status"] == "incomplete" and not r["note"]["ran"]


def test_a_stage_of_our_own_that_breaks_is_reported_and_the_review_still_returns(tmp_path, monkeypatch):
    from referee import engine
    monkeypatch.setattr(engine, "_terms", lambda *a, **k: 1 / 0)
    r, _ = run("clean-return", tmp_path)
    assert r["status"] == "incomplete" and [(e["endpoint"], e["step"], e["kind"]) for e in r["errors"]] == [("Extract", "read the key terms", "internal")]
    assert r["terms"] == [] and r["note"]["ran"] and (tmp_path / "clean-return" / "review.json").exists()
    monkeypatch.setattr(engine, "compare_documents", lambda *a, **k: 1 / 0)
    r, _ = run("quiet-changes", tmp_path)
    assert r["status"] != "ready" and any(e["endpoint"] == "Rules" for e in r["errors"])


def test_misaligned_evidence_is_counted_not_hidden(tmp_path):
    r, _ = run("quiet-changes", tmp_path, misalign=True)
    assert r["compare"]["unaligned"] > 0
    flagged = [c for c in r["changes"] if c["blocking"] and c["compare"]["relation"] == "conflict"]
    assert flagged and all(c["compare"]["aligned"] is False and "v2" not in c["compare"] for c in flagged)


# ------------------------------------------------------------------ output
def test_marked_pdfs_from_compare_are_saved_and_rendered(tmp_path):
    r, _ = run("quiet-changes", tmp_path)
    for key in ("v1", "v2"):
        d = r["documents"][key]
        assert d["marked_pdf"] and (tmp_path / "quiet-changes" / d["marked_pdf"]).stat().st_size > 1000
        assert len(d["pages"]) == d["pages_count"] and (tmp_path / "quiet-changes" / d["pages"][0]["image"]).exists()
        layer = next(l for l in d["layers"] if l["id"] == "compare")
        assert len(layer["pages"]) == d["pages_count"] and (tmp_path / "quiet-changes" / layer["pages"][0]["image"]).exists()


def test_every_change_points_at_a_place_on_both_pages(tmp_path):
    r, _ = run("quiet-changes", tmp_path)
    for c in r["changes"]:
        for p in c["parts"]:
            assert p["v2"]["located"] and p["v2"]["rects"], c["summary"]
            assert p["v1"]["located"] and p["v1"]["rects"], c["summary"]


def test_the_fields_the_reply_draft_reads_are_all_there(tmp_path):
    """referee/reply.py reads these from review.json."""
    r, _ = run("quiet-changes", tmp_path)
    for c in r["changes"]:
        assert {"id", "clause", "title", "blocking", "parts"} <= set(c) and c["parts"][0]["v1"].keys() >= {"quote"}
    held = [c for c in r["changes"] if c["blocking"]]
    assert all(c["compare"]["v1_quote"] and c["playbook"]["passages"][0]["id"] for c in held)
    from referee.reply import build_brief
    brief, items = build_brief(r, {c["id"]: "counter" for c in held})
    assert len(items) == len(held) and "Clause 4.2 (Payment Terms)" in brief


def test_record_then_replay_gives_the_same_review(tmp_path):
    d = CASES / "quiet-changes"
    rec = tmp_path / "rec"
    live = Gateway("live", rec, record=True, client=FakeClocked(load_truth(d)))
    a, _ = run("quiet-changes", tmp_path / "a", gw=live)
    replay = Gateway("replay", rec)
    b, _ = run("quiet-changes", tmp_path / "b", gw=replay)
    strip = lambda r: json.dumps({k: v for k, v in r.items() if k not in ("audit", "mode", "endpoints")}, sort_keys=True)
    assert strip(a) == strip(b) and b["mode"] == "replay" and all(x["replayed"] for x in replay.audit)


def test_replay_refuses_a_request_it_never_recorded(tmp_path):
    r, _ = run("clean-return", tmp_path, gw=Gateway("replay", tmp_path / "empty"))
    assert r["status"] == "incomplete" and all(e["kind"] == "replay_miss" for e in r["errors"])


def test_credit_cap_stops_further_calls(tmp_path):
    d = CASES / "quiet-changes"
    gw = Gateway("live", client=FakeClocked(load_truth(d)), credit_cap=30)
    r, _ = run("quiet-changes", tmp_path, gw=gw)
    assert any(e["kind"] == "budget" for e in r["errors"]) and r["status"] != "ready"


def test_ledger_states_plainly_when_nothing_substantive_changed(tmp_path):
    r, _ = run("clean-return", tmp_path)
    ledger, _ = build_ledger(r["changes"])
    assert ledger.startswith("The returned copy does not change any commercial or legal term")


def test_a_phrase_that_also_appears_in_another_clause_is_placed_in_its_own_clause(tmp_path):
    """'Portland, Oregon' is in the notices clause (page 2) and, after the edit, in the venue clause (page 3)."""
    r, _ = run("quiet-changes", tmp_path)
    law = next(c for c in r["changes"] if c["clause"] == "15")
    assert {p["v2"]["page"] for p in law["parts"]} == {3}


def test_overlays_stay_inside_the_cropped_page(tmp_path):
    r, _ = run("quiet-changes-injected", tmp_path)

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k == "rects":
                    for x0, y0, x1, y1 in v:
                        assert 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1
                else:
                    walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(r)
    page = r["documents"]["v2"]["pages"][0]
    assert page["width"] < 0.9 * 8.5 * 132                # side margins were cropped


def test_ledger_names_the_clauses_that_did_not_change(tmp_path):
    r, _ = run("quiet-changes", tmp_path)
    ledger, _ = build_ledger(r["changes"], read_pdf(CASES / "quiet-changes" / "v2.pdf"))
    assert "Clause 4.1 (Fees) is unchanged" in ledger and "Clause 4.2 (Payment Terms) is unchanged" not in ledger


def test_extracted_terms_compare_by_value_not_by_wording():
    from referee.engine import _same_term
    assert _same_term("three (3) years after termination or expiration of this Agreement", "3 years")
    assert _same_term("thirty (30) days", "30 days")
    assert not _same_term("thirty (30)", "ninety (90)")
    assert not _same_term("up to a maximum of 10% of the monthly fee", "up to a minimum of 10% of the monthly fee")
    assert not _same_term("30", "130") and not _same_term("Delaware", "Oregon")


def test_route_comes_from_two_yes_no_readings_and_the_middle_is_not_an_answer():
    from referee.engine import _route_from_answers as route
    assert route(0.95, 0.10)[0] == "accept" and route(0.95, 0.90)[0] == "accept"
    assert route(0.05, 0.92)[0] == "counter"
    assert route(0.05, 0.08)[0] == "escalate"
    assert route(0.50, 0.90)[0] is None and route(0.10, 0.50)[0] is None and route(None, 0.9)[0] is None


def test_a_position_shared_by_two_cards_is_converted_to_page_fractions_once():
    """An edit inside a held sentence is listed twice; its rectangles must not be shifted twice."""
    from referee.textdoc import remap_rects
    shared = {"rects": [[0.5, 0.5, 0.6, 0.55]]}
    tree = {"a": [shared], "b": {"again": shared}}
    remap_rects(tree, (0.1, 0.1, 0.9, 0.9))
    assert shared["rects"] == [[0.5, 0.5, 0.625, 0.5625]]
