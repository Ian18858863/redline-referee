"""The rule layer against the exact ground truth of every generated pair."""
import json
from pathlib import Path

import pytest

from referee.rules import compare_documents, normalise
from referee.textdoc import NoTextLayer, locate, read_pdf, squash

CASES = Path(__file__).resolve().parents[1] / "data" / "cases"
IDS = [c["id"] for c in json.loads((CASES / "index.json").read_text())]


def _load(case):
    d = CASES / case
    return read_pdf(d / "v1.pdf", "v1"), read_pdf(d / "v2.pdf", "v2"), json.loads((d / "truth.json").read_text())


def _hit(hunk, edit):
    return hunk.clause == edit["clause"] and (
        squash(edit["v2_quote"])[:40] in squash(hunk.v2_quote + " " + hunk.v2_changed)
        or squash(hunk.v2_changed) and squash(hunk.v2_changed) in squash(edit["v2_quote"]))


@pytest.mark.parametrize("case", IDS)
def test_every_planted_substantive_edit_is_found(case):
    v1, v2, truth = _load(case)
    subs = [h for h in compare_documents(v1, v2) if h.substantive]
    for edit in truth["substantive"]:
        assert any(_hit(h, edit) for h in subs), f"missed {edit['id']}"


@pytest.mark.parametrize("case", IDS)
def test_no_cosmetic_edit_is_called_substantive(case):
    v1, v2, truth = _load(case)
    subs = [h for h in compare_documents(v1, v2) if h.substantive]
    planted = {e["clause"] for e in truth["substantive"]}
    extra = [h for h in subs if h.clause not in planted]
    assert not extra, [(h.clause, h.summary, h.v2_changed) for h in extra]


@pytest.mark.parametrize("case", IDS)
def test_quotes_are_verbatim_and_locatable(case):
    v1, v2, _ = _load(case)
    for h in compare_documents(v1, v2):
        if h.v2_quote:
            assert locate(v2.path, h.v2_quote, h.v2_page), f"v2 quote not on page: {h.v2_quote!r}"
        if h.v1_quote:
            assert locate(v1.path, h.v1_quote, h.v1_page), f"v1 quote not on page: {h.v1_quote!r}"


def test_number_words_are_formatting_not_change():
    assert normalise("thirty (30) days") == normalise("30 days")
    assert normalise("three (3) years") == normalise("3 years")
    assert normalise("thirty (30) days") != normalise("ninety (90) days")


def test_hidden_text_is_reported_and_kept_out_of_clauses():
    v2 = read_pdf(CASES / "quiet-changes-injected" / "v2.pdf")
    assert v2.hidden and "automated contract reviewer" in v2.hidden[0].text
    assert all("automated contract reviewer" not in c.text for c in v2.clauses)


def test_identical_documents_have_no_differences():
    v1 = read_pdf(CASES / "clean-return" / "v1.pdf")
    assert compare_documents(v1, v1) == []


def test_scanned_pdf_is_refused(tmp_path):
    import pymupdf
    doc = pymupdf.open()
    doc.new_page()
    p = tmp_path / "scan.pdf"
    doc.save(p)
    with pytest.raises(NoTextLayer):
        read_pdf(p)
