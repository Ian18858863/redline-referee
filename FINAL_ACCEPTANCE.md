# Final integration acceptance

## Version and scope

The verified integration combines:

- `main@72ebf36`, which includes PR #2's hidden-text fix (`8fcf25d`), Claude's merged PR #3 and the refreshed narrated video;
- Claude's product branch at `bfbc679` (`7714266` adds who decides next, evidence explanations, the criteria gap table and study status; `bfbc679` rebuilds the static demo);
- Codex's prepared-handoff wording correction and final integration review.

PR #2 was merged elsewhere in the repository while this verification was in progress. This integration does not undo that merge, perform any further merge into `main`, deploy, or update the official entry. The owner explicitly requested **no further merge**; the combined work is submitted as a separate reviewable PR. Commit-specific CI is linked in its conversation.

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
| API cost and frozen evidence | New real SEC excerpt demo: 6 live calls, 19 credits; round total 1,139 / 1,500. Frozen corpora and first outputs unchanged | Pass |

The external `--check` gate still fails on the held Common Paper rewording. The holdout `--check` gate still fails on its original 8/9 part-level scoring and the 9/10 routing result. Green regression CI does not convert either quality target into a pass. Thresholds were not lowered to clear those targets.

The static demo rebuild was checked. Parallel completion can reorder the displayed audit requests, so the already-committed case files are retained. This PR updates the built JavaScript and does not rewrite the recorded case evidence.

## Remaining gap to a 95-point award target

**A 95/100 result is not established.** The official criteria require practical usefulness and execution as well as integration quality. More tests alone do not prove those points.

1. **Real user value remains unmeasured.** There is no completed paired observation with an intended user. Complete the existing protocol with consenting, English-capable intended users, report hits, false findings, time and corrections, and retain its order/task/sample-size limitations. AI previews are functional QA only.
2. **Generalization remains limited.** The external tests use short public templates with our mutations and labels, without independent legal adjudication. Bigger or expert-labelled evidence is needed for broader reliability claims. The known conservative misses remain visible.
3. **Final video/UI synchronization remains limited.** Claude's new first-screen route and criteria explanations are included and inspected. The owner’s other agent merged PR #3 and refreshed the narrated video in main@72ebf36 during acceptance. That recording covers the authored examples; this PR adds the real public-filing excerpt case and the prepared-handoff wording. It is not a recording of this new case.
4. **Submission and deployment remain separate gates.** Review the actual combined PR head, the current official submission rules and the intended demo/source links. Keep the release reachable through judging. Neither a merge, a deployment nor an official-entry update was performed in this acceptance.

Product review also corrected two evidence statements: a five-second recorded example is not a latency promise, and the fixed-order v1 study does not implement B/A counterbalancing. No participant outcomes were added. Any later contribution must be reviewed on its actual branch/PR, with affected checks rerun on the final head before the owner decides on merge/deployment.

## Real public filing check

Aclaris SEC filings, 2023 and 2024; short verbatim excerpts only, not complete-contract coverage. Both actual changes held, zero errors, six fresh API calls, 19 credits. Offline replay repeated the result with zero calls. Provenance, exact excerpts and exported-PDF hashes are in data/public-filings. A user feedback request is pending; completed paired human studies remain zero.

Update: the owner subsequently inspected the real case and supplied one qualitative feedback observation (salary change was clear; perceived usefulness; incomplete interface comprehension). See study/OWNER_FEEDBACK.md. Completed paired/timed studies remain zero; no efficiency figure was measured.
