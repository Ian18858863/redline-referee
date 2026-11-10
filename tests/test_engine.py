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


def test_compare_clears_a_faithful_rewording(tmp_path):
    r, _ = run("quiet-changes", tmp_path)
    reworded = [c for c in r["changes"] if c["cls"] == "reworded"]
    assert len(reworded) == 1 and not reworded[0]["blocking"] and reworded[0]["clause"] == "4.2"
    pay = next(c for c in r["changes"] if c["clause"] == "4.2" and c["blocking"])
    assert pay["found_by"] == "both" and "90" in pay["summary"]


def test_compare_overrules_a_spelling_fix_that_changes_meaning(tmp_path):
    r, _ = run("disclosed-changes", tmp_path)
    c = next(c for c in r["changes"] if "minimum" in c["summary"])
    assert c["blocking"] and c["found_by"] == "compare"
    assert any(c["id"] in a["changes"] for a in r["ablation"] if a["endpoint"] == "Compare")


def test_without_compare_the_meaning_change_stays_a_spelling_fix(tmp_path):
    r, _ = run("disclosed-changes", tmp_path, compare_misses={"min_max"})
    c = next(c for c in r["changes"] if "minimum" in c["summary"])
    assert c["cls"] == "spelling" and not c["blocking"]


def test_a_rule_finding_compare_misses_stays_open(tmp_path):
    r, _ = run("quiet-changes", tmp_path, compare_misses={"liability_cap"})
    c = next(c for c in r["changes"] if c["clause"] == "9")
    assert c["blocking"] and c["found_by"] == "rules" and c["compare"]["relation"] == "silent"


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


@pytest.mark.parametrize("step", ["compare", "grounding", "rerank", "decide", "injection", "extract"])
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
    assert all(c["compare"]["aligned"] is False for c in r["changes"] if c["found_by"] == "both" and c["blocking"])


def test_marked_pdfs_from_compare_are_saved_and_rendered(tmp_path):
    r, _ = run("quiet-changes", tmp_path)
    for key in ("v1", "v2"):
        d = r["documents"][key]
        assert d["marked_pdf"] and (tmp_path / "quiet-changes" / d["marked_pdf"]).stat().st_size > 1000
        assert len(d["pages"]) == d["pages_count"] and (tmp_path / "quiet-changes" / d["pages"][0]["image"]).exists()


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
