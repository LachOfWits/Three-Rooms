"""Punch-list verification (audit VERIFY_APP_SPEC / VERIFY_APP_SECURITY +
SPEC-APP §5 additions @vlad and @realist).

Covers, against the COMMITTED outputs (no engine execution — fast):
  - agents.avatar_json + section 8.1 default-avatar rule server-side,
    builtin seeding (incl. @red-team's horns), the old-schema DB migration;
  - global handle uniqueness + PATCH /api/agents/{room}/{id} (handle
    immutable);
  - mention routing per §5: up to 3 distinct mentions, cross-room, mock
    findings replies;
  - @holdings sleeve-change duty (Feb -> March books, step-8 dVaR) and
    the D4 private-credit proxy catch (direction: overstatement);
  - @red-team second standing item; @results-validator spread-floor and
    sim-percentile checks;
  - @vlad delta_normal reconciliations and @realist bands (zero false
    positives on clean runs, corroboration on D1/D4);
  - delta_normal tool invariants + per-post bound;
  - POST /api/runs seeded-path allowlist; _guard_path sibling-dir fix;
  - scorecard v2: direction-aware D4 + must_flag_changes;
  - config.agent_mode delegating to the runtime.

AGENT_MODE pinned to mock; no network, no .env dependence.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path

os.environ["AGENT_MODE"] = "mock"
os.environ["ENGINE_PACE_SECONDS"] = "0"
os.environ.setdefault(
    "APP_RUNS_DIR",
    str(Path(tempfile.mkdtemp(prefix="punchlist_runs_")) / "runs"))

import pytest
from fastapi.testclient import TestClient

from app import config
from app.agents import api as agents_api
from app.agents import avatars, citation, tools
from app.agents.checks.room1 import holdings
from app.agents.checks.room3 import realist, warden
from app.server import db

_TMP = Path(tempfile.mkdtemp(prefix="punchlist_"))
DB_FILE = _TMP / "punch.sqlite"

PROJECT = Path(__file__).resolve().parents[1]
# Run directories are month/version/stage (PENDING-BATCH2 section 1):
# outputs/<YYYY_MM>/vN/{esg,pricing}. `out_dir` is the pricing side
# (the priced results every dashboard endpoint reads); the ESG
# artefacts sit beside it and resolve through engine_bridge.
OUT_FEB = PROJECT / "outputs" / "2026_02" / "v1" / "pricing"
OUT_MAR = PROJECT / "outputs" / "2026_03" / "v1" / "pricing"
# 2603_v2 — the March book (+15% private credit) and March cohorts.
OUT_MARBOOK = PROJECT / "outputs" / "2026_03" / "v2" / "pricing"
OUT_D1 = PROJECT / "scenarios" / "seeded" / "preview_out" / "d1_only"
OUT_D4 = PROJECT / "scenarios" / "seeded" / "preview_out" / "d4_only"


@pytest.fixture(scope="module")
def conn():
    c = db.init_db(DB_FILE)
    agents_api.ensure_builtins(c)
    return c


@pytest.fixture(scope="module")
def client(conn):
    from app.server.main import app
    with TestClient(app) as c:
        yield c


def _register_run(conn, asof: str, out_dir: Path) -> int:
    cur = conn.execute(
        "INSERT INTO runs (asof, kind, status, out_dir, seed, sims) "
        "VALUES (?, 'base', 'done', ?, ?, ?)",
        (asof, str(out_dir), config.DEFAULT_SEED, config.DEFAULT_SIMS))
    conn.commit()
    return cur.lastrowid


def _run_row(conn, rid):
    return conn.execute("SELECT * FROM runs WHERE id = ?", (rid,)).fetchone()


@pytest.fixture(scope="module")
def feb(conn):
    return _register_run(conn, "2026-02", OUT_FEB)


@pytest.fixture(scope="module")
def mar(conn):
    return _register_run(conn, "2026-03", OUT_MAR)


@pytest.fixture(scope="module")
def marbook(conn):
    return _register_run(conn, "2026-03", OUT_MARBOOK)


@pytest.fixture(scope="module")
def d1_run(conn):
    return _register_run(conn, "2026-03", OUT_D1)


@pytest.fixture(scope="module")
def d4_run(conn):
    """The D4 outputs, copied to a temp out dir WITH a run manifest naming
    the seeded book — the shape an app-created seeded run has."""
    out = _TMP / "d4_run"
    shutil.copytree(OUT_D4, out)
    (out / "inputs").mkdir()
    manifest = {
        "assumptions_path": str(PROJECT / "assumptions" / "2026-03.yaml"),
        "book_path": str(PROJECT / "scenarios" / "seeded" /
                         "positions_D4.json"),
        # d4_only is priced on the MARCH cohorts, matching its clean
        # baseline outputs/2026_03/v2 (ground_truth meta.preview_runs)
        "liabilities_path": str(PROJECT / "book" /
                                "liabilities_2026-03.json"),
        "seeded": True,
    }
    (out / "inputs" / "manifest.json").write_text(json.dumps(manifest),
                                                  encoding="utf-8")
    return _register_run(conn, "2026-03", out)


def _ctx(conn, prev_id, curr_id, seeded=False):
    return agents_api.PassContext(
        1, _run_row(conn, prev_id) if prev_id else None,
        _run_row(conn, curr_id), seeded)


# --------------------------------------------------------------------------
# avatars: default rule, builtin seeding, migration
# --------------------------------------------------------------------------

def test_default_avatar_rule():
    assert avatars.initials("Private Credit") == "PC"
    assert avatars.initials("Vlad") == "V"
    assert avatars.initials("", "@pre-flight-checks") == "PR"
    assert avatars.fg_for("#FFFFFF") == avatars.FG_DARK
    assert avatars.fg_for("#1F2937") == avatars.FG_LIGHT
    assert len(set(avatars.PALETTE)) == 12
    # assigned once, deterministic for a handle; complete and valid
    a = avatars.default_avatar("Custom Check", "@custom-check")
    b = avatars.default_avatar("Custom Check", "@custom-check")
    assert a == b
    assert a["bg"] in avatars.PALETTE
    assert a["fg"] == avatars.fg_for(a["bg"])
    assert a["glyph"] == "CC" and a["accessory"] == "none"
    # normalize fills gaps and rejects malformed colors
    out = json.loads(avatars.normalize({"bg": "not-a-color", "glyph": "ZZ"},
                                       "Zed", "@zed"))
    assert out["bg"] in avatars.PALETTE and out["glyph"] == "ZZ"


def test_builtins_seeded_with_avatars(conn):
    rows = conn.execute(
        "SELECT * FROM agents WHERE builtin = 1").fetchall()
    handles = {r["handle"] for r in rows}
    assert {"@vlad", "@realist", "@focused", "@red-team"} <= handles
    # PENDING-BATCH2: -@curve-check (absorbed by @pre-flight-checks),
    # -@focused-book (merged into @focused, which now posts in rooms 1
    # and 3 as one agent), +@pre-flight-checks, +@pc-desk, +@story
    assert len(rows) == 18
    for r in rows:
        avatar = json.loads(r["avatar_json"])
        assert avatar["bg"].startswith("#") and avatar["glyph"]
    rt = json.loads(conn.execute(
        "SELECT avatar_json FROM agents WHERE handle = '@red-team'"
    ).fetchone()["avatar_json"])
    assert rt["accessory"] == "horns"           # section 8.1: the red-team
    assert rt["bg"].upper() == "#C0392B"        # red circle
    assert rt.get("horn_color", "").upper() == "#F1C40F"  # yellow horns


def test_old_schema_db_migrates(tmp_path):
    """A DB created on the audited schema (no avatar_json, UNIQUE(room,
    handle), duplicate handles across rooms) migrates: column added, global
    uniqueness enforced, duplicate collapsed, posts re-pointed."""
    p = tmp_path / "old.sqlite"
    raw = sqlite3.connect(p)
    raw.executescript("""
        CREATE TABLE agents (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          room INTEGER NOT NULL CHECK (room IN (1, 2, 3)),
          handle TEXT NOT NULL, name TEXT, focus TEXT, persona_prompt TEXT,
          builtin INTEGER NOT NULL DEFAULT 0 CHECK (builtin IN (0, 1)),
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          UNIQUE (room, handle));
        CREATE TABLE posts (
          id INTEGER PRIMARY KEY AUTOINCREMENT, room INTEGER NOT NULL,
          agent_id INTEGER REFERENCES agents(id),
          author_label TEXT NOT NULL, type TEXT NOT NULL, parent_id INTEGER,
          body_md TEXT NOT NULL, claims_json TEXT,
          status TEXT NOT NULL DEFAULT 'published',
          suppression_reason TEXT, run_id INTEGER,
          created_at TEXT NOT NULL DEFAULT (datetime('now')));
        INSERT INTO agents (id, room, handle, builtin) VALUES
          (1, 1, '@red-team', 1), (2, 3, '@red-team', 0), (3, 2, '@x', 0);
        INSERT INTO posts (room, agent_id, author_label, type, body_md)
          VALUES (3, 2, '@red-team', 'origin', 'dup post');
    """)
    raw.commit()
    raw.close()
    # exactly what init_db does, without re-pointing the module's live DB
    c = db._connect(str(p))
    db._migrate_agents(c)
    c.executescript(db.SCHEMA)
    c.commit()
    cols = {r["name"] for r in c.execute("PRAGMA table_info(agents)")}
    assert "avatar_json" in cols
    rows = c.execute("SELECT * FROM agents ORDER BY id").fetchall()
    assert [r["id"] for r in rows] == [1, 3]  # duplicate collapsed
    post = c.execute("SELECT agent_id FROM posts").fetchone()
    assert post["agent_id"] == 1  # re-pointed at the surviving row
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("INSERT INTO agents (room, handle) VALUES (2, '@x')")
    # posts' FK must still target `agents` (the rename-first bug rewrote it
    # to `agents_old` and broke every later insert): inserting works, and
    # the FK is enforced against the migrated table
    c.execute("INSERT INTO posts (room, agent_id, author_label, type, "
              "body_md) VALUES (1, 1, '@red-team', 'origin', 'post-mig')")
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("INSERT INTO posts (room, agent_id, author_label, type, "
                  "body_md) VALUES (1, 999, '@ghost', 'origin', 'nope')")
    c.close()


# --------------------------------------------------------------------------
# PATCH /api/agents/{room}/{id}
# --------------------------------------------------------------------------

def test_patch_agent_edits_and_handle_immutable(client, conn):
    r = client.post("/api/agents/2", json={"handle": "patch-me",
                                           "name": "Patch Me"})
    assert r.status_code == 200
    agent = r.json()["agent"]
    r = client.patch(f"/api/agents/2/{agent['id']}", json={
        "name": "Patched", "focus": "new focus",
        "avatar_json": {"bg": "#0D9488", "glyph": "PM"}})
    assert r.status_code == 200
    got = r.json()["agent"]
    assert got["name"] == "Patched" and got["focus"] == "new focus"
    avatar = json.loads(got["avatar_json"])
    assert avatar["bg"] == "#0D9488" and avatar["glyph"] == "PM"
    assert avatar["fg"] == avatars.fg_for("#0D9488")
    # handle is immutable
    r = client.patch(f"/api/agents/2/{agent['id']}",
                     json={"handle": "@renamed"})
    assert r.status_code == 422
    # builtins are editable too (single-user tool)
    rt = conn.execute("SELECT * FROM agents WHERE handle = '@red-team'"
                      ).fetchone()
    r = client.patch(f"/api/agents/{rt['room']}/{rt['id']}",
                     json={"focus": "edited builtin focus"})
    assert r.status_code == 200
    assert r.json()["agent"]["focus"] == "edited builtin focus"
    # wrong room -> 404
    assert client.patch(f"/api/agents/3/{rt['id']}",
                        json={"name": "x"}).status_code == 404


# --------------------------------------------------------------------------
# mention routing (SPEC-APP §5)
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def clean_pass(conn, feb, mar):
    ids = []
    for room in (1, 2, 3):
        ids += agents_api.run_room_pass(room, feb, mar, seeded=False)
    return ids


def test_mention_routing_three_cross_room(conn, clean_pass, mar):
    """Four distinct mentions in a room-3 post: the first three are honoured
    (cross-room — @red-team lives in room 1, @vlad in room 2), the fourth
    is noted but not routed; each mentioned agent replies with its current
    findings for the active run plus the mock-mode note."""
    cur = conn.execute(
        "INSERT INTO posts (room, agent_id, author_label, type, body_md, "
        "status, run_id) VALUES (3, NULL, 'you', 'origin', "
        "'@red-team what are we missing? @vlad does it reconcile? "
        "@realist sane? @pc-desk too.', 'published', ?)", (mar,))
    conn.commit()
    ids = agents_api.handle_human_post(3, cur.lastrowid)
    replies = [conn.execute("SELECT * FROM posts WHERE id = ?",
                            (i,)).fetchone() for i in ids]
    assert [r["author_label"] for r in replies] == \
        ["@red-team", "@vlad", "@realist"]  # order of appearance, max 3
    for r in replies:
        assert r["type"] == "reply" and r["status"] == "published"
        # Without a key there is no analysis to give and none is invented:
        # replies used to quote the agent's last finding back, dressed as a
        # conversation. Routing is still what this test is about.
        assert "API key" in r["body_md"]
    # the governor note about the un-routed fourth mention
    assert "further mentions were noted but not routed" in \
        replies[0]["body_md"]


def test_no_mention_falls_back_to_room_default(conn, clean_pass, mar):
    cur = conn.execute(
        "INSERT INTO posts (room, agent_id, author_label, type, body_md, "
        "status, run_id) VALUES (1, NULL, 'you', 'origin', "
        "'why did the aggregate move?', 'published', ?)", (mar,))
    conn.commit()
    ids = agents_api.handle_human_post(1, cur.lastrowid)
    assert len(ids) == 1
    reply = conn.execute("SELECT * FROM posts WHERE id = ?",
                         (ids[0],)).fetchone()
    assert reply["author_label"] == "@red-team"  # room-1 default


# --------------------------------------------------------------------------
# clean pass: new personas publish, zero false positives
# --------------------------------------------------------------------------

def test_clean_pass_vlad_and_realist_zero_false_positives(conn, clean_pass):
    marks = ",".join("?" * len(clean_pass))
    posts = conn.execute(
        f"SELECT * FROM posts WHERE id IN ({marks})", clean_pass).fetchall()
    by = {}
    for p in posts:
        by.setdefault(p["author_label"], []).append(p)
    assert "@vlad" in by and "@realist" in by
    for p in by["@vlad"] + by["@realist"]:
        assert p["status"] == "published", p["suppression_reason"]
        assert "FLAG" not in p["body_md"]
    vlad_body = by["@vlad"][0]["body_md"]
    assert "Delta-normal read" in vlad_body
    assert "Euler decomposition" in vlad_body
    assert "exposures" in vlad_body and "correlations" in vlad_body
    assert "stable against its own history" in vlad_body  # no escalation
    realist_body = by["@realist"][0]["body_md"]
    assert "Everything sits in band" in realist_body
    validator = by["@results-validator"][0]["body_md"]
    assert "Spread floor incidence: PASS" in validator
    assert "Sim percentile consistency: PASS" in validator
    red = by["@red-team"][0]["body_md"]
    assert "private-credit proxy" in red
    assert "rate risk is overstated" in red.lower() or \
        "RATE risk is overstated" in red
    assert "NAV smoothing" in red


# --------------------------------------------------------------------------
# @holdings: sleeve-change duty + D4 proxy catch
# --------------------------------------------------------------------------

def test_holdings_no_longer_carries_the_sleeve_change_duty(conn, feb,
                                                           marbook):
    """PENDING-ROSTER rename: @book-warden -> @holdings keeps INPUT
    VERIFICATION ONLY (ISINs, ratings, bucket mappings). The prev-vs-curr
    sleeve/allocation-change duty moved to room 3's @warden — @holdings
    must not surface it even against the +15% PC book pair that used to
    trigger it."""
    drafts = holdings(_ctx(conn, feb, marbook))
    assert not any("Material allocation change" in d["body"]
                   for d in drafts)
    drafts_clean = holdings(_ctx(conn, feb, feb))
    assert not any("Material allocation change" in d["body"]
                   for d in drafts_clean)


def test_book_warden_catches_d4_as_overstatement(conn, feb, d4_run):
    drafts = holdings(_ctx(conn, feb, d4_run, seeded=True))
    flags = [d for d in drafts if "FLAG" in d["body"]
             and "positions_D4.json" in d["body"]]
    assert flags, [d["body"][:120] for d in drafts]
    body = flags[0]["body"]
    assert "pc_proxy_ref.csv" in body
    assert "CCC" in body
    assert "overstated" in body or "overstatement" in body  # the direction
    assert "distressed" in body
    for pid in ("PCF-001", "PCF-002", "PCF-003", "PCF-004"):
        assert pid in body
    # materiality is quoted from the run's assumptions: CCC vs HY level/vol
    assert "9.94%" in body and "3.28%" in body
    # and it publishes bound
    agent = conn.execute("SELECT * FROM agents WHERE handle='@holdings'"
                         ).fetchone()
    pid, ok = agents_api.publish_post(
        room=1, agent_row=agent, body=body, claims=flags[0]["claims"],
        post_type="origin", session=flags[0]["session"])
    assert ok, conn.execute("SELECT suppression_reason FROM posts WHERE "
                            "id = ?", (pid,)).fetchone()


# --------------------------------------------------------------------------
# @realist corroboration: D4 above band, D1 below band, clean in band
# --------------------------------------------------------------------------

def test_realist_flags_d4_pcfs_above_band(conn, feb, d4_run):
    drafts = realist(_ctx(conn, feb, d4_run, seeded=True))
    body = drafts[0]["body"]
    assert "FLAG — reasonableness" in body
    assert "looks high" in body
    assert "private credit" in body
    assert "I'd expect" in body
    # band quoted for the GBP or USD PC sleeve
    assert ("7.0–13.0%" in body) or ("13.0–18.0%" in body)


def test_realist_flags_d1_ir_gbp_block_below_band(conn, feb, d1_run):
    """D1 understates the gbp_swap 10y vol. With the liability duration gap
    closed the GBP rates block is a small share of the surplus VaR, so the
    AGGREGATE barely moves (-0.14%) and stays in band — the portfolio-level
    corroboration signal is the ir_gbp block itself, below its band. Still
    'looks light', still a quoted band, still corroboration only (the scored
    route is @vcv's recomputation)."""
    drafts = realist(_ctx(conn, feb, d1_run, seeded=True))
    body = drafts[0]["body"]
    assert "FLAG — reasonableness" in body
    assert "looks light" in body
    assert "ir_gbp" in body
    assert "1.4–1.7%" in body
    # and the aggregate is NOT flagged: a defect this size must not be
    # claimed as a headline-level anomaly when it is not one
    assert "aggregate 99.5% VaR:" not in body


def test_realist_clean_march_book_in_band(conn, feb, marbook):
    drafts = realist(_ctx(conn, feb, marbook))
    body = drafts[0]["body"]
    assert "Everything sits in band" in body
    assert "FLAG" not in body


# --------------------------------------------------------------------------
# delta_normal tool invariants + bound
# --------------------------------------------------------------------------

def test_delta_normal_euler_sums_and_gap(conn):
    res = tools.delta_normal("2026-03")
    a = res["a"]
    assert a["euler_components_sum_gbp"] == \
        pytest.approx(a["aggregate_var_gbp"], rel=1e-9)
    assert sum(a["euler_components_block_gbp"].values()) == \
        pytest.approx(a["aggregate_var_gbp"], rel=1e-9)
    assert a["diversification_benefit_gbp"] > 0
    assert abs(a["approximation_gap_pct"]) < 8.0  # convexity-sized


def test_delta_normal_pair_steps_sum_exactly(conn):
    res = tools.delta_normal("2026-02", "2026-03")
    pair = res["pair"]
    assert sum(pair["steps_gbp"].values()) == \
        pytest.approx(pair["analytic_delta_var_gbp"], rel=1e-9)
    assert pair["analytic_delta_var_gbp"] == pytest.approx(
        res["b"]["aggregate_var_gbp"] - res["a"]["aggregate_var_gbp"],
        rel=1e-9)
    cells = pair["largest_correlation_cells"]
    assert len(cells) == 3
    assert all("~" in c["cell"] for c in cells)
    # cells are ordered by |delta|
    deltas = [abs(c["delta"]) for c in cells]
    assert deltas == sorted(deltas, reverse=True)


def test_delta_normal_bounded_two_per_post(conn):
    s = tools.ToolSession()
    s.delta_normal_calls = 2
    with pytest.raises(tools.ToolLimitError):
        s.call("delta_normal", run_a="2026-03")


# --------------------------------------------------------------------------
# security: runs allowlist + guard-path sibling fix
# --------------------------------------------------------------------------

def test_runs_api_seeded_path_allowlist(client, monkeypatch):
    from app.server import engine_bridge
    monkeypatch.setattr(engine_bridge, "execute_run", lambda run_id: None)
    # outside scenarios//book -> refused
    r = client.post("/api/runs", json={
        "asof": "2026-03", "seeded_assumptions": "C:/Windows/win.ini"})
    assert r.status_code == 422
    r = client.post("/api/runs", json={
        "asof": "2026-03",
        "seeded_book": str(PROJECT / "data" / "processed" / "fx.csv")})
    assert r.status_code == 422
    # ground truth -> refused even though it lives under scenarios/
    r = client.post("/api/runs", json={
        "asof": "2026-03",
        "seeded_assumptions": "scenarios/seeded/ground_truth.yaml"})
    assert r.status_code == 422
    # legitimate seeded inputs under scenarios/ and book/ -> accepted
    r = client.post("/api/runs", json={
        "asof": "2026-03",
        "seeded_assumptions": "scenarios/seeded/assumptions_2026-03_D1.yaml",
        "seeded_book": "book/positions_2026-03.json"})
    assert r.status_code == 200


def test_guard_path_rejects_sibling_directories():
    evil = PROJECT / "outputs_evil" / "x.json"
    with pytest.raises(tools.ToolError, match="outside"):
        tools._guard_path(evil)


# --------------------------------------------------------------------------
# scorecard v2: direction-aware D4 + must_flag_changes
# --------------------------------------------------------------------------

def test_scorecard_scores_d4_direction_and_must_flag(conn, client, feb,
                                                     marbook, d4_run):
    """After the holdings D4 post above (published in this module's DB)
    and with a seeded run registered, the scorecard must show D4 detected
    WITH the overstatement direction. The +15% PC allocation-change duty
    moved from @holdings to room 3's @warden (PENDING-ROSTER renames) — run
    it for real against the Feb -> March book pair and publish, so the
    scorecard's must_flag_changes route has something to detect."""
    prev_row, curr_row = _run_row(conn, feb), _run_row(conn, marbook)
    warden_ctx = agents_api.PassContext(3, prev_row, curr_row, seeded=False)
    warden_drafts = warden(warden_ctx)
    warden_agent = conn.execute(
        "SELECT * FROM agents WHERE handle = '@warden'").fetchone()
    agents_api.publish_drafts(warden_ctx, warden_agent, warden_drafts)

    sc = client.get("/api/scorecard").json()
    det = sc["detection"]
    assert det is not None
    defects = {d["id"]: d for d in det["defects"]}
    assert set(defects) == {"D1", "D2", "D3A", "D3B", "D4"}
    assert defects["D4"]["detected"] is True
    assert defects["D4"]["direction_ok"] is True
    mfc = {m["id"]: m for m in det["must_flag_changes"]}
    assert "MFC-PC15" in mfc
    assert mfc["MFC-PC15"]["detected"] is True
    assert mfc["MFC-PC15"]["mischaracterised_post_ids"] == []
    # recall counts the 5 defects + the must-flag change
    assert det["recall"] == pytest.approx(
        (sum(d["detected"] for d in defects.values()) + 1) / 6)


def test_scorecard_direction_miss_scores_as_miss(conn):
    """A D4-shaped catch that calls the CCC proxy an UNDERSTATEMENT must
    not count as detected (ground truth v2 scoring note)."""
    from app.server.main import _defect_matches, _direction_ok
    d4 = {"id": "D4", "file": "scenarios/seeded/positions_D4.json",
          "field": "positions[id in PCF-001..PCF-004].rating",
          "direction": "overstatement"}
    wrong = "positions_D4.json re-rates the funds; risk is understated."
    right = "positions_D4.json re-rates the funds; risk is overstated."
    assert _defect_matches(d4, wrong) and not _direction_ok(d4, wrong)
    assert _defect_matches(d4, right) and _direction_ok(d4, right)


# --------------------------------------------------------------------------
# config mode: single source of truth
# --------------------------------------------------------------------------

def test_config_agent_mode_delegates_to_runtime(monkeypatch):
    from app.agents import runtime
    monkeypatch.setenv("AGENT_MODE", "live")
    assert runtime.agent_mode() == "live"
    assert config.agent_mode() == "live"   # no lag: same source of truth
    monkeypatch.setenv("AGENT_MODE", "mock")
    assert config.agent_mode() == "mock"


# --------------------------------------------------------------------------
# regression: the pieces publish through the citation gate
# --------------------------------------------------------------------------

def test_new_checks_have_all_claims_bound(conn, clean_pass):
    marks = ",".join("?" * len(clean_pass))
    for p in conn.execute(
            f"SELECT * FROM posts WHERE id IN ({marks})",
            clean_pass).fetchall():
        if p["author_label"] not in ("@vlad", "@realist",
                                     "@results-validator"):
            continue
        assert p["status"] == "published", (p["author_label"],
                                            p["suppression_reason"])
        for c in json.loads(p["claims_json"] or "[]"):
            rj = tools.fetch_result_json(c["tool_call_id"])
            assert rj is not None
            assert citation.value_in_result(float(c["value"]), rj), c
