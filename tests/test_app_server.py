"""App server tests (SPEC-APP sections 1, 3, 7).

These must pass with or without app/agents present: every agents import in
the server is guarded, so passes/narration simply no-op when the package is
absent. Engine invocations are real (base 2026-03, sims 2000 for speed).
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import tempfile
from pathlib import Path

# Point the app at temp storage and zero the watchability pacing BEFORE any
# app import (app.config reads these at import / per-call).
_TMP = Path(tempfile.mkdtemp(prefix="three_rooms_test_"))
os.environ["APP_DB_PATH"] = str(_TMP / "app.sqlite")
os.environ["APP_RUNS_DIR"] = str(_TMP / "runs")
os.environ["ENGINE_PACE_SECONDS"] = "0"

import pytest
import yaml
from fastapi.testclient import TestClient

from app import config
from app.server import db, engine_bridge
from app.server.main import app

STAGES = ("setup", "esg", "pricing", "validation")


@pytest.fixture(scope="module")
def conn():
    return db.init_db(os.environ["APP_DB_PATH"])


@pytest.fixture(scope="module")
def client(conn):
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def base_run(conn):
    """Real tiny engine invocation: base 2026-03 with sims=2000."""
    run = engine_bridge.create_run(asof="2026-03", kind="base", sims=2000)
    assert run["status"] == "queued"
    done = engine_bridge.execute_run(run["id"])
    assert done["status"] == "done", done
    return done


# --- schema ----------------------------------------------------------------

def test_schema_init():
    c = db.init_db(_TMP / "schema_only.sqlite")
    tables = {r["name"] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()}
    assert {"runs", "stage_events", "agents", "posts", "tool_calls",
            "gates"} <= tables

    cols = {r["name"] for r in c.execute("PRAGMA table_info(runs)").fetchall()}
    assert cols == {"id", "asof", "kind", "parent_run_id", "seed", "sims",
                    "status", "out_dir", "adjustments_json", "started_at",
                    "finished_at"}
    cols = {r["name"] for r in c.execute("PRAGMA table_info(posts)").fetchall()}
    assert {"room", "agent_id", "author_label", "type", "parent_id",
            "body_md", "claims_json", "status", "suppression_reason",
            "run_id"} <= cols

    # dict row factory
    row = c.execute("SELECT 1 AS one").fetchone()
    assert row == {"one": 1}

    # enum constraints hold
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("INSERT INTO runs (asof, kind) VALUES ('2026-03', 'weird')")
    # handle GLOBALLY unique across rooms (SPEC-APP section 3: @-mentions
    # are never ambiguous)
    c.execute("INSERT INTO agents (room, handle) VALUES (1, '@x')")
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("INSERT INTO agents (room, handle) VALUES (2, '@x')")
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("INSERT INTO agents (room, handle) VALUES (1, '@x')")
    # avatar_json column present (SPEC-APP section 3)
    cols = {r["name"] for r in
            c.execute("PRAGMA table_info(agents)").fetchall()}
    assert "avatar_json" in cols
    # re-point the module at the main test db for the other tests
    db.init_db(os.environ["APP_DB_PATH"])


# --- run creation + engine bridge ------------------------------------------

def test_run_creation_executes_engine(base_run, conn):
    # PENDING-BATCH2 section 1: outputs/<YYYY_MM>/vN/{inputs,esg,pricing}.
    # `out_dir` is the pricing side; the stage log and inputs/ belong to the
    # run as a whole and sit at the vN root.
    out = Path(base_run["out_dir"])
    root = out.parent
    assert out.name == "pricing"
    # The identity a human sees is the label and the directory; the integer
    # id stays inside the DB and must appear in neither.
    version = out.parent.name                       # 'vN'
    assert re.fullmatch(r"v\d+", version)
    assert base_run["label"] == f"2603_{version}"
    assert base_run["run_dir"].endswith(f"2026_03/{version}")
    assert base_run["version"] == int(version[1:])
    assert str(base_run["id"]) not in Path(base_run["run_dir"]).parts
    assert base_run["kind"] == "base"
    assert base_run["sims"] == 2000
    for fn in ("valuation.json", "var_aggregate.json",
               "var_standalone_factors.json", "var_standalone_positions.csv",
               "sim_pnl_sample.csv", "sim_pnl_positions.npy",
               "sim_surplus.npy"):
        assert (out / fn).exists(), fn
    for fn in ("assumptions_used.yaml", "sim_factors.npy", "sim_index.json"):
        assert (root / "esg" / fn).exists(), fn
        assert not (out / fn).exists(), f"{fn} must not stay on the pricing side"
    assert (root / "stage_log.jsonl").exists()
    assert (root / "inputs" / "manifest.json").exists()

    val = json.loads((out / "valuation.json").read_text(encoding="utf-8"))
    assert val["meta"]["n_sims"] == 2000
    assert val["asset_total_gbp"] > 0

    events = conn.execute(
        "SELECT stage, status FROM stage_events WHERE run_id = ? ORDER BY id",
        (base_run["id"],)).fetchall()
    assert [e["stage"] for e in events] == [s for s in STAGES for _ in (0, 1)]
    assert all(e["status"] in ("started", "done") for e in events)
    assert len(events) == 8

    man = engine_bridge.read_manifest(base_run["out_dir"])
    assert man["assumptions_path"].endswith("2026-03.yaml")
    assert man["seeded"] is False


def test_runs_api(client, base_run):
    r = client.get("/api/runs")
    assert r.status_code == 200
    ids = [run["id"] for run in r.json()["runs"]]
    assert base_run["id"] in ids
    r = client.get(f"/api/runs/{base_run['id']}")
    assert r.status_code == 200
    body = r.json()
    assert body["run"]["status"] == "done"
    assert len(body["stage_events"]) == 8


# --- gate flow -------------------------------------------------------------

def test_gate_approve_triggers_rerun_with_derived_yaml(client, base_run, conn):
    adjustments = {"vols.gbp_swap.10": 0.0099}
    cur = conn.execute(
        "INSERT INTO gates (run_id, adjustments_json, rationale) "
        "VALUES (?, ?, ?)",
        (base_run["id"], json.dumps(adjustments),
         "test: correct the 10y swap vol"))
    conn.commit()
    gate_id = cur.lastrowid

    # reject requires a named human
    r = client.post(f"/api/gates/{gate_id}/approve", json={})
    assert r.status_code == 422

    r = client.post(f"/api/gates/{gate_id}/approve",
                    json={"decided_by": "lachlan"})
    assert r.status_code == 200, r.text
    gate = r.json()["gate"]
    assert gate["status"] == "approved"
    assert gate["decided_by"] == "lachlan"
    rerun_id = gate["result_run_id"]
    assert rerun_id

    # TestClient runs background tasks before returning, so the rerun is done.
    rerun = engine_bridge.get_run(rerun_id)
    assert rerun["status"] == "done"
    assert rerun["kind"] == "rerun"
    assert rerun["parent_run_id"] == base_run["id"]  # lineage
    assert json.loads(rerun["adjustments_json"]) == adjustments

    # Derived YAML under the rerun's own inputs/, with the override applied;
    # the committed assumptions file is untouched.
    derived = list((Path(rerun["out_dir"]).parent / "inputs")
                   .glob("assumptions_*_derived.yaml"))
    assert len(derived) == 1
    doc = yaml.safe_load(derived[0].read_text(encoding="utf-8"))
    assert doc["vols"]["gbp_swap"][10] == 0.0099
    committed = yaml.safe_load(
        (config.ASSUMPTIONS_DIR / "2026-03.yaml").read_text(encoding="utf-8"))
    assert committed["vols"]["gbp_swap"][10] != 0.0099

    # The rerun actually used the derived file.
    man = engine_bridge.read_manifest(rerun["out_dir"])
    assert man["assumptions_path"] == str(derived[0])
    val = json.loads((Path(rerun["out_dir"]) / "valuation.json")
                     .read_text(encoding="utf-8"))
    assert val["meta"]["assumptions_path"] == str(derived[0])

    # A decided gate cannot be re-decided.
    r = client.post(f"/api/gates/{gate_id}/approve",
                    json={"decided_by": "lachlan"})
    assert r.status_code == 409


def test_gate_reject(client, base_run, conn):
    cur = conn.execute(
        "INSERT INTO gates (run_id, adjustments_json) VALUES (?, ?)",
        (base_run["id"], json.dumps({"spreads.HY": 0.05})))
    conn.commit()
    gate_id = cur.lastrowid
    r = client.post(f"/api/gates/{gate_id}/reject", json={"decided_by": "lachlan"})
    assert r.status_code == 200
    assert r.json()["gate"]["status"] == "rejected"
    # nothing executed
    assert r.json()["gate"]["result_run_id"] is None


# --- SSE -------------------------------------------------------------------

def test_sse_yields_stage_events(base_run):
    """Real uvicorn server: TestClient buffers streaming responses, so SSE is
    exercised over an actual HTTP connection (the production path)."""
    import socket
    import threading
    import time

    import requests
    import uvicorn

    # free port
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

    events = []
    url = f"http://127.0.0.1:{port}/api/runs/{base_run['id']}/events"
    resp = requests.get(url, stream=True, timeout=(5, 30))
    try:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        current = None
        for raw in resp.iter_lines(decode_unicode=True):
            line = raw or ""
            if line.startswith("event: "):
                current = line.split("event: ", 1)[1]
            elif line.startswith("data: ") and current:
                events.append((current, json.loads(line.split("data: ", 1)[1])))
                if current == "run_status":
                    break
                current = None
    finally:
        resp.close()
        server.force_exit = True
        server.should_exit = True
        thread.join(timeout=10)

    stage_events = [d for e, d in events if e == "stage"]
    assert len(stage_events) == 8
    assert [d["stage"] for d in stage_events] == \
        [s for s in STAGES for _ in (0, 1)]
    assert all(d["run_id"] == base_run["id"] for d in stage_events)
    status = [d for e, d in events if e == "run_status"][0]
    assert status["status"] == "done"


# --- rooms / posts / misc --------------------------------------------------

def test_human_post_and_feed(client, base_run):
    r = client.post("/api/rooms/1/posts",
                    json={"body": "Why is the 10y swap vol below the 5y?"})
    assert r.status_code == 200, r.text
    post = r.json()["post"]
    assert post["agent_id"] is None
    assert post["type"] == "origin"
    assert post["status"] == "published"

    r = client.post("/api/rooms/1/posts",
                    json={"body": "Following up.", "parent_id": post["id"]})
    assert r.status_code == 200
    assert r.json()["post"]["type"] == "reply"

    feed = client.get("/api/rooms/1/feed").json()
    assert any(p["id"] == post["id"] for p in feed["posts"])
    thread = client.get(f"/api/rooms/1/feed?thread={post['id']}").json()
    assert thread["thread"]["id"] == post["id"]
    children = thread["children"]
    # The human follow-up reply is in the thread.
    humans = [c for c in children if c["agent_id"] is None]
    assert [c["body_md"] for c in humans] == ["Following up."]
    # With app/agents present each human post draws a bounded agent reply
    # (mock acknowledgement); without the package there are none. Either
    # way the governor's per-thread reply cap holds.
    agent_replies = [c for c in children if c["agent_id"] is not None]
    assert all(c["type"] == "reply" and c["status"] == "published"
               for c in agent_replies)
    assert len(agent_replies) <= config.MAX_REPLIES_PER_THREAD

    assert client.post("/api/rooms/1/posts", json={"body": "  "}).status_code == 422
    assert client.get("/api/rooms/9/feed").status_code == 404


def test_refresh_without_agents_package(client, base_run):
    """No app/agents yet -> refresh reports 503 rather than silently no-op."""
    try:
        import app.agents.api  # noqa: F401
        pytest.skip("agents package present; 503 path not applicable")
    except ImportError:
        pass
    r = client.post("/api/rooms/1/refresh",
                    json={"run_id": base_run["id"], "seeded": False})
    assert r.status_code == 503


def test_dashboard_reads_output_files(client, base_run):
    r = client.get(f"/api/dashboard/3?run={base_run['id']}")
    assert r.status_code == 200
    cur = r.json()["current"]
    assert cur["valuation"]["surplus_gbp"] == pytest.approx(
        cur["valuation"]["asset_total_gbp"] - cur["valuation"]["liability_pv_gbp"])
    assert cur["var_aggregate"]["aggregate_var_gbp"] > 0
    assert len(cur["top_positions_by_var"]) == 10
    # room 1 carries the assumptions actually used
    r = client.get(f"/api/dashboard/1?run={base_run['id']}")
    assert r.json()["assumptions"]["curves"]["gbp_swap"]

    r = client.get("/api/dashboard/2?run=999999")
    assert r.status_code == 404


def test_config_and_scorecard(client):
    cfg = client.get("/api/config").json()
    assert cfg["agent_mode"] == "mock"
    assert cfg["limits"]["MAX_TOOL_CALLS_PER_POST"] == 12
    assert cfg["limits"]["ENGINE_PACE_SECONDS"] == 0.0
    assert "ANTHROPIC_API_KEY" not in json.dumps(cfg)

    sc = client.get("/api/scorecard").json()
    assert "suppression_rate" in sc
    assert sc["detection"] is None  # no seeded run active


def test_agents_interface_wiring(client, base_run, conn, monkeypatch):
    """Stub app.agents.api per the shared interface and check the server
    calls it: persona seeding, refresh -> run_room_pass, human post ->
    handle_human_post, stage event -> stage_narrator_post."""
    import sys
    import types

    calls = {"pass": [], "human": [], "narrate": []}

    def _insert_agent_post(room, body, run_id=None):
        cur = conn.execute(
            "INSERT INTO posts (room, agent_id, author_label, type, body_md, "
            "status, run_id) VALUES (?, NULL, '@stub', 'origin', ?, "
            "'published', ?)", (room, body, run_id))
        conn.commit()
        return cur.lastrowid

    api_mod = types.ModuleType("app.agents.api")
    api_mod.builtin_personas = lambda: [
        {"room": 1, "handle": "@stub-check", "name": "Stub", "focus": "t",
         "persona_prompt": "p"}]

    def run_room_pass(room, prev_run_id, curr_run_id, seeded):
        calls["pass"].append((room, prev_run_id, curr_run_id, seeded))
        return [_insert_agent_post(room, "pass post", curr_run_id)]

    def handle_human_post(room, post_id):
        calls["human"].append((room, post_id))
        return [_insert_agent_post(room, f"re: {post_id}")]

    def stage_narrator_post(run_id, stage_event):
        calls["narrate"].append((run_id, stage_event["stage"],
                                 stage_event["status"]))
        return _insert_agent_post(2, f"stage {stage_event['stage']}", run_id)

    api_mod.run_room_pass = run_room_pass
    api_mod.handle_human_post = handle_human_post
    api_mod.stage_narrator_post = stage_narrator_post
    agents_pkg = types.ModuleType("app.agents")
    agents_pkg.api = api_mod
    monkeypatch.setitem(sys.modules, "app.agents", agents_pkg)
    monkeypatch.setitem(sys.modules, "app.agents.api", api_mod)

    # persona seeding (server startup path), idempotent
    from app.server.main import seed_builtin_agents
    assert seed_builtin_agents() == 1
    assert seed_builtin_agents() == 0
    row = conn.execute("SELECT * FROM agents WHERE handle = '@stub-check'"
                       ).fetchone()
    assert row["builtin"] == 1 and row["room"] == 1

    # refresh -> run_room_pass with the pair
    r = client.post("/api/rooms/1/refresh",
                    json={"pair": [base_run["id"], base_run["id"]],
                          "seeded": True})
    assert r.status_code == 200
    assert calls["pass"] == [(1, base_run["id"], base_run["id"], True)]

    # human post -> handle_human_post
    r = client.post("/api/rooms/3/posts", json={"body": "what moved?"})
    assert r.status_code == 200
    assert calls["human"] == [(3, r.json()["post"]["id"])]

    # stage event emission -> narrator called with the recorded event
    ev = {"stage": "esg", "status": "done", "ts": "2026-08-28T00:00:00+00:00"}
    engine_bridge._emit_stage_event(base_run["id"], ev, api_mod)
    assert calls["narrate"] == [(base_run["id"], "esg", "done")]


def test_agent_builder(client):
    r = client.post("/api/agents/1", json={"handle": "custom-check",
                                           "name": "Custom Check",
                                           "focus": "spread floors"})
    assert r.status_code == 200
    agent = r.json()["agent"]
    assert agent["handle"] == "@custom-check"
    assert agent["builtin"] == 0
    # section 8.1 default avatar assigned at creation and stored
    avatar = json.loads(agent["avatar_json"])
    assert avatar["glyph"] == "CC"
    assert avatar["bg"].startswith("#") and avatar["fg"].startswith("#")
    assert avatar["accessory"] == "none"
    assert any(a["id"] == agent["id"]
               for a in client.get("/api/agents/1").json()["agents"])
    # duplicate handle rejected in the same room AND across rooms (global)
    assert client.post("/api/agents/1",
                       json={"handle": "custom-check"}).status_code == 409
    assert client.post("/api/agents/3",
                       json={"handle": "custom-check"}).status_code == 409
