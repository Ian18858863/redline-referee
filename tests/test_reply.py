"""The reply to the customer: Fill drafts it, Verify decides what may stay. The API is stubbed here."""
import json
from pathlib import Path

from referee.gateway import Gateway
from referee.reply import build_brief, customer_position, draft_reply

ROOT = Path(__file__).resolve().parents[1]


class Res(dict):
    status, attempts, headers, cost_usd, credits, model_ms, idempotency_key = 200, 1, {}, 0.0, 2, 50, "k"


class Stub:
    """Fill writes one sentence per change; Verify supports all of them except the ones listed in `reject`."""

    def __init__(self, invent: dict | None = None, reject: set | None = None, fail: set | None = None, short: bool = False):
        self.invent, self.reject, self.fail, self.short, self.calls = invent or {}, reject or set(), fail or set(), short, []

    def fill(self, request, idempotency_key=None, timeout=None):
        self.calls.append(("fill", request))
        if "fill" in self.fail:
            raise RuntimeError("fill outage")
        fields = {}
        for name, spec in request["fields"].items():
            if spec["type"] == "generate":
                fields[name] = {"value": self.invent.get(name, "We accept this change." if "accept" in spec["description"] else
                                                         "This change is under review." if "escalate" in spec["description"] else
                                                         "We cannot accept this change as written."), "source": "generated"}
            elif spec["type"] == "extract":
                line = next(l for l in request["text"].split("\n") if "What we can agree to instead" in l or "put back" in l)
                fields[name] = {"value": line.split("instead: ")[-1] if "instead: " in line else None}
            elif spec["type"] == "yes_no":
                fields[name] = {"value": "countered" in spec["description"] and "do not accept" in request["text"]}
            else:
                fields[name] = {"value": "firm" if "do not accept" in request["text"] else "routine"}
        return Res({"fields": fields})

    def grounding(self, request, idempotency_key=None, timeout=None):
        self.calls.append(("grounding", request))
        if "grounding" in self.fail:
            raise RuntimeError("verify outage")
        claims = [{"claim": c, "verdict": "not_stated" if any(r in c for r in self.reject) else "supported",
                   "supported": 0.02 if any(r in c for r in self.reject) else 0.97} for c in request["claims"]]
        return Res({"claims": claims[:-1] if self.short else claims})


def review_fixture() -> dict:
    change = lambda cid, clause, title, passages=(): {
        "id": cid, "clause": clause, "title": title, "blocking": True,
        "parts": [{"v1": {"quote": f"sent wording of {title}"}, "v2": {"quote": f"returned wording of {title}"}}],
        "compare": {"v1_quote": f"The full sentence we sent about {title}."}, "playbook": {"passages": [{"id": p} for p in passages]}}
    return {"changes": [change("c1", "3", "Renewal"), change("c2", "4.2", "Payment Terms", ["payment-large"]),
                        change("c3", "9", "Limitation of Liability"), change("c4", "7", "Intellectual Property"),
                        {"id": "c9", "clause": "14", "title": "Notices", "blocking": False, "parts": [{"v1": {}, "v2": {}}]}]}


DECISIONS = {"c1": "accept", "c2": "counter", "c3": "escalate", "c4": "counter"}


def run(**kw):
    stub = Stub(**kw)
    return draft_reply(review_fixture(), DECISIONS, Gateway("live", client=stub)), stub


def test_the_brief_never_carries_the_internal_ladder():
    brief, items = build_brief(review_fixture(), DECISIONS)
    assert [i["id"] for i in items] == ["c1", "c2", "c3", "c4"]
    assert "VP Finance" not in brief and "approval" not in brief.lower()
    assert "net forty-five days" in brief                       # the wording the playbook clears for customers
    assert "The full sentence we sent about Intellectual Property." in brief   # no cleared wording: ask for ours back


def test_position_comes_from_the_playbooks_cleared_wording_or_from_what_we_sent():
    r = review_fixture()
    assert customer_position(r["changes"][1]) == ("We can extend payment to net forty-five days from the invoice date.", "playbook")
    assert customer_position(r["changes"][3])[1] == "sent"


def test_every_drafted_sentence_is_checked_and_supported_ones_are_kept():
    out, stub = run()
    assert out["ran"] and not out["errors"] and len(out["paragraphs"]) == 4 and not out["removed"]
    claims = next(req for name, req in stub.calls if name == "grounding")["claims"]
    assert len(claims) == 4 and "net forty-five days" in claims[1]
    assert "Clause 4.2 (Payment Terms)" in out["text"] and "corrected copy" in out["text"]


def test_a_sentence_verify_does_not_support_is_removed_from_what_the_customer_sees():
    out, _ = run(invent={"reply_c3": "We will raise the liability cap to twenty-four months."}, reject={"twenty-four months"})
    assert [p["id"] for p in out["removed"]] == ["c3"] and "twenty-four" not in out["text"]
    assert len(out["paragraphs"]) == 3


def test_a_failed_step_gives_no_reply_rather_than_an_unchecked_one():
    for step in ("fill", "grounding"):
        out, _ = run(fail={step})
        assert out["errors"] and out["text"] == "" and not out["ran"]


def test_a_verify_answer_that_skips_a_sentence_is_an_error():
    out, _ = run(short=True)
    assert out["errors"] and out["text"] == ""


def test_typed_answers_choose_the_closing():
    out, _ = run()
    assert out["tone"] == "firm" and out["closing"].startswith("Please send a corrected copy")
    calm = draft_reply(review_fixture(), {"c1": "accept"}, Gateway("live", client=Stub()))
    assert calm["tone"] == "routine" and calm["closing"].startswith("We will confirm")


def test_nothing_to_decide_needs_no_api_call():
    stub = Stub()
    out = draft_reply({"changes": []}, {}, Gateway("live", client=stub))
    assert "ready to countersign" in out["text"] and stub.calls == []


def test_playbook_cleared_wording_exists_for_every_passage_the_desk_may_offer():
    book = json.loads((ROOT / "data" / "playbook.json").read_text())
    say = {p["id"] for t in book["topics"] for p in t["passages"] if p.get("say")}
    assert {"payment-large", "fees-increase", "term-renewal-notice", "service-credits", "data-export"} <= say
