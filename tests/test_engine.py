"""Engine tests on a small hand-built toy book (SPEC sections 1-5).

Fixtures (toy assumptions YAML, 6-position book, liabilities) are written
deterministically into tests/fixtures/ so the engine CLIs can also be run
against them end-to-end.
"""

from __future__ import annotations

import copy
import json
import math
import os
import subprocess
import sys

import numpy as np
import pytest
import yaml

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from engine import curves, esg, pricing, var  # noqa: E402
from engine.run import run_engine  # noqa: E402
from engine.attribution import run_attribution  # noqa: E402

FIXTURES = os.path.join(PROJECT_ROOT, "tests", "fixtures")


# ---------------------------------------------------------------------------
# Toy fixture construction
# ---------------------------------------------------------------------------

def toy_assumptions() -> dict:
    """Small hand-built assumptions dict. Flat curves for hand computation."""
    n = esg.N_FACTORS
    # All pairwise correlation 0.25: C = 0.75*I + 0.25*J (PSD by construction).
    corr = [[1.0 if i == j else 0.25 for j in range(n)] for i in range(n)]
    tenor_flat = lambda v: {2: v, 5: v, 10: v, 20: v}
    return {
        "meta": {"asof": "2026-06-30", "calibration_window_days": 504,
                 "sources": ["toy fixture built in tests/test_engine.py"]},
        "curves": {
            "gbp_swap": tenor_flat(0.04),
            "gbp_gilt": tenor_flat(0.04),
            "ust": tenor_flat(0.03),
        },
        "spreads": {"AA": 0.005, "A": 0.01, "BBB": 0.02, "HY": 0.05,
                    "CCC": 0.10},
        "equity": {"FTSE100": 8000.0, "SP500": 6000.0, "SX5E": 5200.0},
        "fx": {"GBPUSD": 1.25},
        "vols": {
            "gbp_swap": tenor_flat(0.008),
            "gbp_gilt": tenor_flat(0.008),
            "ust": tenor_flat(0.009),
            "spread": {"AA": 0.002, "A": 0.003, "BBB": 0.005, "HY": 0.01,
                       "CCC": 0.02},
            "equity": {"FTSE100": 0.15, "SP500": 0.16, "SX5E": 0.17},
            "fx": {"GBPUSD": 0.08},
        },
        "correlation": {"order": list(esg.FACTOR_ORDER), "matrix": corr},
    }


def toy_book() -> dict:
    """6-position toy book; ref index levels match the toy asof (scale 1)."""
    return {
        "ref_asof": "2026-06-30",
        "ref_index_levels": {"FTSE100": 8000.0, "SP500": 6000.0, "SX5E": 5200.0},
        "positions": [
            {"id": "T001", "type": "govt_bond", "name": "Toy gilt 5% 8y",
             "isin": "GB0000000001", "currency": "GBP", "notional": 1000000,
             "coupon": 0.05, "maturity_years": 8, "curve": "gbp_gilt"},
            {"id": "T002", "type": "corp_bond", "name": "Toy USD BBB 4.5% 2y",
             "isin": "US0000000002", "currency": "USD", "notional": 2000000,
             "coupon": 0.045, "maturity_years": 2, "curve": "ust",
             "rating": "BBB"},
            {"id": "T003", "type": "equity", "name": "Toy UK equity",
             "isin": "GB0000000003", "currency": "GBP", "index": "FTSE100",
             "quantity": 10000, "ref_price": 10.0},
            {"id": "T004", "type": "equity", "name": "Toy US equity",
             "isin": "US0000000004", "currency": "USD", "index": "SP500",
             "quantity": 5000, "ref_price": 40.0},
            {"id": "T005", "type": "cash", "name": "GBP cash",
             "currency": "GBP", "amount": 500000},
            {"id": "T006", "type": "cash", "name": "USD cash",
             "currency": "USD", "amount": 1000000},
        ],
    }


def toy_liabilities() -> dict:
    return {"currency": "GBP",
            "cashflows": [{"t": t, "amount": 100000} for t in range(1, 11)]}


def toy_assumptions_next() -> dict:
    """A second month-end for attribution: every block moves."""
    a = toy_assumptions()
    a["meta"]["asof"] = "2026-07-31"
    a["curves"]["gbp_swap"] = {2: 0.042, 5: 0.043, 10: 0.044, 20: 0.045}
    a["curves"]["gbp_gilt"] = {2: 0.043, 5: 0.044, 10: 0.045, 20: 0.046}
    a["curves"]["ust"] = {2: 0.028, 5: 0.029, 10: 0.031, 20: 0.033}
    a["spreads"] = {"AA": 0.006, "A": 0.012, "BBB": 0.025, "HY": 0.045,
                    "CCC": 0.11}
    a["equity"] = {"FTSE100": 8400.0, "SP500": 5800.0, "SX5E": 5300.0}
    a["fx"] = {"GBPUSD": 1.30}
    a["vols"]["equity"] = {"FTSE100": 0.17, "SP500": 0.18, "SX5E": 0.19}
    a["vols"]["fx"] = {"GBPUSD": 0.09}
    n = esg.N_FACTORS
    a["correlation"]["matrix"] = [
        [1.0 if i == j else 0.30 for j in range(n)] for i in range(n)]
    return a


@pytest.fixture(scope="session")
def fixture_paths():
    """Write toy fixtures to tests/fixtures/ and return their paths."""
    os.makedirs(FIXTURES, exist_ok=True)
    paths = {
        "assumptions": os.path.join(FIXTURES, "toy_assumptions.yaml"),
        "assumptions_next": os.path.join(FIXTURES, "toy_assumptions_next.yaml"),
        "book": os.path.join(FIXTURES, "toy_positions.json"),
        "liabilities": os.path.join(FIXTURES, "toy_liabilities.json"),
    }
    with open(paths["assumptions"], "w", encoding="utf-8") as f:
        yaml.safe_dump(toy_assumptions(), f, sort_keys=False)
    with open(paths["assumptions_next"], "w", encoding="utf-8") as f:
        yaml.safe_dump(toy_assumptions_next(), f, sort_keys=False)
    with open(paths["book"], "w", encoding="utf-8") as f:
        json.dump(toy_book(), f, indent=2)
    with open(paths["liabilities"], "w", encoding="utf-8") as f:
        json.dump(toy_liabilities(), f, indent=2)
    return paths


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------

def test_govt_bond_matches_hand_computed_discount_sum():
    """Gilt, flat 4% curve: price = sum cf * 1.04^-t, to 1e-10."""
    a = toy_assumptions()
    state = esg.base_state(a)
    pos = toy_book()["positions"][0]  # 1m notional, 5% coupon, 8y
    engine_px = pricing.value_position(pos, state)[0]
    hand = sum(50000.0 * 1.04 ** (-t) for t in range(1, 9)) + 1000000.0 * 1.04 ** (-8)
    assert math.isclose(engine_px, hand, rel_tol=1e-10, abs_tol=1e-10)


def test_corp_bond_spread_term_profile_hand_computed():
    """USD BBB 2y: z(t) = ust + 0.02 * M(t); M = 0.85 at and below 2y."""
    a = toy_assumptions()
    state = esg.base_state(a)
    pos = toy_book()["positions"][1]  # 2m notional, 4.5%, 2y, BBB on ust
    z = 0.03 + 0.02 * 0.85  # flat ust 3% + BBB level * M(<=2y)
    usd_px = 90000.0 / (1 + z) + 2090000.0 / (1 + z) ** 2
    hand_gbp = usd_px / 1.25
    engine_px = pricing.value_position(pos, state)[0]
    assert math.isclose(engine_px, hand_gbp, rel_tol=1e-10, abs_tol=1e-10)


def test_curve_interpolation_and_flat_extrapolation():
    rates = np.array([0.02, 0.03, 0.04, 0.05])  # at tenors 2, 5, 10, 20
    z = curves.interp_zero(rates, [1.0, 2.0, 3.5, 10.0, 25.0])
    assert z[0] == pytest.approx(0.02)          # flat below 2y
    assert z[1] == pytest.approx(0.02)
    assert z[2] == pytest.approx(0.02 + 0.01 * 1.5 / 3.0)  # linear 2y-5y
    assert z[3] == pytest.approx(0.04)
    assert z[4] == pytest.approx(0.05)          # flat above 20y


def test_equity_scaling_by_index_move_since_ref_asof():
    """Value = quantity * ref_price * index(asof) / index(ref_asof)."""
    a = toy_assumptions()
    book = toy_book()
    ref_levels = book["ref_index_levels"]
    eq = book["positions"][2]  # FTSE100, 10000 * 10.0

    base_val = pricing.value_position(eq, esg.base_state(a), ref_levels)[0]
    assert base_val == pytest.approx(100000.0)  # base run: scale is 1

    a2 = copy.deepcopy(a)
    a2["equity"]["FTSE100"] = 8800.0  # +10% index move since ref_asof
    moved = pricing.value_position(eq, esg.base_state(a2), ref_levels)[0]
    assert moved == pytest.approx(110000.0, rel=1e-12)


def test_fx_conversion_both_directions():
    assert pricing.usd_to_gbp(1250000.0, 1.25) == pytest.approx(1000000.0)
    assert pricing.gbp_to_usd(1000000.0, 1.25) == pytest.approx(1250000.0)
    # Round trip is identity.
    assert pricing.gbp_to_usd(pricing.usd_to_gbp(987654.321, 1.31), 1.31) == \
        pytest.approx(987654.321, rel=1e-14)
    # USD cash position carries FX risk only: GBP value = USD / GBPUSD.
    a = toy_assumptions()
    usd_cash = toy_book()["positions"][5]
    val = pricing.value_position(usd_cash, esg.base_state(a))[0]
    assert val == pytest.approx(1000000.0 / 1.25)
    # GBPUSD up => GBP value of USD assets down (and vice versa).
    a_up = copy.deepcopy(a)
    a_up["fx"]["GBPUSD"] = 1.40
    assert pricing.value_position(usd_cash, esg.base_state(a_up))[0] < val


def test_liability_pv_on_gbp_swap_curve():
    a = toy_assumptions()
    pv = pricing.pv_liabilities(toy_liabilities(), esg.base_state(a))[0]
    hand = sum(100000.0 * 1.04 ** (-t) for t in range(1, 11))
    assert math.isclose(pv, hand, rel_tol=1e-10, abs_tol=1e-10)


# ---------------------------------------------------------------------------
# ESG / shocks
# ---------------------------------------------------------------------------

def test_spread_floor_binds_on_huge_negative_shock():
    a = toy_assumptions()
    state = esg.base_state(a)
    shocks = np.zeros((1, esg.N_FACTORS))
    shocks[0, 14] = -1.0  # spread_BBB shocked hugely negative
    shocked = esg.apply_shocks(state, shocks)
    assert shocked["spreads"][0, 2] == 0.0          # floored at exactly 0
    assert shocked["spreads"][0, 0] == pytest.approx(0.005)  # AA untouched

    # With huge spread vols, many sims breach; floor must bind everywhere.
    a_big = copy.deepcopy(a)
    a_big["vols"]["spread"] = {"AA": 2.0, "A": 2.0, "BBB": 2.0, "HY": 2.0,
                               "CCC": 2.0}
    sim = esg.simulate_shocks(a_big, 2000, seed=7)
    shocked = esg.apply_shocks(state, sim)
    assert shocked["spreads"].min() == 0.0
    assert (shocked["spreads"] >= 0.0).all()
    # A floored spread prices the corp bond exactly on the govy curve.
    floored = {"curves": {k: v[None, :] for k, v in state["curves"].items()},
               "spreads": np.zeros((1, len(esg.RATINGS))),
               "equity": state["equity"][None, :],
               "fx": np.atleast_1d(state["fx"])}
    corp = toy_book()["positions"][1]
    on_govy = pricing.value_position(corp, floored)[0]
    hand = (90000.0 / 1.03 + 2090000.0 / 1.03 ** 2) / 1.25
    assert math.isclose(on_govy, hand, rel_tol=1e-10, abs_tol=1e-10)


def test_frozen_factors_leave_state_unchanged():
    a = toy_assumptions()
    state = esg.base_state(a)
    shocks = esg.simulate_shocks(a, 100, seed=3)
    shocked = esg.apply_shocks(state, shocks, live_mask=var.block_mask("fx"))
    for c in esg.CURVE_NAMES:
        assert np.array_equal(shocked["curves"][c],
                              np.tile(state["curves"][c], (100, 1)))
    assert np.array_equal(shocked["equity"],
                          np.tile(state["equity"], (100, 1)))
    assert not np.array_equal(shocked["fx"], np.full(100, state["fx"]))


# ---------------------------------------------------------------------------
# VaR
# ---------------------------------------------------------------------------

def test_aggregate_var_le_sum_of_block_standalones(fixture_paths):
    a = toy_assumptions()
    book = toy_book()
    liabs = toy_liabilities()
    shocks = esg.simulate_shocks(a, 5000, seed=20260831)
    blocks = var.block_standalone_vars(a, book["positions"], liabs, shocks,
                                       book["ref_index_levels"])
    agg = var.aggregate_var(a, book["positions"], liabs, shocks,
                            book["ref_index_levels"])
    assert set(blocks) == {"ir_gbp", "ir_usd", "credit", "equity", "fx"}
    assert all(v >= 0.0 for v in blocks.values())
    assert agg <= sum(blocks.values()) + 1e-9


def test_var_is_positive_995_loss():
    pnl = np.arange(-1000.0, 1000.0)  # symmetric sample
    v = var.var_from_pnl(pnl)
    assert v == pytest.approx(-np.quantile(pnl, 0.005))
    assert v > 0


# ---------------------------------------------------------------------------
# Run CLI: end-to-end + seed determinism
# ---------------------------------------------------------------------------

def _run_cli(args):
    return subprocess.run([sys.executable, "-m"] + args, cwd=PROJECT_ROOT,
                          capture_output=True, text=True)


OUTPUT_FILES = ["valuation.json", "var_standalone_positions.csv",
                "var_standalone_factors.json", "var_aggregate.json",
                "sim_pnl_sample.csv"]


def test_run_cli_end_to_end(fixture_paths):
    out_dir = os.path.join(FIXTURES, "out")
    res = _run_cli(["engine.run",
                    "--assumptions", fixture_paths["assumptions"],
                    "--book", fixture_paths["book"],
                    "--liabilities", fixture_paths["liabilities"],
                    "--out", out_dir, "--seed", "20260831", "--sims", "5000"])
    assert res.returncode == 0, res.stderr
    for name in OUTPUT_FILES:
        assert os.path.exists(os.path.join(out_dir, name)), name

    with open(os.path.join(out_dir, "valuation.json")) as f:
        val = json.load(f)
    assert len(val["positions"]) == 6
    assert val["meta"]["seed"] == 20260831
    assert val["meta"]["n_sims"] == 5000
    assert len(val["meta"]["assumptions_sha256"]) == 64
    assert len(val["meta"]["book_sha256"]) == 64
    assert val["asset_total_gbp"] == pytest.approx(
        sum(p["market_value_gbp"] for p in val["positions"]))
    assert val["surplus_gbp"] == pytest.approx(
        val["asset_total_gbp"] - val["liability_pv_gbp"])

    with open(os.path.join(out_dir, "sim_pnl_sample.csv")) as f:
        lines = f.read().strip().splitlines()
    assert len(lines) == 1001  # header + first 1000 sims

    with open(os.path.join(out_dir, "var_aggregate.json")) as f:
        agg = json.load(f)
    assert agg["diversification_benefit_gbp"] >= -1e-9


def test_seed_determinism_two_runs_bit_identical(fixture_paths):
    out_a = os.path.join(FIXTURES, "out_det_a")
    out_b = os.path.join(FIXTURES, "out_det_b")
    for out_dir in (out_a, out_b):
        run_engine(fixture_paths["assumptions"], fixture_paths["book"],
                   fixture_paths["liabilities"], out_dir,
                   seed=123, n_sims=3000)
    for name in OUTPUT_FILES:
        with open(os.path.join(out_a, name), "rb") as f:
            bytes_a = f.read()
        with open(os.path.join(out_b, name), "rb") as f:
            bytes_b = f.read()
        assert bytes_a == bytes_b, "output %s differs between runs" % name


def test_different_seed_changes_simulation(fixture_paths):
    a = toy_assumptions()
    s1 = esg.simulate_shocks(a, 500, seed=1)
    s1_again = esg.simulate_shocks(a, 500, seed=1)
    s2 = esg.simulate_shocks(a, 500, seed=2)
    assert np.array_equal(s1, s1_again)
    assert not np.array_equal(s1, s2)


# ---------------------------------------------------------------------------
# Attribution
# ---------------------------------------------------------------------------

def test_attribution_steps_plus_residual_equal_total(fixture_paths):
    prev_out = os.path.join(FIXTURES, "out_attr_prev")
    curr_out = os.path.join(FIXTURES, "out_attr_curr")
    seed, sims = 20260831, 3000
    run_engine(fixture_paths["assumptions"], fixture_paths["book"],
               fixture_paths["liabilities"], prev_out, seed=seed, n_sims=sims)
    run_engine(fixture_paths["assumptions_next"], fixture_paths["book"],
               fixture_paths["liabilities"], curr_out, seed=seed, n_sims=sims)

    attr_out = os.path.join(FIXTURES, "out_attr")
    attr = run_attribution(prev_out, curr_out,
                           fixture_paths["assumptions"],
                           fixture_paths["assumptions_next"],
                           fixture_paths["book"], fixture_paths["liabilities"],
                           attr_out)
    assert os.path.exists(os.path.join(attr_out, "attribution.json"))

    for section in ("mtm", "var"):
        s = attr[section]
        step_names = [st["name"] for st in s["steps"]]
        assert step_names == ["gbp_swap", "gbp_gilt", "ust", "spread",
                              "equity", "fx", "vcv", "book", "liabilities"]
        total = s["total_change_gbp"]
        sum_steps = sum(st["delta_gbp"] for st in s["steps"])
        # Steps + explicit residual == total, to 1e-8.
        assert abs(sum_steps + s["residual_gbp"] - total) < 1e-8
        assert s["additivity_check"]["additive_within_1e-6"] is True

    # Sequential recompute with the stored runs' seed reproduces the stored
    # endpoints, so the explicit residual is (near) zero here.
    assert abs(attr["mtm"]["residual_gbp"]) < 1e-6
    assert abs(attr["var"]["residual_gbp"]) < 1e-6

    # MTM: the VCV step changes no market level, and the book is unchanged.
    mtm_steps = {st["name"]: st["delta_gbp"] for st in attr["mtm"]["steps"]}
    assert mtm_steps["vcv"] == 0.0
    assert mtm_steps["book"] == 0.0
    assert mtm_steps["liabilities"] == 0.0  # single --liabilities: exactly 0
    # VaR: the VCV step matters (vols and correlation moved).
    var_steps = {st["name"]: st["delta_gbp"] for st in attr["var"]["steps"]}
    assert var_steps["vcv"] != 0.0

    # gbp_swap step moves liabilities only (no asset prices on gbp_swap in
    # the toy book) => rates up means liability PV down means surplus up.
    assert mtm_steps["gbp_swap"] > 0.0


# ---------------------------------------------------------------------------
# 21-factor structure (SPEC sections 2 and 6)
# ---------------------------------------------------------------------------

def test_factor_set_is_21_in_spec_order():
    """FACTOR_ORDER must be exactly the SPEC section 6 21-name list."""
    assert esg.N_FACTORS == 21
    assert esg.FACTOR_ORDER == [
        "gbp_swap_2", "gbp_swap_5", "gbp_swap_10", "gbp_swap_20",
        "gbp_gilt_2", "gbp_gilt_5", "gbp_gilt_10", "gbp_gilt_20",
        "ust_2", "ust_5", "ust_10", "ust_20",
        "spread_AA", "spread_A", "spread_BBB", "spread_HY", "spread_CCC",
        "eq_FTSE100", "eq_SP500", "eq_SX5E", "fx_GBPUSD",
    ]
    assert esg.RATINGS == ["AA", "A", "BBB", "HY", "CCC"]
    # VaR blocks partition the 21 factors exactly, in SPEC section 4 shape.
    all_idx = sorted(i for idxs in var.BLOCKS.values() for i in idxs)
    assert all_idx == list(range(21))
    assert var.BLOCKS["credit"] == list(range(12, 17))
    assert var.BLOCKS["equity"] == list(range(17, 20))
    assert var.BLOCKS["fx"] == [20]


def test_21_factor_simulation_shape_and_determinism():
    a = toy_assumptions()
    s = esg.simulate_shocks(a, 400, seed=11)
    assert s.shape == (400, 21)
    assert np.array_equal(s, esg.simulate_shocks(a, 400, seed=11))
    # The CCC factor is live and distinct from HY.
    ccc = s[:, esg.FACTOR_ORDER.index("spread_CCC")]
    hy = s[:, esg.FACTOR_ORDER.index("spread_HY")]
    assert np.abs(ccc).max() > 0.0
    assert not np.array_equal(ccc, hy)
    # A 20-factor (legacy) correlation matrix must be rejected loudly.
    a20 = copy.deepcopy(a)
    a20["correlation"]["order"] = [f for f in esg.FACTOR_ORDER
                                   if f != "spread_CCC"]
    a20["correlation"]["matrix"] = [
        [1.0 if i == j else 0.25 for j in range(20)] for i in range(20)]
    with pytest.raises(ValueError):
        esg.correlation_matrix(a20)


def test_ccc_corp_bond_hand_check():
    """GBP CCC 3y on flat 4% gilt curve: z(t) = 0.04 + 0.10 * M(t).

    M(1) = M(2) = 0.85 (flat below 2y); M(3) = 0.85 + (1/3)*0.15 = 0.90.
    """
    a = toy_assumptions()
    state = esg.base_state(a)
    pos = {"id": "TC01", "type": "corp_bond", "name": "Toy GBP CCC 9% 3y",
           "isin": "GB0000000099", "currency": "GBP", "notional": 1000000,
           "coupon": 0.09, "maturity_years": 3, "curve": "gbp_gilt",
           "rating": "CCC"}
    z1 = 0.04 + 0.10 * 0.85
    z2 = 0.04 + 0.10 * 0.85
    z3 = 0.04 + 0.10 * (0.85 + (1.0 / 3.0) * 0.15)
    hand = (90000.0 / (1 + z1)
            + 90000.0 / (1 + z2) ** 2
            + 1090000.0 / (1 + z3) ** 3)
    engine_px = pricing.value_position(pos, state)[0]
    assert math.isclose(engine_px, hand, rel_tol=1e-10, abs_tol=1e-10)
    # Monotonicity: same bond proxied HY (5% spread) prices strictly higher.
    hy_px = pricing.value_position(dict(pos, rating="HY"), state)[0]
    assert hy_px > engine_px


def test_unknown_position_metadata_fields_are_inert():
    """asset_class / strategy / unknown fields never change a price or error."""
    a = toy_assumptions()
    state = esg.base_state(a)
    base = {"id": "PCF-001", "type": "corp_bond",
            "name": "Toy PC fund proxy 8% 4y", "currency": "GBP",
            "notional": 5000000, "coupon": 0.08, "maturity_years": 4,
            "curve": "gbp_gilt", "rating": "HY"}
    decorated = dict(base, asset_class="private_credit",
                     strategy="senior direct lending",
                     some_future_field={"nested": [1, 2, 3]})
    assert pricing.value_position(decorated, state)[0] == \
        pricing.value_position(base, state)[0]
    # Same for equities and cash.
    eq = toy_book()["positions"][2]
    assert pricing.value_position(dict(eq, asset_class="x", strategy="y"),
                                  state)[0] == \
        pricing.value_position(eq, state)[0]
    cash = toy_book()["positions"][4]
    assert pricing.value_position(dict(cash, asset_class="x"), state)[0] == \
        pricing.value_position(cash, state)[0]


# ---------------------------------------------------------------------------
# Attribution step 8: two books (SPEC section 5)
# ---------------------------------------------------------------------------

def toy_book_next() -> dict:
    """Curr book: GBP cash cut 500k -> 200k, a PC-fund CCC proxy bond added.

    The proxy carries asset_class/strategy metadata (inert to the engine).
    """
    b = toy_book()
    for p in b["positions"]:
        if p["id"] == "T005":
            p["amount"] = 200000
    b["positions"].append(
        {"id": "PCF-100", "type": "corp_bond",
         "name": "Toy PC fund proxy 9% 4y", "currency": "GBP",
         "notional": 300000, "coupon": 0.09, "maturity_years": 4,
         "curve": "gbp_gilt", "rating": "CCC",
         "asset_class": "private_credit",
         "strategy": "senior direct lending"})
    return b


@pytest.fixture(scope="session")
def two_book_paths(fixture_paths):
    path = os.path.join(FIXTURES, "toy_positions_next.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(toy_book_next(), f, indent=2)
    return dict(fixture_paths, book_next=path)


def test_attribution_two_books_step8_nonzero_and_control_total(two_book_paths):
    p = two_book_paths
    seed, sims = 20260831, 3000
    prev_out = os.path.join(FIXTURES, "out_attr2_prev")
    curr_out = os.path.join(FIXTURES, "out_attr2_curr")
    run_engine(p["assumptions"], p["book"], p["liabilities"],
               prev_out, seed=seed, n_sims=sims)
    # Curr stored run uses the CURR book (with the CCC proxy + metadata):
    # exercises a full engine run over a book with unknown metadata fields.
    run_engine(p["assumptions_next"], p["book_next"], p["liabilities"],
               curr_out, seed=seed, n_sims=sims)

    attr_out = os.path.join(FIXTURES, "out_attr_two_books")
    attr = run_attribution(prev_out, curr_out,
                           p["assumptions"], p["assumptions_next"],
                           None, p["liabilities"], attr_out,
                           prev_book_path=p["book"],
                           curr_book_path=p["book_next"])

    for section in ("mtm", "var"):
        s = attr[section]
        steps = {st["name"]: st["delta_gbp"] for st in s["steps"]}
        # Step 8 carries the book change and is nonzero in both measures.
        assert steps["book"] != 0.0
        # Control total: steps + explicit residual == total; residual ~0
        # because the chain lands exactly on the stored curr run's state.
        total = s["total_change_gbp"]
        sum_steps = sum(st["delta_gbp"] for st in s["steps"])
        assert abs(sum_steps + s["residual_gbp"] - total) < 1e-8
        assert s["additivity_check"]["additive_within_1e-6"] is True
        assert abs(s["residual_gbp"]) < 1e-6

    # MTM step 8 direction: 500k cash became 200k cash + a sub-par CCC bond
    # (300k notional priced well below par) => surplus falls at the swap.
    mtm_steps = {st["name"]: st["delta_gbp"] for st in attr["mtm"]["steps"]}
    assert mtm_steps["book"] < 0.0
    # VaR step 8 direction: riskless GBP cash swapped into a CCC bond.
    var_steps = {st["name"]: st["delta_gbp"] for st in attr["var"]["steps"]}
    assert var_steps["book"] > 0.0

    # Meta records both books with hashes.
    meta = attr["meta"]
    assert meta["prev_book_sha256"] != meta["curr_book_sha256"]
    assert len(meta["prev_book_sha256"]) == 64


def test_attribution_single_book_step8_exactly_zero(two_book_paths):
    """With a single --book, step 8 must be exactly 0.0 (not merely small)."""
    p = two_book_paths
    prev_out = os.path.join(FIXTURES, "out_attr_prev")
    curr_out = os.path.join(FIXTURES, "out_attr_curr")
    if not os.path.exists(os.path.join(prev_out, "valuation.json")):
        run_engine(p["assumptions"], p["book"], p["liabilities"],
                   prev_out, seed=20260831, n_sims=3000)
    if not os.path.exists(os.path.join(curr_out, "valuation.json")):
        run_engine(p["assumptions_next"], p["book"], p["liabilities"],
                   curr_out, seed=20260831, n_sims=3000)
    attr = run_attribution(prev_out, curr_out,
                           p["assumptions"], p["assumptions_next"],
                           p["book"], p["liabilities"],
                           os.path.join(FIXTURES, "out_attr_single_book"))
    for section in ("mtm", "var"):
        steps = {st["name"]: st["delta_gbp"]
                 for st in attr[section]["steps"]}
        assert steps["book"] == 0.0
        assert steps["liabilities"] == 0.0  # single --liabilities: exact 0


def test_attribution_cli_two_books_and_arg_validation(two_book_paths):
    p = two_book_paths
    prev_out = os.path.join(FIXTURES, "out_attr2_prev")
    curr_out = os.path.join(FIXTURES, "out_attr2_curr")
    # Depends on outputs from the two-book test; rebuild if absent.
    for out_dir, a_path, b_path in (
            (prev_out, p["assumptions"], p["book"]),
            (curr_out, p["assumptions_next"], p["book_next"])):
        if not os.path.exists(os.path.join(out_dir, "valuation.json")):
            run_engine(a_path, b_path, p["liabilities"], out_dir,
                       seed=20260831, n_sims=3000)

    out_dir = os.path.join(FIXTURES, "out_attr_cli_two_books")
    res = _run_cli(["engine.attribution",
                    "--prev", prev_out, "--curr", curr_out,
                    "--prev-assumptions", p["assumptions"],
                    "--curr-assumptions", p["assumptions_next"],
                    "--prev-book", p["book"], "--curr-book", p["book_next"],
                    "--liabilities", p["liabilities"],
                    "--out", out_dir, "--sims", "3000"])
    assert res.returncode == 0, res.stderr
    with open(os.path.join(out_dir, "attribution.json")) as f:
        attr = json.load(f)
    assert {st["name"]: st["delta_gbp"]
            for st in attr["mtm"]["steps"]}["book"] != 0.0

    # Neither --book nor the pair: argparse error, nonzero exit.
    res = _run_cli(["engine.attribution",
                    "--prev", prev_out, "--curr", curr_out,
                    "--prev-assumptions", p["assumptions"],
                    "--curr-assumptions", p["assumptions_next"],
                    "--liabilities", p["liabilities"],
                    "--out", out_dir])
    assert res.returncode != 0
    # Only one of the pair: also rejected.
    res = _run_cli(["engine.attribution",
                    "--prev", prev_out, "--curr", curr_out,
                    "--prev-assumptions", p["assumptions"],
                    "--curr-assumptions", p["assumptions_next"],
                    "--prev-book", p["book"],
                    "--liabilities", p["liabilities"],
                    "--out", out_dir])
    assert res.returncode != 0


# ---------------------------------------------------------------------------
# Liability cohorts (SPEC sections 3 and 7) + attribution step 9
# ---------------------------------------------------------------------------

def toy_liability_cohorts() -> dict:
    """Four-cohort toy liabilities per the SPEC section 7 schema."""
    return {"cohorts": [
        {"id": "L-PROP-GBP", "class": "property", "currency": "GBP",
         "curve": "gbp_swap",
         "cashflows": [{"t": t, "amount": 100000} for t in (1, 2, 3)]},
        {"id": "L-PROP-USD", "class": "property", "currency": "USD",
         "curve": "ust",
         "cashflows": [{"t": t, "amount": 40000} for t in (1, 2, 3)]},
        {"id": "L-CAS-GBP", "class": "casualty", "currency": "GBP",
         "curve": "gbp_swap",
         "cashflows": [{"t": t, "amount": 60000} for t in range(1, 11)]},
        {"id": "L-CAS-USD", "class": "casualty", "currency": "USD",
         "curve": "ust",
         "cashflows": [{"t": t, "amount": 50000} for t in range(1, 11)]},
    ]}


def toy_liability_cohorts_next() -> dict:
    """Curr liabilities: every cohort scaled +15% (new written business)."""
    liabs = toy_liability_cohorts()
    for c in liabs["cohorts"]:
        for cf in c["cashflows"]:
            cf["amount"] = round(cf["amount"] * 1.15)
    return liabs


@pytest.fixture(scope="session")
def cohort_liability_paths(fixture_paths):
    prev = os.path.join(FIXTURES, "toy_liability_cohorts.json")
    curr = os.path.join(FIXTURES, "toy_liability_cohorts_next.json")
    with open(prev, "w", encoding="utf-8") as f:
        json.dump(toy_liability_cohorts(), f, indent=2)
    with open(curr, "w", encoding="utf-8") as f:
        json.dump(toy_liability_cohorts_next(), f, indent=2)
    return dict(fixture_paths, liabilities_cohorts=prev,
                liabilities_cohorts_next=curr)


def test_liability_cohort_pv_hand_check():
    """Per-cohort PV: GBP property on flat 4% swap; USD casualty on flat 3%
    ust converted at GBPUSD 1.25. Total = sum of the four cohorts."""
    a = toy_assumptions()
    state = esg.base_state(a)
    cohorts, pvs = pricing.pv_liability_cohorts(toy_liability_cohorts(), state)
    assert [c["id"] for c in cohorts] == \
        ["L-PROP-GBP", "L-PROP-USD", "L-CAS-GBP", "L-CAS-USD"]
    assert pvs.shape == (1, 4)

    hand_prop_gbp = sum(100000.0 * 1.04 ** (-t) for t in (1, 2, 3))
    hand_prop_usd = sum(40000.0 * 1.03 ** (-t) for t in (1, 2, 3)) / 1.25
    hand_cas_gbp = sum(60000.0 * 1.04 ** (-t) for t in range(1, 11))
    hand_cas_usd = sum(50000.0 * 1.03 ** (-t) for t in range(1, 11)) / 1.25
    assert math.isclose(pvs[0, 0], hand_prop_gbp, rel_tol=1e-10)
    assert math.isclose(pvs[0, 1], hand_prop_usd, rel_tol=1e-10)
    assert math.isclose(pvs[0, 2], hand_cas_gbp, rel_tol=1e-10)
    assert math.isclose(pvs[0, 3], hand_cas_usd, rel_tol=1e-10)

    total = pricing.pv_liabilities(toy_liability_cohorts(), state)[0]
    assert math.isclose(
        total, hand_prop_gbp + hand_prop_usd + hand_cas_gbp + hand_cas_usd,
        rel_tol=1e-10)


def test_legacy_liabilities_load_as_single_gbp_swap_cohort():
    """Back-compat: a legacy {currency, cashflows} file must still load as a
    single GBP/gbp_swap cohort with an unchanged PV."""
    legacy = toy_liabilities()
    cohorts = pricing.normalize_liabilities(legacy)
    assert len(cohorts) == 1
    assert cohorts[0]["currency"] == "GBP"
    assert cohorts[0]["curve"] == "gbp_swap"
    assert cohorts[0]["cashflows"] == legacy["cashflows"]
    # PV identical to the pre-cohort hand computation.
    a = toy_assumptions()
    pv = pricing.pv_liabilities(legacy, esg.base_state(a))[0]
    hand = sum(100000.0 * 1.04 ** (-t) for t in range(1, 11))
    assert math.isclose(pv, hand, rel_tol=1e-10, abs_tol=1e-10)
    # A legacy non-GBP file is rejected loudly.
    with pytest.raises(ValueError):
        pricing.normalize_liabilities(
            {"currency": "USD", "cashflows": legacy["cashflows"]})


def test_liability_cohort_schema_validation():
    good = toy_liability_cohorts()
    # USD cohort on gbp_swap: currency/curve mismatch is rejected.
    bad_curve = copy.deepcopy(good)
    bad_curve["cohorts"][1]["curve"] = "gbp_swap"
    with pytest.raises(ValueError):
        pricing.normalize_liabilities(bad_curve)
    # Unknown class rejected.
    bad_class = copy.deepcopy(good)
    bad_class["cohorts"][0]["class"] = "motor"
    with pytest.raises(ValueError):
        pricing.normalize_liabilities(bad_class)
    # Missing field rejected.
    bad_missing = copy.deepcopy(good)
    del bad_missing["cohorts"][2]["curve"]
    with pytest.raises(ValueError):
        pricing.normalize_liabilities(bad_missing)
    # Empty cohort list rejected.
    with pytest.raises(ValueError):
        pricing.normalize_liabilities({"cohorts": []})


_LIAB_BLOCK_SLICES = {
    "gbp_swap": slice(0, 4), "gbp_gilt": slice(4, 8), "ust": slice(8, 12),
    "spread": slice(12, 17), "equity": slice(17, 20), "fx": slice(20, 21),
}


def _cohort_pv_under_shock(liabs, block):
    a = toy_assumptions()
    state0 = esg.base_state(a)
    shocks = np.zeros((1, esg.N_FACTORS))
    shocks[0, _LIAB_BLOCK_SLICES[block]] = 0.01  # +100bp / +1% proportional
    return (float(pricing.pv_liabilities(liabs, state0)[0]),
            float(pricing.pv_liabilities(
                liabs, esg.apply_shocks(state0, shocks))[0]))


def test_usd_liability_cohort_responds_to_ust_and_fx_only():
    usd_only = {"cohorts": [c for c in toy_liability_cohorts()["cohorts"]
                            if c["currency"] == "USD"]}
    # ust up => USD PV down => GBP PV down.
    pv0, pv = _cohort_pv_under_shock(usd_only, "ust")
    assert pv < pv0
    # GBPUSD up => GBP value of USD liabilities down.
    pv0, pv = _cohort_pv_under_shock(usd_only, "fx")
    assert pv < pv0
    # gbp_swap (and every other block) leaves the USD cohorts exactly alone.
    for block in ("gbp_swap", "gbp_gilt", "spread", "equity"):
        pv0, pv = _cohort_pv_under_shock(usd_only, block)
        assert pv == pv0, "USD cohorts must not respond to %s" % block


def test_gbp_liability_cohort_responds_to_gbp_swap_only():
    gbp_only = {"cohorts": [c for c in toy_liability_cohorts()["cohorts"]
                            if c["currency"] == "GBP"]}
    pv0, pv = _cohort_pv_under_shock(gbp_only, "gbp_swap")
    assert pv < pv0, "swap rates up => GBP cohort PV down"
    for block in ("gbp_gilt", "ust", "spread", "equity", "fx"):
        pv0, pv = _cohort_pv_under_shock(gbp_only, block)
        assert pv == pv0, "GBP cohorts must not respond to %s" % block


def test_block_masks_carry_liability_ir_usd_and_fx_risk():
    """Block standalone VaRs must reflect cohort liabilities: with an inert
    asset (GBP cash), ir_gbp, ir_usd and fx VaR are all nonzero (driven by
    the liabilities alone); credit and equity are exactly zero."""
    a = toy_assumptions()
    liabs = toy_liability_cohorts()
    inert_asset = [{"id": "C001", "type": "cash", "name": "GBP cash",
                    "currency": "GBP", "amount": 1000000}]
    shocks = esg.simulate_shocks(a, 2000, seed=20260831)
    blocks = var.block_standalone_vars(a, inert_asset, liabs, shocks)
    assert blocks["ir_gbp"] > 0.0   # GBP cohorts on gbp_swap
    assert blocks["ir_usd"] > 0.0   # USD cohorts on ust
    assert blocks["fx"] > 0.0       # USD cohorts translated at GBPUSD
    assert blocks["credit"] == 0.0  # nothing responds: exactly zero P&L
    assert blocks["equity"] == 0.0


def test_run_engine_cohort_liabilities_and_determinism(cohort_liability_paths):
    """End-to-end run with cohort liabilities: valuation carries per-cohort
    PVs summing to the total; two same-seed runs are bit-identical."""
    p = cohort_liability_paths
    out_a = os.path.join(FIXTURES, "out_cohort_a")
    out_b = os.path.join(FIXTURES, "out_cohort_b")
    for out_dir in (out_a, out_b):
        run_engine(p["assumptions"], p["book"], p["liabilities_cohorts"],
                   out_dir, seed=321, n_sims=2000)
    with open(os.path.join(out_a, "valuation.json")) as f:
        val = json.load(f)
    cohorts = val["liability_cohorts"]
    assert [c["id"] for c in cohorts] == \
        ["L-PROP-GBP", "L-PROP-USD", "L-CAS-GBP", "L-CAS-USD"]
    assert {c["currency"] for c in cohorts} == {"GBP", "USD"}
    assert val["liability_pv_gbp"] == pytest.approx(
        sum(c["pv_gbp"] for c in cohorts))
    assert val["surplus_gbp"] == pytest.approx(
        val["asset_total_gbp"] - val["liability_pv_gbp"])
    for name in OUTPUT_FILES:
        with open(os.path.join(out_a, name), "rb") as f:
            bytes_a = f.read()
        with open(os.path.join(out_b, name), "rb") as f:
            bytes_b = f.read()
        assert bytes_a == bytes_b, "output %s differs between runs" % name


def test_attribution_two_liabilities_step9_nonzero_and_control_total(
        cohort_liability_paths):
    p = cohort_liability_paths
    seed, sims = 20260831, 3000
    prev_out = os.path.join(FIXTURES, "out_attr_liab_prev")
    curr_out = os.path.join(FIXTURES, "out_attr_liab_curr")
    run_engine(p["assumptions"], p["book"], p["liabilities_cohorts"],
               prev_out, seed=seed, n_sims=sims)
    run_engine(p["assumptions_next"], p["book"],
               p["liabilities_cohorts_next"], curr_out, seed=seed,
               n_sims=sims)

    attr_out = os.path.join(FIXTURES, "out_attr_two_liabs")
    attr = run_attribution(prev_out, curr_out,
                           p["assumptions"], p["assumptions_next"],
                           p["book"], None, attr_out,
                           prev_liabilities_path=p["liabilities_cohorts"],
                           curr_liabilities_path=p["liabilities_cohorts_next"])

    for section in ("mtm", "var"):
        s = attr[section]
        steps = {st["name"]: st["delta_gbp"] for st in s["steps"]}
        # Step 9 carries the liability change and is nonzero in both measures.
        assert steps["liabilities"] != 0.0
        # Step 8 stays exactly 0 (single book).
        assert steps["book"] == 0.0
        # Control total: steps + explicit residual == total; residual ~0.
        total = s["total_change_gbp"]
        sum_steps = sum(st["delta_gbp"] for st in s["steps"])
        assert abs(sum_steps + s["residual_gbp"] - total) < 1e-8
        assert s["additivity_check"]["additive_within_1e-6"] is True
        assert abs(s["residual_gbp"]) < 1e-6

    # MTM step 9 direction: every cohort scaled +15% => liability PV up =>
    # surplus down at the swap.
    mtm_steps = {st["name"]: st["delta_gbp"] for st in attr["mtm"]["steps"]}
    assert mtm_steps["liabilities"] < 0.0

    # Meta records both liability files with hashes.
    meta = attr["meta"]
    assert meta["prev_liabilities_sha256"] != meta["curr_liabilities_sha256"]
    assert len(meta["prev_liabilities_sha256"]) == 64
    assert meta["liabilities_sha256"] == meta["curr_liabilities_sha256"]


def test_attribution_cli_two_liabilities_and_arg_validation(
        cohort_liability_paths):
    p = cohort_liability_paths
    prev_out = os.path.join(FIXTURES, "out_attr_liab_prev")
    curr_out = os.path.join(FIXTURES, "out_attr_liab_curr")
    for out_dir, a_path, l_path in (
            (prev_out, p["assumptions"], p["liabilities_cohorts"]),
            (curr_out, p["assumptions_next"], p["liabilities_cohorts_next"])):
        if not os.path.exists(os.path.join(out_dir, "valuation.json")):
            run_engine(a_path, p["book"], l_path, out_dir,
                       seed=20260831, n_sims=3000)

    out_dir = os.path.join(FIXTURES, "out_attr_cli_two_liabs")
    res = _run_cli(["engine.attribution",
                    "--prev", prev_out, "--curr", curr_out,
                    "--prev-assumptions", p["assumptions"],
                    "--curr-assumptions", p["assumptions_next"],
                    "--book", p["book"],
                    "--prev-liabilities", p["liabilities_cohorts"],
                    "--curr-liabilities", p["liabilities_cohorts_next"],
                    "--out", out_dir, "--sims", "3000"])
    assert res.returncode == 0, res.stderr
    with open(os.path.join(out_dir, "attribution.json")) as f:
        attr = json.load(f)
    assert {st["name"]: st["delta_gbp"]
            for st in attr["mtm"]["steps"]}["liabilities"] != 0.0

    # Neither --liabilities nor the pair: argparse error, nonzero exit.
    res = _run_cli(["engine.attribution",
                    "--prev", prev_out, "--curr", curr_out,
                    "--prev-assumptions", p["assumptions"],
                    "--curr-assumptions", p["assumptions_next"],
                    "--book", p["book"], "--out", out_dir])
    assert res.returncode != 0
    # Only one of the pair: also rejected.
    res = _run_cli(["engine.attribution",
                    "--prev", prev_out, "--curr", curr_out,
                    "--prev-assumptions", p["assumptions"],
                    "--curr-assumptions", p["assumptions_next"],
                    "--book", p["book"],
                    "--prev-liabilities", p["liabilities_cohorts"],
                    "--out", out_dir])
    assert res.returncode != 0
