"""Local server: the static UI, the built demo cases, and live reviews of uploaded PDFs.

Run:  uv run uvicorn referee.server:app --port 8787
Live reviews need CLOCKED_API_KEY in the environment. The key stays on the server.

What a small public demo needs, and where it is done here
  one review at a time      a review runs in a worker thread, never on the event loop; a second upload waits
                            REVIEW_WAIT_SECONDS (20) and is then told to retry. Pages and /api/config stay fast.
  limits                    LIVE_RUNS_PER_HOUR per address (12) and LIVE_RUNS_PER_DAY for everyone (100). Only a
                            review that really started counts; a rejected upload costs nothing.
  uploads are not served    the two uploaded files live outside the folder mounted at /runs and are deleted when
                            the review ends. /runs serves only <run id>/<file>.{json,png,pdf}.
  runs expire               RUN_TTL_HOURS (24; 0 keeps them) and RUN_KEEP_MAX (40), swept when requests come in.
  errors                    fixed sentences for the client; the detail goes to the server log.
Set TRUST_FORWARDED_FOR=1 behind one reverse proxy or tunnel, so the per-address limit sees the visitor's address.
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import shutil
import threading
import time
from pathlib import Path

import pymupdf
from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import MutableHeaders

from .engine import review
from .gateway import Gateway
from .textdoc import NoTextLayer

log = logging.getLogger("referee.server")

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "out" / "runs"            # served at /runs: what a review wrote
UPLOADS = ROOT / "out" / "uploads"      # never served: the two files as uploaded, for the length of the review
SITE_CASES = ROOT / "docs" / "cases"
MAX_PDF_BYTES = 2_500_000          # Clocked's public limit for one PDF
MAX_NOTE_CHARS = 8_000
MAX_BODY_BYTES = 2 * MAX_PDF_BYTES + 4 * MAX_NOTE_CHARS + 16_384    # two PDFs, the note, the multipart framing
MAX_WAITING = 3                    # uploads allowed to wait for the running review; the next is refused at once

RUN_ID = re.compile(r"[0-9a-f]{12,32}")
RUN_FILE = re.compile(r"[0-9a-f]{12,32}/[A-Za-z0-9][A-Za-z0-9._-]*\.(?:json|png|pdf)")
LABEL = {"v1": "The version we sent", "v2": "The copy they returned"}
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}

MSG_NO_KEY = "No Clocked API key is configured on this server, so live reviews are off."
MSG_BUSY = "Another review is running. Try again in a minute."
MSG_BODY_TOO_LARGE = f"The upload is larger than this server accepts. One PDF can be at most {MAX_PDF_BYTES // 1000:,} KB."
MSG_MISSING = "Choose both PDFs: the version we sent and the copy they returned."
MSG_NOTE_TOO_LONG = f"The cover note is longer than {MAX_NOTE_CHARS:,} characters. Shorten it and try again."
MSG_UNREADABLE = ("These files could not be read as text PDFs. Export them again from the source document "
                  "and try again.")
MSG_NOT_FOUND = "Not found."


def _env_number(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def runs_per_hour() -> int:
    return max(0, int(_env_number("LIVE_RUNS_PER_HOUR", 12)))


def runs_per_day() -> int:
    return max(0, int(_env_number("LIVE_RUNS_PER_DAY", 100)))


def run_ttl_seconds() -> float:
    return _env_number("RUN_TTL_HOURS", 24) * 3600


class Limiter:
    """Sliding windows over the reviews that started: one hour per address, one day for everyone."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_addr: dict[str, list[float]] = {}
        self._all: list[float] = []

    def _prune(self, now: float) -> None:
        self._all = [t for t in self._all if now - t < 86400]
        self._by_addr = {a: kept for a, ts in self._by_addr.items() if (kept := [t for t in ts if now - t < 3600])}

    def _refuse(self, addr: str, now: float) -> None:
        per_hour, per_day = runs_per_hour(), runs_per_day()
        mine = self._by_addr.get(addr, [])
        if len(mine) >= per_hour:
            wait = 3600 - (now - mine[0]) if mine else 3600
            minutes = max(1, round(wait / 60))
            raise HTTPException(429, f"This address has reached the limit of {per_hour} live reviews per hour. "
                                     f"Try again in about {minutes} {'minute' if minutes == 1 else 'minutes'}. "
                                     "The recorded cases stay available.",
                                headers={"Retry-After": str(int(max(wait, 60)))})
        if len(self._all) >= per_day:
            wait = 86400 - (now - self._all[0]) if self._all else 86400
            raise HTTPException(429, f"This demo has reached its limit of {per_day} live reviews per day. "
                                     "The recorded cases stay available. Try a live review again later.",
                                headers={"Retry-After": str(int(max(wait, 60)))})

    def check(self, addr: str) -> None:
        """Refuse early, without counting anything."""
        with self._lock:
            now = time.time()
            self._prune(now)
            self._refuse(addr, now)

    def start(self, addr: str) -> float:
        """Count one review that is about to start; returns the stamp to hand back to refund()."""
        with self._lock:
            now = time.time()
            self._prune(now)
            self._refuse(addr, now)
            self._by_addr.setdefault(addr, []).append(now)
            self._all.append(now)
            return now

    def refund(self, addr: str, stamp: float) -> None:
        """The review stopped before anything was sent to the API: give the allowance back."""
        with self._lock:
            for bucket in (self._by_addr.get(addr, []), self._all):
                if stamp in bucket:
                    bucket.remove(stamp)

    def counts(self, addr: str) -> tuple[int, int]:
        with self._lock:
            self._prune(time.time())
            return len(self._by_addr.get(addr, [])), len(self._all)


class RunFiles(StaticFiles):
    """Serves exactly <run id>/<file>.{json,png,pdf} of a run that has not expired. Nothing nested (so no input
    folder of an older layout), no dot files, no listing, no symlinks out of the folder."""

    def get_path(self, scope) -> str:
        path = super().get_path(scope).replace(os.sep, "/")
        if not RUN_FILE.fullmatch(path):
            raise HTTPException(404, MSG_NOT_FOUND)
        run_id, name = path.split("/")
        if name in ("v1.pdf", "v2.pdf") and os.environ.get("SERVE_SOURCE_PDF", "1") == "0":
            raise HTTPException(404, MSG_NOT_FOUND)
        ttl = run_ttl_seconds()
        try:
            if ttl > 0 and time.time() - (Path(str(self.directory)) / run_id).stat().st_mtime > ttl:
                raise HTTPException(404, MSG_NOT_FOUND)
        except OSError:
            raise HTTPException(404, MSG_NOT_FOUND) from None
        return path


class Shield:
    """ASGI wrapper: security headers on every response, no traversal in any path, and a cap on the size of an
    upload that is applied before and while the body is read (not after it is in memory)."""

    def __init__(self, app, max_body: int = MAX_BODY_BYTES) -> None:
        self.app, self.max_body = app, max_body

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for k, v in SECURITY_HEADERS.items():
                    headers[k] = v
                if path.startswith(("/api/", "/runs/")):
                    headers["Cache-Control"] = "no-store"
            await send(message)

        if "\\" in path or "\x00" in path or ".." in path.split("/"):
            return await JSONResponse({"detail": MSG_NOT_FOUND}, 404)(scope, receive, send_with_headers)
        if scope["method"] == "POST" and path == "/api/review":
            declared = dict(scope["headers"]).get(b"content-length", b"").decode("latin-1").strip()
            if declared.isdigit() and int(declared) > self.max_body:
                return await JSONResponse({"detail": MSG_BODY_TOO_LARGE}, 413)(scope, receive, send_with_headers)
            seen = 0

            async def counted():          # a body sent in chunks has no declared length: count it as it arrives
                nonlocal seen
                message = await receive()
                if message["type"] == "http.request":
                    seen += len(message.get("body", b""))
                    if seen > self.max_body:
                        raise HTTPException(413, MSG_BODY_TOO_LARGE)
                return message
            return await self.app(scope, counted, send_with_headers)
        await self.app(scope, receive, send_with_headers)


def _check_bytes(key: str, data: bytes) -> None:
    """The checks that need no PDF library. They run before anything is counted or stored."""
    if not data:
        raise HTTPException(422, f"{LABEL[key]} is an empty file. Choose the PDF again.")
    if len(data) > MAX_PDF_BYTES:
        raise HTTPException(413, f"{LABEL[key]} is larger than {MAX_PDF_BYTES // 1000:,} KB, "
                                 "the most one PDF can be for a live review.")
    if b"%PDF-" not in data[:1024]:
        raise HTTPException(422, f"{LABEL[key]} is not a PDF file. Both files must be PDFs.")


def _check_pdf(key: str, data: bytes) -> None:
    """Open the file once. Called with the review slot held: MuPDF is used by one thread at a time."""
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception:
        log.info("upload %s rejected: not openable", key, exc_info=True)
        raise HTTPException(422, f"{LABEL[key]} could not be opened as a PDF. The file may be damaged. "
                                 "Export it again and try again.") from None
    try:
        if doc.needs_pass:
            raise HTTPException(422, f"{LABEL[key]} is password-protected. Remove the password and upload it again.")
        if doc.page_count < 1:
            raise HTTPException(422, f"{LABEL[key]} has no pages.")
    finally:
        doc.close()


def _display_name(up: UploadFile, fallback: str) -> str:
    name = re.sub(r"[\x00-\x1f\x7f]", "", (up.filename or "").replace("\\", "/").rsplit("/", 1)[-1]).strip()
    return name[:80] or fallback


def _client_addr(request: Request) -> str:
    if os.environ.get("TRUST_FORWARDED_FOR") == "1":      # the last entry is the one our own proxy wrote
        last = request.headers.get("x-forwarded-for", "").split(",")[-1].strip()
        if last:
            return last[:64]
    return request.client.host if request.client else "unknown"


def _stamp_elapsed(path: Path, elapsed_ms: int) -> None:
    """Write the wall time of the review into review.json, under audit.elapsed_ms. The file is replaced whole."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data.get("audit"), dict):
        data["audit"] = {}
    data["audit"]["elapsed_ms"] = elapsed_ms
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def create_app(runs_dir: str | Path | None = None, uploads_dir: str | Path | None = None,
               web_dir: str | Path | None = None, cases_dir: str | Path | None = None) -> FastAPI:
    runs = Path(runs_dir) if runs_dir else RUNS
    uploads = Path(uploads_dir) if uploads_dir else UPLOADS
    web = Path(web_dir) if web_dir else ROOT / "web"
    cases = Path(cases_dir) if cases_dir else SITE_CASES
    runs.mkdir(parents=True, exist_ok=True)
    uploads.mkdir(parents=True, exist_ok=True)

    app = FastAPI(title="Redline Referee", docs_url=None, redoc_url=None, openapi_url=None)
    busy = threading.Semaphore(1)
    limiter = Limiter()
    state_lock = threading.Lock()
    state = {"waiting": 0, "swept": 0.0}
    active: set[str] = set()                  # runs being written right now: the sweep leaves them alone
    app.state.limiter, app.state.runs, app.state.uploads, app.state.active = limiter, runs, uploads, active

    def sweep(force: bool = True, protect: tuple[str, ...] = ()) -> int:
        """Delete expired runs, the oldest runs beyond RUN_KEEP_MAX, and uploads left behind by a crash."""
        now = time.time()
        with state_lock:
            if not force and now - state["swept"] < 60:
                return 0
            state["swept"] = now
            protected = set(active) | set(protect)
        ttl, keep = run_ttl_seconds(), max(1, int(_env_number("RUN_KEEP_MAX", 40)))
        removed = 0

        def folders(base: Path) -> list[tuple[float, Path]]:
            found = []
            try:
                for p in base.iterdir():
                    if RUN_ID.fullmatch(p.name) and p.is_dir() and not p.is_symlink():
                        found.append((p.stat().st_mtime, p))
            except OSError:
                log.warning("could not list %s", base.name, exc_info=True)
            return sorted(found, reverse=True)

        for i, (mtime, p) in enumerate(folders(runs)):       # newest first; a protected run keeps its place in the count
            if p.name not in protected and ((ttl > 0 and now - mtime > ttl) or i >= keep):
                shutil.rmtree(p, ignore_errors=True)
                removed += 1
        for mtime, p in folders(uploads):     # normally deleted when the review ends
            if p.name not in protected and now - mtime > 3600:
                shutil.rmtree(p, ignore_errors=True)
                removed += 1
        if removed:
            log.info("swept %d expired run folder(s)", removed)
        return removed

    app.state.sweep = sweep

    def take_slot() -> None:
        wait = max(0.0, _env_number("REVIEW_WAIT_SECONDS", 20))
        with state_lock:
            if state["waiting"] >= MAX_WAITING:
                raise HTTPException(503, MSG_BUSY, headers={"Retry-After": "60"})
            state["waiting"] += 1
        try:
            got = busy.acquire(timeout=wait) if wait > 0 else busy.acquire(blocking=False)
        finally:
            with state_lock:
                state["waiting"] -= 1
        if not got:
            raise HTTPException(503, MSG_BUSY, headers={"Retry-After": "60"})

    @app.exception_handler(RequestValidationError)
    async def missing_fields(request: Request, exc: RequestValidationError):
        return JSONResponse({"detail": MSG_MISSING}, 422)

    @app.get("/api/config")
    async def config():
        await run_in_threadpool(sweep, False)
        return {"live": bool(os.environ.get("CLOCKED_API_KEY")), "max_pdf_bytes": MAX_PDF_BYTES,
                "max_note_chars": MAX_NOTE_CHARS, "credit_cap": int(_env_number("CLOCKED_CREDIT_CAP", 1500)),
                "runs_per_hour": runs_per_hour(), "run_ttl_hours": _env_number("RUN_TTL_HOURS", 24)}

    @app.post("/api/review")
    def api_review(request: Request, v1: UploadFile = File(...), v2: UploadFile = File(...), note: str = Form("")):
        # A plain function: FastAPI runs it in a worker thread, so the event loop keeps serving everyone else.
        if not os.environ.get("CLOCKED_API_KEY"):
            raise HTTPException(503, MSG_NO_KEY)
        sweep()
        data = {}
        for key, up in (("v1", v1), ("v2", v2)):
            data[key] = up.file.read(MAX_PDF_BYTES + 1)
            _check_bytes(key, data[key])
        if len(note) > MAX_NOTE_CHARS:
            raise HTTPException(422, MSG_NOTE_TOO_LONG)
        addr = _client_addr(request)
        limiter.check(addr)                    # refuse early; nothing is counted yet
        take_slot()
        try:
            for key in ("v1", "v2"):
                _check_pdf(key, data[key])
            stamp = limiter.start(addr)        # from here on it is a review that started
            run_id = secrets.token_hex(16)
            folder, inputs = runs / run_id, uploads / run_id
            with state_lock:
                active.add(run_id)
            gw, reviewed = None, False
            try:
                inputs.mkdir(parents=True)
                for key in ("v1", "v2"):
                    (inputs / f"{key}.pdf").write_bytes(data[key])
                title = f"{_display_name(v1, 'v1.pdf')} / {_display_name(v2, 'v2.pdf')}"
                gw = Gateway("live")           # its time budget starts now, with the slot already held
                started = time.monotonic()
                result = review(inputs / "v1.pdf", inputs / "v2.pdf", note, gw, folder,
                                case={"id": run_id, "title": title, "description": "Uploaded for a live review."})
                reviewed = True
                elapsed_ms = round((time.monotonic() - started) * 1000)
                _stamp_elapsed(folder / "review.json", elapsed_ms)
                status = result["status"]
            except Exception as exc:
                shutil.rmtree(folder, ignore_errors=True)
                asked_api = reviewed or (gw is not None and bool(getattr(gw, "sent", 0) or gw.audit))
                if not asked_api:              # stopped while reading the files: it cost nothing, so count nothing
                    limiter.refund(addr, stamp)
                if isinstance(exc, NoTextLayer):
                    which = next((LABEL[k] for k in ("v1", "v2") if str(exc).startswith(f"{k}.pdf")), "One of the files")
                    log.info("run %s: no text layer: %s", run_id, exc)
                    raise HTTPException(422, f"{which} has no usable text layer. Scanned copies cannot be quoted "
                                             "character by character, so they are not reviewed. "
                                             "Upload a PDF with selectable text.") from None
                log.exception("run %s failed (%s)", run_id, "during the review" if asked_api else "while reading the files")
                if asked_api:
                    raise HTTPException(500, "The review could not be completed because of an error on this server, "
                                             f"not because of your files. Reference: {run_id[:8]}.") from None
                raise HTTPException(422, MSG_UNREADABLE) from None
            finally:
                shutil.rmtree(inputs, ignore_errors=True)
                with state_lock:
                    active.discard(run_id)
        finally:
            busy.release()
        sweep(protect=(run_id,))               # with the new run in place, so RUN_KEEP_MAX holds
        return {"id": run_id, "base": f"runs/{run_id}/", "status": status, "elapsed_ms": elapsed_ms}

    reply_hits: dict[str, list[float]] = {}

    @app.post("/api/reply")
    def api_reply(request: Request, payload: dict = Body(...)):
        """Draft the reply to the customer for the decisions the reviewer took (Fill), checked by Verify."""
        if not os.environ.get("CLOCKED_API_KEY"):
            raise HTTPException(503, MSG_NO_KEY)
        base, decisions = str(payload.get("base") or ""), payload.get("decisions")
        m = re.fullmatch(r"(runs|cases)/([A-Za-z0-9][A-Za-z0-9_-]{0,63})/?", base)
        if not m or not isinstance(decisions, dict) or len(decisions) > 200:
            raise HTTPException(422, "Send the review's base path and the decisions taken.")
        if any(not isinstance(k, str) or v not in ("accept", "counter", "escalate") for k, v in decisions.items()):
            raise HTTPException(422, "A decision is one of accept, counter or escalate.")
        folder = (runs if m.group(1) == "runs" else cases) / m.group(2)
        source = folder / "review.json"
        if not source.is_file():
            raise HTTPException(404, MSG_NOT_FOUND)
        from .reply import ReplyNotReady, validate_reply
        saved_review = json.loads(source.read_text(encoding="utf-8"))
        try:
            validate_reply(saved_review, decisions)
        except ReplyNotReady as exc:
            raise HTTPException(422, str(exc)) from None
        addr, now = _client_addr(request), time.time()
        with state_lock:
            recent = [t for t in reply_hits.get(addr, []) if now - t < 3600]
            if len(recent) >= max(1, int(_env_number("LIVE_REPLIES_PER_HOUR", 30))):
                raise HTTPException(429, "Too many reply drafts from this address in the last hour. Try again later.")
            reply_hits[addr] = recent + [now]
        try:
            from .reply import draft_reply
            out = draft_reply(saved_review, decisions,
                              Gateway("live", credit_cap=int(_env_number("CLOCKED_REPLY_CREDIT_CAP", 120))))
        except Exception:
            log.exception("reply for %s failed", base)
            raise HTTPException(500, "The reply could not be drafted because of an error on this server.") from None
        out.pop("brief", None)
        return out

    app.mount("/runs", RunFiles(directory=runs), name="runs")
    if cases.is_dir():
        app.mount("/cases", StaticFiles(directory=cases), name="cases")
    app.mount("/", StaticFiles(directory=web, html=True), name="web")
    app.add_middleware(Shield)
    return app


app = create_app()
