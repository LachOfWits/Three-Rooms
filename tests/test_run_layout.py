"""The run folder restructure (PENDING-BATCH2 section 1).

    outputs/
      2026_02/v1/{inputs,esg,pricing}
      2026_03/v1/...  v2/...
      attr_2026_02_v1__2026_03_v1/    (both ends named)
      attr_2026_02_v1__2026_03_v2/
      research/2026_03_focused.md

Covers:
  - the layout helpers (month dir, label, version dir, parsing, next free
    version, artefact resolution across the two stages);
  - `place_stage_artefacts` PARTITIONING what the engine wrote — the ESG
    side gets the assumptions actually used plus the factor draws and the
    index, the pricing side keeps the priced results — with `engine/run.py`
    itself unchanged (it still writes everything into one `--out`);
  - the committed outputs on disk: only 2026_02 and 2026_03 survive, each
    version carries exactly the two stages, no integer run id names a
    directory anywhere;
  - the identity that leaves the server: `GET /api/runs` and
    `GET /api/runs/{id}` expose the LABEL (`2603_v1`) and the directory,
    and the integer id never appears in a path.

AGENT_MODE pinned to mock; the engine is NOT executed here (the committed
runs are read as they stand), so this module is fast.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

os.environ["AGENT_MODE"] = "mock"
os.environ["ENGINE_PACE_SECONDS"] = "0"

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import config
from app.agents import api as agents_api
from app.agents import research, tools
from app.server import db, engine_bridge as eb, main as server_main

PROJECT = Path(__file__).resolve().parents[1]
OUTPUTS = PROJECT / "outputs"
_TMP = Path(tempfile.mkdtemp(prefix="runlayout_"))

KEPT_MONTHS = {"2026_02", "2026_03"}
KEPT_VERSIONS = {("2026_02", "v1"), ("2026_03", "v1"), ("2026_03", "v2")}
ATTR_DIRS = {"attr_2026_02_v1__2026_03_v1", "attr_2026_02_v1__2026_03_v2"}


# ==========================================================================
# layout helpers
# ==========================================================================

def test_month_dir_and_label():
    assert eb.month_dir_name("2026-03-31") == "2026_03"
    assert eb.month_dir_name("2026-03") == "2026_03"
    assert eb.run_label("2026-03-31", 1) == "2603_v1"
    assert eb.run_label("2026-02-27", 12) == "2602_v12"


def test_parse_run_dir_round_trips_and_rejects_flat_dirs():
    d = OUTPUTS / "2026_03" / "v2" / "pricing"
    month, version, root = eb.parse_run_dir(d)
    assert (month, version) == ("2026-03", 2)
    assert root == OUTPUTS / "2026_03" / "v2"
    # the run root and the other stage resolve to the same identity
    assert eb.parse_run_dir(root)[:2] == ("2026-03", 2)
    assert eb.parse_run_dir(root / "esg")[:2] == ("2026-03", 2)
    assert eb.run_root(root / "esg") == root
    # anything not in the layout is reported as such, never guessed at
    for flat in (OUTPUTS / "research", PROJECT / "scenarios" / "seeded"
                 / "preview_out", None, ""):
        assert eb.parse_run_dir(flat) == (None, None, None)


def test_version_dir_follows_the_configured_root(monkeypatch):
    monkeypatch.setattr(config, "RUNS_DIR", str(_TMP / "root"))
    assert eb.version_dir("2026-03-31", 3) == _TMP / "root" / "2026_03" / "v3"


def test_next_version_never_reuses_a_directory_on_disk(monkeypatch, conn):
    """A fresh database must not overwrite a committed run: the next free
    version is one past the highest version ON DISK **or** in the database,
    whichever is further along — not one past the DB row count.

    Uses a month no other test touches, so the answer does not depend on
    what else has been run into the shared database."""
    root = _TMP / "nextver"
    monkeypatch.setattr(config, "RUNS_DIR", str(root))
    assert eb.next_version("2027-09") == 1
    for v in (1, 2, 5):
        (root / "2027_09" / f"v{v}").mkdir(parents=True)
    (root / "2027_09" / "notaversion").mkdir()
    assert eb.next_version("2027-09") == 6
    assert eb.next_version("2027-10") == 1        # a different month is free

    # a run recorded in the DB whose directory has been moved away still
    # holds its version — the counter never walks backwards over history
    conn.execute("INSERT INTO runs (asof, kind, status, out_dir) "
                 "VALUES ('2027-09', 'base', 'done', ?)",
                 (str(root / "2027_09" / "v9" / "pricing"),))
    conn.commit()
    assert eb.next_version("2027-09") == 10


def test_artifact_path_resolves_across_the_two_stages():
    root = OUTPUTS / "2026_03" / "v1"
    pricing, esg = root / "pricing", root / "esg"
    # asked from either stage, or from the root, each artefact is found on
    # the side that actually holds it
    for start in (root, pricing, esg):
        assert eb.artifact_path(start, "valuation.json") == \
            pricing / "valuation.json"
        assert eb.artifact_path(start, "sim_factors.npy") == \
            esg / "sim_factors.npy"
        assert eb.artifact_path(start, "assumptions_used.yaml") == \
            esg / "assumptions_used.yaml"
    # traversal is impossible: only the basename is ever used
    assert eb.artifact_path(root, "../../../book/positions.json").name == \
        "positions.json"
    # a missing file resolves to the directory asked about, so the caller's
    # "no such file" message names somewhere real
    assert eb.artifact_path(pricing, "nope.json") == pricing / "nope.json"


def test_artifact_path_still_serves_a_flat_directory(tmp_path):
    """Temp sensitivity runs and test fixtures keep the engine's own
    single-directory shape; resolution must not require the layout."""
    (tmp_path / "valuation.json").write_text("{}", encoding="utf-8")
    assert eb.artifact_path(tmp_path, "valuation.json") == \
        tmp_path / "valuation.json"


def test_manifest_and_stage_log_belong_to_the_run_not_a_stage():
    root = OUTPUTS / "2026_03" / "v1"
    assert eb.manifest_path(root / "pricing") == \
        root / "inputs" / "manifest.json"
    assert eb.manifest_path(root / "esg") == root / "inputs" / "manifest.json"
    assert eb.stage_log_path(root / "pricing") == root / "stage_log.jsonl"


def test_attribution_directory_names_both_ends():
    assert eb.attribution_dir_name("2026-02", 1, "2026-03", 2) == \
        "attr_2026_02_v1__2026_03_v2"
    prev = {"asof": "2026-02", "out_dir": str(OUTPUTS / "2026_02" / "v1"
                                              / "pricing")}
    curr = {"asof": "2026-03", "out_dir": str(OUTPUTS / "2026_03" / "v2"
                                              / "pricing")}
    assert eb.attribution_dir_names(prev, curr)[0] == \
        "attr_2026_02_v1__2026_03_v2"
    # the exact pair exists on disk, so it is the one chosen
    assert eb.attribution_dir_for_runs(prev, curr) == \
        OUTPUTS / "attr_2026_02_v1__2026_03_v2"
    # a run outside the layout (an ad-hoc or seeded run) falls back to the
    # months' base pair rather than inventing a directory
    adhoc = {"asof": "2026-03", "id": 999,
             "out_dir": str(PROJECT / "scenarios" / "seeded" / "preview_out")}
    assert "attr_2026_02_v1__2026_03_v1" in eb.attribution_dir_names(prev,
                                                                     adhoc)


# ==========================================================================
# place_stage_artefacts — the partition
# ==========================================================================

def _fake_engine_output(pricing: Path) -> None:
    pricing.mkdir(parents=True, exist_ok=True)
    for name in eb.PRICING_ARTEFACTS:
        (pricing / name).write_bytes(b"x")
    for name in ("sim_factors.npy", "sim_index.json"):
        (pricing / name).write_bytes(b"x")


def test_place_stage_artefacts_partitions_the_engine_output(tmp_path):
    root = tmp_path / "2026_03" / "v1"
    pricing = root / "pricing"
    _fake_engine_output(pricing)
    used = tmp_path / "used.yaml"
    used.write_text("meta: {asof: 2026-03-31}\n", encoding="utf-8")

    res = eb.place_stage_artefacts(pricing, str(used))
    assert sorted(res["moved"]) == ["sim_factors.npy", "sim_index.json"]

    esg = root / "esg"
    assert sorted(p.name for p in esg.iterdir()) == \
        sorted(eb.ESG_ARTEFACTS)
    assert sorted(p.name for p in pricing.iterdir()) == \
        sorted(eb.PRICING_ARTEFACTS)
    # a partition, not a copy: nothing is duplicated across the two sides
    assert not set(eb.ESG_ARTEFACTS) & {p.name for p in pricing.iterdir()}
    assert (esg / "assumptions_used.yaml").read_text(encoding="utf-8") == \
        used.read_text(encoding="utf-8")

    # idempotent: running it again on an already-laid-out run changes nothing
    again = eb.place_stage_artefacts(pricing, str(used))
    assert again["moved"] == []
    assert sorted(p.name for p in esg.iterdir()) == sorted(eb.ESG_ARTEFACTS)


def test_place_stage_artefacts_leaves_a_flat_directory_alone(tmp_path):
    """`run_sensitivity` writes the engine's single-directory shape into a
    temp dir; the bridge must not restructure something that is not a run."""
    _fake_engine_output(tmp_path)
    res = eb.place_stage_artefacts(tmp_path, None)
    assert res == {"moved": [], "assumptions_used": None}
    assert (tmp_path / "sim_factors.npy").exists()
    assert not (tmp_path / "esg").exists()


# ==========================================================================
# the committed outputs on disk
# ==========================================================================

def test_only_the_two_months_survive_and_no_integer_id_names_a_directory():
    entries = {p.name for p in OUTPUTS.iterdir()}
    assert entries == KEPT_MONTHS | ATTR_DIRS | {"research", "summary.md"}
    # the old flat outputs/runs/<int id>/ layout is gone, and no directory
    # anywhere under outputs/ is named by an integer
    assert not (OUTPUTS / "runs").exists()
    for p in OUTPUTS.rglob("*"):
        if p.is_dir():
            assert not p.name.isdigit(), p


def test_every_committed_run_carries_exactly_the_two_stages():
    seen = set()
    for month in sorted(KEPT_MONTHS):
        for vdir in sorted((OUTPUTS / month).iterdir()):
            assert re.fullmatch(r"v\d+", vdir.name), vdir
            seen.add((month, vdir.name))
            assert {p.name for p in vdir.iterdir()} == {"esg", "pricing",
                                                        "inputs"}
            assert sorted(p.name for p in (vdir / "esg").iterdir()) == \
                sorted(eb.ESG_ARTEFACTS)
            assert sorted(p.name for p in (vdir / "pricing").iterdir()) == \
                sorted(eb.PRICING_ARTEFACTS)
            man = json.loads((vdir / "inputs" / "manifest.json")
                             .read_text(encoding="utf-8"))
            assert man["label"] == eb.run_label(month.replace("_", "-") + "-01",
                                                int(vdir.name[1:]))
            assert man["seed"] == config.DEFAULT_SEED
            assert man["sims"] == config.DEFAULT_SIMS
    assert seen == KEPT_VERSIONS


def test_esg_side_describes_the_run_that_produced_the_factors():
    """`assumptions_used.yaml` beside `sim_factors.npy` is the point of the
    ESG directory: the calibration the shocks were drawn from, and the
    shocks. It must be the file the run actually priced on."""
    for month, v in sorted(KEPT_VERSIONS):
        root = OUTPUTS / month / v
        man = json.loads((root / "inputs" / "manifest.json")
                         .read_text(encoding="utf-8"))
        used = (root / "esg" / "assumptions_used.yaml").read_bytes()
        assert used == Path(man["assumptions_path"]).read_bytes()
        val = json.loads((root / "pricing" / "valuation.json")
                         .read_text(encoding="utf-8"))
        # the engine records the path it was handed (relative on the command
        # line); the manifest records it absolute — the same file either way
        assert (PROJECT / val["meta"]["assumptions_path"]).resolve() == \
            Path(man["assumptions_path"]).resolve()
        idx = json.loads((root / "esg" / "sim_index.json")
                         .read_text(encoding="utf-8"))
        factors = np.load(root / "esg" / "sim_factors.npy", mmap_mode="r")
        surplus = np.load(root / "pricing" / "sim_surplus.npy", mmap_mode="r")
        assert factors.shape == (idx["n_sims"], len(idx["factor_columns"]))
        assert surplus.shape == (idx["n_sims"],)


def test_both_attributions_name_the_runs_they_walked_between():
    for name in sorted(ATTR_DIRS):
        a = json.loads((OUTPUTS / name / "attribution.json")
                       .read_text(encoding="utf-8"))
        m = a["meta"]
        prev_end, curr_end = name[len("attr_"):].split("__")
        for end, key in ((prev_end, "prev_dir"), (curr_end, "curr_dir")):
            month, version = end.rsplit("_", 1)
            assert Path(m[key]).parts[-3:] == (month, version, "pricing"), end
    # the demo pair is the one carrying a book AND a liability change
    demo = json.loads((OUTPUTS / "attr_2026_02_v1__2026_03_v2"
                       / "attribution.json").read_text(encoding="utf-8"))
    m = demo["meta"]
    assert Path(m["prev_book_path"]).name != Path(m["curr_book_path"]).name
    assert Path(m["prev_liabilities_path"]).name != \
        Path(m["curr_liabilities_path"]).name
    steps = {s["name"]: s["delta_gbp"] for s in demo["mtm"]["steps"]}
    assert steps["book"] != 0.0 and steps["liabilities"] != 0.0
    # the single-book pair reports both as structurally zero, not hidden
    single = json.loads((OUTPUTS / "attr_2026_02_v1__2026_03_v1"
                         / "attribution.json").read_text(encoding="utf-8"))
    steps = {s["name"]: s["delta_gbp"] for s in single["mtm"]["steps"]}
    assert steps["book"] == 0.0 and steps["liabilities"] == 0.0


def test_research_notes_are_month_underscored_per_agent():
    # The convention, not a fixed inventory: notes are regenerated per
    # cycle and a month's note may legitimately be absent between runs.
    names = sorted(p.name for p in (OUTPUTS / "research").iterdir())
    assert names, "no research notes at all"
    # the .levels.json sidecar carries @focused's independently-sourced
    # levels as structured data beside its note
    pattern = re.compile(
        r"^\d{4}_\d{2}_(focused|wide-eye)\.md(\.levels\.json)?$")
    assert all(pattern.match(n) for n in names), names
    assert research.note_filename("2026-03-31", "focused") == \
        "2026_03_focused.md"


# ==========================================================================
# resolution through the agents' tools
# ==========================================================================

@pytest.fixture(scope="module")
def conn():
    c = db.init_db(_TMP / "runlayout.sqlite")
    agents_api.ensure_builtins(c)
    return c


@pytest.fixture(scope="module")
def client(conn):
    with TestClient(server_main.app) as c:
        yield c


def _register(conn, asof, out_dir) -> int:
    cur = conn.execute(
        "INSERT INTO runs (asof, kind, status, out_dir, seed, sims) "
        "VALUES (?, 'base', 'done', ?, ?, ?)",
        (asof, str(out_dir), config.DEFAULT_SEED, config.DEFAULT_SIMS))
    conn.commit()
    return cur.lastrowid


@pytest.fixture(scope="module")
def pair(conn):
    return (_register(conn, "2026-02", OUTPUTS / "2026_02" / "v1" / "pricing"),
            _register(conn, "2026-03", OUTPUTS / "2026_03" / "v1" / "pricing"))


def test_resolve_out_dir_accepts_month_and_explicit_version(conn):
    base = OUTPUTS / "2026_03" / "v1" / "pricing"
    for key in ("2026-03", "2026_03", "2026-03-31", "2026-03/v1",
                "2026_03_v1"):
        assert tools.resolve_out_dir(key) == base, key
    assert tools.resolve_out_dir("2026_03/v2") == \
        OUTPUTS / "2026_03" / "v2" / "pricing"
    assert tools.resolve_out_dir("attr_2026_02_v1__2026_03_v2") == \
        OUTPUTS / "attr_2026_02_v1__2026_03_v2"


def test_read_output_reaches_both_stages_and_names_the_run(conn, pair):
    _feb, mar = pair
    res = tools.read_output(mar, "valuation.json")
    assert res["dir"] == "2026_03/v1"            # the RUN, not the stage
    assert res["data"]["asset_total_gbp"] > 0
    esg = tools.read_output(mar, "sim_index.json")
    assert esg["dir"] == "2026_03/v1"
    assert esg["data"]["n_sims"] == config.DEFAULT_SIMS
    with pytest.raises(tools.ToolError, match="2026_03/v1/nope.json"):
        tools.read_output(mar, "nope.json")


def test_scenario_tools_read_factors_and_pnl_from_opposite_stages(conn, pair):
    """`read_scenario` needs the ESG factor draws AND the pricing-stage P&L
    for the same simulation — the split must be invisible to the caller."""
    _feb, mar = pair
    sc = tools.read_scenario(mar, rank=1)
    assert sc["n_sims"] == config.DEFAULT_SIMS
    assert sc["out_dir"] == "2026_03/v1"
    assert sc["factors"] and sc["surplus_pnl_gbp"] < 0


# ==========================================================================
# what leaves the server: label + directory, never the integer id
# ==========================================================================

def test_runs_api_exposes_the_label_and_the_directory(client, pair):
    feb, mar = pair
    body = client.get("/api/runs").json()["runs"]
    by_id = {r["id"]: r for r in body}
    assert by_id[feb]["label"] == "2602_v1"
    assert by_id[mar]["label"] == "2603_v1"
    for rid, expected in ((feb, "outputs/2026_02/v1"),
                          (mar, "outputs/2026_03/v1")):
        r = by_id[rid]
        assert r["run_dir"] == expected
        assert r["esg_dir"] == expected + "/esg"
        assert r["pricing_dir"] == expected + "/pricing"
        assert r["month"] == str(r["asof"])[:7]
        # the integer id is DB-internal: it names no directory
        assert str(rid) not in Path(r["run_dir"]).parts

    one = client.get(f"/api/runs/{mar}").json()["run"]
    assert one["label"] == "2603_v1"
    assert one["run_dir"] == "outputs/2026_03/v1"


def test_labels_stay_unique_when_a_run_is_outside_the_layout(conn, pair):
    """A seeded/ad-hoc run, or a row left over from the old flat
    `outputs/runs/<id>/`, still needs a label — but it must not be handed
    one a real `vN` directory already owns, or the picker shows two runs
    called `2603_v1` and a human cannot tell them apart."""
    _feb, mar = pair
    layout_v2 = _register(conn, "2026-03", OUTPUTS / "2026_03" / "v2"
                          / "pricing")
    seeded = _register(conn, "2026-03",
                       PROJECT / "scenarios" / "seeded" / "preview_out")
    legacy = _register(conn, "2026-03", PROJECT / "outputs" / "runs" / "7")
    labels = {rid: eb.get_run(rid)["label"]
              for rid in (mar, layout_v2, seeded, legacy)}
    assert labels[mar] == "2603_v1"          # owns v1 on disk
    assert labels[layout_v2] == "2603_v2"    # owns v2 on disk
    # the two outside the layout take the next free numbers, in id order
    assert labels[seeded] == "2603_v3"
    assert labels[legacy] == "2603_v4"
    assert len(set(labels.values())) == len(labels)
    for rid in (seeded, legacy):
        conn.execute("DELETE FROM runs WHERE id = ?", (rid,))
    conn.execute("DELETE FROM runs WHERE id = ?", (layout_v2,))
    conn.commit()


def test_dashboard_pair_finds_the_attribution_by_both_ends(client, conn, pair):
    feb, mar = pair
    r = client.get("/api/dashboard/3", params={"pair": f"{feb},{mar}"})
    assert r.status_code == 200, r.text
    attr = r.json().get("attribution")
    assert attr is not None
    assert Path(attr["meta"]["curr_dir"]).parts[-3:] == \
        ("2026_03", "v1", "pricing")

    # the March-book run picks up the OTHER attribution — the one whose
    # ends actually match it
    marbook = _register(conn, "2026-03", OUTPUTS / "2026_03" / "v2" / "pricing")
    r = client.get("/api/dashboard/3", params={"pair": f"{feb},{marbook}"})
    assert r.status_code == 200, r.text
    attr = r.json()["attribution"]
    assert Path(attr["meta"]["curr_dir"]).parts[-3:] == \
        ("2026_03", "v2", "pricing")
