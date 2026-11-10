"""Run/replay the second locked corpus (data/holdout): fresh sources, fresh edits, labelled before any run.

  python eval/run_holdout.py            replay recordings/holdout (no key, no network)
  python eval/run_holdout.py --live     send what is not recorded yet, on the shared round ledger
  python eval/run_holdout.py --check    exit non-zero unless every frozen acceptance target is met

The corpus was built and frozen by a process that had no access to the engine, its results or the earlier
failures (see data/holdout/SOURCES.md and LOCK.json). The first complete output is kept in
eval/holdout-results-first.json with the digests of the code that produced it and is never overwritten.
Failures stay counted. Routing here is a component test with supplied deal facts, three retrieval arms.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from eval.round_budget import RoundGateway  # noqa: E402
from eval.run_external import transition  # noqa: E402
from referee.engine import _decide, _final_route, _rerank, restore_marks, review  # noqa: E402
from referee.gateway import StepError  # noqa: E402

BASE = ROOT / "data/holdout"
CODE = ["referee/engine.py", "referee/rules.py", "referee/textdoc.py", "referee/prompts.py", "referee/note.py", "eval/run_holdout.py"]
STRICT = {"accept": 0, "counter": 1, "escalate": 2}


def sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_lock() -> dict:
    lock = json.loads((BASE / "LOCK.json").read_text())
    for path, expected in lock["files"].items():
        assert sha(ROOT / path) == expected, f"Frozen file changed: {path}"
    return lock


def score(truth: dict, r: dict) -> dict:
    changes = r.get("changes", [])
    held = [c for c in changes if c.get("blocking")]
    edits = [{"old": a, "new": b, "held": any(transition(c, (a, b)) for c in held)} for a, b in truth.get("substantive", [])]
    faithful = [{"old": a, "new": b, "found": any(transition(c, (a, b)) for c in changes),
                 "cleared": not any(transition(c, (a, b)) for c in held) and any(transition(c, (a, b)) for c in changes)}
                for a, b in truth.get("faithful", [])]
    claims = r.get("note", {}).get("claims", [])
    hidden = r.get("hidden_text", [])
    blockers = [x for x in r.get("reasons", []) if x.get("level") == "block"]
    return {"id": truth["id"], "status": r.get("status"), "expected_status": truth["expected_status"],
            "complete": not r.get("errors"), "errors": r.get("errors", []), "edits": edits, "faithful": faithful,
            "unexpected_blocking": max(0, len(held) - sum(e["held"] for e in edits)),
            "false_cover": bool(truth.get("false_cover")),
            "false_cover_caught": any(c.get("verdict") == "contradicted" for c in claims),
            "true_cover_contradicted": (not truth.get("false_cover")) and any(c.get("verdict") == "contradicted" for c in claims),
            "concealed_new": bool(truth.get("concealed_new")),
            "concealed_blocked": any(x["source"] in ("Rules", "Guard") for x in blockers),
            "inherited_fine_print": bool(truth.get("inherited_fine_print")),
            "hidden": [{"text": h.get("text", "")[:80], "inherited": h.get("inherited")} for h in hidden],
            "blockers": [f"{x['source']}: {x['text'][:120]}" for x in blockers],
            "warnings": [w.get("kind") or w.get("text", "")[:60] for w in r.get("warnings", [])],
            "readers": [{"summary": c.get("summary"), "held": c.get("blocking"), **(c.get("readers") or {})} for c in changes
                        if c.get("cls") in ("substantive", "reworded")],
            "audit_credits": r.get("audit", {}).get("credits")}


def detection(gw, lock) -> tuple[list[dict], dict]:
    rows = []
    for cid in lock["cases"]:
        d = BASE / cid
        cover = (d / "cover.txt").read_text() if (d / "cover.txt").exists() else ""
        r = review(d / "v1.pdf", d / "v2.pdf", cover, gw, ROOT / "out/holdout" / cid, route=False, terms=False, concurrency=1,
                   case={"id": cid, "title": cid, "description": "Second locked corpus"})
        row = score(json.loads((d / "truth.json").read_text()), r)
        rows.append(row)
        print(json.dumps({k: row[k] for k in ("id", "status", "expected_status", "complete", "edits", "faithful", "blockers", "warnings")}), flush=True)
    false_cover = [r for r in rows if r["false_cover"]]
    totals = {"completed": sum(r["complete"] for r in rows), "cases": len(rows),
              "substantive_held": sum(e["held"] for r in rows for e in r["edits"]), "substantive": sum(len(r["edits"]) for r in rows),
              "faithful_cleared": sum(e["cleared"] for r in rows for e in r["faithful"]), "faithful": sum(len(r["faithful"]) for r in rows),
              "faithful_not_found": sum(not e["found"] for r in rows for e in r["faithful"]),
              "false_cover_caught": sum(r["false_cover_caught"] for r in false_cover), "false_covers": len(false_cover),
              "true_cover_contradicted": sum(r["true_cover_contradicted"] for r in rows),
              "unexpected_blocking": sum(r["unexpected_blocking"] for r in rows),
              "new_concealed_blocked": sum(r["concealed_blocked"] for r in rows if r["concealed_new"]),
              "new_concealed": sum(r["concealed_new"] for r in rows),
              "inherited_fine_print_blocked": sum(any(b.startswith("Rules") for b in r["blockers"]) for r in rows if r["inherited_fine_print"]),
              "inherited_fine_print": sum(r["inherited_fine_print"] for r in rows),
              "status_correct": sum(r["status"] == r["expected_status"] for r in rows)}
    return rows, totals


def routing(gw) -> tuple[list[dict], dict]:
    fixtures = json.loads((BASE / "routing.json").read_text())
    book, rows, errors = fixtures["playbook"], [], []
    marks = restore_marks(book)
    everything = [{"id": p["id"], "title": p["title"], "text": p["text"], **({"restore_original": True} if p["id"] in marks else {})}
                  for t in book["topics"] for p in t["passages"]]

    def run(endpoint, request, label, role=None, check=None):
        try:
            response = gw.call(endpoint, request, label)
            return check(response) if check else response
        except Exception as e:
            errors.append(e.as_dict() if isinstance(e, StepError) else {"step": label, "kind": type(e).__name__, "message": str(e)[:200]})
            return None

    for case in fixtures["cases"]:
        base = {"id": case["id"], "clause": str(case.get("clause", "1")), "title": case.get("title", "Term"), "summary": case["summary"],
                "parts": [{"kind": case.get("kind", "wording"), "v1": {"quote": case["we_sent"]}, "v2": {"quote": case["they_returned"]}}]}
        for arm in ("rerank", "all_passages", "first_topic_passage"):
            c, start = copy.deepcopy(base), len(errors)
            if arm == "rerank":
                c["playbook"] = _rerank(c, book, case["fee"], run)
            else:
                c["playbook"] = {"status": "guidance", "passages": everything if arm == "all_passages" else everything[:1]}
            c["decide"] = _decide(c, run, {"annual_fee": case["fee"]})
            _final_route(c)
            kept = [p["id"] for p in (c["playbook"] or {}).get("passages", [])]
            rows.append({"id": case["id"], "fee": case["fee"], "summary": case["summary"], "arm": arm, "expected": case["expected"],
                         "route": c["route"], "correct": c["route"] == case["expected"],
                         "loose": STRICT.get(c["route"], 2) < STRICT[case["expected"]],
                         "strict": STRICT.get(c["route"], 2) > STRICT[case["expected"]],
                         "complete": len(errors) == start, "passages": kept,
                         "expected_passage": case.get("expected_passage"),
                         "expected_passage_kept": case.get("expected_passage") in kept if case.get("expected_passage") else None,
                         "decide": c["decide"], "route_reason": c.get("route_reason"), "errors": errors[start:]})
            print(json.dumps({k: rows[-1][k] for k in ("id", "arm", "expected", "route", "correct", "loose", "passages")}), flush=True)
    arms = {arm: {"correct": sum(r["correct"] and r["complete"] for r in rows if r["arm"] == arm),
                  "loose": sum(r["loose"] for r in rows if r["arm"] == arm), "strict": sum(r["strict"] for r in rows if r["arm"] == arm),
                  "complete": sum(r["complete"] for r in rows if r["arm"] == arm), "cases": len(fixtures["cases"])}
            for arm in ("rerank", "all_passages", "first_topic_passage")}
    return rows, arms


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--only", choices=["detection", "routing"])
    a = ap.parse_args()
    lock = verify_lock()
    gw = RoundGateway(a.live, ROOT / "recordings/holdout")
    out = {"protocol": lock.get("protocol"), "acceptance": lock.get("acceptance")}
    if a.only != "routing":
        out["pairs"], out["detection"] = detection(gw, lock)
        print(json.dumps(out["detection"]), flush=True)
    if a.only != "detection":
        out["routing_rows"], out["routing"] = routing(gw)
        print(json.dumps(out["routing"]), flush=True)
    out["new_actual_credits"], out["new_uncertain_credits"] = gw.credits, gw.unsettled
    (ROOT / "eval/holdout-results.json").write_text(json.dumps(out, indent=2) + "\n")
    first = ROOT / "eval/holdout-results-first.json"
    if not first.exists() and a.only is None:
        head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=ROOT).stdout.strip()
        first.write_text(json.dumps({"git_head": head, "code": {p: sha(ROOT / p) for p in CODE}, **out}, indent=2) + "\n")
    if a.check:
        acc, d, r = lock.get("acceptance", {}), out.get("detection", {}), out.get("routing", {}).get("rerank", {})
        misses = [k for k, got in (("completed", d.get("completed")), ("substantive_held", d.get("substantive_held")),
                                   ("faithful_cleared", d.get("faithful_cleared")), ("false_cover_caught", d.get("false_cover_caught")),
                                   ("status_correct", d.get("status_correct")), ("routing_correct", r.get("correct")))
                  if k in acc and got != acc[k]]
        if "new_loose_routes" in acc and r.get("loose", 0) != acc["new_loose_routes"]:
            misses.append("new_loose_routes")
        if misses:
            raise SystemExit("Holdout acceptance not met: " + ", ".join(misses))


if __name__ == "__main__":
    main()
