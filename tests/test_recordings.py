"""End to end, on what the real API answered.

Every case with a folder under recordings/ is replayed (no network, no key) and the
whole review is checked against the case's exact ground truth. These are the same
recordings the demo site and eval/RESULTS.md are built from. A case in data/cases/
that has no recordings/<case>/ folder yet is simply not here.

How each group was recorded, and so how it is replayed:
  demo    the full review: cover note, key terms, routing
  eval    with a cover note, without Extract, Rerank and Decide (route=False, terms=False)
  struct  like eval
  hard    no cover note, route=False, terms=False; the truth lists every hard edit as changed or
          reworded, and only "every changed edit is held" is asserted (see HARD_FALSE_ALARMS)
"""
import json
from pathlib import Path

import pytest

from referee.engine import review
from referee.gateway import Gateway

ROOT = Path(__file__).resolve().parents[1]
CASES, REC = ROOT / "data" / "cases", ROOT / "recordings"
INDEX = {c["id"]: c for c in json.loads((CASES / "index.json").read_text())}
RECORDED = sorted(d.name for d in REC.iterdir() if d.is_dir() and d.name in INDEX and any(d.glob("*.json"))) if REC.is_dir() else []
DEMO = [c for c in RECORDED if INDEX[c]["group"] == "demo"]
HARD = [c for c in RECORDED if INDEX[c]["group"] == "hard"]
# One faithful rewording of the hard set is read as a change by both readers and stays held. It is a known
# false alarm of the API's reading, reported in eval/, and nothing here asserts on it either way.
HARD_FALSE_ALARMS = {"hard-a-05"}

pytestmark = pytest.mark.skipif(not RECORDED, reason="no recorded Clocked responses in recordings/")
_cache: dict = {}


def replay(case: str, tmp_path_factory):
    if case not in _cache:
        d = CASES / case
        full = INDEX[case]["group"] == "demo"          # evaluation pairs are recorded without routing and terms
        gw = Gateway("replay", REC / case)
        r = review(d / "v1.pdf", d / "v2.pdf", (d / "cover.txt").read_text(), gw,
                   tmp_path_factory.mktemp(case), route=full, terms=full)
        _cache[case] = (r, gw, json.loads((d / "truth.json").read_text()))
    return _cache[case]


def wrong_file(truth: dict) -> bool:
    return bool(truth.get("wrong_file"))


@pytest.mark.parametrize("case", RECORDED)
def test_replay_is_complete_and_never_touches_the_network(case, tmp_path_factory):
    r, gw, _ = replay(case, tmp_path_factory)
    assert r["errors"] == [] and r["mode"] == "replay"
    assert gw.audit and all(a["replayed"] and (a.get("request_id") or a.get("trace")) for a in gw.audit)
    assert r["audit"]["credits"] > 0


@pytest.mark.parametrize("case", RECORDED)
def test_status_equals_ground_truth(case, tmp_path_factory):
    r, _, truth = replay(case, tmp_path_factory)
    assert r["status"] == truth["expected_status"]


@pytest.mark.parametrize("case", RECORDED)
def test_every_planted_change_is_held_and_nothing_else_is(case, tmp_path_factory):
    r, _, truth = replay(case, tmp_path_factory)
    held = {c["clause"] for c in r["changes"] if c["blocking"]}
    planted = {e["clause"] for e in truth["substantive"]}
    assert planted <= held, f"missed: {sorted(planted - held)}"
    if INDEX[case]["group"] != "hard":                # the hard set has one known false alarm: see HARD_FALSE_ALARMS
        assert held <= planted, f"held without cause: {sorted(held - planted)}"


@pytest.mark.parametrize("case", HARD)
def test_every_hard_edit_that_changes_meaning_is_held_edit_by_edit(case, tmp_path_factory):
    """Clause numbers are not enough on the hard set (several edits share a clause): each planted edit must be
    inside a held change, found by the words it changed."""
    r, _, truth = replay(case, tmp_path_factory)
    nz = lambda t: "".join(ch for ch in t.lower() if ch.isalnum())
    for e in truth["substantive"]:
        held = [c for c in r["changes"] if c["blocking"] and c["clause"] == e["clause"]]
        assert held, e["id"]
        text = nz(" ".join(p[s][k] for c in held for p in c["parts"] for s in ("v1", "v2") for k in ("quote", "changed"))
                  + " " + " ".join(str((c["compare"] or {}).get(k) or "") for c in held for k in ("v1_quote", "v2_quote")))
        words = [w for w in e["v2_quote"].split() if w not in e["v1_quote"].split()] or [w for w in e["v1_quote"].split() if w not in e["v2_quote"].split()]
        assert any(nz(w) in text for w in words), (e["id"], words)


@pytest.mark.parametrize("case", HARD)
def test_the_hard_set_is_replayed_as_it_was_recorded(case, tmp_path_factory):
    r, gw, truth = replay(case, tmp_path_factory)
    assert r["note"]["claims"] == [] and r["terms"] == [] and not any(c["playbook"] for c in r["changes"])
    assert {a["endpoint"] for a in gw.audit} == {"Compare", "Verify", "Guard"}
    cleared = {c["clause"] for c in r["changes"] if c["cls"] == "reworded"}
    reworded = {e["clause"] for e in truth["cosmetic"] if e["id"] not in HARD_FALSE_ALARMS}
    changed = {e["clause"] for e in truth["substantive"]}
    assert cleared <= reworded | changed                 # nothing is called a rewording that the truth does not list


@pytest.mark.parametrize("case", RECORDED)
def test_faithful_rewordings_are_cleared(case, tmp_path_factory):
    r, _, truth = replay(case, tmp_path_factory)
    for e in truth["cosmetic"]:
        if e["id"].startswith("reword"):
            assert any(c["cls"] == "reworded" and c["clause"] == e["clause"] for c in r["changes"]), e["id"]


@pytest.mark.parametrize("case", RECORDED)
def test_one_word_meaning_changes_are_held_by_the_rules_and_confirmed_by_a_clocked_reader(case, tmp_path_factory):
    """"maximum" became "minimum": two real words, so the rule layer holds it without asking anyone.
    The readers are still asked, and on the recordings they confirm it; they could clear it only together."""
    r, _, truth = replay(case, tmp_path_factory)
    for e in truth["substantive"]:
        if e["id"] == "min_max":
            c = next(c for c in r["changes"] if c["clause"] == e["clause"] and c["blocking"] and "minimum" in c["summary"])
            assert not c["promoted"] and c["readers"]["rules"] == "substantive" and c["found_by"] == "both"
            assert c["found_by_label"] in ("Rules + Verify + Compare", "Rules + Verify", "Rules + Compare")
            assert c["readers"]["verify"] == "changed" or c["readers"]["compare"] == "conflict"


@pytest.mark.parametrize("case", RECORDED)
def test_every_difference_carries_the_three_readings_and_says_which_one_won(case, tmp_path_factory):
    r, _, _ = replay(case, tmp_path_factory)
    for c in r["changes"]:
        rd = c["readers"]
        assert rd["rules"] in ("substantive", "spelling", "formatting", "administrative")
        assert rd["verify"] in ("changed", "same", "unclear", "not_asked") and rd["compare"] in ("conflict", "same", "silent", "not_asked")
        if c["cls"] in ("formatting", "administrative"):      # settled by the rules, sent to no reader
            assert (rd["verify"], rd["compare"]) == ("not_asked", "not_asked") and "not sent to the API" in c["why"]
        if c["blocking"] and not rd["agree"]:
            assert "stricter reading wins" in c["why"], c["why"]
        assert "(s)" not in c["why"] + c["summary"]
        assert c["uid"]["v1"] or c["uid"]["v2"] or c["clause"] == "header"


@pytest.mark.parametrize("case", RECORDED)
def test_what_each_endpoint_decided_adds_up(case, tmp_path_factory):
    r, _, truth = replay(case, tmp_path_factory)
    by = {e["name"]: e for e in r["endpoints"]}
    assert abs(sum(r["audit"]["by_endpoint_share"].values()) - 1) < 0.001
    contradicted = [i for i, c in enumerate(r["note"]["claims"]) if c["verdict"] == "contradicted"]
    assert by["Verify"]["decided_detail"]["note_statements_contradicted"] == contradicted
    assert by["Verify"]["decided_detail"]["cleared"] == [c["id"] for c in r["changes"] if c["cls"] == "reworded"]
    assert by["Compare"]["decided_detail"]["gate_stopped_the_review"] is wrong_file(truth)
    routed = [c for c in r["changes"] if c["blocking"] and c["playbook"]]
    assert by["Rerank"]["decided"] == len(routed) and by["Decide"]["decided"] <= len(routed)
    assert not any("(s)" in x["text"] for x in r["reasons"] + r["ablation"])


@pytest.mark.parametrize("case", RECORDED)
def test_no_false_statement_in_the_cover_note_gets_through(case, tmp_path_factory):
    r, _, truth = replay(case, tmp_path_factory)
    got = {c["text"]: c["verdict"] for c in r["note"]["claims"]}
    missed = [c["text"] for c in truth["letter_claims"] if c["expected"] == "contradicted" and got.get(c["text"]) != "contradicted"]
    assert not missed, missed


@pytest.mark.parametrize("case", RECORDED)
def test_no_true_statement_in_the_cover_note_is_called_false(case, tmp_path_factory):
    """A true statement may come back "not stated" (two do, in the evaluation set); it must never come back contradicted."""
    r, _, truth = replay(case, tmp_path_factory)
    got = {c["text"]: c["verdict"] for c in r["note"]["claims"]}
    wrong = [c["text"] for c in truth["letter_claims"] if c["expected"] == "supported" and got.get(c["text"]) == "contradicted"]
    assert not wrong, wrong


@pytest.mark.parametrize("case", DEMO)
def test_demo_cover_notes_get_exactly_the_expected_verdicts(case, tmp_path_factory):
    r, _, truth = replay(case, tmp_path_factory)
    got = {c["text"]: c["verdict"] for c in r["note"]["claims"]}
    wrong = {c["text"]: (c["expected"], got.get(c["text"])) for c in truth["letter_claims"] if got.get(c["text"]) != c["expected"]}
    assert not wrong, wrong


@pytest.mark.parametrize("case", RECORDED)
def test_hidden_instructions_are_flagged_and_clean_copies_are_not(case, tmp_path_factory):
    r, _, truth = replay(case, tmp_path_factory)
    if wrong_file(truth):                             # the review stops at the gate: Guard is never asked
        assert r["guard"] == []
        return
    flagged = {g["target"] for g in r["guard"] if g.get("flagged")}
    # hidden text sits in the returned PDF (rules and Guard both see it); a visible instruction sits in the note
    assert ("Returned copy" in flagged) == truth["injection"] and bool(r["hidden_text"]) == truth["injection"]
    assert ("Cover note" in flagged) == bool(truth.get("note_instruction"))


@pytest.mark.parametrize("case", RECORDED)
def test_evidence_offsets_reproduce_their_quotes(case, tmp_path_factory):
    r, _, _ = replay(case, tmp_path_factory)
    assert r["compare"]["unaligned"] == 0
    for c in r["note"]["claims"]:
        if c["evidence"]:
            assert c["evidence"]["aligned"] is not False, c["text"]
    for t in r["terms"]:
        for side in ("v1", "v2"):
            assert t[side]["aligned"] is not False, t["field"]


@pytest.mark.parametrize("case", RECORDED)
def test_both_files_read_as_versions_of_one_agreement(case, tmp_path_factory):
    r, gw, truth = replay(case, tmp_path_factory)
    doc, gate = r["compare"]["document"], r["compare"]["gate"]
    if wrong_file(truth):                             # the gate is the whole review
        assert gate["passed"] is False and gate["stopped"] and (doc["relation"] == "unrelated" or doc["related"] < 0.5)
        assert r["headline"] == "Not the same agreement" and r["changes"] == [] and len(gw.audit) == 1
        return
    assert doc["relation"] in ("same", "conflicting", "overlapping") and doc["related"] >= 0.5
    assert gate["passed"] is True and gate["stopped"] is False
    # the gate asks for the relation only: it must stay the cheapest call of the review
    gate_call = next(x for x in gw.audit if x["step"] == "compare v1 v2")
    assert (gate_call["credits"] or 0) <= 5


@pytest.mark.parametrize("case", DEMO)
def test_every_open_change_is_routed_with_or_without_guidance(case, tmp_path_factory):
    r, _, _ = replay(case, tmp_path_factory)
    for c in (c for c in r["changes"] if c["blocking"]):
        assert c["route"] in ("accept", "counter", "escalate") and c["route_reason"]
        if c["playbook"]["status"] == "none":
            assert c["route"] == "escalate"
        if c["decide"]["route"] is None:                # Decide gave no clear yes or no
            assert c["route"] == "escalate"
        assert all(p["relevance"] >= 0.9 for p in c["playbook"]["passages"])


@pytest.mark.parametrize("case", DEMO)
def test_key_terms_are_read_from_both_versions_and_placed_on_the_page(case, tmp_path_factory):
    r, _, truth = replay(case, tmp_path_factory)
    if wrong_file(truth):
        assert r["terms"] == []
        return
    assert len(r["terms"]) == 13
    for t in r["terms"]:
        for side in ("v1", "v2"):
            assert t[side]["value"] and t[side]["rects"], (t["field"], side)


@pytest.mark.parametrize("case", DEMO)
def test_changed_terms_agree_with_the_changes_found(case, tmp_path_factory):
    """A key term Extract reads differently in the two versions must sit in a clause that holds a change."""
    r, _, truth = replay(case, tmp_path_factory)
    moved = {t["field"] for t in r["terms"] if t["changed"]}
    planted = {e["id"] for e in truth["substantive"]}
    expect = {"pay_terms": {"payment_terms"}, "fee_amount": {"annual_fee"}, "late_interest": {"late_interest"},
              "price_cap": {"price_increase_cap"}, "initial_term": {"initial_term"}, "renewal_notice": {"renewal_notice"},
              "sla_uptime": {"uptime"}, "sla_credit_cap": {"service_credit_limit"}, "min_max": {"service_credit_limit"},
              "data_export": {"data_export_window"}, "confidentiality": {"confidentiality_period"},
              "liability_cap": {"liability_cap"}, "governing_law": {"governing_law", "venue"}}
    if wrong_file(truth) or not planted <= set(expect):   # an edit with no key term of its own (a hard rewording, an added clause)
        return
    should = set().union(*(expect.get(p, set()) for p in planted)) if planted else set()
    assert moved == should, {"unexpected": sorted(moved - should), "missing": sorted(should - moved)}


# ---- a different agreement: nothing may depend on the demo document ---------
OTHER = ROOT / "tests" / "fixtures" / "other-agreement"


@pytest.mark.skipif(not (REC / "other-agreement").is_dir(), reason="other-agreement not recorded")
def test_a_different_agreement_in_a_different_layout(tmp_path):
    gw = Gateway("replay", REC / "other-agreement")
    r = review(OTHER / "v1.pdf", OTHER / "v2.pdf", (OTHER / "cover.txt").read_text(), gw, tmp_path)
    assert r["errors"] == [] and r["status"] == "blocked"
    held = {c["clause"]: c for c in r["changes"] if c["blocking"]}
    assert set(held) == {"3", "4", "8"}
    assert all(c["found_by_label"] == "Rules + Verify + Compare" for c in held.values())
    assert [c["cls"] for c in r["changes"] if c["clause"] == "5"] == ["formatting"]
    verdicts = {c["text"]: c["verdict"] for c in r["note"]["claims"]}
    assert verdicts["Everything else is as you sent it."] == "contradicted"
    assert verdicts["We only tidied the formatting of the return clause."] == "supported"
    # an NDA has no fees or service levels: those terms come back empty, not invented
    empty = {t["field"] for t in r["terms"] if not t["v2"]["value"]}
    assert {"annual_fee", "payment_terms", "uptime", "liability_cap"} <= empty
    # the playbook is about a different kind of deal: no guidance means a person decides
    assert all(c["route"] == "escalate" for c in held.values())
