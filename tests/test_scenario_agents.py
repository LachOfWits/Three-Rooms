"""Scenario tools and the analytical agents (PENDING-ROSTER M, M.1, @warden).

Covers:
  - `read_scenario` / `tail_analysis` / `price_scenario` / `query_scenarios`
    against the committed 50k-sim outputs, including the controls that make
    them trustworthy: the VaR scenario reproduces the reported VaR, and
    `price_scenario`'s market leg reconciles to the ENGINE's own sequential
    attribution to the penny.
  - `@warden` — the month-end summary: the exact headline form, the
    written-business finding, the pro-rata / decision split, and the fact
    that it always posts.
  - `@lily` — the quantitative/context split, the duration gap, and the
    quarantine holding on her context post.
  - `@vlad` — the per-run VaR-scenario duty and the joint-plausibility read.

Engine outputs are read from `outputs/` (gitignored, produced by the engine
runs); the same convention as tests/test_punchlist.py. No API calls
anywhere: AGENT_MODE is pinned to style.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="scenario_agents_test_"))
os.environ.setdefault("APP_DB_PATH", str(_TMP / "app.sqlite"))
os.environ.setdefault("APP_RUNS_DIR", str(_TMP / "runs"))
os.environ["ENGINE_PACE_SECONDS"] = "0"
os.environ["AGENT_MODE"] = "mock"

import numpy as np
import pytest

from app import config
from app.agents import api as agents_api
from app.agents import citation, tools
from app.agents.checks.room2 import vlad
from app.agents.checks.room3 import lily, warden
from app.server import db

PROJECT = Path(__file__).resolve().parents[1]
# Run directories are month/version/stage (PENDING-BATCH2 section 1):
# outputs/<YYYY_MM>/vN/{esg,pricing}. `out_dir` is the pricing side
# (the priced results every dashboard endpoint reads); the ESG
# artefacts sit beside it and resolve through engine_bridge.
OUT_FEB = PROJECT / "outputs" / "2026_02" / "v1" / "pricing"
OUT_MAR = PROJECT / "outputs" / "2026_03" / "v1" / "pricing"
# 2603_v2 — the March book (+15% private credit) and March cohorts.
OUT_MARBOOK = PROJECT / "outputs" / "2026_03" / "v2" / "pricing"
# Both ends named: 2602_v1 -> 2603_v2, the two-book/two-liability pair.
ATTR_BOOKS = PROJECT / "outputs" / "attr_2026_02_v1__2026_03_v2"
# The simulated factor draws are an ESG-stage artefact; the P&L
# arrays are pricing-stage. Read each from its own side.
ESG_MAR = OUT_MAR.parent / "esg"

DB_FILE = _TMP / "scenario_agents.sqlite"

pytestmark = pytest.mark.skipif(
    not (OUT_FEB / "sim_surplus.npy").exists()
    or not (OUT_MARBOOK / "sim_surplus.npy").exists(),
    reason="committed 50k-sim outputs (with retained simulations) required")


@pytest.fixture(scope="module")
def conn():
    c = db.init_db(DB_FILE)
    agents_api.ensure_builtins(c)
    return c


def _register(conn, asof: str, out_dir: Path) -> int:
    cur = conn.execute(
        "INSERT INTO runs (asof, kind, status, out_dir, seed, sims) "
        "VALUES (?, 'base', 'done', ?, ?, ?)",
        (asof, str(out_dir), config.DEFAULT_SEED, config.DEFAULT_SIMS))
    conn.commit()
    return cur.lastrowid


@pytest.fixture(scope="module")
def feb(conn):
    return _register(conn, "2026-02", OUT_FEB)


@pytest.fixture(scope="module")
def mar(conn):
    return _register(conn, "2026-03", OUT_MAR)


@pytest.fixture(scope="module")
def marbook(conn):
    """The demo pair's current side: March market, March book AND March
    liability cohorts — the run in which written business is visible."""
    return _register(conn, "2026-03", OUT_MARBOOK)


def _json(p: Path):
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


# ==========================================================================
# read_scenario
# ==========================================================================

def test_read_scenario_defaults_to_the_var_scenario(conn, mar):
    sc = tools.read_scenario(mar)
    reported = _json(OUT_MAR / "var_aggregate.json")["aggregate_var_gbp"]
    assert sc["n_sims"] == 50000
    assert sc["loss_rank"] == 250          # 0.5% of 50,000
    assert sc["is_var_scenario"] is True
    assert sc["loss_percentile"] == pytest.approx(0.005)
    # the scenario at that rank IS the reported quantile (sample order
    # statistic vs interpolated percentile: agree to well within 0.5%)
    assert sc["loss_gbp"] == pytest.approx(reported, rel=0.005)
    assert sc["reported_aggregate_var_gbp"] == pytest.approx(reported)


def test_read_scenario_returns_all_21_factors_with_levels(conn, mar):
    sc = tools.read_scenario(mar)
    assert len(sc["factors"]) == 21
    a = tools.read_assumptions("2026-03")["data"]
    by = {f["factor"]: f for f in sc["factors"]}
    # additive on rates, proportional on equity/FX (SPEC section 2)
    swap10 = by["gbp_swap_10"]
    assert swap10["shocked_level"] == pytest.approx(
        float(a["curves"]["gbp_swap"][10]) + swap10["shock"])
    ftse = by["eq_FTSE100"]
    assert ftse["shocked_level"] == pytest.approx(
        float(a["equity"]["FTSE100"]) * (1.0 + ftse["shock"]))
    # size in annual vols is the read @vlad needs
    assert swap10["shock_in_vols"] == pytest.approx(
        swap10["shock"] / swap10["vol_annual"])


def test_read_scenario_position_pnl_reconciles_to_surplus(conn, mar):
    sc = tools.read_scenario(mar)
    assert sc["asset_pnl_gbp"] + sc["liability_pnl_gbp"] == pytest.approx(
        sc["surplus_pnl_gbp"], rel=1e-4, abs=1.0)
    ranked = sc["positions_by_loss"]
    assert ranked == sorted(ranked, key=lambda r: r["pnl_gbp"])


def test_read_scenario_by_index_round_trips(conn, mar):
    sc = tools.read_scenario(mar)
    again = tools.read_scenario(mar, index=sc["index"])
    assert again["loss_rank"] == sc["loss_rank"]
    assert again["surplus_pnl_gbp"] == pytest.approx(sc["surplus_pnl_gbp"])


def test_read_scenario_joint_plausibility_is_a_chi2_read(conn, mar):
    jp = tools.read_scenario(mar)["joint_plausibility"]
    assert jp["available"] is True
    assert jp["degrees_of_freedom"] == 21
    assert jp["chi2_expected_d2"] == 21.0
    assert jp["mahalanobis_d2"] > 0
    assert 0.0 <= jp["chi2_percentile"] <= 100.0


def test_read_scenario_refuses_bad_ranks_and_missing_sims(conn, mar):
    with pytest.raises(tools.ToolError, match="outside"):
        tools.read_scenario(mar, rank=0)
    with pytest.raises(tools.ToolError, match="outside"):
        tools.read_scenario(mar, rank=10**9)
    with pytest.raises(tools.ToolError, match="no saved simulation"):
        # a real attribution directory: it exists, and it holds no
        # simulation arrays — which is what the message must say
        tools.read_scenario("attr_2026_02_v1__2026_03_v1")


# ==========================================================================
# tail_analysis
# ==========================================================================

def test_tail_analysis_worst_n_statistics(conn, mar):
    ta = tools.tail_analysis(mar)
    reported = _json(OUT_MAR / "var_aggregate.json")["aggregate_var_gbp"]
    assert ta["n_tail"] == 250
    # the tail threshold is the VaR; the mean tail loss (expected
    # shortfall) is necessarily worse than it
    assert ta["tail_threshold_gbp"] == pytest.approx(reported, rel=0.005)
    assert ta["mean_tail_loss_gbp"] > ta["tail_threshold_gbp"]
    assert ta["worst_loss_gbp"] >= ta["mean_tail_loss_gbp"]
    assert ta["expected_shortfall_gbp"] == ta["mean_tail_loss_gbp"]


def test_tail_analysis_contributions_sum_to_the_mean_tail_loss(conn, mar):
    ta = tools.tail_analysis(mar)
    total = sum(c["mean_pnl_gbp"] for c in ta["mean_position_contributions"])
    assert total == pytest.approx(-ta["mean_tail_loss_gbp"], rel=1e-4)
    shares = [c["share_of_mean_tail_loss"]
              for c in ta["mean_position_contributions"]]
    assert sum(shares) == pytest.approx(1.0, rel=1e-6)


def test_tail_analysis_top5_frequency_and_floor_incidence(conn, mar):
    ta = tools.tail_analysis(mar, n=100)
    assert ta["n_tail"] == 100
    freqs = ta["top5_loser_frequency"]
    assert all(0.0 <= f["top5_frequency"] <= 1.0 for f in freqs)
    # exactly five losers are counted per scenario
    assert sum(f["top5_count"] for f in freqs) <= 5 * 100
    floor = ta["spread_floor"]
    assert set(floor["per_rating_counts"]) == {
        "spread_AA", "spread_A", "spread_BBB", "spread_HY", "spread_CCC"}
    # the base spreads sit far above what a 99.5% move can erase
    assert floor["scenarios_with_any_floor_bound"] == 0
    assert floor["incidence_rate"] == 0.0


def test_tail_analysis_factor_distribution_is_risk_off(conn, mar):
    ta = tools.tail_analysis(mar)
    by = {f["factor"]: f for f in ta["factor_distributions"]}
    # in the loss tail equities fall and credit spreads widen — the signs
    # that make the tail a coherent state of the world
    assert by["eq_SP500"]["mean_shock_in_vols"] < 0
    assert by["spread_HY"]["mean_shock_in_vols"] > 0


def test_tail_analysis_bounds_its_own_appetite(conn, mar):
    with pytest.raises(tools.ToolError, match="n must be"):
        tools.tail_analysis(mar, n=99999)
    with pytest.raises(tools.ToolError, match="quantile"):
        tools.tail_analysis(mar, quantile=1.0)


# ==========================================================================
# price_scenario
# ==========================================================================

def test_price_scenario_base_matches_the_engine_valuation(conn, mar):
    ps = tools.price_scenario(mar)
    val = _json(OUT_MAR / "valuation.json")
    assert ps["base"]["asset_total_gbp"] == pytest.approx(
        val["asset_total_gbp"], rel=1e-9)
    assert ps["base"]["liability_pv_gbp"] == pytest.approx(
        val["liability_pv_gbp"], rel=1e-9)
    assert ps["base"]["surplus_gbp"] == pytest.approx(
        val["surplus_gbp"], rel=1e-9)
    assert ps["deterministic"] is True and ps["simulation"] is False


def test_price_scenario_swap_rise_raises_surplus(conn, mar):
    """@lily's structural fact: GBP cohorts discount on gbp_swap and no
    asset does, so a swap-rate rise cuts liabilities and lifts surplus."""
    ps = tools.price_scenario(mar, {"gbp_swap": 0.01})
    assert ps["delta"]["liability_pv_gbp"] < 0
    assert ps["delta"]["asset_total_gbp"] == pytest.approx(0.0, abs=1.0)
    assert ps["delta"]["surplus_gbp"] > 0
    assert ps["delta"]["surplus_gbp"] == pytest.approx(
        -ps["delta"]["liability_pv_gbp"], rel=1e-9)


def test_price_scenario_block_and_factor_keys_agree(conn, mar):
    block = tools.price_scenario(mar, {"gbp_gilt": 0.005})
    tenors = tools.price_scenario(mar, {"gbp_gilt_2": 0.005,
                                        "gbp_gilt_5": 0.005,
                                        "gbp_gilt_10": 0.005,
                                        "gbp_gilt_20": 0.005})
    assert block["shocked"]["surplus_gbp"] == pytest.approx(
        tenors["shocked"]["surplus_gbp"], rel=1e-12)
    # the friendly spelling resolves to the SPEC factor name
    alias = tools.price_scenario(mar, {"equity_FTSE100": -0.2})
    canon = tools.price_scenario(mar, {"eq_FTSE100": -0.2})
    assert alias["shocked"]["asset_total_gbp"] == pytest.approx(
        canon["shocked"]["asset_total_gbp"], rel=1e-12)


def test_price_scenario_refuses_unknown_keys_and_bad_values(conn, mar):
    with pytest.raises(tools.ToolError, match="unknown shock key"):
        tools.price_scenario(mar, {"gbp_swap_7": 0.01})
    with pytest.raises(tools.ToolError, match="must be a number"):
        tools.price_scenario(mar, {"gbp_swap": "a lot"})


def test_price_scenario_durations_and_the_duration_gap(conn, mar):
    """@lily owns the gap: it must be assets-FI minus liabilities, and the
    cohort durations must match the P&C shape SPEC section 3 mandates."""
    d = tools.price_scenario(mar)["durations"]
    assert d["duration_gap_years"] == pytest.approx(
        d["assets_fixed_income_years"] - d["liabilities_years"])
    assert d["duration_gap_all_assets_years"] == pytest.approx(
        d["assets_all_years"] - d["liabilities_years"])
    assert 3.6 <= d["liabilities_years"] <= 4.4      # SPEC section 7 target
    assert d["assets_all_years"] < d["assets_fixed_income_years"]

    coh = {c["id"]: c for c in tools.price_scenario(mar)["liability_cohorts"]}
    for cid in ("L-PROP-GBP", "L-PROP-USD"):
        assert 1.8 <= coh[cid]["effective_duration_years"] <= 2.6
    for cid in ("L-CAS-GBP", "L-CAS-USD"):
        assert 5.0 <= coh[cid]["effective_duration_years"] <= 7.0


def test_price_scenario_exposures_split_assets_and_liabilities(conn, mar):
    ex = tools.price_scenario(mar)["exposures"]
    for name in ("gbp_swap", "gbp_gilt", "ust", "spread", "equity", "fx",
                 "ir_gbp", "ir_usd"):
        e = ex[name]
        assert e["surplus_gbp"] == pytest.approx(
            e["assets_gbp"] - e["liabilities_gbp"], rel=1e-6, abs=1e-3)
    # only the liabilities discount on gbp_swap; only the assets carry
    # gilt and spread risk
    assert ex["gbp_swap"]["assets_gbp"] == pytest.approx(0.0, abs=1e-6)
    assert ex["gbp_swap"]["liabilities_gbp"] < 0
    assert ex["gbp_gilt"]["liabilities_gbp"] == pytest.approx(0.0, abs=1e-6)
    assert ex["spread"]["liabilities_gbp"] == pytest.approx(0.0, abs=1e-6)
    # the USD cohorts translate at GBPUSD, so liabilities ARE an FX
    # position (SPEC section 3)
    assert ex["fx"]["liabilities_gbp"] != 0.0
    assert 0.0 < ex["ir_gbp"]["liability_share_of_gross"] < 1.0


def test_price_scenario_sleeves_cover_the_whole_book(conn, mar):
    ps = tools.price_scenario(mar)
    sl = ps["by_sleeve"]
    assert set(sl) == {"govt_bond", "corp_bond", "private_credit", "equity",
                       "cash"}
    assert sum(s["n_positions"] for s in sl.values()) == len(ps["positions"])
    assert sum(s["base_value_gbp"] for s in sl.values()) == pytest.approx(
        ps["base"]["asset_total_gbp"], rel=1e-9)
    assert sl["private_credit"]["n_positions"] == 4


def test_price_scenario_to_asof_reproduces_the_other_month(conn, feb, mar):
    """`to_asof` shocks the whole market state to another month-end: the
    prior book repriced there must equal that month's own valuation, since
    the book is the same file on both dates."""
    ps = tools.price_scenario(feb, to_asof=mar)
    mar_val = _json(OUT_MAR / "valuation.json")
    assert ps["shocked"]["asset_total_gbp"] == pytest.approx(
        mar_val["asset_total_gbp"], rel=1e-9)
    assert ps["shocked"]["liability_pv_gbp"] == pytest.approx(
        mar_val["liability_pv_gbp"], rel=1e-9)


def test_price_scenario_market_leg_matches_engine_attribution(conn, feb,
                                                              marbook):
    """The control on @warden's whole reconciliation: exact repricing of
    the prior balance sheet at the current market state must equal the sum
    of the ENGINE's own attribution steps 1-7 (the market steps), and the
    residual flows must equal steps 8 (book) and 9 (liabilities)."""
    if not (ATTR_BOOKS / "attribution.json").exists():
        pytest.skip("book-pair attribution outputs not present")
    attr = _json(ATTR_BOOKS / "attribution.json")
    steps = {s["name"]: s["delta_gbp"] for s in attr["mtm"]["steps"]}
    market_steps = sum(steps[n] for n in ("gbp_swap", "gbp_gilt", "ust",
                                          "spread", "equity", "fx", "vcv"))

    p1 = tools.price_scenario(feb, to_asof=marbook)
    assert p1["delta"]["surplus_gbp"] == pytest.approx(market_steps, abs=1.0)

    p2 = tools.price_scenario(marbook)
    flow_assets = (p2["base"]["asset_total_gbp"]
                   - p1["shocked"]["asset_total_gbp"])
    flow_liabs = (p2["base"]["liability_pv_gbp"]
                  - p1["shocked"]["liability_pv_gbp"])
    assert flow_assets == pytest.approx(steps["book"], abs=1.0)
    assert flow_liabs == pytest.approx(-steps["liabilities"], abs=1.0)


def test_price_scenario_book_override_is_guarded(conn, mar):
    ps = tools.price_scenario(mar, book="positions_2026-03.json")
    assert ps["book_file"] == "positions_2026-03.json"
    with pytest.raises(tools.ToolError, match="ground truth"):
        tools.price_scenario(mar, book="ground_truth.yaml")
    with pytest.raises(tools.ToolError, match="no such book"):
        tools.price_scenario(mar, book="../../etc/passwd")


# ==========================================================================
# query_scenarios
# ==========================================================================

def test_query_scenarios_filters_and_reports_conditional_stats(conn, mar):
    q = tools.query_scenarios(mar, "equity_FTSE100 < -0.20")
    factors = np.load(str(ESG_MAR / "sim_factors.npy"))
    expected = int((factors[:, 17] < -0.20).sum())
    assert q["n_matching"] == expected
    assert q["match_rate"] == pytest.approx(expected / 50000)
    assert q["implied_return_period_years"] == pytest.approx(50000 / expected)
    # conditional on a big equity fall, spreads WIDEN — the joint
    # plausibility read the correlation matrix is supposed to deliver
    by = {f["factor"]: f for f in q["factor_conditional_means"]}
    assert by["eq_FTSE100"]["mean_shock"] < -0.20
    assert by["spread_BBB"]["mean_shock"] > 0
    assert q["surplus_pnl_gbp"]["mean"] < q["surplus_pnl_gbp"][
        "unconditional_mean"]


def test_query_scenarios_reverse_stress_test(conn, mar):
    """`where='surplus_pnl < -X'` returns what would have to happen for the
    balance sheet to break (SS1/23 reverse stress testing)."""
    q = tools.query_scenarios(mar, "surplus_pnl < -100e6")
    surplus = np.load(str(OUT_MAR / "sim_surplus.npy"))
    assert q["n_matching"] == int((surplus < -100e6).sum())
    assert q["n_matching"] > 0
    assert q["surplus_pnl_gbp"]["max"] < -100e6
    draws = {f["factor"]: f["mean_shock_in_vols"]
             for f in q["factor_conditional_means"]}
    # it takes a simultaneous multi-sigma equity fall AND spread widening
    assert draws["eq_SP500"] < -2.0
    assert draws["spread_HY"] > 2.0


def test_query_scenarios_and_or_grammar(conn, mar):
    factors = np.load(str(ESG_MAR / "sim_factors.npy"))
    q = tools.query_scenarios(
        mar, "spread_HY > 0.02 and equity_SP500 < -0.10")
    expected = int(((factors[:, 15] > 0.02)
                    & (factors[:, 18] < -0.10)).sum())
    assert q["n_matching"] == expected
    q_or = tools.query_scenarios(
        mar, "spread_HY > 0.02 or equity_SP500 < -0.10")
    expected_or = int(((factors[:, 15] > 0.02)
                       | (factors[:, 18] < -0.10)).sum())
    assert q_or["n_matching"] == expected_or


def test_query_scenarios_is_parsed_never_evaluated(conn, mar):
    """The where clause is a tiny parsed grammar — arbitrary Python must
    not reach an interpreter."""
    for bad in ("__import__('os').system('echo hi') < 1",
                "import os",
                "1 < 2",
                "surplus_pnl < (1 + 1)"):
        with pytest.raises(tools.ToolError):
            tools.query_scenarios(mar, bad)
    with pytest.raises(tools.ToolError, match="unknown variable"):
        tools.query_scenarios(mar, "gdp_growth < 0")
    with pytest.raises(tools.ToolError, match="where must be"):
        tools.query_scenarios(mar, "")


def test_query_scenarios_empty_match_is_reported_not_faked(conn, mar):
    q = tools.query_scenarios(mar, "surplus_pnl < -10e9")
    assert q["n_matching"] == 0
    assert "no simulated scenario" in q["note"]
    assert "surplus_pnl_gbp" not in q


# ==========================================================================
# the scenario tools are reachable as tools (registry + live-mode specs)
# ==========================================================================

def test_scenario_tools_are_registered_and_specified():
    names = ("read_scenario", "tail_analysis", "price_scenario",
             "query_scenarios")
    for n in names:
        assert n in tools.REGISTRY
    specs = {s["name"]: s for s in tools.TOOL_SPECS}
    for n in names:
        assert n in specs, f"{n} missing from TOOL_SPECS (live mode)"
        assert specs[n]["input_schema"]["type"] == "object"


def test_scenario_tool_calls_are_recorded_and_bounded(conn, mar):
    s = tools.ToolSession(run_id=mar, max_calls=3)
    tc, res = s.call("price_scenario", asof=mar, shocks={"gbp_swap": 0.01})
    stored = json.loads(tools.fetch_result_json(tc))
    assert stored["delta"]["surplus_gbp"] == pytest.approx(
        res["delta"]["surplus_gbp"])
    # values quoted from the result bind under the citation gate
    assert citation.value_in_result(res["delta"]["surplus_gbp"],
                                    tools.fetch_result_json(tc))
    s.call("read_scenario", run=mar)
    s.call("tail_analysis", run=mar, n=25)
    with pytest.raises(tools.ToolLimitError):
        s.call("query_scenarios", run=mar, where="surplus_pnl < 0")


# ==========================================================================
# @warden — the month-end summary
# ==========================================================================

HEADLINE_RE = re.compile(
    r"^\*\*AuM (£[\d,.]+[kmb]n?), ([+-]£[\d,.]+[kmb]n?|nil)\*\* — "
    r"premium (-?£[\d,.]+[kmb]n?|nil) · "
    r"investment performance (-?£[\d,.]+[kmb]n?|nil) · "
    r"\*\*VaR (£[\d,.]+[kmb]n?) \((\d+\.\d)% of assets\), "
    r"([+-]£[\d,.]+[kmb]n?|nil)\*\*")


def _run_row(conn, run_id):
    if run_id is None:
        return None
    return conn.execute("SELECT * FROM runs WHERE id = ?",
                        (run_id,)).fetchone()


def _Ctx(conn, room, prev_id, curr_id, seeded=False):
    """A PassContext over registered runs (no engine re-run needed)."""
    return agents_api.PassContext(room, _run_row(conn, prev_id),
                                  _run_row(conn, curr_id), seeded)


@pytest.fixture(scope="module")
def warden_drafts(conn, feb, marbook):
    ctx = _Ctx(conn, 3, feb, marbook)
    return warden(ctx), ctx


def test_warden_headline_is_exactly_the_specified_form(warden_drafts):
    drafts, _ = warden_drafts
    body = drafts[0]["body"]
    m = HEADLINE_RE.match(body)
    assert m is not None, f"headline does not match the required form:\n{body}"
    aum, d_aum, premium, perf, var, pct, d_var = m.groups()
    # money in, money made, risk carried — in that order, one line
    assert aum.startswith("£") and var.startswith("£")
    assert float(pct) > 0


def test_warden_makes_the_written_business_finding(warden_drafts):
    drafts, _ = warden_drafts
    body = drafts[0]["body"]
    assert "written business, not investment performance" in body
    assert "similar magnitude, same direction" in body
    # both sides of the balance sheet are reconciled
    assert "Liabilities" in body and "discounting and translation" in body
    assert "reserve movement the discount curves do not account for" in body


def test_warden_separates_pro_rata_from_the_deliberate_tilt(warden_drafts):
    drafts, _ = warden_drafts
    body = drafts[0]["body"]
    assert "private credit took" in body and "pro-rata share of" in body
    assert "it is a decision" in body
    work = drafts[1]["body"]
    assert "| sleeve | flow | pro-rata | vs pro-rata |" in work
    assert "private credit" in work


def test_warden_reconciles_to_the_engine_attribution(conn, warden_drafts):
    """@warden's flows must be the engine's own attribution steps 8 and 9 —
    the claims are checked against the tool results, so this pins the
    NUMBERS, not just the words."""
    if not (ATTR_BOOKS / "attribution.json").exists():
        pytest.skip("book-pair attribution outputs not present")
    steps = {s["name"]: s["delta_gbp"] for s in
             _json(ATTR_BOOKS / "attribution.json")["mtm"]["steps"]}
    claims = drafts_claims(warden_drafts[0])
    values = [abs(c["value"]) for c in claims]
    assert any(abs(v - abs(steps["book"])) < 1.0 for v in values), \
        "the asset flow (attribution step 8) is not quoted"
    assert any(abs(v - abs(steps["liabilities"])) < 1.0 for v in values), \
        "the liability flow (attribution step 9) is not quoted"


def drafts_claims(drafts):
    out = []
    for d in drafts:
        out.extend(d.get("claims") or [])
    return out


def test_warden_posts_are_pinned_and_significant(warden_drafts):
    drafts, _ = warden_drafts
    assert drafts[0]["kind"] == "origin"
    assert drafts[0]["pinned"] is True
    assert drafts[0]["significance"] in ("notable", "critical")
    assert drafts[1]["kind"] == "expansion"


def test_warden_sources_chip_through_to_the_analysis(conn, feb, marbook):
    """Run the whole room-3 pass: @warden runs last and its post must carry
    source chips down into the analysis it summarises (SPEC-APP H)."""
    ids = agents_api.run_room_pass(3, feb, marbook, seeded=False)
    marks = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT * FROM posts WHERE id IN ({marks}) ORDER BY id",
        list(ids)).fetchall()
    bad = [r["suppression_reason"] for r in rows
           if r["status"] != "published"]
    assert not bad, bad
    wardens = [r for r in rows if r["author_label"] == "@warden"]
    assert wardens, "@warden must post every cycle"
    origin = [r for r in wardens if r["type"] == "origin"][0]
    sources = json.loads(origin["sources_json"] or "[]")
    assert sources, "the summary should chip through to the analysis"
    others = {r["id"] for r in rows if r["author_label"] != "@warden"}
    assert set(sources) <= others


def test_warden_publishes_with_every_claim_bound(conn, feb, marbook):
    ctx = _Ctx(conn, 3, feb, marbook)
    agent = conn.execute("SELECT * FROM agents WHERE handle = '@warden'"
                         ).fetchone()
    ids = agents_api.publish_drafts(ctx, agent, warden(ctx))
    assert len(ids) == 2
    for pid in ids:
        post = conn.execute("SELECT * FROM posts WHERE id = ?",
                            (pid,)).fetchone()
        assert post["status"] == "published", post["suppression_reason"]
        claims = json.loads(post["claims_json"]) if post["claims_json"] else []
        assert claims and all(c["tool_call_id"] for c in claims)
        n_calls = conn.execute(
            "SELECT COUNT(*) AS n FROM tool_calls WHERE post_id = ?",
            (pid,)).fetchone()["n"]
        assert n_calls <= config.MAX_TOOL_CALLS_PER_POST


def test_warden_always_posts_even_with_no_flows(conn, feb, mar):
    """Same book and same cohort file both sides: the honest answer is that
    the whole move is market — and the summary still posts (minimum
    significance routine)."""
    ctx = _Ctx(conn, 3, feb, mar)
    drafts = warden(ctx)
    assert drafts and drafts[0]["significance"] in ("routine", "notable",
                                                    "critical")
    body = drafts[0]["body"]
    assert HEADLINE_RE.match(body)
    assert "no written business in this pair" in body
    assert "the whole movement is market" in body


def test_warden_posts_a_single_month_summary_without_a_pair(conn, mar):
    ctx = _Ctx(conn, 3, None, mar)
    drafts = warden(ctx)
    assert len(drafts) == 1
    assert drafts[0]["pinned"] is True
    assert drafts[0]["significance"] == "routine"
    assert "**AuM " in drafts[0]["body"]
    assert "no market / flows / decision split" in drafts[0]["body"]


def test_warden_runs_last_in_room_three(conn):
    from app.agents.checks import ROOM_CHECKS
    handles = [h for h, _ in ROOM_CHECKS[3]]
    assert "@warden" in handles
    # it reads everyone, so no ANALYSIS may follow it — only @red-team's
    # closing challenge (SPEC-APP H.1, the last voice) and @story, which
    # reads the whole room including @warden and is pinned above it
    # (PENDING-BATCH2 §10).
    after = handles[handles.index("@warden") + 1:]
    assert set(after) <= {"@red-team", "@story"}
    assert handles[-1] == "@story"


def test_story_runs_last_and_is_pinned_first_in_every_room(conn):
    """PENDING-BATCH2 §10: one @story post per room, running LAST in each
    pass (it reads everything in the room) and flagged for pinning FIRST.
    Pinned posts are ordered newest-first, so running after @warden is
    exactly what puts @story above it in the feed."""
    from app.agents import api as agents_api_mod
    from app.agents.checks import ROOM_CHECKS
    for room in (1, 2, 3):
        handles = [h for h, _ in ROOM_CHECKS[room]]
        assert handles.count("@story") == 1, room
        assert handles[-1] == "@story", room
        ordered = agents_api_mod._topological_room_order(
            conn, ROOM_CHECKS[room])
        assert ordered[-1][0] == "@story", room


# ==========================================================================
# @lily — liabilities
# ==========================================================================

@pytest.fixture(scope="module")
def lily_drafts(conn, feb, marbook):
    return lily(_Ctx(conn, 3, feb, marbook))


def test_lily_quantitative_owns_the_duration_gap(lily_drafts):
    body = lily_drafts[0]["body"]
    assert "duration gap" in body
    assert "The duration gap is mine to own" in body
    ps = tools.price_scenario("2026-03", book="positions_2026-03.json",
                              liabilities="liabilities_2026-03.json")
    d = ps["durations"]
    # the numbers in the post are the tool's numbers
    vals = [abs(c["value"]) for c in lily_drafts[0]["claims"]]
    for expected in (d["liabilities_years"], d["assets_fixed_income_years"],
                     abs(d["duration_gap_years"])):
        assert any(abs(v - expected) < 1e-6 for v in vals)


def test_lily_explains_why_a_rate_rise_raises_surplus(lily_drafts):
    body = lily_drafts[0]["body"]
    assert "bad market can be a good month" in body
    assert "cuts liability PV" in body and "surplus +" in body


def test_lily_quantifies_the_liability_share_of_each_block(lily_drafts):
    body = lily_drafts[0]["body"]
    for block in ("ir_gbp", "ir_usd", "fx"):
        assert block in body
    assert "of the gross rate exposure sits on my side" in body


def test_lily_context_post_carries_no_numbers_at_all(lily_drafts):
    context = [d for d in lily_drafts if d.get("context")]
    assert len(context) == 1
    body = context[0]["body"]
    assert not context[0]["claims"]
    assert not citation.numeric_tokens(body)
    assert "enters no calculation" in body
    # she states the limit rather than implying a capability
    assert "no catastrophe model" in body
    assert "forced sales" in body


def test_lily_publishes_and_the_quarantine_holds(conn, feb, marbook):
    ctx = _Ctx(conn, 3, feb, marbook)
    agent = conn.execute("SELECT * FROM agents WHERE handle = '@lily'"
                         ).fetchone()
    assert agent is not None and agent["room"] == 3
    ids = agents_api.publish_drafts(ctx, agent, lily(ctx))
    assert len(ids) == 3
    for pid in ids:
        post = conn.execute("SELECT * FROM posts WHERE id = ?",
                            (pid,)).fetchone()
        assert post["status"] == "published", post["suppression_reason"]
    ctx_post = conn.execute(
        "SELECT * FROM posts WHERE id = ? ", (ids[2],)).fetchone()
    assert not (json.loads(ctx_post["claims_json"])
                if ctx_post["claims_json"] else [])


def test_lily_is_registered_as_an_outward_and_internal_agent():
    from app.agents import personas
    p = personas.by_handle("@lily")
    assert p is not None and p["room"] == 3
    assert p.get("outlook") == "both"       # SPEC-APP E
    assert "@lily" not in personas.CONTEXT_HANDLES, \
        "only her context POST is quarantined, not the whole persona"


# ==========================================================================
# @vlad — the per-run VaR-scenario duty
# ==========================================================================

@pytest.fixture(scope="module")
def vlad_body(conn, feb, mar):
    drafts = vlad(_Ctx(conn, 2, feb, mar))
    return drafts[0]


def test_vlad_opens_the_var_scenario_itself(vlad_body):
    body = vlad_body["body"]
    assert "I opened the VaR scenario itself" in body
    assert "Loss rank 250 of 50,000" in body
    assert "vols" in body


def test_vlad_reads_joint_plausibility(vlad_body):
    body = vlad_body["body"]
    assert "Mahalanobis distance" in body and "chi-squared" in body
    # the clean March run IS a coherent risk-off draw, so no flag
    assert "**FLAG — joint plausibility.**" not in body
    assert "coherent risk-off state of the world" in body
    assert "dependency the calibration does not contain" in body


def test_vlad_publishes_with_bound_claims(conn, feb, mar):
    ctx = _Ctx(conn, 2, feb, mar)
    agent = conn.execute("SELECT * FROM agents WHERE handle = '@vlad'"
                         ).fetchone()
    ids = agents_api.publish_drafts(ctx, agent, vlad(ctx))
    post = conn.execute("SELECT * FROM posts WHERE id = ?",
                        (ids[0],)).fetchone()
    assert post["status"] == "published", post["suppression_reason"]
    n_calls = conn.execute(
        "SELECT COUNT(*) AS n FROM tool_calls WHERE post_id = ?",
        (ids[0],)).fetchone()["n"]
    assert n_calls <= config.MAX_TOOL_CALLS_PER_POST


def test_vlad_degrades_honestly_without_saved_simulations(conn):
    """No retained arrays: he says he could not open the scenario rather
    than inventing a read of it."""
    out = _TMP / "no_sims"
    out.mkdir(exist_ok=True)
    for name in ("valuation.json", "var_aggregate.json"):
        (out / name).write_text((OUT_MAR / name).read_text(encoding="utf-8"),
                                encoding="utf-8")
    rid = _register(conn, "2026-03", out)
    drafts = vlad(_Ctx(conn, 2, None, rid))
    body = drafts[0]["body"]
    assert ("could not open the VaR scenario" in body
            or "could not resolve them" in body)
