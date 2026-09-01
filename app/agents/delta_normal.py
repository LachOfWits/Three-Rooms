"""Delta-normal analytic cross-check (SPEC-APP section 4, the `delta_normal`
tool; used by @vlad).

Deterministic engine code, NO simulation:

- factor exposures by small-bump repricing of the full surplus
  (central differences: +/-1bp on rates and spread levels, +/-1% proportional
  on equity and fx),
- closed-form aggregate VaR  Z * sqrt(w' Sigma w)  with Z = 2.576 (the
  one-sided 99.5% normal point) and Sigma = diag(vols) C diag(vols),
- Euler component VaR per factor and per block, summing exactly to the
  aggregate,
- closed-form block standalone VaRs and the implied diversification benefit,
- for a pair of runs: the aggregate delta-VaR split into EXPOSURE / VOL /
  CORRELATION movements by sequential substitution in the closed form,
  naming the largest-moving correlation cells.

Everything here is a deterministic function of the run's input files, so a
number quoted from this module binds to the recorded `delta_normal` tool
call like any other engine output. The comparison against the run's
SIMULATED VaR (the approximation gap) is done by the tool wrapper in
tools.py, which also reads var_aggregate.json.
"""

from __future__ import annotations

import numpy as np

from engine import esg, pricing
from engine.var import BLOCKS

Z_995 = 2.576  # one-sided 99.5% standard-normal point (SPEC-APP section 4)

# central-bump sizes per factor, in shock units (additive decimals for rates
# and spread levels; proportional returns for equity and fx)
_BUMP_ADDITIVE = 1e-4   # 1bp
_BUMP_PROPORTIONAL = 1e-2  # 1%


def _bump_sizes() -> np.ndarray:
    h = np.full(esg.N_FACTORS, _BUMP_ADDITIVE)
    h[esg.EQUITY_SLICE] = _BUMP_PROPORTIONAL
    h[esg.FX_INDEX] = _BUMP_PROPORTIONAL
    return h


def factor_exposures(assumptions: dict, positions: list, liabilities: dict,
                     ref_index_levels=None) -> tuple[np.ndarray, float]:
    """(w, base_surplus): w[i] = dSurplus/dz_i by central difference, where
    z_i is the factor shock in the ESG's own units (SPEC section 2)."""
    state0 = esg.base_state(assumptions)
    h = _bump_sizes()
    n = esg.N_FACTORS
    shocks = np.zeros((1 + 2 * n, n))
    for i in range(n):
        shocks[1 + 2 * i, i] = h[i]
        shocks[2 + 2 * i, i] = -h[i]
    sim_state = esg.apply_shocks(state0, shocks)
    s = pricing.surplus(positions, liabilities, sim_state, ref_index_levels)
    base = float(s[0])
    w = np.array([(s[1 + 2 * i] - s[2 + 2 * i]) / (2.0 * h[i])
                  for i in range(n)])
    return w, base


def _closed_form(w: np.ndarray, vols: np.ndarray, corr: np.ndarray) -> float:
    sigma_w = (vols * w)
    return float(Z_995 * np.sqrt(sigma_w @ corr @ sigma_w))


def analytics(w: np.ndarray, vols: np.ndarray, corr: np.ndarray) -> dict:
    """Aggregate, Euler components (factor + block), block standalones and
    diversification benefit, all in closed form."""
    sigma = np.outer(vols, vols) * corr
    var_w = sigma @ w
    total_sq = float(w @ var_w)
    total = float(Z_995 * np.sqrt(max(total_sq, 0.0)))
    if total > 0:
        comps = Z_995 * w * var_w / np.sqrt(total_sq)
    else:
        comps = np.zeros_like(w)
    factor_comps = {esg.FACTOR_ORDER[i]: float(comps[i])
                    for i in range(esg.N_FACTORS)}
    block_comps = {b: float(sum(comps[i] for i in idx))
                   for b, idx in BLOCKS.items()}
    block_standalone = {}
    for b, idx in BLOCKS.items():
        wb = w[idx]
        block_standalone[b] = float(
            Z_995 * np.sqrt(max(wb @ sigma[np.ix_(idx, idx)] @ wb, 0.0)))
    sum_standalone = float(sum(block_standalone.values()))
    return {
        "aggregate_var_gbp": total,
        "euler_components_factor_gbp": factor_comps,
        "euler_components_block_gbp": block_comps,
        "euler_components_sum_gbp": float(sum(comps)),
        "block_standalone_gbp": block_standalone,
        "sum_block_standalone_gbp": sum_standalone,
        "diversification_benefit_gbp": sum_standalone - total,
        "diversification_ratio": (total / sum_standalone
                                  if sum_standalone > 0 else None),
    }


def single_run(assumptions: dict, positions: list, liabilities: dict,
               ref_index_levels=None) -> dict:
    """Full delta-normal read of one run's inputs."""
    w, base = factor_exposures(assumptions, positions, liabilities,
                               ref_index_levels)
    vols = esg.vol_vector(assumptions)
    corr = esg.correlation_matrix(assumptions)
    out = analytics(w, vols, corr)
    out["base_surplus_gbp"] = base
    out["exposures_gbp_per_unit_shock"] = {
        esg.FACTOR_ORDER[i]: float(w[i]) for i in range(esg.N_FACTORS)}
    out["z"] = Z_995
    return out


def pair_decomposition(run_a: dict, run_b: dict, top_cells: int = 3) -> dict:
    """Delta-VaR split by sequential substitution in the closed form:

        V0 = f(w_a, vols_a, C_a)
        V1 = f(w_b, vols_a, C_a)   -> exposure step  (V1 - V0)
        V2 = f(w_b, vols_b, C_a)   -> vol step       (V2 - V1)
        V3 = f(w_b, vols_b, C_b)   -> correlation    (V3 - V2)

    Steps sum exactly to V3 - V0. run_a/run_b are dicts with keys
    w, vols, corr (from factor_exposures / esg readers)."""
    wa, va, ca = run_a["w"], run_a["vols"], run_a["corr"]
    wb, vb, cb = run_b["w"], run_b["vols"], run_b["corr"]
    v0 = _closed_form(wa, va, ca)
    v1 = _closed_form(wb, va, ca)
    v2 = _closed_form(wb, vb, ca)
    v3 = _closed_form(wb, vb, cb)
    dc = cb - ca
    iu = np.triu_indices(esg.N_FACTORS, k=1)
    order = np.argsort(-np.abs(dc[iu]))
    cells = []
    for k in order[:top_cells]:
        i, j = int(iu[0][k]), int(iu[1][k])
        cells.append({
            "cell": f"{esg.FACTOR_ORDER[i]}~{esg.FACTOR_ORDER[j]}",
            "from": float(ca[i, j]),
            "to": float(cb[i, j]),
            "delta": float(dc[i, j]),
        })
    return {
        "analytic_var_a_gbp": v0,
        "analytic_var_b_gbp": v3,
        "analytic_delta_var_gbp": v3 - v0,
        "steps_gbp": {
            "exposure": v1 - v0,
            "vol": v2 - v1,
            "correlation": v3 - v2,
        },
        "steps_sum_gbp": v3 - v0,
        "largest_correlation_cells": cells,
        "z": Z_995,
    }
