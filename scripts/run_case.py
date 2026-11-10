"""Run cases through the review and write the result where the site reads it.

  python scripts/run_case.py --demo                 replay the demo cases from recordings/
  python scripts/run_case.py --demo --live          call Clocked and record every exchange
  python scripts/run_case.py quiet-changes --live   one case

Replay never calls the API. Live needs CLOCKED_API_KEY.
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


def run(case_id: str, live: bool, out_root: Path, **kw) -> dict:
    d = CASES / case_id
    truth = json.loads((d / "truth.json").read_text())
    gw = Gateway("auto" if live else "replay", ROOT / "recordings" / case_id, record=live,
                 credit_cap=kw.pop("credit_cap", None))
    r = review(d / "v1.pdf", d / "v2.pdf", (d / "cover.txt").read_text(), gw, out_root / case_id,
               case={"id": case_id, "title": truth["title"], "description": truth["description"]}, **kw)
    live_calls = sum(not a["replayed"] for a in gw.audit)
    print(f"{case_id:26} {r['status']:10} expected {truth['expected_status']:8} changes {len(r['changes']):2} "
          f"errors {len(r['errors'])} calls {len(gw.audit)} (live {live_calls}) credits {r['audit']['credits']}")
    for e in r["errors"][:4]:
        print("   !", e["endpoint"], e["step"], "-", e["message"][:160])
    return r


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cases", nargs="*")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--out", default=str(ROOT / "docs" / "cases"))
    ap.add_argument("--credit-cap", type=int, default=None)
    a = ap.parse_args()
    index = json.loads((CASES / "index.json").read_text())
    ids = a.cases or [c["id"] for c in index if c["group"] == "demo"]
    out = Path(a.out)
    listing = []
    for cid in ids:
        r = run(cid, a.live, out, credit_cap=a.credit_cap)
        meta = next(c for c in index if c["id"] == cid)
        listing.append({"id": cid, "title": meta["title"], "description": meta["description"], "status": r["status"]})
    if a.demo or not a.cases:
        (out / "index.json").write_text(json.dumps(listing, indent=1) + "\n")


if __name__ == "__main__":
    main()
