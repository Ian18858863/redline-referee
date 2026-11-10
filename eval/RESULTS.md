# Evaluation results

Everything here is computed from recorded answers of the live Clocked API (`recordings/`) against exact ground truth (`data/cases/*/truth.json`). `python eval/run_eval.py` and `python eval/run_hard.py` recompute it without a key.

Differences in extracted, aligned text are found by deterministic code. This is not a guarantee of recall across arbitrary PDF layouts. What is measured below is what the application does with each difference. Failed reviews, missing edits and unchecked claims stay in the denominators.

## Seeded pairs (15 of 15 completed without a failed step)

Twelve pairs with two to four planted substantive edits, plus three pairs in which whole clauses were added or removed and the rest renumbered.

| Measure | Result |
|---|---|
| Planted substantive edits held as changes | 30 of 30 |
| ... read as contradicted by Verify (sentence against the other PDF) | 30 of 30 asked |
| ... read as a conflict by Compare (clause against clause) | 26 of 26 asked |
| Whole clauses added or removed, held | 4 of 4 |
| One-word swaps between two real words (maximum, minimum), held | 2 of 2 |
| Cosmetic edits (spelling, formatting, address, rewording) wrongly held | 0 of 41 |
| Faithful rewordings cleared | 4 of 4 |
| Clauses held with no planted cause | 0 |
| Expected cover-note statements not checked | 0 |
| False cover-note statements judged contradicted | 30 of 30 |
| True cover-note statements judged supported | 37 of 39 (2 not stated, 0 wrongly contradicted) |
| Hidden instructions flagged by Guard / found as hidden text by rules | 2 of 2 / 2 of 2 |
| Guard false alarms on pairs without an injection | 0 of 13 |
| Final status equal to ground truth | 15 of 15 |
| Credits, all 15 full reviews | 1136 (about 76 per review) |

## Routing

Each planted edit has a route the playbook dictates (`EXPECTED_ROUTE` in `referee/contracts.py`). Rerank picks the passages, Decide answers two yes/no questions, code composes the route.

| Measure | Result |
|---|---|
| Expected routes with no usable route returned | 0 |
| Route equal to what the playbook dictates | 27 of 30 |
| Wrong on the strict side (sent to a person or countered when the desk could accept) | 3 |
| Wrong on the loose side (accepted or countered what needed an approver) | 0 |
| The deciding playbook passage was among those Rerank kept | 24 of 24 |
| Other passages Rerank also kept for those edits | 4 |
| No passage kept when the playbook has none (escalated by rule) | 6 of 6 |

Routes that differ from the playbook: data_export: counter (playbook says accept); data_export: counter (playbook says accept); data_export: counter (playbook says accept).

## The hard set: 39 one-sentence edits that a diff cannot settle

24 change what a sentence obliges or allows (a duty made optional, a mutual limit made one-sided, days made business days, a negation removed). 15 say the same thing in other words. One edit per sentence, two pairs (`hard-a`, `hard-b`).

| | 24 changes of meaning | 15 faithful rewordings |
|---|---|---|
| The application (clears only if Verify supports both ways and Compare does not object) | 24 held, 0 wrongly cleared | 14 cleared, 1 held |
| Verify alone | read 22 as changed, **2 as the same** | read 14 as the same |
| Compare alone | read 23 as conflicting, 0 as the same, 1 not flagged | read 14 as the same |

The two readers disagreed on 3 edits (hard-a-15, hard-b-13, hard-b-15). Verify read two real changes as the same meaning (a mutual exclusion of damages made one-sided; "will defend" made "may defend"); Compare read both as conflicts, so they were held. Compare did not flag one widened scope that Verify read as changed. The one rewording both readers took for a change ("exclusive of taxes" to "do not include taxes") was held: an error on the strict side.

## What each reader changes

Every recorded pair with ground truth (24 pairs: 69 real changes, 20 faithful rewordings, 39 false cover-note statements) decided again from the same recordings, with a reader's answers ignored.

| Decided by | Real changes cleared (missed) | Faithful rewordings held (false alarms) | False statements not caught | Final status wrong |
|---|---|---|---|---|
| All three | 0 | 1 | 0 | 0 of 24 |
| Without Verify | 0 | 20 | 39 | 13 of 24 |
| Without Compare | 4 | 1 | 1 | 1 of 24 |
| Rules alone | 0 | 20 | 39 | 13 of 24 |

Ablation coverage (a missing counterfactual response is not a model error or a completed measurement):

| Variant | Complete replays |
|---|---|
| all three | 24 of 24 |
| without Verify | 24 of 24 |
| without Compare | 24 of 24 |
| rules alone | 24 of 24 |

Without Verify the cover note is taken at its word. With rules alone every rewording is a change and nothing checks the note.

These are authored variations of one synthetic agreement plus a hard set and one unrelated agreement. They show that the application does what it claims on data with exact answers. They are not an accuracy figure for contracts in general.
