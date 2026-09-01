"""VaR computations (SPEC sections 1 and 4).

VaR: 1-year horizon, 99.5th percentile loss of surplus (assets - liabilities),
in GBP, reported as a positive number.

Factor blocks (SPEC section 4), indices into the 21-factor vector
(esg.FACTOR_ORDER):
  ir_gbp : gbp_swap + gbp_gilt   (factors 0-7)
  ir_usd : ust                   (factors 8-11)
  credit : spread levels         (factors 12-16: AA, A, BBB, HY, CCC)
  equity : equity indices        (factors 17-19)
  fx     : GBPUSD                (factor 20)

Block standalone VaR: only that block's factors live, all others frozen at
their base values; liabilities are included in the portfolio P&L for every
block. With the SPEC section 7 cohort schema they genuinely move in three of
them: ir_gbp (GBP cohorts on gbp_swap), ir_usd (USD cohorts on ust) and fx
(USD cohorts translated at GBPUSD).
"""

from __future__ import annotations

import numpy as np

from . import esg, pricing

VAR_LEVEL = 0.995

# Exact block definitions (SPEC section 4) as factor indices.
BLOCKS = {
    "ir_gbp": list(range(0, 8)),
    "ir_usd": list(range(8, 12)),
    "credit": list(range(12, 17)),
    "equity": list(range(17, 20)),
    "fx": [20],
}


def var_from_pnl(pnl: np.ndarray, level: float = VAR_LEVEL) -> float:
    """99.5th percentile loss (positive number) from a P&L sample."""
    return float(-np.quantile(np.asarray(pnl, dtype=float), 1.0 - level))


def block_mask(block: str) -> np.ndarray:
    """Boolean (21,) live-mask for a named factor block."""
    mask = np.zeros(esg.N_FACTORS, dtype=bool)
    mask[BLOCKS[block]] = True
    return mask


def surplus_pnl(assumptions, positions, liabilities, shocks,
                ref_index_levels=None, live_mask=None) -> np.ndarray:
    """Simulated 1-year surplus P&L (n_sims,) vs the base state, in GBP."""
    state0 = esg.base_state(assumptions)
    base = pricing.surplus(positions, liabilities, state0, ref_index_levels)[0]
    sim_state = esg.apply_shocks(state0, shocks, live_mask)
    sim = pricing.surplus(positions, liabilities, sim_state, ref_index_levels)
    return sim - base


def position_pnl_matrix(assumptions, positions, shocks,
                        ref_index_levels=None) -> np.ndarray:
    """Per-position P&L matrix (n_sims, n_positions), all factors live."""
    state0 = esg.base_state(assumptions)
    base = pricing.value_positions(positions, state0, ref_index_levels)  # (1, n)
    sim_state = esg.apply_shocks(state0, shocks)
    sim = pricing.value_positions(positions, sim_state, ref_index_levels)
    return sim - base


def position_standalone_vars(assumptions, positions, shocks,
                             ref_index_levels=None) -> list:
    """Full-factor standalone VaR per position (that position alone)."""
    pnl = position_pnl_matrix(assumptions, positions, shocks, ref_index_levels)
    return [var_from_pnl(pnl[:, j]) for j in range(pnl.shape[1])]


def block_standalone_vars(assumptions, positions, liabilities, shocks,
                          ref_index_levels=None) -> dict:
    """Standalone surplus VaR per factor block (other factors frozen)."""
    out = {}
    for name in BLOCKS:
        pnl = surplus_pnl(assumptions, positions, liabilities, shocks,
                          ref_index_levels, live_mask=block_mask(name))
        out[name] = var_from_pnl(pnl)
    return out


def aggregate_var(assumptions, positions, liabilities, shocks,
                  ref_index_levels=None) -> float:
    """Full-correlation aggregate surplus VaR (all factors live)."""
    pnl = surplus_pnl(assumptions, positions, liabilities, shocks,
                      ref_index_levels)
    return var_from_pnl(pnl)
