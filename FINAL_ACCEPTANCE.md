# Final integration acceptance — PR #2

## Version and scope

The verified integration is `main@52717ca` (the maintainer's merged PR #1 work) plus PR #2's hidden-text integrity fix and handoff wording correction. This is the code under review, not a deployed release. The owner requested **no merge**.

Repository inventory found PR #1 merged and PR #2 open. No additional Claude product PR or `claude/product-evidence-and-demo` branch was present when checked. The merged maintainer changes are already the base of PR #2; an unpublished later change cannot be accepted as part of this integration. Commit-specific remote CI results are linked in the PR conversation.

## Engineering acceptance

| Gate | Evidence | State |
|---|---|---|
| Concealed value edits cannot receive an inherited-print exemption | Currency, signed amounts, decimal/percent and inequality PDF regressions; benign Guard still leads to blocked status and a non-signing reply | Pass |
| Preserve genuine inherited print | NFC-equivalent Unicode and whitespace normalize; currency, punctuation, operators, case and non-Latin text remain significant; duplicates match one for one | Pass |
| Full Python regression | 991 passed, 1 skipped, on the integrated core | Pass |
| Existing development corpus and four ablations | 15/15 reviews complete; 30/30 substantive edits held; 27/30 routes correct; zero loose routes; all four reader configurations complete on 24 cases | Pass, with disclosed conservative routing misses |
| Hard-set regression | 24/24 substantive edits held; 14/15 faithful rewordings cleared | Pass, with one known false hold |
| First external corpus | 6/6 substantive edits held; 2/3 faithful rewordings cleared; 5/6 statuses correct | Regression preserved; quality target remains unmet |
| Second external corpus | 8/8 statuses; 6/6 substantive edits held; 9/9 faithful rewrites at card level, 8/9 under the original scorer; routing 9/10 versus 7/10 and 5/10; no loose routes | Regression preserved; original quality gate remains unmet |
| Review-to-message consistency | Blocked review stays non-signable before and after suggested decisions; wrong-file case prepares only a request for the correct file; clean review can be signed off | Pass in browser |
| Honest handoff state | UI prepares a copyable summary or file request; it does not claim delivery to an approver or counterparty. Drafts require review before sending | Fixed and verified in browser |
| Source/built UI and responsive behavior | JavaScript syntax checks; `web/app.js` equals `docs/app.js`; 320, 390, 1024 and 1440px widths without horizontal overflow; phone summary retains both copy actions | Pass |
| API cost and frozen evidence | No new paid calls, no changed frozen input, recording, first output or shared budget ledger | Pass |

The external `--check` gate still fails on the held Common Paper rewording. The holdout `--check` gate still fails on its original 8/9 part-level scoring and the 9/10 routing result. Green regression CI does not convert either quality target into a pass. Thresholds were not lowered to clear those targets.

The static demo rebuild was checked. Parallel completion can reorder the displayed audit requests, so the already-committed case files are retained. This PR updates the built JavaScript and does not rewrite the recorded case evidence.

## Remaining gap to a 95-point award target

**A 95/100 result is not established.** The official criteria require practical usefulness and execution as well as integration quality. More tests alone do not prove those points.

1. **Real user value remains unmeasured.** There is no completed paired observation with an intended user. Complete the existing protocol with consenting, English-capable intended users, report hits, false findings, time and corrections, and retain its order/task/sample-size limitations. AI previews are functional QA only.
2. **Generalization remains limited.** The external tests use short public templates with our mutations and labels, without independent legal adjudication. Bigger or expert-labelled evidence is needed for broader reliability claims. The known conservative misses remain visible.
3. **Final product/video review needs the actual product changes.** The existing video records the last merged interface; the handoff wording in PR #2 has changed. Review or refresh the recording on the final accepted UI before claiming an exact current-interface walkthrough. No separate new Claude product artifact was available to verify.
4. **Submission and deployment remain separate gates.** Review the actual combined PR head, the current official submission rules and the intended demo/source links. Keep the release reachable through judging. Neither a merge, a deployment nor an official-entry update was performed in this acceptance.

The receiving product contributor should submit an inspectable branch/PR, not a claim that work is complete. After it is available, check the combined diff, rerun the affected tests and browser flows, and record the exact final head in the PR before the owner decides on merge/deployment.
