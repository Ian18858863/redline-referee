"""One door to the Clocked API: record, replay, budget, audit.

Modes
  live    call the API (needs CLOCKED_API_KEY); with record=True every exchange is saved
  replay  answer only from saved exchanges; a request that was never recorded is an error
  auto    replay when a recording exists, otherwise live

Requests are identified by a hash of their body (PDF bytes are hashed, not stored
twice), so a replay is the exact answer the API gave to the exact same request.
Retries reuse one Idempotency-Key (the SDK does this), so a retried request is
billed once.

Budget   The credit cap is enforced by reservation: before a call leaves, its estimated
         cost is reserved under the lock, and settled to the real figure when the call
         returns. Calls in flight therefore count against the cap, and the cap can be
         passed by at most one call (the one that was allowed while there was still room).
Cache    Within one Gateway an identical request is sent once. A repeat, also one made
         while the first is still in flight, gets the same answer and is logged in the
         audit as cached with zero credits.
Ids      `trace` is Cloudflare's cf-ray. `request_id` is set only when the API returns a
         real request id header. Recordings made before the two were told apart hold the
         cf-ray under `request_id`; they still replay, and the audit then shows it as both.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ENDPOINTS = {
    "compare": "/clocked/compare", "grounding": "/clocked/grounding", "rerank": "/clocked/rerank",
    "decide": "/clocked/decide", "extract": "/clocked/extract", "injection": "/clocked/injection", "fill": "/clocked/fill",
}
PRODUCT_NAME = {"compare": "Compare", "grounding": "Verify", "rerank": "Rerank", "decide": "Decide",
                "extract": "Extract", "injection": "Guard", "fill": "Fill"}
_BY_PATH = {v: k for k, v in ENDPOINTS.items()}
# Credits reserved for a call before its real cost is known: the highest cost seen per endpoint in recordings/,
# rounded up. Override per endpoint with CLOCKED_ESTIMATE_<ENDPOINT> (for example CLOCKED_ESTIMATE_COMPARE=60)
# or with Gateway(estimates={...}). A Gateway also raises its estimate to the highest cost it has seen itself.
DEFAULT_ESTIMATES = {"compare": 50, "grounding": 20, "rerank": 5, "decide": 3, "extract": 12, "injection": 25, "fill": 40}
_CF_RAY = re.compile(r"[0-9a-f]{12,20}-[A-Z]{3}")
_SEQ_LOCK = threading.Lock()            # one writer at a time allocates a recording's sequence number


def load_env_file() -> None:
    """Read CLOCKED_* settings from a git-ignored .env file next to the project, if the shell did not set them."""
    env = Path(__file__).resolve().parents[1] / ".env"
    if not env.is_file():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[7:]
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            if k.strip().startswith("CLOCKED_"):
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env_file()


class StepError(Exception):
    def __init__(self, endpoint: str, label: str, message: str, status: int | None = None, kind: str = "error"):
        super().__init__(message)
        self.endpoint, self.label, self.message, self.status, self.kind = endpoint, label, message, status, kind

    def as_dict(self) -> dict:
        return {"endpoint": PRODUCT_NAME.get(self.endpoint, self.endpoint), "step": self.label,
                "message": self.message, "status": self.status, "kind": self.kind}


def pdf_b64(path: str | Path) -> str:
    return base64.b64encode(Path(path).read_bytes()).decode("ascii")


def _strip(obj: Any) -> Any:
    """Replace PDF payloads by their hash so request bodies are small and comparable."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k == "pdf" and isinstance(v, str) and len(v) > 200:
                out[k] = {"sha256": hashlib.sha256(base64.b64decode(v)).hexdigest()}
            else:
                out[k] = _strip(v)
        return out
    if isinstance(obj, list):
        return [_strip(v) for v in obj]
    return obj


def request_hash(endpoint: str, request: dict) -> str:
    canon = json.dumps({"endpoint": endpoint, "request": _strip(request)}, sort_keys=True, ensure_ascii=False,
                       separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


class Gateway:
    def __init__(self, mode: str = "auto", recordings: str | Path | None = None, *, record: bool = False,
                 credit_cap: int | None = None, time_budget_s: float = 150.0, client: Any = None,
                 estimates: dict[str, int] | None = None):
        if mode not in ("live", "replay", "auto"):
            raise ValueError("mode must be live, replay or auto")
        self.mode, self.record = mode, record
        self.dir = Path(recordings) if recordings else None
        self.credit_cap = credit_cap if credit_cap is not None else int(os.environ.get("CLOCKED_CREDIT_CAP", "1500"))
        self.deadline = time.monotonic() + time_budget_s
        self.credits = 0                        # settled: what returned calls actually cost
        self.reserved = 0                       # estimates of the calls in flight
        self.unsettled = 0                      # estimates of calls that failed without an answer (may have been billed)
        self.estimates = _estimates(estimates)
        self.sent = 0                           # live calls that left for the API, answered or not
        self.audit: list[dict] = []
        self._client = client
        self._lock = threading.Lock()
        self._seen: dict[str, int] = {}         # highest real cost per endpoint, so far
        self._memo: dict[str, tuple[dict, dict]] = {}       # request hash -> (response, meta) of a live call
        self._inflight: dict[str, threading.Event] = {}
        self._index: dict[str, Path] = {}
        self.used: set[Path] = set()            # recordings this run actually read or wrote
        self.rejected: list[Path] = []          # recordings whose stored request does not match their hash
        if self.dir and self.dir.is_dir():
            for f in sorted(self.dir.glob("*.json")):
                try:
                    rec = json.loads(f.read_text(encoding="utf-8"))
                    h = rec["request_hash"]
                    if "request" in rec and request_hash(_BY_PATH.get(rec.get("endpoint"), rec.get("endpoint")),
                                                         rec["request"]) != h:
                        self.rejected.append(f)     # edited or damaged: never answer with it
                        continue
                    self._index[h] = f
                except Exception:
                    continue

    # -- public ---------------------------------------------------------------
    @property
    def has_key(self) -> bool:
        return bool(os.environ.get("CLOCKED_API_KEY")) or self._client is not None

    def call(self, endpoint: str, request: dict, label: str) -> dict:
        if endpoint not in ENDPOINTS:
            raise ValueError(f"unknown endpoint {endpoint}")
        h = request_hash(endpoint, request)
        if self.mode != "replay":
            hit = self._cached(h)
            if hit is not None:
                return self._answer_from_cache(endpoint, label, hit)
        saved = self._index.get(h)
        if saved is not None and self.mode in ("replay", "auto"):
            rec = json.loads(saved.read_text(encoding="utf-8"))
            self.used.add(saved)
            self._log(endpoint, label, rec["meta"], replayed=True)
            return rec["response"]
        if self.mode == "replay":
            raise StepError(endpoint, label, "This request was never recorded, so there is nothing to replay. "
                            "Run it live with a Clocked API key.", kind="replay_miss")
        if not self.has_key:
            raise StepError(endpoint, label, "No Clocked API key is configured (CLOCKED_API_KEY).", kind="no_key")

        # One identical request in flight at a time: a second caller waits for the first one's answer.
        while True:
            with self._lock:
                hit = self._memo.get(h)
                pending = None if hit is not None else self._inflight.get(h)
                if hit is None and pending is None:
                    mine = self._inflight[h] = threading.Event()
            if hit is not None:
                return self._answer_from_cache(endpoint, label, hit)
            if pending is None:
                break
            left = self.deadline - time.monotonic()
            if left <= 0:
                raise StepError(endpoint, label, "The review ran out of its time budget before this step.", kind="timeout")
            pending.wait(left)                  # then look again: cached now, or the first call failed and we try
        try:
            return self._live(endpoint, request, label, h)
        finally:
            with self._lock:
                self._inflight.pop(h, None)
            mine.set()

    def _live(self, endpoint: str, request: dict, label: str, h: str) -> dict:
        remaining = self.deadline - time.monotonic()
        if remaining <= 1:
            raise StepError(endpoint, label, "The review ran out of its time budget before this step.", kind="timeout")
        with self._lock:                        # check and reserve in one step, so calls in flight count
            if self.credits + self.reserved + self.unsettled >= self.credit_cap:
                raise StepError(endpoint, label, f"Credit cap of {self.credit_cap} reached; step not run.", kind="budget")
            estimate = max(self.estimates.get(endpoint, 0), self._seen.get(endpoint, 0))
            self.reserved += estimate
            self.sent += 1
        key = str(uuid.uuid4())
        started = time.monotonic()
        try:
            result = getattr(self._sdk(), endpoint)(request, idempotency_key=key, timeout=min(90.0, remaining))
        except Exception as exc:  # typed SDK errors carry status and the API's message
            status = getattr(exc, "status", None)
            with self._lock:
                self.reserved -= estimate
                if not status:                  # no answer at all (timeout, connection): it may have been billed
                    self.unsettled += estimate
            kind = "rate_limit" if status == 429 else "timeout" if "timeout" in type(exc).__name__.lower() else "error"
            raise StepError(endpoint, label, f"{type(exc).__name__}: {exc}"[:400], status, kind) from exc
        try:
            headers = getattr(result, "headers", {}) or {}
            meta = {
                "status": getattr(result, "status", 200), "credits": getattr(result, "credits", None),
                "model_ms": getattr(result, "model_ms", None), "cost_usd": getattr(result, "cost_usd", None),
                "attempts": getattr(result, "attempts", 1), "idempotency_key": getattr(result, "idempotency_key", key),
                "request_id": _request_id(headers), "trace": _trace(headers),
                "wall_ms": round((time.monotonic() - started) * 1000),
                "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            response = json.loads(json.dumps(dict(result)))
            spent = int(meta["credits"] or 0)
        except Exception as exc:
            with self._lock:                    # an answer we cannot read was still paid for: keep the estimate
                self.reserved -= estimate
                self.unsettled += estimate
            raise StepError(endpoint, label, "The API answered in a form that could not be read.", kind="error") from exc
        with self._lock:                        # settle the reservation to the real figure
            self.reserved -= estimate
            self.credits += spent
            self._seen[endpoint] = max(self._seen.get(endpoint, 0), spent)
            self._memo[h] = (response, meta)
        if self.record and self.dir:
            path = self._save(endpoint, label, h, request, meta, response)
            self._index[h] = path
            self.used.add(path)
        self._log(endpoint, label, meta, replayed=False)
        return json.loads(json.dumps(response))      # the caller's copy; the cached one stays as received

    def prune(self) -> int:
        """Delete recordings in this folder that the run did not use (left over from earlier request shapes)."""
        stale = [f for f in (self.dir.glob("*.json") if self.dir and self.dir.is_dir() else []) if f not in self.used]
        for f in stale:
            f.unlink()
        return len(stale)

    # -- internals ------------------------------------------------------------
    def _sdk(self):
        if self._client is None:
            from clocked_sdk import Clocked
            self._client = Clocked()
        return self._client

    def _cached(self, h: str) -> tuple[dict, dict] | None:
        with self._lock:
            return self._memo.get(h)

    def _answer_from_cache(self, endpoint: str, label: str, hit: tuple[dict, dict]) -> dict:
        response, meta = hit
        self._log(endpoint, label, {**meta, "credits": 0, "model_ms": 0, "attempts": 0}, replayed=False, cached=True)
        return json.loads(json.dumps(response))

    def _save(self, endpoint: str, label: str, h: str, request: dict, meta: dict, response: dict) -> Path:
        """Write one exchange. The sequence number is taken and the file put in place under one lock, so two
        calls that finish together never get the same number; the file appears whole or not at all."""
        body = json.dumps({"endpoint": ENDPOINTS[endpoint], "label": label, "request_hash": h,
                           "request": _strip(request), "meta": meta, "response": response},
                          indent=1, ensure_ascii=False) + "\n"
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", label)[:40]
        with _SEQ_LOCK:
            self.dir.mkdir(parents=True, exist_ok=True)
            path = self.dir / f"{_next_seq(self.dir):02d}-{endpoint}-{safe}-{h[:8]}.json"
            tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
            tmp.write_text(body, encoding="utf-8")
            os.replace(tmp, path)
        return path

    def _log(self, endpoint: str, label: str, meta: dict, replayed: bool, cached: bool = False) -> None:
        request_id, trace = audit_ids(meta)
        with self._lock:
            self.audit.append({"endpoint": PRODUCT_NAME[endpoint], "path": ENDPOINTS[endpoint], "step": label,
                               "credits": meta.get("credits"), "model_ms": meta.get("model_ms"),
                               "attempts": meta.get("attempts"), "request_id": request_id, "trace": trace,
                               "idempotency_key": meta.get("idempotency_key"), "recorded_at": meta.get("recorded_at"),
                               "replayed": replayed, "cached": cached})


def _estimates(overrides: dict[str, int] | None) -> dict[str, int]:
    out = dict(DEFAULT_ESTIMATES)
    for name in ENDPOINTS:
        raw = os.environ.get(f"CLOCKED_ESTIMATE_{name.upper()}", "").strip()
        if raw.isdigit():
            out[name] = int(raw)
    for name, value in (overrides or {}).items():
        if name not in ENDPOINTS:
            raise ValueError(f"unknown endpoint {name}")
        out[name] = max(0, int(value))
    return out


def _next_seq(folder: Path) -> int:
    """One more than the highest sequence number in the folder (not the file count: pruning leaves gaps)."""
    top = 0
    for f in folder.glob("*.json"):
        m = re.match(r"(\d+)-", f.name)
        if m:
            top = max(top, int(m.group(1)))
    return top + 1


def _request_id(headers: dict) -> str | None:
    """A request id the API itself returned. Cloudflare's cf-ray is not one: see _trace."""
    low = {str(k).lower(): v for k, v in headers.items()}
    for name in ("x-request-id", "request-id"):
        if low.get(name):
            return low[name]
    for k, v in low.items():
        if k.endswith("request-id") and v:
            return v
    return None


def _trace(headers: dict) -> str | None:
    for k, v in headers.items():
        if str(k).lower() == "cf-ray":
            return v or None
    return None


def audit_ids(meta: dict) -> tuple[str | None, str | None]:
    """(request_id, trace) of one exchange, for exchanges recorded before and after the two were told apart.
    Older recordings stored the cf-ray under `request_id` and have no `trace`: the value is kept where it was
    (readers of `request_id` still find it) and also returned as the trace, which is what it is."""
    request_id, trace = meta.get("request_id"), meta.get("trace")
    if "trace" not in meta and isinstance(request_id, str) and _CF_RAY.fullmatch(request_id):
        trace = request_id
    return request_id, trace
