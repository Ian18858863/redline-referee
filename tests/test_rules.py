"""The rule layer against the exact ground truth of every generated pair."""
import json
from pathlib import Path

import pytest

from referee.rules import compare_documents, normalise
from referee.textdoc import NoTextLayer, locate, read_pdf, squash

CASES = Path(__file__).resolve().parents[1] / "data" / "cases"
# The hard set is about meaning, which the rule layer does not read; it is measured in eval/run_hard.py.
IDS = [c["id"] for c in json.loads((CASES / "index.json").read_text()) if c["group"] != "hard"]


# Documented limit of a layer that cannot read meaning: a faithful rewording looks like a change. Verify and
# Compare read those. ("maximum" -> "minimum" used to be the other limit: it passed as a spelling fix. Since the
# spelling rule asks a dictionary, two real words are a change of wording and the rules find it on their own.)
RULES_BLIND: set[str] = set()
RULES_OVERCALL = {"reword_dispute"}


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
        if edit["id"] in RULES_BLIND:
            continue
        assert any(_hit(h, edit) for h in subs), f"missed {edit['id']}"


@pytest.mark.parametrize("case", IDS)
def test_no_cosmetic_edit_is_called_substantive(case):
    v1, v2, truth = _load(case)
    if truth.get("wrong_file"):
        pytest.skip("a different agreement came back: nothing in it is cosmetic")
    subs = [h for h in compare_documents(v1, v2) if h.substantive]
    planted = {e["clause"] for e in truth["substantive"]}
    planted |= {e["clause"] for e in truth["cosmetic"] if e["id"] in RULES_OVERCALL or e["kind"] == "wording"}
    extra = [h for h in subs if h.clause not in planted]
    assert not extra, [(h.clause, h.summary, h.v2_changed) for h in extra]


@pytest.mark.parametrize("case", IDS)
def test_quotes_are_verbatim_and_locatable(case):
    v1, v2, _ = _load(case)
    for h in compare_documents(v1, v2):
        assert h.located and h.v1_located and h.v2_located
        if h.v2_quote:
            assert locate(v2.path, h.v2_quote, h.v2_page), f"v2 quote not on page: {h.v2_quote!r}"
        if h.v1_quote:
            assert locate(v1.path, h.v1_quote, h.v1_page), f"v1 quote not on page: {h.v1_quote!r}"


def test_number_words_are_formatting_not_change():
    assert normalise("thirty (30) days") == normalise("30 days")
    assert normalise("three (3) years") == normalise("3 years")
    assert normalise("thirty (30) days") != normalise("ninety (90) days")
    assert normalise("thirty (30) days") != normalise("ninety (30) days")      # the words and the digits must agree
    assert normalise("ninety (30) days") != normalise("30 days")


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


def test_one_word_that_flips_the_meaning_is_substantive_and_a_rewording_is_overcalled():
    """Two real words are a change of wording, never a spelling fix; a faithful rewording still reads as a change."""
    v1, v2, _ = _load("disclosed-changes")
    hunks = compare_documents(v1, v2)
    flipped = [h for h in hunks if "minimum" in h.v2_changed]
    assert [(h.cls, h.kind, h.summary) for h in flipped] == [("substantive", "wording", "“maximum” → “minimum”")]
    assert [h.v2_changed for h in hunks if h.cls == "spelling"] == ["written"]        # a real typo fix stays a typo fix
    v1, v2, _ = _load("quiet-changes")
    hunks = compare_documents(v1, v2)
    assert any(h.substantive and h.kind == "wording" and "tell Provider" in h.v2_quote + h.v2_changed for h in hunks)


def test_every_clause_has_a_stable_id_and_hunks_point_at_it():
    v1, v2, _ = _load("quiet-changes")
    assert [c.uid for c in v2.clauses] == [f"c{i:03d}" for i in range(1, len(v2.clauses) + 1)]
    assert v2.by_uid("c001").number == "0" and v2.clause("4.2").uid == v2.by_uid(v2.clause("4.2").uid).uid
    for h in compare_documents(v1, v2):
        assert v1.by_uid(h.v1_uid).number == h.clause == v2.by_uid(h.v2_uid).number


@pytest.mark.parametrize("case,added,removed,renumbered", [
    ("struct-01", ["Most Favoured Customer"], [], "Renumbered 14 → 15"),
    ("struct-02", [], ["Late Payment"], "Renumbered 4.4 → 4.3"),
    ("struct-03", ["Most Favoured Customer"], ["Audit"], "Renumbered 13 → 12"),
])
def test_added_and_removed_clauses_are_reported_as_such(case, added, removed, renumbered):
    """A clause slipped in or taken out shifts every later number. That is one added or removed clause plus a note."""
    if not (CASES / case).exists():
        pytest.skip("structure cases not generated")
    v1, v2, truth = _load(case)
    hunks = compare_documents(v1, v2)
    assert [h.title for h in hunks if h.kind == "added"] == added and [h.title for h in hunks if h.kind == "removed"] == removed
    notes = [h for h in hunks if h.kind == "renumbered"]
    assert len(notes) == 1 and notes[0].cls == "formatting" and notes[0].summary.startswith(renumbered)
    planted = {e["clause"] for e in truth["substantive"]}
    assert {h.clause for h in hunks if h.substantive} == planted
    assert not any(h.summary == "Heading changed" for h in hunks)
