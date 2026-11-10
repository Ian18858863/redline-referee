"""Shared durable ledger for this authorized 1,500-credit experiment round.

The gateway serializes new calls and leaves a 150-credit safety margin because
the remote service reports actual billing only after a call returns. Unanswered
calls retain their estimated cost; a restart never resets the ledger.
"""
import json
import threading
from pathlib import Path

from referee.gateway import Gateway, StepError

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "eval/external-ledger.jsonl"
AUTHORIZED = 1500
SEND_LIMIT = 1350


class RoundGateway(Gateway):
    def __init__(self, live=False):
        rows = [json.loads(s) for s in LEDGER.read_text().splitlines()] if LEDGER.exists() else []
        self.prior = sum(r["charged"] + r["uncertain"] for r in rows)
        super().__init__("auto" if live else "replay", ROOT / "recordings/external",
                         record=live, credit_cap=max(0, SEND_LIMIT - self.prior), time_budget_s=3600)
        self.round_lock = threading.Lock()

    def _live(self, endpoint, request, label, h):
        with self.round_lock:
            estimate = max(self.estimates[endpoint], self._seen.get(endpoint, 0))
            if self.credits + self.unsettled + estimate > self.credit_cap:
                raise StepError(endpoint, label, "Round budget reserve reached; no call sent.", kind="budget")
            before, unknown, sent = self.credits, self.unsettled, self.sent
            try:
                return super()._live(endpoint, request, label, h)
            finally:
                if self.sent > sent:
                    row = {"endpoint": endpoint, "label": label, "request_hash": h,
                           "charged": self.credits - before, "uncertain": self.unsettled - unknown,
                           "round_accounted": self.prior + self.credits + self.unsettled}
                    with LEDGER.open("a") as f:
                        f.write(json.dumps(row) + "\n")


def verify_lock():
    import hashlib
    lock = json.loads((ROOT / "data/external/LOCK.json").read_text())
    for path, expected in lock["files"].items():
        assert hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == expected, f"Frozen file changed: {path}"
    return lock
