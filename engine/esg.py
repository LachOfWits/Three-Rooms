"""Economic scenario generator (SPEC section 2).

21 risk factors evolve over the 1-year horizon as correlated normal increments
(one-step Brownian motion) via Cholesky decomposition of the assumption
correlation matrix, scaled by the assumption vols.

Shock application:
  - rates (gbp_swap, gbp_gilt, ust tenor points): additive, decimal
  - spread levels (AA, A, BBB, HY, CCC):          additive, floored at 0
                                                  post-shock
  - equity index levels:                          level * (1 + z)
  - fx (GBPUSD):                                  level * (1 + z)
"""

from __future__ import annotations

import numpy as np

from .curves import TENORS, curve_dict_to_array

# Exact factor order (SPEC section 6 `correlation.order`).
FACTOR_ORDER = [
    "gbp_swap_2", "gbp_swap_5", "gbp_swap_10", "gbp_swap_20",
    "gbp_gilt_2", "gbp_gilt_5", "gbp_gilt_10", "gbp_gilt_20",
    "ust_2", "ust_5", "ust_10", "ust_20",
    "spread_AA", "spread_A", "spread_BBB", "spread_HY", "spread_CCC",
    "eq_FTSE100", "eq_SP500", "eq_SX5E", "fx_GBPUSD",
]
N_FACTORS = len(FACTOR_ORDER)

CURVE_NAMES = ["gbp_swap", "gbp_gilt", "ust"]
RATINGS = ["AA", "A", "BBB", "HY", "CCC"]
EQUITY_INDICES = ["FTSE100", "SP500", "SX5E"]

# Factor index slices into the 21-vector, per block.
CURVE_SLICES = {"gbp_swap": slice(0, 4), "gbp_gilt": slice(4, 8), "ust": slice(8, 12)}
SPREAD_SLICE = slice(12, 17)
EQUITY_SLICE = slice(17, 20)
FX_INDEX = 20


def base_state(assumptions: dict) -> dict:
    """Extract the deterministic base market state from an assumptions dict.

    Returns arrays: curves {name: (4,)}, spreads (5,), equity (3,), fx scalar.
    """
    return {
        "curves": {c: curve_dict_to_array(assumptions["curves"][c]) for c in CURVE_NAMES},
        "spreads": np.array([float(assumptions["spreads"][r]) for r in RATINGS]),
        "equity": np.array([float(assumptions["equity"][i]) for i in EQUITY_INDICES]),
        "fx": float(assumptions["fx"]["GBPUSD"]),
    }


def vol_vector(assumptions: dict) -> np.ndarray:
    """Annualized vols as a (21,) vector in FACTOR_ORDER."""
    v = assumptions["vols"]
    out = []
    for c in CURVE_NAMES:
        out.extend(float(v[c][int(t)]) for t in TENORS)
    out.extend(float(v["spread"][r]) for r in RATINGS)
    out.extend(float(v["equity"][i]) for i in EQUITY_INDICES)
    out.append(float(v["fx"]["GBPUSD"]))
    return np.array(out)


def correlation_matrix(assumptions: dict) -> np.ndarray:
    """Load and validate the 21x21 correlation matrix (order must match)."""
    corr = assumptions["correlation"]
    order = list(corr["order"])
    if order != FACTOR_ORDER:
        raise ValueError(
            "correlation.order does not match SPEC factor order; got %r" % order
        )
    m = np.asarray(corr["matrix"], dtype=float)
    if m.shape != (N_FACTORS, N_FACTORS):
        raise ValueError("correlation.matrix must be %dx%d, got %r"
                         % (N_FACTORS, N_FACTORS, m.shape))
    if not np.allclose(m, m.T, atol=1e-12):
        raise ValueError("correlation matrix is not symmetric")
    return m


def cholesky_factor(assumptions: dict) -> np.ndarray:
    """Lower-triangular Cholesky factor of the correlation matrix.

    Calibration must deliver a PSD matrix (SPEC section 6); a non-PD matrix is
    an input error here, not something the engine silently repairs.
    """
    m = correlation_matrix(assumptions)
    try:
        return np.linalg.cholesky(m)
    except np.linalg.LinAlgError as exc:
        raise ValueError(
            "correlation matrix is not positive definite; calibration must "
            "supply a PSD (repaired) matrix"
        ) from exc


def simulate_shocks(assumptions: dict, n_sims: int, seed: int) -> np.ndarray:
    """Simulate (n_sims, 21) correlated 1-year factor shocks.

    shock = vol * (L @ eps) with eps ~ iid N(0, 1), L = chol(correlation).
    Deterministic given seed (numpy PCG64 default_rng).
    """
    L = cholesky_factor(assumptions)
    vols = vol_vector(assumptions)
    rng = np.random.default_rng(seed)
    eps = rng.standard_normal((n_sims, N_FACTORS))
    return (eps @ L.T) * vols


def apply_shocks(state: dict, shocks: np.ndarray, live_mask=None) -> dict:
    """Apply factor shocks to a base state; returns a simulated state.

    state: output of base_state(). shocks: (n_sims, 21).
    live_mask: optional boolean (21,) — factors where False are frozen (shock
    zeroed). Spread levels are floored at 0 *post-shock* (SPEC section 2).

    Returns: curves {name: (n_sims, 4)}, spreads (n_sims, 5),
    equity (n_sims, 3), fx (n_sims,).
    """
    shocks = np.asarray(shocks, dtype=float)
    if live_mask is not None:
        mask = np.asarray(live_mask, dtype=bool)
        shocks = shocks * mask[None, :]
    curves = {
        c: state["curves"][c][None, :] + shocks[:, CURVE_SLICES[c]]
        for c in CURVE_NAMES
    }
    spreads = np.maximum(state["spreads"][None, :] + shocks[:, SPREAD_SLICE], 0.0)
    equity = state["equity"][None, :] * (1.0 + shocks[:, EQUITY_SLICE])
    fx = state["fx"] * (1.0 + shocks[:, FX_INDEX])
    return {"curves": curves, "spreads": spreads, "equity": equity, "fx": fx}
