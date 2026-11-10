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

## What would strengthen a subsequent submission

1. **Independent documents before more features.** Obtain public or authorised contracts from several origins, preserve their original exports and define exact edit labels before running them. Include different deal sizes, absent deal facts, added clauses, meaningful address/notice changes, benign rewrites and failed API steps. Keep those cases separate from the examples used to tune prompts.
2. **Lock the evaluation before tuning.** Commit case hashes, labels, thresholds and metrics before the first live run. Report the first result separately from fixes. Locking this project's existing, already-inspected samples now would not make them held-out data.
3. **Measure the whole route.** Keep denominators for every expected change, every statement and every route, including failures. Distinguish the correct playbook passage, an overly strict route and a route that is too permissive. Exercise Rerank `expand` with real recorded responses; current evidence for that branch is a unit test.
4. **Measure reviewer outcomes.** Compare manual review with assisted review on the same authorised task: time, missed changes, unnecessary escalations and reviewer corrections. Endpoint confidence is not measured accuracy.
5. **Keep modules around responsibilities.** Cover-note parsing belongs in `referee/note.py`; PDF layout and deterministic classification stay in `textdoc.py` and `rules.py`; transport stays in `gateway.py`; pure evaluation accounting belongs in `eval/scoring.py`. Retain the engine as the orchestrator. Further extraction should follow a concrete change, not create a parallel framework.

Useful comparisons are [SignSafe](https://github.com/BuildwithDan911/signsafe) for input and product breadth, [FixProof](https://github.com/MuhammadFaizan-prog/fixproof/blob/main/EVAL.md) for locked evaluations and disclosed post-tuning results, [AgentAudit](https://github.com/Himanshu-Sangshetti/agentaudit/blob/main/bench/PROTOCOL.md) for external baselines and ablations, and [Docket](https://github.com/j4yop/docket/blob/main/benchmarks/necessity-results.md) for inspectable endpoint contributions. Their published results concern different tasks and cannot be used as a head-to-head accuracy ranking. This work does not copy their implementation.
