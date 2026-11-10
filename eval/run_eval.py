"""Score the product on the seeded evaluation pairs against their exact ground truth.

  python eval/run_eval.py            replay recordings/eval-*/ (no API calls)
  python eval/run_eval.py --live     call Clocked for anything not yet recorded, and record it

Rerank, Decide and Extract are skipped here: the evaluation measures what was found
and what was believed, not how it was routed. Writes eval/results.json and eval/RESULTS.md.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from referee.engine import review  # noqa: E402
from referee.gateway import Gateway  # noqa: E402

CASES = ROOT / "data" / "cases"


def score(case_id: str, live: bool, cap: int | None) -> dict:
    d = CASES / case_id
    truth = json.loads((d / "truth.json").read_text())
    gw = Gateway("auto" if live else "replay", ROOT / "recordings" / case_id, record=live, credit_cap=cap)
    r = review(d / "v1.pdf", d / "v2.pdf", (d / "cover.txt").read_text(), gw, ROOT / "out" / "eval" / case_id,
               case={"id": case_id, "title": truth["title"], "description": ""}, route=False, terms=False)
    blocking = [c for c in r["changes"] if c["blocking"]]
    row = {"case": case_id, "status": r["status"], "expected_status": truth["expected_status"],
           "errors": len(r["errors"]), "credits": r["audit"]["credits"], "edits": [], "claims": []}
    planted_clauses = set()
    for e in truth["substantive"]:
        planted_clauses.add(e["clause"])
        hits = [c for c in blocking if c["clause"] == e["clause"]]
        by_compare = any(c["found_by"] in ("both", "compare") for c in hits)
        row["edits"].append({"id": e["id"], "found": bool(hits), "compare": by_compare,
                             "rules": any(c["found_by"] in ("both", "rules") for c in hits)})
    row["false_alarms"] = [f"{c['clause']} {c['summary']}" for c in blocking if c["clause"] not in planted_clauses]
    rewords = [e for e in truth["cosmetic"] if e["id"].startswith("reword")]
    row["rewordings"] = len(rewords)
    row["rewordings_cleared"] = sum(1 for e in rewords if any(c["cls"] == "reworded" and c["clause"] == e["clause"] for c in r["changes"]))
    expected = {c["text"]: c["expected"] for c in truth["letter_claims"]}
    for c in r["note"]["claims"]:
        if c["text"] in expected:
            row["claims"].append({"text": c["text"], "expected": expected[c["text"]], "got": c["verdict"]})
    row["injection_planted"] = truth["injection"]
    row["injection_flagged"] = any(g.get("flagged") for g in r["guard"])
    row["hidden_text_found"] = bool(r["hidden_text"])
    return row


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--credit-cap", type=int, default=600)
    a = ap.parse_args()
    ids = a.only or [c["id"] for c in json.loads((CASES / "index.json").read_text()) if c["group"] == "eval"]
    rows = []
    for cid in ids:
        row = score(cid, a.live, a.credit_cap)
        rows.append(row)
        print(cid, row["status"], "/", row["expected_status"], "edits", sum(e["found"] for e in row["edits"]), "of", len(row["edits"]),
              "false alarms", len(row["false_alarms"]), "errors", row["errors"], "credits", row["credits"])
    complete = [r for r in rows if r["errors"] == 0]
    edits = [e for r in complete for e in r["edits"]]
    meaning = [e for e in edits if e["id"] == "min_max"]
    claims = [c for r in complete for c in r["claims"]]
    false = [c for c in claims if c["expected"] == "contradicted"]
    true_ = [c for c in claims if c["expected"] == "supported"]
    inj = [r for r in complete if r["injection_planted"]]
    clean = [r for r in complete if not r["injection_planted"]]
    s = {
        "pairs": len(rows), "pairs_complete": len(complete),
        "edits_planted": len(edits), "edits_found": sum(e["found"] for e in edits),
        "edits_found_by_compare": sum(e["compare"] for e in edits),
        "edits_found_by_rules": sum(e["rules"] for e in edits),
        "meaning_edits_planted": len(meaning), "meaning_edits_caught_by_compare": sum(e["found"] for e in meaning),
        "false_alarms": sum(len(r["false_alarms"]) for r in complete),
        "rewordings_planted": sum(r["rewordings"] for r in complete),
        "rewordings_cleared": sum(r["rewordings_cleared"] for r in complete),
        "false_statements": len(false), "false_statements_caught": sum(c["got"] == "contradicted" for c in false),
        "true_statements": len(true_), "true_statements_supported": sum(c["got"] == "supported" for c in true_),
        "true_statements_wrongly_contradicted": sum(c["got"] == "contradicted" for c in true_),
        "injections_planted": len(inj), "injections_flagged_by_guard": sum(r["injection_flagged"] for r in inj),
        "injections_found_as_hidden_text": sum(r["hidden_text_found"] for r in inj),
        "guard_false_alarms": sum(r["injection_flagged"] for r in clean),
        "status_correct": sum(r["status"] == r["expected_status"] for r in complete),
        "credits": sum(r["credits"] for r in rows),
    }
    (ROOT / "eval" / "results.json").write_text(json.dumps({"summary": s, "pairs": rows}, indent=1) + "\n")
    md = ["# Evaluation results", "",
          f"{s['pairs_complete']} of {s['pairs']} seeded pairs completed without a failed step. "
          "Every pair is synthetic and its ground truth is exact (see `referee/contracts.py`). "
          "All Clocked answers behind these numbers are recorded under `recordings/eval-*/`.", "",
          "| Measure | Result |", "|---|---|",
          f"| Planted substantive edits found | {s['edits_found']} of {s['edits_planted']} |",
          f"| ... confirmed by Compare | {s['edits_found_by_compare']} of {s['edits_planted']} |",
          f"| ... found by the rule layer | {s['edits_found_by_rules']} of {s['edits_planted']} |",
          f"| One-word meaning changes the rule layer reads as spelling, caught by Compare | {s['meaning_edits_caught_by_compare']} of {s['meaning_edits_planted']} |",
          f"| Faithful rewordings cleared by Compare | {s['rewordings_cleared']} of {s['rewordings_planted']} |",
          f"| Clauses wrongly held as substantive | {s['false_alarms']} |",
          f"| False cover-note statements judged contradicted by Verify | {s['false_statements_caught']} of {s['false_statements']} |",
          f"| True cover-note statements judged supported by Verify | {s['true_statements_supported']} of {s['true_statements']} |",
          f"| True statements wrongly judged contradicted | {s['true_statements_wrongly_contradicted']} |",
          f"| Hidden instructions flagged by Guard | {s['injections_flagged_by_guard']} of {s['injections_planted']} |",
          f"| Hidden instructions found as hidden text by rules | {s['injections_found_as_hidden_text']} of {s['injections_planted']} |",
          f"| Guard false alarms on pairs without an injection | {s['guard_false_alarms']} of {len(clean)} |",
          f"| Final status equal to ground truth | {s['status_correct']} of {s['pairs_complete']} |",
          f"| Credits used | {s['credits']} |", "",
          "Twelve pairs of one agreement are a demonstration with exact answers, not an accuracy benchmark for contracts in general.", ""]
    (ROOT / "eval" / "RESULTS.md").write_text("\n".join(md))
    print(json.dumps(s, indent=1))


if __name__ == "__main__":
    main()
