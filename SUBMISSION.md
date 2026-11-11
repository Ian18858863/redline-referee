# Redline Referee — submission copy

## Project overview

Redline Referee turns a returned contract into a reviewable decision: what changed, where the evidence is, whether the cover note tells the truth, and who should decide next.

Upload two PDFs and the counterparty's note. Deterministic comparison preserves changed values; Clocked Verify and Compare read meaning independently. The reviewer sees exact passages on both pages, disagreements that need attention, and a status set by code. A negotiation playbook supplies accept, counter or escalate guidance. A proposed reply is checked sentence by sentence before the reviewer copies it.

The evidence includes recorded live API responses, reader ablations and two locked external-source corpora. On the eight-pair second corpus, all six planted substantive edits were held and all eight final statuses matched their labels. The ten-case routing control scored 9/10 with Rerank-selected guidance, versus 7/10 with all guidance and 5/10 with the first passage. These are small, labelled evaluations, not general contract-accuracy claims.

A separate public-filing example compares genuine 2023 and 2024 Aclaris employment-agreement excerpts. Its live run held both substantive changes with zero failed stages: six calls, 19 credits. The source documents remain linked for inspection.

Our ambition is a dependable evidence layer for contract decisions: a reviewer can move from an assurance to the exact change, the applicable authority and the next action. The reviewer remains responsible for approval. Human productivity improvement has not yet been measured.

## Clocked integration

Verify and Compare are independent readers whose answers affect classification. Verify checks changed sentences against the other version in both directions. Compare checks the clauses for a conflict. A faithful rewording clears only when Verify supports both directions and Compare does not object. Changed values are retained by deterministic rules. Disagreement holds the change for review.

Verify also checks the counterparty's cover note against the returned document and detected changes. A contradicted assurance can block signing. A whole-document Compare gate stops unrelated files before further review.

Rerank selects the negotiation-playbook passages applicable to a change and deal context. Decide answers policy questions using those passages; code maps sufficiently clear answers to accept, counter or escalate. An uncertain answer or missing authority goes to a person. Extract supplies structured deal facts and PDF locations for the subscription-agreement workflow.

Guard checks untrusted document and note content for instructions aimed at the AI reviewer. Concealed content is also examined by code; unchanged inherited fine print is distinguished from newly concealed text. Fill drafts a reply from the reviewer's decisions, then Verify checks its sentences. Unsupported sentences are removed. The interface prepares a handoff for the reviewer to copy and share.

Seven endpoints have distinct roles. Their request hashes, saved responses and credit metadata are inspectable. Replay works without credentials; new reviews require a local API key. Tests exercise failure states and malformed replies, and the evaluations reproduce from recordings. The public employment-agreement example focuses on change review; the separate negotiation cases demonstrate the subscription playbook and reply workflow.

## Links and release scope

- [Repository](https://github.com/Ian18858863/redline-referee)
- [Proposed final integration and real-contract example — PR #4](https://github.com/Ian18858863/redline-referee/pull/4)
- [Published demo](https://ian18858863.github.io/redline-referee/)
- [Narrated full-workflow video](https://ian18858863.github.io/redline-referee/demo.mp4)
- [Evaluation and first-output evidence](eval/EXTERNAL_RESULTS.md)
- [Judging guide](JUDGING.md)
- Real public-filing case in this delivery: open the packaged site at `#case=aclaris-public-filings`. This addition is proposed on the integration branch; do not describe it as deployed until that branch is published.

The existing video demonstrates authored negotiation cases. The new SEC example uses verbatim excerpts of actual filed agreements, exported to PDFs; it is not a full-agreement review or a completed human-user study. There are zero completed paired human observations.

## Public-filing sources

- [2023 Aclaris / Joseph Monahan amended and restated employment agreement](https://www.sec.gov/Archives/edgar/data/1557746/000155837023010987/acrs-20230609xex10d1.htm)
- [2024 second amended and restated employment agreement](https://www.sec.gov/Archives/edgar/data/1557746/000155837024001753/acrs-20231231xex10d15.htm)

These historical public versions show annual base salary changing from $280,800 to $430,000 and the benefit-plan comparator changing from part-time to full-time employees. The demo's source note identifies the included sections. It makes no claim that Aclaris used or endorsed this application.

One later qualitative owner observation found the salary change clear and potentially useful for review, while noting incomplete understanding of the interface. This is owner feedback, not an independent or timed study; [context and limits](study/OWNER_FEEDBACK.md).
