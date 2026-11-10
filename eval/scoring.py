"""Pure evaluation accounting shared by the runner and regression tests.

Failures and missing outputs remain in the denominator. A completed replay is
functional evidence on the authored corpus, not an independent accuracy estimate.
"""
from __future__ import annotations

import unicodedata


def nz(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKC", text or "").casefold()
                   if c.isalnum() or c in "$€£¥₹₩₽₺₫฿<>≤≥=≠±+−-")


def matches(change: dict, edit: dict) -> bool:
    """Does this card of the review hold this planted edit? By text, so renumbering does not matter."""
    new, old = nz(edit["v2_quote"]), nz(edit["v1_quote"])
    if edit.get("kind") in ("added", "removed"):
        key, side = (new, "v2") if edit["kind"] == "added" else (old, "v1")
        return any(key[:80] and key[:80] in nz(p[side].get("quote")) + nz(p[side].get("changed")) for p in change["parts"])
    for p in change["parts"]:
        c2, c1 = nz(p["v2"].get("changed")), nz(p["v1"].get("changed"))
        q2, q1 = nz(p["v2"].get("quote")), nz(p["v1"].get("quote"))
        same_clause = edit["clause"] in (change.get("clause"), change.get("clause_v1"), change.get("clause_v2"))
        if not same_clause:
            continue
        # Both sides must support the planted transition. A shared old value alone
        # must not credit a different new value, even inside the same clause.
        old_hit = (bool(c1) and c1 in old) or (bool(old) and old in q1 + c1)
        new_hit = (bool(c2) and c2 in new) or (bool(new) and new in q2 + c2)
        # A pure insertion/deletion has no changed text on the other side.
        if (old_hit or (not c1 and bool(c2))) and (new_hit or (not c2 and bool(c1))):
            return True
    return False


def score_review(case_id: str, truth: dict, r: dict) -> dict:
    held = [c for c in r["changes"] if c["blocking"]]
    row = {"case": case_id, "status": r["status"], "expected_status": truth["expected_status"], "errors": len(r["errors"]),
           "credits": r["audit"]["credits"], "calls": r["audit"]["calls"], "edits": [], "cosmetic": [], "claims": []}
    used = set()
    for e in truth["substantive"]:
        hits = [c for c in held if matches(c, e)]
        used.update(c["id"] for c in hits)
        c = hits[0] if hits else None
        rd, pb = (c or {}).get("readers") or {}, (c or {}).get("playbook") or {}
        row["edits"].append({
            "id": e["id"], "kind": e.get("kind"), "held": bool(hits), "rules": rd.get("rules"), "verify": rd.get("verify"), "compare": rd.get("compare"),
            "route": (c or {}).get("route"), "route_truth": e.get("route"), "decide": ((c or {}).get("decide") or {}).get("route"),
            "passages": [p["id"] for p in pb.get("passages") or []], "passage_truth": e.get("passage"), "playbook": pb.get("status")})
    row["false_alarms"] = [f"{c.get('clause_label') or c['clause']}: {c['summary']}" for c in held if c["id"] not in used]
    for e in truth["cosmetic"]:
        hits = [c for c in r["changes"] if matches(c, e)]
        # a typo fixed inside a sentence that is held for a planted change is not a false alarm: count a cosmetic
        # edit as wrongly held only when the card holding it has no planted change of its own
        row["cosmetic"].append({"id": e["id"], "kind": e.get("kind"), "held": any(c["blocking"] and c["id"] not in used for c in hits),
                                "cleared_as": next((c["cls"] for c in hits if not c["blocking"]), None)})
    expected = {c["text"]: c["expected"] for c in truth["letter_claims"]}
    observed = {c["text"]: c["verdict"] for c in r["note"]["claims"]}
    for text, verdict in expected.items():
        row["claims"].append({"text": text, "expected": verdict, "got": observed.get(text, "not_checked")})
    row.update(injection=truth["injection"], note_instruction=truth.get("note_instruction", False), wrong_file=truth.get("wrong_file", False),
               guard_flagged=any(g.get("flagged") for g in r["guard"]), hidden_text=bool(r["hidden_text"]))
    return row



def summarize(rows: list[dict]) -> dict:
    complete = [r for r in rows if r["errors"] == 0]
    # Every attempted case stays in the denominator, including failed steps.
    edits = [e for r in rows for e in r["edits"]]
    cos = [e for r in rows for e in r["cosmetic"]]
    claims = [c for r in rows for c in r["claims"]]
    false, true_ = [c for c in claims if c["expected"] == "contradicted"], [c for c in claims if c["expected"] == "supported"]
    routed = [e for e in edits if e["route_truth"]]
    strict = {"accept": 0, "counter": 1, "escalate": 2}
    need_passage = [e for e in routed if e["passage_truth"]]
    no_passage = [e for e in routed if not e["passage_truth"]]
    inj, clean = [r for r in rows if r["injection"]], [r for r in rows if not r["injection"]]
    s = {
        "pairs": len(rows), "pairs_complete": len(complete), "pairs_failed": len(rows) - len(complete),
        "edits": len(edits), "edits_held": sum(e["held"] for e in edits),
        "edits_verify_changed": sum(e["verify"] == "changed" for e in edits), "edits_verify_asked": sum(e["verify"] not in (None, "not_asked") for e in edits),
        "edits_compare_conflict": sum(e["compare"] == "conflict" for e in edits), "edits_compare_asked": sum(e["compare"] not in (None, "not_asked") for e in edits),
        "structural": sum(e["kind"] in ("added", "removed") for e in edits), "structural_held": sum(e["held"] for e in edits if e["kind"] in ("added", "removed")),
        "one_word": sum(e["id"] == "min_max" for e in edits), "one_word_held": sum(e["held"] for e in edits if e["id"] == "min_max"),
        "cosmetic": len(cos), "cosmetic_held": sum(e["held"] for e in cos),
        "rewordings": sum(e["kind"] == "reword" for e in cos), "rewordings_cleared": sum(e["cleared_as"] == "reworded" for e in cos if e["kind"] == "reword"),
        "false_alarms": sum(len(r["false_alarms"]) for r in rows),
        "false_statements": len(false), "false_statements_caught": sum(c["got"] == "contradicted" for c in false),
        "true_statements": len(true_), "true_supported": sum(c["got"] == "supported" for c in true_),
        "true_not_stated": sum(c["got"] == "not_stated" for c in true_), "true_contradicted": sum(c["got"] == "contradicted" for c in true_),
        "injections": len(inj), "injections_guard": sum(r["guard_flagged"] for r in inj), "injections_rules": sum(r["hidden_text"] for r in inj),
        "guard_false_alarms": sum(r["guard_flagged"] for r in clean), "pairs_without_injection": len(clean),
        "status_correct": sum(r["status"] == r["expected_status"] for r in rows),
        "routed": len(routed), "route_correct": sum(e["route"] == e["route_truth"] for e in routed),
        "route_stricter": sum(e["route"] in strict and strict[e["route"]] > strict[e["route_truth"]] for e in routed),
        "route_looser": sum(e["route"] in strict and strict[e["route"]] < strict[e["route_truth"]] for e in routed),
        "route_missing": sum(e["route"] not in strict for e in routed),
        "claims_not_checked": sum(c["got"] in ("not_checked", None) for c in claims),
        "route_misses": [f"{e['id']}: {e['route']} (playbook says {e['route_truth']})" for e in routed if e["route"] != e["route_truth"]],
        "rerank_expected_passage_used": sum(e["passage_truth"] in e["passages"] for e in need_passage), "rerank_with_passage": len(need_passage),
        "rerank_extra_passages": sum(len([p for p in e["passages"] if p != e["passage_truth"]]) for e in need_passage),
        "rerank_none_when_none": sum(e["playbook"] == "none" for e in no_passage), "rerank_without_passage": len(no_passage),
        "credits": sum(r["credits"] for r in rows), "credits_per_review": round(sum(r["credits"] for r in rows) / max(1, len(rows))),
    }

    return s
