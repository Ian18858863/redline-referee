# External-source reliability and routing evidence

The release has **not met all acceptance targets** in the frozen protocol. Source texts, labels, PDFs and routing fixtures were committed in `efd8384` before the first new live call. The first-run code hashes and input hashes are in `data/external/LOCK.json`. Attribution and limits are in `data/external/SOURCES.md`.

The three externally authored agreements contain our own pre-labelled edits. This is not an independently labelled, blinded or representative legal benchmark. The corrected results below reuse inputs inspected during debugging and are **regression evidence after unblinding**, not a fresh holdout score.

## Detection: current production code

| Measure | Observed | Frozen target |
|---|---:|---:|
| Reviews completed without failed API/application stages | 6/6 | 6/6 |
| Planted substantive edits held | 6/6 | 6/6 |
| Faithful rewordings cleared | **1/3** | 3/3 |
| False formatting-only cover notes contradicted | 3/3 | 3/3 |
| Final status matches corrected expected status | **4/6** | 6/6 |

Common Paper and Bonterms both replace “reasonable standard of care” with “reasonable level of care.” Compare reads the same meaning; Verify does not support it in both directions, so both stay held. These are two occurrences of the same rewording, not two independent error types. The UI now calls these disagreements **Meaning needs review**, retains the blocker and shows the actual reader evidence; it does not silently waive them.

The publisher's Bonterms PDF also contains tiny “CC-BY-4.0” branding in both versions. The current hidden-text rule blocks this pre-existing fine print. Guard itself reads it as benign. This additional false blocker is still present and is a priority for the receiving maintainer: distinguish unchanged publisher fine print from newly concealed content without weakening injection handling. Both benign cover notes are judged supported; this is not a cover-note false contradiction.

The deterministic reader previously sorted text by the top of its glyph box. A replacement word drawn in another font could precede its sentence or appear under the previous clause despite sharing its physical baseline. Sorting by baseline fixes the Bonterms edit ordering: its two substantive edits now stay in clauses 7 and 8, and the benign edit stays in clause 3. Existing layout and rule regressions and new mixed-font/native-PDF tests cover this change.

## Scoring errata and preserved first output

`external-results-first.json` retains the initial raw report. Its 4/6 held and 0/3 faithful counts are **not valid first-pass accuracy estimates**: the scorer required the complete planted phrase inside a deliberately bounded evidence quote. The corrected scorer also accepts an actual changed span within the planted phrase, on both sides of the same card part. Missing differences still do not count as cleared. Targeted accounting tests cover this correction.

The frozen changed-pair status labels said `changes`; the existing product correctly uses `blocked` when the cover note is contradicted. The scorer reports the corrected expectation **and** the original frozen label. No frozen input or truth file was edited. Do not call either scoring correction a model improvement.

## Routing control: production versus exploratory changes

Six fixed fee/payment contexts, including exactly $100,000, use a separate fictional policy with explicit authority. Fee context is supplied, not extracted from a PDF. Each arm uses the same change, context and Decide implementation; retrieval alone varies. This is a component test, not an end-to-end contract routing benchmark.

| Current production arm | Complete | Correct |
|---|---:|---:|
| Rerank-selected guidance | 6/6 | **4/6** |
| All guidance supplied | 6/6 | **4/6** |
| First same-topic passage only | 6/6 | **4/6** |

There is **no measured Rerank accuracy uplift** in this control. For large deals requesting 45 days, the fixture explicitly allows countering with the original 30-day term; the current compromise-only question instead escalates. This misses the 6/6 routing target.

Exploratory question changes, including a separate restoration-authority question, reached 6/6 on this component set. Full development-corpus replay then found unsafe authority expansion: the restoration variant scored 22/30 routes with five routes on the loose side, versus production's 27/30 with zero on the loose side. It was rejected and reverted. `routing-experiments/` retains its questions/results and the intermediate runs; exact paid exchanges remain in recordings. A receiving maintainer should model authority explicitly and revalidate on new locked inputs, rather than ship a locally successful prompt.

## Cost and reproduction

The round used **645 actual credits**, with **0 unsettled estimated credits**, against the user's 1,500-credit authorization. `external-ledger.jsonl` records every new call's request hash, endpoint, charged credits and cumulative round accounting, including rejected exploratory approaches and development recording refreshes. Individual recordings retain remote billing metadata. Replay billing metadata describes saved calls; it is not a new charge.

`RoundGateway` serializes new calls, persists accounting across restarts and stops new reservations at 1,350 credits, leaving 150 credits because remote billing is reported after the call. Do not run multiple live experiment processes concurrently. No credentials or raw participant answers are committed.

```sh
uv run python eval/run_external.py                 # offline reproduction, reports known failures
uv run python eval/run_external.py --check         # intentionally fails the unmet acceptance targets
uv run python eval/run_routing.py                  # offline reproduction, all three arms
uv run python eval/run_routing.py --check          # intentionally fails 4/6 production routing
uv run python eval/run_eval.py --check --check-ablation
uv run python eval/run_hard.py
uv run pytest -q
```

Detection intentionally skips the SaaS Extract schema and production routing playbook. Scans/OCR, multilingual agreements, negotiated customer contracts and independent legal adjudication remain unmeasured. There is no completed paired human study and no measured productivity benefit. The English-only local study tool and its protocol are under `study/`; automated previews are excluded from human evidence.

---

# Maintainer follow-up: three general fixes and a second locked corpus

Everything above is the first PR author's report and is left as written. This section was added by the receiving maintainer after reproducing it (942 tests, the four offline evaluators, the browser walkthrough).

## What changed in the code, and why it is not corpus-specific

1. **Fine print that both versions carry does not block.** Concealed text in the returned copy is a blocker only when the version we sent does not conceal the same text, matched one for one. Inherited fine print is listed with its reason; Guard still reads all of the returned copy. A second copy, or the same passage with a word added, is new and blocks. No publisher or string is named anywhere.
2. **A PDF edited in place is read in reading order.** The Bonterms rewording was held because of the file, not the phrase: an in-place edit draws the replacement words last, so in the raw text layer the edited sentence has a hole and the new words sit at the end of the page. Verify, given the raw PDF, read the hole. The engine now looks for every sentence it read, whole, in the raw text layer; where a sentence is not there in one piece, Verify is given the reading-order text for that file and the review says the file looks edited in place. No threshold moved.
3. **Authority to send our original term back is policy data.** A passage may be marked `restore_original` in the playbook. Only when Rerank keeps such a passage is Decide asked a third yes/no question (does that grant apply to this request), and only a sure yes is a counter; an unsure answer goes to a person. The application playbook marks no passage, so its recorded routes cannot change: development routing replays at 27/30 with zero loose routes, byte for byte.

**Not changed, on purpose:** the Common Paper rewording ("reasonable standard of care" to "reasonable level of care"). On a clean export Verify reads a change (supported 0.41 and 0.15) and Compare reads the same meaning. That is a disagreement between the readers, not an artefact, and "standard of care" is arguably a term of art. The rule that the stricter reading wins was not weakened to pass it; the card says the meaning needs review.

## The first six pairs after the fixes (regression evidence, these inputs were seen)

| Measure | Before | After | Frozen target |
|---|---:|---:|---:|
| Planted substantive edits held | 6/6 | 6/6 | 6/6 |
| Faithful rewordings cleared | 1/3 | **2/3** | 3/3 |
| False cover notes contradicted | 3/3 | 3/3 | 3/3 |
| Final status correct | 4/6 | **5/6** | 6/6 |

19 credits. `run_external.py --check` still fails on the one reader disagreement, as it should.

## Second locked corpus (`data/holdout`, protocol 2): first run

Built, labelled and frozen by a separate process that was not allowed to read the engine, any result, any recording or the handoff, and was told only what the tool is for. Committed in `4f29db8` before the first call on it. Sources not used before: Common Paper Design Partner Agreement (CC BY 4.0, exported), Bonterms SLA 1.0 (CC BY 4.0, the publisher's PDF edited in place), GitHub Balanced Employee IP Agreement (CC0, exported), plus the Bonterms NDA publisher PDF for the fine-print pair. Eight pairs: 6 substantive edits (and/or, exclusive/non-exclusive, will/may, including/excluding, a party swapped, 30 days/5 days), 9 faithful rewordings (synonyms, a reordered list, passive to active, a merged and a split sentence, a dropped redundant word), one pair with newly concealed text, three with publisher fine print in both versions. `eval/holdout-results-first.json` is the untouched first output with the digests of the code that produced it.

| Measure | First run | Frozen target |
|---|---:|---:|
| Reviews completed without a failed step | 8/8 | 8/8 |
| Planted substantive edits held | 6/6 | 6/6 |
| Faithful rewordings cleared, as first scored | **8/9** | 9/9 |
| ... counting a rewrite listed as two parts of one card (erratum below) | 9/9 | 9/9 |
| False cover notes contradicted / true cover notes contradicted | 3/3 / 0 | 3/3 / 0 |
| Newly concealed text blocked (rules and Guard both) | 1/1 | |
| Reviews blocked for fine print both versions carry | 0/3 | 0 |
| Clauses held with no planted cause | 0 | |
| Final status correct | 8/8 | 8/8 |

**Scoring erratum, same kind as the first corpus.** The passive-to-active rewrite ("are found invalid ... by any court" to "any court ... finds ... invalid") was cleared by the application: one card, class reworded, Verify same both ways, Compare same, status ready. The scorer inherited from the first corpus wants both sides of an edit inside one part of a card; this rewrite is listed as a deletion in one part and an insertion in another, so the scorer reports it "not found". Both counts are reported. The card-level matcher was added after the first run and is not a model improvement.

All three in-place-edited files, and only those, were recognised as edited in place. This corpus is small, ours, and labelled by one process without legal review. It is a fresh check of the fixes, not a contract benchmark.

## Routing on the second corpus: ten cases, three retrieval arms, first run

A new fictional policy (notice of non-renewal by deal size, renewal price uplift by deal size, auto-renewal), two passages carrying explicit restoration grants for a stated range, one passage reserving a change for another approver. Deal facts are supplied, as before.

| Arm | Correct | Too strict | Too loose |
|---|---:|---:|---:|
| Rerank-selected passage | **9/10** | 1 | 0 |
| All passages supplied | 7/10 | 3 | 0 |
| First passage only | 5/10 | 5 | 0 |

Here retrieval did change the outcome: with the one passage for the right deal size, Decide was sure the restoration grant applied in two of the three restoration cases; with all five passages in front of it, it was sure in none and the change went to a person. The Rerank miss is the case exactly at the $60,000 threshold (restore 0.55: not an answer, so a person decides). No arm produced a loose route. Ten cases, one run, one policy: this shows the mechanism working on unseen inputs, not a general routing accuracy. The first corpus's six-case control still shows a tie (4/6 in every arm) because its policy file is frozen without restoration marks.

## Cost and what is still open

Round ledger after this work: **1,120 credits** charged, 0 unsettled, of the 1,350 send limit (19 for the regression re-run, 456 for the second corpus). Same ledger file, appended only.

Still not met or not done:
- one reader disagreement on a faithful rewording in the first corpus (kept as held);
- no completed paired observation with an intended user. One person did phase A only. No productivity claim is made anywhere;
- scans, OCR, multilingual contracts, negotiated customer paper and independent legal adjudication remain unmeasured.

```sh
uv run python eval/run_holdout.py            # offline reproduction of the table above
uv run python eval/run_holdout.py --check    # fails on the one strict routing miss and the first-scored 8/9
```
