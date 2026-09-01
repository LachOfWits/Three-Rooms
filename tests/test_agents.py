"""Agent-layer tests (SPEC-APP sections 0, 2, 4, 5, 9).

Section 9's pytest bullet: citation binding, suppression, quarantine, gate
flow, mock pass end-to-end. Engine invocations are real (sims=2000 for
speed); no API calls anywhere (AGENT_MODE pinned to mock).
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

# Point the app at temp storage BEFORE any app import (same pattern as
# test_app_server.py; alphabetical collection runs this module first, so
# app.config binds to these paths — both modules use temp dirs either way).
_TMP = Path(tempfile.mkdtemp(prefix="three_rooms_agents_test_"))
os.environ.setdefault("APP_DB_PATH", str(_TMP / "app.sqlite"))
os.environ.setdefault("APP_RUNS_DIR", str(_TMP / "runs"))
os.environ["ENGINE_PACE_SECONDS"] = "0"
os.environ["AGENT_MODE"] = "mock"  # explicit env wins over any .env

import pytest
import yaml
from fastapi.testclient import TestClient

from app import config
from app.agents import api as agents_api
from app.agents import citation, tools
from app.server import db, engine_bridge
from app.server.main import app

DB_FILE = _TMP / "agents.sqlite"

ROOM1_HANDLES = {"@pre-flight-checks", "@vcv", "@holdings", "@red-team",
                 "@focused", "@story"}
ROOM2_HANDLES = {"@run-monitor", "@results-validator", "@vlad"}
ROOM3_HANDLES = {"@attrib", "@rates-desk", "@credit-desk",
                 "@equity-desk", "@pc-desk", "@wide-eye", "@realist",
                 "@story"}


@pytest.fixture(scope="module")
def conn():
    return db.init_db(DB_FILE)


def _run(asof: str, seeded: bool = False) -> dict:
    kwargs = {}
    if seeded:
        kwargs = {
            "seeded_assumptions_path":
                str(config.SCENARIOS_DIR / "seeded" /
                    "assumptions_2026-03_D1.yaml"),
            "seeded_book_path":
                str(config.SCENARIOS_DIR / "seeded" / "positions_D2.json"),
        }
    run = engine_bridge.create_run(asof=asof, kind="base", sims=2000, **kwargs)
    done = engine_bridge.execute_run(run["id"])
    assert done["status"] == "done", done
    return done


@pytest.fixture(scope="module")
def base_pair(conn):
    return _run("2026-02"), _run("2026-03")


@pytest.fixture(scope="module")
def seeded_run(conn):
    run = _run("2026-03", seeded=True)
    man = engine_bridge.read_manifest(run["out_dir"])
    assert man["seeded"] is True
    return run


def _posts_for(conn, ids):
    marks = ",".join("?" * len(ids))
    return conn.execute(f"SELECT * FROM posts WHERE id IN ({marks}) ORDER BY id",
                        list(ids)).fetchall()


def _tool_calls_for(conn, post_id):
    return conn.execute("SELECT * FROM tool_calls WHERE post_id = ?",
                        (post_id,)).fetchall()


# --- citation binding ------------------------------------------------------

def test_citation_binding_publishes_bound_claim(conn, base_pair):
    agents_api.ensure_builtins(conn)
    s = tools.ToolSession()
    tc_id, res = s.call("verify_claim", left=1234567.0, op="eq",
                        right=1234567.0, tol=0.0)
    agent = conn.execute("SELECT * FROM agents WHERE handle = '@red-team'"
                         ).fetchone()
    pid, ok = agents_api.publish_post(
        room=1, agent_row=agent, body="The figure is £1.23m, verified.",
        claims=[{"text": "£1.23m", "value": 1234567.0, "tool_call_id": tc_id}],
        post_type="origin", session=s)
    assert ok is True
    post = conn.execute("SELECT * FROM posts WHERE id = ?", (pid,)).fetchone()
    assert post["status"] == "published"
    # session tool calls were bound to the post
    assert [t["id"] for t in _tool_calls_for(conn, pid)] == [tc_id]


def test_citation_binding_rejects_wrong_value(conn):
    """A claim citing a tool call whose result does NOT contain the value."""
    s = tools.ToolSession()
    tc_id, _ = s.call("verify_claim", left=100.0, op="eq", right=100.0)
    agent = conn.execute("SELECT * FROM agents WHERE handle = '@red-team'"
                         ).fetchone()
    pid, ok = agents_api.publish_post(
        room=1, agent_row=agent, body="VaR is £999.99m.",
        claims=[{"text": "£999.99m", "value": 999990000.0,
                 "tool_call_id": tc_id}],
        post_type="origin", session=s)
    assert ok is False
    post = conn.execute("SELECT * FROM posts WHERE id = ?", (pid,)).fetchone()
    assert post["status"] == "suppressed"
    assert "not found in tool_call" in post["suppression_reason"]


def test_citation_tolerance_and_whitelists():
    """Unit-level: tokens that do/don't require binding."""
    toks = citation.numeric_tokens(
        "On 2026-03-31 the 10y point (99.5 level, run 4, D1) moved 85.3bp")
    assert [t.text for t in toks] == ["85.3"]
    # 0.5% relative tolerance on values
    assert citation.value_in_result(135.9e6, json.dumps({"v": 135906470.42}))
    assert not citation.value_in_result(140e6, json.dumps({"v": 135906470.42}))


# --- suppression -----------------------------------------------------------

def test_unbound_numeric_body_is_suppressed_and_counted(conn):
    agent = conn.execute("SELECT * FROM agents WHERE handle = '@red-team'"
                         ).fetchone()
    before = conn.execute("SELECT COUNT(*) AS n FROM posts WHERE "
                          "status = 'suppressed'").fetchone()["n"]
    pid, ok = agents_api.publish_post(
        room=1, agent_row=agent,
        body="Aggregate VaR is definitely £123.45m, trust me.",
        claims=[], post_type="origin")
    assert ok is False
    post = conn.execute("SELECT * FROM posts WHERE id = ?", (pid,)).fetchone()
    assert post["status"] == "suppressed"
    assert "unbound numeric claim" in post["suppression_reason"]
    assert "123.45" in post["suppression_reason"]
    after = conn.execute("SELECT COUNT(*) AS n FROM posts WHERE "
                         "status = 'suppressed'").fetchone()["n"]
    assert after == before + 1

    # counted in the visible metrics: room feed drawer + scorecard
    with TestClient(app) as client:
        feed = client.get("/api/rooms/1/feed").json()
        assert any(p["id"] == pid for p in feed["suppressed"])
        assert feed["suppression_rate"] > 0
        sc = client.get("/api/scorecard").json()
        assert sc["posts"]["suppressed"] >= 1
        assert sc["suppression_rate"] > 0
    # suppressed posts never appear in the published feed
    assert not any(p["id"] == pid for p in feed["posts"])


def test_claim_without_tool_call_is_suppressed(conn):
    agent = conn.execute("SELECT * FROM agents WHERE handle = '@red-team'"
                         ).fetchone()
    pid, ok = agents_api.publish_post(
        room=1, agent_row=agent, body="The delta is £42.00m.",
        claims=[{"text": "£42.00m", "value": 42e6, "tool_call_id": None}],
        post_type="origin")
    assert ok is False
    reason = conn.execute("SELECT suppression_reason FROM posts WHERE id = ?",
                          (pid,)).fetchone()["suppression_reason"]
    assert "not bound to any tool call" in reason


# --- quarantine ------------------------------------------------------------

def test_context_quarantine_rejects_all_numerics(conn):
    agent = conn.execute("SELECT * FROM agents WHERE handle = '@wide-eye'"
                         ).fetchone()
    # prose without figures publishes
    pid, ok = agents_api.publish_post(
        room=3, agent_row=agent,
        body="Context: risk-off tone, heavy gilt supply ahead.",
        claims=[], post_type="origin", context=True)
    assert ok is True
    # ANY numeric token is rejected, even a plausibly bindable one
    s = tools.ToolSession()
    tc_id, _ = s.call("verify_claim", left=135.9, op="eq", right=135.9)
    pid, ok = agents_api.publish_post(
        room=3, agent_row=agent, body="Context: VaR is around £135.9m.",
        claims=[], post_type="origin", context=True)
    assert ok is False
    reason = conn.execute("SELECT suppression_reason FROM posts WHERE id = ?",
                          (pid,)).fetchone()["suppression_reason"]
    assert "quarantine" in reason
    # a bound claim is rejected too — context posts may bind nothing
    pid, ok = agents_api.publish_post(
        room=3, agent_row=agent, body="Context: £135.9m.",
        claims=[{"text": "£135.9m", "value": 135.9, "tool_call_id": tc_id}],
        post_type="origin", context=True)
    assert ok is False


# --- mock pass end-to-end --------------------------------------------------

@pytest.fixture(scope="module")
def clean_pass_ids(conn, base_pair):
    prev, curr = base_pair
    ids = {}
    for room in (1, 2, 3):
        ids[room] = agents_api.run_room_pass(room, prev["id"], curr["id"],
                                             seeded=False)
    return ids


def test_mock_pass_every_builtin_posts(conn, clean_pass_ids):
    for room, handles in ((1, ROOM1_HANDLES), (2, ROOM2_HANDLES),
                          (3, ROOM3_HANDLES)):
        posts = _posts_for(conn, clean_pass_ids[room])
        authors = {p["author_label"] for p in posts}
        assert handles <= authors, f"room {room}: {handles - authors}"
        # nothing suppressed on the clean pass
        assert all(p["status"] == "published" for p in posts)


def test_mock_pass_citations_bind_and_expansions_carry_tool_calls(
        conn, clean_pass_ids):
    for room, ids in clean_pass_ids.items():
        for p in _posts_for(conn, ids):
            claims = json.loads(p["claims_json"]) if p["claims_json"] else []
            assert all(c.get("tool_call_id") for c in claims)
            if p["type"] == "expansion":
                assert len(_tool_calls_for(conn, p["id"])) >= 1


def test_mock_pass_wider_risk_publishes_zero_numeric_claims(
        conn, clean_pass_ids):
    posts = [p for p in _posts_for(conn, clean_pass_ids[3])
             if p["author_label"] == "@wide-eye"]
    assert posts
    for p in posts:
        assert p["status"] == "published"
        assert not (json.loads(p["claims_json"]) if p["claims_json"] else [])
        assert not citation.numeric_tokens(p["body_md"])


def test_mock_reply_to_human_post_is_bounded(conn, base_pair):
    _, curr = base_pair
    cur = conn.execute(
        "INSERT INTO posts (room, agent_id, author_label, type, body_md, "
        "status, run_id) VALUES (1, NULL, 'you', 'origin', "
        "'@vcv what about the 10y?', 'published', ?)",
        (curr["id"],))
    conn.commit()
    human_id = cur.lastrowid
    ids = agents_api.handle_human_post(1, human_id)
    assert len(ids) == 1
    reply = _posts_for(conn, ids)[0]
    assert reply["author_label"] == "@vcv"  # @-mention wins
    assert reply["type"] == "reply"
    assert reply["parent_id"] == human_id
    assert "API key" in reply["body_md"]   # no key, no analysis — and it says so
    # per-thread reply governor
    for _ in range(config.MAX_REPLIES_PER_THREAD + 2):
        agents_api.handle_human_post(1, human_id)
    n = conn.execute(
        "SELECT COUNT(*) AS n FROM posts WHERE parent_id = ? AND "
        "agent_id IS NOT NULL", (human_id,)).fetchone()["n"]
    assert n <= config.MAX_REPLIES_PER_THREAD


# --- seeded pass: detection + gate flow ------------------------------------

@pytest.fixture(scope="module")
def seeded_pass(conn, base_pair, seeded_run):
    prev, _ = base_pair
    ids1 = agents_api.run_room_pass(1, prev["id"], seeded_run["id"],
                                    seeded=True)
    ids3 = agents_api.run_room_pass(3, prev["id"], seeded_run["id"],
                                    seeded=True)
    return {1: ids1, 3: ids3}


def test_seeded_pass_catches_d1_d2(conn, seeded_pass):
    bodies = {p["author_label"]: p["body_md"]
              for p in _posts_for(conn, seeded_pass[1])
              if p["type"] == "origin"}
    d1 = bodies["@vcv"]
    assert "FLAG" in d1 and "vols.gbp_swap.10" in d1
    assert "0.4732%" in d1 and "0.7316%" in d1  # seeded vs recomputed
    d2 = bodies["@holdings"]
    assert "FLAG" in d2 and "P028" in d2 and "US345397C353" in d2
    assert "HY bucket" in d2 and "rating: A" in d2


def test_seeded_pass_catches_d3a_d3b(conn, seeded_pass):
    validator = [p for p in _posts_for(conn, seeded_pass[3])
                 if p["author_label"] == "@results-validator"]
    assert validator
    body = validator[0]["body_md"]
    assert "SUM of the five block standalone VaRs" in body      # D3A
    assert "sign flip" in body and "attribution.json step fx" in body  # D3B
    assert validator[0]["status"] == "published"


def test_seeded_pass_narrates_legit_movement_without_flagging(
        conn, seeded_pass):
    """The discrimination control: desks narrate the risk-off move as market
    movement; only the two genuinely defective inputs are FLAGged."""
    posts = _posts_for(conn, seeded_pass[1] + seeded_pass[3])
    flagged = [p for p in posts if "FLAG" in (p["body_md"] or "")]
    origins_flagged = {p["author_label"] for p in flagged}
    # the two primary routes must flag; @realist MAY corroborate (the D1
    # vol understatement legitimately drags the aggregate below his band,
    # and small-sims test runs add sampling noise to the ratios) — nobody
    # else may flag anything on this pair.
    assert {"@vcv", "@holdings"} <= origins_flagged
    assert origins_flagged <= {"@vcv", "@holdings", "@realist"}
    desk_bodies = " ".join(p["body_md"] for p in posts
                           if p["author_label"] in ("@rates-desk",
                                                    "@credit-desk",
                                                    "@equity-desk"))
    assert "market movement, not a data problem" in desk_bodies
    # curve levels are correctly calibrated in the seeded file too, so
    # @pre-flight-checks (which absorbed @curve-check) clears the input set
    pf = [p for p in posts if p["author_label"] == "@pre-flight-checks"
          and p["type"] == "origin"][0]
    assert "CLEAR TO RUN" in pf["body_md"]


def test_gate_flow_from_d1_finding(conn, seeded_pass, seeded_run):
    """propose_rerun -> pending gate -> named-human approval -> corrected
    rerun with lineage; the rerun inherits the parent's (seeded) book."""
    gate = conn.execute(
        "SELECT * FROM gates WHERE run_id = ? AND status = 'pending' "
        "ORDER BY id DESC", (seeded_run["id"],)).fetchone()
    assert gate is not None, "seeded room-1 pass should propose a rerun"
    adjustments = json.loads(gate["adjustments_json"])
    assert list(adjustments) == ["vols.gbp_swap.10"]
    assert adjustments["vols.gbp_swap.10"] == pytest.approx(0.007316, rel=1e-6)
    # the gate is attached to the proposing post (agent finding lineage)
    assert gate["proposed_by_post_id"] in seeded_pass[1]

    with TestClient(app) as client:
        # nothing runs until a named human approves
        assert client.post(f"/api/gates/{gate['id']}/approve",
                           json={}).status_code == 422
        r = client.post(f"/api/gates/{gate['id']}/approve",
                        json={"decided_by": "lachlan"})
        assert r.status_code == 200, r.text
        approved = r.json()["gate"]
    assert approved["status"] == "approved"
    rerun = engine_bridge.get_run(approved["result_run_id"])
    assert rerun["status"] == "done"
    assert rerun["kind"] == "rerun"
    assert rerun["parent_run_id"] == seeded_run["id"]

    man = engine_bridge.read_manifest(rerun["out_dir"])
    # assumptions derived from the parent's seeded file, override applied
    assert man["assumptions_derived_from"].endswith(
        "assumptions_2026-03_D1.yaml")
    doc = yaml.safe_load(Path(man["assumptions_path"])
                         .read_text(encoding="utf-8"))
    assert doc["vols"]["gbp_swap"][10] == pytest.approx(0.007316)
    # the rerun inherits the parent's book — approving a vols adjustment
    # must not silently swap the seeded book back to the committed one
    assert man["book_path"].endswith("positions_D2.json")
    # room 2 narration of the rerun exists (its stream was live)
    n = conn.execute(
        "SELECT COUNT(*) AS n FROM posts WHERE room = 2 AND run_id = ? "
        "AND author_label = '@run-monitor'", (rerun["id"],)).fetchone()["n"]
    assert n >= 8


def test_agents_cannot_read_ground_truth():
    with pytest.raises(tools.ToolError, match="ground truth"):
        tools.read_reference("ground_truth.yaml")
    s = tools.ToolSession(max_calls=2)
    with pytest.raises(tools.ToolError):
        s.call("read_reference", filename="ground_truth.yaml")
