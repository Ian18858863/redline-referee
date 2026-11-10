"""Measure the two Clocked readers on the hard set: one hard edit per sentence, exact labels.

  python eval/run_hard.py          replay recordings/hard-*/
  python eval/run_hard.py --live   call Clocked for what is not recorded yet, and record

For every planted edit: what the rule layer said, what Verify read (the sentence against the
other version's PDF, both ways), what Compare read (clause against clause), and what the
application did (held as a change, or cleared). Writes eval/hard.json and eval/hard-summary.json.
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
from eval.scoring import matches  # noqa: E402

CASES = ROOT / "data" / "cases"


def covering(change: dict, edit: dict) -> bool:
    return matches(change, edit)


def run(case_id: str, live: bool) -> list[dict]:
    d = CASES / case_id
    truth = json.loads((d / "truth.json").read_text())
    gw = Gateway("auto" if live else "replay", ROOT / "recordings" / case_id, record=live, credit_cap=900, time_budget_s=300)
    r = review(d / "v1.pdf", d / "v2.pdf", "", gw, ROOT / "out" / "hard" / case_id,
               case={"id": case_id, "title": truth["title"], "description": ""}, route=False, terms=False)
    print(case_id, r["status"], "changes", len(r["changes"]), "errors", len(r["errors"]), "credits", r["audit"]["credits"], "calls", r["audit"]["calls"])
    for e in r["errors"][:3]:
        print("  !", e)
    rows = []
    for label, edits in (("changed", truth["substantive"]), ("same", truth["cosmetic"])):
        for e in edits:
            hits = [c for c in r["changes"] if covering(c, e)]
            held = any(c["blocking"] for c in hits)
            best = sorted(hits, key=lambda c: not c["blocking"])[0] if hits else None
            v, cp = (best or {}).get("verify") or {}, (best or {}).get("compare") or {}
            rows.append({"case": case_id, "id": e["id"], "clause": e["clause"], "what": e["label"], "truth": label,
                         "found": bool(hits), "rules": (best or {}).get("why", "")[:0] or ("substantive" if hits else "missed"),
                         "cls": (best or {}).get("cls"), "label": (best or {}).get("found_by_label"),
                         "verify": v.get("relation"), "verify_p": [(v.get("returned_against_sent") or {}).get("supported"), (v.get("sent_against_returned") or {}).get("supported")],
                         "compare": cp.get("relation"), "compare_p": cp.get("confidence"), "held": held,
                         "v1": e["v1_quote"], "v2": e["v2_quote"]})
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    a = ap.parse_args()
    rows = run("hard-a", a.live) + run("hard-b", a.live)
    (ROOT / "eval" / "hard.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False) + "\n")
    ch, sm = [r for r in rows if r["truth"] == "changed"], [r for r in rows if r["truth"] == "same"]
    print(f"\n{'id':11} {'truth':8} {'verify':9} {'compare':9} {'app':8}  what")
    for r in rows:
        ok = (r["held"] and r["truth"] == "changed") or (not r["held"] and r["truth"] == "same")
        print(f"{r['id']:11} {r['truth']:8} {str(r['verify']):9} {str(r['compare']):9} {'held' if r['held'] else 'cleared':8} {'' if ok else '<-- WRONG'}  {r['what']}")
    def reader_says_changed(r, who):
        return {"verify": r["verify"] == "changed", "compare": r["compare"] == "conflict"}[who]
    def reader_says_same(r, who):
        return {"verify": r["verify"] == "same", "compare": r["compare"] == "same"}[who]
    s = {"changed_total": len(ch), "same_total": len(sm),
         "app_held_changed": sum(r["held"] for r in ch), "app_cleared_same": sum(not r["held"] for r in sm),
         "app_wrongly_cleared": [r["id"] for r in ch if not r["held"]], "app_held_same": [r["id"] for r in sm if r["held"]],
         "verify_changed_on_changed": sum(reader_says_changed(r, "verify") for r in ch), "verify_same_on_same": sum(reader_says_same(r, "verify") for r in sm),
         "verify_same_on_changed": [r["id"] for r in ch if reader_says_same(r, "verify")],
         "compare_conflict_on_changed": sum(reader_says_changed(r, "compare") for r in ch), "compare_same_on_same": sum(reader_says_same(r, "compare") for r in sm),
         "compare_same_on_changed": [r["id"] for r in ch if reader_says_same(r, "compare")],
         "compare_conflict_on_same": [r["id"] for r in sm if reader_says_changed(r, "compare")],
         "verify_changed_on_same": [r["id"] for r in sm if reader_says_changed(r, "verify")],
         "disagree": [r["id"] for r in rows if (r["verify"] == "changed") != (r["compare"] == "conflict")]}
    print(json.dumps(s, indent=1))
    (ROOT / "eval" / "hard-summary.json").write_text(json.dumps(s, indent=1) + "\n")


if __name__ == "__main__":
    main()
