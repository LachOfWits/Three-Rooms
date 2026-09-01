"""Curve utilities (SPEC section 2 and 3).

Linear interpolation on zero rates between the tenor points {2, 5, 10, 20},
flat extrapolation outside 2y-20y. Discounting: df(t) = (1 + z(t)) ** (-t).

All rates are decimals (0.042 = 4.2%). Tenors are in years.
"""

from __future__ import annotations

import numpy as np

# Canonical tenor grid for every curve (SPEC section 1).
TENORS = np.array([2.0, 5.0, 10.0, 20.0])

# Spread term profile M (SPEC section 2):
# spread(tenor, rating) = level[rating] * M[tenor].
SPREAD_TERM_PROFILE = {2.0: 0.85, 5.0: 1.00, 10.0: 1.10, 20.0: 1.20}
_M_VALUES = np.array([SPREAD_TERM_PROFILE[t] for t in TENORS])


def interp_weights(times) -> np.ndarray:
    """Weight matrix W of shape (len(times), 4) with z(t) = W @ z_tenors.

    Linear between adjacent tenor points, flat (weight 1 on the end tenor)
    outside [2, 20]. Works for any array-like of times in years.
    """
    times = np.atleast_1d(np.asarray(times, dtype=float))
    w = np.zeros((times.shape[0], TENORS.shape[0]))
    for i, t in enumerate(times):
        if t <= TENORS[0]:
            w[i, 0] = 1.0
        elif t >= TENORS[-1]:
            w[i, -1] = 1.0
        else:
            j = int(np.searchsorted(TENORS, t, side="right")) - 1
            t0, t1 = TENORS[j], TENORS[j + 1]
            frac = (t - t0) / (t1 - t0)
            w[i, j] = 1.0 - frac
            w[i, j + 1] = frac
    return w


def interp_zero(tenor_rates, times) -> np.ndarray:
    """Interpolate zero rates.

    tenor_rates: array of shape (4,) or (n_sims, 4) holding rates at TENORS.
    times: array-like of times in years, length T.
    Returns shape (T,) for 1-d input, (n_sims, T) for 2-d input.
    """
    rates = np.asarray(tenor_rates, dtype=float)
    w = interp_weights(times)
    if rates.ndim == 1:
        return w @ rates
    return rates @ w.T


def spread_profile(times) -> np.ndarray:
    """Term profile M(t), interpolated like zero rates (flat outside 2-20)."""
    return interp_weights(times) @ _M_VALUES


def discount_factors(z, times) -> np.ndarray:
    """df(t) = (1 + z(t)) ** (-t), elementwise; broadcasts over leading dims."""
    times = np.asarray(times, dtype=float)
    return (1.0 + np.asarray(z, dtype=float)) ** (-times)


def curve_dict_to_array(curve: dict) -> np.ndarray:
    """Assumptions curve mapping {2: z2, 5: z5, 10: z10, 20: z20} -> array (4,)."""
    return np.array([float(curve[int(t)]) for t in TENORS])
