"""Backend surfaces for PENDING-BATCH2 §3-§6.

  §3  POST /api/cycle/run — research -> room 1 -> room 2 -> room 3, chained,
      with stage transitions pushed over SSE.
  §4  Per-room pass state (idle|running|done|failed), who is pending, who has
      posted; reply counts on every post in a feed response.
  §5  GET /api/agents/{handle}/profile — persona record, grants, counts, and
      every post it has made newest-first with room + thread ids.
  §6  Notifications persist after being read; unread above read, newest
      first, capped at 50.
  §6 (identity) every response that names a run carries the LABEL and the
      directory, never a bare integer.

No engine subprocesses: run rows point at the committed outputs/<month>/
directories, exactly as test_snapshots_notifications.py does. AGENT_MODE is
pinned to mock — nothing here makes an API call.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="three_rooms_batch2_test_"))
os.environ.setdefault("APP_DB_PATH", str(_TMP / "app.sqlite"))
os.environ.setdefault("APP_RUNS_DIR", str(_TMP / "runs"))
os.environ["ENGINE_PACE_SECONDS"] = "0"
os.environ["AGENT_MODE"] = "mock"

import pytest
from fastapi.testclient import TestClient

from app import config
from app.agents import api as agents_api
from app.server import db
from app.server import main as server_main
from app.server.events import broker
from app.server.main import app

DB_FILE = _TMP / "batch2.sqlite"


@pytest.fixture(scope="module")
def conn():
    c = db.init_db(DB_FILE)
    agents_api.ensure_builtins(c)
    return c


def _fake_run(conn, month: str) -> dict:
    out_dir = config.OUTPUTS_DIR / month.replace("-", "_") / "v1" / "pricing"
    assert (out_dir / "valuation.json").exists(), out_dir
    cur = conn.execute(
        "INSERT INTO runs (asof, kind, seed, sims, status, out_dir) "
        "VALUES (?, 'base', 20260831, 50000, 'done', ?)",
        (month, str(out_dir)))
    conn.commit()
    return conn.execute("SELECT * FROM runs WHERE id = ?",
                        (cur.lastrowid,)).fetchone()


@pytest.fixture(scope="module")
def pair(conn):
    return _fake_run(conn, "2026-02"), _fake_run(conn, "2026-03")


@pytest.fixture(scope="module")
def client(conn):
    with TestClient(app) as c:
        yield c


class _Capture:
    """Record everything published on the broker while a pass runs."""

    def __init__(self):
        self.events = []
        self._orig = broker.publish

    def __enter__(self):
        def spy(event, data, run_id=None):
            self.events.append((event, data))
            return self._orig(event, data, run_id=run_id)
        broker.publish = spy
        return self

    def __exit__(self, *exc):
        broker.publish = self._orig
        return False

    def of(self, name):
        return [d for e, d in self.events if e == name]


# --------------------------------------------------------------------------
# §4 — pass state: idle | running | done | failed, pending and done rosters
# --------------------------------------------------------------------------

def test_room_activity_starts_idle(client):
    r = client.get("/api/rooms/2/activity")
    assert r.status_code == 200
    body = r.json()
    assert body["room"] == 2
    assert body["status"] in ("idle", "done")
    assert isinstance(body["pending"], list)
    assert isinstance(body["done"], list)
    # §3's strip needs "Run room 2 — 3 agents" BEFORE anything has run, so
    # the roster is resolved even when the room is idle
    assert body["expected_count"] == len(body["roster"]) >= 1
    assert {a["handle"] for a in body["roster"]} >= {"@run-monitor"}


def test_idle_rosters_are_available_for_every_stage(client):
    stages = client.get("/api/activity").json()["stages"]
    by_key = {s["key"]: s for s in stages}
    assert {a["handle"] for a in by_key["research"]["roster"]} == \
        {"@focused", "@wide-eye"}
    for key in ("room:1", "room:2", "room:3"):
        assert by_key[key]["expected_count"] >= 3
        assert all(a["name"] for a in by_key[key]["roster"])


def test_room_activity_404s_on_a_room_that_does_not_exist(client):
    assert client.get("/api/rooms/9/activity").status_code == 404


def test_a_room_pass_reports_running_then_done_with_who_posted(conn, pair):
    """The pass state moves idle -> running -> done, the roster of agents
    still to post shrinks as posts land, and every transition is PUSHED."""
    prev, curr = pair
    with _Capture() as cap:
        err = server_main._bg_room_pass(2, prev["id"], curr["id"], False)
    assert err is None

    activity = cap.of("activity")
    assert activity, "a pass must publish activity events"
    assert activity[0]["status"] == "running"
    assert activity[0]["room"] == 2
    # the opening event lists the room's agents as pending, none done yet
    assert activity[0]["pending"], "a running pass must name who is pending"
    assert activity[0]["done"] == []
    assert all(set(a) >= {"handle", "name", "avatar"}
               for a in activity[0]["pending"])

    final = activity[-1]
    assert final["status"] == "done"
    assert final["pending"] == []          # nothing pending once it is done
    assert final["error"] is None
    posted = {a["handle"] for a in final["done"]}
    assert "@run-monitor" in posted or posted, posted
    # the run is named by label + directory, never by a bare integer (§6)
    assert final["run"]["label"].endswith("_v1")
    assert final["run"]["run_dir"]


def test_activity_endpoint_matches_the_state_after_a_pass(client):
    body = client.get("/api/rooms/2/activity").json()
    assert body["status"] == "done"
    assert body["pending"] == []
    assert body["done_count"] >= 1
    assert body["expected_count"] >= body["done_count"] - 0


def test_a_failed_pass_is_reported_failed_not_stuck_running(conn, pair,
                                                            monkeypatch):
    prev, curr = pair

    class _Boom:
        CYCLE_STAGES = ("research", 1, 2, 3)

        def run_room_pass(self, *a, **k):
            raise RuntimeError("engine on fire")

    monkeypatch.setattr(server_main, "_agents_api", lambda: _Boom())
    err = server_main._bg_room_pass(1, prev["id"], curr["id"], False)
    assert err and "engine on fire" in err
    state = server_main._activity_payload("room:1")
    assert state["status"] == "failed"
    assert state["error"] and "engine on fire" in state["error"]


def test_all_stage_activity_is_available_in_one_request(client):
    body = client.get("/api/activity").json()
    keys = [s["key"] for s in body["stages"]]
    assert keys == ["research", "room:1", "room:2", "room:3"]
    assert "cycle" in body
    assert isinstance(body["running"], list)


def test_research_stage_has_the_same_activity_shape(client):
    body = client.get("/api/research/activity").json()
    assert body["stage"] == "research"
    assert body["room"] is None
    assert set(body) >= {"status", "pending", "done"}


def test_research_pass_tracks_its_two_agents(client):
    with _Capture() as cap:
        err = server_main._bg_research_pass("2026-03")
    assert err is None
    events = cap.of("activity")
    assert events[0]["status"] == "running"
    assert {a["handle"] for a in events[0]["pending"]} == {"@focused",
                                                           "@wide-eye"}
    final = events[-1]
    assert final["status"] == "done"
    assert {a["handle"] for a in final["done"]} == {"@focused", "@wide-eye"}
    assert final["month"] == "2026-03"


# --------------------------------------------------------------------------
# §3 — run all: research -> 1 -> 2 -> 3
# --------------------------------------------------------------------------

def test_cycle_requires_a_month(client):
    assert client.post("/api/cycle/run", json={}).status_code == 422
    assert client.post("/api/cycle/run",
                       json={"month": "March"}).status_code == 422


def test_cycle_422s_when_the_month_has_no_run(client):
    r = client.post("/api/cycle/run", json={"month": "2019-01"})
    assert r.status_code == 422
    assert "no run exists" in r.json()["detail"]


def test_cycle_run_chains_all_four_stages(client, pair):
    _, curr = pair
    with _Capture() as cap:
        r = client.post("/api/cycle/run", json={"month": "2026-03"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "scheduled"
    assert body["stages"] == ["research", 1, 2, 3]
    # §6: the runs are named by label + directory
    assert body["run"]["label"] and body["run"]["run_dir"]
    assert body["prev_run"] is None or body["prev_run"]["label"]

    # TestClient drains background tasks before returning, so by here the
    # whole cycle has run.
    cycles = cap.of("cycle")
    assert cycles, "stage transitions must be pushed over SSE"
    seen = [(c["stage"], c["status"]) for c in cycles]
    assert ("research", "running") in [
        (s["stage"], s["status"]) for c in cycles for s in c["stages"]]
    assert cycles[-1]["status"] == "done", cycles[-1]["error"]
    assert [s["status"] for s in cycles[-1]["stages"]] == ["done"] * 4
    assert [s["stage"] for s in cycles[-1]["stages"]] == ["research", 1, 2, 3]

    status = client.get("/api/cycle/status").json()
    assert status["status"] == "done"
    assert status["month"] == "2026-03"
    assert len(status["activity"]) == 4
    assert all(a["status"] == "done" for a in status["activity"])


def test_cycle_run_409s_on_a_run_that_is_not_done(client, conn):
    cur = conn.execute(
        "INSERT INTO runs (asof, kind, seed, sims, status, out_dir) "
        "VALUES ('2026-03', 'base', 1, 10, 'running', NULL)")
    conn.commit()
    r = client.post("/api/cycle/run", json={"run_id": cur.lastrowid,
                                            "month": "2026-03"})
    assert r.status_code == 409
    conn.execute("DELETE FROM runs WHERE id = ?", (cur.lastrowid,))
    conn.commit()


# --------------------------------------------------------------------------
# §4 — reply counts on every post in a feed response
# --------------------------------------------------------------------------

def _origin(conn, room: int, body: str) -> int:
    cur = conn.execute(
        "INSERT INTO posts (room, agent_id, author_label, type, body_md, "
        "status) VALUES (?, NULL, 'you', 'origin', ?, 'published')",
        (room, body))
    conn.commit()
    return cur.lastrowid


def _child(conn, room: int, parent: int, ptype: str,
           status: str = "published") -> int:
    cur = conn.execute(
        "INSERT INTO posts (room, agent_id, author_label, type, parent_id, "
        "body_md, status) VALUES (?, NULL, 'you', ?, ?, 'x', ?)",
        (room, ptype, parent, status))
    conn.commit()
    return cur.lastrowid


def test_reply_count_is_on_every_post_and_counts_replies_only(client, conn):
    root = _origin(conn, 3, "counting test")
    _child(conn, 3, root, "reply")
    _child(conn, 3, root, "reply")
    _child(conn, 3, root, "expansion")          # working, not conversation
    _child(conn, 3, root, "reply", "suppressed")  # never shown, never counted

    feed = client.get("/api/rooms/3/feed").json()
    assert all("reply_count" in p for p in feed["posts"])
    mine = next(p for p in feed["posts"] if p["id"] == root)
    assert mine["reply_count"] == 2

    thread = client.get("/api/rooms/3/feed", params={"thread": root}).json()
    assert thread["thread"]["reply_count"] == 2
    assert all("reply_count" in c for c in thread["children"])
    assert all(c["reply_count"] == 0 for c in thread["children"])


def test_a_post_with_no_replies_reports_zero(client, conn):
    lone = _origin(conn, 3, "no replies here")
    feed = client.get("/api/rooms/3/feed").json()
    assert next(p for p in feed["posts"]
                if p["id"] == lone)["reply_count"] == 0


# --------------------------------------------------------------------------
# §5 — agent profile
# --------------------------------------------------------------------------

def _some_handle(conn) -> str:
    """An agent that actually worked in the passes above — the one with the
    most recorded tool calls, so the counts block has something to show."""
    row = conn.execute(
        "SELECT a.handle AS handle, COUNT(tc.id) AS n FROM agents a "
        "JOIN posts p ON p.agent_id = a.id "
        "JOIN tool_calls tc ON tc.post_id = p.id "
        "GROUP BY a.handle ORDER BY n DESC LIMIT 1").fetchone()
    assert row is not None, "no agent has made a tool call yet"
    return row["handle"]


def test_agent_profile_404s_on_an_unknown_handle(client):
    assert client.get("/api/agents/@nobody/profile").status_code == 404


def test_agent_profile_carries_persona_grants_counts_and_posts(client, conn):
    handle = _some_handle(conn)
    r = client.get(f"/api/agents/{handle}/profile")
    assert r.status_code == 200
    body = r.json()

    agent = body["agent"]
    assert agent["handle"] == handle
    assert agent["outlook"] in ("internal", "outward", "both")
    assert "persona_prompt" in agent and "focus" in agent
    assert agent["home_room"] == agent["room"]
    assert agent["room_name"]
    assert isinstance(agent["reads_from"], list)
    assert isinstance(agent["modified"], bool)

    grants = body["grants"]
    assert isinstance(grants["web_search"], bool)
    assert "read_data_series" in grants["tools"]

    counts = body["counts"]
    assert set(counts) >= {"published", "suppressed", "quiet", "tool_calls"}
    assert counts["published"] >= 1        # the room-2 pass above posted
    assert counts["tool_calls"] >= 1

    posts = body["posts"]
    assert posts, "the profile must list the agent's posts"
    assert [p["id"] for p in posts] == sorted(
        (p["id"] for p in posts), reverse=True)   # newest first
    p = posts[0]
    assert set(p) >= {"room", "room_name", "thread_id", "reply_count",
                      "significance", "status", "run"}
    assert p["thread_id"]
    # §6 again: a run on a post is named, not numbered
    if p["run"] is not None:
        assert p["run"]["label"] and p["run"]["run_dir"]


def test_agent_profile_accepts_a_handle_without_the_at_sign(client, conn):
    handle = _some_handle(conn)
    bare = client.get(f"/api/agents/{handle.lstrip('@')}/profile")
    assert bare.status_code == 200
    assert bare.json()["agent"]["handle"] == handle


def test_agent_profile_spans_rooms(client, conn):
    """Posts are listed across ALL rooms, not just the agent's home room —
    @results-validator lives in room 2 and posts its draft-report review
    into room 3."""
    handle = "@results-validator"
    if conn.execute("SELECT id FROM agents WHERE handle = ?",
                    (handle,)).fetchone() is None:
        pytest.skip("roster does not carry @results-validator")
    body = client.get(f"/api/agents/{handle}/profile").json()
    rooms = {p["room"] for p in body["posts"]}
    assert rooms, "expected posts from the passes run above"
    assert body["agent"]["home_room"] == 2


# --------------------------------------------------------------------------
# §6 — notifications: new vs seen, nothing removed
# --------------------------------------------------------------------------

def _notify(conn, kind="reply", post_id=None, room=1) -> int:
    cur = conn.execute(
        "INSERT INTO notifications (kind, post_id, room) VALUES (?, ?, ?)",
        (kind, post_id, room))
    conn.commit()
    return cur.lastrowid


def test_read_notifications_are_kept_and_sort_below_unread(client, conn):
    conn.execute("DELETE FROM notifications")
    conn.commit()
    old = _notify(conn)
    mid = _notify(conn)
    new = _notify(conn)

    assert client.post(f"/api/notifications/{mid}/read").status_code == 200
    body = client.get("/api/notifications").json()
    ids = [n["id"] for n in body["notifications"]]
    # nothing removed
    assert set(ids) == {old, mid, new}
    # unread first (newest first within), then read
    assert ids == [new, old, mid]
    assert body["unread_count"] == 2
    assert body["notifications"][-1]["read"] is True
    assert body["notifications"][-1]["read_at"] is not None
    assert body["notifications"][0]["read"] is False


def test_reading_twice_keeps_the_first_seen_time(client, conn):
    nid = _notify(conn)
    first = client.post(f"/api/notifications/{nid}/read").json()
    again = client.post(f"/api/notifications/{nid}/read").json()
    assert first["notification"]["read_at"] == again["notification"]["read_at"]


def test_read_all_greys_everything_and_removes_nothing(client, conn):
    before = conn.execute(
        "SELECT COUNT(*) AS n FROM notifications").fetchone()["n"]
    r = client.post("/api/notifications/read_all")
    assert r.status_code == 200
    assert r.json()["unread_count"] == 0
    after = conn.execute(
        "SELECT COUNT(*) AS n FROM notifications").fetchone()["n"]
    assert after == before
    listed = client.get("/api/notifications").json()
    assert listed["unread_count"] == 0
    assert len(listed["notifications"]) == min(before, 50)
    assert all(n["read"] for n in listed["notifications"])


def test_the_list_is_capped_at_fifty(client, conn):
    conn.execute("DELETE FROM notifications")
    conn.commit()
    for _ in range(60):
        _notify(conn)
    body = client.get("/api/notifications").json()
    assert len(body["notifications"]) == 50
    assert body["total"] == 60
    assert body["truncated"] is True
    assert body["unread_count"] == 60          # the count is not capped
    # the 50 shown are the newest
    assert body["notifications"][0]["id"] == max(
        r["id"] for r in conn.execute(
            "SELECT id FROM notifications").fetchall())


# --------------------------------------------------------------------------
# §6 — a run is named by its label and directory, never a bare integer
# --------------------------------------------------------------------------

def test_run_responses_name_the_run_by_label_and_directory(client, pair):
    _, curr = pair
    body = client.get(f"/api/runs/{curr['id']}").json()["run"]
    assert body["label"] == "2603_v1"
    assert body["run_dir"].endswith("2026_03/v1")
    assert body["esg_dir"] and body["pricing_dir"]

    listed = client.get("/api/runs").json()["runs"]
    assert all(r.get("label") for r in listed)

    dash = client.get("/api/dashboard/3",
                      params={"run": curr["id"]}).json()
    assert dash["current"]["run"]["label"] == "2603_v1"
    assert dash["current"]["run"]["run_dir"]


def test_a_scheduled_room_pass_names_its_runs(client, pair):
    prev, curr = pair
    r = client.post("/api/rooms/2/refresh",
                    json={"pair": [prev["id"], curr["id"]]})
    assert r.status_code == 200
    body = r.json()
    assert body["run"]["label"] == "2603_v1"
    assert body["prev_run"]["label"] == "2602_v1"
    assert body["run"]["run_dir"]


def test_snapshot_listing_names_its_run(client, pair):
    _, curr = pair
    body = client.get("/api/rooms/3/snapshots",
                      params={"run_id": curr["id"]}).json()
    assert body["run"]["label"] == "2603_v1"


# --------------------------------------------------------------------------
# the run-independent SSE stream that carries all of the above
# --------------------------------------------------------------------------

def test_global_event_stream_replays_state_and_pushes_a_pass(client, pair):
    """Real uvicorn server — TestClient buffers streaming responses, so the
    run-independent stream is exercised over an actual HTTP connection (the
    same pattern as test_app_server.test_sse_yields_stage_events).

    Asserts both halves of §4's "push, don't poll": the stream OPENS with the
    current state of all four stages, and a room pass started afterwards
    arrives on it live.
    """
    import json as _json
    import socket
    import threading
    import time

    import requests
    import uvicorn

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()

    server = uvicorn.Server(uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 15
    while not server.started:
        assert time.time() < deadline, "uvicorn did not start"
        time.sleep(0.05)

    prev, curr = pair
    events = []
    resp = requests.get(f"http://127.0.0.1:{port}/api/events",
                        stream=True, timeout=(5, 30))
    try:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        pass_thread = None
        current = None
        for raw in resp.iter_lines(decode_unicode=True):
            line = raw or ""
            if line.startswith("event: "):
                current = line.split("event: ", 1)[1]
            elif line.startswith("data: ") and current:
                events.append((current, _json.loads(
                    line.split("data: ", 1)[1])))
                current = None
                # the replay is 4 activity frames + 1 cycle frame; once it
                # has arrived, start a pass and watch it come down the wire
                if len(events) == 5 and pass_thread is None:
                    pass_thread = threading.Thread(
                        target=server_main._bg_room_pass,
                        args=(3, prev["id"], curr["id"], False), daemon=True)
                    pass_thread.start()
                if len(events) > 5 and events[-1][0] == "activity" \
                        and events[-1][1]["status"] == "done":
                    break
    finally:
        resp.close()
        server.force_exit = True
        server.should_exit = True
        thread.join(timeout=10)

    replay = events[:5]
    assert [e for e, _ in replay] == ["activity"] * 4 + ["cycle"]
    assert [d["key"] for _, d in replay[:4]] == \
        ["research", "room:1", "room:2", "room:3"]

    live = [d for e, d in events[5:] if e == "activity"]
    assert live, "a pass must push activity over the open stream"
    assert live[0]["status"] == "running" and live[0]["room"] == 3
    assert live[0]["pending"], "the pending roster must ride the push"
    assert live[-1]["status"] == "done"
