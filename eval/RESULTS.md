# Evaluation results

12 of 12 seeded pairs completed without a failed step. Every pair is synthetic and its ground truth is exact (see `referee/contracts.py`). All Clocked answers behind these numbers are recorded under `recordings/eval-*/`.

| Measure | Result |
|---|---|
| Planted substantive edits found | 23 of 23 |
| ... classed as a change by the rule layer alone | 21 of 23 |
| ... read as a conflict by Compare (clause against clause) | 23 of 23 |
| ... read as contradicted by Verify (sentence against the other PDF) | 23 of 23 |
| One-word meaning changes the rule layer takes for spelling fixes, held as changes | 2 of 2 (Compare 2, Verify 2) |
| Faithful rewordings cleared (Verify both ways, no objection from Compare) | 4 of 4 |
| Clauses wrongly held as substantive | 0 |
| False cover-note statements judged contradicted by Verify | 24 of 24 |
| True cover-note statements judged supported by Verify | 30 of 32 |
| True statements judged not stated (shown as such, never blocking) | 2 |
| True statements wrongly judged contradicted | 0 |
| Hidden instructions flagged by Guard | 2 of 2 |
| Hidden instructions found as hidden text by rules | 2 of 2 |
| Guard false alarms on pairs without an injection | 0 of 10 |
| Final status equal to ground truth | 12 of 12 |
| Credits used | 945 |

Twelve pairs of one agreement are a demonstration with exact answers, not an accuracy benchmark for contracts in general.
