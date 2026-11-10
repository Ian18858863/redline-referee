"""Regressions found by external PDFs, plus two-sided evaluation accounting."""
import json
from pathlib import Path

import pymupdf
import pytest

from eval.run_external import transition, score
from eval.round_budget import verify_lock
from referee.textdoc import read_pdf
from referee.rules import compare_documents

ROOT = Path(__file__).resolve().parents[1]


def test_frozen_inputs_stay_unchanged_after_the_first_run():
    assert len(verify_lock()["cases"]) == 6


def test_inserted_font_is_read_at_its_baseline_not_its_ascender(tmp_path):
    path = tmp_path / "mixed.pdf"
    doc = pymupdf.open(); page = doc.new_page()
    page.insert_text((40, 100), "1. Confidentiality")
    before = "Recipient grants "
    changed = "a license"
    after = " under these rights to use the disclosed information."
    x = 40 + pymupdf.get_text_length(before, fontname="tiro", fontsize=11)
    y = x + pymupdf.get_text_length(changed, fontname="helv", fontsize=11) + 2
    page.insert_text((40, 140), before, fontname="tiro", fontsize=11)
    page.insert_text((y, 140), after, fontname="tiro", fontsize=11)
    page.insert_text((x, 140), changed, fontname="helv", fontsize=11)
    doc.save(path); doc.close()
    c = read_pdf(path, "v2").clause("1")
    assert "Recipient grants a license under these rights" in c.text


@pytest.mark.parametrize("cid,clauses", [("bonterms-changed", ["7", "8"]), ("bonterms-benign", ["3"])])
def test_native_pdf_edits_do_not_move_into_another_clause(cid, clauses):
    d = ROOT / "data/external" / cid
    a, b = read_pdf(d / "v1.pdf", "v1"), read_pdf(d / "v2.pdf", "v2")
    hunks = compare_documents(a, b)
    assert [h.clause for h in hunks] == clauses
    assert all(h.located for h in hunks)


def test_scorer_uses_both_sides_and_accepts_bounded_context():
    card = {"parts": [{"v1": {"quote": "is one calendar week", "changed": "one calendar week"},
                       "v2": {"quote": "is four calendar weeks", "changed": "four calendar weeks"}}]}
    assert transition(card, ("initial term is one calendar week", "initial term is four calendar weeks"))
    assert not transition(card, ("initial term is one calendar week", "initial term is eight calendar weeks"))


def test_missing_faithful_change_does_not_count_as_cleared():
    truth = {"id": "missing", "expected_status": "ready", "false_cover": False,
             "substantive": [], "faithful": [["standard of care", "level of care"]]}
    r = {"status": "incomplete", "changes": [], "errors": [{"step": "failed"}]}
    s = score(truth, r)
    assert not s["complete"] and not s["faithful"][0]["cleared"]


def test_round_budget_survives_restart_and_does_not_charge_replays(tmp_path, monkeypatch):
    from eval import round_budget as budget
    from referee.gateway import StepError
    monkeypatch.setattr(budget, "LEDGER", tmp_path / "ledger.jsonl")
    monkeypatch.setattr(budget, "SEND_LIMIT", 50)
    class Result(dict):
        status, credits, headers = 200, 7, {}
    class Client:
        def compare(self, *args, **kwargs):
            return Result(ok=True)
    gw = budget.RoundGateway(True, tmp_path / "recordings"); gw._client = Client()
    assert gw.call("compare", {"x": 1}, "first") == {"ok": True}
    gw.call("compare", {"x": 1}, "cached")
    assert gw.credits == 7 and len(budget.LEDGER.read_text().splitlines()) == 1
    again = budget.RoundGateway(True, tmp_path / "recordings"); again._client = Client()
    assert again.prior == 7
    again.call("compare", {"x": 1}, "replayed")
    assert again.credits == 0
    with pytest.raises(StepError, match="Round budget"):
        again.call("compare", {"x": 2}, "not sent")
    assert again.sent == 0


# ---- maintainer follow-up: fine print in both versions, and PDFs edited in place --------------------------
def test_a_pdf_edited_in_place_is_recognised_by_its_text_layer_and_a_clean_export_is_not():
    from referee.engine import drawn_out_of_order
    d = ROOT / "data/external/bonterms-benign"
    assert drawn_out_of_order(read_pdf(d / "v1.pdf", "v1")) == []          # the publisher's own file reads in order
    assert drawn_out_of_order(read_pdf(d / "v2.pdf", "v2")) == [1]         # the replacement words were drawn last
    for name in ("commonpaper-benign", "sixarm-changed"):
        e = ROOT / "data/external" / name
        assert drawn_out_of_order(read_pdf(e / "v2.pdf", "v2")) == []


def _replay(cid, tmp_path):
    from eval.round_budget import RoundGateway
    from referee.engine import review
    d = ROOT / "data/external" / cid
    gw = RoundGateway(False)
    r = review(d / "v1.pdf", d / "v2.pdf", (d / "cover.txt").read_text(), gw, tmp_path / cid,
               route=False, terms=False, concurrency=1)
    return r, gw


def test_verify_reads_an_edited_pdf_in_reading_order(tmp_path):
    """The sentence we sent is checked against the returned copy as it reads, not with a hole where the edit is."""
    r, gw = _replay("bonterms-benign", tmp_path)
    assert r["errors"] == [] and all(a["replayed"] for a in gw.audit)
    assert any(w.get("kind") == "edited_in_place" and "returned copy" in w["text"] for w in r["warnings"])
    change = next(c for c in r["changes"] if "level" in c["summary"])
    v = change["verify"]
    assert v["sent_against_returned"]["supported"] >= 0.8 and v["returned_against_sent"]["supported"] >= 0.8
    assert change["cls"] == "reworded" and not change["blocking"]


def test_publisher_fine_print_in_both_versions_is_listed_and_does_not_block(tmp_path):
    r, _ = _replay("bonterms-benign", tmp_path)
    assert [(h["text"], h["inherited"]) for h in r["hidden_text"]] == [("CC-BY-4.0", True)]
    assert r["status"] == "ready" and not any(x["level"] == "block" for x in r["reasons"])
    assert any(x["level"] == "note" and "also in the version we sent" in x["text"] for x in r["reasons"])


def test_substantive_edits_in_an_edited_pdf_are_still_held_and_the_false_note_still_blocks(tmp_path):
    r, _ = _replay("bonterms-changed", tmp_path)
    held = [c for c in r["changes"] if c["blocking"]]
    assert len(held) == 2 and all(c["readers"]["verify"] == "changed" and c["readers"]["compare"] == "conflict" for c in held)
    assert r["status"] == "blocked" and any(x["source"] == "Verify" for x in r["reasons"])


def test_a_disagreement_between_the_readers_stays_held(tmp_path):
    """Common Paper, the same two words: Verify reads a change, Compare the same meaning. Nothing was tuned to
    clear it; the stricter reading wins and the card says the meaning needs review."""
    r, _ = _replay("commonpaper-benign", tmp_path)
    change = next(c for c in r["changes"] if "level" in c["summary"])
    assert change["blocking"] and change["readers"] == {"rules": "substantive", "verify": "changed", "compare": "same", "agree": False}
