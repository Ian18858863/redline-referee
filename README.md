# Redline Referee

**Catch what changed in the contract they sent back.**

You send a customer your agreement. They return a "clean copy" with a note: *we only fixed typos and updated our address*. Somebody now has to read two PDFs side by side to find out whether that is true.

Redline Referee does that reading. Give it the version you sent, the copy that came back and the cover note. It returns:

- every difference, located on **both original PDFs**, each one read three times: by deterministic rules, by Clocked Compare and by Clocked Verify;
- each statement of the cover note, checked by Clocked Verify and labelled supported, contradicted or not stated;
- for each real change, the passages of your negotiation playbook that apply to a deal of this size, picked by Clocked Rerank, and a route: accept, counter or escalate;
- one status that code, not a model, decides: **Ready to sign**, **Changes to resolve**, **Do not sign** or **Review incomplete**.

Entry for the Clocked Build Challenge 2026, built on November 9 and 10, 2026. Awards entered: **Best Use of Verify, Compare, or Rerank**, and Best Overall App.

| | |
|---|---|
| Live demo (replays recorded Clocked responses, no key needed) | https://ian18858863.github.io/redline-referee/ |
| Video, 2 min 55 s | https://ian18858863.github.io/redline-referee/demo.mp4 |
| Evaluation on 12 seeded pairs | [`eval/RESULTS.md`](eval/RESULTS.md) |

## The four demo cases

Every answer shown in the demo is a recorded response of the live Clocked API (`recordings/`), with its request ID and credits.

| Case | What the customer did | Status | Calls | Credits |
|---|---|---|---|---|
| Quiet changes | Note says "only typos and our address". Four terms moved (routed: one accept, one counter, two escalate), one sentence was reworded faithfully | **Do not sign** | 21 | 139 |
| Quiet changes with a hidden instruction | Two digits of the fee swapped, two clauses rewritten, white 1 pt text telling AI reviewers to approve | **Do not sign** | 18 | 134 |
| Disclosed changes | Three terms changed and disclosed, one of them a single word: `maximum` became `minimum` | **Changes to resolve** | 18 | 119 |
| Clean return | Typos fixed, address updated, note accurate | **Ready to sign** | 10 | 81 |

## What each endpoint changes

A call whose result nothing depends on is not in this application. `tests/test_engine.py` holds a test for each row, and the interface has a panel, "What each endpoint changed", that states for the open review what would have gone wrong without each one.

| Endpoint | What it reads | What its output changes |
|---|---|---|
| **Verify** `/clocked/grounding`, across versions | Every sentence that differs, as an explicit claim, against **the other version's PDF**, in both directions (`pdf` input, `threshold: 0.6`, `evidence: "all"`) | The reading of meaning. "Contradicted" holds a difference as a change, including a one-word edit the rules took for a spelling fix. "Supported" in both directions clears a faithful rewording, unless Compare objects. |
| **Verify** `/clocked/grounding`, cover note | Each sentence of the note as a claim, against the returned copy and the list of changes | A contradicted statement turns the status to **Do not sign** and is linked to the change that contradicts it. "Not stated" is shown as not stated, never as false, and never blocks. |
| **Compare** `/clocked/compare`, clause against clause | For each clause that differs: the clause as sent and the clause as returned, two text documents, `evidence: "all"` | The second reading of meaning. A conflict confirms a change or overrules a spelling fix; it also vetoes clearing a rewording. Its two passages are checked character by character and drawn on both pages. |
| **Compare** `/clocked/compare`, PDF against PDF | The two whole PDFs, `return_pdf: "highlight"` | A gate: below 0.5 "related", the review stops with "these are not two versions of one agreement". The marked copies it returns are a layer in the viewer. |
| **Rerank** `/clocked/rerank` | For each open change: the playbook passages, with the deal size (read by Extract) as `context` | Only passages that come back `use` at 0.9 or above are shown and passed to Decide, at most two. A passage written for a different deal size comes back `skip / different_case` and is not quoted. `expand` triggers a second request over the neighbouring passages of that topic. No usable passage sends the change to Legal. |
| **Decide** `/clocked/decide` | One change, the passages Rerank kept, and the deal facts Extract read. Two yes/no questions: may the deal desk accept this as written, and does the playbook give the desk a counter | Code turns the two probabilities into a route: accept at 0.6 or above; counter when accept is 0.4 or below and counter is 0.6 or above; escalate when both are 0.4 or below. Anything between 0.4 and 0.6 is not an answer and goes to a person. |
| **Extract** `/clocked/extract` | Thirteen key terms from each PDF, `return_pdf: "box"` | The side-by-side terms table, and the annual fee that Rerank receives as context. |
| **Guard** `/clocked/injection` | The cover note and the returned PDF as untrusted content, `return_pdf: "box"` | Text addressed to an AI reviewer blocks signing. |

Pipeline is not used: it accepts text only, and this application is built on PDF input.

## How the pieces work together

```
v1.pdf  v2.pdf  cover note
   |       |        |
  rule layer (no model): align clauses, diff word by word, classify, find hidden text
       |
       +--> Compare, clause against clause  --+
       +--> Verify, sentence against the      +--> three readings per difference; code decides
       |    other version's PDF, both ways  --+    promote a "spelling fix" / clear a rewording
       +--> Compare, PDF against PDF: is this one agreement?   Guard: note and returned PDF
       |
  list of changes ----> Verify (cover note, claim by claim, against v2 + the list)
       |
  per open change:  Extract (deal size) -> Rerank (playbook) -> Decide (route)
       |
  status, set by code:  blocked > incomplete > changes > ready
```

Why a rule layer at all: nobody should depend on a model noticing that 30 became 90. The rule layer finds every textual difference deterministically and quotes it verbatim. What it cannot do is read meaning. `maximum` to `minimum` is one word, two letters apart, and looks like a typo fix; a rewritten sentence looks like a change. Those two judgments are given to Verify and Compare, and a value that differs is never cleared by either.

What the whole-document Compare is not used for: on two versions of one contract it pairs almost every passage, so its marked PDF highlights nearly the whole page, and in our tests it missed one single-number change that the clause-level requests all caught. It is used for what it does reliably here, which is to say whether the two files belong together.

## Uncertain results, missing information, failed requests

| Situation | What happens |
|---|---|
| A Clocked request fails, times out or hits the credit cap | The step is listed by name, the status becomes **Review incomplete**, and it can never read "Ready to sign". The SDK retries 429 and 5xx under one Idempotency-Key, so a retried request is billed once. |
| Evidence offsets do not reproduce the quote | `text[start:end] == quote` is checked in Unicode code points. A mismatch is counted and shown as "evidence not aligned"; no approximate box is drawn. |
| A quote cannot be found on the page | The change is listed without a position rather than with a guessed one. |
| Verify and Compare disagree, or one of them did not answer | The stricter reading wins. A rewording is cleared only when Verify supports it in both directions at 0.8 or above and Compare does not read a conflict. |
| Compare or Verify reads a conflict in a passage the rules already identified (an address in a notices clause) | Shown, not promoted. |
| Decide lands between 0.4 and 0.6, or Rerank finds no passage, or either step fails | The change is escalated to a person. In the first demo case the liability cap is such a change: Decide answered 0.42, so code did not follow it. |
| Verify answers "not stated" for a cover-note statement | Shown as not stated. It does not block and is not treated as false. |
| A scanned PDF with no text layer | Refused with an explanation: nothing in it can be quoted character by character. |
| A request that was never recorded, in replay mode | A named error, not a silent fallback. |

Thresholds live in `referee/prompts.py`.

## Evaluation

Twelve seeded pairs of the agreement, each with two to four planted substantive edits, cosmetic edits, a cover note whose statements have known truth values, and in two pairs a hidden instruction. Ground truth is exact because every edit is a literal replacement (`referee/contracts.py`). All responses are recorded under `recordings/eval-*/`; `uv run python eval/run_eval.py` recomputes the table from them without calling the API.

| Measure | Result |
|---|---|
| Planted substantive edits found | 23 of 23 |
| ... classed as a change by the rule layer alone | 21 of 23 |
| ... read as a conflict by Compare, clause against clause | 23 of 23 |
| ... read as contradicted by Verify, sentence against the other PDF | 23 of 23 |
| One-word meaning changes the rule layer takes for spelling fixes, held as changes | 2 of 2 |
| Faithful rewordings cleared | 4 of 4 |
| Clauses wrongly held as substantive | 0 |
| False cover-note statements judged contradicted by Verify | 24 of 24 |
| True cover-note statements judged supported by Verify | 30 of 32 (the other 2 "not stated", shown as such, not blocking) |
| True statements wrongly judged contradicted | 0 |
| Hidden instructions flagged by Guard | 2 of 2, and 0 false alarms on the 10 pairs without one |
| Final status equal to ground truth | 12 of 12 |
| Credits for all 12 pairs | 945 |

Twelve variations of one agreement are a demonstration with exact answers, not an accuracy benchmark for contracts in general.

## Run it

Requirements: Python 3.10+ and [uv](https://docs.astral.sh/uv/).

```sh
uv sync
uv run pytest                      # 272 tests: rules vs ground truth, engine logic, recorded live responses
uv run python scripts/build_site.py  # rebuild docs/ from the recordings, no API key needed
uv run uvicorn referee.server:app --port 8787
```

Open http://localhost:8787. Without a key the four demo cases replay recorded Clocked responses. With a key, "Review your own PDFs" runs a live review of any two text-layer PDFs:

```sh
export CLOCKED_API_KEY=...         # never commit it; the key stays on the server
uv run uvicorn referee.server:app --port 8787
```

Re-record or extend:

```sh
uv run python scripts/build_cases.py           # regenerate the synthetic PDFs and ground truth
uv run python scripts/run_case.py --demo --live  # call Clocked and record every exchange
uv run python eval/run_eval.py --live          # run the evaluation pairs, record, write eval/RESULTS.md
```

`CLOCKED_CREDIT_CAP` (default 1500) is a hard stop per review.

## What is in the repository

| Path | Contents |
|---|---|
| `referee/rules.py`, `referee/textdoc.py` | The deterministic layer: PDF text, clauses, diff, classification, hidden text, locating quotes on pages |
| `referee/engine.py` | The review: the three readings and how code decides between them, cover note, Rerank, Decide, status |
| `referee/gateway.py` | The one door to Clocked: record, replay by request hash, credit cap, time budget, audit trail |
| `referee/prompts.py` | Every instruction, question and threshold sent to Clocked |
| `referee/contracts.py` | The synthetic agreement and the catalogue of edits with exact ground truth |
| `data/cases/` | 4 demo pairs and 12 evaluation pairs (PDFs, cover notes, `truth.json`) |
| `data/playbook.json` | The fictional negotiation playbook |
| `recordings/` | Every Clocked exchange behind the demo and the evaluation, with request IDs and credits |
| `web/` | The interface (no framework, no build step) |
| `docs/` | The built static demo (GitHub Pages) |
| `tests/`, `eval/` | Tests and the evaluation |

## Limits

- One synthetic agreement in sixteen variations (four demo cases, twelve evaluation pairs), plus one unrelated agreement with a different layout (an A4 non-disclosure agreement with inline headings, `tests/fixtures/other-agreement/`), reviewed end to end on the live API and recorded. The numbers above show that the application does what it claims on data with exact answers; they are not an accuracy figure for contracts in general.
- Text-layer PDFs only. Scans are refused.
- Clauses are found by their numbering. A document without numbered clauses is compared as paragraphs and reads less well.
- The rule layer treats a one-word edit within two letters as spelling until Verify or Compare says otherwise. If neither answers, that edit stays under spelling and the review is marked incomplete.
- The playbook is fictional and short. Two topics are missing on purpose, to show what happens when there is no guidance.
- Routing errs on the strict side. In the third demo case the playbook lets the deal desk extend the data export window to ninety days, and Decide still answered that it may not be accepted as written; the change is routed to counter. We left that answer as recorded.
- This is decision support for a person at a deal desk. It does not give legal advice and it does not sign anything.

## Data and disclosure

All documents, companies, people and playbook rules are fictional and were written for this project. No real contract or personal data is included. No API key is in the repository or its history.

The application was designed and written with AI coding assistance (Claude Code). `tests/fake_clocked.py` is a stand-in for the API used only by unit tests of our own logic; the demo, the evaluation and `tests/test_recordings.py` use real recorded responses.

MIT License.
