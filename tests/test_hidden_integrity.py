"""Inherited fine print must not hide edits that the visible-text diff cannot see.

Synthetic PDF regressions exercise the real reader and review policy, with a
benign Guard stub. They test our deterministic safety boundary, not API accuracy.
"""
from types import SimpleNamespace

import pytest
from reportlab.pdfgen.canvas import Canvas

from referee.engine import classify_hidden, review
from referee.gateway import Gateway
from referee.reply import draft_reply
from referee.rules import compare_documents
from referee.textdoc import read_pdf
from tests.fake_clocked import FakeClocked


def hidden(*texts):
    return SimpleNamespace(hidden=[SimpleNamespace(text=t, page=1, reason="tiny") for t in texts])


@pytest.mark.parametrize("old,new", [
    ("$100", "€100"),
    ("€100", "£100"),
    ("Balance -100", "Balance +100"),
    ("Balance −100", "Balance 100"),
    ("Cap 1.0%", "Cap 10%"),
    ("Cap 10%", "Cap 10"),
    ("Limit <100", "Limit >100"),
    ("Limit ≤100", "Limit ≥100"),
    ("30 days", "30days"),
    ("Customer must approve", "customer must approve"),
    ("Notice: no renewal", "Notice no renewal"),
    ("付款三十天", "付款九十天"),
    ("Σ100", "Δ100"),
    ("Scope A²", "Scope A"),
    ("A B", "A\u200bB"),
    ("123", "１２３"),
])
def test_meaning_bearing_characters_cannot_be_erased_by_inheritance(old, new):
    assert [inherited for _, inherited in classify_hidden(hidden(old), hidden(new))] == [False]


@pytest.mark.parametrize("old,new", [
    ("CC-BY-4.0", "CC-BY-4.0"),
    ("$100", "$100"),
    ("付款三十天", "付款三十天"),
    ("©", "©"),
    (" net 30\n days ", "net 30 days"),
    ("net\u00a030 days", "net 30 days"),
    ("café", "cafe\u0301"),
])
def test_unchanged_print_and_layout_equivalents_are_inherited(old, new):
    assert [inherited for _, inherited in classify_hidden(hidden(old), hidden(new))] == [True]


def test_a_different_currency_cannot_consume_the_originals_matching_slot():
    got = classify_hidden(hidden("$100"), hidden("€100", "$100", "$100"))
    assert [inherited for _, inherited in got] == [False, True, False]


def make_pdf(path, note):
    canvas = Canvas(str(path), invariant=1)
    canvas.setFont("Helvetica-Bold", 12)
    canvas.drawString(50, 700, "1. Payment")
    canvas.setFont("Helvetica", 11)
    canvas.drawString(50, 675, "Customer shall pay invoices within thirty days of receipt.")
    canvas.drawString(50, 655, "Both parties shall perform their obligations under this agreement.")
    canvas.setFont("Helvetica", 2)
    canvas.drawString(50, 80, note)
    canvas.save()


EMPTY_TRUTH = {"substantive": [], "cosmetic": [], "letter_claims": [], "injection": False}


@pytest.mark.parametrize("old,new", [
    ("$100", "€100"),
    ("Balance -100", "Balance +100"),
    ("Cap 1.0%", "Cap 10%"),
    ("Limit <100", "Limit >100"),
])
def test_changed_concealed_values_block_even_when_guard_is_benign(tmp_path, old, new):
    v1, v2 = tmp_path / "sent.pdf", tmp_path / "returned.pdf"
    make_pdf(v1, old); make_pdf(v2, new)
    a, b = read_pdf(v1, "v1"), read_pdf(v2, "v2")
    assert [h.text for h in a.hidden] == [old]
    assert [h.text for h in b.hidden] == [new]
    assert not compare_documents(a, b)  # the change is outside the visible-clause diff
    client = FakeClocked(EMPTY_TRUTH)
    result = review(v1, v2, "", Gateway("live", client=client), tmp_path / "review", route=False, terms=False)
    assert not result["errors"] and result["status"] == "blocked"
    assert result["hidden_text"][0]["text"] == new and result["hidden_text"][0]["inherited"] is False
    assert any(r["source"] == "Rules" and r["level"] == "block" for r in result["reasons"])
    assert all(g["ran"] and not g["flagged"] for g in result["guard"])
    assert "injection" in client.calls  # Guard still inspects the returned PDF
    reply_client = FakeClocked(EMPTY_TRUTH)
    reply = draft_reply(result, {}, Gateway("live", client=reply_client))
    assert "cannot sign" in reply["text"].lower() and "countersign" not in reply["text"].lower()
    assert not reply_client.calls  # the blocked-state reply needs no generated claims


def test_unchanged_tiny_amount_stays_listed_and_does_not_block(tmp_path):
    v1, v2 = tmp_path / "sent.pdf", tmp_path / "returned.pdf"
    make_pdf(v1, "$100"); make_pdf(v2, "$100")
    result = review(v1, v2, "", Gateway("live", client=FakeClocked(EMPTY_TRUTH)), tmp_path / "review", route=False, terms=False)
    assert not result["errors"] and result["status"] == "ready"
    assert result["hidden_text"][0]["inherited"] is True
    assert any(r["level"] == "note" and r["source"] == "Rules" for r in result["reasons"])
