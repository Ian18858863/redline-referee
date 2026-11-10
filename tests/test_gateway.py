"""The gateway on its own: budget, cache, recordings, replay, audit.

Nothing here can reach the network: every live Gateway is given a stub client, the real
SDK client is replaced by one that fails on construction, and the key is a dummy.
"""
import base64
import hashlib
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from referee import gateway as G
from referee.gateway import ENDPOINTS, Gateway, StepError, _strip, audit_ids, request_hash

ROOT = Path(__file__).resolve().parents[1]
REC, CASES = ROOT / "recordings", ROOT / "data" / "cases"
BY_PATH = {v: k for k, v in ENDPOINTS.items()}
CF_RAY = "a5845e52ced34e45-BOS"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    import clocked_sdk

    def refuse(*a, **k):
        raise AssertionError("a test tried to build the real Clocked client")
    monkeypatch.setattr(clocked_sdk, "Clocked", refuse)
    monkeypatch.setenv("CLOCKED_API_KEY", "test-key-not-real")
    for name in ENDPOINTS:
        monkeypatch.delenv(f"CLOCKED_ESTIMATE_{name.upper()}", raising=False)


class Result(dict):
    def __init__(self, body, credits, headers, key):
        super().__init__(body)
        self.status, self.attempts, self.cost_usd = 200, 1, 0.0
        self.credits, self.model_ms, self.headers, self.idempotency_key = credits, 5.0, headers, key


class Stub:
    """Answers every endpoint the same way; counts calls and how many were in flight together."""

    def __init__(self, credits=40, delay=0.0, headers=None, fail=None, gate=None):
        self.credits, self.delay, self.fail, self.gate = credits, delay, fail, gate
        self.headers = {"cf-ray": CF_RAY} if headers is None else headers
        self.calls, self.timeouts, self.in_flight, self.peak = [], [], 0, 0
        self._lock = threading.Lock()

    def __getattr__(self, name):
        if name not in ENDPOINTS:
            raise AttributeError(name)

        def endpoint(request, idempotency_key=None, timeout=None):
            with self._lock:
                self.calls.append((name, request))
                self.timeouts.append(timeout)
                self.in_flight += 1
                self.peak = max(self.peak, self.in_flight)
            try:
                if self.gate is not None:
                    self.gate.wait(5)
                if self.delay:
                    time.sleep(self.delay)
                if self.fail is not None:
                    raise self.fail
                credits = self.credits(request) if callable(self.credits) else self.credits
                return Result({"echo": request.get("n"), "nested": {"list": [1, 2]}}, credits, self.headers, idempotency_key)
            finally:
                with self._lock:
                    self.in_flight -= 1
        return endpoint


def attempt(gw, endpoint, request, label="step"):
    try:
        return gw.call(endpoint, request, label)
    except StepError as e:
        return e


# ------------------------------------------------------------------ the cap
def test_cap_is_reserved_before_the_call_so_parallel_calls_cannot_overshoot():
    """The review's case: cap 10, eight parallel calls of 40 credits each. Before, all eight went out (320)."""
    stub = Stub(credits=40, delay=0.15)
    gw = Gateway("live", client=stub, credit_cap=10)
    with ThreadPoolExecutor(8) as pool:
        out = list(pool.map(lambda n: attempt(gw, "compare", {"n": n}), range(8)))
    sent = [o for o in out if not isinstance(o, StepError)]
    refused = [o for o in out if isinstance(o, StepError)]
    assert len(stub.calls) == 1 and len(sent) == 1
    assert len(refused) == 7 and all(e.kind == "budget" for e in refused)
    assert gw.credits == 40 and gw.reserved == 0 and gw.sent == 1


def test_cap_is_passed_by_at_most_one_call_estimate():
    stub = Stub(credits=40, delay=0.1)
    gw = Gateway("live", client=stub, credit_cap=100, estimates={"compare": 40})
    with ThreadPoolExecutor(8) as pool:
        out = list(pool.map(lambda n: attempt(gw, "compare", {"n": n}), range(8)))
    assert len(stub.calls) == 3                      # 0, 40 and 80 were under the cap; 120 was not
    assert gw.credits == 120 <= gw.credit_cap + gw.estimates["compare"]
    assert sum(isinstance(o, StepError) and o.kind == "budget" for o in out) == 5
    assert gw.reserved == 0


def test_reservation_is_settled_to_the_real_cost_and_learns_from_it():
    gate = threading.Event()
    stub = Stub(credits=6, gate=gate)
    gw = Gateway("live", client=stub, credit_cap=1000, estimates={"compare": 50})
    with ThreadPoolExecutor(1) as pool:
        fut = pool.submit(gw.call, "compare", {"n": 1}, "a")
        while not stub.calls:
            time.sleep(0.005)
        assert gw.reserved == 50 and gw.credits == 0     # in flight: counted against the cap, not yet spent
        gate.set()
        fut.result()
    assert gw.reserved == 0 and gw.credits == 6

    dear = Stub(credits=80)
    gw = Gateway("live", client=dear, credit_cap=1000, estimates={"extract": 10})
    gw.call("extract", {"n": 1}, "a")
    gate2 = threading.Event()
    dear.gate = gate2
    with ThreadPoolExecutor(1) as pool:
        fut = pool.submit(gw.call, "extract", {"n": 2}, "b")
        while len(dear.calls) < 2:
            time.sleep(0.005)
        assert gw.reserved == 80                     # the estimate rose to what this endpoint really cost
        gate2.set()
        fut.result()


def test_estimates_come_from_the_environment_and_the_constructor(monkeypatch):
    monkeypatch.setenv("CLOCKED_ESTIMATE_COMPARE", "77")
    monkeypatch.setenv("CLOCKED_ESTIMATE_DECIDE", "not a number")
    gw = Gateway("live", client=Stub(), estimates={"rerank": 9})
    assert gw.estimates["compare"] == 77 and gw.estimates["rerank"] == 9
    assert gw.estimates["decide"] == G.DEFAULT_ESTIMATES["decide"]
    assert set(gw.estimates) == set(ENDPOINTS)
    with pytest.raises(ValueError):
        Gateway("live", client=Stub(), estimates={"nope": 1})


def test_a_refused_call_frees_its_reservation_and_a_lost_one_keeps_it():
    refused = RuntimeError("upstream said no")
    refused.status = 503
    gw = Gateway("live", client=Stub(fail=refused), estimates={"compare": 40})
    e = attempt(gw, "compare", {"n": 1})
    assert isinstance(e, StepError) and e.status == 503 and e.kind == "error"
    assert (gw.reserved, gw.unsettled, gw.credits) == (0, 0, 0)

    class ReadTimeout(Exception):
        pass
    gw = Gateway("live", client=Stub(fail=ReadTimeout("no answer")), credit_cap=70, estimates={"compare": 40})
    e = attempt(gw, "compare", {"n": 1})
    assert e.kind == "timeout" and (gw.reserved, gw.unsettled, gw.credits) == (0, 40, 0)
    attempt(gw, "compare", {"n": 2})
    assert attempt(gw, "compare", {"n": 3}).kind == "budget"      # 80 possibly billed: over the cap of 70
    assert gw.sent == 2 and gw.audit == []


def test_rate_limit_answers_keep_their_kind():
    busy = RuntimeError("slow down")
    busy.status = 429
    e = attempt(Gateway("live", client=Stub(fail=busy)), "rerank", {"n": 1})
    assert e.kind == "rate_limit" and e.as_dict()["endpoint"] == "Rerank"


# ---------------------------------------------------------------- the cache
def test_identical_live_requests_are_sent_once_and_logged_as_cached():
    stub = Stub(credits=12)
    gw = Gateway("live", client=stub)
    a = gw.call("grounding", {"n": 1, "claims": ["x"]}, "first")
    b = gw.call("grounding", {"claims": ["x"], "n": 1}, "again")     # same body, other key order
    c = gw.call("grounding", {"n": 2, "claims": ["x"]}, "different")
    assert len(stub.calls) == 2 and a == b and c != a
    first, again, other = gw.audit
    assert (first["cached"], first["credits"], first["replayed"]) == (False, 12, False)
    assert (again["cached"], again["credits"], again["replayed"], again["step"]) == (True, 0, False, "again")
    assert again["trace"] == first["trace"] and again["idempotency_key"] == first["idempotency_key"]
    assert other["cached"] is False
    assert gw.credits == 24 and sum(r["credits"] for r in gw.audit) == 24
    a["nested"]["list"].append("changed by the caller")
    assert gw.call("grounding", {"n": 1, "claims": ["x"]}, "third")["nested"]["list"] == [1, 2]


def test_identical_requests_in_flight_together_share_one_call():
    """Two identical files make the engine send the same Extract twice at the same moment."""
    stub = Stub(credits=20, delay=0.15)
    gw = Gateway("live", client=stub)
    with ThreadPoolExecutor(6) as pool:
        out = list(pool.map(lambda _: gw.call("extract", {"n": 7}, "extract"), range(6)))
    assert len(stub.calls) == 1 and all(o == out[0] for o in out)
    assert gw.credits == 20 and sum(r["cached"] for r in gw.audit) == 5 and len(gw.audit) == 6


def test_a_failed_call_is_not_cached():
    boom = RuntimeError("outage")
    boom.status = 500
    stub = Stub(fail=boom)
    gw = Gateway("live", client=stub)
    assert isinstance(attempt(gw, "decide", {"n": 1}), StepError)
    stub.fail = None
    assert gw.call("decide", {"n": 1}, "retry")["echo"] == 1
    assert len(stub.calls) == 2 and [r["cached"] for r in gw.audit] == [False]


def test_cache_in_auto_mode_does_not_bill_the_repeat_twice(tmp_path):
    stub = Stub(credits=9)
    gw = Gateway("auto", tmp_path / "rec", record=True, client=stub)
    gw.call("rerank", {"n": 1}, "one")
    gw.call("rerank", {"n": 1}, "one again")
    assert len(stub.calls) == 1 and [r["credits"] for r in gw.audit] == [9, 0]
    assert len(list((tmp_path / "rec").glob("*.json"))) == 1
    later = Gateway("auto", tmp_path / "rec", client=Stub(credits=9))
    assert later.call("rerank", {"n": 1}, "replayed")["echo"] == 1
    assert later.audit[0]["replayed"] and later.audit[0]["cached"] is False and later._client.calls == []


# ------------------------------------------------------------ the recordings
def test_recording_sequence_numbers_never_collide(tmp_path):
    rec = tmp_path / "rec"
    gate = threading.Event()
    gw = Gateway("live", rec, record=True, client=Stub(credits=1, gate=gate))
    with ThreadPoolExecutor(12) as pool:
        futs = [pool.submit(gw.call, "compare", {"n": n}, f"compare clause {n}.1/a") for n in range(12)]
        time.sleep(0.2)
        gate.set()                                   # all twelve finish at the same moment
        [f.result() for f in futs]
    names = sorted(f.name for f in rec.iterdir())
    assert len(names) == 12 and all(n.endswith(".json") for n in names)      # and no temporary file left
    assert sorted(int(n.split("-")[0]) for n in names) == list(range(1, 13))
    assert all(re.fullmatch(r"\d\d-compare-compare_clause_\d+\.1_a-[0-9a-f]{8}\.json", n) for n in names)
    for f in rec.iterdir():
        body = json.loads(f.read_text())
        assert list(body) == ["endpoint", "label", "request_hash", "request", "meta", "response"]
        assert f.name.endswith(body["request_hash"][:8] + ".json")

    (rec / names[2]).unlink()                        # a gap, as pruning leaves: the next number is still new
    again = Gateway("live", rec, record=True, client=Stub(credits=1))
    again.call("decide", {"n": 99}, "decide 9")
    assert sorted(f.name for f in rec.iterdir())[-1].startswith("13-decide-decide_9-")
    assert len(Gateway("replay", rec)._index) == 12


def test_two_gateways_writing_one_folder_do_not_share_a_number(tmp_path):
    rec = tmp_path / "rec"
    gws = [Gateway("live", rec, record=True, client=Stub(credits=1, delay=0.05)) for _ in range(4)]
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(lambda i: gws[i % 4].call("rerank", {"n": i}, "r"), range(16)))
    assert sorted(int(f.name.split("-")[0]) for f in rec.iterdir()) == list(range(1, 17))


def test_pdfs_are_stored_as_their_hash_and_hash_the_same_either_way(tmp_path):
    raw = b"%PDF-1.7 " + b"x" * 400
    request = {"pdf": base64.b64encode(raw).decode(), "fields": ["a"]}
    gw = Gateway("live", tmp_path / "rec", record=True, client=Stub())
    gw.call("extract", request, "extract terms v1")
    stored = json.loads(next((tmp_path / "rec").iterdir()).read_text())
    assert stored["request"] == {"pdf": {"sha256": hashlib.sha256(raw).hexdigest()}, "fields": ["a"]}
    assert request_hash("extract", request) == request_hash("extract", stored["request"]) == stored["request_hash"]
    assert Gateway("replay", tmp_path / "rec").call("extract", request, "x") == stored["response"]


def test_request_hash_has_not_moved():
    """Recordings are found by this value: if it changes, every saved exchange is lost."""
    assert request_hash("rerank", {"query": "q", "documents": [{"id": "a", "text": "é t"}], "top_n": 3}) == \
        "9fac8f143bef25a4534c5258d76cc676e76735691d2e640c7f4d19d241c5d4ba"
    pdf = base64.b64encode(b"%PDF-1.7 " + b"x" * 400).decode()
    assert request_hash("extract", {"pdf": pdf, "fields": ["a"]}) == \
        "fc60108a024cf1006941ab73d218bb964cd046282ee20ca2fa76dd57adcd2099"


# ------------------------------------------------------------------- replay
def test_replay_miss_is_an_error_and_never_goes_live(tmp_path):
    stub = Stub()
    gw = Gateway("replay", tmp_path / "nothing", client=stub)
    with pytest.raises(StepError) as e:
        gw.call("compare", {"n": 1}, "compare v1 v2")
    assert e.value.kind == "replay_miss" and e.value.as_dict()["step"] == "compare v1 v2"
    assert stub.calls == [] and gw.audit == [] and gw.sent == 0


def test_a_recording_whose_request_does_not_match_its_hash_is_not_replayed(tmp_path):
    rec = tmp_path / "rec"
    gw = Gateway("live", rec, record=True, client=Stub())
    gw.call("decide", {"n": 1}, "a")
    f = next(rec.iterdir())
    body = json.loads(f.read_text())
    body["request_hash"] = request_hash("decide", {"n": 2})      # claims to be the answer to another request
    f.write_text(json.dumps(body))
    replay = Gateway("replay", rec)
    assert replay.rejected == [f] and replay._index == {}
    assert attempt(replay, "decide", {"n": 2}).kind == "replay_miss"
    assert attempt(replay, "decide", {"n": 1}).kind == "replay_miss"


RECORDED = sorted(d.name for d in REC.iterdir() if d.is_dir() and any(d.glob("*.json"))) if REC.is_dir() else []


@pytest.mark.skipif(not RECORDED, reason="no recordings/")
@pytest.mark.parametrize("case", [c for c in ("quiet-changes", "eval-01") if c in RECORDED] or RECORDED[:1])
def test_existing_recordings_replay_request_by_request(case):
    """The files in recordings/ were written before this change. Each one must still be found by the hash of
    its own stored request (PDFs already replaced by their sha256, the form _strip leaves alone)."""
    folder = REC / case
    before = {f.name: f.read_bytes() for f in folder.glob("*.json")}
    gw = Gateway("replay", folder)
    assert gw.rejected == [] and len(gw._index) == len(before)
    for name in sorted(before):
        rec = json.loads(before[name])
        endpoint = BY_PATH[rec["endpoint"]]
        assert _strip(rec["request"]) == rec["request"]
        assert request_hash(endpoint, rec["request"]) == rec["request_hash"], name
        assert gw.call(endpoint, rec["request"], rec["label"]) == rec["response"], name
        row = gw.audit[-1]
        assert row["replayed"] and not row["cached"] and row["credits"] == rec["meta"]["credits"]
        # the same exchange is found from the real request, with the PDF bytes in it
        for src in sorted((CASES / case).glob("v*.pdf")) if (CASES / case).is_dir() else []:
            sha = hashlib.sha256(src.read_bytes()).hexdigest()
            if rec["request"].get("pdf") == {"sha256": sha}:
                real = {**rec["request"], "pdf": base64.b64encode(src.read_bytes()).decode()}
                assert gw.call(endpoint, real, rec["label"]) == rec["response"]
    assert gw.used == {folder / n for n in before} and gw.sent == 0
    assert {f.name: f.read_bytes() for f in folder.glob("*.json")} == before      # read, never rewritten


@pytest.mark.skipif(not RECORDED, reason="no recordings/")
def test_every_existing_recording_is_still_indexed():
    for case in RECORDED:
        files = list((REC / case).glob("*.json"))
        gw = Gateway("replay", REC / case)
        assert gw.rejected == [], case
        assert len(gw._index) == len({json.loads(f.read_text())["request_hash"] for f in files}), case


# -------------------------------------------------------------- time budget
def test_time_budget_stops_calls_before_they_leave():
    stub = Stub()
    gw = Gateway("live", client=stub, time_budget_s=0.5)         # under the one second a call needs at least
    e = attempt(gw, "compare", {"n": 1})
    assert e.kind == "timeout" and stub.calls == [] and gw.reserved == 0 and gw.sent == 0


def test_each_call_gets_no_more_time_than_the_budget_has_left():
    stub = Stub()
    Gateway("live", client=stub, time_budget_s=30).call("compare", {"n": 1}, "a")
    Gateway("live", client=stub, time_budget_s=600).call("compare", {"n": 1}, "a")
    assert 28 < stub.timeouts[0] <= 30 and stub.timeouts[1] == 90.0


def test_a_caller_waiting_for_an_identical_call_gives_up_when_the_budget_ends():
    gate = threading.Event()
    stub = Stub(gate=gate)
    gw = Gateway("live", client=stub, time_budget_s=1.3)
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(attempt, gw, "compare", {"n": 1})
        while not stub.calls:
            time.sleep(0.005)
        second = pool.submit(attempt, gw, "compare", {"n": 1})
        waited = second.result(timeout=5)
        gate.set()
        first.result(timeout=5)
    assert isinstance(waited, StepError) and waited.kind == "timeout" and len(stub.calls) == 1


# -------------------------------------------------------------------- audit
AUDIT_KEYS = {"endpoint", "path", "step", "credits", "model_ms", "attempts", "request_id", "trace",
              "idempotency_key", "recorded_at", "replayed", "cached"}


def test_cf_ray_is_the_trace_and_not_a_request_id(tmp_path):
    gw = Gateway("live", tmp_path / "rec", record=True, client=Stub(headers={"CF-Ray": CF_RAY, "server": "cloudflare"}))
    gw.call("injection", {"n": 1}, "guard cover note")
    row = gw.audit[0]
    assert set(row) == AUDIT_KEYS
    assert row["request_id"] is None and row["trace"] == CF_RAY
    assert (row["endpoint"], row["path"], row["step"]) == ("Guard", "/clocked/injection", "guard cover note")
    meta = json.loads(next((tmp_path / "rec").iterdir()).read_text())["meta"]
    assert meta["request_id"] is None and meta["trace"] == CF_RAY
    assert {"status", "credits", "model_ms", "cost_usd", "attempts", "idempotency_key", "wall_ms", "recorded_at"} <= set(meta)
    replayed = Gateway("replay", tmp_path / "rec")
    replayed.call("injection", {"n": 1}, "guard cover note")
    assert replayed.audit[0]["request_id"] is None and replayed.audit[0]["trace"] == CF_RAY


def test_a_real_request_id_header_is_reported_as_one():
    gw = Gateway("live", client=Stub(headers={"cf-ray": CF_RAY, "X-Request-Id": "req_123"}))
    gw.call("decide", {"n": 1}, "decide 9")
    assert (gw.audit[0]["request_id"], gw.audit[0]["trace"]) == ("req_123", CF_RAY)
    gw = Gateway("live", client=Stub(headers={"clocked-request-id": "req_9"}))
    gw.call("decide", {"n": 1}, "decide 9")
    assert (gw.audit[0]["request_id"], gw.audit[0]["trace"]) == ("req_9", None)
    gw = Gateway("live", client=Stub(headers={}))
    gw.call("decide", {"n": 1}, "decide 9")
    assert (gw.audit[0]["request_id"], gw.audit[0]["trace"]) == (None, None)


def test_older_recordings_keep_request_id_and_gain_trace():
    """They stored the cf-ray under request_id and have no trace: both readers must find it."""
    assert audit_ids({"request_id": CF_RAY}) == (CF_RAY, CF_RAY)
    assert audit_ids({"request_id": "req_123"}) == ("req_123", None)
    assert audit_ids({"request_id": None, "trace": CF_RAY}) == (None, CF_RAY)
    assert audit_ids({}) == (None, None)
    if not RECORDED:
        pytest.skip("no recordings/")
    # a recording made before `trace` existed (cf-ray stored as request_id) and one made after must both replay
    recs = [(f.parent, json.loads(f.read_text())) for d in RECORDED for f in sorted((REC / d).glob("*.json"))[:3]]
    old = next(((d, r) for d, r in recs if "trace" not in r["meta"]), None)
    new = next(((d, r) for d, r in recs if r["meta"].get("trace")), None)
    for found in (old, new):
        if found is None:
            continue
        folder, rec = found
        gw = Gateway("replay", folder)
        gw.call(BY_PATH[rec["endpoint"]], rec["request"], rec["label"])
        row = gw.audit[0]
        assert set(row) == AUDIT_KEYS
        assert row["trace"] == (rec["meta"].get("trace") or rec["meta"].get("request_id"))


def test_unknown_endpoint_and_mode_are_refused():
    with pytest.raises(ValueError):
        Gateway("online")
    with pytest.raises(ValueError):
        Gateway("live", client=Stub()).call("summarise", {}, "x")


def test_no_key_and_no_client_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("CLOCKED_API_KEY")
    e = attempt(Gateway("live"), "compare", {"n": 1})
    assert e.kind == "no_key"
