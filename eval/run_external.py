"""Run/replay the locked external-source detection challenge. Failures stay counted."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from eval.round_budget import RoundGateway, verify_lock
from eval.scoring import nz
from referee.engine import review


def transition(c, edit):
    # Require both sides in the SAME review card, including surrounding quotes.
    # No clause-number or model label assumption is used by the scorer.
    old, new = map(nz, edit)
    def hit(side, expected):
        changed, quote = nz(side.get("changed", "")), nz(side.get("quote", ""))
        return expected in quote or bool(changed and changed in expected)
    return bool(old and new) and any(hit(p["v1"], old) and hit(p["v2"], new)
                                    for p in c.get("parts", []))


def score(truth, r):
    changes = r.get("changes", [])
    held = [c for c in changes if c.get("blocking")]
    edits = [{"old": a, "new": b, "held": any(transition(c, (a, b)) for c in held)}
             for a, b in truth["substantive"]]
    faithful = [{"old": a, "new": b, "cleared": not any(transition(c, (a, b)) for c in held)
                 and any(transition(c, (a, b)) for c in changes)} for a, b in truth["faithful"]]
    claims = r.get("note", {}).get("claims", [])
    # Protocol-v1 erratum: a contradicted cover note is a product-level blocker,
    # not merely "changes". Retain the frozen original label and first result.
    expected = "blocked" if truth["false_cover"] else truth["expected_status"]
    return {"id": truth["id"], "status": r.get("status"), "expected_status": expected,
            "frozen_original_status": truth["expected_status"],
            "complete": not r.get("errors"), "errors": r.get("errors", []), "edits": edits, "faithful": faithful,
            "unexpected_blocking": max(0, len(held) - sum(e["held"] for e in edits)),
            "false_cover": truth["false_cover"],
            "false_cover_caught": any(c.get("verdict") == "contradicted" for c in claims),
            "claims_checked": len(claims), "audit_credits": r.get("audit", {}).get("credits")}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--live", action="store_true"); ap.add_argument("--check", action="store_true")
    a = ap.parse_args(); lock = verify_lock(); gw = RoundGateway(a.live)
    rows = []
    for cid in lock["cases"]:
        d = ROOT / "data/external" / cid
        r = review(d / "v1.pdf", d / "v2.pdf", (d / "cover.txt").read_text(), gw,
                   ROOT / "out/external" / cid, route=False, terms=False, concurrency=1,
                   case={"id": cid, "title": cid, "description": "Pre-labelled public-source challenge"})
        row = score(json.loads((d / "truth.json").read_text()), r)
        rows.append(row)
        print(json.dumps(row), flush=True)
    totals = {"completed": sum(r["complete"] for r in rows), "cases": len(rows),
              "held": sum(e["held"] for r in rows for e in r["edits"]), "substantive": sum(len(r["edits"]) for r in rows),
              "faithful_cleared": sum(e["cleared"] for r in rows for e in r["faithful"]), "faithful": sum(len(r["faithful"]) for r in rows),
              "false_cover_caught": sum(r["false_cover_caught"] for r in rows if r["false_cover"]),
              "false_covers": sum(r["false_cover"] for r in rows),
              "status_correct": sum(r["status"] == r["expected_status"] for r in rows),
              "new_actual_credits": gw.credits, "new_uncertain_credits": gw.unsettled}
    result = {"summary": totals, "pairs": rows}
    (ROOT / "eval/external-results.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(totals), flush=True)
    if a.check and (totals["completed"] != 6 or totals["held"] != 6 or totals["faithful_cleared"] != 3 or totals["false_cover_caught"] != 3 or totals["status_correct"] != 6):
        raise SystemExit("Challenge acceptance not met; retain failures and inspect external-results.json.")


if __name__ == "__main__":
    main()
