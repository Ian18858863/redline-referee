"""Our own logic around the API: cross-labelling, routing, status, failure and replay.

Clocked itself is replaced by tests/fake_clocked.py here. The real API's behaviour is
measured separately, on recorded live responses (tests/test_recordings.py, eval/).
"""
import json
from pathlib import Path

import pytest

from referee.engine import build_ledger, review, split_claims
from referee.gateway import Gateway
from tests.fake_clocked import FakeClocked, load_truth

CASES = Path(__file__).resolve().parents[1] / "data" / "cases"


def run(case, tmp_path, gw=None, **fake):
    d = CASES / case
    gw = gw or Gateway("live", client=FakeClocked(load_truth(d), **fake))
    return review(d / "v1.pdf", d / "v2.pdf", (d / "cover.txt").read_text(), gw, tmp_path / case), gw


@pytest.mark.parametrize("case", ["clean-return", "quiet-changes", "quiet-changes-injected", "disclosed-changes"])
def test_status_matches_ground_truth(case, tmp_path):
    r, _ = run(case, tmp_path)
    assert r["status"] == load_truth(CASES / case)["expected_status"]
    assert r["errors"] == []


def test_verify_clears_a_faithful_rewording(tmp_path):
    r, _ = run("quiet-changes", tmp_path)
    reworded = [c for c in r["changes"] if c["cls"] == "reworded"]
    assert len(reworded) == 1 and not reworded[0]["blocking"] and reworded[0]["clause"] == "4.2"
    assert reworded[0]["verify"]["relation"] == "same"
    pay = next(c for c in r["changes"] if c["clause"] == "4.2" and c["blocking"])
    assert pay["found_by"] == "both" and "90" in pay["summary"] and pay["found_by_label"] == "Rules + Verify + Compare"


def test_verify_overrules_a_spelling_fix_that_changes_meaning(tmp_path):
    r, _ = run("disclosed-changes", tmp_path, compare_misses={"min_max"})
    c = next(c for c in r["changes"] if "minimum" in c["summary"])
    assert c["blocking"] and c["promoted"] and c["found_by_label"] == "Verify"
    assert any(c["id"] in a["changes"] for a in r["ablation"] if a["endpoint"] == "Verify")


def test_compare_alone_can_also_overrule_a_spelling_fix(tmp_path):
    r, _ = run("disclosed-changes", tmp_path, verify_misses={"min_max"})
    c = next(c for c in r["changes"] if "minimum" in c["summary"])
    assert c["blocking"] and c["promoted"] and c["found_by_label"] == "Compare"
    assert any(c["id"] in a["changes"] for a in r["ablation"] if a["endpoint"] == "Compare")


def test_with_neither_reader_the_meaning_change_stays_a_spelling_fix(tmp_path):
    r, _ = run("disclosed-changes", tmp_path, compare_misses={"min_max"}, verify_misses={"min_max"})
    c = next(c for c in r["changes"] if "minimum" in c["summary"])
    assert c["cls"] == "spelling" and not c["blocking"]


def test_a_typo_next_to_a_real_change_is_not_promoted(tmp_path):
    r, _ = run("quiet-changes", tmp_path)
    typo = next(c for c in r["changes"] if c["clause"] == "3" and c["cls"] == "spelling")
    assert not typo["blocking"]
    assert any(c["clause"] == "3" and c["blocking"] for c in r["changes"])


def test_when_compare_and_verify_disagree_the_change_stays_open(tmp_path):
    r, _ = run("quiet-changes", tmp_path, compare_misses={"liability_cap"})
    c = next(c for c in r["changes"] if c["clause"] == "9")
    assert c["blocking"] and c["compare"]["relation"] == "same" and c["verify"]["relation"] == "changed"
    assert c["found_by_label"] == "Rules + Verify"


def test_compare_reads_only_the_clauses_in_question(tmp_path):
    r, gw = run("quiet-changes", tmp_path)
    asked = sorted(a["step"] for a in gw.audit if a["step"].startswith("compare clause"))
    assert asked == sorted(f"compare clause {n}" for n in ("3", "4.2", "5", "9", "15"))
    assert r["compare"]["clauses_asked"] == 5 and r["compare"]["document"]["relation"] == "conflicting"


def test_compare_sentences_are_placed_on_both_pages(tmp_path):
    r, _ = run("quiet-changes", tmp_path)
    pay = next(c for c in r["changes"] if c["clause"] == "4.2" and c["blocking"])
    assert pay["compare"]["relation"] == "conflict" and pay["compare"]["v1"]["rects"] and pay["compare"]["v2"]["rects"]


def test_a_number_that_moved_is_never_cleared_even_if_both_readers_miss_it(tmp_path):
    r, _ = run("quiet-changes", tmp_path, compare_misses={"pay_terms"}, verify_misses={"pay_terms"})
    pay = next(c for c in r["changes"] if c["clause"] == "4.2" and "90" in c["summary"])
    assert pay["blocking"] and pay["found_by"] == "rules" and pay["found_by_label"] == "Rules"


def test_compare_vetoes_clearing_a_rewording_it_reads_as_a_conflict(tmp_path):
    r, _ = run("quiet-changes-injected", tmp_path, verify_misses={"termination_convenience"})
    c = next(c for c in r["changes"] if c["clause"] == "11" and c["kind"] != "format")
    assert c["blocking"] and c["cls"] == "substantive"


def test_files_that_are_not_two_versions_of_one_agreement_are_not_reviewed_as_such(tmp_path):
    r, _ = run("clean-return", tmp_path, unrelated=True)
    assert r["status"] == "incomplete" and any(x["source"] == "Compare" and "same agreement" in x["text"] for x in r["reasons"])


def test_contradicted_cover_note_blocks_and_links_to_the_change(tmp_path):
    r, _ = run("quiet-changes", tmp_path)
    bad = [c for c in r["note"]["claims"] if c["verdict"] == "contradicted"]
    assert len(bad) == 3 and r["status"] == "blocked"
    pay = next(c for c in bad if "Payment terms" in c["text"])
    target = next(c for c in r["changes"] if c["id"] in pay["change_ids"])
    assert target["clause"] == "4.2" and pay["evidence"]["aligned"] is True


def test_true_statements_about_untouched_terms_are_supported(tmp_path):
    r, _ = run("quiet-changes", tmp_path)
    fee = next(c for c in r["note"]["claims"] if "annual subscription fee" in c["text"])
    assert fee["verdict"] == "supported"


def test_no_playbook_guidance_forces_escalation(tmp_path):
    r, _ = run("quiet-changes-injected", tmp_path)
    ip = next(c for c in r["changes"] if c["clause"] == "7")
    assert ip["playbook"]["status"] == "none" and ip["route"] == "escalate" and "Legal" in ip["route_reason"]


def test_rerank_expand_fetches_the_neighbouring_passage(tmp_path):
    r, gw = run("quiet-changes", tmp_path)
    law = next(c for c in r["changes"] if c["clause"] == "15")
    assert law["playbook"]["expanded"] and law["playbook"]["passages"][0]["id"] == "law-other"
    assert any(a["step"] == "rerank 15 expand" for a in gw.audit)


def test_passages_for_another_deal_size_are_skipped_not_quoted(tmp_path):
    r, _ = run("quiet-changes", tmp_path)
    pay = next(c for c in r["changes"] if c["clause"] == "4.2" and c["blocking"])
    assert [p["id"] for p in pay["playbook"]["passages"]] == ["payment-large"]
    assert [p["id"] for p in pay["playbook"]["skipped"]] == ["payment-small"]


def test_low_confidence_route_is_not_followed(tmp_path):
    r, _ = run("quiet-changes", tmp_path, low_confidence_route=True)
    assert all(c["route"] == "escalate" for c in r["changes"] if c["blocking"])


def test_injection_blocks_even_when_everything_else_is_disclosed(tmp_path):
    r, _ = run("quiet-changes-injected", tmp_path)
    assert r["status"] == "blocked"
    assert any(x["source"] == "Guard" for x in r["reasons"]) and any(x["source"] == "Rules" for x in r["reasons"])
    assert r["hidden_text"] and r["guard"][-1]["evidence"][0]["rects"]


@pytest.mark.parametrize("step", ["compare", "grounding", "rerank", "decide", "injection", "extract"])  # grounding = Verify
def test_a_failed_step_is_never_reported_as_a_pass(step, tmp_path):
    r, _ = run("clean-return" if step not in ("rerank", "decide") else "disclosed-changes", tmp_path, fail={step})
    assert r["status"] in ("incomplete", "blocked") and r["status"] != "ready"
    assert any(step in e["message"] for e in r["errors"])


def test_failed_routing_step_escalates_the_change(tmp_path):
    r, _ = run("disclosed-changes", tmp_path, fail={"decide"})
    assert all(c["route"] == "escalate" for c in r["changes"] if c["blocking"])


def test_misaligned_evidence_is_counted_not_hidden(tmp_path):
    r, _ = run("quiet-changes", tmp_path, misalign=True)
    assert r["compare"]["unaligned"] > 0
    flagged = [c for c in r["changes"] if c["blocking"] and c["compare"]["relation"] == "conflict"]
    assert flagged and all(c["compare"]["aligned"] is False and "v2" not in c["compare"] for c in flagged)


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


def test_claims_skip_greetings_questions_and_sign_offs():
    claims = split_claims("Hi Dana,\n\nWe fixed typos. Nothing else changed.\n\nCan you sign by Friday?\n\nThanks,\nPriya")
    assert claims == ["We fixed typos.", "Nothing else changed."]


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
    from referee.textdoc import read_pdf
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
