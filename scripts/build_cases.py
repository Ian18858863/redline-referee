"""Generate every synthetic case under data/cases/<id>/ (PDFs, cover note, ground truth)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from referee.contracts import demo_pairs, eval_pairs  # noqa: E402
from referee.pdfgen import render_pdf  # noqa: E402

ROOT = Path(__file__).resolve().parents[1] / "data" / "cases"


def write_pair(pair, group: str) -> None:
    out = ROOT / pair.case_id
    render_pdf(pair.v1, out / "v1.pdf")
    render_pdf(pair.v2, out / "v2.pdf", injection=pair.injection)
    (out / "cover.txt").write_text(pair.letter, encoding="utf-8")
    truth = {
        "case_id": pair.case_id, "group": group, "title": pair.title, "description": pair.description,
        "injection": pair.injection, "expected_status": pair.expected_status, "style": pair.meta["style"],
        "substantive": [
            {"id": e.id, "clause": e.clause, "label": e.label, "kind": e.kind, "v1_quote": e.old,
             "v2_quote": e.new, "v1_value": e.v1_value, "v2_value": e.v2_value}
            for e in pair.edits if e.substantive],
        "cosmetic": [
            {"id": e.id, "clause": e.clause, "label": e.label, "kind": e.kind, "v1_quote": e.old, "v2_quote": e.new}
            for e in pair.edits if not e.substantive],
        "letter_claims": pair.letter_claims,
    }
    (out / "truth.json").write_text(json.dumps(truth, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    demos, evals = demo_pairs(), eval_pairs()
    for p in demos:
        write_pair(p, "demo")
    for p in evals:
        write_pair(p, "eval")
    index = [{"id": p.case_id, "group": g, "title": p.title, "description": p.description}
             for g, ps in (("demo", demos), ("eval", evals)) for p in ps]
    (ROOT / "index.json").write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(demos)} demo and {len(evals)} evaluation pairs to {ROOT}")


if __name__ == "__main__":
    main()
