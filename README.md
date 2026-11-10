# Redline Referee

**Catch what changed in the contract they sent back.**

[![tests](https://github.com/Ian18858863/redline-referee/actions/workflows/tests.yml/badge.svg)](https://github.com/Ian18858863/redline-referee/actions/workflows/tests.yml)

You send a customer your agreement. They return a "clean copy" with a note: *we only fixed typos and updated our address*. Somebody now has to read two PDFs side by side to find out whether that is true.

Redline Referee does that reading. Give it the version you sent, the copy that came back and the cover note. It returns:

- every difference, located on **both original PDFs**; each one that could matter is read by deterministic rules and by two Clocked readers, Verify and Compare;
- each statement of the cover note, checked by Verify: supported, contradicted or not stated;
- for each real change, the passages of your negotiation playbook that apply to a deal of this size (Rerank) and a route: accept, counter or escalate (Decide);
- a reply to the customer, drafted by Fill and kept only where Verify supports it;
- one status that code sets: **Ready to sign**, **Changes to resolve**, **Do not sign** or **Review incomplete**.

Entry for the Clocked Build Challenge 2026, built on November 9 and 10, 2026.

| | |
|---|---|
| Live demo, no key needed (replays recorded answers of the live Clocked API) | https://ian18858863.github.io/redline-referee/ |
| Narrated video, 3 min 20 s | https://ian18858863.github.io/redline-referee/demo.mp4 |
| Evaluation | [`eval/RESULTS.md`](eval/RESULTS.md) |

### For judges: three minutes

1. Open the demo. The first case is a returned copy whose note says nothing changed. The status is **Do not sign**, and the "Cover note" tab shows which three statements Verify contradicted.
2. Use the "Start here" strip: **A changed number** opens the payment edit; **A faithful rewording** opens wording that should be cleared. **Two readers, one disagreement** is the case where Verify read two real changes as "same meaning" and Compare read them as conflicts. **A note that talks to your AI** is stopped by Guard alone. **The wrong file came back** stops after one Compare call that cost one credit.
3. Open "How it works" at the bottom of any case: what each endpoint decided in that review, what would have gone wrong without it, and every request with its credits.

To run it on your own two PDFs, see [Run it](#run-it). For the evidence trail, reproduction checks and remaining work against the published criteria, see [the reviewer guide](JUDGING.md).

## What each endpoint changes

"Meaningful use means the endpoint's output changes what the application does or shows." Each row below has tests in `tests/test_engine.py`, and the numbers in the last column come from the recordings in this repository (`eval/RESULTS.md`).

| Endpoint | What it reads | What its output changes | Measured |
|---|---|---|---|
| **Verify** `/clocked/grounding`, across versions | Every sentence that differs, as an explicit claim, against **the other version's PDF**, both directions | "Contradicted" confirms a change. "Supported" both ways clears a faithful rewording, if Compare does not object | Without it: 20 of 20 rewordings held as changes |
| **Verify**, cover note | Each statement of the note, against the returned copy and the list of changes | A contradicted statement turns the status to **Do not sign** and is linked to the change behind it. "Not stated" never blocks | 30 of 30 false statements contradicted; 0 true ones contradicted |
| **Verify**, reply | Each sentence Fill drafted, against the decisions taken | A sentence that is not supported is removed from what the customer sees | |
| **Compare** `/clocked/compare`, clause against clause | Each differing clause as sent and as returned, two text documents | A conflict confirms a change and vetoes clearing it. Its passages are checked character by character and drawn on both pages | Without it: 4 real changes cleared, because Verify read them as the same meaning |
| **Compare**, whole documents | The two PDFs, asked for the relation only | A gate: if they are not versions of one agreement the review stops there | 1 to 3 credits per review; fired on the wrong-file case at "related 1%" |
| **Rerank** `/clocked/rerank` | The playbook passages for each open change, with the deal size as `context` | Only passages returned `use` at 0.9 or above are shown and passed on, at most three. A passage for another deal size comes back `skip / different_case`. No passage: the change goes to Legal | Deciding passage kept 24 of 24 times; none kept 6 of 6 times when the playbook has none |
| **Decide** `/clocked/decide` | One change, the passages Rerank kept, the deal facts Extract read. Two yes/no questions | Code turns two probabilities into accept, counter or escalate. Between 0.4 and 0.6 is not an answer: a person decides | 27 of 30 routes equal to the playbook; the 3 misses are on the strict side |
| **Extract** `/clocked/extract` | Thirteen key terms from each PDF, `return_pdf: "box"` | The side-by-side terms table; the annual fee and term given to Rerank and Decide | |
| **Fill** `/clocked/fill` | A brief of the decisions: generated sentences, a verbatim position per countered change, three typed answers | The reply to the customer and its closing line | Drafted for 6 demo cases; every sentence then went through Verify |
| **Guard** `/clocked/injection` | The cover note and the returned PDF, as untrusted content | Text addressed to an AI reviewer blocks signing | 2 of 2 hidden instructions, 0 of 13 false alarms; the only blocker in one demo case |

Pipeline is not used: it takes text only, and this application reads PDFs.

## How the pieces work together

```
v1.pdf  v2.pdf  cover note
   |       |
   +---+---+--> Compare, whole documents: one agreement?  no -> stop (1 credit)
       |
  rule layer, no model: clauses, alignment, word-level diff, classes, hidden text
       |
       +--> Compare, clause against clause  --+   a difference that could matter gets both readers;
       +--> Verify, sentence against the      +-> code decides: held, or cleared as a faithful rewording
            other version's PDF, both ways  --+   (only if Verify supports both ways and Compare does not object)
       |
  list of changes ----> Verify (cover note, statement by statement)      Guard (note, returned PDF)
       |
  per open change:  Extract (deal facts) -> Rerank (playbook) -> Decide (two yes/no) -> route
       |
  reviewer decides ---> Fill (reply) -> Verify (each sentence) -> what the customer gets
       |
  status, set by code:  blocked > incomplete > changes > ready
```

**Why a rule layer.** Nobody should depend on a model noticing that 30 became 90. Deterministic code enumerates differences in the extracted, aligned text and quotes them verbatim. PDF extraction and alignment can still miss content; deterministic comparison alone is not a guarantee of recall on arbitrary PDFs. A number, a date, a symbol or a currency that differs is never cleared by a model. Spelling, formatting and a notice address are settled by rules and are not sent to the API.

**Why two readers.** What the rules cannot do is read meaning: "will defend" to "may defend" is one word; a rewritten sentence may say the same thing. On 39 such edits Verify alone would have cleared two real changes and Compare alone left one unflagged. Together: 24 of 24 changes of meaning held, 14 of 15 faithful rewordings cleared, and the one miss is a false alarm.

## Measured

These are development examples, not a held-out contract benchmark. Every attempted review, expected statement and expected route stays in the evaluation denominator, including missing or failed results. `uv run python eval/run_eval.py --check` fails when evaluation coverage is incomplete; known model mistakes remain reported.

All numbers are computed from recorded answers of the live API against exact ground truth; `uv run python eval/run_eval.py` recomputes them without a key. Full tables: [`eval/RESULTS.md`](eval/RESULTS.md).

| On 15 seeded pairs (12 with planted edits, 3 with whole clauses added or removed) | |
|---|---|
| Planted substantive edits held | 30 of 30 |
| Cosmetic edits wrongly held | 0 of 41 |
| False cover-note statements contradicted / true ones supported | 30 of 30 / 37 of 39 (2 "not stated") |
| Final status equal to ground truth | 15 of 15 |
| Route equal to the playbook | 27 of 30; 3 too strict, 0 too loose |
| Credits per full review | about 76 |

| Decided by (24 recorded pairs: 69 real changes, 20 faithful rewordings, 39 false statements) | Real changes missed | Rewordings held | False statements not caught | Status wrong |
|---|---|---|---|---|
| Rules, Verify and Compare | 0 | 1 | 0 | 0 of 24 |
| Without Verify | 0 | 20 | 39 | 13 of 24 |
| Without Compare | 4 | 1 | 1 | 1 of 24 |
| Rules alone | 0 | 20 | 39 | 13 of 24 |

The demo's eight cases cost 647 credits in total, replies included: from 1 credit (the wrong file) to 125 (four changes, a false note and a drafted reply).

Twenty-five pairs built from one synthetic agreement, plus one unrelated agreement in another layout, show that the application does what it claims on data with exact answers. They are not an accuracy figure for contracts in general.

## Uncertain results, missing information, failed requests

| Situation | What happens |
|---|---|
| A request fails, times out, or the credit cap is reached | The step is named, the status becomes **Review incomplete**, never "Ready to sign". Retries reuse one Idempotency-Key. Credits are reserved before a call, so parallel calls cannot overshoot the cap |
| A response is empty, malformed or missing what the step needs | A failed step with a named error. 69 malformed-response variants are tested (`tests/test_engine_fuzz.py`); none raises, none reads as a pass |
| Evidence offsets do not reproduce the quote | `text[start:end] == quote` is checked in code points. A mismatch is reported as "not aligned"; no approximate box is drawn |
| The two readers disagree, or one did not answer | The stricter reading wins and the card says so. Silence is not consent |
| More than 30 sentences differ | Verify is asked in batches. Nothing is cut |
| Decide is between 0.4 and 0.6, Rerank keeps no passage, or either step fails | The change goes to a person |
| Verify says "not stated" about a cover-note statement | Shown as not stated; it does not block and is not treated as false |
| A drafted reply sentence is not supported | Removed from the reply and listed for the reviewer. If Fill or Verify fails there is no reply rather than an unchecked one |
| A scanned PDF, or scanned pages inside a text PDF | Refused, or the review is incomplete and names the pages |
| Replay meets a request that was never recorded | A named error, not a silent fallback |

Thresholds are in `referee/prompts.py`.

## What an adversarial review found

Before submitting the final version we had the project attacked by three independent reviews: a sceptical judge, a code breaker and a first-time user. The reports changed the application.

- **"Upload any two PDFs" was not true.** A line starting with a number was taken for a heading and hid a change; a Chinese contract came back "formatting only"; white text on a dark table header was reported as hidden. The deterministic layer was rewritten and now has 112 layout tests (numbering styles and none, columns, tables, headers and footers, four languages, inserted, removed and moved clauses).
- **Two real words are not a spelling fix.** "Maximum" to "minimum" used to be classed as spelling until a model objected. Now a one-word edit is a spelling fix only if the old word is not a word.
- **The most expensive call decided nothing.** The whole-document Compare cost about 45 credits and never changed an outcome. It is now asked for the relation only (1 to 3 credits) and stops the review when the wrong file comes back.
- **"Read three times" was an overclaim.** Formatting is settled by rules alone, and in the first recordings the two readers never disagreed. We built the hard set to find out whether both are needed. They are; the numbers are above.
- **A judge on a phone saw a third of the page.** There is now a phone layout, a "Start here" strip and readable crops of both pages next to every change.

## Run it

Requirements: Python 3.10+ and [uv](https://docs.astral.sh/uv/).

```sh
uv sync
uv run pytest                          # application, failure-path and recorded-response tests; no API calls
uv run python scripts/build_site.py    # rebuild docs/ from the recordings
uv run uvicorn referee.server:app --port 8787
```

Open http://localhost:8787. Without a key the demo cases replay recorded answers. With a key, "Review your own PDFs" reviews any two text-layer PDFs live:

```sh
cp .env.example .env                   # put CLOCKED_API_KEY in it; .env is git-ignored
uv run uvicorn referee.server:app --port 8787
```

Or in a container: `docker build -t redline-referee . && docker run -p 8787:8787 -e CLOCKED_API_KEY=... redline-referee`. `render.yaml` deploys the same container. A public instance is limited per address and per day (`LIVE_RUNS_PER_HOUR`, `LIVE_RUNS_PER_DAY`), and each review has a hard credit cap (`CLOCKED_CREDIT_CAP`).

Re-record or extend:

```sh
uv run python scripts/build_cases.py             # regenerate the synthetic PDFs and ground truth
uv run python scripts/run_case.py --demo --live  # call Clocked, record every exchange, draft the replies
uv run python eval/run_eval.py --live            # evaluation pairs
uv run python eval/run_hard.py --live            # the hard set
```

## What is in the repository

| Path | Contents |
|---|---|
| `referee/textdoc.py`, `referee/rules.py` | The deterministic layer: PDF text, clauses, alignment, diff, classes, hidden text, locating quotes |
| `referee/note.py` | Cover-note statements, instructions, courtesies and signature parsing |
| `referee/engine.py` | The review: gate, the readings and how code decides between them, cover note, routing, status |
| `referee/reply.py` | The reply to the customer: Fill drafts, Verify decides what stays |
| `referee/gateway.py` | The one door to Clocked: record, replay by request hash, credit reservation, time budget, audit |
| `referee/prompts.py` | Every instruction, question and threshold sent to Clocked |
| `referee/server.py` | Uploads, limits, the reply endpoint |
| `referee/contracts.py`, `data/` | The synthetic agreement, the catalogue of edits with exact ground truth, the playbook |
| `recordings/` | Recorded exchanges with the live API behind the demo and the evaluation, with credits |
| `web/`, `docs/` | The interface (no framework, no build step) and the built static demo |
| `tests/`, `eval/` | Application, failure-path and recorded-response tests, plus the evaluation |

## Limits

- The documents are synthetic: one Master Services Agreement in 25 variations and one non-disclosure agreement. Other layouts are covered by generated test PDFs, not by exports from Word or a contract system.
- Text-layer PDFs only, single documents up to the API's limits. Multi-column pages and tables are read by heuristics.
- The thirteen key terms Extract reads are those of a subscription agreement. On the non-disclosure agreement most of them are correctly empty.
- Routing errs on the strict side: the playbook lets the desk extend the data export window to ninety days and Decide still answered that it may not be accepted as written, in all three pairs that contain that edit. We left the answers as recorded.
- One faithful rewording in the hard set ("exclusive of taxes" to "do not include taxes") was held by both readers. Also on the strict side.
- Rerank's `expand` (the answer is in a neighbouring passage) is handled and unit-tested, but did not occur in the recordings.
- The cover note is checked against the returned copy and against our own list of changes. That list comes from the rule layer and the two readers, not from the note; a statement about something outside the documents can only come back "not stated".
- The playbook is fictional and short. Two topics are missing on purpose, to show what happens without guidance.
- This is decision support for a person at a deal desk. It does not give legal advice and it does not sign anything.

## Data and disclosure

All documents, companies, people and playbook rules are fictional and were written for this project. No real contract or personal data is included. No API key is in the repository or its history.

The application was designed and written with AI coding assistance (Claude Code), including the adversarial reviews described above. The video is narrated by a synthetic voice. `tests/fake_clocked.py` is a stand-in for the API used only by unit tests of our own logic; the demo, the evaluation and `tests/test_recordings.py` use real recorded responses.

Awards entered: Best Overall App, the three group awards, and People's Choice.

MIT License.
