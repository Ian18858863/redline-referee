"""Refresh missing requests on the development corpus under the shared round budget.

Never prunes history. Successful exact-request recordings replay without charge.
Only run after deliberately changing the application request shape.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from eval.round_budget import RoundGateway
from referee.engine import review
from referee.reply import draft_reply


def main():
    rows = []
    index = json.loads((ROOT / "data/cases/index.json").read_text())
    index.append({"id": "other-agreement", "group": "eval", "title": "Other agreement", "description": "Existing alternative-layout regression"})
    for meta in index:
        if meta["group"] not in ("demo", "eval", "struct"):
            continue
        cid = meta["id"]; d = ROOT / "data/cases" / cid
        if cid == "other-agreement":
            d = ROOT / "tests/fixtures/other-agreement"
        gw = RoundGateway(True, ROOT / "recordings" / cid)
        r = review(d / "v1.pdf", d / "v2.pdf", (d / "cover.txt").read_text(), gw,
                   ROOT / "out/round-refresh" / cid, case={k: meta[k] for k in ("id", "title", "description")}, concurrency=1)
        reply = None
        if meta["group"] == "demo" and r["status"] != "incomplete" and r["headline"] != "Not the same agreement":
            decisions = {c["id"]: c["route"] for c in r["changes"] if c["blocking"] and c.get("route")}
            reply = draft_reply(r, decisions, gw)
        rows.append({"case": cid, "errors": r["errors"], "reply_errors": (reply or {}).get("errors", []),
                     "new_actual": gw.credits, "new_uncertain": gw.unsettled})
        print(json.dumps(rows[-1]), flush=True)
    (ROOT / "eval/refresh-results.json").write_text(json.dumps(rows, indent=2) + "\n")
    if any(r["errors"] or r["reply_errors"] for r in rows):
        raise SystemExit("Some development reviews remain incomplete.")


if __name__ == "__main__":
    main()
