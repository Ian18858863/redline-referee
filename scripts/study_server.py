"""Local-only anonymous workflow study. No API calls, no publication of answers.

uv run python scripts/study_server.py   -> http://127.0.0.1:8792
Writes raw responses only to git-ignored out/study/. Never deploy this server.
"""
import json
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

app = FastAPI(docs_url=None, redoc_url=None)


@app.get("/")
def index():
    return FileResponse(ROOT / "study/index.html")


@app.get("/api/ready")
def ready():
    p = ROOT / "out/external/commonpaper-changed/review.json"
    if not p.exists():
        return {"ready": False}
    r = json.loads(p.read_text())
    return {"ready": not r.get("errors"), "status": r.get("status")}


@app.get("/app/cases/index.json")
def cases():
    return [{"id": "commonpaper-changed", "title": "Common Paper NDA", "description": "Public-source challenge", "status": "changes"}]


@app.get("/app/api/config")
def config():
    return {"live": False}


@app.post("/api/answer")
async def answer(request: Request):
    if request.headers.get("origin") != "http://127.0.0.1:8792":
        raise HTTPException(403)
    if len(await request.body()) > 16000:
        raise HTTPException(413)
    data = await request.json()
    if data.get("phase") not in ("manual", "assisted") or not isinstance(data.get("findings"), str):
        raise HTTPException(422)
    token = data.get("participant") or uuid.uuid4().hex
    if len(token) != 32 or any(c not in "0123456789abcdef" for c in token):
        raise HTTPException(422)
    out = ROOT / "out/study"; out.mkdir(parents=True, exist_ok=True)
    record = {k: data.get(k) for k in ("phase", "findings", "sign", "reason", "confidence", "elapsed_ms", "preview")}
    record.update(participant=token, received_at=time.time(), protocol="workflow-observation-v1")
    path = out / f"{token}-{data['phase']}.json"
    if path.exists():
        raise HTTPException(409, "This phase has already been submitted.")
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    return {"participant": token, "saved": True}


app.mount("/pdf", StaticFiles(directory=ROOT / "data/external"), name="pdf")
app.mount("/app/cases", StaticFiles(directory=ROOT / "out/external"), name="case-files")
app.mount("/app", StaticFiles(directory=ROOT / "web", html=True), name="workbench")

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8792, log_level="warning")
