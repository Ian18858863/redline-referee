"""One door to the Clocked API: record, replay, budget, audit.

Modes
  live    call the API (needs CLOCKED_API_KEY); with record=True every exchange is saved
  replay  answer only from saved exchanges; a request that was never recorded is an error
  auto    replay when a recording exists, otherwise live

Requests are identified by a hash of their body (PDF bytes are hashed, not stored
twice), so a replay is the exact answer the API gave to the exact same request.
Retries reuse one Idempotency-Key (the SDK does this), so a retried request is
billed once.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ENDPOINTS = {
    "compare": "/clocked/compare", "grounding": "/clocked/grounding", "rerank": "/clocked/rerank",
    "decide": "/clocked/decide", "extract": "/clocked/extract", "injection": "/clocked/injection",
}
PRODUCT_NAME = {"compare": "Compare", "grounding": "Verify", "rerank": "Rerank", "decide": "Decide",
                "extract": "Extract", "injection": "Guard"}


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
                 credit_cap: int | None = None, time_budget_s: float = 150.0, client: Any = None):
        if mode not in ("live", "replay", "auto"):
            raise ValueError("mode must be live, replay or auto")
        self.mode, self.record = mode, record
        self.dir = Path(recordings) if recordings else None
        self.credit_cap = credit_cap if credit_cap is not None else int(os.environ.get("CLOCKED_CREDIT_CAP", "1500"))
        self.deadline = time.monotonic() + time_budget_s
        self.credits = 0
        self.audit: list[dict] = []
        self._client = client
        self._lock = threading.Lock()
        self._index: dict[str, Path] = {}
        if self.dir and self.dir.is_dir():
            for f in sorted(self.dir.glob("*.json")):
                try:
                    self._index[json.loads(f.read_text(encoding="utf-8"))["request_hash"]] = f
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
        saved = self._index.get(h)
        if saved is not None and self.mode in ("replay", "auto"):
            rec = json.loads(saved.read_text(encoding="utf-8"))
            self._log(endpoint, label, rec["meta"], replayed=True)
            return rec["response"]
        if self.mode == "replay":
            raise StepError(endpoint, label, "This request was never recorded, so there is nothing to replay. "
                            "Run it live with a Clocked API key.", kind="replay_miss")
        if not self.has_key:
            raise StepError(endpoint, label, "No Clocked API key is configured (CLOCKED_API_KEY).", kind="no_key")
        with self._lock:
            if self.credits >= self.credit_cap:
                raise StepError(endpoint, label, f"Credit cap of {self.credit_cap} reached; step not run.", kind="budget")
        remaining = self.deadline - time.monotonic()
        if remaining <= 1:
            raise StepError(endpoint, label, "The review ran out of its time budget before this step.", kind="timeout")
        key = str(uuid.uuid4())
        started = time.monotonic()
        try:
            result = getattr(self._sdk(), endpoint)(request, idempotency_key=key, timeout=min(90.0, remaining))
        except Exception as exc:  # typed SDK errors carry status and the API's message
            status = getattr(exc, "status", None)
            kind = "rate_limit" if status == 429 else "timeout" if "timeout" in type(exc).__name__.lower() else "error"
            raise StepError(endpoint, label, f"{type(exc).__name__}: {exc}"[:400], status, kind) from exc
        meta = {
            "status": getattr(result, "status", 200), "credits": getattr(result, "credits", None),
            "model_ms": getattr(result, "model_ms", None), "cost_usd": getattr(result, "cost_usd", None),
            "attempts": getattr(result, "attempts", 1), "idempotency_key": getattr(result, "idempotency_key", key),
            "request_id": _request_id(getattr(result, "headers", {}) or {}),
            "wall_ms": round((time.monotonic() - started) * 1000),
            "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        response = json.loads(json.dumps(dict(result)))
        with self._lock:
            self.credits += int(meta["credits"] or 0)
        if self.record and self.dir:
            self.dir.mkdir(parents=True, exist_ok=True)
            n = len(list(self.dir.glob("*.json"))) + 1
            path = self.dir / f"{n:02d}-{endpoint}-{label.replace(' ', '_').replace('/', '_')[:40]}-{h[:8]}.json"
            path.write_text(json.dumps({"endpoint": ENDPOINTS[endpoint], "label": label, "request_hash": h,
                                        "request": _strip(request), "meta": meta, "response": response},
                                       indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
            self._index[h] = path
        self._log(endpoint, label, meta, replayed=False)
        return response

    # -- internals ------------------------------------------------------------
    def _sdk(self):
        if self._client is None:
            from clocked_sdk import Clocked
            self._client = Clocked()
        return self._client

    def _log(self, endpoint: str, label: str, meta: dict, replayed: bool) -> None:
        with self._lock:
            self.audit.append({"endpoint": PRODUCT_NAME[endpoint], "path": ENDPOINTS[endpoint], "step": label,
                               "credits": meta.get("credits"), "model_ms": meta.get("model_ms"),
                               "attempts": meta.get("attempts"), "request_id": meta.get("request_id"),
                               "idempotency_key": meta.get("idempotency_key"), "recorded_at": meta.get("recorded_at"),
                               "replayed": replayed})


def _request_id(headers: dict) -> str | None:
    for k, v in headers.items():
        if "request-id" in k.lower() or k.lower() in ("x-request-id", "cf-ray"):
            return v
    return None
