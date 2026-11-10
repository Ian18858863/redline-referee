"""The rule layer on layouts other than the house style.

Every case here was a way to break "any two text-layer PDFs": a heading style that was not recognised, a
wrapped line read as a heading, columns, tables, headers and footers, other languages, symbols, hidden text.
The PDFs are generated on the fly (tests/layout_fixtures.py). Each test plants a change and asserts that it
is reported as substantive (or, for a cosmetic one, that it is not) and that the quotes can be found on the page.
"""
import time

import pymupdf
import pytest
from reportlab.lib import colors
from reportlab.platypus import PageBreak

from referee.rules import compare_documents, is_english_word, normalise, sentences, words_value
from referee.textdoc import NoTextLayer, locate, read_pdf
from tests import layout_fixtures as lf
from tests.layout_fixtures import BASE, HEAD, P, apply, build, canvas_doc, clauses_flow, edit, styled_pair


@pytest.fixture(scope="module")
def tmp(tmp_path_factory):
    return tmp_path_factory.mktemp("layouts")


def run(a, b):
    d1, d2 = read_pdf(a, "v1"), read_pdf(b, "v2")
    return d1, d2, compare_documents(d1, d2)


def subs(hunks):
    return [h for h in hunks if h.substantive]


def assert_locatable(hunks, a, b):
    for h in hunks:
        assert h.located, (h.clause, h.summary, h.v1_quote, h.v2_quote)
        if h.v1_quote:
            assert locate(a, h.v1_quote, h.v1_page or None), f"v1 quote not on the page: {h.v1_quote!r}"
        if h.v2_quote:
            assert locate(b, h.v2_quote, h.v2_page or None), f"v2 quote not on the page: {h.v2_quote!r}"


def assert_two_planted(d1, d2, hunks, a, b, clause4, clause8):
    """The standard pair: 30 → 60 days in the payment clause, Delaware → Nevada in the governing-law clause."""
    s = subs(hunks)
    assert [(h.clause, h.kind) for h in s] == [(clause4, "duration"), (clause8, "wording")], [(h.clause, h.summary) for h in hunks]
    assert s[0].summary == "30 days → 60 days" and "Nevada" in s[1].summary
    assert len(hunks) == 2
    assert_locatable(hunks, a, b)


# ---------------------------------------------------------------- headings

def test_a_wrapped_line_that_starts_with_a_number_is_not_a_heading(tmp):
    def doc(n):
        return ["4. Payment Terms", "Customer shall pay each undisputed invoice issued under this Agreement within",
                f"{n} days of the invoice date.", "5. Late Payment", "Overdue amounts accrue interest at the rate of 1.5% per month until paid."]
    a, b = canvas_doc(tmp / "wrap_v1.pdf", doc(30)), canvas_doc(tmp / "wrap_v2.pdf", doc(90))
    d1, d2, hunks = run(a, b)
    assert [c.number for c in d1.clauses] == ["4", "5"] == [c.number for c in d2.clauses]
    assert "30 days of the invoice date." in d1.clause("4").text
    assert [(h.clause, h.kind, h.summary) for h in subs(hunks)] == [("4", "duration", "30 days → 90 days")]
    assert_locatable(hunks, a, b)


def test_a_wrapped_duration_and_a_wrapped_cross_reference_are_body_text(tmp):
    def doc(months, sec):
        return ["8. Non-Solicitation", "Neither party shall solicit the employees of the other party during the term and for",
                f"{months} months after termination.", "9. Survival",
                "The obligations of the parties under this clause and under the confidentiality provisions in",
                f"Section {sec} above shall survive termination."]
    a, b = canvas_doc(tmp / "wrap2_v1.pdf", doc(12, 8)), canvas_doc(tmp / "wrap2_v2.pdf", doc(36, 2))
    d1, _, hunks = run(a, b)
    assert [c.number for c in d1.clauses] == ["8", "9"]
    assert [(h.clause, h.kind, h.summary) for h in subs(hunks)] == [("8", "duration", "12 months → 36 months"), ("9", "number", "8 → 2")]
    assert_locatable(hunks, a, b)


STYLES = [
    ("bare", "{n} {t}", False, "4", "8"),
    ("dot", "{n}. {t}", False, "4", "8"),
    ("section-dash", "Section {n} - {t}", False, "4", "8"),
    ("section-dot", "Section {n}. {t}", False, "4", "8"),
    ("section-caps", "SECTION {n} – {T}", False, "4", "8"),
    ("article-colon", "Article {n}: {t}", False, "4", "8"),
    ("paragraph-sign", "§ {n} {t}", False, "4", "8"),
    ("clause", "Clause {n} {t}", False, "4", "8"),
    ("sub", "Section {n}.1 {t}", False, "4.1", "8.1"),
    ("inline", "{n}. {t}.", True, "4", "8"),
    ("inline-section", "Section {n}. {t}.", True, "4", "8"),
    ("inline-colon", "{n}.1 {t}:", True, "4.1", "8.1"),
]


@pytest.mark.parametrize("tag,fmt,inline,c4,c8", STYLES, ids=[s[0] for s in STYLES])
def test_heading_styles(tmp, tag, fmt, inline, c4, c8):
    a, b = styled_pair(tmp, f"style_{tag}", fmt, inline)
    d1, d2, hunks = run(a, b)
    assert d1.structure == "numbered"
    assert len(d1.clauses) == 8 and [c.title.lower() for c in d1.clauses] == [t.lower() for _, t, _ in BASE]
    assert d1.clauses[3].number == c4 and d1.clauses[3].text == BASE[3][2]
    assert [c.uid for c in d1.clauses] == [f"c{i:03d}" for i in range(1, 9)]
    assert_two_planted(d1, d2, hunks, a, b, c4, c8)


def test_roman_numeral_articles(tmp):
    rb = [(lf.ROMAN[i], t, x) for i, (n, t, x) in enumerate(BASE)]
    rb2 = [(lf.ROMAN[i], t, x) for i, (n, t, x) in enumerate(lf.planted(BASE))]
    a, b = styled_pair(tmp, "roman", "Article {n}. {t}", clauses=rb, v2=rb2)
    d1, d2, hunks = run(a, b)
    assert [c.number for c in d1.clauses] == lf.ROMAN[:8]
    assert_two_planted(d1, d2, hunks, a, b, "IV", "VIII")


def test_article_with_its_title_on_the_next_line(tmp):
    def flow(clauses):
        out = []
        for i, (n, t, x) in enumerate(clauses):
            out += [P(f"Article {lf.ROMAN[i]}", HEAD), P(t.upper(), HEAD), P(x)]
        return out
    a, b = build(tmp / "art_v1.pdf", flow(BASE)), build(tmp / "art_v2.pdf", flow(lf.planted(BASE)))
    d1, d2, hunks = run(a, b)
    assert [(c.number, c.title) for c in d1.clauses][3] == ("IV", "PAYMENT TERMS")
    assert_two_planted(d1, d2, hunks, a, b, "IV", "VIII")


def test_numbers_above_99_and_three_level_numbers(tmp):
    big = [(str(i + 96), t, x) for i, (n, t, x) in enumerate(BASE)]
    big2 = edit(edit(big, "99", "thirty (30) days", "sixty (60) days"), "103", "State of Delaware", "State of Nevada")
    a, b = styled_pair(tmp, "big", "{n}. {t}", clauses=big, v2=big2)
    d1, d2, hunks = run(a, b)
    assert [c.number for c in d1.clauses] == [str(n) for n in range(96, 104)]
    assert_two_planted(d1, d2, hunks, a, b, "99", "103")
    deep = [(f"4.2.{i + 1}", t, x) for i, (n, t, x) in enumerate(BASE)]
    deep2 = edit(edit(deep, "4.2.4", "thirty (30) days", "sixty (60) days"), "4.2.8", "State of Delaware", "State of Nevada")
    a, b = styled_pair(tmp, "deep", "{n} {t}", clauses=deep, v2=deep2)
    d1, d2, hunks = run(a, b)
    assert [c.number for c in d1.clauses] == [f"4.2.{i}" for i in range(1, 9)]
    assert_two_planted(d1, d2, hunks, a, b, "4.2.4", "4.2.8")


def test_numbered_paragraphs_without_titles(tmp):
    para = [(f"1.{i + 1}", "", x) for i, (n, t, x) in enumerate(BASE)]
    para2 = [(f"1.{i + 1}", "", x) for i, (n, t, x) in enumerate(lf.planted(BASE))]
    a, b = styled_pair(tmp, "para", "{n}", True, clauses=para, v2=para2)
    d1, d2, hunks = run(a, b)
    assert [c.number for c in d1.clauses] == [f"1.{i}" for i in range(1, 9)] and d1.clauses[7].text == BASE[7][2]
    assert_two_planted(d1, d2, hunks, a, b, "1.4", "1.8")


@pytest.mark.parametrize("head", [lf.HEAD, lf.PLAIN_HEAD], ids=["bold", "plain"])
def test_unnumbered_headings(tmp, head):
    a, b = styled_pair(tmp, f"untitled_{head.name}", "{t}", head=head)
    d1, d2, hunks = run(a, b)
    assert d1.structure == "headings"
    assert [(c.number, c.title) for c in d1.clauses] == [(f"#{i + 1}", t) for i, (_, t, _) in enumerate(BASE)]
    assert_two_planted(d1, d2, hunks, a, b, "#4", "#8")


def test_no_structure_at_all_is_split_into_paragraphs(tmp):
    a, b = styled_pair(tmp, "flat", "")
    d1, d2, hunks = run(a, b)
    assert d1.structure == "paragraphs"
    assert [c.number for c in d1.clauses] == [f"¶ {i}" for i in range(1, 9)]
    assert all(c.number != "0" and c.title != "Preamble" for c in d1.clauses)
    assert [c.text for c in d1.clauses] == [x for _, _, x in BASE]
    assert_two_planted(d1, d2, hunks, a, b, "¶ 4", "¶ 8")


def test_list_items_inside_a_clause_do_not_restart_the_numbering(tmp):
    def doc(days, sec):
        return ["3. Deliverables", "Provider shall deliver the following items to Customer:", "1. Monthly usage reports",
                "2. Security audit summaries", f"3. Incident reports within {days} days", "4. Survival",
                "The obligations of the parties under this clause and under the confidentiality provisions in",
                f"Section {sec} above shall survive termination.", "5. Fees", "Customer shall pay the fees set out in the order form."]
    a, b = canvas_doc(tmp / "list_v1.pdf", doc(5, 8)), canvas_doc(tmp / "list_v2.pdf", doc(45, 2))
    d1, _, hunks = run(a, b)
    assert [(c.number, c.title) for c in d1.clauses] == [("3", "Deliverables"), ("4", "Survival"), ("5", "Fees")]
    assert [(h.clause, h.kind, h.summary) for h in subs(hunks)] == [("3", "duration", "5 days → 45 days"), ("4", "number", "8 → 2")]
    assert_locatable(hunks, a, b)


# --------------------------------------------------------------- structure

AUDIT = ("4", "Audit Rights", "Customer may audit Provider's facilities and records at any time without notice and at Provider's cost.")


def test_inserted_clause_with_renumbering(tmp):
    ins = BASE[:3] + [AUDIT] + [(str(int(n) + 1), t, x) for n, t, x in BASE[3:]]
    a, b = styled_pair(tmp, "ins", "{n}. {t}", v2=ins)
    d1, d2, hunks = run(a, b)
    s = subs(hunks)
    assert [(h.kind, h.clause, h.title) for h in s] == [("added", "4", "Audit Rights")]
    assert s[0].v1_uid is None and d2.by_uid(s[0].v2_uid).title == "Audit Rights" and s[0].v2_sentence == AUDIT[2]
    notes = [h for h in hunks if h.kind == "renumbered"]
    assert len(notes) == 1 and notes[0].cls == "formatting" and notes[0].summary.startswith("Renumbered 4 → 5")
    assert [r[1] + ">" + r[3] for r in notes[0].extra["renumbered"]] == ["4>5", "5>6", "6>7", "7>8", "8>9"]
    assert len(hunks) == 2
    assert_locatable(hunks, a, b)


def test_deleted_clause_with_renumbering(tmp):
    dele = BASE[:5] + [(str(int(n) - 1), t, x) for n, t, x in BASE[6:]]
    a, b = styled_pair(tmp, "del", "{n}. {t}", v2=dele)
    d1, d2, hunks = run(a, b)
    s = subs(hunks)
    assert [(h.kind, h.clause, h.title) for h in s] == [("removed", "6", "Limitation of Liability")]
    assert s[0].v2_uid is None and d1.by_uid(s[0].v1_uid).number == "6"
    notes = [h for h in hunks if h.kind == "renumbered"]
    assert len(notes) == 1 and notes[0].summary.startswith("Renumbered 7 → 6") and notes[0].title == "Assignment"
    assert len(hunks) == 2
    assert_locatable(hunks, a, b)


def test_moved_clause(tmp):
    moved = BASE[:5] + [("6", BASE[6][1], BASE[6][2]), ("7", BASE[7][1], BASE[7][2]), ("8", BASE[5][1], BASE[5][2])]
    a, b = styled_pair(tmp, "mov", "{n}. {t}", v2=moved)
    _, _, hunks = run(a, b)
    assert subs(hunks) == []
    assert [(h.kind, h.title, h.summary) for h in hunks if h.kind == "moved"] == [("moved", "Limitation of Liability", "Clause moved: 6 → 8")]
    assert {h.kind for h in hunks} == {"moved", "renumbered"} and all(h.cls == "formatting" for h in hunks)
    assert_locatable(hunks, a, b)


def test_clause_replaced_under_the_same_number(tmp):
    repl = BASE[:5] + [("6", "Publicity", "Provider may use Customer's name and logo in its marketing materials and on its website without further consent from Customer.")] + BASE[6:]
    a, b = styled_pair(tmp, "repl", "{n}. {t}", v2=repl)
    _, _, hunks = run(a, b)
    assert sorted((h.kind, h.title) for h in hunks) == [("added", "Publicity"), ("removed", "Limitation of Liability")]


def test_appendix_that_restarts_at_1(tmp):
    sched = [("1", "Fees Schedule", "The fee for the Standard tier is $5,000 per month."),
             ("2", "Support Hours", "Support is available from 9 a.m. to 5 p.m. Eastern Time on business days.")]

    def flow(s):
        return clauses_flow(BASE, "{n}. {t}") + [PageBreak(), P("SCHEDULE A", HEAD)] + clauses_flow(s, "{n}. {t}")
    a, b = build(tmp / "app_v1.pdf", flow(sched)), build(tmp / "app_v2.pdf", flow(edit(sched, "1", "$5,000", "$9,000")))
    d1, d2, hunks = run(a, b)
    assert len({c.uid for c in d2.clauses}) == len(d2.clauses) == 11
    assert d2.clause("1").title == "Services"                       # the body's clause 1 is still the first match
    assert [(h.kind, h.summary) for h in hunks] == [("money", "$5,000 → $9,000")]
    hit = d2.by_uid(hunks[0].v2_uid)
    assert hit.title == "Fees Schedule" and hit.page == 2 and "$9,000" in hit.text and hit.number == "Schedule A 1"
    assert d1.by_uid(hunks[0].v1_uid).title == "Fees Schedule" and d2.by_uid("nope") is None and d2.by_uid(None) is None
    assert_locatable(hunks, a, b)


# ------------------------------------------------------------------ layout

def test_two_columns_are_read_column_by_column(tmp):
    u = lf.unique_clauses(30, sentences=3)
    u2 = edit(u, "4", u[3][2].split(". ")[1], "Customer may terminate this Agreement at any time without cause")
    u2 = edit(u2, "22", u[21][2].split(". ")[0], "Provider shall pay liquidated damages of $900,000 on demand")
    a = build(tmp / "cols_v1.pdf", clauses_flow(u, "{n}. {t}"), cols=2)
    b = build(tmp / "cols_v2.pdf", clauses_flow(u2, "{n}. {t}"), cols=2)
    d1, d2, hunks = run(a, b)
    assert [c.number for c in d1.clauses] == [str(i) for i in range(1, 31)]
    assert [c.text for c in d1.clauses] == [x for _, _, x in u]
    assert sorted({h.clause for h in subs(hunks)}, key=int) == ["4", "22"] and {h.clause for h in hunks} == {"4", "22"}
    assert any(h.kind == "money" and "$900,000" in h.summary for h in hunks)
    assert_locatable(hunks, a, b)


def test_two_columns_under_a_running_header(tmp):
    u = lf.unique_clauses(16, sentences=3)
    p = build(tmp / "cols_hdr.pdf", clauses_flow(u, "{n}. {t}"), cols=2,
              on_page=lf.header_footer("MASTER SERVICES AGREEMENT - CONFIDENTIAL", footer="Page {n}"))
    d = read_pdf(p)
    assert [c.text for c in d.clauses] == [x for _, _, x in u]
    assert {m.text for m in d.margins} >= {"MASTER SERVICES AGREEMENT - CONFIDENTIAL", "Page 1"}


def test_changed_table_cell_is_reported_with_its_row(tmp):
    a = lf.pricing_table(tmp / "tab_v1.pdf")
    b = lf.pricing_table(tmp / "tab_v2.pdf", premium="$21,000")
    d1, d2, hunks = run(a, b)
    assert d1.clause("5").title == "Pricing Schedule" and d1.clause("5").text.startswith("Tier Monthly fee Users Response time Standard $5,000")
    assert [(h.clause, h.cls, h.kind, h.summary) for h in hunks] == [("5", "substantive", "money", "$12,000 → $21,000")]
    h = hunks[0]
    assert (h.v1_quote, h.v2_quote) == ("Premium $12,000 200 4 hours", "Premium $21,000 200 4 hours")
    assert h.extra["table_row"]["header"] == "Tier Monthly fee Users Response time"
    assert_locatable(hunks, a, b)
    c = lf.pricing_table(tmp / "tab_v3.pdf", response="9 hours")
    _, _, hunks = run(a, c)
    assert [(h.clause, h.kind, h.summary, h.v2_quote) for h in hunks] == [("5", "duration", "4 hours → 9 hours", "Premium $12,000 200 9 hours")]
    assert_locatable(hunks, a, c)


def test_bare_page_number_footer_does_not_delete_numeric_cells(tmp):
    a, b = lf.cap_table(tmp / "cap_v1.pdf", "4"), lf.cap_table(tmp / "cap_v2.pdf", "40")
    d1, d2, hunks = run(a, b)
    assert d1.clause("2").text.endswith("Year Fee cap (%) 1 3 2 4 3 5") and d2.clause("2").text.endswith("Year Fee cap (%) 1 3 2 40 3 5")
    assert [m.text for m in d1.margins] == ["1", "2", "3"] and all(m.where == "bottom" for m in d1.margins)
    assert [(h.clause, h.cls, h.kind, h.summary) for h in hunks] == [("2", "substantive", "number", "4 → 40")]
    assert hunks[0].v2_page == 2 and hunks[0].extra["table_row"]["header"] == "Year Fee cap (%)"
    assert_locatable(hunks, a, b)


def test_one_page_document_with_a_differing_header(tmp):
    def doc(name, header):
        return build(tmp / name, clauses_flow(BASE[:3], "{n}. {t}"), on_page=lf.header_footer(header))
    a, b = doc("hdr1_v1.pdf", "Draft v1 - 2 March 2026"), doc("hdr1_v2.pdf", "Draft v2 - 9 March 2026")
    d1, d2, hunks = run(a, b)
    assert d1.pages == 1 and all("Draft" not in c.text for c in d1.clauses + d2.clauses)
    assert subs(hunks) == []
    assert [(h.cls, h.kind, h.v1_quote, h.v2_quote) for h in hunks] == [("administrative", "header", "Draft v1 - 2 March 2026", "Draft v2 - 9 March 2026")]
    assert_locatable(hunks, a, b)


def test_a_footer_that_states_a_term_is_not_an_administrative_note(tmp):
    def doc(name, footer):
        return build(tmp / name, clauses_flow(BASE[:3], "{n}. {t}"), on_page=lf.page_footer(footer))
    a = doc("foot_v1.pdf", "Larkspur MSA - confidential")
    b = doc("foot_v2.pdf", "All fees are subject to a 10% annual surcharge.")
    d1, d2, hunks = run(a, b)
    assert [m.text for m in d2.margins] == ["All fees are subject to a 10% annual surcharge."]
    s = subs(hunks)
    assert [(h.clause, h.kind, h.v2_quote) for h in s] == [("header", "added", "All fees are subject to a 10% annual surcharge.")]
    assert [(h.cls, h.kind, h.v1_quote) for h in hunks if not h.substantive] == [("administrative", "header", "Larkspur MSA - confidential")]
    assert_locatable(hunks, a, b)


def test_two_page_document_with_a_header_on_the_second_page_only(tmp):
    two = [(str(i + 1), t, x + " " + x + " " + x) for i, (n, t, x) in enumerate(BASE)] + [("9", "Notices", BASE[0][2] * 6)]

    def doc(name, header):
        return build(tmp / name, clauses_flow(two, "{n}. {t}"), on_page=lf.header_footer(header, first_page=False))
    a, b = doc("hdr2_v1.pdf", "Draft of 2 March 2026 - Larkspur confidential"), doc("hdr2_v2.pdf", "Draft of 9 March 2026 - Brightwater comments")
    d1, d2, hunks = run(a, b)
    assert d1.pages == 2 and subs(hunks) == [] and [h.kind for h in hunks] == ["header"]
    assert all("Draft of" not in c.text for c in d1.clauses + d2.clauses)


def test_running_header_and_page_x_of_y_on_a_long_document(tmp):
    big = [(str(i + 1), f"{t} {i + 1}", x + f" Item {i + 1} applies.") for i, (n, t, x) in enumerate(BASE * 3)]

    def doc(name, clauses, header):
        return build(tmp / name, clauses_flow(clauses, "{n}. {t}"), on_page=lf.header_footer(header, footer="Page {n} of 2"))
    a = doc("hdr3_v1.pdf", big, "Larkspur MSA - Draft v1 - 2 March 2026")
    b = doc("hdr3_v2.pdf", edit(big, "12", "thirty (30) days", "sixty (60) days"), "Larkspur MSA - Customer comments v2 - 9 March 2026")
    d1, d2, hunks = run(a, b)
    assert d1.pages >= 2 and all("Larkspur MSA" not in c.text and "Page 1" not in c.text for c in d1.clauses)
    assert [(h.clause, h.kind) for h in subs(hunks)] == [("12", "duration")]
    assert [h.kind for h in hunks if not h.substantive] == ["header"]


def test_a_body_line_that_repeats_on_both_pages_is_not_a_header(tmp):
    def doc(name, d1_, d2_):
        return build(tmp / name, [P("1. Payment", HEAD), P("Customer shall pay each invoice"), P(f"within {d1_} days of the invoice date."),
                                  PageBreak(), P("2. Refunds", HEAD), P("Provider shall refund each overpayment"),
                                  P(f"within {d2_} days of the invoice date.")])
    a, b = doc("rep_v1.pdf", 30, 30), doc("rep_v2.pdf", 90, 5)
    d1, _, hunks = run(a, b)
    assert d1.margins == [] and d1.clause("1").text.endswith("within 30 days of the invoice date.")
    assert [(h.clause, h.summary) for h in subs(hunks)] == [("1", "30 days → 90 days"), ("2", "30 days → 5 days")]
    assert_locatable(hunks, a, b)


def test_hyphenated_line_breaks(tmp):
    a = lf.hyphenated(tmp / "hy_v1.pdf")
    _, _, hunks = run(a, lf.hyphenated(tmp / "hy_v2.pdf", "not pay"))
    assert len(subs(hunks)) == 1 and "shall not pay" in hunks[0].v2_quote
    assert_locatable(hunks, a, tmp / "hy_v2.pdf")
    _, _, hunks = run(a, lf.hyphenated(tmp / "hy_v3.pdf", hyphens=False))          # same words, re-flowed
    assert hunks and all(h.cls == "formatting" for h in hunks)


def test_change_on_the_first_line_after_a_page_break(tmp):
    def doc(n):
        return ["1. Payment", "Customer shall pay each undisputed invoice issued by Provider under this Agreement within", "\f",
                f"{n} calendar days following receipt of the invoice, without set-off or counterclaim of any kind.",
                "2. Taxes", "Fees are exclusive of all taxes."]
    a = canvas_doc(tmp / "brk_v1.pdf", doc("forty-five (45)"), footer="Page {n}")
    b = canvas_doc(tmp / "brk_v2.pdf", doc("one hundred and twenty (120)"), footer="Page {n}")
    d1, _, hunks = run(a, b)
    assert "Page 1" not in d1.clause("1").text and len(subs(hunks)) == 1 and hunks[0].summary == "45 → 120"
    found = locate(a, hunks[0].v1_quote, hunks[0].v1_page)
    assert [f["page"] for f in found] == [1, 2] and all(f["rects"] for f in found)       # the quote runs over the break
    assert hunks[0].located


# --------------------------------------------------------------- languages

def test_spanish_contract(tmp):
    ub, uh = lf.uni_styles()
    a, b = styled_pair(tmp, "es", "{n}. {t}", clauses=lf.ES, v2=apply(lf.ES, lf.ES_EDITS), style=ub, head=uh)
    d1, _, hunks = run(a, b)
    assert [c.title for c in d1.clauses] == [t for _, t, _ in lf.ES]
    assert [h.clause for h in hunks] == ["2", "3", "4", "5"] and all(h.substantive for h in hunks)
    assert_locatable(hunks, a, b)


def test_german_contract_with_paragraph_signs(tmp):
    ub, uh = lf.uni_styles()
    a, b = styled_pair(tmp, "de", "§ {n} {t}", clauses=lf.DE, v2=apply(lf.DE, lf.DE_EDITS), style=ub, head=uh)
    d1, _, hunks = run(a, b)
    assert [(c.number, c.title) for c in d1.clauses] == [(n, t) for n, t, _ in lf.DE]
    assert [h.clause for h in hunks] == ["3", "4", "5", "5"] and all(h.substantive for h in hunks)
    assert any("Möller" in h.summary for h in hunks) and all(h.cls != "spelling" for h in hunks)
    assert_locatable(hunks, a, b)


@pytest.mark.parametrize("fmt,numbers", [("{n}. {t}", ["1", "2", "3", "4", "5"]), ("第{n}条 {t}", ["第一条", "第二条", "第三条", "第四条", "第五条"])],
                         ids=["arabic", "di-tiao"])
def test_chinese_contract(tmp, fmt, numbers):
    zb, zh = lf.uni_styles("STSong-Light")
    src = lf.ZH if fmt.startswith("{n}") else [(lf.CN[i], t, x) for i, (n, t, x) in enumerate(lf.ZH)]
    edits = lf.ZH_EDITS if fmt.startswith("{n}") else [(lf.CN[int(n) - 1], o, w) for n, o, w in lf.ZH_EDITS]
    a, b = styled_pair(tmp, f"zh{len(fmt)}", fmt, clauses=src, v2=apply(src, edits), style=zb, head=zh)
    d1, _, hunks = run(a, b)
    assert [c.number for c in d1.clauses] == numbers and [c.title for c in d1.clauses] == [t for _, t, _ in lf.ZH]
    assert [c.text for c in d1.clauses] == [x for _, _, x in lf.ZH]                 # wrapped lines join without a space
    assert [h.clause for h in hunks] == [numbers[2], numbers[3], numbers[3], numbers[4]] and all(h.substantive for h in hunks)
    assert hunks[0].kind == "number" and hunks[0].summary == "三十 → 九十"
    assert_locatable(hunks, a, b)


def test_a_one_word_edit_in_another_language_is_never_a_spelling_fix(tmp):
    ub, uh = lf.uni_styles()
    a, b = styled_pair(tmp, "es1", "{n}. {t}", clauses=lf.ES, v2=edit(lf.ES, "4", "consentimiento", "consentimento"), style=ub, head=uh)
    _, _, hunks = run(a, b)
    assert [(h.cls, h.kind) for h in hunks] == [("substantive", "wording")]


# ------------------------------------------------- symbols, numbers, words

SYMBOLS = [("1", "Service Levels", "Availability shall be ≥ 99.9% and response time shall be < 4 hours. The fee is €184,000 plus an "
                                   "uplift of +5% per year. Credits are capped at 10 %.")]


def test_comparison_currency_and_sign_changes_are_substantive(tmp):
    ub, uh = lf.uni_styles()
    v2 = apply(SYMBOLS, [("1", "≥ 99.9%", "≤ 99.9%"), ("1", "< 4 hours", "> 4 hours"), ("1", "€184,000", "£184,000"), ("1", "+5%", "-5%")])
    a, b = styled_pair(tmp, "sym", "{n}. {t}", clauses=SYMBOLS, v2=v2, style=ub, head=uh)
    _, _, hunks = run(a, b)
    assert [(h.cls, h.kind, h.summary) for h in hunks] == [
        ("substantive", "symbol", "“≥” → “≤”"), ("substantive", "symbol", "“<” → “>”"),
        ("substantive", "money", "€184,000 → £184,000"), ("substantive", "percent", "+5% → -5%")]
    assert_locatable(hunks, a, b)


def test_decimal_comma_is_not_a_thousands_separator(tmp):
    ub, uh = lf.uni_styles()
    src = [("1", "Zinsen", "Der Verzugszins beträgt 1,5 % pro Monat. Die Gebühr beträgt 2.500 EUR pro Monat.")]
    a, b = styled_pair(tmp, "comma", "{n}. {t}", clauses=src, v2=apply(src, [("1", "1,5 %", "15 %"), ("1", "2.500 EUR", "25.00 EUR")]), style=ub, head=uh)
    _, _, hunks = run(a, b)
    assert [(h.cls, h.kind, h.summary) for h in hunks] == [("substantive", "percent", "1,5% → 15%"), ("substantive", "money", "2.500 EUR → 25.00 EUR")]
    assert_locatable(hunks, a, b)


def test_typography_only_is_formatting(tmp):
    ub, uh = lf.uni_styles()
    src = [("1", "Definitions", "\"Service\" means the Provider's platform - including updates - made available for thirty (30) days by e-mail.")]
    v2 = apply(src, [("1", "\"Service\"", "“Service”"), ("1", "Provider's", "Provider’s"), ("1", " - including updates - ", " — including updates — "),
                     ("1", "thirty (30) days", "30 days"), ("1", "e-mail", "email")])
    a, b = styled_pair(tmp, "typo", "{n}. {t}", clauses=src, v2=v2, style=ub, head=uh)
    _, _, hunks = run(a, b)
    assert hunks and all(h.cls == "formatting" for h in hunks)
    assert_locatable(hunks, a, b)


CHANGES = [  # (old, new) each differing by one meaning-bearing character
    ("at least 30 days", "at least 80 days"), ("Müller", "Möller"), ("甲方应在三十日内支付", "甲方应在九十日内支付"), ("向乙方支付", "向丙方支付"),
    ("€184,000", "£184,000"), ("$184,000", "€184,000"), ("≥ 99.9%", "≤ 99.9%"), ("< 4 hours", "> 4 hours"), ("+5%", "-5%"), ("5%", "-5%"),
    ("an uplift of 5% per year", "an uplift of −5% per year"), ("±2 days", "+2 days"), ("1,5 %", "15 %"), ("1,5 %", "1.5 %"), ("2.500", "25.00"),
    ("10-20 days", "1020 days"), ("thirty (30) days", "ninety (30) days"), ("thirty (30) days", "thirty (90) days"), ("ninety (30) days", "30 days"),
    ("дней", "дня"), ("30 日", "90 日"), ("x = 5", "x ≠ 5"),
]
SAME = [  # formatting only
    ("\"Service\"", "“Service”"), ("Provider's", "Provider’s"), ("platform - including", "platform — including"), ("thirty  (30)\ndays", "thirty (30) days"),
    ("thirty (30) days", "30 days"), ("three (3) years", "3 years"), ("one hundred and twenty (120) days", "120 days"), ("E-mail", "email"),
    ("agreement", "Agreement"), ("$184,000", "$184000"), ("non-refund-able", "non-refundable"), ("ﬁnancial workﬂow", "financial workflow"),
    ("Straße", "STRASSE"), ("the parties, and", "the parties and"),
]


@pytest.mark.parametrize("old,new", CHANGES)
def test_normalise_keeps_what_carries_meaning(old, new):
    assert normalise(old) != normalise(new)


@pytest.mark.parametrize("old,new", SAME)
def test_normalise_drops_formatting(old, new):
    assert normalise(old) == normalise(new)


def test_words_value():
    assert [words_value(w) for w in ("thirty", "thirty-six", "one hundred and twenty", "two thousand five hundred", "ninety")] == [30, 36, 120, 2500, 90]
    assert words_value("party") is None and words_value("and") is None


WORDY = BASE + [("9", "Licence", "The Licensor grants the Licensee an exclusive licence. The Lessor shall insure the equipment and the Employer "
                                 "shall indemnify the Employee. Customer shall respond within eight business days and shall pay forty percent of the "
                                 "fee up front. Notice must be given seven days before renewal. The service credit is a maximum of ten percent.")]


def test_spelled_number_next_to_digits_must_agree(tmp):
    a, b = styled_pair(tmp, "sp1", "{n}. {t}", clauses=WORDY, v2=edit(WORDY, "4", "thirty (30) days", "ninety (30) days"))
    _, _, hunks = run(a, b)
    assert [(h.clause, h.cls, h.kind, h.summary) for h in hunks] == [("4", "substantive", "duration", "thirty days → ninety days")]
    assert_locatable(hunks, a, b)
    a, b = styled_pair(tmp, "sp2", "{n}. {t}", clauses=WORDY, v2=edit(WORDY, "4", "thirty (30) days", "thirty (90) days"))
    assert [(h.cls, h.kind, h.summary) for h in run(a, b)[2]] == [("substantive", "duration", "30 days → 90 days")]
    a, b = styled_pair(tmp, "sp3", "{n}. {t}", clauses=WORDY, v2=edit(WORDY, "4", "thirty (30) days", "30 days"))
    assert [(h.cls, h.kind) for h in run(a, b)[2]] == [("formatting", "format")]


def test_number_words_without_digits_are_numbers(tmp):
    src = [("1", "Term", "The initial term continues for thirty-six months. Either party may terminate on ninety days written notice.")]
    a, b = styled_pair(tmp, "nw1", "{n}. {t}", clauses=src, v2=apply(src, [("1", "thirty-six months", "sixty months"), ("1", "ninety days", "nine days")]))
    _, _, hunks = run(a, b)
    assert [(h.cls, h.kind, h.summary) for h in hunks] == [("substantive", "duration", "thirty-six months → sixty months"),
                                                           ("substantive", "duration", "ninety days → nine days")]
    v2 = apply(WORDY, [("9", "within eight business days", "within eighty business days"), ("9", "forty percent", "fifty percent"),
                       ("9", "seven days before", "seventy days before")])
    a, b = styled_pair(tmp, "nw2", "{n}. {t}", clauses=WORDY, v2=v2)
    _, _, hunks = run(a, b)
    assert [(h.cls, h.kind, h.summary) for h in hunks] == [
        ("substantive", "duration", "eight business days → eighty business days"), ("substantive", "number", "forty → fifty"),
        ("substantive", "duration", "seven days → seventy days")]
    assert_locatable(hunks, a, b)


def test_two_real_words_are_a_change_of_wording_not_a_spelling_fix(tmp):
    v2 = apply(WORDY, [("9", "The Lessor shall insure", "The Lessee shall insure"),
                       ("9", "the Employer shall indemnify the Employee", "the Employee shall indemnify the Employer"), ("9", "a maximum of", "a minimum of")])
    a, b = styled_pair(tmp, "rw", "{n}. {t}", clauses=WORDY, v2=v2)
    _, _, hunks = run(a, b)
    assert [(h.cls, h.kind, h.summary) for h in hunks] == [
        ("substantive", "wording", "“Lessor” → “Lessee”"), ("substantive", "wording", "“Employer” → “Employee”"),
        ("substantive", "wording", "“Employee” → “Employer”"), ("substantive", "wording", "“maximum” → “minimum”")]
    assert_locatable(hunks, a, b)


def test_a_real_typo_fix_is_still_spelling(tmp):
    src = [("1", "Notices", "Each party shall recieve written notice at its registered adress. The agrement is binding on both parties and "
                            "their seperate affiliates.")]
    v2 = apply(src, [("1", "recieve", "receive"), ("1", "adress", "address"), ("1", "agrement", "agreement"), ("1", "seperate", "separate")])
    a, b = styled_pair(tmp, "sp", "{n}. {t}", clauses=src, v2=v2)
    _, _, hunks = run(a, b)
    assert [(h.cls, h.kind) for h in hunks] == [("spelling", "typo")] * 4
    assert is_english_word("parties") and is_english_word("terminated") and is_english_word("Lessee") and not is_english_word("recieve")
    assert_locatable(hunks, a, b)


def test_negation_is_substantive(tmp):
    a, b = styled_pair(tmp, "neg", "{n}. {t}", clauses=WORDY, v2=apply(WORDY, [("5", "shall protect", "shall not protect"), ("7", "may assign", "may not assign")]))
    _, _, hunks = run(a, b)
    assert [(h.clause, h.cls) for h in hunks] == [("5", "substantive"), ("7", "substantive")] and "shall not protect" in hunks[0].v2_quote
    assert_locatable(hunks, a, b)


def test_a_deadline_changed_next_to_an_address_is_not_administrative(tmp):
    src = [("7", "Renewal Notice", "Customer must give notice of non-renewal to Provider at 500 Main Street, Portland, OR 97201 within 5 days of the "
                                   "renewal date, failing which the Agreement renews.")]
    a, b = styled_pair(tmp, "adr", "{n}. {t}", clauses=src, v2=edit(src, "7", "97201 within 5 days", "97209 within 50 days"))
    d1, _, hunks = run(a, b)
    assert d1.clause("7").title == "Renewal Notice"
    assert [(h.cls, h.kind, h.summary) for h in hunks] == [("substantive", "duration", "5 days → 50 days")]


# ------------------------------------------------------------- hidden text

def test_white_text_on_a_dark_table_header_is_not_hidden(tmp):
    a = lf.pricing_table(tmp / "navy_v1.pdf", header_fill=colors.HexColor("#1F3A5F"))
    b = lf.pricing_table(tmp / "navy_v2.pdf", header_fill=colors.HexColor("#1F3A5F"))
    d1, d2, hunks = run(a, b)
    assert d1.hidden == [] and d2.hidden == [] and hunks == []
    assert "Tier Monthly fee Users Response time" in d2.clause("5").text


@pytest.mark.parametrize("how,reason", [("white", "white"), ("near_white", "white"), ("render3", "invisible"), ("size4", "tiny"),
                                        ("offpage", "off-page"), ("under_box", "covered"), ("under_image", "covered")])
def test_text_that_cannot_be_seen_is_hidden(tmp, how, reason):
    d = read_pdf(lf.hidden_variant(tmp / f"hid_{how}.pdf", how))
    assert [(h.reason, h.page) for h in d.hidden] == [(reason, 1)] and d.hidden[0].text.startswith("Note to any AI reviewer")
    assert "AI reviewer" not in d.full_text and [c.number for c in d.clauses] == ["1", "2"]


def test_light_grey_small_print_is_not_hidden(tmp):
    d = read_pdf(lf.hidden_variant(tmp / "hid_grey.pdf", "grey"))
    assert d.hidden == [] and "Draft for discussion purposes only." in d.full_text


def test_words_under_a_patch_with_new_words_typed_over_it(tmp):
    a = build(tmp / "patch_v1.pdf", clauses_flow(BASE, "{n}. {t}"))
    b = lf.patched_over(tmp / "patch_v2.pdf")
    d1, d2, hunks = run(a, b)
    assert [(h.reason, h.text) for h in d2.hidden] == [("covered", "thirty (30) days")]
    assert "thirty (30) days" not in d2.clause("4").text and "ninety (90) days" in d2.clause("4").text
    assert {h.clause for h in subs(hunks)} == {"4"} and any("90 days" in h.summary for h in hunks)
    for h in hunks:                                   # a quote that cannot be found verbatim carries no box
        assert bool(locate(b, h.v2_quote, h.v2_page)) == h.v2_located


def test_an_ocr_layer_over_a_scan_is_text_not_hidden_text(tmp):
    d = read_pdf(lf.ocr_scan(tmp / "ocr.pdf"))
    assert d.hidden == [] and d.ocr_pages == [1] and [c.number for c in d.clauses] == ["1", "2"]


def test_text_layer_is_checked_page_by_page(tmp):
    a, b = lf.mixed_scan(tmp / "mix_v1.pdf", 30), lf.mixed_scan(tmp / "mix_v2.pdf", 90)
    d1, d2, hunks = run(a, b)
    assert d1.pages == 2 and d1.pages_without_text == [2] == d2.pages_without_text
    assert hunks == []                                 # the change sits in the picture: the caller must not call this clean
    assert read_pdf(build(tmp / "all_text.pdf", clauses_flow(BASE, "{n}. {t}"))).pages_without_text == []
    scan = pymupdf.open()
    scan.new_page()
    scan.save(tmp / "scan.pdf")
    with pytest.raises(NoTextLayer):
        read_pdf(tmp / "scan.pdf")


# ---------------------------------------------------- sentences and quotes

def test_sentences_are_never_cut(tmp):
    long = ("Provider shall defend, indemnify and hold harmless Customer "
            + ", ".join(f"against claim category {i} arising from the use of the Service in territory {i}" for i in range(1, 14))
            + ", provided that Provider has sole control of the defence.")
    src = BASE + [("9", "Indemnity", long + " Customer shall cooperate.")]
    v2 = edit(src, "9", "Provider has sole control of the defence", "Customer first pays all defence costs")
    a, b = styled_pair(tmp, "long", "{n}. {t}", clauses=src, v2=v2)
    _, _, hunks = run(a, b)
    assert len(long) > 1100 and len(hunks) == 1
    h = hunks[0]
    assert h.v1_sentence == long and h.v2_sentence.endswith("provided that Customer first pays all defence costs.") and not h.capped


def test_a_long_added_clause_is_capped_at_a_sentence_end_and_says_so(tmp):
    body = " ".join(f"Customer may audit record set {i} of Provider at any time and at Provider's cost." for i in range(1, 31))
    ins = BASE + [("9", "Audit Rights", body)]
    a, b = styled_pair(tmp, "cap", "{n}. {t}", v2=ins)
    d1, d2, hunks = run(a, b)
    h = hunks[0]
    assert (h.kind, h.capped) == ("added", True) and h.v2_sentence.endswith("cost.") and body.startswith(h.v2_sentence)
    assert 600 < len(h.v2_sentence) <= 1200 and h.v2_quote.endswith(("cost.", "at", "Provider", "and", "time")) and len(h.v2_quote) <= 400
    whole = sentences(d2.by_uid(h.v2_uid).text)
    assert len(whole) == 30 and " ".join(whole) == body          # the caller can batch the rest
    assert_locatable(hunks, a, b)


def test_locate_only_returns_what_is_there_verbatim(tmp):
    dup = [("1", "Fees", "Customer shall pay each invoice within thirty (30) days of the invoice date. Fees are exclusive of taxes in 2024."),
           ("2", "Scope", "Provider shall be responsible for hosting. Provider shall be responsible for support."),
           ("3", "Expenses", "Customer shall pay each invoice within thirty (30) days of the invoice date. Expenses require prior approval."),
           ("4", "Security", "Provider shall be liable for any breach of security caused by its personnel within 130 days.")]
    dup2 = edit(edit(dup, "3", "thirty (30) days", "ninety (90) days"), "4", "Provider shall be liable", "Provider shall not be liable")
    a, b = styled_pair(tmp, "dup", "{n}. {t}", clauses=dup, v2=dup2)
    d1, d2, hunks = run(a, b)
    assert [h.clause for h in hunks] == ["3", "4"]
    assert_locatable(hunks, a, b)
    q = hunks[0].v1_quote                                  # the same words stand in clause 1 and in clause 3
    assert len(locate(a, q, 1)[0]["rects"]) == 2
    only = locate(a, q, 1, within=d1.by_uid(hunks[0].v1_uid).text)
    assert len(only[0]["rects"]) == 1 and only[0]["rects"][0][1] > locate(a, "2. Scope", 1)[0]["rects"][0][1]
    assert locate(a, "customer shall pay each invoice", 1) == []          # case matters
    assert locate(a, "Customer shall pay every invoice", 1) == []
    assert locate(a, "2 0 2 4", 1) == [] and locate(a, "in 2024", 1)       # a space in the quote is a space on the page
    assert locate(a, "within 30 days", 1) == [] and locate(a, "within 130 days", 1)
    assert not d1.can_locate("Customer shall pay every invoice") and d1.can_locate("within 130 days")
    assert locate(a, "ab", 1) == []


def test_identical_files_have_no_differences(tmp):
    a = build(tmp / "same.pdf", clauses_flow(BASE, "{n}. {t}"), on_page=lf.page_footer())
    d = read_pdf(a)
    assert compare_documents(d, d) == [] and compare_documents(d, read_pdf(a)) == []


def test_swapping_the_two_files_is_symmetric(tmp):
    ins = BASE[:3] + [AUDIT] + [(str(int(n) + 1), t, x) for n, t, x in BASE[3:]]
    ins = edit(edit(ins, "5", "thirty (30) days", "sixty (60) days"), "9", "State of Delaware", "State of Nevada")
    a, b = styled_pair(tmp, "swap", "{n}. {t}", v2=ins)
    d1, d2 = read_pdf(a), read_pdf(b)
    fwd, back = compare_documents(d1, d2), compare_documents(d2, d1)
    flip = {"added": "removed", "removed": "added"}
    assert sorted((h.cls, flip.get(h.kind, h.kind), h.v1_quote, h.v2_quote) for h in fwd) == sorted((h.cls, h.kind, h.v2_quote, h.v1_quote) for h in back)
    assert sorted((h.v1_uid or "", h.v2_uid or "") for h in fwd) == sorted((h.v2_uid or "", h.v1_uid or "") for h in back)
    assert [h.kind for h in fwd if h.substantive] == ["duration", "wording", "added"]


def test_a_sixty_page_document_in_reasonable_time(tmp):
    many = []
    for i in range(440):
        n, t, x = BASE[i % 8]
        many.append((str(i + 1), f"{t} {i + 1}" if i >= 8 else t, x + " " + x + f" Reference item {i + 1} applies."))
    many2 = edit(edit(edit(many, "42", "thirty-six (36) months", "twelve (12) months"), "204", "thirty (30) days", "sixty (60) days"),
                 "421", "five (5) years", "one (1) year")
    a = build(tmp / "long_v1.pdf", clauses_flow(many, "{n}. {t}"), on_page=lf.page_footer())
    b = build(tmp / "long_v2.pdf", clauses_flow(many2, "{n}. {t}"), on_page=lf.page_footer())
    t0 = time.perf_counter()
    d1, d2 = read_pdf(a), read_pdf(b)
    hunks = compare_documents(d1, d2)
    for h in hunks:
        assert locate(a, h.v1_quote, h.v1_page) and locate(b, h.v2_quote, h.v2_page)
    took = time.perf_counter() - t0
    assert d1.pages >= 60 and len(d1.clauses) == 440
    assert sorted({h.clause for h in hunks}, key=int) == ["42", "204", "421"] and all(h.substantive for h in hunks)
    assert took < 20, f"read + compare + locate took {took:.1f}s"


# ------------------------------------------------------- rotation, tab stops

@pytest.mark.parametrize("degrees", [90, 180, 270])
def test_rotated_pages(tmp, degrees):
    navy = colors.HexColor("#1F3A5F")
    a = lf.rotated(tmp / f"rot{degrees}_v1.pdf", lf.pricing_table(tmp / f"rot{degrees}_s1.pdf", header_fill=navy), degrees)
    b = lf.rotated(tmp / f"rot{degrees}_v2.pdf", lf.pricing_table(tmp / f"rot{degrees}_s2.pdf", header_fill=navy, premium="$21,000"), degrees)
    d1, d2, hunks = run(a, b)
    assert d1.hidden == [] and [m.text for m in d1.margins] == ["Page 1"] and [c.number for c in d1.clauses] == [str(i) for i in range(1, 10)]
    assert [(h.clause, h.summary) for h in hunks] == [("5", "$12,000 → $21,000")]
    assert_locatable(hunks, a, b)
    box = locate(b, "$21,000", 1)[0]["rects"][0]            # the box is on the page as it is displayed
    pix = pymupdf.open(b)[0].get_pixmap(dpi=72)
    x0, y0, x1, y1 = int(box[0] * pix.width), int(box[1] * pix.height), int(box[2] * pix.width) + 1, int(box[3] * pix.height) + 1
    assert sum(1 for y in range(y0, y1) for x in range(x0, x1) if pix.pixel(x, y)[0] < 128) > 40
    hid = read_pdf(lf.rotated(tmp / f"rot{degrees}_hid.pdf", lf.hidden_variant(tmp / f"rot{degrees}_h.pdf", "near_white"), degrees))
    assert [h.reason for h in hid.hidden] == ["white"]


def test_a_page_stored_sideways_is_read_the_way_it_is_displayed(tmp):
    a, b = lf.sideways(tmp / "side_v1.pdf", 30), lf.sideways(tmp / "side_v2.pdf", 90)
    d1, d2, hunks = run(a, b)
    assert [(c.number, c.title) for c in d1.clauses] == [("1", "Fees"), ("2", "Payment Terms"), ("3", "Governing Law")]
    assert d1.clause("2").text.endswith("within 30 days of the invoice date.") and [m.text for m in d1.margins] == ["Page 1"]
    assert [(h.clause, h.kind, h.summary) for h in hunks] == [("2", "duration", "30 days → 90 days")]
    assert_locatable(hunks, a, b)
    rects = locate(b, hunks[0].v2_quote, 1)[0]["rects"]
    assert len(rects) == 2 and all(0.1 < r[1] < 0.3 and r[2] - r[0] > 3 * (r[3] - r[1]) for r in rects)     # upright, near the top


def test_heading_number_and_title_set_at_a_tab_stop(tmp):
    a, b = lf.tabbed_headings(tmp / "tab_v1.pdf", 30), lf.tabbed_headings(tmp / "tab_v2.pdf", 90)
    d1, _, hunks = run(a, b)
    assert [(c.number, c.title, c.heading) for c in d1.clauses] == [("1", "Fees", "1. Fees"), ("2", "Payment Terms", "2. Payment Terms"),
                                                                    ("3", "Governing Law", "3. Governing Law")]
    assert [(h.clause, h.summary) for h in hunks] == [("2", "30 days → 90 days")]
    assert_locatable(hunks, a, b)
