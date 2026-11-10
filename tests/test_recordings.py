"""End to end, on what the real API answered.

Every case with a folder under recordings/ is replayed (no network, no key) and the
whole review is checked against the case's exact ground truth. These are the same
recordings the demo site and eval/RESULTS.md are built from.
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


@pytest.mark.parametrize("case", RECORDED)
def test_replay_is_complete_and_never_touches_the_network(case, tmp_path_factory):
    r, gw, _ = replay(case, tmp_path_factory)
    assert r["errors"] == [] and r["mode"] == "replay"
    assert gw.audit and all(a["replayed"] and a["request_id"] for a in gw.audit)
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
    assert held <= planted, f"held without cause: {sorted(held - planted)}"


@pytest.mark.parametrize("case", RECORDED)
def test_faithful_rewordings_are_cleared(case, tmp_path_factory):
    r, _, truth = replay(case, tmp_path_factory)
    for e in truth["cosmetic"]:
        if e["id"].startswith("reword"):
            assert any(c["cls"] == "reworded" and c["clause"] == e["clause"] for c in r["changes"]), e["id"]


@pytest.mark.parametrize("case", RECORDED)
def test_one_word_meaning_changes_are_caught_by_a_clocked_reader(case, tmp_path_factory):
    r, _, truth = replay(case, tmp_path_factory)
    for e in truth["substantive"]:
        if e["id"] == "min_max":
            c = next(c for c in r["changes"] if c["clause"] == e["clause"] and c["blocking"])
            assert c["promoted"] and c["found_by_label"] in ("Verify + Compare", "Verify", "Compare")


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
    flagged = any(g.get("flagged") for g in r["guard"])
    assert flagged == truth["injection"] and bool(r["hidden_text"]) == truth["injection"]


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
    r, _, _ = replay(case, tmp_path_factory)
    doc = r["compare"]["document"]
    assert doc["relation"] in ("same", "conflicting", "overlapping") and doc["related"] >= 0.5
    assert all(r["documents"][k]["marked_pdf"] for k in ("v1", "v2"))


@pytest.mark.parametrize("case", DEMO)
def test_every_open_change_is_routed_with_or_without_guidance(case, tmp_path_factory):
    r, _, _ = replay(case, tmp_path_factory)
    for c in (c for c in r["changes"] if c["blocking"]):
        assert c["route"] in ("accept", "counter", "escalate") and c["route_reason"]
        if c["playbook"]["status"] == "none":
            assert c["route"] == "escalate"
        if (c["decide"]["route_confidence"] or 0) < 0.6:
            assert c["route"] == "escalate"
        assert all(p["relevance"] >= 0.9 for p in c["playbook"]["passages"])


@pytest.mark.parametrize("case", DEMO)
def test_key_terms_are_read_from_both_versions_and_placed_on_the_page(case, tmp_path_factory):
    r, _, _ = replay(case, tmp_path_factory)
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
    should = set().union(*(expect.get(p, set()) for p in planted)) if planted else set()
    assert moved == should, {"unexpected": sorted(moved - should), "missing": sorted(should - moved)}
