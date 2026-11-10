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
