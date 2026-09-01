"""Tests for PENDING-ROSTER: roster renames + significance levels (G) +
fresh snapshots (E) + notifications centre (F) + agents reading other
agents (H, H.1) + research as genuine research (5.1 rewrite).

No engine subprocesses: run rows point at the committed outputs/<month>/
dirs (same pattern as test_research.py). AGENT_MODE pinned to style.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="three_rooms_snap_notif_test_"))
os.environ.setdefault("APP_DB_PATH", str(_TMP / "app.sqlite"))
os.environ.setdefault("APP_RUNS_DIR", str(_TMP / "runs"))
os.environ["ENGINE_PACE_SECONDS"] = "0"
os.environ["AGENT_MODE"] = "mock"

import pytest
from fastapi.testclient import TestClient

from app import config
from app.agents import api as agents_api
from app.agents import research, tools
from app.server import db
from app.server.main import app

DB_FILE = _TMP / "snap.sqlite"


@pytest.fixture(scope="module")
def conn():
    c = db.init_db(DB_FILE)
    agents_api.ensure_builtins(c)
    return c


def _fake_run(conn, month: str) -> dict:
    # The committed base run for <month>: outputs/<YYYY_MM>/v1/pricing.
    out_dir = config.OUTPUTS_DIR / month.replace("-", "_") / "v1" / "pricing"
    assert (out_dir / "valuation.json").exists()
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


# --------------------------------------------------------------------------
# schema: the new columns/tables exist (fresh-DB schema, per the roster)
# --------------------------------------------------------------------------

def test_schema_carries_the_new_columns_and_tables(conn):
    agent_cols = {r["name"] for r in
                 conn.execute("PRAGMA table_info(agents)").fetchall()}
    assert {"outlook", "reads_from", "reads_on_request"} <= agent_cols
    post_cols = {r["name"] for r in
                conn.execute("PRAGMA table_info(posts)").fetchall()}
    assert {"significance", "snapshot_id", "sources_json",
           "pinned"} <= post_cols
    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()}
    assert {"snapshots", "notifications"} <= tables


def test_builtin_outlooks_match_the_roster(conn):
    rows = {r["handle"]: r["outlook"] for r in
           conn.execute("SELECT handle, outlook FROM agents").fetchall()}
    for h in ("@pre-flight-checks", "@vcv", "@holdings", "@red-team",
             "@run-monitor", "@results-validator", "@vlad", "@attrib",
             "@realist"):
        assert rows[h] == "internal", h
    for h in ("@focused", "@wide-eye", "@rates-desk", "@credit-desk",
             "@equity-desk"):
        assert rows[h] == "outward", h


# --------------------------------------------------------------------------
# significance (SPEC-APP G)
# --------------------------------------------------------------------------

def test_mock_pass_posts_carry_a_valid_significance(conn, pair):
    prev, curr = pair
    ids = []
    for room in (1, 2, 3):
        ids += agents_api.run_room_pass(room, prev["id"], curr["id"],
                                        seeded=False)
    marks = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT significance FROM posts WHERE id IN ({marks}) "
        "AND status = 'published'", ids).fetchall()
    assert rows
    for r in rows:
        assert r["significance"] in ("critical", "notable", "routine",
                                     "quiet")
    # not every post can be the same level — a real spread of verdicts,
    # not a stylistic constant (the discipline SPEC-APP G asks for)
    assert len({r["significance"] for r in rows}) >= 2


def test_scorecard_reports_quiet_rate(client):
    sc = client.get("/api/scorecard").json()
    assert "quiet_rate" in sc
    assert 0.0 <= sc["quiet_rate"] <= 1.0


# --------------------------------------------------------------------------
# fresh snapshots (SPEC-APP E) — room 3 only, outward agents, appended
# --------------------------------------------------------------------------

def test_run_snapshot_appends_outward_posts_only(conn, pair):
    _, curr = pair
    result = agents_api.run_snapshot(curr["id"])
    assert result["seq"] == 1
    assert result["post_ids"]
    assert result["data_through"] > str(curr["asof"])[:10]

    marks = ",".join("?" * len(result["post_ids"]))
    rows = conn.execute(
        f"SELECT * FROM posts WHERE id IN ({marks})",
        result["post_ids"]).fetchall()
    authors = {r["author_label"] for r in rows}
    # only outward (+'both') agents post into a snapshot
    assert authors <= {"@focused", "@wide-eye", "@rates-desk",
                       "@credit-desk", "@equity-desk", "@pc-desk",
                       "@lily"}
    assert "@pre-flight-checks" not in authors and "@vcv" not in authors
    for r in rows:
        assert r["snapshot_id"] == result["snapshot_id"]
        assert r["room"] == 3

    # a second snapshot APPENDS (never replaces) — seq increments, old
    # snapshot's posts are untouched
    result2 = agents_api.run_snapshot(curr["id"])
    assert result2["seq"] == 2
    assert result2["snapshot_id"] != result["snapshot_id"]
    still_there = conn.execute(
        "SELECT COUNT(*) AS n FROM posts WHERE snapshot_id = ? "
        "AND status = 'published'",
        (result["snapshot_id"],)).fetchone()["n"]
    assert still_there == len(result["post_ids"])


def test_snapshot_endpoint_schedules_a_background_pass(conn, client, pair):
    _, curr = pair
    r = client.post("/api/rooms/3/snapshot", json={"run_id": curr["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "scheduled"
    assert client.post("/api/rooms/3/snapshot",
                       json={}).status_code == 422  # run_id required
    assert client.post("/api/rooms/3/snapshot",
                       json={"run_id": 999999}).status_code == 404
    r2 = client.get(f"/api/rooms/3/snapshots?run_id={curr['id']}")
    assert r2.status_code == 200, r2.text
    assert isinstance(r2.json()["snapshots"], list)


# --------------------------------------------------------------------------
# notifications (SPEC-APP F)
# --------------------------------------------------------------------------

def test_human_reply_creates_a_notable_notification(conn, pair):
    _, curr = pair
    cur = conn.execute(
        "INSERT INTO posts (room, agent_id, author_label, type, body_md, "
        "status, run_id) VALUES (1, NULL, 'you', 'origin', "
        "'@vcv does this look right?', 'published', ?)",
        (curr["id"],))
    conn.commit()
    human_id = cur.lastrowid
    reply_ids = agents_api.handle_human_post(1, human_id)
    assert len(reply_ids) == 1
    n = conn.execute(
        "SELECT * FROM notifications WHERE post_id = ?",
        (reply_ids[0],)).fetchone()
    assert n is not None
    assert n["kind"] == "mention_answered"
    assert n["thread_root_id"] == human_id
    assert n["read_at"] is None


def test_notifications_endpoints(client):
    before = client.get("/api/notifications").json()
    assert before["unread_count"] >= 1
    nid = before["notifications"][0]["id"]
    r = client.post(f"/api/notifications/{nid}/read")
    assert r.status_code == 200
    assert r.json()["notification"]["read_at"] is not None
    assert client.post("/api/notifications/999999/read").status_code == 404

    r2 = client.post("/api/notifications/read_all")
    assert r2.status_code == 200
    after = client.get("/api/notifications", params={"unread_only": True})
    assert after.json()["notifications"] == []
    assert after.json()["unread_count"] == 0


def test_only_notable_and_critical_notify(conn, pair):
    """A pass-generated origin post (parent_id NULL) never notifies — only
    a reply/mention landing on a human's thread does (SPEC-APP F/G)."""
    prev, curr = pair
    before = conn.execute(
        "SELECT COUNT(*) AS n FROM notifications").fetchone()["n"]
    agents_api.run_room_pass(2, prev["id"], curr["id"], seeded=False)
    after = conn.execute(
        "SELECT COUNT(*) AS n FROM notifications").fetchone()["n"]
    assert after == before  # scheduled-pass posts don't notify


# --------------------------------------------------------------------------
# agents reading other agents (SPEC-APP H) + cycle rejection
# --------------------------------------------------------------------------

def test_read_agent_posts_tool_preserves_original_tool_call_id(conn, pair):
    prev, curr = pair
    agents_api.run_room_pass(1, prev["id"], curr["id"], seeded=False)
    s = tools.ToolSession(run_id=curr["id"])
    # @vcv, not @pre-flight-checks: a CLEAR TO RUN verdict carries no
    # figures at all by design, and this test needs a post with claims.
    tc_id, res = s.call("read_agent_posts", room=1, handle="@vcv")
    assert res["posts"]
    origin = [p for p in res["posts"] if p["type"] == "origin"][0]
    assert origin["claims"]
    for c in origin["claims"]:
        assert c["tool_call_id"]  # the ORIGINAL executed tool call, not None
        row = conn.execute("SELECT * FROM tool_calls WHERE id = ?",
                           (c["tool_call_id"],)).fetchone()
        assert row is not None and row["tool"] in (
            "read_assumptions", "read_data_series")


def test_read_agent_posts_context_quarantine_survives(conn, pair):
    """@wide-eye posts carry no claims by construction — read_agent_posts
    cannot hand a citing agent anything to launder (SPEC-APP H rule 2)."""
    prev, curr = pair
    agents_api.run_room_pass(3, prev["id"], curr["id"], seeded=False)
    s = tools.ToolSession(run_id=curr["id"])
    _, res = s.call("read_agent_posts", room=3, handle="@wide-eye")
    assert res["posts"]
    for p in res["posts"]:
        assert p["claims"] == []


def test_attrib_reads_the_three_desks(conn):
    row = conn.execute(
        "SELECT reads_from FROM agents WHERE handle = '@attrib'").fetchone()
    assert set(json.loads(row["reads_from"])) == \
        {"@rates-desk", "@credit-desk", "@equity-desk"}


def test_red_team_two_pass_reads_from(conn):
    row = conn.execute(
        "SELECT reads_from, reads_on_request FROM agents "
        "WHERE handle = '@red-team'").fetchone()
    assert set(json.loads(row["reads_from"])) == {"room:1", "room:3"}
    assert json.loads(row["reads_on_request"]) == ["room:2"]


def test_red_team_speaks_twice_opening_and_closing(conn, pair):
    prev, curr = pair
    ids1 = agents_api.run_room_pass(1, prev["id"], curr["id"], seeded=False)
    ids3 = agents_api.run_room_pass(3, prev["id"], curr["id"], seeded=False)
    marks1 = ",".join("?" * len(ids1))
    marks3 = ",".join("?" * len(ids3))
    opening = conn.execute(
        f"SELECT * FROM posts WHERE id IN ({marks1}) AND "
        "author_label = '@red-team'", ids1).fetchall()
    closing = conn.execute(
        f"SELECT * FROM posts WHERE id IN ({marks3}) AND "
        "author_label = '@red-team'", ids3).fetchall()
    assert opening and closing
    assert "Opening challenge" in opening[0]["body_md"]
    assert "Closing challenge" in closing[0]["body_md"]
    assert closing[0]["room"] == 3 and opening[0]["room"] == 1


def test_topological_order_runs_desks_before_attrib(conn, pair):
    prev, curr = pair
    ids = agents_api.run_room_pass(3, prev["id"], curr["id"], seeded=False)
    marks = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT id, author_label FROM posts WHERE id IN ({marks}) "
        "ORDER BY id", ids).fetchall()
    order = [r["author_label"] for r in rows]
    desks_last_idx = max(order.index(h) for h in
                         ("@rates-desk", "@credit-desk", "@equity-desk")
                         if h in order)
    assert order.index("@attrib") > desks_last_idx


def test_validate_reads_from_rejects_a_cycle(conn):
    conn.execute(
        "INSERT INTO agents (room, handle, name, builtin, reads_from) "
        "VALUES (3, '@cycle-a', 'A', 0, '[\"@cycle-b\"]')")
    conn.execute(
        "INSERT INTO agents (room, handle, name, builtin) "
        "VALUES (3, '@cycle-b', 'B', 0)")
    conn.commit()
    with pytest.raises(ValueError, match="cycle"):
        agents_api.validate_reads_from(conn, "@cycle-b", ["@cycle-a"])
    # a non-cyclic assignment is fine
    agents_api.validate_reads_from(conn, "@cycle-b", ["@realist"])


def test_edit_agent_endpoint_rejects_a_cycle_with_422(client, conn):
    a = client.post("/api/agents/3", json={"handle": "@loop-a"}).json()["agent"]
    b = client.post("/api/agents/3", json={"handle": "@loop-b"}).json()["agent"]
    r1 = client.patch(f"/api/agents/3/{a['id']}",
                      json={"reads_from": ["@loop-b"]})
    assert r1.status_code == 200, r1.text
    r2 = client.patch(f"/api/agents/3/{b['id']}",
                      json={"reads_from": ["@loop-a"]})
    assert r2.status_code == 422
    assert "cycle" in r2.json()["detail"].lower()


# --------------------------------------------------------------------------
# research as genuine research (SPEC-APP 5.1 rewrite, item 6)
# --------------------------------------------------------------------------

def test_wide_eye_research_is_an_honest_mock_stub():
    note = research.generate_note("2026-03", agent="wide-eye")
    assert "Web research did not complete" in note["markdown"]
    assert note["stats"]["meta"]["web_research"] is False
    assert Path(note["path"]).name == "2026_03_wide-eye.md"


def test_focused_note_still_the_default_agent():
    note = research.generate_note("2026-03")
    assert note["stats"]["meta"]["agent"] == "focused"
    assert Path(note["path"]).name == "2026_03_focused.md"


def test_snapshot_research_note_is_not_persisted(pair):
    _, curr = pair
    note = research.generate_note(str(curr["asof"])[:7], agent="focused",
                                  data_through="2026-04-15")
    assert note["path"] is None  # snapshot notes are computed, not written
    assert note["stats"]["meta"]["snapshot_data_through"] == "2026-04-15"
    # the window runs from month-end (not the PRIOR month) to data_through
    assert note["stats"]["gbp_swap"]["prev_asof"] == "2026-03-31"


def test_read_research_tool_accepts_data_through():
    s = tools.ToolSession()
    tc_id, res = s.call("read_research", asof="2026-03", agent="focused",
                        data_through="2026-04-15")
    assert res["file"] is None
    assert res["agent"] == "focused"
