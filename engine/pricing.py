"""Position and liability pricing (SPEC sections 3 and 7).

- Bonds: fixed coupon, annual payments, integer years to maturity.
  price = sum cashflow(t) * df(t), df(t) = (1 + z(t)) ** (-t).
  Government: z(t) from the govy curve (gbp_gilt or ust).
  Corporate: z(t) = local govy curve + spread(t, rating);
  spread(t, rating) = level[rating] * M(t).
- Equities: single names proxied 1:1 by their index. Value = quantity *
  ref_price * index_level(asof) / index_level(ref_asof) (SPEC section 7). The
  book may carry `ref_index_levels` (index levels at `ref_asof`); when absent,
  ref_asof is taken to equal the assumptions asof and the scale is 1.
- Cash: par; USD cash carries FX risk only.
- FX: USD positions are priced in USD then converted at GBPUSD
  (GBP value = USD / GBPUSD; GBPUSD = USD per 1 GBP).
- Liabilities: four cohorts by class and currency (SPEC section 7), each a
  fixed annual claims-payment vector. GBP cohorts PV on the gbp_swap curve;
  USD cohorts PV on the ust curve in USD and convert at GBPUSD, so
  liabilities carry FX risk. A legacy {"currency", "cashflows"} file still
  loads as a single GBP/gbp_swap cohort (back-compat).

All values returned in GBP.
"""

from __future__ import annotations

import numpy as np

from .curves import discount_factors, interp_weights, spread_profile
from .esg import RATINGS, EQUITY_INDICES


def usd_to_gbp(usd_amount, gbpusd):
    """GBP value of a USD amount at GBPUSD (USD per 1 GBP)."""
    return np.asarray(usd_amount, dtype=float) / np.asarray(gbpusd, dtype=float)


def gbp_to_usd(gbp_amount, gbpusd):
    """USD value of a GBP amount at GBPUSD (USD per 1 GBP)."""
    return np.asarray(gbp_amount, dtype=float) * np.asarray(gbpusd, dtype=float)


def _as_sim_state(state: dict) -> dict:
    """Normalize a base (deterministic) state to simulated shape with S = 1."""
    curves = {}
    for name, arr in state["curves"].items():
        arr = np.asarray(arr, dtype=float)
        curves[name] = arr[None, :] if arr.ndim == 1 else arr
    spreads = np.asarray(state["spreads"], dtype=float)
    if spreads.ndim == 1:
        spreads = spreads[None, :]
    equity = np.asarray(state["equity"], dtype=float)
    if equity.ndim == 1:
        equity = equity[None, :]
    fx = np.atleast_1d(np.asarray(state["fx"], dtype=float))
    return {"curves": curves, "spreads": spreads, "equity": equity, "fx": fx}


def _bond_cashflows(position: dict):
    """(times, cashflows) for a fixed-coupon annual bond, integer maturity."""
    T = int(position["maturity_years"])
    if T < 1:
        raise ValueError("maturity_years must be a positive integer: %r" % position)
    notional = float(position["notional"])
    coupon = float(position["coupon"])
    times = np.arange(1, T + 1, dtype=float)
    cfs = np.full(T, coupon * notional)
    cfs[-1] += notional
    return times, cfs


def value_position(position: dict, state: dict, ref_index_levels=None) -> np.ndarray:
    """Value one position in GBP under a (possibly simulated) state.

    Returns an array of shape (n_sims,); n_sims = 1 for a base state.
    """
    st = _as_sim_state(state)
    ptype = position["type"]
    currency = position["currency"]

    if ptype in ("govt_bond", "corp_bond"):
        times, cfs = _bond_cashflows(position)
        w = interp_weights(times)  # (T, 4)
        z = st["curves"][position["curve"]] @ w.T  # (S, T)
        if ptype == "corp_bond":
            r_idx = RATINGS.index(position["rating"])
            level = st["spreads"][:, r_idx]  # (S,)
            z = z + level[:, None] * spread_profile(times)[None, :]
        value_local = discount_factors(z, times) @ cfs  # (S,)
    elif ptype == "equity":
        i_idx = EQUITY_INDICES.index(position["index"])
        level = st["equity"][:, i_idx]  # (S,)
        if ref_index_levels is not None and position["index"] in ref_index_levels:
            scale = level / float(ref_index_levels[position["index"]])
        else:
            # ref_asof == asof: scale is 1 (SPEC section 7, base run).
            scale = np.ones_like(level)
        value_local = float(position["quantity"]) * float(position["ref_price"]) * scale
    elif ptype == "cash":
        value_local = np.full(st["fx"].shape, float(position["amount"]))
    else:
        raise ValueError("unknown position type %r" % ptype)

    if currency == "GBP":
        return np.asarray(value_local, dtype=float)
    if currency == "USD":
        return usd_to_gbp(value_local, st["fx"])
    raise ValueError("unsupported currency %r" % currency)


def value_positions(positions, state: dict, ref_index_levels=None) -> np.ndarray:
    """Value all positions; returns (n_sims, n_positions) in GBP."""
    cols = [value_position(p, state, ref_index_levels) for p in positions]
    return np.column_stack(cols)


# Liability cohort schema (SPEC sections 3 and 7).
LIABILITY_CLASSES = ("property", "casualty")
# Discount curve per currency: GBP cohorts on gbp_swap; USD cohorts on ust
# (no USD swap curve in the factor set — stated simplification, SPEC section 3).
LIABILITY_CURVE_BY_CCY = {"GBP": "gbp_swap", "USD": "ust"}


def normalize_liabilities(liabilities: dict) -> list:
    """Normalize a liabilities file to a list of cohort dicts.

    Two accepted shapes (SPEC section 7):
      - {"cohorts": [...]} — each cohort carries id, class
        (property|casualty), currency (GBP|USD), curve (gbp_swap for GBP,
        ust for USD) and cashflows [{"t": ..., "amount": ...}, ...].
      - legacy {"currency": "GBP", "cashflows": [...]} — loads as a single
        GBP/gbp_swap cohort (back-compat path).
    """
    if "cohorts" in liabilities:
        cohorts = list(liabilities["cohorts"])
        if not cohorts:
            raise ValueError("liabilities: cohorts list is empty")
        for c in cohorts:
            for key in ("id", "class", "currency", "curve", "cashflows"):
                if key not in c:
                    raise ValueError(
                        "liability cohort missing %r: %r" % (key, c))
            if c["class"] not in LIABILITY_CLASSES:
                raise ValueError(
                    "cohort %s: class must be one of %r, got %r"
                    % (c["id"], LIABILITY_CLASSES, c["class"]))
            ccy = c["currency"]
            if ccy not in LIABILITY_CURVE_BY_CCY:
                raise ValueError(
                    "cohort %s: unsupported currency %r" % (c["id"], ccy))
            if c["curve"] != LIABILITY_CURVE_BY_CCY[ccy]:
                raise ValueError(
                    "cohort %s: %s cohorts discount on %s (SPEC section 3), "
                    "got %r" % (c["id"], ccy, LIABILITY_CURVE_BY_CCY[ccy],
                                c["curve"]))
        return cohorts
    # Legacy single-vector schema: one GBP cohort on gbp_swap.
    if liabilities.get("currency", "GBP") != "GBP":
        raise ValueError("legacy liabilities must be GBP (SPEC section 3)")
    return [{"id": "LEGACY-GBP", "class": "legacy", "currency": "GBP",
             "curve": "gbp_swap", "cashflows": liabilities["cashflows"]}]


def _pv_cohort(cohort: dict, st: dict) -> np.ndarray:
    """PV of one cohort in GBP under a normalized sim state; shape (S,)."""
    cfs = sorted(cohort["cashflows"], key=lambda c: float(c["t"]))
    times = np.array([float(c["t"]) for c in cfs])
    amounts = np.array([float(c["amount"]) for c in cfs])
    z = st["curves"][cohort["curve"]] @ interp_weights(times).T  # (S, T)
    pv_local = discount_factors(z, times) @ amounts  # (S,), cohort currency
    if cohort["currency"] == "USD":
        return usd_to_gbp(pv_local, st["fx"])
    return np.asarray(pv_local, dtype=float)


def pv_liability_cohorts(liabilities: dict, state: dict):
    """Per-cohort liability PVs in GBP.

    Returns (cohorts, pvs): the normalized cohort list and an array of shape
    (n_sims, n_cohorts) (n_sims = 1 for a base state).
    """
    st = _as_sim_state(state)
    cohorts = normalize_liabilities(liabilities)
    pvs = np.column_stack([_pv_cohort(c, st) for c in cohorts])
    return cohorts, pvs


def pv_liabilities(liabilities: dict, state: dict) -> np.ndarray:
    """Total liability PV across cohorts, in GBP.

    GBP cohorts discount on gbp_swap; USD cohorts discount on ust in USD and
    convert at GBPUSD (so liabilities respond to ir_gbp, ir_usd and fx).
    Returns (n_sims,) in GBP (n_sims = 1 for a base state).
    """
    return pv_liability_cohorts(liabilities, state)[1].sum(axis=1)


def surplus(positions, liabilities, state: dict, ref_index_levels=None) -> np.ndarray:
    """Surplus = total asset value - liability PV, in GBP; shape (n_sims,)."""
    assets = value_positions(positions, state, ref_index_levels).sum(axis=1)
    return assets - pv_liabilities(liabilities, state)
