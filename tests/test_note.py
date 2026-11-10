"""Regression cases for factual prose that resembles a courtesy or a signature."""
import pytest

from referee.note import split_claims, split_note


@pytest.mark.parametrize('claim', [
    'Payment terms are unchanged',
    'P.S. Liability is unlimited',
    'No commercial terms changed',
    'Thanks for confirming the annual fee is unchanged.',
    'Thank you for agreeing to waive the liability cap.',
])
def test_facts_survive_signatures_and_courtesy_openings(claim):
    note = 'Nothing changed.\n\nThanks,\nPriya\n\n' + claim
    parsed = split_note(note)
    assert claim in parsed['claims']
    assert all(item['text'] != claim for item in parsed['skipped'])
    assert parsed['sent'][parsed['claims'].index(claim)] == claim


def test_a_fact_on_the_next_signature_line_is_not_skipped():
    assert split_claims('Thanks,\nPriya\nPayment terms are unchanged') == ['Payment terms are unchanged']


def test_names_contacts_and_known_courtesies_remain_skipped():
    parsed = split_note('The fee is unchanged.\n\nThanks for your patience.\n\nBest,\nJ. Smith\nAcme Corp.\njane@example.com')
    assert parsed['claims'] == ['The fee is unchanged.']
    assert {x['text'] for x in parsed['skipped']} >= {'J. Smith', 'Acme Corp.', 'jane@example.com'}


def test_unpunctuated_lists_are_not_capped():
    claims = [f'Clause {i} is unchanged' for i in range(45)]
    assert split_claims('\n'.join('- ' + c for c in claims)) == claims


def test_fact_after_signature_reaches_verify_and_blocks_when_contradicted(tmp_path):
    from pathlib import Path
    from referee.engine import review
    from referee.gateway import Gateway
    from tests.fake_clocked import FakeClocked, load_truth

    case = Path(__file__).resolve().parents[1] / 'data/cases/clean-return'
    claim = 'The annual subscription fee is one dollar'
    truth = load_truth(case)
    truth['letter_claims'] = [{'text': claim, 'expected': 'contradicted', 'about': 'fee'}]
    result = review(case / 'v1.pdf', case / 'v2.pdf', 'Thanks,\nPriya\n' + claim,
                    Gateway('live', client=FakeClocked(truth)), tmp_path)
    assert result['note']['statements'] == result['note']['checked'] == 1
    assert result['note']['claims'][0]['verdict'] == 'contradicted'
    assert result['status'] == 'blocked'
