# Redline Referee

**Catch what changed in the contract they sent back.**

You send a customer your agreement. They return a "clean copy" with a note: *we only fixed typos and updated our address*. Somebody now has to read two PDFs side by side to find out whether that is true.

Redline Referee does that reading. Give it the version you sent, the copy that came back and the cover note. It returns:

- every difference, marked on **both original PDFs** by Clocked Compare;
- each statement of the cover note, checked by Clocked Verify and labelled supported, contradicted or not stated;
- for each real change, the passages of your negotiation playbook that apply to a deal of this size, picked by Clocked Rerank, and a route: accept, counter or escalate;
- one status that code, not a model, decides: **Ready to sign**, **Changes to resolve**, **Do not sign** or **Review incomplete**.

Entry for the Clocked Build Challenge 2026, built between November 9 and November 10, 2026. Award entered: **Best Use of Verify, Compare, or Rerank** (and Best Overall App).

<!-- LINKS -->

## What each endpoint changes

A call whose result nothing depends on is not in this application. This table is the contract; `tests/test_engine.py` holds one test per row.

| Endpoint | What it reads | What its output changes |
|---|---|---|
| **Compare** `/clocked/compare` | The two PDFs as PDF documents, `return_pdf: "highlight"`, `evidence: "all"`, with instructions that define a conflict between two versions of one agreement | (1) The marked copies of both PDFs are what the reviewer looks at. (2) A rule finding it confirms is labelled as found twice. (3) It **overrules the rule layer in both directions**: a one-word edit the rules class as a spelling fix (`maximum` to `minimum`) becomes a change to resolve when Compare reads the passages as conflicting; a rewritten sentence the rules class as a change is cleared when Compare reads both versions as the same statement. |
| **Verify** `/clocked/grounding` | Each sentence of the cover note as an explicit claim, against the returned copy and the list of changes, `threshold: 0.6`, `evidence: "all"` | A contradicted statement turns the status to **Do not sign** and is linked to the change that contradicts it. "Not stated" is shown as not stated, never as false. |
| **Rerank** `/clocked/rerank` | For each open change: the playbook passages, with the deal size (read by Extract) as `context` | Only passages that come back `use` are shown and passed to Decide. A passage written for a different deal size comes back `skip / different_case` and is not quoted. `expand` triggers a second request over the neighbouring passages of that topic. No `use` passage at all sends the change to Legal. |
| **Decide** `/clocked/decide` | One change plus the passages Rerank kept | The suggested route. Followed only at confidence 0.6 or above; code can tighten it, never loosen it. |
| **Extract** `/clocked/extract` | Thirteen key terms from each PDF, `return_pdf: "box"` | The side-by-side terms table, and the annual fee that Rerank receives as context. |
| **Guard** `/clocked/injection` | The cover note and the returned PDF as untrusted content | Text addressed to an AI reviewer blocks signing. |

Pipeline is not used: it accepts text only, and this application is built on PDF input and marked PDFs.

## How the pieces work together

```
v1.pdf  v2.pdf  cover note
   |       |        |
   +---+---+        |
       |            |
  rule layer (no model): align clauses, diff word by word, classify, find hidden text
       |
  Compare (PDF in, marked PDFs out) ---- cross-label: both / rules only / Compare only
       |                                 Compare may promote a "spelling fix" or clear a rewording
  list of changes ---------------------> Verify (cover note, claim by claim, against v2 + the list)
       |
  per open change:  Extract (deal size) -> Rerank (playbook) -> Decide (route)
       |
  status, set by code:  blocked > incomplete > changes > ready
```

Why a rule layer at all: nobody should depend on a model noticing that 30 became 90. The rule layer finds every textual difference deterministically and quotes it verbatim. What it cannot do is read meaning, and that is exactly where Compare is used.

## Uncertain results, missing information, failed requests

| Situation | What happens |
|---|---|
| A Clocked request fails, times out or hits the credit cap | The step is listed by name, the status becomes **Review incomplete**, and it can never read "Ready to sign". The SDK retries 429 and 5xx under one Idempotency-Key, so a retried request is billed once. |
| Evidence offsets do not reproduce the quote | `text[start:end] == quote` is checked in Unicode code points. A mismatch is counted and shown as "evidence not aligned"; no approximate box is drawn. |
| A quote cannot be found on the page | The change is listed without a position rather than with a guessed one. |
| Compare is silent on a rule finding | The finding stays open, labelled "rules only". |
| Compare flags a passage the rules already identified (an address in a notices clause) | Shown as flagged, not promoted. |
| Decide is below 0.6 confidence, or Rerank finds no passage, or either step fails | The change is escalated to a person. |
| Verify answers "not stated" | Shown as not stated. It does not block and is not treated as false. |
| A scanned PDF with no text layer | Refused with an explanation: nothing in it can be quoted character by character. |
| A request that was never recorded, in replay mode | A named error, not a silent fallback. |

Thresholds live in `referee/prompts.py`.

<!-- EVAL -->

## Run it

Requirements: Python 3.10+ and [uv](https://docs.astral.sh/uv/).

```sh
uv sync
uv run pytest                      # rule layer vs ground truth, engine logic, recorded responses
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
| `referee/engine.py` | The review: cross-labelling, Verify, Rerank, Decide, status |
| `referee/gateway.py` | The one door to Clocked: record, replay by request hash, credit cap, time budget, audit trail |
| `referee/prompts.py` | Every instruction, question and threshold sent to Clocked |
| `referee/contracts.py` | The synthetic agreement and the catalogue of edits with exact ground truth |
| `data/cases/` | 4 demo pairs and 12 evaluation pairs (PDFs, cover notes, `truth.json`) |
| `data/playbook.json` | The fictional negotiation playbook |
| `recordings/` | Every Clocked exchange behind the demo and the evaluation, with request IDs and credits |
| `web/` | The interface (no framework, no build step) |
| `docs/` | The built static demo |
| `tests/`, `eval/` | Tests and the evaluation |

## Limits

- One synthetic agreement in sixteen variations. The numbers above show that the application does what it claims on data with exact answers; they are not an accuracy figure for contracts in general.
- Text-layer PDFs only. Scans are refused.
- Clauses are found by their numbering. A document without numbered clauses is compared as paragraphs and reads less well.
- The rule layer treats a one-word edit within two letters as spelling until Compare says otherwise. If Compare is unavailable, that edit is listed under spelling, and the review is marked incomplete.
- The playbook is fictional and short. Two topics are missing on purpose, to show what happens when there is no guidance.
- This is decision support for a person at a deal desk. It does not give legal advice and it does not sign anything.

## Data and disclosure

All documents, companies, people and playbook rules are fictional and were written for this project. No real contract or personal data is included. No API key is in the repository or its history.

The application was designed and written with AI coding assistance (Claude Code). `tests/fake_clocked.py` is a stand-in for the API used only by unit tests of our own logic; the demo, the evaluation and `tests/test_recordings.py` use real recorded responses.

MIT License.
