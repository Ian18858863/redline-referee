"""Local server: the static UI, the built demo cases, and live reviews of uploaded PDFs.

Run:  uv run uvicorn referee.server:app --port 8787
Live reviews need CLOCKED_API_KEY in the environment. The key stays on the server.
"""
from __future__ import annotations

import os
import threading
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.staticfiles import StaticFiles

from .engine import review
from .gateway import Gateway
from .textdoc import NoTextLayer

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "out" / "runs"
SITE_CASES = ROOT / "docs" / "cases"
MAX_PDF_BYTES = 2_500_000          # Clocked's public limit for one PDF
MAX_NOTE_CHARS = 8_000
PER_IP_PER_HOUR = int(os.environ.get("LIVE_RUNS_PER_HOUR", "12"))

app = FastAPI(title="Redline Referee", docs_url=None, redoc_url=None, openapi_url=None)
_busy = threading.Semaphore(1)
_hits: dict[str, list[float]] = {}


@app.get("/api/config")
def config():
    return {"live": bool(os.environ.get("CLOCKED_API_KEY")), "max_pdf_bytes": MAX_PDF_BYTES,
            "credit_cap": int(os.environ.get("CLOCKED_CREDIT_CAP", "1500"))}


@app.post("/api/review")
async def api_review(request: Request, v1: UploadFile = File(...), v2: UploadFile = File(...), note: str = Form("")):
    if not os.environ.get("CLOCKED_API_KEY"):
        raise HTTPException(503, "No Clocked API key is configured on this server, so live reviews are off.")
    ip = request.client.host if request.client else "?"
    now = time.time()
    recent = [t for t in _hits.get(ip, []) if now - t < 3600]
    if len(recent) >= PER_IP_PER_HOUR:
        raise HTTPException(429, "Too many live reviews from this address in the last hour.")
    _hits[ip] = recent + [now]
    run_id = uuid.uuid4().hex[:12]
    folder = RUNS / run_id
    (folder / "input").mkdir(parents=True, exist_ok=True)
    paths = {}
    for name, up in (("v1", v1), ("v2", v2)):
        data = await up.read()
        if len(data) > MAX_PDF_BYTES:
            raise HTTPException(413, f"{up.filename}: larger than {MAX_PDF_BYTES // 1000} KB.")
        if b"%PDF-" not in data[:1024]:
            raise HTTPException(422, f"{up.filename}: not a PDF file.")
        paths[name] = folder / "input" / f"{name}.pdf"
        paths[name].write_bytes(data)
    if len(note) > MAX_NOTE_CHARS:
        raise HTTPException(422, f"The cover note is longer than {MAX_NOTE_CHARS} characters.")
    if not _busy.acquire(timeout=120):
        raise HTTPException(503, "Another review is running. Try again in a minute.")
    try:
        result = review(paths["v1"], paths["v2"], note, Gateway("live"), folder,
                        case={"id": run_id, "title": f"{v1.filename} / {v2.filename}", "description": "Uploaded for a live review."})
    except NoTextLayer as e:
        raise HTTPException(422, str(e))
    except Exception as e:  # a damaged PDF and the like
        raise HTTPException(422, f"These files could not be read: {type(e).__name__}: {e}"[:300])
    finally:
        _busy.release()
    return {"id": run_id, "base": f"runs/{run_id}/", "status": result["status"]}


RUNS.mkdir(parents=True, exist_ok=True)
app.mount("/runs", StaticFiles(directory=RUNS), name="runs")
if SITE_CASES.is_dir():
    app.mount("/cases", StaticFiles(directory=SITE_CASES), name="cases")
app.mount("/", StaticFiles(directory=ROOT / "web", html=True), name="web")
