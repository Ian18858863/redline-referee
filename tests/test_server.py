"""The server around a review: one at a time and off the event loop, limits, errors, storage, headers.

The review itself is replaced by a stub that writes a minimal review.json, the key is a dummy and the real
SDK client fails on construction, so nothing here can reach the network. Runs and uploads go to tmp_path.
"""
import asyncio
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pymupdf
import pytest
from fastapi.testclient import TestClient

from referee import server
from referee.textdoc import NoTextLayer


def make_pdf(text="Master Services Agreement. Fees are due within thirty (30) days.", **save) -> bytes:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), text)
    data = doc.tobytes(**save)
    doc.close()
    return data


PDF = make_pdf()
LOCKED = make_pdf(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="secret", owner_pw="owner")


class StubReview:
    """Stands in for referee.engine.review: records what it was given and writes what the UI loads."""

    def __init__(self):
        self.calls, self.running, self.peak = [], 0, 0
        self.started, self.release = threading.Event(), None
        self.error, self.touch_api = None, False
        self._lock = threading.Lock()

    def __call__(self, v1_path, v2_path, letter, gw, out_dir, *, case=None, **kw):
        with self._lock:
            self.running += 1
            self.peak = max(self.peak, self.running)
        try:
            v1_path, v2_path, out = Path(v1_path), Path(v2_path), Path(out_dir)
            self.calls.append({"v1": v1_path, "v2": v2_path, "note": letter, "case": case, "out": out, "gw": gw,
                               "v1_bytes": v1_path.read_bytes(), "v2_bytes": v2_path.read_bytes()})
            self.started.set()
            if self.release is not None:
                assert self.release.wait(10)
            if self.touch_api:
                gw.sent += 1
            if self.error is not None:
                raise self.error(v1_path) if callable(self.error) else self.error
            out.mkdir(parents=True, exist_ok=True)
            (out / "v1.pdf").write_bytes(v1_path.read_bytes())
            (out / "v1-p1.png").write_bytes(b"\x89PNG\r\n\x1a\n")
            result = {"schema": 1, "case": case, "status": "changes",
                      "audit": {"requests": [], "credits": 0, "calls": 0}}
            (out / "review.json").write_text(json.dumps(result), encoding="utf-8")
            return result
        finally:
            with self._lock:
                self.running -= 1


@pytest.fixture
def env(tmp_path, monkeypatch):
    import clocked_sdk

    def refuse(*a, **k):
        raise AssertionError("a test tried to build the real Clocked client")
    monkeypatch.setattr(clocked_sdk, "Clocked", refuse)
    monkeypatch.setenv("CLOCKED_API_KEY", "test-key-not-real")
    for name in ("LIVE_RUNS_PER_HOUR", "LIVE_RUNS_PER_DAY", "RUN_TTL_HOURS", "RUN_KEEP_MAX", "REVIEW_WAIT_SECONDS",
                 "TRUST_FORWARDED_FOR", "SERVE_SOURCE_PDF"):
        monkeypatch.delenv(name, raising=False)
    stub = StubReview()
    monkeypatch.setattr(server, "review", stub)
    web, cases = tmp_path / "web", tmp_path / "cases"
    web.mkdir(), cases.mkdir()
    (web / "index.html").write_text("<!doctype html><title>Redline Referee</title>")
    (web / "app.js").write_text("// app")
    (cases / "index.json").write_text("[]")
    app = server.create_app(tmp_path / "runs", tmp_path / "uploads", web, cases)

    class Env:
        pass
    e = Env()
    e.app, e.stub, e.runs, e.uploads, e.tmp, e.monkeypatch = app, stub, tmp_path / "runs", tmp_path / "uploads", tmp_path, monkeypatch
    with TestClient(app) as client:          # one event loop for every request of the test, as in the real server
        e.client = client
        yield e


def upload(client, v1=PDF, v2=PDF, note="Nothing changed.", names=("ours.pdf", "theirs.pdf"), **kw):
    return client.post("/api/review", data={"note": note},
                       files={"v1": (names[0], v1, "application/pdf"), "v2": (names[1], v2, "application/pdf")}, **kw)


def leftovers(e):
    return [p.name for p in e.runs.iterdir()] + [p.name for p in e.uploads.iterdir()]


def clean(text: str, e) -> bool:
    """A sentence for a person: no server path, no class name, no stack."""
    return (str(e.tmp) not in text and "/" not in text and "\\" not in text and "Traceback" not in text
            and not re.search(r"\b\w+(Error|Exception)\b", text) and text.strip().endswith("."))


# ------------------------------------------------------- what the UI relies on
def test_config_reports_whether_live_reviews_are_on(env):
    body = env.client.get("/api/config").json()
    assert body["live"] is True and body["max_pdf_bytes"] == server.MAX_PDF_BYTES
    env.monkeypatch.delenv("CLOCKED_API_KEY")
    assert env.client.get("/api/config").json()["live"] is False
    r = upload(env.client)
    assert r.status_code == 503 and "No Clocked API key" in r.json()["detail"]
    assert env.stub.calls == [] and leftovers(env) == []


def test_review_returns_the_location_and_serves_the_output(env):
    r = upload(env.client, note="We fixed typos.")
    assert r.status_code == 200
    body = r.json()
    assert re.fullmatch(r"[0-9a-f]{32}", body["id"]) and body["base"] == f"runs/{body['id']}/"
    assert body["status"] == "changes" and isinstance(body["elapsed_ms"], int) and body["elapsed_ms"] >= 0
    call = env.stub.calls[0]
    assert call["note"] == "We fixed typos." and call["v1_bytes"] == PDF and call["case"]["id"] == body["id"]
    assert call["case"]["title"] == "ours.pdf / theirs.pdf" and call["out"] == env.runs / body["id"]
    review = env.client.get(f"/{body['base']}review.json")
    assert review.status_code == 200 and review.json()["status"] == "changes"
    assert env.client.get(f"/{body['base']}v1-p1.png").status_code == 200
    assert env.client.get(f"/{body['base']}v1.pdf").content == PDF      # the engine's own copy, linked by the UI


def test_static_mounts_are_in_place(env):
    assert "Redline Referee" in env.client.get("/").text
    assert env.client.get("/app.js").status_code == 200
    assert env.client.get("/cases/index.json").json() == []
    assert env.client.get("/runs/").status_code == 404 and env.client.get("/api/nothing").status_code == 404
    assert env.client.get("/api/review").status_code in (404, 405)       # only POST starts a review


def test_the_module_level_app_has_the_same_routes():
    paths = {getattr(r, "path", None) for r in server.app.routes}
    assert {"/api/config", "/api/review", "/runs", ""} <= paths
    assert ("/cases" in paths) == server.SITE_CASES.is_dir()


# ------------------------------------------------- elapsed time in the audit
def test_elapsed_ms_is_returned_and_written_into_the_review(env):
    env.stub.release = threading.Event()
    threading.Timer(0.25, env.stub.release.set).start()
    body = upload(env.client).json()
    assert 200 <= body["elapsed_ms"] < 5000
    saved = json.loads((env.runs / body["id"] / "review.json").read_text())
    assert saved["audit"]["elapsed_ms"] == body["elapsed_ms"]
    assert saved["audit"]["calls"] == 0 and saved["status"] == "changes" and saved["schema"] == 1     # the rest is kept
    assert [p.name for p in (env.runs / body["id"]).iterdir() if p.name.endswith(".tmp")] == []


# ------------------------------------------- one review, off the event loop
def test_the_app_stays_responsive_while_a_review_runs(env):
    env.stub.release = threading.Event()
    with ThreadPoolExecutor(1) as pool:
        running = pool.submit(upload, env.client)
        assert env.stub.started.wait(5)
        timings = []
        for path in ("/api/config", "/", "/cases/index.json", "/api/config"):
            t = time.monotonic()
            assert env.client.get(path).status_code == 200
            timings.append(time.monotonic() - t)
        assert not running.done() and max(timings) < 0.2, timings
        env.stub.release.set()
        assert running.result(10).status_code == 200


def test_a_second_review_waits_a_bounded_time_then_is_told_to_retry(env):
    env.monkeypatch.setenv("REVIEW_WAIT_SECONDS", "0.3")
    env.stub.release = threading.Event()
    with ThreadPoolExecutor(1) as pool:
        running = pool.submit(upload, env.client)
        assert env.stub.started.wait(5)
        t = time.monotonic()
        second = upload(env.client)
        waited = time.monotonic() - t
        assert second.status_code == 503 and second.json()["detail"] == server.MSG_BUSY
        assert second.headers["retry-after"] == "60" and 0.25 < waited < 3
        env.stub.release.set()
        assert running.result(10).status_code == 200
    assert len(env.stub.calls) == 1 and env.stub.peak == 1
    assert env.app.state.limiter.counts("testclient") == (1, 1)       # the refused one was not counted
    assert len(leftovers(env)) == 1


def test_queued_reviews_run_one_after_another(env):
    env.monkeypatch.setenv("REVIEW_WAIT_SECONDS", "10")
    env.stub.release = threading.Event()
    with ThreadPoolExecutor(3) as pool:
        futs = [pool.submit(upload, env.client) for _ in range(3)]
        assert env.stub.started.wait(5)
        time.sleep(0.2)
        assert env.stub.running == 1
        env.stub.release.set()
        assert [f.result(10).status_code for f in futs] == [200, 200, 200]
    assert env.stub.peak == 1 and len(env.stub.calls) == 3


def test_only_a_few_uploads_may_wait(env):
    env.monkeypatch.setenv("REVIEW_WAIT_SECONDS", "10")
    env.stub.release = threading.Event()
    with ThreadPoolExecutor(8) as pool:
        first = pool.submit(upload, env.client)
        assert env.stub.started.wait(5)
        rest = [pool.submit(upload, env.client) for _ in range(server.MAX_WAITING + 3)]
        deadline = time.monotonic() + 5
        while sum(f.done() for f in rest) < 3 and time.monotonic() < deadline:
            time.sleep(0.02)
        refused = [f.result() for f in rest if f.done()]
        assert len(refused) == 3 and all(r.status_code == 503 for r in refused)       # at once, not after the wait
        env.stub.release.set()
        codes = sorted(f.result(15).status_code for f in [first, *rest])
    assert codes == [200] * (1 + server.MAX_WAITING) + [503] * 3 and env.stub.peak == 1


# ------------------------------------------------------------- rate limits
BAD_UPLOADS = {
    "not a pdf": (dict(v2=b"PK\x03\x04 this is a docx"), 422, "The copy they returned is not a PDF file."),
    "too large": (dict(v1=b"%PDF-1.7\n" + b"0" * server.MAX_PDF_BYTES), 413, "The version we sent is larger than 2,500 KB"),
    "empty": (dict(v1=b""), 422, "The version we sent is an empty file."),
    "damaged": (dict(v2=b"%PDF-1.7\nthis is not really a pdf at all"), 422, "The copy they returned could not be opened as a PDF."),
    "password": (dict(v2=LOCKED), 422, "The copy they returned is password-protected."),
    "long note": (dict(note="x" * (server.MAX_NOTE_CHARS + 1)), 422, "The cover note is longer than 8,000 characters."),
}


@pytest.mark.parametrize("kind", BAD_UPLOADS)
def test_each_rejected_upload_has_its_own_sentence(env, kind):
    kw, status, sentence = BAD_UPLOADS[kind]
    r = upload(env.client, **kw)
    assert r.status_code == status, r.text
    detail = r.json()["detail"]
    assert detail.startswith(sentence) and clean(detail, env)
    assert env.stub.calls == [] and leftovers(env) == []
    assert env.app.state.limiter.counts("testclient") == (0, 0)


def test_rejected_uploads_do_not_use_up_the_allowance(env):
    env.monkeypatch.setenv("LIVE_RUNS_PER_HOUR", "2")
    for _ in range(3):
        for kw, status, _ in BAD_UPLOADS.values():
            assert upload(env.client, **kw).status_code == status
    env.monkeypatch.delenv("CLOCKED_API_KEY")
    assert upload(env.client).status_code == 503
    env.monkeypatch.setenv("CLOCKED_API_KEY", "test-key-not-real")
    assert [upload(env.client).status_code for _ in range(2)] == [200, 200]       # the allowance is still whole
    third = upload(env.client)
    assert third.status_code == 429 and int(third.headers["retry-after"]) >= 60
    detail = third.json()["detail"]
    assert "limit of 2 live reviews per hour" in detail and "minutes" in detail and clean(detail, env)
    assert len(env.stub.calls) == 2 and upload(env.client, v1=b"nope").status_code == 422


def test_a_review_that_stops_before_the_api_is_not_counted(env):
    env.monkeypatch.setenv("LIVE_RUNS_PER_HOUR", "1")
    env.stub.error = lambda p: NoTextLayer(f"{p.name} has no usable text layer (about 3 characters per page).")
    for _ in range(3):
        assert upload(env.client).status_code == 422
    env.stub.error = RuntimeError("cannot parse")
    assert upload(env.client).status_code == 422
    assert env.app.state.limiter.counts("testclient") == (0, 0)
    env.stub.error = None
    assert upload(env.client).status_code == 200
    assert upload(env.client).status_code == 429


def test_a_review_that_failed_after_asking_the_api_is_counted(env):
    env.monkeypatch.setenv("LIVE_RUNS_PER_HOUR", "1")
    env.stub.error, env.stub.touch_api = RuntimeError("engine bug"), True
    assert upload(env.client).status_code == 500
    assert env.app.state.limiter.counts("testclient") == (1, 1)
    assert upload(env.client).status_code == 429


def test_the_daily_cap_is_shared_by_every_address(env):
    env.monkeypatch.setenv("LIVE_RUNS_PER_HOUR", "5")
    env.monkeypatch.setenv("LIVE_RUNS_PER_DAY", "3")
    env.monkeypatch.setenv("TRUST_FORWARDED_FOR", "1")
    codes = [upload(env.client, headers={"X-Forwarded-For": f"203.0.113.9, 198.51.100.{i}"}).status_code for i in range(3)]
    assert codes == [200, 200, 200]
    r = upload(env.client, headers={"X-Forwarded-For": "198.51.100.77"})
    assert r.status_code == 429 and "limit of 3 live reviews per day" in r.json()["detail"] and clean(r.json()["detail"], env)
    assert env.app.state.limiter.counts("198.51.100.1") == (1, 3)


def test_the_hourly_cap_is_per_address(env):
    env.monkeypatch.setenv("LIVE_RUNS_PER_HOUR", "1")
    env.monkeypatch.setenv("TRUST_FORWARDED_FOR", "1")
    a, b = {"X-Forwarded-For": "198.51.100.1"}, {"X-Forwarded-For": "198.51.100.2"}
    assert [upload(env.client, headers=h).status_code for h in (a, a, b, b)] == [200, 429, 200, 429]
    env.monkeypatch.delenv("TRUST_FORWARDED_FOR")           # not behind a proxy: the header is ignored
    assert upload(env.client, headers=a).status_code == 200
    assert upload(env.client, headers=b).status_code == 429


def test_old_hits_fall_out_of_the_window(env):
    limiter = env.app.state.limiter
    env.monkeypatch.setenv("LIVE_RUNS_PER_HOUR", "1")
    real = time.time
    stamp = limiter.start("a")
    assert limiter.counts("a") == (1, 1)
    env.monkeypatch.setattr(time, "time", lambda: real() + 3601)
    assert limiter.counts("a") == (0, 1) and limiter._by_addr == {}       # the key is dropped, not kept empty
    limiter.start("a")
    env.monkeypatch.setattr(time, "time", lambda: real() + 86401 + 3601)
    assert limiter.counts("a") == (0, 0)
    limiter.refund("a", stamp)                                # refunding something already gone is harmless


# ----------------------------------------------------------- error messages
def test_scanned_pdfs_keep_their_specific_message(env):
    env.stub.error = lambda p: NoTextLayer(f"v2.pdf has no usable text layer (about 3 characters per page) in {p.parent}")
    r = upload(env.client)
    detail = r.json()["detail"]
    assert r.status_code == 422 and detail.startswith("The copy they returned has no usable text layer.")
    assert "Scanned copies cannot be quoted character by character" in detail and clean(detail, env)
    assert leftovers(env) == []


def test_errors_never_carry_paths_class_names_or_stack_details(env, caplog):
    env.stub.error = lambda p: pymupdf.FileDataError(f"Failed to open file '{p}' as type pdf.")
    with caplog.at_level("INFO", logger="referee.server"):
        r = upload(env.client)
    assert r.status_code == 422 and r.json()["detail"] == server.MSG_UNREADABLE and clean(r.json()["detail"], env)
    assert str(env.tmp) not in r.text and "FileDataError" not in r.text
    assert "Failed to open file" in caplog.text and "FileDataError" in caplog.text      # the detail is in the log
    assert leftovers(env) == []

    env.stub.error, env.stub.touch_api = lambda p: KeyError(f"verdict missing in {p}"), True
    caplog.clear()
    r = upload(env.client)
    run_id = env.stub.calls[-1]["case"]["id"]
    assert r.status_code == 500 and r.json()["detail"].endswith(f"Reference: {run_id[:8]}.")
    assert "not because of your files" in r.json()["detail"] and str(env.tmp) not in r.text and "KeyError" not in r.text
    assert run_id in caplog.text and "KeyError" in caplog.text
    assert leftovers(env) == []


def test_missing_fields_get_a_sentence_not_a_validation_dump(env):
    r = env.client.post("/api/review", data={"note": "x"}, files={"v1": ("a.pdf", PDF, "application/pdf")})
    assert r.status_code == 422 and r.json() == {"detail": server.MSG_MISSING}


def test_an_oversized_body_is_refused_before_it_is_read(env):
    big = b"%PDF-1.7\n" + b"0" * (server.MAX_BODY_BYTES // 2 + 10)
    r = upload(env.client, v1=big, v2=big)
    assert r.status_code == 413 and r.json()["detail"] == server.MSG_BODY_TOO_LARGE and clean(r.json()["detail"], env)
    assert env.stub.calls == [] and leftovers(env) == []
    assert r.headers["x-content-type-options"] == "nosniff"

    def chunks():                                        # no Content-Length: counted as it arrives
        yield b'--b\r\nContent-Disposition: form-data; name="v1"; filename="a.pdf"\r\n\r\n%PDF-1.7\n'
        for _ in range(server.MAX_BODY_BYTES // 65536 + 2):
            yield b"0" * 65536
        yield b"\r\n--b--\r\n"
    r = env.client.post("/api/review", content=chunks(), headers={"Content-Type": "multipart/form-data; boundary=b"})
    assert "content-length" not in r.request.headers
    assert r.status_code == 413 and r.json()["detail"] == server.MSG_BODY_TOO_LARGE


def test_uploaded_file_names_are_reduced_to_a_plain_title(env):
    upload(env.client, names=("../../etc/passwd.pdf", "C:\\Users\\dana\\" + "n" * 200 + ".pdf"))
    title = env.stub.calls[0]["case"]["title"]
    assert title == "passwd.pdf / " + "n" * 80

    class Up:
        filename = "a/b\\c\x00\x07\n.pdf"
    assert server._display_name(Up(), "v1.pdf") == "c.pdf"
    Up.filename = None
    assert server._display_name(Up(), "v1.pdf") == "v1.pdf"


# ------------------------------------------------- storage of uploads and runs
def test_uploads_are_kept_outside_the_served_folder_and_deleted_after(env):
    body = upload(env.client).json()
    call = env.stub.calls[0]
    assert call["v1"].parent == call["v2"].parent == env.uploads / body["id"]
    assert env.runs not in call["v1"].parents and env.runs.resolve() not in call["v1"].resolve().parents
    assert list(env.uploads.iterdir()) == [] and not (env.runs / body["id"] / "input").exists()
    for path in ("input/v1.pdf", "../uploads/x/v1.pdf", f"../../uploads/{body['id']}/v1.pdf"):
        assert env.client.get(f"/runs/{body['id']}/{path}").status_code == 404
    env.monkeypatch.setenv("SERVE_SOURCE_PDF", "0")                  # the engine's copy can be withheld too
    assert env.client.get(f"/runs/{body['id']}/v1.pdf").status_code == 404
    assert env.client.get(f"/runs/{body['id']}/review.json").status_code == 200


def test_an_input_folder_from_the_old_layout_is_not_served(env):
    old = env.runs / "32aeaeadd374"
    (old / "input").mkdir(parents=True)
    (old / "input" / "v1.pdf").write_bytes(PDF)
    (old / "review.json").write_text("{}")
    assert env.client.get("/runs/32aeaeadd374/review.json").status_code == 200
    assert env.client.get("/runs/32aeaeadd374/input/v1.pdf").status_code == 404


def make_run(e, name, age_hours=0.0, base=None):
    d = (base or e.runs) / name
    d.mkdir(parents=True)
    (d / "review.json").write_text("{}")
    stamp = time.time() - age_hours * 3600
    os.utime(d, (stamp, stamp))
    return d


def test_expired_runs_are_deleted_when_a_request_comes_in(env):
    old, fresh = make_run(env, "a" * 12, 25), make_run(env, "b" * 12, 23)
    stale_upload = make_run(env, "c" * 32, 2, base=env.uploads)
    other = env.runs / "notes"                       # not a run folder: never touched
    other.mkdir()
    os.utime(other, (0, 0))
    keep_file = env.runs / ("d" * 12)                # a file, not a folder
    keep_file.write_text("x")
    os.utime(keep_file, (0, 0))
    assert env.client.get(f"/runs/{old.name}/review.json").status_code == 404        # expired: not served even before the sweep
    assert old.exists()
    body = upload(env.client).json()
    assert not old.exists() and not stale_upload.exists()
    assert fresh.exists() and other.exists() and keep_file.exists() and (env.runs / body["id"]).exists()

    env.monkeypatch.setenv("RUN_TTL_HOURS", "1")
    assert env.app.state.sweep() == 1 and not fresh.exists() and (env.runs / body["id"]).exists()
    env.monkeypatch.setenv("RUN_TTL_HOURS", "0")     # 0 keeps runs whatever their age
    ancient = make_run(env, "e" * 12, 24 * 365)
    assert env.app.state.sweep() == 0 and ancient.exists()
    assert env.client.get(f"/runs/{ancient.name}/review.json").status_code == 200


def test_config_requests_sweep_too_but_not_every_time(env):
    old = make_run(env, "a" * 12, 30)
    assert env.client.get("/api/config").status_code == 200 and not old.exists()
    again = make_run(env, "b" * 12, 30)
    assert env.client.get("/api/config").status_code == 200 and again.exists()       # swept less than a minute ago


def test_the_number_of_kept_runs_is_capped(env):
    env.monkeypatch.setenv("RUN_KEEP_MAX", "3")
    made = [make_run(env, f"{i:012x}", age_hours=i) for i in range(1, 7)]       # 1 h old ... 6 h old
    ids = [upload(env.client).json()["id"] for _ in range(2)]
    left = sorted(p.name for p in env.runs.iterdir())
    assert left == sorted([ids[0], ids[1], made[0].name])                      # the three newest


def test_a_running_review_is_never_swept(env):
    env.monkeypatch.setenv("RUN_KEEP_MAX", "1")
    env.stub.release = threading.Event()
    with ThreadPoolExecutor(1) as pool:
        running = pool.submit(upload, env.client)
        assert env.stub.started.wait(5)
        run_id = env.stub.calls[0]["case"]["id"]
        (env.runs / run_id).mkdir()
        os.utime(env.runs / run_id, (0, 0))
        os.utime(env.uploads / run_id, (0, 0))
        newer = make_run(env, "f" * 12)
        env.app.state.sweep()
        assert (env.runs / run_id).exists() and (env.uploads / run_id).exists() and newer.exists()
        env.stub.release.set()
        assert running.result(10).status_code == 200


# ------------------------------------------------ headers and path handling
def test_security_headers_are_on_every_kind_of_response(env):
    body = upload(env.client).json()
    responses = [env.client.get("/"), env.client.get("/api/config"), env.client.get(f"/runs/{body['id']}/review.json"),
                 env.client.get("/nope.html"), env.client.get("/runs/nope/review.json"),
                 upload(env.client, v1=b"nope"), env.client.get("/api/review"), env.client.post("/api/config")]
    assert [r.status_code for r in responses][:6] == [200, 200, 200, 404, 404, 422]
    assert all(r.status_code in (404, 405) for r in responses[6:])
    for r in responses:
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["x-frame-options"] == "DENY"
        assert r.headers["referrer-policy"] == "no-referrer"
        assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert responses[1].headers["cache-control"] == "no-store" and responses[2].headers["cache-control"] == "no-store"


TRAVERSALS = ["/runs/%2e%2e/secret.txt", "/runs/%2e%2e/uploads/RID/v1.pdf", "/runs/RID/%2e%2e/%2e%2e/secret.txt",
              "/runs/RID/..%2f..%2fsecret.txt", "/runs/..%5csecret.txt", "/runs/RID%2f..%2f..%2fsecret.txt",
              "/%2e%2e/secret.txt", "/cases/%2e%2e/secret.txt", "/runs/RID/", "/runs/RID", "/runs/RID/.hidden.json",
              "/runs/RID/review.json.tmp", "/runs/RID/notes.txt", "/runs/RID/sub/review.json", "/runs/RID/link.json",
              "/runs/linked/review.json", "/runs//etc/passwd", "/runs/RID/review.json%00.png"]


@pytest.mark.parametrize("path", TRAVERSALS)
def test_paths_cannot_leave_the_runs_folder(env, path):
    (env.tmp / "secret.txt").write_text("TOP SECRET")
    body = upload(env.client).json()
    run = env.runs / body["id"]
    (run / ".hidden.json").write_text("{}")
    (run / "notes.txt").write_text("x")
    (run / "sub").mkdir()
    (run / "sub" / "review.json").write_text("{}")
    (run / "link.json").symlink_to(env.tmp / "secret.txt")
    (env.runs / "linked").symlink_to(env.tmp)
    make_run(env, "RID", base=env.uploads)
    r = env.client.get(path.replace("RID", body["id"]))
    assert r.status_code == 404 and "TOP SECRET" not in r.text
    assert r.headers["x-content-type-options"] == "nosniff"


def test_dot_segments_are_refused_before_routing():
    """Clients normalise /a/../b themselves, so hand the app the raw path the way a hostile client would."""
    sent = []

    async def inner(scope, receive, send):
        raise AssertionError("the request reached the application")

    async def go(path):
        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            sent.append(message)
        await server.Shield(inner)({"type": "http", "method": "GET", "path": path, "headers": []}, receive, send)
    for path in ("/runs/../uploads/x/v1.pdf", "/runs/abc/../../etc/passwd", "/..", "/runs/a\\..\\b", "/runs/a\x00.json"):
        sent.clear()
        asyncio.run(go(path))
        assert sent[0]["status"] == 404
        assert (b"x-frame-options", b"DENY") in sent[0]["headers"]


# ------------------------------------- the real engine behind the endpoint
def test_the_real_engine_runs_behind_the_endpoint_without_the_network(env):
    """No stub for review here: the engine itself, with the unit-test stand-in for the API as its client."""
    from referee import engine
    from referee.gateway import Gateway
    from tests.fake_clocked import FakeClocked, load_truth

    case = server.ROOT / "data" / "cases" / "quiet-changes"
    fake = FakeClocked(load_truth(case))
    env.monkeypatch.setattr(server, "review", engine.review)
    env.monkeypatch.setattr(server, "Gateway", lambda mode: Gateway(mode, client=fake))
    r = upload(env.client, v1=(case / "v1.pdf").read_bytes(), v2=(case / "v2.pdf").read_bytes(),
               note=(case / "cover.txt").read_text())
    assert r.status_code == 200, r.text
    body = r.json()
    saved = env.client.get(f"/{body['base']}review.json").json()
    assert saved["status"] == body["status"] and saved["case"]["id"] == body["id"]
    assert saved["audit"]["elapsed_ms"] == body["elapsed_ms"] > 0
    assert saved["audit"]["calls"] == len(saved["audit"]["requests"]) > 0
    assert all({"request_id", "trace", "cached", "replayed"} <= set(row) for row in saved["audit"]["requests"])
    page = saved["documents"]["v2"]["pages"][0]
    image = page["image"] if isinstance(page, dict) else page
    assert env.client.get(f"/{body['base']}{image}").status_code == 200
    assert list(env.uploads.iterdir()) == [] and not (env.runs / body["id"] / "input").exists()
    assert env.app.state.limiter.counts("testclient") == (1, 1)


def test_a_real_scanned_pdf_gets_the_scanned_message_and_costs_nothing(env):
    from referee import engine

    blank = pymupdf.open()
    blank.new_page()
    scanned = blank.tobytes()
    env.monkeypatch.setattr(server, "review", engine.review)
    env.monkeypatch.setattr(server, "Gateway", lambda mode: type("G", (), {"audit": [], "sent": 0})())
    r = upload(env.client, v2=scanned)
    assert r.status_code == 422 and "has no usable text layer." in r.json()["detail"] and clean(r.json()["detail"], env)
    assert env.app.state.limiter.counts("testclient") == (0, 0) and leftovers(env) == []


# ---- /api/reply -----------------------------------------------------------------------------------------------
def test_reply_needs_a_key_a_known_review_and_valid_decisions(tmp_path, monkeypatch):
    import json as _json

    from fastapi.testclient import TestClient

    import referee.reply as reply_mod
    from referee.server import create_app

    runs, cases = tmp_path / "runs", tmp_path / "cases"
    (cases / "demo").mkdir(parents=True)
    (cases / "demo" / "review.json").write_text(_json.dumps({"changes": []}))
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("ok")
    client = TestClient(create_app(runs_dir=runs, uploads_dir=tmp_path / "up", web_dir=web, cases_dir=cases))
    monkeypatch.delenv("CLOCKED_API_KEY", raising=False)
    assert client.post("/api/reply", json={"base": "cases/demo/", "decisions": {}}).status_code == 503
    monkeypatch.setenv("CLOCKED_API_KEY", "test-key-not-real")
    seen = {}
    monkeypatch.setattr(reply_mod, "draft_reply", lambda review, decisions, gw: seen.update(d=decisions) or {"ran": True, "text": "hello", "brief": "internal"})
    ok = client.post("/api/reply", json={"base": "cases/demo/", "decisions": {"c1": "accept"}})
    assert ok.status_code == 200 and ok.json()["text"] == "hello" and "brief" not in ok.json() and seen["d"] == {"c1": "accept"}
    assert client.post("/api/reply", json={"base": "cases/missing/", "decisions": {}}).status_code == 404
    assert client.post("/api/reply", json={"base": "../etc/", "decisions": {}}).status_code == 422
    assert client.post("/api/reply", json={"base": "cases/demo/", "decisions": {"c1": "sign"}}).status_code == 422
