"""The reply to the customer: drafted by Fill, then checked sentence by sentence by Verify.

After the reviewer has decided each change (accept, counter, escalate), this module turns the
decisions into a message the customer can read. Two rules shape it:

  * The customer must never see the internal ladder (who may approve what). Fill writes from a
    brief that carries only what may be said: what we accept, the position we hold, and which
    clauses are still with our approvers.
  * A drafted sentence is sent only if Verify finds it supported by that brief. A sentence that
    is contradicted or not stated is removed from the draft and listed for the reviewer.

Fill is also asked three typed questions about the reply as a whole (tone to take, whether a
corrected copy should be requested, whether a call is warranted); code uses the answers to pick
the closing line.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .gateway import Gateway, StepError

VERIFY_FLOOR = 0.6
INSTRUCTIONS = (
    "You are writing for the Provider's deal desk to the Customer's procurement contact, about a copy of an "
    "agreement the Customer returned with changes. Use only the brief. Be plain, courteous and specific. For an "
    "accepted change, say we accept it. For a countered change, state the position we can agree to, exactly as "
    "the brief gives it, and ask for the clause to be put back or changed to that. For a change that is with our "
    "approvers, say only that it is under review; do not promise an outcome. Never mention internal approvers, "
    "approval limits, playbooks or percentages of confidence."
)
VERIFY_INSTRUCTIONS = (
    "The source is the brief the reply was written from. A claim is supported only if the brief states the same "
    "decision about the same clause: accepted, countered with that exact position, or under review. A claim that "
    "promises something the brief does not state, or names a different term, is contradicted or not stated."
)
ACTION_WORDS = {"accept": "We accept this change.", "counter": "We do not accept this change as written.",
                "escalate": "This change is under review on our side."}


def _nz(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", t.lower())


def _say() -> dict[str, str]:
    book = json.loads((Path(__file__).resolve().parents[1] / "data" / "playbook.json").read_text(encoding="utf-8"))
    return {p["id"]: p["say"] for t in book["topics"] for p in t["passages"] if p.get("say")}


def customer_position(change: dict) -> tuple[str, str]:
    """What may be said to the customer about a countered change, and where it comes from.

    The playbook marks some wording as cleared for customers (`say`). Without it, the only safe
    position is the sentence we sent in the first place.
    """
    say = _say()
    for p in (change.get("playbook") or {}).get("passages") or []:
        if p.get("id") in say:
            return say[p["id"]], "playbook"
    sent = (change.get("compare") or {}).get("v1_quote") or change["parts"][0]["v1"].get("quote") or ""
    return re.sub(r"\s+", " ", sent).strip(), "sent"


def build_brief(review: dict, decisions: dict[str, str]) -> tuple[str, list[dict]]:
    lines, items = [], []
    for c in review["changes"]:
        d = decisions.get(c["id"])
        if not c.get("blocking") or d not in ACTION_WORDS:
            continue
        part = c["parts"][0]
        returned = re.sub(r"\s+", " ", part["v2"].get("quote") or "").strip()
        text = f"Clause {c['clause']} ({c['title']}). The returned copy reads: \"{returned}\". {ACTION_WORDS[d]}"
        if d == "counter":
            position, source = customer_position(c)
            text += (f" What we can agree to instead: {position}" if source == "playbook"
                     else f" We ask for the wording we sent to be put back: \"{position}\".")
        lines.append(text)
        items.append({"id": c["id"], "clause": c["clause"], "title": c["title"], "decision": d})
    return "\n".join(lines), items


def draft_reply(review: dict, decisions: dict[str, str], gw: Gateway) -> dict:
    brief, items = build_brief(review, decisions)
    out = {"ran": False, "paragraphs": [], "removed": [], "text": "", "closing": None, "errors": [], "brief": brief,
           "decisions": {i["id"]: i["decision"] for i in items}}
    if not items:
        out["text"] = "Thank you. The returned copy matches what we sent, and we are ready to countersign."
        return out
    fields = {f"reply_{i['id']}": {"type": "generate", "format": "text", "max_tokens": 70,
                                   "description": f"One or two sentences to the customer about clause {i['clause']} ({i['title']}), "
                                                  f"saying what we decided: {i['decision']}."}
              for i in items}
    for i in items:
        if i["decision"] == "counter":          # the position itself is read out of the brief verbatim, never written freely
            fields[f"position_{i['id']}"] = {"type": "extract", "infer": False,
                                             "description": f"The sentence stating what we can agree to instead for clause {i['clause']} ({i['title']})"}
    fields["corrected_copy"] = {"type": "yes_no", "description": "Does at least one change need the customer to send a corrected copy "
                                                                 "(a change we countered)?"}
    fields["call_needed"] = {"type": "yes_no", "description": "Are two or more changes still under review on our side, so that a "
                                                              "short call would help?"}
    fields["tone"] = {"type": "choice", "options": ["routine", "firm"],
                      "description": "routine if everything is accepted or under review; firm if we refuse any change as written"}
    try:
        f = gw.call("fill", {"text": brief, "instructions": INSTRUCTIONS, "fields": fields}, "fill reply")
    except StepError as e:
        out["errors"].append(e.as_dict())
        return out
    got = f.get("fields") or {}
    drafted = []
    for i in items:
        v = (got.get(f"reply_{i['id']}") or {}).get("value")
        if isinstance(v, str) and v.strip():
            text = re.sub(r"\s+", " ", v).strip()
            if text[-1] not in ".!?":
                text = text.rsplit(". ", 1)[0].rstrip(".") + "." if ". " in text else text + "."
            pos = (got.get(f"position_{i['id']}") or {}).get("value") if i["decision"] == "counter" else None
            if isinstance(pos, str) and pos.strip() and _nz(pos) not in _nz(text):
                pos = re.sub(r"\s+", " ", pos).strip()
                text += " " + (pos if pos[-1] in ".!?\"" else pos + ".")
            drafted.append({**i, "text": text})
        else:
            out["removed"].append({**i, "text": "", "reason": "Fill returned no sentence for this change."})
    if drafted:
        try:
            vr = gw.call("grounding", {"documents": [{"id": "brief", "text": brief}], "answer": " ".join(d["text"] for d in drafted),
                                       "claims": [d["text"] for d in drafted], "instructions": VERIFY_INSTRUCTIONS,
                                       "threshold": VERIFY_FLOOR, "evidence": "all"}, "verify reply")
        except StepError as e:
            out["errors"].append(e.as_dict())
            return out
        claims = vr.get("claims") or []
        if len(claims) != len(drafted):
            out["errors"].append({"endpoint": "Verify", "step": "verify reply", "message": "The response did not cover every drafted sentence.",
                                  "status": None, "kind": "invalid_response"})
            return out
        for d, c in zip(drafted, claims):
            d.update(verdict=c.get("verdict"), supported=c.get("supported"),
                     evidence=re.sub(r"\s+", " ", (c.get("evidence") or {}).get("quote") or "") or None)
            if c.get("verdict") == "supported" and (c.get("supported") or 0) >= VERIFY_FLOOR:
                out["paragraphs"].append(d)
            else:
                out["removed"].append({**d, "reason": "Verify did not find this sentence supported by the decisions."})
    yes = lambda k: bool((got.get(k) or {}).get("value"))
    tone = (got.get("tone") or {}).get("value")
    closing = ("Please send a corrected copy with the points above and we will countersign promptly."
               if yes("corrected_copy") else "We will confirm as soon as our review is complete.")
    if yes("call_needed"):
        closing += " If it helps, we are glad to walk through the open points on a short call."
    out.update(ran=True, closing=closing, tone=tone,
               typed={"corrected_copy": got.get("corrected_copy"), "call_needed": got.get("call_needed"), "tone": got.get("tone")})
    opening = ("Thank you for returning the agreement. We compared it with the version we sent. "
               + ("A few points need to change before we can countersign:" if tone == "firm" else "Here is where we stand on each point:"))
    out["text"] = "\n\n".join([opening] + [f"Clause {p['clause']} ({p['title']}): {p['text']}" for p in out["paragraphs"]] + [closing])
    return out
