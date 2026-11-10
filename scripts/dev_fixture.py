"""UI development only: build out/dev/<case>/review.json with the unit-test stand-in for Clocked.

Nothing written here is shipped. The demo and the evaluation use recorded live responses.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from referee.engine import review  # noqa: E402
from referee.gateway import Gateway  # noqa: E402
from tests.fake_clocked import FakeClocked, load_truth  # noqa: E402

index = []
for case in ("clean-return", "quiet-changes", "quiet-changes-injected", "disclosed-changes"):
    d = ROOT / "data" / "cases" / case
    truth = load_truth(d)
    r = review(d / "v1.pdf", d / "v2.pdf", (d / "cover.txt").read_text(),
               Gateway("live", client=FakeClocked(truth)), ROOT / "out" / "dev" / case,
               case={"id": case, "title": truth["title"], "description": truth["description"]})
    index.append({"id": case, "title": truth["title"], "description": truth["description"], "status": r["status"]})
    print(case, r["status"], len(r["changes"]), "changes", r["audit"]["calls"], "calls")
(ROOT / "out" / "dev" / "index.json").write_text(json.dumps(index, indent=1))
