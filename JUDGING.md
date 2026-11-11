# Reviewing Redline Referee

This is a reviewer's guide based on the [published challenge criteria](https://clocked.rezolve.ai/challenge), not an official score or a prediction of an award. The application checks what changed in a returned contract, whether the cover note describes it honestly, and how the deal desk should respond.

## A three-minute evidence trail

Open the [recorded demo](https://ian18858863.github.io/redline-referee/). The public site follows `main`; proposed pull-request changes are available locally until merged. Every built-in result is replayed from a recorded Clocked response. A replay demonstrates the saved input, not arbitrary new documents.

1. **Quiet changes:** select the payment-term change. Follow its exact quotes on both PDFs, the two readers, the applicable playbook passage, and the counter route. Then open the cover note: the status also depends on whether the customer's explanation is true.
2. **Two readers, one disagreement:** inspect a difference that Verify read as the same meaning and Compare read as a conflict. The stricter reading holds the change. Compare this with a faithful rewording that is cleared. The point is the change in the decision, not the number of calls.
3. **A note that talks to your AI:** Guard supplies the blocker. Then select **The wrong file came back**: the document gate stops the review before the remaining API calls. These demonstrate distinct controls.
4. Inspect the proposed reply and its claim checks. An unsupported sentence is removed. A reviewer still owns the decision and the message.

The UI's **How it works** panel shows the actual endpoint effects, probabilities, request identifiers and recorded credits for that case. Do not present replay speed as live latency.

## How the implementation maps to judging

| Published criterion | Evidence in this project | Remaining improvement |
|---|---|---|
| Practical usefulness | A returned contract, an explanation, a negotiation playbook and an actionable route | Observe an authorised deal-desk user completing the task; measure review time and corrections |
| Core integration and group depth | Verify and Compare affect whether a difference is held; Rerank selects the guidance used by Decide | Measure the complete flow on independently authored agreements |
| Group breadth | All three knowledge endpoints have distinct effects and recorded examples | Do not add an endpoint merely for a breadth claim |
| Additional endpoints | Extract supplies deal context; Guard blocks instructions; Fill drafts a checked reply | Show a positive and a benign case for each claimed contribution |
| Implementation and user experience | Source quotes, per-change decisions, phone layout, partial-failure states | Test real exports, longer agreements and keyboard-only review with users |
| Explanation and reproducibility | Public recordings, exact planted edits, automated tests, reader ablations | Keep missing results in denominators and separate post-tuning results from independent validation |

## Reproduce without credentials

```sh
uv sync --locked
uv run pytest -q
uv run python eval/run_hard.py
uv run python eval/run_eval.py --check
uv run python scripts/build_site.py
uv run uvicorn referee.server:app --port 8787
```

The tests include injected failures and authored fake API responses to verify application policy. The evaluation and built demo replay real saved responses. No new model calls are required by these commands. The test configuration isolates local Clocked credentials and spending limits before the dotenv loader runs. `--check` rejects incomplete full reviews or missing expected claims/routes; `--check-ablation` additionally requires complete counterfactual replays; it does not require a perfect model score. Known model mistakes remain visible in `eval/RESULTS.md`. The `two-readers` / without-Compare counterfactual originally lacked a cover-note response because the changed ledger produces a new request hash. The missing request was recorded separately on 11 November 2026 (one live Verify call, 10 credits); it now replays by its exact hash. No baseline answer was substituted.

## Which numbers are what

Three kinds of result appear in this repository. They are not interchangeable.

| Kind | What it is | Where | What it supports |
|---|---|---|---|
| **First output** | The untouched result of the first run on inputs frozen beforehand | `eval/external-results-first.json` (first corpus), `eval/holdout-results-first.json` (second corpus, with the digests of the code that ran) | The only numbers that say anything about unseen inputs |
| **Scoring correction** | The same answers counted by a corrected scorer, reported beside the first count | First corpus: the scorer required a whole planted phrase inside a bounded quote. Second corpus: 8/9 rewordings as first scored, 9/9 when a rewrite listed as two parts of one card is matched | That the application's answer was right and the count was wrong. Not a model improvement |
| **Post-fix regression** | Inputs that were looked at while fixing, run again after the fix | First corpus after the maintainer fixes: 2/3 rewordings, 5/6 statuses. All development pairs (`eval/RESULTS.md`) | That a fix did what it was meant to and broke nothing recorded. Not a holdout score |

The development numbers (30/30, 0/41, 30/30, 15/15, 27/30) are regression evidence on one synthetic agreement in 25 variations. The video's closing card and the entry form quote those and the second corpus's first output, and say which is which.

## Gaps against the five published criteria

The weights are the challenge's. The assessments are ours, each tied to something you can open. No score is claimed.

| Criterion (weight) | Evidence that exists | What is missing or weak |
|---|---|---|
| **Practical usefulness (25)** | A complete task: two PDFs and a note in, a status, located changes, routes and a checked reply out. Runs live on uploaded PDFs in about five seconds (`referee/server.py`, shown in the video) | **No completed observation with an intended user.** One person did the manual half of `study/PROTOCOL.md` and stopped. No time saved, no reduction in misses, nothing about real deal desks is measured or claimed. The playbook is fictional. The public demo is a replay; live review needs a local key |
| **Quality of the core Clocked integration (30)** | Verify and Compare each decide outcomes the other would get wrong: without Verify 20/20 faithful rewordings are held and 39/39 false statements pass; without Compare 4 real changes are cleared (`eval/RESULTS.md`, recomputed from recordings). On the blind second corpus: 6/6 edits held, 9/9 rewordings cleared, 8/8 statuses | Small, self-labelled corpora (25 synthetic pairs, 14 pairs on public templates). One rewording is still held because the readers disagree. No independent legal adjudication. Scans, OCR and non-English contracts are unmeasured end to end |
| **Thoughtful use of additional endpoints (15)** | Rerank changes the route: 9/10 with its passage against 7/10 with all passages and 5/10 with the first, no loose route (second corpus, ten cases). Decide's unsure answers go to a person. Guard is the only blocker in one case. Fill drafts a reply and Verify removes unsupported sentences. Extract supplies deal facts | The first corpus's routing control is a tie (4/6 in every arm). Rerank's `expand` never occurred in a recording. Routing is too strict on one development edit in all three pairs that contain it. Extract's thirteen terms fit a subscription agreement only. Protect and Inspect are not used |
| **Implementation and user experience (20)** | 991 tests in CI, 69 malformed-response variants, failure states that never read as a pass, a phone layout, exact quotes located on both pages, a first screen that pairs the customer's claim with the change and who decides next | The interface was checked by automated walkthroughs and by us, not by intended users. Reviewer decisions are not persisted across reloads. Multi-column pages and tables are read by heuristics |
| **Clear explanation and reproducibility (10)** | Every figure recomputes offline from recordings (`eval/run_eval.py`, `run_hard.py`, `run_external.py`, `run_holdout.py`); frozen inputs with hashes; first outputs and errata kept; limits listed in the README | The evidence is spread over four documents. |

## What the rules allow after the deadline

Checked on the [official rules](https://clocked.rezolve.ai/build-challenge/rules) on 10 November 2026, 11 p.m. ET ("Changes: none since publication on October 19, 2026"):

- "Entries cannot be submitted or changed after the submission deadline; the entry as saved at the deadline is the one judged." The deadline is 10 November 2026, 11:59 p.m. Eastern Time. The entry is the form: project text, demo link, repository link, awards, team.
- "The application must be started and developed during the building period" (21 October to 10 November 2026) and "Keep the repository's development history available so judges can see what was built and when."
- "Keep the demo and the repository publicly accessible through the winners' announcement on November 18, 2026."

The rules do not say whether the linked repository or demo may change during judging. Because development must happen in the building period and the dated history is what judges read, this project treats 11:59 p.m. ET as a freeze for `main` and the deployed demo. Anything not merged by then stays on its branch, visible and dated, and is not presented as part of the entry.

## What would strengthen a subsequent submission

The new [external-source results](eval/EXTERNAL_RESULTS.md) implement the pre-run source/label lock and the controlled retrieval comparison below. They exposed inherited tiny-print blocking, held faithful rewordings and no routing uplift. The first two were traced to general causes and fixed, restoration authority was modelled as policy data, and all three were then run once on a second corpus that was built blind and frozen first: 8/8 statuses, 6/6 substantive edits held, 9/9 rewordings cleared (8/9 as first scored), routing 9/10 with Rerank against 7/10 with all passages and 5/10 with the first passage, no loose route. Still open: one reader disagreement on the first corpus, kept as held, and no completed paired user observation. Post-fix results on the first six pairs are regression evidence. The [maintainer handoff](HANDOFF.md) gives concrete release gates and validation commands.

1. **Independent documents before more features.** Obtain public or authorised contracts from several origins, preserve their original exports and define exact edit labels before running them. Include different deal sizes, absent deal facts, added clauses, meaningful address/notice changes, benign rewrites and failed API steps. Keep those cases separate from the examples used to tune prompts.
2. **Lock the evaluation before tuning.** Commit case hashes, labels, thresholds and metrics before the first live run. Report the first result separately from fixes. Locking this project's existing, already-inspected samples now would not make them held-out data.
3. **Measure the whole route.** Keep denominators for every expected change, every statement and every route, including failures. Distinguish the correct playbook passage, an overly strict route and a route that is too permissive. Exercise Rerank `expand` with real recorded responses; current evidence for that branch is a unit test.
4. **Measure reviewer outcomes.** Compare manual review with assisted review on the same authorised task: time, missed changes, unnecessary escalations and reviewer corrections. Endpoint confidence is not measured accuracy.
5. **Keep modules around responsibilities.** Cover-note parsing belongs in `referee/note.py`; PDF layout and deterministic classification stay in `textdoc.py` and `rules.py`; transport stays in `gateway.py`; pure evaluation accounting belongs in `eval/scoring.py`. Retain the engine as the orchestrator. Further extraction should follow a concrete change, not create a parallel framework.

Useful comparisons are [SignSafe](https://github.com/BuildwithDan911/signsafe) for input and product breadth, [FixProof](https://github.com/MuhammadFaizan-prog/fixproof/blob/main/EVAL.md) for locked evaluations and disclosed post-tuning results, [AgentAudit](https://github.com/Himanshu-Sangshetti/agentaudit/blob/main/bench/PROTOCOL.md) for external baselines and ablations, and [Docket](https://github.com/j4yop/docket/blob/main/benchmarks/necessity-results.md) for inspectable endpoint contributions. Their published results concern different tasks and cannot be used as a head-to-head accuracy ranking. This work does not copy their implementation.
