"""Score the application against exact ground truth, on recorded answers of the live API.

  python eval/run_eval.py            replay recordings/ (no API calls, no key)
  python eval/run_eval.py --live     call Clocked for whatever is not recorded yet, and record it

What is measured, and on what:
  * classification, cover note, Guard, status and routing: 12 seeded pairs + 3 pairs with whole
    clauses added or removed (full reviews);
  * the two readers of meaning: the hard set, 39 edits of one sentence each (eval/run_hard.py);
  * what each reader changes: every recorded pair is decided again with Verify ignored, with
    Compare ignored, and with both ignored, from the same recordings.
Finding differences in extracted, aligned text is the rule layer's job and is deterministic;
PDF extraction and alignment can still miss content. What these numbers measure is the classification of each
difference, the reading of the cover note, the routing, and the readers.
Writes eval/results.json and eval/RESULTS.md.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))
from referee.engine import review  # noqa: E402
from referee.gateway import Gateway  # noqa: E402
from eval.scoring import matches, score_review, summarize  # noqa: E402

CASES, REC = ROOT / "data" / "cases", ROOT / "recordings"
INDEX = json.loads((CASES / "index.json").read_text())



def run(case_id: str, live: bool, tag: str = "full", **kw) -> dict:
    d = CASES / case_id
    gw = Gateway("auto" if live else "replay", REC / case_id, record=live, credit_cap=900, time_budget_s=300)
    cover = (d / "cover.txt").read_text() if (d / "cover.txt").exists() else ""
    return review(d / "v1.pdf", d / "v2.pdf", cover, gw, ROOT / "out" / "eval" / tag / case_id,
                  case={"id": case_id, "title": case_id, "description": ""}, **kw)


def score(case_id: str, r: dict) -> dict:
    truth = json.loads((CASES / case_id / "truth.json").read_text())
    return score_review(case_id, truth, r)


def pct(a: int, b: int) -> str:
    return f"{a} of {b}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--check", action="store_true", help="exit nonzero for incomplete full reviews or missing expected claims/routes")
    ap.add_argument("--check-ablation", action="store_true", help="also require all counterfactual replays to be complete")
    a = ap.parse_args()
    by_group = lambda g: [c["id"] for c in INDEX if c["group"] == g]
    main_ids = by_group("eval") + by_group("struct")
    rows = []
    for cid in main_ids:
        row = score(cid, run(cid, a.live))
        rows.append(row)
        print(f"{cid:10} {row['status']:9}/{row['expected_status']:9} held {sum(e['held'] for e in row['edits'])}/{len(row['edits'])} "
              f"false alarms {len(row['false_alarms'])} errors {row['errors']} credits {row['credits']}")
    s = summarize(rows)

    # ---- what each reader changes: every recorded pair, decided again without it (replay only) ----
    from run_hard import covering  # noqa: E402
    abl_ids = [c["id"] for c in INDEX if c["group"] in ("demo", "eval", "struct", "hard") and (REC / c["id"]).is_dir() and c["id"] != "wrong-file"]
    variants = {"all three": frozenset(), "without Verify": frozenset({"verify"}), "without Compare": frozenset({"compare"}),
                "rules alone": frozenset({"verify", "compare"})}
    abl = {k: {"real_cleared": 0, "same_held": 0, "false_statements_missed": 0, "status_wrong": 0, "pairs": 0, "incomplete": 0, "failures": []} for k in variants}
    totals = {"real": 0, "same": 0, "false_statements": 0}
    for cid in abl_ids:
        truth = json.loads((CASES / cid / "truth.json").read_text())
        hard = truth["group"] == "hard"
        for name, ign in variants.items():
            r = run(cid, False, tag="abl-" + name.replace(" ", "-"), route=False, terms=False, ignore_readers=ign)
            t = abl[name]
            t["pairs"] += 1
            t["incomplete"] += bool(r["errors"])
            if r["errors"]:
                t["failures"].append({"case": cid, "steps": [
                    {k: e.get(k) for k in ("endpoint", "step", "kind")} for e in r["errors"]]})
            find = covering if hard else matches
            for e in truth["substantive"]:
                t["real_cleared"] += not any(c["blocking"] and find(c, e) for c in r["changes"])
            for e in truth["cosmetic"]:
                if hard or e.get("kind") == "reword":
                    t["same_held"] += any(c["blocking"] and find(c, e) for c in r["changes"])
            got = {c["text"]: c["verdict"] for c in r["note"]["claims"]}
            t["false_statements_missed"] += sum(1 for c in truth["letter_claims"] if c["expected"] == "contradicted" and got.get(c["text"]) != "contradicted")
            t["status_wrong"] += r["status"] != truth["expected_status"]
            if name == "all three":
                totals["real"] += len(truth["substantive"])
                totals["same"] += sum(1 for e in truth["cosmetic"] if hard or e.get("kind") == "reword")
                totals["false_statements"] += sum(1 for c in truth["letter_claims"] if c["expected"] == "contradicted")
    hard_summary = json.loads((ROOT / "eval" / "hard-summary.json").read_text()) if (ROOT / "eval" / "hard-summary.json").exists() else None
    (ROOT / "eval" / "results.json").write_text(json.dumps({"summary": s, "ablation": abl, "ablation_totals": totals, "ablation_pairs": abl_ids,
                                                           "hard": hard_summary, "pairs": rows}, indent=1) + "\n")

    md = ["# Evaluation results", "",
          "Everything here is computed from recorded answers of the live Clocked API (`recordings/`) against exact ground truth "
          "(`data/cases/*/truth.json`). `python eval/run_eval.py` and `python eval/run_hard.py` recompute it without a key.", "",
          "Differences in extracted, aligned text are found by deterministic code. This is not a guarantee of recall across arbitrary PDF layouts. "
          "What is measured below is what the application does with each difference. Failed reviews, missing edits and unchecked claims stay in the denominators.", "",
          f"## Seeded pairs ({s['pairs_complete']} of {s['pairs']} completed without a failed step)", "",
          "Twelve pairs with two to four planted substantive edits, plus three pairs in which whole clauses were added or removed and the rest renumbered.", "",
          "| Measure | Result |", "|---|---|",
          f"| Planted substantive edits held as changes | {pct(s['edits_held'], s['edits'])} |",
          f"| ... read as contradicted by Verify (sentence against the other PDF) | {pct(s['edits_verify_changed'], s['edits_verify_asked'])} asked |",
          f"| ... read as a conflict by Compare (clause against clause) | {pct(s['edits_compare_conflict'], s['edits_compare_asked'])} asked |",
          f"| Whole clauses added or removed, held | {pct(s['structural_held'], s['structural'])} |",
          f"| One-word swaps between two real words (maximum, minimum), held | {pct(s['one_word_held'], s['one_word'])} |",
          f"| Cosmetic edits (spelling, formatting, address, rewording) wrongly held | {pct(s['cosmetic_held'], s['cosmetic'])} |",
          f"| Faithful rewordings cleared | {pct(s['rewordings_cleared'], s['rewordings'])} |",
          f"| Clauses held with no planted cause | {s['false_alarms']} |",
          f"| Expected cover-note statements not checked | {s['claims_not_checked']} |",
          f"| False cover-note statements judged contradicted | {pct(s['false_statements_caught'], s['false_statements'])} |",
          f"| True cover-note statements judged supported | {pct(s['true_supported'], s['true_statements'])} ({s['true_not_stated']} not stated, {s['true_contradicted']} wrongly contradicted) |",
          f"| Hidden instructions flagged by Guard / found as hidden text by rules | {pct(s['injections_guard'], s['injections'])} / {pct(s['injections_rules'], s['injections'])} |",
          f"| Guard false alarms on pairs without an injection | {pct(s['guard_false_alarms'], s['pairs_without_injection'])} |",
          f"| Final status equal to ground truth | {pct(s['status_correct'], s['pairs'])} |",
          f"| Credits, all {s['pairs']} full reviews | {s['credits']} (about {s['credits_per_review']} per review) |", "",
          "## Routing", "",
          "Each planted edit has a route the playbook dictates (`EXPECTED_ROUTE` in `referee/contracts.py`). Rerank picks the passages, Decide answers two yes/no questions, code composes the route.", "",
          "| Measure | Result |", "|---|---|",
          f"| Expected routes with no usable route returned | {s['route_missing']} |",
          f"| Route equal to what the playbook dictates | {pct(s['route_correct'], s['routed'])} |",
          f"| Wrong on the strict side (sent to a person or countered when the desk could accept) | {s['route_stricter']} |",
          f"| Wrong on the loose side (accepted or countered what needed an approver) | {s['route_looser']} |",
          f"| The deciding playbook passage was among those Rerank kept | {pct(s['rerank_expected_passage_used'], s['rerank_with_passage'])} |",
          f"| Other passages Rerank also kept for those edits | {s['rerank_extra_passages']} |",
          f"| No passage kept when the playbook has none (escalated by rule) | {pct(s['rerank_none_when_none'], s['rerank_without_passage'])} |", ""]
    if s["route_misses"]:
        md += ["Routes that differ from the playbook: " + "; ".join(s["route_misses"]) + ".", ""]
    if hard_summary:
        h = hard_summary
        md += ["## The hard set: 39 one-sentence edits that a diff cannot settle", "",
               "24 change what a sentence obliges or allows (a duty made optional, a mutual limit made one-sided, days made business days, "
               "a negation removed). 15 say the same thing in other words. One edit per sentence, two pairs (`hard-a`, `hard-b`).", "",
               "| | 24 changes of meaning | 15 faithful rewordings |", "|---|---|---|",
               f"| The application (clears only if Verify supports both ways and Compare does not object) | {h['app_held_changed']} held, {len(h['app_wrongly_cleared'])} wrongly cleared | {h['app_cleared_same']} cleared, {len(h['app_held_same'])} held |",
               f"| Verify alone | read {h['verify_changed_on_changed']} as changed, **{len(h['verify_same_on_changed'])} as the same** | read {h['verify_same_on_same']} as the same |",
               f"| Compare alone | read {h['compare_conflict_on_changed']} as conflicting, {len(h['compare_same_on_changed'])} as the same, {h['changed_total'] - h['compare_conflict_on_changed'] - len(h['compare_same_on_changed'])} not flagged | read {h['compare_same_on_same']} as the same |", "",
               f"The two readers disagreed on {len(h['disagree'])} edits ({', '.join(h['disagree'])}). Verify read two real changes as the same meaning "
               "(a mutual exclusion of damages made one-sided; \"will defend\" made \"may defend\"); Compare read both as conflicts, so they were held. "
               "Compare did not flag one widened scope that Verify read as changed. The one rewording both readers took for a change "
               "(\"exclusive of taxes\" to \"do not include taxes\") was held: an error on the strict side.", ""]
    md += ["## What each reader changes", "",
           f"Every recorded pair with ground truth ({len(abl_ids)} pairs: {totals['real']} real changes, {totals['same']} faithful rewordings, "
           f"{totals['false_statements']} false cover-note statements) decided again from the same recordings, with a reader's answers ignored.", "",
           "| Decided by | Real changes cleared (missed) | Faithful rewordings held (false alarms) | False statements not caught | Final status wrong |", "|---|---|---|---|---|"]
    for name, t in abl.items():
        md.append(f"| {name[0].upper() + name[1:]} | {t['real_cleared']} | {t['same_held']} | {t['false_statements_missed']} | {t['status_wrong']} of {t['pairs']} |")
    md += ["", "Ablation coverage (a missing counterfactual response is not a model error or a completed measurement):", "",
           "| Variant | Complete replays |", "|---|---|"]
    for name, t in abl.items():
        md.append(f"| {name} | {t['pairs'] - t['incomplete']} of {t['pairs']} |")
    for name, t in abl.items():
        for failure in t["failures"]:
            steps = ", ".join(f"{e['endpoint']}: {e['step']} ({e['kind']})" for e in failure["steps"])
            md.append(f"\nIncomplete: `{failure['case']}` / {name}: {steps}. "
                      "The changed request has no saved response; it was not replaced by a baseline or a fabricated answer.")
    md += ["", "Without Verify the cover note is taken at its word. With rules alone every rewording is a change and nothing checks the note.", "",
           "These are authored variations of one synthetic agreement plus a hard set and one unrelated agreement. They show that the application "
           "does what it claims on data with exact answers. They are not an accuracy figure for contracts in general.", ""]
    (ROOT / "eval" / "RESULTS.md").write_text("\n".join(md))
    print(json.dumps(s, indent=1)); print(json.dumps(abl, indent=1))
    if (a.check or a.check_ablation) and (not rows or s["pairs_failed"] or s["claims_not_checked"] or s["route_missing"]
                    or (a.check_ablation and any(t["incomplete"] for t in abl.values()))):
        raise SystemExit("Evaluation incomplete: see the named failures and coverage counts above.")


if __name__ == "__main__":
    main()
