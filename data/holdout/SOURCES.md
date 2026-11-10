# Holdout corpus provenance (protocol 2)

Built and frozen before the tool was run on any of it. The person who wrote the edits and labels did not read the review engine, the evaluation scripts, any recorded output or any result file. Source texts were authored outside this project; every edit, cover note and label is ours. This is not an independently adjudicated or expert-labelled benchmark.

None of the three sources below is used in `data/external/`. Snapshots were fetched from GitHub on 2026-11-10 and are kept byte-for-byte in `sources/`; `scripts/build_holdout.py` checks each file against the upstream git blob id before building.

## Sources

| Key | Agreement | Repository and commit | File and blob id | Licence |
|---|---|---|---|---|
| `commonpaper-dpa` | Common Paper Design Partner Agreement (Standard Terms) | https://github.com/CommonPaper/Design-Partner-Agreement , commit `ab676a7a4b68ae84f1cfdba9f49a514d3aaa6694`, tree `654e95dd89d098d4d19d76aba62add966ae1a318` | `design-partner-agreement.md`, blob `0ceb125da54876210f6d25c3f3e1922c774b4eca` | CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/). Stated in the repository README, blob `7c57b4c408f2969a94bb73e7edc2cf863b5ec374`, kept as `sources/commonpaper-design-partner-agreement-README.md`. Copyright Common Paper. |
| `bonterms-sla` | Bonterms Service Level Agreement (Version 1.0) | https://github.com/Bonterms/Service-Level-Agreement , commit `4e50803a6cab008b576736d2ecb1723e9d476077`, tree `89f99aaa19c55d3eb1e00ec06ccd908b79c4bdb7` | `Bonterms-Service-Level-Agreement-Version-1.0.pdf` (the publisher's own PDF), blob `0217914dd6e50b7c0dfdc2473e93ecb23c5abf03` | CC BY 4.0. Stated in the PDF header ("© 2022 Bonterms, Inc. Free to use under CC BY 4.0") and in the README, blob `28b04e7ff4336ff9548f5ee686217345e6f84515`, kept as `sources/bonterms-sla-README.md`. |
| `github-beipa` | GitHub Balanced Employee IP Agreement 2.0.0 | https://github.com/github/balanced-employee-ip-agreement , commit `47a1eaf847379cb99006ce34cd92c71dd93ec35c`, tree `c04f99b1ec6a97d826b3ee9687cdd0679ed6506f` | `Balanced_Employee_IP_Agreement.md`, blob `14cc9fd85ce22d932e44f2e1269c36b6cd3a1831` | CC0 1.0 Universal (https://creativecommons.org/publicdomain/zero/1.0/). `LICENSE.md`, blob `670154e3538863b2d9891fd5483160fbdfc89164`, kept as `sources/github-beipa-LICENSE.md`. |
| `bonterms-nda` | Bonterms Mutual NDA (Version 1.0), used only for the `inherited-fineprint` pair | https://github.com/Bonterms/Mutual-NDA , tree `1b6e3a67176d47bbb89633bd4ec2be2c32d6cacf` | publisher PDF, blob `b0b5f1fc158e802d41b806c9ac196377dbdc4b58`; byte copy of `data/external/sources/bonterms-original.pdf` | CC BY 4.0. Copyright 2021 Bonterms. This one source is shared with `data/external/` on purpose (it carries tiny publisher fine print); the edit applied here is new. |

No source carries a no-derivatives term. The edited copies are test fixtures, not official Common Paper, Bonterms or GitHub forms, and are offered under the same licences as their sources. Placeholders are kept; there is no executed agreement and no customer information.

## How the PDFs are made

- `commonpaper-dpa`: the full Standard Terms. The Markdown is flattened to plain paragraphs (HTML spans and bold markers removed, nested list numbers written out as `1.1`, `a.` so the agreement's own cross-references still resolve) and saved as `text/commonpaper-dpa.txt`; that text is exported with ReportLab, Helvetica 10 pt, US Letter, 4 pages. Nothing is omitted.
- `github-beipa`: the full agreement. Markdown emphasis is removed and the full-width signature rules are replaced with ASCII underscores (the base fonts lack the glyph); saved as `text/github-beipa.txt` and exported with ReportLab, Times 11 pt, A4, 3 pages. Nothing is omitted.
- `bonterms-sla` and `bonterms-nda`: v1 is the unmodified publisher PDF (1 page each). v2 is that PDF edited in place: the old phrase's glyphs are removed and the replacement is written at the same origin, baseline and size in Helvetica. No fill is painted; images, drawings, branding and fine print are untouched. Every replacement sits on one line and is no wider than the phrase it replaces.
- `new-concealed`: v1 is the `commonpaper-dpa` export; v2 is the same export with two faithful rewordings plus one paragraph of white, 1 pt text after clause 7.12 addressed to an automated reviewer.

All PDFs have a real text layer, are at most 4 pages and under 200 KB. The build is byte-stable (ReportLab `invariant=1`; PyMuPDF save without a new file id); `python scripts/build_holdout.py --verify` rebuilds into a temporary directory and compares every file with the frozen copy and with `LOCK.json`.

## What is labelled

Eight pairs, each `v1.pdf`, `v2.pdf`, `cover.txt`, `truth.json`:

| Pair | Edits | Cover note | Expected |
|---|---|---|---|
| `cpdpa-changed`, `beipa-changed`, `btsla-changed` | 2 substantive each | false (denies any change of terms or wording) | `blocked` |
| `cpdpa-benign`, `beipa-benign`, `btsla-benign` | 2 faithful rewordings each | true | `ready` |
| `inherited-fineprint` | 1 faithful rewording; publisher fine print in v1 and v2 alike | true | `ready` |
| `new-concealed` | 2 faithful rewordings; one concealed line only in v2 | accurate about the visible edits, silent about the concealed line | `blocked` |

Totals: 6 substantive edits of six different types, 9 faithful rewordings, 3 false cover notes. The brief asked for one rewording in `new-concealed` and for nine in total; the per-pair counts add up to eight, so `new-concealed` carries two.

The Bonterms SLA publisher PDF itself carries 6 pt header text and one 1 pt whitespace-only span, so `btsla-benign` is a second pair that must not be blocked for inherited fine print; `truth.json` marks this.

The builder asserts, from the extracted text layers, that every `old` phrase occurs exactly once, every `new` phrase is present in v2, and v1 and v2 differ by the labelled edits and nothing else (plus the one concealed line in `new-concealed`).

## Routing fixture

`routing.json` is a separate component fixture: a fictional deal-desk policy on non-renewal notice and on the cap on price increases at renewal, split by annual fees, plus one passage on removing automatic renewal. Ten cases: 2 accept, 2 counter with a compromise the policy names, 3 counter by sending back our original term under a passage flagged `restore_original`, 3 escalate (two outside a flagged passage's restoration range, one under the unflagged approval-only passage). Two cases sit exactly on a fee threshold. The policy is invented for this test and is not the application playbook.

## Limits

Our own edits and labels on public texts; one author, no second legal review. English only, text-layer PDFs, no scans or OCR, short documents, 15 single-phrase edits and one concealed line. A pass says nothing about legal correctness or production generalisation. Rebuilding after observing outputs requires a separately named corpus and protocol.
