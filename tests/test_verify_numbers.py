"""Adversarial numerical verification of the month-end risk prototype.

Independent checks written against SPEC.md, NOT against the engine's own
helpers wherever the check demands independence:

  1. Hand repricing of one govt bond and one USD corporate bond from the
     assumptions YAML with a from-scratch discount-sum implementation
     (own interpolation, own spread term profile, own FX conversion),
     matched to valuation.json at 1e-6 relative.
  2. Empirical 99.5th-percentile loss of sim_pnl_sample.csv (1,000 draws)
     vs var_aggregate.json (50,000 draws), with an order-statistic
     tolerance argument (see test docstring).
  3. Aggregate VaR <= sum of factor-block standalone VaRs, every run.
  4. Spread floor: engine.esg.apply_shocks with a huge negative spread
     shock must floor spreads at exactly 0.
  5. FX convention: a USD cash position's GBP value == USD / GBPUSD exactly.
  6. Liability PV responds to gbp_swap shocks only (not gilt/ust/spread/
     equity/fx).
  7. Attribution (recommended pair 2026-02 -> 2026-03): steps + residual
     == total; residual size relative to total is asserted and reported.

Run:  .venv/Scripts/python.exe -m pytest tests/test_verify_numbers.py -q
Evidence and verdicts: VERIFY_NUMBERS.md.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import numpy as np
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine import esg, pricing  # noqa: E402  (checks 4-6 test engine behavior)

# Run directories are month/version/stage (PENDING-BATCH2 section 1):
# outputs/<YYYY_MM>/vN/{esg,pricing}. Only 2026_02 and 2026_03 are kept, so
# the walks below cover every run on disk — including 2603_v2, the March
# book, which the old per-month list did not reach.
RUNS = ["2026_02/v1", "2026_03/v1", "2026_03/v2"]
ATTRIBUTIONS = ["attr_2026_02_v1__2026_03_v1", "attr_2026_02_v1__2026_03_v2"]
DEMO_PAIR = "attr_2026_02_v1__2026_03_v1"   # 2602_v1 -> 2603_v1, single book

VERIFY_RUN = "2026_03/v1"          # run used for the repricing checks
VERIFY_MONTH = "2026-03"           # its assumptions month
GOVT_BOND_ID = "P003"              # UKT 4.25 2032, 6y (off-grid: interp needed)
CORP_BOND_ID = "P013"              # Apple 3.85% 2043, USD, AA, 17y (off-grid)
USD_CASH_ID = "P045"               # USD cash (main), 20,000,000 USD


# ---------------------------------------------------------------------------
# Independent (from-scratch) pricing helpers — deliberately NOT engine code.
# ---------------------------------------------------------------------------

_TENORS = [2.0, 5.0, 10.0, 20.0]
_M = {2.0: 0.85, 5.0: 1.00, 10.0: 1.10, 20.0: 1.20}   # SPEC section 2


def _interp(tenor_values, t):
    """Linear interpolation on the 2/5/10/20 grid, flat outside (SPEC s2)."""
    if t <= _TENORS[0]:
        return tenor_values[0]
    if t >= _TENORS[-1]:
        return tenor_values[-1]
    for j in range(len(_TENORS) - 1):
        t0, t1 = _TENORS[j], _TENORS[j + 1]
        if t0 <= t <= t1:
            f = (t - t0) / (t1 - t0)
            return tenor_values[j] * (1.0 - f) + tenor_values[j + 1] * f
    raise AssertionError("unreachable")


def _hand_price_bond(position, assumptions):
    """Independent SPEC section 3 bond price in GBP.

    price = sum cashflow(t) * (1 + z(t))^(-t), annual coupons, integer
    maturity; corporate z(t) = govy z(t) + spread_level * M(t); USD converted
    at GBPUSD (GBP = USD / GBPUSD).
    """
    curve = [float(assumptions["curves"][position["curve"]][int(t)])
             for t in _TENORS]
    m_vals = [_M[t] for t in _TENORS]
    notional = float(position["notional"])
    coupon = float(position["coupon"])
    T = int(position["maturity_years"])
    total = 0.0
    for t in range(1, T + 1):
        z = _interp(curve, float(t))
        if position["type"] == "corp_bond":
            level = float(assumptions["spreads"][position["rating"]])
            z += level * _interp(m_vals, float(t))
        cf = coupon * notional + (notional if t == T else 0.0)
        total += cf * (1.0 + z) ** (-float(t))
    if position["currency"] == "USD":
        total /= float(assumptions["fx"]["GBPUSD"])
    return total


# ---------------------------------------------------------------------------
# Fixtures / loaders
# ---------------------------------------------------------------------------

def _load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _pricing(run: str):
    """The priced side of a run directory: outputs/<YYYY_MM>/vN/pricing."""
    return ROOT / "outputs" / Path(run) / "pricing"


@pytest.fixture(scope="module")
def assumptions():
    with open(ROOT / "assumptions" / f"{VERIFY_MONTH}.yaml",
              encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def book():
    return _load_json(ROOT / "book" / "positions.json")


@pytest.fixture(scope="module")
def liabilities():
    return _load_json(ROOT / "book" / "liabilities.json")


@pytest.fixture(scope="module")
def valuation():
    return _load_json(_pricing(VERIFY_RUN) / "valuation.json")


def _position(book, pid):
    return next(p for p in book["positions"] if p["id"] == pid)


def _market_value(valuation, pid):
    return next(p["market_value_gbp"] for p in valuation["positions"]
                if p["id"] == pid)


# ---------------------------------------------------------------------------
# Check 1 — hand repricing of two bonds to 1e-6 relative
# ---------------------------------------------------------------------------

def test_reprice_govt_bond_by_hand(assumptions, book, valuation):
    p = _position(book, GOVT_BOND_ID)
    assert p["type"] == "govt_bond" and p["currency"] == "GBP"
    hand = _hand_price_bond(p, assumptions)
    engine_mv = _market_value(valuation, GOVT_BOND_ID)
    assert abs(hand / engine_mv - 1.0) < 1e-6, (hand, engine_mv)


def test_reprice_usd_corp_bond_by_hand(assumptions, book, valuation):
    p = _position(book, CORP_BOND_ID)
    assert p["type"] == "corp_bond" and p["currency"] == "USD"
    hand = _hand_price_bond(p, assumptions)
    engine_mv = _market_value(valuation, CORP_BOND_ID)
    assert abs(hand / engine_mv - 1.0) < 1e-6, (hand, engine_mv)


def test_reprice_liabilities_by_hand(assumptions, liabilities, valuation):
    """Bonus: independent liability PV over the four P&C cohorts (SPEC s3):
    GBP cohorts discount on `gbp_swap`, USD cohorts on `ust` and are then
    converted at GBPUSD. Priced entirely by hand here — no engine code in
    the path — and the total must match the engine to 1e-6."""
    gbpusd = float(assumptions["fx"]["GBPUSD"])
    curves = {name: [float(assumptions["curves"][name][int(t)])
                     for t in _TENORS]
              for name in ("gbp_swap", "ust")}
    cohorts = liabilities["cohorts"]
    assert {c["id"] for c in cohorts} == {"L-PROP-GBP", "L-PROP-USD",
                                          "L-CAS-GBP", "L-CAS-USD"}
    pv = 0.0
    for c in cohorts:
        # SPEC s3: GBP on gbp_swap, USD on ust (the stated USD risk-free)
        assert c["curve"] == ("gbp_swap" if c["currency"] == "GBP" else "ust")
        curve = curves[c["curve"]]
        local = sum(float(cf["amount"])
                    * (1.0 + _interp(curve, float(cf["t"])))
                    ** (-float(cf["t"]))
                    for cf in c["cashflows"])
        pv += local if c["currency"] == "GBP" else local / gbpusd
    assert abs(pv / valuation["liability_pv_gbp"] - 1.0) < 1e-6


# ---------------------------------------------------------------------------
# Check 2 — sim_pnl_sample.csv vs var_aggregate.json
# ---------------------------------------------------------------------------

def test_sample_percentile_consistent_with_aggregate_var():
    """The stored sample is the FIRST 1,000 of the same 50,000 seeded draws.

    Tolerance argument: let X = #{sample P&Ls <= -VaR_50k}. The 99.5% loss
    quantile of the full run sits at the ~250th worst of 50,000, so a
    1,000-draw subsample (without replacement) has X ~ Hypergeometric,
    E[X] = 1000*250/50000 = 5, sd ~ 2.2. We accept 0 <= X <= 15 (> 4 sd;
    P(X > 15) < 1e-4, P(X = 0) ~ 0.6%, and we additionally accept X = 0 only
    if the quantile ratio check passes). Separately the empirical 0.5%
    quantile of 1,000 draws has relative sampling error ~ 6% (asymptotic
    quantile SE ~ sqrt(p(1-p)/n)/f(q) with near-normal P&L); we allow +/-25%
    (~4 SE).
    """
    rows = list(csv.DictReader(
        open(_pricing(VERIFY_RUN) / "sim_pnl_sample.csv",
             encoding="utf-8")))
    pnl = np.array([float(r["surplus_pnl_gbp"]) for r in rows])
    assert len(pnl) == 1000
    agg = _load_json(_pricing(VERIFY_RUN) /
                     "var_aggregate.json")["aggregate_var_gbp"]

    exceed = int((pnl <= -agg).sum())
    assert 0 <= exceed <= 15, f"exceedance count {exceed} implausible"

    emp_var = -float(np.quantile(pnl, 0.005))
    ratio = emp_var / agg
    assert 0.75 <= ratio <= 1.25, f"empirical/aggregate VaR ratio {ratio:.4f}"


# ---------------------------------------------------------------------------
# Check 3 — aggregate VaR <= sum of block standalone VaRs, every month
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("run", RUNS)
def test_aggregate_var_leq_sum_of_blocks(run):
    agg = _load_json(_pricing(run) / "var_aggregate.json")
    blocks = _load_json(_pricing(run) /
                        "var_standalone_factors.json")["blocks"]
    sum_blocks = sum(blocks.values())
    assert agg["aggregate_var_gbp"] <= sum_blocks
    # and the stored sum matches the stored blocks
    assert abs(agg["sum_standalone_blocks_gbp"] - sum_blocks) < 1e-6


# ---------------------------------------------------------------------------
# Check 4 — spread floor at 0 under a huge negative shock (engine.esg)
# ---------------------------------------------------------------------------

def test_spread_floor_direct_esg_invocation(assumptions):
    state0 = esg.base_state(assumptions)
    shocks = np.zeros((3, esg.N_FACTORS))
    shocks[0, esg.SPREAD_SLICE] = -5.0     # -500%: absurdly negative
    shocks[1, esg.SPREAD_SLICE] = -1e9
    shocks[2, esg.SPREAD_SLICE] = -0.0001  # small: must NOT floor to 0
    sim = esg.apply_shocks(state0, shocks)
    assert np.all(sim["spreads"][0] == 0.0)
    assert np.all(sim["spreads"][1] == 0.0)
    expected = np.maximum(state0["spreads"] - 0.0001, 0.0)
    assert np.allclose(sim["spreads"][2], expected)
    # floor must not leak into other factor types
    assert np.allclose(sim["equity"], state0["equity"][None, :])
    assert np.allclose(sim["fx"], state0["fx"])


# ---------------------------------------------------------------------------
# Check 5 — FX convention: USD cash GBP value == USD / GBPUSD exactly
# ---------------------------------------------------------------------------

def test_usd_cash_fx_convention(assumptions, book, valuation):
    p = _position(book, USD_CASH_ID)
    assert p["type"] == "cash" and p["currency"] == "USD"
    gbpusd = float(assumptions["fx"]["GBPUSD"])
    expected = float(p["amount"]) / gbpusd
    # engine valuation output must equal USD / GBPUSD to the float
    assert _market_value(valuation, USD_CASH_ID) == pytest.approx(
        expected, rel=0, abs=0.0)
    # and a direct engine call reproduces it bit-for-bit
    state0 = esg.base_state(assumptions)
    direct = float(pricing.value_position(p, state0)[0])
    assert direct == expected


# ---------------------------------------------------------------------------
# Check 6 — liability PV responds to gbp_swap, ust and fx, and to nothing
# else (SPEC s3: GBP cohorts discount on gbp_swap, USD cohorts on ust and
# are converted at GBPUSD, so the P&C book is an FX position too).
# ---------------------------------------------------------------------------

BLOCK_SLICES = {
    "gbp_swap": slice(0, 4), "gbp_gilt": slice(4, 8), "ust": slice(8, 12),
    "spread": slice(12, 17), "equity": slice(17, 20), "fx": slice(20, 21),
}
# blocks the liability cashflows are actually exposed to, and the sign the
# +100bp / +1% shock must produce
LIABILITY_BLOCKS = {"gbp_swap": "down", "ust": "down", "fx": "down"}


@pytest.mark.parametrize("block", list(BLOCK_SLICES))
def test_liability_pv_sensitivity(assumptions, liabilities, block):
    state0 = esg.base_state(assumptions)
    pv0 = float(pricing.pv_liabilities(liabilities, state0)[0])
    shocks = np.zeros((1, esg.N_FACTORS))
    shocks[0, BLOCK_SLICES[block]] = 0.01   # +100bp / +1% proportional
    pv = float(pricing.pv_liabilities(liabilities,
                                      esg.apply_shocks(state0, shocks))[0])
    if block in LIABILITY_BLOCKS:
        # rates up => discount harder => PV down; GBPUSD up (stronger
        # sterling) => the USD cohorts are worth less in GBP => PV down
        assert abs(pv - pv0) > 1e6, f"liability PV must respond to {block}"
        assert pv < pv0, f"{block} +1% must reduce liability PV"
    else:
        assert pv == pv0, f"liability PV must not respond to {block}"


# ---------------------------------------------------------------------------
# Check 7 — attribution additivity and residual size (recommended pair)
# ---------------------------------------------------------------------------

def _attr(pair_dir):
    return _load_json(ROOT / "outputs" / pair_dir / "attribution.json")


def test_attribution_recommended_pair_additivity():
    a = _attr(DEMO_PAIR)
    for section in ("mtm", "var"):
        s = a[section]
        sum_steps = sum(st["delta_gbp"] for st in s["steps"])
        residual = s["residual_gbp"]
        total = s["total_change_gbp"]
        # steps + residual == total (well within 1e-6 GBP)
        assert abs(sum_steps + residual - total) < 1e-6, section
        # residual must be small relative to the total move
        ratio = abs(residual) / abs(total)
        assert ratio < 1e-9, (section, ratio)
        # NOTE (documented finding, not an assertion failure): the residual
        # is EXACTLY 0.0 here, not "nonzero-but-small". Sequential one-block-
        # at-a-time attribution telescopes (sum of step deltas == recomputed
        # end - start by construction), so the residual line can only ever
        # capture a stored-run vs recomputation mismatch, which is zero for
        # a deterministic engine with identical seed/book/assumptions.
        # See VERIFY_NUMBERS.md check 7.


@pytest.mark.parametrize("pair_dir", ATTRIBUTIONS)
def test_attribution_all_pairs_additivity(pair_dir):
    a = _attr(pair_dir)
    for section in ("mtm", "var"):
        s = a[section]
        sum_steps = sum(st["delta_gbp"] for st in s["steps"])
        assert abs(sum_steps + s["residual_gbp"]
                   - s["total_change_gbp"]) < 1e-6
        assert s["additivity_check"]["additive_within_1e-6"] is True


def test_attribution_recommended_pair_headline_numbers():
    """Verify the integrator's headline claims for 2026-02 -> 2026-03.

    NB (2026-08-29): values updated for the four-cohort P&C liability
    rebuild (SPEC section 3/7) — liability modified duration ~4.3 in place
    of the old ~9.4 GBP-only annuity, so the gbp_swap discounting offset to
    equity/gilt losses is much smaller and the pair's surplus now FALLS
    (-13.9m) rather than rising (+13.3m as under the old liability model).
    Figures below are read from a fresh `outputs/` regeneration (seed
    20260831, 50,000 sims) — see outputs/summary.md.
    """
    a = _attr(DEMO_PAIR)
    assert a["mtm"]["prev_surplus_gbp"] == pytest.approx(158.7e6, abs=0.05e6)
    assert a["mtm"]["curr_surplus_gbp"] == pytest.approx(144.8e6, abs=0.05e6)
    assert a["var"]["prev_aggregate_var_gbp"] == pytest.approx(71.6e6,
                                                              abs=0.05e6)
    assert a["var"]["curr_aggregate_var_gbp"] == pytest.approx(70.4e6,
                                                              abs=0.05e6)
    steps = {s["name"]: s["delta_gbp"] for s in a["mtm"]["steps"]}
    assert steps["gbp_swap"] == pytest.approx(11.7e6, abs=0.1e6)
    assert steps["gbp_gilt"] == pytest.approx(-11.9e6, abs=0.1e6)
    assert steps["ust"] == pytest.approx(1.1e6, abs=0.1e6)
    assert steps["equity"] == pytest.approx(-14.8e6, abs=0.1e6)
    assert a["mtm"]["total_change_gbp"] == pytest.approx(-13.9e6, abs=0.1e6)
    assert a["var"]["total_change_gbp"] == pytest.approx(-1.2e6, abs=0.05e6)
