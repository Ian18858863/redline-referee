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
