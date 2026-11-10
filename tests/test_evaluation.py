"""Evaluation must count omissions and failed cases, rather than select only successes."""
from eval.scoring import matches, nz, score_review, summarize


def fixture():
    truth = {'substantive': [{'id': 'fee', 'clause': '4', 'v1_quote': '$100', 'v2_quote': '$200',
                             'route': 'counter', 'passage': 'fees'}],
             'cosmetic': [], 'letter_claims': [{'text': 'Nothing changed', 'expected': 'contradicted'}],
             'expected_status': 'blocked', 'injection': False}
    review = {'changes': [], 'status': 'incomplete', 'errors': [{'step': 'failed'}],
              'audit': {'credits': 0, 'calls': 0}, 'note': {'claims': []}, 'guard': [], 'hidden_text': []}
    return truth, review


def test_failed_review_and_missing_claim_route_stay_in_denominators():
    truth, review = fixture()
    row = score_review('missing', truth, review)
    s = summarize([row])
    assert s['pairs'] == s['pairs_failed'] == 1 and s['pairs_complete'] == 0
    assert s['edits'] == s['false_statements'] == s['routed'] == 1
    assert s['edits_held'] == s['false_statements_caught'] == s['route_correct'] == 0
    assert s['route_missing'] == s['claims_not_checked'] == 1
    assert s['route_stricter'] == s['route_looser'] == 0


def test_a_missing_claim_is_not_called_not_stated():
    truth, review = fixture()
    truth['letter_claims'][0]['expected'] = 'supported'
    row = score_review('missing', truth, review)
    assert row['claims'][0]['got'] == 'not_checked'


def test_match_requires_the_actual_new_value_as_well_as_the_old_one():
    truth, _ = fixture()
    card = {'clause': '4', 'parts': [{'v1': {'quote': '$100', 'changed': '$100'},
                                    'v2': {'quote': '$300', 'changed': '$300'}}]}
    assert not matches(card, truth['substantive'][0])
    card['parts'][0]['v2'] = {'quote': '$200', 'changed': '$200'}
    assert matches(card, truth['substantive'][0])


def test_normalisation_preserves_non_latin_text_and_currency_signs():
    assert nz('付款期限三十天') != nz('付款期限九十天')
    assert nz('$100') != nz('€100')


def test_shared_words_in_another_clause_do_not_credit_an_edit():
    card = {'clause': '3', 'parts': [{'v1': {'quote': 'either party gives notice', 'changed': 'either party'},
                                    'v2': {'quote': 'Customer gives notice', 'changed': 'Customer'}}]}
    edit = {'clause': '9', 'v1_quote': 'Neither party will be liable for indirect',
            'v2_quote': 'Customer will not be liable for indirect'}
    assert not matches(card, edit)
