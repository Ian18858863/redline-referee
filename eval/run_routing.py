"""Controlled retrieval experiment: same change, same fee, same Decide; change retrieval only.

This is a component experiment with known fee context, not an end-to-end PDF
accuracy measurement. An explicit fictional policy gives different routes by
deal size; all-passages is a strong baseline and first-topic is a weak baseline.
No improvement is assumed or required by the frozen protocol.
"""
import argparse
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from eval.round_budget import RoundGateway, verify_lock
from referee.engine import _rerank, _decide, _final_route, _passages
from referee.gateway import StepError


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--live", action="store_true"); ap.add_argument("--check", action="store_true")
    a = ap.parse_args(); verify_lock(); gw = RoundGateway(a.live)
    fixtures = json.loads((ROOT / "data/external/routing.json").read_text())
    book, rows = fixtures["playbook"], []
    errors = []
    def run(endpoint, request, label, role=None, check=None):
        try:
            response = gw.call(endpoint, request, label)
            return check(response) if check else response
        except Exception as e:
            errors.append(e.as_dict() if isinstance(e, StepError) else {"step": label, "kind": type(e).__name__})
            return None
    for case in fixtures["cases"]:
        base = {"id": case["id"], "clause": "2", "title": "Payment", "summary": f"30 → {case['days']} days",
                "parts": [{"kind": "duration", "v1": {"quote": "Customer shall pay invoices within 30 days."},
                           "v2": {"quote": f"Customer shall pay invoices within {case['days']} days."}}]}
        for arm in ("rerank", "all_passages", "first_topic_passage"):
            c = copy.deepcopy(base); start = len(errors)
            if arm == "rerank":
                c["playbook"] = _rerank(c, book, case["fee"], run)
            else:
                ps = _passages(book)
                c["playbook"] = {"status": "guidance", "passages": ps if arm == "all_passages" else ps[:1]}
            c["decide"] = _decide(c, run, {"annual_fee": case["fee"]})
            _final_route(c)
            rows.append({"id": case["id"], "fee": case["fee"], "days": case["days"], "arm": arm,
                         "expected": case["expected"], "route": c["route"], "correct": c["route"] == case["expected"],
                         "complete": len(errors) == start, "passages": [p["id"] for p in c["playbook"]["passages"]],
                         "decide": c["decide"], "errors": errors[start:]})
            print(json.dumps(rows[-1]), flush=True)
    result = {"arms": {arm: {"correct": sum(r["correct"] and r["complete"] for r in rows if r["arm"] == arm),
                              "complete": sum(r["complete"] for r in rows if r["arm"] == arm), "cases": 6}
                       for arm in ("rerank", "all_passages", "first_topic_passage")}, "rows": rows}
    (ROOT / "eval/routing-results.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result["arms"]))
    if a.check and (result["arms"]["rerank"]["correct"] != 6 or any(r["complete"] != 6 for r in result["arms"].values())):
        raise SystemExit("Routing acceptance not met; see named failures.")


if __name__ == "__main__":
    main()
