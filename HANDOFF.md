# Maintainer handoff for PR #1

Continue on `codex/review-completeness-and-evaluation`, PR https://github.com/Ian18858863/redline-referee/pull/1. Do not push to `main`, merge, enable auto-merge or deploy without the owner's final decision. Keep the competition product, demo and public materials in English.

## Implemented and reviewable

- Cover-note fact extraction and mixed notice/address protections from the earlier PR iteration; pure evaluation accounting and complete four-way ablation replay.
- Reply generation validates completed review state and every open reviewer decision at both Python and HTTP boundaries. A blocked review never promises countersignature. Unsupported, malformed, reordered or invalid-probability reply claims fail closed. If all paragraphs are removed, no closing-only customer reply is released.
- Mixed-font PDF fragments sort by baseline instead of glyph-box top; native Bonterms mutations remain under the correct clauses. New layout and scoring regressions accompany the fix.
- English first-screen evidence pairing uses the open review's actual cover claim and changed term, with a direct evidence action. Recorded-case shortcuts remain available in a disclosure. Wording disagreements are labelled as needing human review. Phone layout is compacted.
- Three public source agreements, licence attribution, six PDF pairs, pre-run hashes/labels, actual Clocked responses, a durable shared credit ledger and three routing retrieval arms. Both first output and unsuccessful experiments are retained.
- A local English workflow-observation tool; raw responses stay under ignored `out/study/`. One human submitted only phase A and could not complete the comparison. No paired outcome or productivity benefit may be claimed. AI previews are QA only.

Read `eval/EXTERNAL_RESULTS.md` before describing results. The production routing change was **reverted** after full-corpus safety regressions; the experimental 6/6 result is not the shipped behavior. Current development routing remains 27/30, all three misses conservative. No Rerank uplift was established in the new controlled fixture (4/6 for each arm).

## Highest-priority remaining work

1. Review the mixed-font ordering and reply invariants for regressions. Preserve hidden-content and instruction blockers and reject any new route that expands authority without evidence.
2. Address the unchanged Bonterms tiny licence-print false blocker with a general, tested distinction between inherited fine print and newly concealed content. Keep all text inspectable and ensure engine status, UI, exports and reply state agree. Do not special-case a publisher or corpus answer.
3. Reduce the two faithful-rewording holds without weakening recall of duties, negations, values and one-sided clauses. Use a separate locked source/mutation set after implementation; current six pairs are now regression data. Do not lower confidence thresholds just to pass these cases.
4. Model authority to restore the original term explicitly. A prompt-only expansion passed six local cases but incorrectly countered clauses requiring an approver in the full corpus. Recheck both the controlled component test and all original routes; zero new loose routes is a release gate.
5. Run a completed paired observation with an English-capable intended user, using `study/PROTOCOL.md`; report task differences, model precomputation, order effects and sample size. Keep participant answers private. Do not count the AI walkthrough as user-value evidence.
6. Review the final English demo and record the current interface if the video no longer matches. The public Pages site still follows `main`; PR changes have not been deployed. Check current official submission requirements before final delivery.

## Validation and credit boundaries

```sh
uv sync
uv run pytest -q
uv run python scripts/build_site.py
uv run python eval/run_eval.py --check --check-ablation
uv run python eval/run_hard.py
uv run python eval/run_external.py
uv run python eval/run_routing.py
node --check web/app.js
node --check docs/app.js
```

The last two evaluators report known quality misses; their `--check` acceptance gates currently fail. Passing CI proves code/recording regressions, not all award targets. Inspect `LOCK.json`, the preserved first output and all new errors rather than filtering failed cases.

The owner authorized at most 1,500 credits for this round: 645 charged, 0 unsettled at handoff. The remaining authorization is 855, with only 705 available under the runner's 1,350 send limit and its 150-credit safety margin. Continue the same ledger if making authorized calls; never reset it or run concurrent live processes. Prefer offline replay. Keys stay in ignored local environment files, never prompts, source, reports or PR comments. The GitHub credential cannot modify workflow files; this work leaves `.github/workflows/` unchanged.

Latest commit-specific CI and review status are reported in the PR conversation. Inspect the actual head before acting. Maintainer approval and final owner authorization remain required for merge/deployment.

## Maintainer update, 10 November 2026 (ET)

Reproduced as handed over (942 tests, four offline evaluators, browser walkthrough). Then, on this branch:

| Gate from the list above | State |
|---|---|
| 1. Mixed-font ordering and reply invariants | Reviewed, kept. Hidden-content and instruction blockers preserved; no route gained authority |
| 2. Unchanged tiny licence print | **Fixed generally**: concealed text blocks only when the version we sent does not carry the same text. Engine status, reasons, listed passages, UI and reply state agree. Checked on the second corpus: 0 of 3 fine-print pairs blocked, the newly concealed pair blocked by rules and Guard |
| 3. Faithful-rewording holds | **One fixed at its cause, one kept.** The Bonterms hold came from the in-place edit (replacement words drawn last in the text layer); edited PDFs are now read in reading order. The Common Paper hold is a real disagreement between Verify and Compare and stays held. No threshold changed. Second corpus: 9 of 9 rewordings cleared, 6 of 6 substantive edits held |
| 4. Restoration authority | **Modelled as policy data** (`restore_original` on a passage, a third question asked only then, unsure goes to a person). Development routes replay unchanged, zero loose. Second corpus: 9/10 with Rerank, no loose route in any arm |
| 5. Paired observation with an intended user | **Not done.** No participant was available before the deadline. No benefit is claimed |
| 6. English demo and video | Checked at four sizes, English only, no overflow, no console errors. Video re-recorded on the current interface |

Evidence, first outputs and errata: the maintainer section of `eval/EXTERNAL_RESULTS.md`. Round ledger: 1,120 credits charged of the 1,350 send limit, 0 unsettled. Merge and deployment were done by the receiving maintainer on the owner's delegation, after CI passed on the final head.
