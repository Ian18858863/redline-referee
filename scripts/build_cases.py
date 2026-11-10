"""Generate every synthetic case under data/cases/<id>/ (PDFs, cover note, ground truth)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from referee.contracts import (EXPECTED_ROUTE, demo_pairs, eval_pairs, extra_demo_pairs, hard_pairs,  # noqa: E402
                               struct_pairs)
from referee.pdfgen import render_pdf  # noqa: E402

ROOT = Path(__file__).resolve().parents[1] / "data" / "cases"


def write_pair(pair, group: str) -> None:
    out = ROOT / pair.case_id
    render_pdf(pair.v1, out / "v1.pdf")
    if pair.meta.get("v2_file"):                      # a different document altogether
        (out / "v2.pdf").write_bytes((ROOT.parents[1] / pair.meta["v2_file"]).read_bytes())
    else:
        render_pdf(pair.v2, out / "v2.pdf", injection=pair.injection)
    (out / "cover.txt").write_text(pair.letter, encoding="utf-8")
    truth = {
        "case_id": pair.case_id, "group": group, "title": pair.title, "description": pair.description,
        "injection": pair.injection, "expected_status": pair.expected_status, "style": pair.meta["style"],
        "substantive": [
            {"id": e.id, "clause": e.clause, "label": e.label, "kind": e.kind, "v1_quote": e.old,
             "v2_quote": e.new, "v1_value": e.v1_value, "v2_value": e.v2_value,
             "route": EXPECTED_ROUTE.get(e.id, (None, None))[0], "passage": EXPECTED_ROUTE.get(e.id, (None, None))[1]}
            for e in pair.edits if e.substantive],
        "cosmetic": [
            {"id": e.id, "clause": e.clause, "label": e.label, "kind": e.kind, "v1_quote": e.old, "v2_quote": e.new}
            for e in pair.edits if not e.substantive],
        "letter_claims": pair.letter_claims,
        "note_instruction": bool(pair.meta.get("note_instruction")),
        "wrong_file": bool(pair.meta.get("v2_file")),
    }
    (out / "truth.json").write_text(json.dumps(truth, indent=2) + "\n", encoding="utf-8")


DEMO_ORDER = ["quiet-changes", "disclosed-changes", "two-readers", "quiet-changes-injected", "instruction-in-note",
              "added-clause", "wrong-file", "clean-return"]


def main() -> None:
    demos = sorted(demo_pairs() + extra_demo_pairs(), key=lambda p: DEMO_ORDER.index(p.case_id))
    groups = (("demo", demos), ("eval", eval_pairs()), ("struct", struct_pairs()), ("hard", hard_pairs()))
    for group, pairs in groups:
        for p in pairs:
            write_pair(p, group)
    index = [{"id": p.case_id, "group": g, "title": p.title, "description": p.description} for g, ps in groups for p in ps]
    (ROOT / "index.json").write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    print("wrote " + ", ".join(f"{len(ps)} {g}" for g, ps in groups) + f" pairs to {ROOT}")


if __name__ == "__main__":
    main()
