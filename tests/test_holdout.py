"""The second locked corpus: frozen inputs, and a replay of what the live API answered on them."""
import json
from pathlib import Path

import pytest

from eval.run_holdout import BASE, card_transition, detection, routing, verify_lock
from eval.round_budget import RoundGateway

ROOT = Path(__file__).resolve().parents[1]
REC = ROOT / "recordings/holdout"
pytestmark = pytest.mark.skipif(not REC.is_dir(), reason="holdout not recorded")


def test_frozen_holdout_inputs_are_unchanged():
    lock = verify_lock()
    assert len(lock["cases"]) == 8 and lock["protocol"] == 2


def test_first_output_is_kept_with_the_code_that_produced_it():
    first = json.loads((ROOT / "eval/holdout-results-first.json").read_text())
    assert first["git_head"] and set(first["code"]) >= {"referee/engine.py", "referee/rules.py", "referee/textdoc.py"}
    assert first["detection"]["cases"] == 8 and first["routing"]["rerank"]["cases"] == 10


@pytest.fixture(scope="module")
def replayed():
    gw = RoundGateway(False, REC)
    rows, totals = detection(gw, verify_lock())
    route_rows, arms = routing(gw)
    assert gw.sent == 0
    return rows, totals, route_rows, arms


def test_detection_replays_to_the_reported_numbers(replayed):
    rows, t, _, _ = replayed
    assert t["completed"] == 8 and t["substantive_held"] == 6 and t["false_cover_caught"] == 3 and t["status_correct"] == 8
    assert t["true_cover_contradicted"] == 0 and t["unexpected_blocking"] == 0
    assert t["faithful_cleared"] == 8 and t["faithful_cleared_card_level"] == 9      # see the scoring erratum
    assert t["new_concealed_blocked"] == 1 and t["inherited_fine_print_blocked"] == 0 and t["inherited_fine_print"] == 3


def test_newly_concealed_text_blocks_and_inherited_fine_print_does_not(replayed):
    rows = {r["id"]: r for r in replayed[0]}
    assert any(b.startswith("Rules") for b in rows["new-concealed"]["blockers"]) and any(b.startswith("Guard") for b in rows["new-concealed"]["blockers"])
    assert rows["inherited-fineprint"]["status"] == "ready" and all(h["inherited"] for h in rows["inherited-fineprint"]["hidden"])


def test_edited_pdfs_are_recognised_only_where_the_file_was_edited_in_place(replayed):
    rows = {r["id"]: r for r in replayed[0]}
    edited = {cid for cid, r in rows.items() if "edited_in_place" in r["warnings"]}
    assert edited == {"btsla-changed", "btsla-benign", "inherited-fineprint"}


def test_routing_replays_with_no_loose_route_in_any_arm(replayed):
    _, _, rows, arms = replayed
    assert {a: v["correct"] for a, v in arms.items()} == {"rerank": 9, "all_passages": 7, "first_topic_passage": 5}
    assert all(v["loose"] == 0 and v["complete"] == 10 for v in arms.values())
    restored = [r["id"] for r in rows if r["arm"] == "rerank" and (r["decide"] or {}).get("counter_kind") == "restore"]
    assert restored == ["nonrenewal-300000-30", "uplift-200000-5"]
    unsure = next(r for r in rows if r["arm"] == "rerank" and r["id"] == "nonrenewal-60000-45")
    assert unsure["route"] == "escalate" and "not sure" in unsure["route_reason"]   # at the threshold: a person decides


def test_card_level_scoring_still_needs_both_sides_on_the_card():
    card = {"parts": [{"v1": {"quote": "terms are found invalid by any court", "changed": "are found invalid by"}, "v2": {"quote": "If any court,", "changed": ""}},
                      {"v1": {"quote": "the remaining terms", "changed": ""}, "v2": {"quote": "finds any terms invalid, the remaining", "changed": "finds any terms invalid"}}]}
    assert card_transition(card, ("terms are found invalid by any court", "any court finds any terms invalid"))
    assert not card_transition(card, ("terms are found invalid by any court", "any court upholds every term"))
