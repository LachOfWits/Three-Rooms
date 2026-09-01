"""Focused-risks research branch tests (SPEC-APP section 5.1).

Covers: deterministic note generation from data/processed/*.csv only; the
read_research tool; the @focused builtin's room-1 duty (base-level
verification, including the fat-fingered-level flag path); room-3 desk
citations of the note; GET /api/research. No engine subprocesses: run rows
point at the committed outputs/<month>/ dirs, whose valuation.json meta
resolves the input files (same resolution path real runs use).
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="three_rooms_research_test_"))
os.environ.setdefault("APP_DB_PATH", str(_TMP / "app.sqlite"))
os.environ.setdefault("APP_RUNS_DIR", str(_TMP / "runs"))
os.environ["ENGINE_PACE_SECONDS"] = "0"
os.environ["AGENT_MODE"] = "mock"  # explicit env wins over any .env

import pandas as pd
import pytest
import yaml
from fastapi.testclient import TestClient

from app import config
from app.agents import api as agents_api
from app.agents import personas, research, tools
from app.agents.checks import ROOM_CHECKS, room1
from app.server import db
from app.server.main import app

DB_FILE = _TMP / "research.sqlite"

ROOM1_HANDLES = {"@pre-flight-checks", "@vcv", "@holdings",
                 "@red-team", "@focused", "@story"}
DESK_HANDLES = {"@rates-desk", "@credit-desk", "@equity-desk"}


@pytest.fixture(scope="module")
def conn():
    return db.init_db(DB_FILE)


def _fake_run(conn, month: str) -> dict:
    """A 'done' run row over the committed base run for <month> —
    outputs/<YYYY_MM>/v1/pricing (PENDING-BATCH2 section 1)."""
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
def committed_pair(conn):
    return _fake_run(conn, "2026-02"), _fake_run(conn, "2026-03")


def _posts_for(conn, ids):
    marks = ",".join("?" * len(ids))
    return conn.execute(
        f"SELECT * FROM posts WHERE id IN ({marks}) ORDER BY id",
        list(ids)).fetchall()


# --- the note itself -------------------------------------------------------

def test_note_generation_is_deterministic():
    a = research.generate_note("2026-03")
    b = research.generate_note("2026-03")
    assert a["markdown"] == b["markdown"]
    assert Path(a["path"]).name == "2026_03_focused.md"
    assert Path(a["path"]).read_text(encoding="utf-8") == a["markdown"]
    assert a["asof"] == "2026-03-31" and a["prev_asof"] == "2026-02-27"
    # both demo-pair months generate
    feb = research.generate_note("2026-02")
    assert feb["asof"] == "2026-02-27" and feb["prev_asof"] == "2026-01-30"


def test_note_values_come_from_source_data_only():
    """Independence: month-end levels equal the raw CSV's last business day
    <= month-end, with no reference to assumptions/ or engine outputs."""
    note = research.generate_note("2026-03")
    df = pd.read_csv(config.PROJECT_ROOT / "data" / "processed" /
                     "gbp_swap.csv", parse_dates=["date"], index_col="date")
    upto = df.loc[:pd.Timestamp("2026-03-31")]
    assert note["stats"]["gbp_swap"]["columns"]["t10"]["level_pct"] == \
        pytest.approx(float(upto["t10"].iloc[-1]), abs=1e-4)
    eq = pd.read_csv(config.PROJECT_ROOT / "data" / "processed" /
                     "equity.csv", parse_dates=["date"], index_col="date")
    assert note["stats"]["equity"]["columns"]["FTSE100"]["level"] == \
        pytest.approx(float(eq.loc[:pd.Timestamp("2026-03-31")]
                            ["FTSE100"].iloc[-1]), abs=1e-4)
    # declared sources are the processed series, nothing else
    assert all(s.startswith("data/processed/")
               for s in note["stats"]["meta"]["sources"])
    # the module is a leaf: no app imports, no reach into other layers
    src = (config.PROJECT_ROOT / "app" / "agents" / "research.py").read_text(
        encoding="utf-8")
    assert "from app" not in src and "import app" not in src
    assert "yaml" not in src  # cannot even parse an assumptions file


def test_note_rejects_bad_asof():
    with pytest.raises(ValueError):
        research.generate_note("garbage")
    with pytest.raises(ValueError):
        research.generate_note("1999-01")  # before the data starts


# --- the tool --------------------------------------------------------------

def test_read_research_tool_registered_and_recorded(conn, committed_pair):
    assert "read_research" in tools.REGISTRY
    assert any(t["name"] == "read_research" for t in tools.TOOL_SPECS)
    _, curr = committed_pair
    s = tools.ToolSession(run_id=curr["id"])
    tc_id, res = s.call("read_research", asof="2026-03")
    assert res["file"] == "2026_03_focused.md"
    assert "Focused-Risks Research Note" in res["markdown"]
    assert res["stats"]["fx"]["columns"]["GBPUSD"]["level"] == 1.3173
    row = conn.execute("SELECT * FROM tool_calls WHERE id = ?",
                       (tc_id,)).fetchone()
    assert row["tool"] == "read_research"
    assert json.loads(row["result_json"])["month"] == "2026-03"
    # a run id resolves to that run's month
    _, res2 = s.call("read_research", asof=str(curr["id"]))
    assert res2["month"] == "2026-03"


# --- room-1 pass: @focused posts, clean, bound -----------------------

@pytest.fixture(scope="module")
def room1_ids(conn, committed_pair):
    prev, curr = committed_pair
    return agents_api.run_room_pass(1, prev["id"], curr["id"], seeded=False)


def test_focused_risks_is_a_builtin_and_registered():
    p = personas.by_handle("@focused")
    assert p is not None and p["room"] == 1
    assert len(personas.BUILTINS) >= 12  # 11 originals + @focused
    assert "@focused" in [h for h, _ in ROOM_CHECKS[1]]


def test_room1_pass_all_builtins_post_including_focused_risks(
        conn, room1_ids):
    posts = _posts_for(conn, room1_ids)
    authors = {p["author_label"] for p in posts}
    assert ROOM1_HANDLES <= authors
    assert all(p["status"] == "published" for p in posts)


def test_focused_risks_clean_post_ties_moves_to_inputs(conn, room1_ids):
    """PENDING-BATCH2 §2: the room-1 post is light context on the inputs
    drawn from @focused's own report — material moves, each tied to the
    assumptions field that carries it, plus the base-level cross-check.
    Conversational and SHORT; the working carries the table."""
    posts = [p for p in _posts_for(conn, room1_ids)
             if p["author_label"] == "@focused"]
    origin = [p for p in posts if p["type"] == "origin"][0]
    body = origin["body_md"]
    assert "FLAG" not in body
    assert "MISMATCH" not in body
    # it references ITS OWN report, by filename
    assert "2026_03_focused.md" in body
    # first person, conversational register (PENDING-BATCH2 §2 register table)
    assert body.startswith("My 2026-03 research note is up")
    # three material moves, each tied to the input field carrying it, one
    # from each of rates / credit / equity-fx
    assert body.count("\n- ") == 3
    assert "`curves." in body and "`spreads." in body
    assert ("`equity." in body or "`fx.GBPUSD`" in body)
    assert "percentile of the trailing two years" in body
    assert "entering the calibration window" in body
    # the cross-check is stated, with its tolerance, and no verdict theatre
    assert "inside tolerance" in body
    assert "wrong file" in body and "stale snapshot" in body
    # SHORT — the smallest post in the room; the working holds the detail
    assert len(body.split()) < 200, len(body.split())
    claims = json.loads(origin["claims_json"])
    assert claims and all(c["tool_call_id"] for c in claims)
    # the ORIGIN carries the source chip that links to the report
    otcs = {t["tool"] for t in conn.execute(
        "SELECT tool FROM tool_calls WHERE post_id = ?",
        (origin["id"],)).fetchall()}
    assert {"read_assumptions", "read_research"} <= otcs
    # the expansion carries the reconciliation working + its own tool calls
    work = [p for p in posts if p["type"] == "expansion"][0]
    assert "MISMATCH" not in work["body_md"]
    assert "| factor | assumed | research month-end | verdict |" in \
        work["body_md"]
    tcs = conn.execute("SELECT tool FROM tool_calls WHERE post_id = ?",
                       (work["id"],)).fetchall()
    assert {"read_assumptions", "read_research"} <= {t["tool"] for t in tcs}


# --- room-1: the flag path (fat-fingered base level) -----------------------

def test_focused_risks_flags_fat_fingered_base_level(conn):
    tmpd = Path(tempfile.mkdtemp(prefix="fr_fatfinger_"))
    doc = yaml.safe_load(
        (config.PROJECT_ROOT / "assumptions" / "2026-03.yaml")
        .read_text(encoding="utf-8"))
    doc["curves"]["gbp_gilt"][10] = float(doc["curves"]["gbp_gilt"][10]) \
        + 0.0005  # +5bp fat-finger, > 1bp tolerance
    apath = tmpd / "assumptions_fatfinger.yaml"
    with open(apath, "w", encoding="utf-8", newline="\n") as f:
        yaml.safe_dump(doc, f, sort_keys=False)
    out_dir = tmpd / "out"
    (out_dir / "inputs").mkdir(parents=True)
    with open(out_dir / "inputs" / "manifest.json", "w",
              encoding="utf-8") as f:
        json.dump({"assumptions_path": str(apath),
                   "book_path": str(config.BOOK_PATH),
                   "liabilities_path": str(config.LIABILITIES_PATH),
                   "seeded": True}, f)
    cur = conn.execute(
        "INSERT INTO runs (asof, kind, status, out_dir) "
        "VALUES ('2026-03', 'base', 'done', ?)", (str(out_dir),))
    conn.commit()
    run = conn.execute("SELECT * FROM runs WHERE id = ?",
                       (cur.lastrowid,)).fetchone()

    ctx = agents_api.PassContext(1, None, run, seeded=True)
    drafts = room1.focused(ctx)
    body = drafts[0]["body"]
    assert "FLAG" in body and "gbp_gilt 10y" in body
    assert "+5.0bp" in body  # the quantified gap
    assert "MISMATCH" in drafts[1]["body"]
    # it publishes through the citation gate (every figure bound)
    agents_api.ensure_builtins(conn)
    agent = conn.execute(
        "SELECT * FROM agents WHERE handle = '@focused'").fetchone()
    ids = agents_api.publish_drafts(ctx, agent, drafts)
    assert all(p["status"] == "published" for p in _posts_for(conn, ids))


# --- room-3 pass: desks cite the research note -----------------------------

@pytest.fixture(scope="module")
def room3_ids(conn, committed_pair):
    prev, curr = committed_pair
    return agents_api.run_room_pass(3, prev["id"], curr["id"], seeded=False)


def test_room3_desks_cite_research_note_and_bind(conn, room3_ids):
    posts = [p for p in _posts_for(conn, room3_ids)
             if p["author_label"] in DESK_HANDLES]
    assert {p["author_label"] for p in posts} == DESK_HANDLES
    for p in posts:
        assert p["status"] == "published", p["author_label"]
        assert "Research note (`2026_03_focused.md`" in p["body_md"]
        tcs = conn.execute(
            "SELECT tool FROM tool_calls WHERE post_id = ?",
            (p["id"],)).fetchall()
        assert "read_research" in {t["tool"] for t in tcs}, p["author_label"]
        claims = json.loads(p["claims_json"])
        assert claims and all(c["tool_call_id"] for c in claims)


# --- the research STAGE (PENDING-BATCH2 §2: it runs FIRST) -----------------

def test_research_pass_generates_both_reports(conn, committed_pair):
    _, curr = committed_pair
    result = agents_api.run_research_pass("2026-03")
    assert result["month"] == "2026-03"
    assert result["mode"] == "mock"          # never an API call in tests
    assert result["errors"] == []
    agents = [r["agent"] for r in result["reports"]]
    assert agents == ["focused", "wide-eye"]  # run order
    for rep in result["reports"]:
        assert rep["web_research"] is False   # mock: our own series only
        assert rep["month"] == "2026-03" and rep["asof"] == "2026-03-31"
        p = Path(rep["path"])
        assert p.exists() and p.name == rep["file"]
        assert p.name in ("2026_03_focused.md", "2026_03_wide-eye.md")
        assert p.stat().st_size > 4000, p.name  # a document, not a stub
    # the stage produces REPORTS, not posts — the rooms write those
    before = conn.execute("SELECT COUNT(*) AS n FROM posts").fetchone()["n"]
    agents_api.run_research_pass("2026-03")
    assert conn.execute("SELECT COUNT(*) AS n FROM posts").fetchone()["n"] \
        == before
    # a run id resolves to that run's month, so a caller need not look it up
    assert agents_api.run_research_pass(curr["id"])["month"] == "2026-03"
    assert agents_api._resolve_month("2026-03-31") == "2026-03"
    with pytest.raises(ValueError):
        agents_api._resolve_month("garbage")


def test_cycle_order_puts_research_first():
    assert agents_api.CYCLE_STAGES == ("research", 1, 2, 3)


def test_live_web_research_is_never_attempted_in_mock():
    """The live web pass exists and is wired in, and the FIRST thing it does
    is check the mode. In mock it returns 'unavailable' without touching the
    SDK, the key or the network — the note then says so in its banner."""
    from app.agents import runtime

    assert runtime.agent_mode() == "mock"
    for agent in research.AGENTS:
        out = agents_api._live_web_context(agent, "2026-03")
        assert set(out) == {"unavailable"}
        assert "no API key" in out["unavailable"]
    # and the mock pass never passes a web context at all
    assert all(r["web_research"] is False
               for r in agents_api.run_research_pass("2026-03")["reports"])


def test_research_personas_describe_what_the_stage_actually_does():
    """The persona prompts drive LIVE mode: they must describe the same job
    the deterministic checks do, or live and mock diverge."""
    f = personas.by_handle("@focused")["persona_prompt"]
    assert "RESEARCH STAGE" in f and "STANDING SET" in f
    for risk in ("interest rates", "inflation", "credit spreads", "defaults",
                 "employment", "GBP/USD", "equities"):
        assert risk in f, risk
    assert "GILT/SWAP BASIS" in f and "curve SHAPE" in f
    assert "smallest post in the room" in f
    assert "read_data_series" in f and "web_search" in f
    # PENDING-BATCH2 s13: ONE agent, two rooms, two briefs -- the room-3
    # brief is @focused's own, not a second persona called @focused-book.
    assert personas.by_handle("@focused-book") is None
    fb = personas.room_brief("@focused", 3)
    assert "I flagged these moves in my report and in room 1" in fb
    assert "standalone block VaR" in fb and "surplus" in fb
    assert 3 in personas.rooms_for(personas.by_handle("@focused"))
    w = personas.by_handle("@wide-eye")["persona_prompt"]
    assert "RESEARCH STAGE" in w
    for theme in ("private credit", "reinsurance", "litigation", "cyber",
                  "climate", "EU AI Act", "gilt market functioning"):
        assert theme in w, theme
    assert "this is material and we cannot currently price it" in w
    assert "NO numeric claims about the portfolio" in w


# --- the two reports -------------------------------------------------------

_STANDING_SECTIONS = ["## 1. Interest rates and the curve",
                      "## 2. Inflation",
                      "## 3. Credit spreads",
                      "## 4. Defaults and distress",
                      "## 5. Employment and the labour market",
                      "## 6. GBP/USD",
                      "## 7. Equities"]


def test_focused_report_is_the_same_standing_set_every_month():
    """The point of a standing set: February and March carry the same seven
    sections in the same order, so the months are comparable."""
    feb = research.generate_note("2026-02")["markdown"]
    mar = research.generate_note("2026-03")["markdown"]
    for md in (feb, mar):
        # the seven NUMBERED risk sections (other '## ' headings, e.g. the
        # independent-levels block, are not part of the standing set)
        numbered = [ln for ln in md.splitlines()
                    if re.match(r"^## \d+\. ", ln)]
        assert numbered[:7] == _STANDING_SECTIONS
    assert feb != mar  # same skeleton, different month's numbers
    assert [r["key"] for r in research.FOCUSED_RISKS] == [
        "rates", "inflation", "credit", "defaults", "employment", "fx",
        "equities"]


def test_focused_report_says_at_the_top_that_mock_has_no_web():
    md = research.generate_note("2026-03")["markdown"]
    head = md.split("## ")[0]
    assert "Web research did not complete" in head
    assert "the *why*" in head
    # and the limitation is repeated where it bites, per risk
    assert md.count("**What drove it.**") == 5  # the five covered risks


def test_focused_report_covers_each_standing_risk_honestly():
    md = research.generate_note("2026-03")["markdown"]
    # risks with no series say so rather than inventing content
    for absent in ("## 2. Inflation", "## 5. Employment"):
        body = md.split(absent, 1)[1].split("\n## ", 1)[0]
        assert "**Not in our data.**" in body
        assert "**In the model?**" in body
        assert "**What live mode would research.**" in body
    # defaults are covered by PROXY and named as one
    body = md.split("## 4. Defaults", 1)[1].split("\n## ", 1)[0]
    assert "Proxy only, and named as one" in body
    assert "This is material and we cannot currently price it." in body
    # the coverage table states, per risk, what backs it and whether it is
    # in the factor set
    assert "| standing risk | our series | in the factor set |" in md
    assert md.count("**none**") == 2  # inflation, employment


def test_focused_report_carries_the_derived_reads():
    """A level table is not research: curve shape, the gilt/swap basis (the
    unhedged exposure PENDING-BATCH2 §2 asks for by name) and the quality
    spreads that stand in for a default series."""
    note = research.generate_note("2026-03")
    md, d = note["markdown"], note["stats"]["derived"]
    assert "**Curve shape.**" in md and "**Gilt/swap basis.**" in md
    assert "| tenor | gilt − swap | Δ month | 2y range | level %ile |" in md
    assert set(d["basis"]) == {"t2", "t5", "t10", "t20"}
    assert set(d["curve_shape"]) == {"gbp_swap", "gbp_gilt", "ust"}
    assert {"HY_BBB", "CCC_HY", "BBB_A"} == set(d["quality"])
    # the basis is gilt MINUS swap, on the two series' common dates
    gilt = pd.read_csv(config.PROJECT_ROOT / "data" / "processed" /
                       "gbp_gilt.csv", parse_dates=["date"], index_col="date")
    swap = pd.read_csv(config.PROJECT_ROOT / "data" / "processed" /
                       "gbp_swap.csv", parse_dates=["date"], index_col="date")
    end = pd.Timestamp("2026-03-31")
    expect = (float(gilt.loc[:end]["t10"].iloc[-1])
              - float(swap.loc[:end]["t10"].iloc[-1])) * 100.0
    assert d["basis"]["t10"]["level_bp"] == pytest.approx(expect, abs=0.11)


def test_focused_report_moves_are_the_real_month_on_month_moves():
    note = research.generate_note("2026-03")
    df = pd.read_csv(config.PROJECT_ROOT / "data" / "processed" /
                     "gbp_gilt.csv", parse_dates=["date"], index_col="date")
    curr = float(df.loc[:pd.Timestamp("2026-03-31")]["t10"].iloc[-1])
    prev = float(df.loc[:pd.Timestamp("2026-02-28")]["t10"].iloc[-1])
    st = note["stats"]["gbp_gilt"]["columns"]["t10"]
    assert st["change_bp"] == pytest.approx((curr - prev) * 100.0, abs=0.05)
    assert 0 <= st["move_percentile"] <= 100
    assert st["trailing_low"] <= st["level_pct"] <= st["trailing_high"]
    assert f"{st['change_bp']:+.1f}bp" in note["markdown"]


def test_wide_eye_report_is_a_real_report_not_a_stub():
    """Mock has no web access and says so — but the note is not a stub: it
    names every standing theme with its channel into the model, and it
    computes the market-level reads our own series genuinely support."""
    note = research.generate_note("2026-03", agent="wide-eye")
    md = note["markdown"]
    assert Path(note["path"]).name == "2026_03_wide-eye.md"
    # honest about the limitation, at the top
    assert "Web research did not complete" in md
    assert note["stats"]["meta"]["web_research"] is False
    assert note["stats"]["meta"]["web_research"] is False
    # every standing theme, with its channel into the model
    assert len(research.WIDER_RISKS) >= 14
    for r in research.WIDER_RISKS:
        assert f"**{r['title']}**" in md, r["key"]
    assert md.count("> Channel into the model —") == len(research.WIDER_RISKS)
    for theme in ("Private credit", "Commercial real estate",
                  "Reinsurance and retrocession", "Cyber",
                  "Regulatory change"):
        assert f"**{theme}" in md
    assert "EU AI Act" in md
    # the standing instruction: say plainly what cannot be priced
    assert "material and we cannot currently price it" in md
    assert "## Standing limitations — material and unpriceable here" in md
    # and what our own data DOES support is computed, not asserted
    assert "## What our own series can say" in md
    assert "| tenor | gilt − swap basis | Δ month | level %ile (2y) |" in md
    d = note["stats"]["derived"]
    assert f"{d['quality']['HY_BBB']['level_bp']:+.1f}bp" in md
    assert f"{d['equity']['SP500']['drawdown_pct']:+.2f}%" in md
    # no fabricated web content anywhere: a theme the web step did not
    # cover says so, rather than being filled in
    assert "Not covered this month" in md
    assert "mock" not in md.lower()
    assert "## Sources" not in md


def test_both_reports_are_deterministic_and_written_atomically():
    for agent in research.AGENTS:
        a = research.generate_note("2026-03", agent=agent)
        b = research.generate_note("2026-03", agent=agent)
        assert a["markdown"] == b["markdown"]
        assert Path(a["path"]).read_text(encoding="utf-8") == a["markdown"]
        assert not list(research.RESEARCH_DIR.glob("*.tmp"))


def test_live_web_context_is_rendered_and_sanitised():
    """The live path is built and exercised here with a SYNTHETIC context —
    no API call, no key, nothing but the renderer. Untrusted prose is
    escaped, capped and never parsed for numbers."""
    ctx = {"risks": {"rates": "Gilts sold off on <script>alert(1)</script> "
                              "supply.", "credit": "x" * 9000},
           "sources": [{"title": "A source", "url": "https://example.test/a"}]}
    note = research.generate_note("2026-03", agent="focused",
                                  out_dir=Path(tempfile.mkdtemp()),
                                  web_context=ctx)
    md = note["markdown"]
    assert "Live mode — web research included" in md
    assert "mock" not in md.lower()
    assert "<script>" not in md and "&lt;script&gt;" in md
    assert "…[truncated]" in md
    assert "## Sources" in md and "https://example.test/a" in md
    assert note["stats"]["meta"]["web_research"] is True
    # a live pass whose search failed says THAT, not "mock mode"
    note2 = research.generate_note("2026-03", agent="wide-eye",
                                   out_dir=Path(tempfile.mkdtemp()),
                                   web_context={"unavailable": "no key."})
    assert "Web research did not complete" in note2["markdown"]
    assert "Mock mode" not in note2["markdown"]   # never lie about being live
    assert "mock" not in note2["markdown"].lower()


# --- room 3: @focused and @wide-eye post against their own reports ---------

def test_focused_room3_is_registered_and_posts_the_book_impact(
        conn, room3_ids):
    assert "@focused" in [h for h, _ in ROOM_CHECKS[3]]
    posts = [p for p in _posts_for(conn, room3_ids)
             if p["author_label"] == "@focused"]
    assert len(posts) == 1
    post = posts[0]
    assert post["status"] == "published", post["suppression_reason"]
    body = post["body_md"]
    assert body.startswith("I flagged these moves in my report")
    assert "2026_03_focused.md" in body          # its own report, by name
    assert body.count("\n- ") == 3               # the same three moves
    # each move tied to the block VaR it moved and sized against surplus
    for block in ("`ir_gbp`", "`credit`", "`equity`"):
        assert block in body
    assert body.count("standalone VaR") == 3
    assert body.count("of surplus") == 3
    assert "Against a surplus of" in body
    assert "on the month" in body                # pair: the block VaR CHANGE
    # it does not re-litigate room 1's reconciliation
    assert "MISMATCH" not in body and "tolerance" not in body
    claims = json.loads(post["claims_json"])
    assert claims and all(c["tool_call_id"] for c in claims)
    tools_used = {t["tool"] for t in conn.execute(
        "SELECT tool FROM tool_calls WHERE post_id = ?",
        (post["id"],)).fetchall()}
    assert {"read_research", "read_output", "verify_claim"} <= tools_used


def test_wide_eye_post_cites_its_report_and_stays_quarantined(
        conn, room3_ids):
    from app.agents import citation

    posts = [p for p in _posts_for(conn, room3_ids)
             if p["author_label"] == "@wide-eye"]
    assert len(posts) == 1
    post = posts[0]
    assert post["status"] == "published", post["suppression_reason"]
    body = post["body_md"]
    # the quarantine holds: no claims, no numeric tokens at all
    assert json.loads(post["claims_json"] or "[]") == []
    assert citation.numeric_tokens(body) == []
    assert body.startswith("**context — enters no calculation**")
    # "in my report I said roughly this — here is what it means for the
    # portfolio as it stands"
    assert "2026_03_wide-eye.md" in body
    assert "What that means for the portfolio as it stands" in body
    assert "cannot search the web" in body
    assert "private credit" in body and "reinsurance" in body
    # the source chip links to the report it is talking about
    tools_used = {t["tool"] for t in conn.execute(
        "SELECT tool FROM tool_calls WHERE post_id = ?",
        (post["id"],)).fetchall()}
    assert "read_research" in tools_used


# --- API -------------------------------------------------------------------

def test_api_research_endpoint(conn):
    with TestClient(app) as client:
        r = client.get("/api/research", params={"asof": "2026-03"})
        assert r.status_code == 200, r.text
        note = r.json()
        assert note["month"] == "2026-03"
        assert "Focused-Risks Research Note — 2026-03" in note["markdown"]
        # regenerated on each request: byte-identical for the same data
        again = client.get("/api/research", params={"asof": "2026-03"})
        assert again.json()["markdown"] == note["markdown"]
        assert client.get("/api/research").status_code == 422  # asof required
        assert client.get("/api/research",
                          params={"asof": "garbage"}).status_code == 422
        # A well-formed month with no note on disk is reported as missing,
        # not manufactured on the fly — the endpoint serves the file the
        # research stage wrote, and says so when there is none.
        gone = client.get("/api/research", params={"asof": "1999-01"})
        assert gone.status_code == 200 and gone.json().get("missing") is True


def test_api_research_serves_both_agents(conn):
    with TestClient(app) as client:
        f = client.get("/api/research",
                       params={"asof": "2026-03", "agent": "focused"}).json()
        w = client.get("/api/research",
                       params={"asof": "2026-03",
                               "agent": "wide-eye"}).json()
        assert f["agent"] == "focused" and w["agent"] == "wide-eye"
        assert f["file"] == "2026_03_focused.md"
        assert w["file"] == "2026_03_wide-eye.md"
        assert "Wide-Eye Research Note — 2026-03" in w["markdown"]
        assert f["markdown"] != w["markdown"]
        # default is still @focused's note (what the tab served before)
        assert client.get("/api/research",
                          params={"asof": "2026-03"}).json()["markdown"] == \
            f["markdown"]


def test_api_research_reports_index(conn):
    with TestClient(app) as client:
        r = client.get("/api/research/reports", params={"asof": "2026-03"})
        assert r.status_code == 200, r.text
        reports = r.json()["reports"]
        assert {x["agent"] for x in reports} == {"focused", "wide-eye"}
        for x in reports:
            assert x["month"] == "2026-03"
            assert x["generated_at"] and x["bytes"] > 4000
            assert x["file"].startswith("2026_03_")
        # newest first across months when no month is given
        allr = client.get("/api/research/reports").json()["reports"]
        months = [x["month"] for x in allr]
        assert months == sorted(months, reverse=True)
        assert client.get("/api/research/reports",
                          params={"asof": "nope"}).status_code == 422


def test_api_research_run_schedules_the_stage(conn, committed_pair):
    _, curr = committed_pair
    with TestClient(app) as client:
        r = client.post("/api/research/run", json={"month": "2026-03"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "scheduled" and body["stage"] == "research"
        assert body["month"] == "2026-03"
        assert body["cycle"] == ["research", 1, 2, 3]  # research runs FIRST
        # background task ran on client exit of the request: both reports
        # are on disk and freshly written
        for name in ("2026_03_focused.md", "2026_03_wide-eye.md"):
            assert (research.RESEARCH_DIR / name).exists()
        # a run id is accepted in place of a month
        assert client.post("/api/research/run",
                           json={"month": curr["id"]}).json()["month"] == \
            "2026-03"
        assert client.post("/api/research/run", json={}).status_code == 422
        assert client.post("/api/research/run",
                           json={"month": "junk"}).status_code == 422
