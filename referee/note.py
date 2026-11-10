"""Deterministic cover-note parsing. Only recognisable courtesies and signatures are skipped.

This module has no API or PDF dependencies. Ambiguous prose is kept for Verify;
Guard still reads the entire original note in the review engine.
"""
from __future__ import annotations

import re


def squash(text: str) -> str:
    return " ".join(text.split())


# ---------------------------------------------------------------- cover note
_ABBREV = set("inc ltd co corp llc plc gmbh no nos mr ms mrs dr prof vs e.g i.e approx sec art cl para p.s ps u.s u.k st etc jr sr est".split())
_COMPANY = set("inc ltd co corp llc plc gmbh".split())
_SENT_GAP = re.compile(r"(?<=[.!?])[\"”’')\]]*\s+")
_SALUTE = re.compile(r"^(?:hi|hello|hey|dear|greetings|good (?:morning|afternoon|evening)|to whom it may concern)\b"
                     r"(?:\s+(?:mr|ms|mrs|dr|prof)\.)?"
                     r"(?:(?:\s+[^\s,:;.!?—–-]+){0,4}\s*[,:;!]|(?:\s+[^\s,:;.!?—–-]+){0,2}\s*(?:\.(?=\s|$)|$))\s*", re.I)
_SIGN = (r"(?i:thanks(?: again| so much| very much| a lot)?|thank you(?: very much| again| so much)?|many thanks|"
         r"best(?: regards| wishes)?|kind regards|warm regards|with regards|regards|sincerely(?: yours)?|"
         r"yours (?:sincerely|faithfully|truly)|cheers|respectfully|cordially|all the best)")
_SIGNOFF = re.compile(rf"^{_SIGN}\s*(?:[,.!]\s*)?$|^{_SIGN}\s*,\s*[A-Z][\w.'’-]*(?:\s+[A-Z][\w.'’-]*){{0,3}}$")
_THANKS_THEN = re.compile(r"^(?:thanks|thank you|many thanks)\s*[,!–—-]\s*(?=\S+\s+\S+\s+\S)", re.I)
_COURTESY = re.compile(r"^(?:thanks|thank you|many thanks)(?: again| so much| very much| a lot)?"
                       r"(?: for (?:your )?(?:patience|help|time|assistance|understanding))?[.!]*$", re.I)
_POSTSCRIPT = re.compile(r"^(?:P\.?\s?S\.?|N\.?B\.?)\s*[:.,-]?\s+", re.I)
_SIGNATURE_FACT = re.compile(r"\b(?:is|are|was|were|has|have|will|shall|must|may|can|cannot|"
                             r"unchanged|changed|unlimited|waived|agreed|only|no|not|nothing|terms|"
                             r"payment|liability|fee|fees|contract|agreement)\b|\d|[$€£¥]", re.I)


def _signature_line(line: str) -> bool:
    """Recognisable contact/name lines only; short factual prose is never a signature."""
    if _POSTSCRIPT.match(line) or _SIGNATURE_FACT.search(line):
        return False
    if re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", line):
        return True
    return bool(re.fullmatch(r"[A-Z][\w.'’-]*(?:(?:,\s*|\s+)[A-Z][\w.'’-]*){0,4}", line))
_BULLET = re.compile(r"^(?:[-*•·–—▪◦]|\(?\d{1,2}[.)]|\(?[a-z]\))\s+(?=\S)")
_TO_AN_AI = re.compile(
    r"\b(?:AI|A\.I\.|LLMs?|GPT|ChatGPT|Copilot)\b|"
    r"(?i:\b(?:artificial intelligence|language model|chat ?bot|automated (?:\w+ ){0,2}(?:reviewer|review|system|tool|assistant|agent)|"
    r"review(?:er)? tool)\b)|"
    r"(?i:\b(?:ignore|disregard|override)\b.{0,40}\b(?:instructions?|rules|checks?|review)\b)")


def _note_sentences(text: str) -> list[str]:
    """Sentences of a paragraph. "Inc." before a lower-case word, "No. 5", "J. Smith" and "P.S." do not end one."""
    out, start = [], 0
    for m in _SENT_GAP.finditer(text):
        before = text[start:m.start()]
        last = (before.split() or [""])[-1]
        nxt = text[m.end():m.end() + 1]
        if last.endswith("."):
            w = last[:-1].lower().lstrip("(\"'“‘")
            if (len(w) == 1 and w.isalpha()) or (w in _ABBREV and not (w in _COMPANY and nxt.isupper())):
                continue
        out.append(text[start:m.end()].strip())
        start = m.end()
    out.append(text[start:].strip())
    return [s for s in out if s]


def _pieces(sentence: str) -> list[tuple[str, str]]:
    """A semicolon list as its items: [(text as written, the claim sent)]. A lead-in before a colon
    ("We changed the following:") is put in front of every item it introduces."""
    if ";" not in sentence:
        return [(sentence, sentence)]
    lead, body = "", sentence
    m = re.match(r"^([^;]{3,200}?:)\s+(?=\S)", sentence)
    if m:
        lead, body = m.group(1), sentence[m.end():]
    parts = [p.strip() for p in body.split(";") if p.strip()]
    if len(parts) < 2 or any(len(p.split()) < 2 for p in parts):
        return [(sentence, sentence)]
    out = []
    for p in parts:
        core = re.sub(r"^(?:and|or)\s+", "", p, flags=re.I)
        out.append((p, f"{lead} {core}" if lead else core))
    return out


def split_note(letter: str) -> dict:
    """Every sentence of the cover note, sorted into what happens to it.

    claims        statements that assert something: each is checked by Verify (`sent` holds the text sent,
                  which differs from `claims` only for a list item that needs its lead-in)
    instructions  text addressed to an AI or a review tool: left to Guard, which reads the whole note
    skipped       salutations, sign-offs, signature lines and questions, with the reason

    A paragraph is not dropped because it opens with "Hi", and a sentence does not need a full stop.
    Bullets and semicolon lists are split into one statement each.
    """
    claims: list[str] = []
    sent: list[str] = []
    instructions: list[dict] = []
    skipped: list[dict] = []

    def take(text: str, claim: str):
        text, claim = text.strip(), claim.strip()
        bare = re.sub(r"^(?:P\.?\s?S\.?|N\.?B\.?)\s*[:.,-]?\s*", "", text)
        if not bare:
            return
        if re.search(r"\?[\"”’')\]]*$", bare):
            skipped.append({"text": text, "why": "question"})
        elif _TO_AN_AI.search(bare):
            instructions.append({"text": text, "claim": claim,
                                 "why": "Addressed to an AI or a review tool: an instruction, not a statement about the agreement."})
        elif _COURTESY.fullmatch(bare):
            skipped.append({"text": text, "why": "courtesy"})
        elif len(re.findall(r"\w+", bare)) < 2:
            skipped.append({"text": text, "why": "too short to state anything"})
        else:
            m = _THANKS_THEN.match(text)
            if m:
                text = text[m.end():]
                claim = claim[m.end():] if _THANKS_THEN.match(claim) else claim
            claims.append(text)
            sent.append(claim)

    signed, first = False, True
    every: list[list[str]] = []                # ["text"|"bullet", content], over the whole note
    body = (letter or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    for para in re.split(r"\n[ \t]*\n", body):
        lines = [squash(l) for l in para.split("\n") if l.strip()]
        if not lines:
            continue
        if first:
            first = False
            m = _SALUTE.match(lines[0])
            if m:
                skipped.append({"text": m.group(0).strip(), "why": "salutation"})
                rest = lines[0][m.end():].strip()
                lines = ([rest] if rest else []) + lines[1:]
        blocks: list[list[str]] = []
        in_signature = False
        for line in lines:
            if in_signature and _signature_line(line):
                skipped.append({"text": line, "why": "signature"})
            elif _SIGNOFF.match(line):
                skipped.append({"text": line, "why": "sign-off"})
                signed = in_signature = True
            elif signed and _signature_line(line):
                skipped.append({"text": line, "why": "signature"})
            elif _BULLET.match(line):
                blocks.append(["bullet", _BULLET.sub("", line, count=1)])
            elif blocks and blocks[-1][0] == "bullet" and not re.search(r"[.!?;:][\"”’')\]]*$", blocks[-1][1]):
                blocks[-1][1] += " " + line                    # a wrapped bullet
            elif blocks and blocks[-1][0] == "text":
                blocks[-1][1] += " " + line
            else:
                in_signature = False
                blocks.append(["text", line])
        every += blocks
    lead = ""
    for n, (kind, content) in enumerate(every):
        if kind == "text":
            lead = ""
            sents = _note_sentences(content)
            bullets_next = n + 1 < len(every) and every[n + 1][0] == "bullet"
            if sents and sents[-1].endswith(":") and bullets_next:     # introduces the bullets that follow
                lead = sents.pop()
            for s in sents:
                for text, claim in _pieces(s):
                    take(text, claim)
        else:
            for s in _note_sentences(content):
                for text, claim in _pieces(s):
                    take(text, f"{lead} {claim}" if lead else claim)
    return {"claims": claims, "sent": sent, "instructions": instructions, "skipped": skipped}


def split_claims(letter: str) -> list[str]:
    """The statements of the note that assert something, all of them, as written."""
    return split_note(letter)["claims"]
