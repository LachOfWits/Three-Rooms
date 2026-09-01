"""Run control, the scenario endpoint, the liability input and the snapshot
citation regression — the P&C + roster integration pass.

Covers:
  - POST /api/runs/{id}/stop (PENDING-ROSTER J): terminates the engine
    subprocess, marks the run `stopped`, KEEPS the partial stage events,
    is idempotent, and leaves a finished run alone;
  - GET /api/runs/{id}/scenario (PENDING-ROSTER M/N): the scenario
    explorer's data, by rank / percentile / index, with the error paths the
    frontend branches on (404 / 409 / 422);
  - POST /api/runs `seeded_liabilities`: the liability cohorts are an input
    like the book (a month-end pair carrying written business changes BOTH
    sides), inherited by a gate rerun, and allowlisted the same way;
  - `_defect_matches` honouring an explicit `match_any`, so two defects
    seeded into one file stay separable on the scorecard;
  - outward snapshot drafts (SPEC-APP E) publishing when the research note
    crosses a threshold — the numbers inside a "notable" observation must
    be BOUND, not interpolated, or the citation gate suppresses exactly the
    snapshots worth reading.

AGENT_MODE pinned to mock; no network, no .env dependence. The engine IS
executed here (the stop path has nothing to terminate otherwise) — one
small-sims run.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path

os.environ["AGENT_MODE"] = "mock"
os.environ["ENGINE_PACE_SECONDS"] = "0"

import pytest
from fastapi.testclient import TestClient

from app import config
from app.agents import api as agents_api
from app.server import db, engine_bridge, main as server_main

PROJECT = Path(__file__).resolve().parents[1]
# Run directories are month/version/stage (PENDING-BATCH2 section 1):
# outputs/<YYYY_MM>/vN/{esg,pricing}. `out_dir` is the pricing side
# (the priced results every dashboard endpoint reads); the ESG
# artefacts sit beside it and resolve through engine_bridge.
OUT_MAR = PROJECT / "outputs" / "2026_03" / "v1" / "pricing"
_TMP = Path(tempfile.mkdtemp(prefix="runctl_"))


@pytest.fixture(scope="module")
def conn():
    c = db.init_db(_TMP / "runctl.sqlite")
    agents_api.ensure_builtins(c)
    return c


@pytest.fixture(scope="module")
def client(conn):
    with TestClient(server_main.app) as c:
        yield c


@pytest.fixture(scope="module")
def mar(conn):
    """A completed run pointing at the committed 2026-03 outputs (which
    carry the retained simulation arrays the scenario endpoint reads)."""
    cur = conn.execute(
        "INSERT INTO runs (asof, kind, status, out_dir, seed, sims) "
        "VALUES ('2026-03', 'base', 'done', ?, ?, ?)",
        (str(OUT_MAR), config.DEFAULT_SEED, config.DEFAULT_SIMS))
    conn.commit()
    return cur.lastrowid


# --------------------------------------------------------------------------
# GET /api/runs/{id}/scenario
# --------------------------------------------------------------------------

def test_scenario_endpoint_returns_the_var_scenario(client, mar):
    r = client.get(f"/api/runs/{mar}/scenario?rank=250&percentile=0.995")
    assert r.status_code == 200, r.text
    s = r.json()["scenario"]
    assert s["loss_rank"] == 250 and s["n_sims"] == 50000
    assert s["is_var_scenario"] is True
    # the quantile is where the run says it is
    assert s["loss_gbp"] == pytest.approx(s["reported_aggregate_var_gbp"],
                                          rel=0.02)
    assert len(s["factors"]) == 21
    assert s["joint_plausibility"]["available"] is True
    # the panel renders these three directly
    assert s["positions_by_loss"] and s["largest_draws_in_vols"]
    assert isinstance(s["spread_floor_incidence"], int)


def test_scenario_percentile_only_is_converted_to_a_rank(client, mar):
    r = client.get(f"/api/runs/{mar}/scenario?percentile=0.99")
    assert r.status_code == 200
    assert r.json()["scenario"]["loss_rank"] == 500   # (1-0.99) * 50,000


def test_scenario_by_raw_index(client, mar):
    r = client.get(f"/api/runs/{mar}/scenario?index=0")
    assert r.status_code == 200
    assert r.json()["scenario"]["index"] == 0


def test_scenario_error_paths(client, mar):
    assert client.get("/api/runs/999999/scenario?rank=1").status_code == 404
    assert client.get(
        f"/api/runs/{mar}/scenario?rank=999999").status_code == 422


def test_scenario_refuses_an_unfinished_run(client, conn):
    cur = conn.execute(
        "INSERT INTO runs (asof, kind, status, out_dir, seed, sims) "
        "VALUES ('2026-03', 'base', 'queued', ?, 1, 1)", (str(OUT_MAR),))
    conn.commit()
    r = client.get(f"/api/runs/{cur.lastrowid}/scenario?rank=1")
    assert r.status_code == 409


# --------------------------------------------------------------------------
# POST /api/runs/{id}/stop
# --------------------------------------------------------------------------

def test_stop_unknown_run_is_404(client):
    assert client.post("/api/runs/999999/stop", json={}).status_code == 404


def test_stop_leaves_a_finished_run_alone(client, conn, mar):
    r = client.post(f"/api/runs/{mar}/stop", json={"stopped_by": "t"})
    assert r.status_code == 200
    assert r.json()["run"]["status"] == "done"


def test_stop_terminates_the_engine_and_keeps_partial_stage_events(
        client, conn, monkeypatch):
    """The record of what happened is itself useful: a stopped run keeps
    every stage event recorded before the kill, and is NOT marked failed."""
    monkeypatch.setattr(config, "RUNS_DIR", str(_TMP / "runs"))
    # TestClient drains BackgroundTasks synchronously before the response
    # returns, so the run would already be finished; drive the engine on its
    # own thread (which is what FastAPI does in the server) and stop it
    # through the HTTP route from here.
    run = engine_bridge.create_run(asof="2026-03-31", sims=50000)
    rid = run["id"]
    worker = threading.Thread(target=engine_bridge.execute_run, args=(rid,),
                              daemon=True)
    worker.start()

    # let the engine get past setup, then stop it mid-flight
    deadline = time.time() + 60
    while time.time() < deadline:
        n = conn.execute("SELECT COUNT(*) AS n FROM stage_events "
                         "WHERE run_id = ? AND stage = 'setup' "
                         "AND status = 'done'", (rid,)).fetchone()["n"]
        if n:
            break
        time.sleep(0.05)
    stopped = client.post(f"/api/runs/{rid}/stop",
                          json={"stopped_by": "a named human"})
    assert stopped.status_code == 200
    assert stopped.json()["run"]["status"] == "stopped"
    assert stopped.json()["run"]["finished_at"]

    got = client.get(f"/api/runs/{rid}").json()
    assert got["run"]["status"] == "stopped"
    events = got["stage_events"]
    assert events, "partial stage events must be kept"
    assert any(e["stage"] == "setup" and e["status"] == "done"
               for e in events)
    # idempotent
    again = client.post(f"/api/runs/{rid}/stop", json={})
    assert again.json()["run"]["status"] == "stopped"
    worker.join(timeout=20)
    # and a stopped run is still listed as history
    assert any(x["id"] == rid and x["status"] == "stopped"
               for x in client.get("/api/runs").json()["runs"])


def test_runs_status_check_admits_stopped(conn):
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' "
                       "AND name = 'runs'").fetchone()["sql"]
    assert "'stopped'" in sql


# --------------------------------------------------------------------------
# liability cohorts are an input like the book
# --------------------------------------------------------------------------

def test_seeded_liabilities_is_recorded_and_allowlisted(client, monkeypatch):
    monkeypatch.setattr(config, "RUNS_DIR", str(_TMP / "runs"))
    r = client.post("/api/runs", json={
        "asof": "2026-03-31", "sims": 1,
        "seeded_book": "book/positions_2026-03.json",
        "seeded_liabilities": "book/liabilities_2026-03.json"})
    assert r.status_code == 200, r.text
    run = r.json()["run"]
    man = engine_bridge.read_manifest(run["out_dir"])
    assert Path(man["book_path"]).name == "positions_2026-03.json"
    assert Path(man["liabilities_path"]).name == "liabilities_2026-03.json"
    client.post(f"/api/runs/{run['id']}/stop", json={})

    bad = client.post("/api/runs", json={
        "asof": "2026-03-31",
        "seeded_liabilities": "../../../etc/passwd"})
    assert bad.status_code == 422
    gt = client.post("/api/runs", json={
        "asof": "2026-03-31",
        "seeded_liabilities": "scenarios/seeded/ground_truth.yaml"})
    assert gt.status_code == 422


def test_rerun_inherits_the_parents_liability_cohorts(conn, monkeypatch):
    """A gate approved on an ASSUMPTIONS finding must not silently swap the
    reserve file back to the committed one — the same rule the book already
    had."""
    monkeypatch.setattr(config, "RUNS_DIR", str(_TMP / "runs"))
    parent = engine_bridge.create_run(
        asof="2026-03-31",
        seeded_book_path=str(PROJECT / "book" / "positions_2026-03.json"),
        seeded_liabilities_path=str(PROJECT / "book" /
                                    "liabilities_2026-03.json"))
    child = engine_bridge.create_run(
        asof=None, kind="rerun", parent_run_id=parent["id"],
        adjustments_json={"vols.gbp_swap.10": 0.007316})
    man = engine_bridge.read_manifest(child["out_dir"])
    assert Path(man["book_path"]).name == "positions_2026-03.json"
    assert Path(man["liabilities_path"]).name == "liabilities_2026-03.json"
    assert man["adjustment_changes"][0]["new"] == 0.007316


# --------------------------------------------------------------------------
# scorecard: two defects in one file stay separable
# --------------------------------------------------------------------------

def test_defect_match_any_overrides_the_tolerant_tokens():
    d2 = {"id": "D2", "file": "scenarios/seeded/positions_D2.json",
          "match_any": ["P028", "US345397C353"]}
    d4 = {"id": "D4", "file": "scenarios/seeded/positions_D4.json",
          "match_any": ["PCF-001", "pc_proxy_ref.csv"]}
    ford = "FLAG — `positions_D2_D4.json`: P028 Ford is booked rating A"
    pcf = "FLAG — `positions_D2_D4.json`: PCF-001 proxy rating is CCC"
    assert server_main._defect_matches(d2, ford)
    assert not server_main._defect_matches(d2, pcf)
    assert server_main._defect_matches(d4, pcf)
    assert not server_main._defect_matches(d4, ford)
    # without match_any the tolerant id/file tokens still apply
    plain = {"id": "D1", "field": "vols.gbp_swap.10"}
    assert server_main._defect_matches(plain, "field `vols.gbp_swap.10` is")


def test_ground_truth_gives_d2_and_d4_explicit_match_tokens():
    gt = server_main._load_ground_truth()
    by_id = {d["id"]: d for d in gt["defects"]}
    assert "P028" in by_id["D2"]["match_any"]
    assert "PCF-001" in by_id["D4"]["match_any"]


# --------------------------------------------------------------------------
# SPEC-APP E: a snapshot with something to say must PUBLISH it
# --------------------------------------------------------------------------

def _snapshot_drafts(conn, run_id, handle, data_through):
    from app.agents.checks.room3 import outward_snapshot_draft
    row = conn.execute("SELECT * FROM agents WHERE handle = ?",
                       (handle,)).fetchone()
    ctx = agents_api.PassContext(
        3, None, conn.execute("SELECT * FROM runs WHERE id = ?",
                              (run_id,)).fetchone(),
        seeded=False, snapshot_id=None, data_through=data_through)
    return row, ctx, outward_snapshot_draft(row, ctx)


@pytest.mark.parametrize("handle", ["@focused", "@rates-desk",
                                    "@equity-desk", "@credit-desk"])
def test_snapshot_notable_observations_are_bound_and_publish(conn, mar,
                                                             handle):
    """Regression: the research note's `notable` strings carry figures (the
    move percentile, month vs trailing daily vol). Interpolating them raw
    left unbound numerics, so the citation gate suppressed precisely the
    snapshots that had crossed a threshold, while silent ones published."""
    row, ctx, drafts = _snapshot_drafts(conn, mar, handle, "2026-04-14")
    assert drafts
    body = drafts[0]["body"]
    pid, ok = agents_api.publish_post(
        room=3, agent_row=row, body=body, claims=drafts[0]["claims"],
        post_type="origin", run_id=mar, session=drafts[0].get("session"),
        significance=drafts[0].get("significance"))
    reason = conn.execute("SELECT suppression_reason FROM posts WHERE id = ?",
                          (pid,)).fetchone()["suppression_reason"]
    assert ok, f"{handle} snapshot suppressed: {reason}"


def test_snapshot_covers_a_threshold_crossing(conn, mar):
    """The fixture date must actually exercise the bug: at least one of the
    snapshot agents has a `notable` observation to render."""
    _, _, drafts = _snapshot_drafts(conn, mar, "@focused", "2026-04-14")
    assert drafts[0]["significance"] == "notable"
    assert "percentile" in drafts[0]["body"] or \
        "daily vol ran" in drafts[0]["body"]


def test_lily_runs_her_outward_remit_only_on_a_snapshot(conn, mar):
    """outlook `both` (SPEC-APP E): the quantitative half is settled with
    the frozen valuation; what advances is the context-marked large-loss
    watch, which carries no portfolio numbers."""
    row, ctx, drafts = _snapshot_drafts(conn, mar, "@lily", "2026-04-14")
    assert len(drafts) == 1
    d = drafts[0]
    assert d["context"] is True and not d["claims"]
    pid, ok = agents_api.publish_post(
        room=3, agent_row=row, body=d["body"], claims=[], post_type="origin",
        run_id=mar, context=True, significance=d.get("significance"))
    assert ok, conn.execute(
        "SELECT suppression_reason FROM posts WHERE id = ?",
        (pid,)).fetchone()["suppression_reason"]
