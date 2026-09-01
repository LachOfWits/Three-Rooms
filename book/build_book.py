"""Build the synthetic book per SPEC section 7 (50 positions incl. 4 PC funds).

Writes:
  - book/positions.json           (exactly 50 positions, baseline book)
  - book/positions_2026-03.json   (baseline + ~GBP 25m premium inflow invested:
                                   PC notionals +15.0% exactly, the remainder
                                   spread pro-rata across every other position
                                   -- growth AND reallocation, SPEC section 7)
  - book/liabilities.json         (four P&C cohorts: property/casualty x GBP/USD)
  - book/liabilities_2026-03.json (cohorts scaled +2.5% for written business,
                                   class mix and duration held constant)

Deterministic: no randomness, no network, no AI. All public-market sizing is
computed at a flat 4% zero curve (sizing convention only -- the engine prices
off the assumptions YAML). Durations are *modified* durations at that flat 4%
curve. The four private credit proxies (PCF-001..004) are instead sized at the
REAL 2026-02 base curves (SPEC section 3: notional such that DCF value ~ NAV
at the base month-end curves), and are EXCLUDED from the flat-4% FI duration
statistic (SPEC section 3; rationale in book/README.md).

Targets (asserted before writing):
  - exactly 50 positions: 12 govt / 16 public corp / 4 PC proxy / 13 equity / 5 cash
  - total assets ~ GBP 1.0bn market value (+/- 10%)
  - public fixed-income (govt + corp, excl. PCF) MV-weighted modified
    duration ~ 5.0 (4.5 - 5.5) at flat 4%
  - PC proxy DCF total ~ GBP 60m (+/- 2%) at the 2026-02 base curves
  - liabilities: property cohort duration 2.0-2.5, casualty 5.9-7.0, overall
    3.6-4.4 (target ~4.0); ~42% property / 58% casualty and 55% GBP / 45% USD
    by PV at flat 4%; total PV ~ GBP 810m (the balance-sheet shape the demo
    already shows)
  - March book: premium inflow within GBP 24.8m-25.2m of target 25m; PC takes
    +15.0% of its own sleeve (a multiple of its pro-rata share of the inflow)

Run:
  "C:/Users/lachl/Documents/AI Symposium 2026 IFoA/prototype/.venv/Scripts/python.exe" \
      "C:/Users/lachl/Documents/AI Symposium 2026 IFoA/prototype/book/build_book.py"
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

BOOK_DIR = Path(__file__).resolve().parent
ROOT = BOOK_DIR.parent

# ---------------------------------------------------------------------------
# Sizing conventions (documented in book/README.md)
# ---------------------------------------------------------------------------
FLAT_Y = 0.04          # flat zero curve used for public-market sizing
GBPUSD_REF = 1.34      # approx GBPUSD used for sizing public USD positions
REF_ASOF = "2026-07-31"
# Index levels at REF_ASOF, taken from assumptions/2026-07.yaml (calibrated
# from data/processed/equity.csv, last business day <= 2026-07-31). The engine
# scales equity ref_price by index_level(asof)/index_level(ref_asof) (SPEC
# section 7); without this the scale silently defaults to 1 and equities carry
# no index risk.
REF_INDEX_LEVELS = {"FTSE100": 10868.1, "SP500": 7489.72, "SX5E": 6358.01}

ASSET_TOTAL_TARGET = 1_000_000_000.0   # GBP
FI_DURATION_TARGET = 5.0
GOVT_MV_TARGET = 340_000_000.0         # GBP
CORP_MV_TARGET = 280_000_000.0         # GBP (16 public corporates)
PC_NAV_TARGET = 60_000_000.0           # GBP (4 PC proxies, at 2026-02 curves)
LIAB_PV_TARGET = 810_000_000.0         # GBP, flat 4% (see README: real-curve
                                       # PVs at 2026-02/03 land within ~2%)

# Liability cohort design (SPEC sections 3 and 7): specialty P&C reserves.
# Shares are of total PV at the flat-4% sizing convention (USD cohorts
# converted at GBPUSD_REF). 42/58 property/casualty is the "roughly 40/60"
# blend positioned so the overall duration sits inside 3.6-4.4 given cohort
# durations at the bottom of their bands (2.0 and 6.0: a 40/60 blend of
# exactly-in-band cohorts cannot get much below 4.4 -- documented in README).
LIAB_GBP_SHARE = 0.55                  # GBP/USD split 55/45 (README rationale)
LIAB_PROP_SHARE = 0.42                 # property/casualty split 42/58
# Payment patterns (fractions of cohort nominal, t = 1..n). Property pays
# fast: bulk in years 1-3, tail to 6y (flat-4% mod duration ~2.03). Casualty
# is long-tailed: t^1.5 * exp(-0.33 t) over 1..15y, peak at year 5, ~2% of
# nominal still paying in year 15 (flat-4% mod duration ~6.00).
LIAB_PROP_SHAPE = [0.40, 0.28, 0.16, 0.09, 0.05, 0.02]
LIAB_CAS_SHAPE = [t ** 1.5 * 2.718281828459045 ** (-0.33 * t)
                  for t in range(1, 16)]
LIAB_CAS_SHAPE = [w / sum(LIAB_CAS_SHAPE) for w in LIAB_CAS_SHAPE]
LIAB_COHORTS = [
    # id, class, currency, curve, shape, share of total flat-4% PV
    ("L-PROP-GBP", "property", "GBP", "gbp_swap",
     LIAB_PROP_SHAPE, LIAB_PROP_SHARE * LIAB_GBP_SHARE),
    ("L-PROP-USD", "property", "USD", "ust",
     LIAB_PROP_SHAPE, LIAB_PROP_SHARE * (1 - LIAB_GBP_SHARE)),
    ("L-CAS-GBP", "casualty", "GBP", "gbp_swap",
     LIAB_CAS_SHAPE, (1 - LIAB_PROP_SHARE) * LIAB_GBP_SHARE),
    ("L-CAS-USD", "casualty", "USD", "ust",
     LIAB_CAS_SHAPE, (1 - LIAB_PROP_SHARE) * (1 - LIAB_GBP_SHARE)),
]
LIAB_GROWTH = 1.025                    # 2026-03 written business: +2.5% PV

# The demo book pair (SPEC section 7): PC sizing month and the March book.
PC_BASE_MONTH = "2026-02"
PAIR_CURR_MONTH = "2026-03"
PC_SCALE = 1.15                        # +15.0% PC allocation in the March book

# A month-end is not a uniform scale-up. Most holdings are simply repriced at
# the new curves (the engine does that); what actually CHANGES in the book is:
#   income received, premium received, that money deployed, and a trade or two.
# These constants shape the Feb -> Mar book so it reads like a real month.
EQUITY_DIV_YIELD = 0.025               # annual, accrued monthly, approximate
DEPLOY_GILT_ID = "P007"                # 10y gilt — takes part of the inflow
DEPLOY_GILT_ADD = 8_000_000
DEPLOY_CORP_ID = "P018"                # A-rated GBP corporate
DEPLOY_CORP_ADD = 5_000_000
TRIM_EQUITY_ID = "P034"                # S&P name, trimmed after the rally
TRIM_EQUITY_MV = 3_000_000             # approx GBP proceeds
GBP_CASH_ID = "P042"
PREMIUM_INFLOW = 25_000_000.0          # GBP of new money invested in March

# Spread term profile M (SPEC section 2), interpolated like zero rates
# (linear between tenor points, flat outside 2-20y) -- mirrors engine/curves.py.
TENORS = [2.0, 5.0, 10.0, 20.0]
M_PROFILE = [0.85, 1.00, 1.10, 1.20]


# ---------------------------------------------------------------------------
# ISIN helpers
# ---------------------------------------------------------------------------
def isin_check_digit(base11: str) -> str:
    """Compute the ISIN check digit (Luhn over the digitised 11-char base)."""
    digits = "".join(str(int(c, 36)) for c in base11)
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 0:      # rightmost digit of the base is doubled
            d *= 2
            d = d // 10 + d % 10
        total += d
    return str((10 - total % 10) % 10)


def finish_isin(isin: str) -> str:
    """Validate a 12-char ISIN's checksum, or append the digit to an 11-char base."""
    if len(isin) == 11:
        return isin + isin_check_digit(isin)
    if len(isin) == 12:
        expect = isin_check_digit(isin[:11])
        if isin[11] != expect:
            raise ValueError(f"ISIN checksum failure for {isin}: expected final digit {expect}")
        return isin
    raise ValueError(f"Bad ISIN length: {isin}")


# ---------------------------------------------------------------------------
# Flat-curve bond maths (annual coupons, integer years, per SPEC section 3)
# ---------------------------------------------------------------------------
def bond_price(coupon: float, years: int, y: float = FLAT_Y) -> float:
    """Price per unit notional at a flat zero curve."""
    dfs = [(1 + y) ** -t for t in range(1, years + 1)]
    return coupon * sum(dfs) + dfs[-1]


def bond_dur_mod(coupon: float, years: int, y: float = FLAT_Y) -> float:
    """Modified duration at the flat curve."""
    price = bond_price(coupon, years, y)
    mac = sum(t * (coupon + (1.0 if t == years else 0.0)) * (1 + y) ** -t
              for t in range(1, years + 1)) / price
    return mac / (1 + y)


# ---------------------------------------------------------------------------
# Real-curve bond maths for the PC proxies (mirrors engine/curves.py +
# engine/pricing.py exactly: linear zero interp, flat outside 2-20y,
# spread(t) = level * M(t), df = (1+z)^-t).
# ---------------------------------------------------------------------------
def _interp(tenor_vals, t: float) -> float:
    if t <= TENORS[0]:
        return tenor_vals[0]
    if t >= TENORS[-1]:
        return tenor_vals[-1]
    for j in range(len(TENORS) - 1):
        if TENORS[j] <= t <= TENORS[j + 1]:
            frac = (t - TENORS[j]) / (TENORS[j + 1] - TENORS[j])
            return tenor_vals[j] * (1 - frac) + tenor_vals[j + 1] * frac
    raise AssertionError


def load_assumptions(month: str) -> dict:
    with open(ROOT / "assumptions" / f"{month}.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def corp_zeros(assump: dict, curve: str, rating, years: int) -> list[float]:
    """z(t) = govy(t) + spread_level(rating) * M(t) for t = 1..years.

    rating=None gives the plain govy curve (government bonds).
    """
    gov = [float(assump["curves"][curve][int(k)]) for k in (2, 5, 10, 20)]
    level = 0.0 if rating is None else float(assump["spreads"][rating])
    return [_interp(gov, t) + level * _interp(M_PROFILE, t)
            for t in range(1, years + 1)]


def corp_price_real(assump: dict, curve: str, rating: str, coupon: float,
                    years: int) -> float:
    """Price per unit notional at real base curves (engine DCF convention)."""
    zs = corp_zeros(assump, curve, rating, years)
    dfs = [(1 + z) ** -t for t, z in zip(range(1, years + 1), zs)]
    return coupon * sum(dfs) + dfs[-1]


def par_coupon_real(assump: dict, curve: str, rating: str, years: int) -> float:
    """Coupon that prices the bond exactly at par on the real base curves."""
    zs = corp_zeros(assump, curve, rating, years)
    dfs = [(1 + z) ** -t for t, z in zip(range(1, years + 1), zs)]
    return (1.0 - dfs[-1]) / sum(dfs)


# ---------------------------------------------------------------------------
# Instrument static data.
# ISINs: 12 chars = transcribed in full from public records (checksum asserted);
# 11 chars = issuer/CUSIP base verified publicly, check digit computed here.
# maturity_years = integer years remaining from 2026-07-31 (nearest year).
# target = initial GBP market-value target before the duration tilt (rescaled).
# ---------------------------------------------------------------------------
GOVT = [
    # name, isin, ccy, curve, coupon, maturity_years, target GBP MV
    ("UKT 4.25 2027",  "GB00B16NNR78", "GBP", "gbp_gilt", 0.0425, 1,  40e6),
    ("UKT 4.75 2030",  "GB00B24FF097", "GBP", "gbp_gilt", 0.0475, 4,  45e6),
    ("UKT 4.25 2032",  "GB0004893086", "GBP", "gbp_gilt", 0.0425, 6,  45e6),
    ("UKT 4.25 2036",  "GB0032452392", "GBP", "gbp_gilt", 0.0425, 10, 30e6),
    ("UKT 4.50 2042",  "GB00B1VWPJ53", "GBP", "gbp_gilt", 0.0450, 16, 20e6),
    ("UKT 4.25 2046",  "GB00B128DP45", "GBP", "gbp_gilt", 0.0425, 20, 15e6),
    ("UST 2.875 2028", "US9128284N73", "USD", "ust",      0.02875, 2, 40e6),
    ("UST 0.625 2030", "US912828ZQ6",  "USD", "ust",      0.00625, 4, 35e6),
    ("UST 3.875 2033", "US91282CHT18", "USD", "ust",      0.03875, 7, 25e6),
    ("UST 4.00 2034",  "US91282CJZ5",  "USD", "ust",      0.0400, 8,  20e6),
    ("UST 2.00 2050",  "US912810SL35", "USD", "ust",      0.0200, 24, 12e6),
    ("UST 4.00 2052",  "US912810TL26", "USD", "ust",      0.0400, 26, 13e6),
]

# 16 public corporates: the strongest 16 of the previous 18-line sleeve.
# Dropped (documented in book/README.md): Diageo Finance 2.875% 2029
# (duplicated the 3y A GBP consumer slot held by Unilever 1.875% 2029) and
# British Telecom 3.125% 2031 (duplicated the mid-tenor BBB GBP telecom slot
# held by the larger benchmark Vodafone 5.90% 2032). Every rating bucket keeps
# at least one line in each currency, and Ford (D2's target) is retained and
# ordered LAST so its id stays P028.
CORP = [
    # name, isin, ccy, rating, coupon, maturity_years, target GBP MV
    ("Apple Inc 3.85% 2043",              "US037833AL42", "USD", "AA",  0.0385, 17, 11e6),
    ("Microsoft Corp 3.45% 2036",         "US594918BS26", "USD", "AA",  0.0345, 10, 15e6),
    ("Nestle Holdings Inc 1.375% 2033",   "XS2354308194", "GBP", "AA",  0.01375, 7, 19e6),
    ("Wellcome Trust Finance 4.625% 2036","XS0261559594", "GBP", "AA",  0.04625, 10, 15e6),
    ("HSBC Holdings 6.00% 2040",          "XS0498768315", "GBP", "A",   0.0600, 14, 13e6),
    ("JPMorgan Chase 3.625% 2027",        "US46625HRX0",  "USD", "A",   0.03625, 1, 26e6),
    ("Total Capital Intl 3.455% 2029",    "US89153VAQ23", "USD", "A",   0.03455, 3, 23e6),
    ("Unilever 1.875% 2029",              "XS1684780205", "GBP", "A",   0.01875, 3, 22e6),
    ("Lloyds Banking Group 2.707% 2035",  "XS2265524640", "GBP", "A",   0.02707, 9, 16e6),
    ("Barclays PLC 3.25% 2033",           "XS1748699011", "GBP", "BBB", 0.0325, 6,  20e6),
    ("AT&T Inc 4.30% 2042",               "US00206RBH49", "USD", "BBB", 0.0430, 16, 11e6),
    ("Verizon Communications 4.329% 2028","US92343VER15", "USD", "BBB", 0.04329, 2, 24e6),
    ("Vodafone Group 5.90% 2032",         "XS0158715713", "GBP", "BBB", 0.0590, 6,  20e6),
    ("Ocado Group 10.50% 2029",           "XS2871478058", "GBP", "HY",  0.1050, 3,  11e6),
    ("Virgin Media Secured Fin 4.25% 2030","XS2062666602","GBP", "HY",  0.0425, 3,  13e6),
    ("Ford Motor Credit 7.35% 2027",      "US345397C353", "USD", "HY",  0.0735, 1,  13e6),
]

# Private credit proxies (SPEC section 3): synthetic 4y annual-coupon corporate
# bonds, base proxy rating HY, unlisted (internal PCF ids, no ISIN). Sized so
# the four DCF values sum ~ GBP 60m at the 2026-02 base curves; coupon = the
# 4y par yield (govy + HY spread) on those curves rounded to 5bp, so each
# proxy prices near par. `asset_class` and `strategy` are inert to the engine
# (verified by engine tests) and consumed by the app layer (SPEC-APP D4).
PC_FUNDS = [
    # id, name, ccy, strategy, target GBP NAV at 2026-02 curves
    ("PCF-001", "PC Fund I - Senior Direct Lending (GBP)",  "GBP",
     "senior_direct_lending", 18e6),
    ("PCF-002", "PC Fund II - Mid-Market Lending (USD)",    "USD",
     "mid_market_lending",    15e6),
    ("PCF-003", "PC Fund III - Opportunistic Credit (USD)", "USD",
     "opportunistic_credit",  13e6),
    ("PCF-004", "PC Fund IV - Real Estate Debt (GBP)",      "GBP",
     "real_estate_debt",      14e6),
]
PC_RATING = "HY"
PC_MATURITY = 4

EQUITY = [
    # name, isin, ccy, index, ref_price (local quote ccy of the position), target GBP MV
    # SX5E names are carried as GBP-denominated lines (no EUR factor in SPEC s2);
    # their ref_price is the EUR price converted at an assumed EURGBP of 0.845.
    # 13 names (5 FTSE100 / 5 SP500 / 3 SX5E): BP PLC and Exxon Mobil were
    # trimmed from the previous 15-line sleeve (smallest lines in their index
    # buckets; the three-index spread is preserved).
    ("AstraZeneca PLC",        "GB0009895292", "GBP", "FTSE100", 128.00, 22e6),
    ("Shell PLC",              "GB00BP6MXD84", "GBP", "FTSE100", 28.50,  22e6),
    ("HSBC Holdings PLC",      "GB0005405286", "GBP", "FTSE100", 10.40,  16e6),
    ("Unilever PLC",           "GB00B10RZP78", "GBP", "FTSE100", 45.50,  15e6),
    ("Rio Tinto PLC",          "GB0007188757", "GBP", "FTSE100", 51.00,  15e6),
    ("Apple Inc",              "US0378331005", "USD", "SP500",   252.00, 20e6),
    ("Microsoft Corp",         "US5949181045", "USD", "SP500",   490.00, 22e6),
    ("Alphabet Inc Class A",   "US02079K3059", "USD", "SP500",   310.00, 16e6),
    ("Johnson & Johnson",      "US4781601046", "USD", "SP500",   200.00, 14e6),
    ("JPMorgan Chase & Co",    "US46625H1005", "USD", "SP500",   305.00, 16e6),
    ("ASML Holding NV",        "NL0010273215", "GBP", "SX5E",    745.00, 20e6),
    ("SAP SE",                 "DE0007164600", "GBP", "SX5E",    199.00, 17e6),
    ("TotalEnergies SE",       "FR0000120271", "GBP", "SX5E",    48.00,  15e6),
]

CASH = [
    # name, ccy, amount (local currency units)
    ("GBP cash (main)",       "GBP", 40_000_000),
    ("GBP cash (collateral)", "GBP", 15_000_000),
    ("GBP cash (margin)",     "GBP", 10_000_000),
    ("USD cash (main)",       "USD", 20_000_000),
    ("USD cash (custody)",    "USD", 13_000_000),
]


def fx_to_gbp(ccy: str, gbpusd: float = GBPUSD_REF) -> float:
    """GBP value of one local-currency unit (SPEC s1: GBP = USD / GBPUSD)."""
    return 1.0 / gbpusd if ccy == "USD" else 1.0


# ---------------------------------------------------------------------------
# Fixed-income sizing: tilt initial MV targets so the MV-weighted modified
# duration (govt + public corp only; PCF excluded, see README) hits
# FI_DURATION_TARGET, keeping sleeve totals fixed.
# ---------------------------------------------------------------------------
def solve_fi_targets():
    bonds = []
    for name, isin, ccy, curve, coupon, mat, target in GOVT:
        bonds.append({"kind": "govt", "name": name, "isin": isin, "ccy": ccy,
                      "curve": curve, "coupon": coupon, "mat": mat, "w0": target,
                      "dur": bond_dur_mod(coupon, mat), "price": bond_price(coupon, mat)})
    for name, isin, ccy, rating, coupon, mat, target in CORP:
        curve = "ust" if ccy == "USD" else "gbp_gilt"
        bonds.append({"kind": "corp", "name": name, "isin": isin, "ccy": ccy,
                      "curve": curve, "rating": rating, "coupon": coupon, "mat": mat,
                      "w0": target, "dur": bond_dur_mod(coupon, mat),
                      "price": bond_price(coupon, mat)})

    sleeve_totals = {"govt": GOVT_MV_TARGET, "corp": CORP_MV_TARGET}

    def targets_for(lam: float):
        out = []
        for kind in ("govt", "corp"):
            grp = [b for b in bonds if b["kind"] == kind]
            raw = [b["w0"] * pow(2.718281828459045, -lam * (b["dur"] - FI_DURATION_TARGET))
                   for b in grp]
            scale = sleeve_totals[kind] / sum(raw)
            out.extend((b, r * scale) for b, r in zip(grp, raw))
        return out

    def weighted_dur(pairs):
        tot = sum(mv for _, mv in pairs)
        return sum(b["dur"] * mv for b, mv in pairs) / tot

    lo, hi = -1.0, 1.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        d = weighted_dur(targets_for(mid))
        if d > FI_DURATION_TARGET:
            lo = mid
        else:
            hi = mid
    pairs = targets_for(0.5 * (lo + hi))

    # Convert GBP MV targets to local-currency notionals, rounded to 100k.
    for b, mv_gbp in pairs:
        notional_local = mv_gbp / fx_to_gbp(b["ccy"]) / b["price"]
        b["notional"] = int(round(notional_local / 100_000.0) * 100_000)
    return bonds


# ---------------------------------------------------------------------------
# Private credit proxy sizing at the REAL 2026-02 base curves.
# ---------------------------------------------------------------------------
def build_pc_positions(assump_base: dict):
    """Returns (positions, total NAV GBP at 2026-02 curves, details)."""
    gbpusd = float(assump_base["fx"]["GBPUSD"])
    out, details = [], []
    total_nav = 0.0
    for pid, name, ccy, strategy, nav_target in PC_FUNDS:
        curve = "ust" if ccy == "USD" else "gbp_gilt"
        par = par_coupon_real(assump_base, curve, PC_RATING, PC_MATURITY)
        coupon = round(par / 0.0005) * 0.0005      # nearest 5bp -> near par
        price = corp_price_real(assump_base, curve, PC_RATING, coupon, PC_MATURITY)
        notional_local = nav_target / fx_to_gbp(ccy, gbpusd) / price
        notional = int(round(notional_local / 100_000.0) * 100_000)
        nav_gbp = notional * price * fx_to_gbp(ccy, gbpusd)
        total_nav += nav_gbp
        out.append({"id": pid, "type": "corp_bond", "name": name,
                    "currency": ccy, "notional": notional,
                    "coupon": round(coupon, 6), "maturity_years": PC_MATURITY,
                    "curve": curve, "rating": PC_RATING,
                    "asset_class": "private_credit", "strategy": strategy})
        details.append((pid, ccy, coupon, price, notional, nav_gbp))
    return out, total_nav, details


def pc_slice_value_gbp(pcf_positions, old_notionals, assump: dict) -> float:
    """GBP market value, at `assump` base curves, of the notional increase."""
    gbpusd = float(assump["fx"]["GBPUSD"])
    total = 0.0
    for p in pcf_positions:
        added = p["notional"] - old_notionals[p["id"]]
        price = corp_price_real(assump, p["curve"], p["rating"],
                                p["coupon"], p["maturity_years"])
        total += added * price * fx_to_gbp(p["currency"], gbpusd)
    return total


# ---------------------------------------------------------------------------
# Liabilities: four P&C cohorts (SPEC sections 3 and 7), calibrated so each
# cohort's PV at the flat-4% sizing convention equals its share of
# LIAB_PV_TARGET (USD cohort amounts are USD; shares are GBP at GBPUSD_REF).
# ---------------------------------------------------------------------------
def build_liability_cohorts(growth: float = 1.0):
    """Cohort dicts per the SPEC section 7 schema, amounts rounded to 1000."""
    cohorts = []
    for cid, klass, ccy, curve, shape, share in LIAB_COHORTS:
        pv_shape = sum(w * (1 + FLAT_Y) ** -t
                       for t, w in enumerate(shape, start=1))
        # cohort nominal in LOCAL currency such that flat-4% PV (GBP at
        # GBPUSD_REF for USD cohorts) = share * target * growth
        nominal = share * LIAB_PV_TARGET * growth / pv_shape / fx_to_gbp(ccy)
        cfs = [{"t": t, "amount": int(round(nominal * w / 1000.0) * 1000)}
               for t, w in enumerate(shape, start=1)]
        cohorts.append({"id": cid, "class": klass, "currency": ccy,
                        "curve": curve, "cashflows": cfs})
    return cohorts


def cohort_metrics_flat(cohort: dict):
    """(PV in GBP at GBPUSD_REF, modified duration) at the flat-4% curve."""
    cfs = cohort["cashflows"]
    pv_local = sum(c["amount"] * (1 + FLAT_Y) ** -c["t"] for c in cfs)
    mac = sum(c["t"] * c["amount"] * (1 + FLAT_Y) ** -c["t"]
              for c in cfs) / pv_local
    return pv_local * fx_to_gbp(cohort["currency"]), mac / (1 + FLAT_Y)


def cohort_pv_real(cohort: dict, assump: dict) -> float:
    """Cohort PV in GBP at real base curves (engine conventions: linear zero
    interp on 2/5/10/20, flat outside; df=(1+z)^-t; USD / GBPUSD)."""
    gov = [float(assump["curves"][cohort["curve"]][int(k)])
           for k in (2, 5, 10, 20)]
    pv_local = sum(c["amount"] * (1 + _interp(gov, float(c["t"]))) ** -c["t"]
                   for c in cohort["cashflows"])
    return pv_local * fx_to_gbp(cohort["currency"],
                                float(assump["fx"]["GBPUSD"]))


# ---------------------------------------------------------------------------
# Real-curve position valuation at a month-end (mirrors engine/pricing.py) --
# used to size and report the March premium inflow at the 2026-03 base state.
# ---------------------------------------------------------------------------
def position_unit_mv_real(pos: dict, assump: dict) -> float:
    """GBP market value per unit (per 1 notional / 1 share / 1 cash unit)."""
    gbpusd = float(assump["fx"]["GBPUSD"])
    fx = fx_to_gbp(pos["currency"], gbpusd)
    if pos["type"] in ("govt_bond", "corp_bond"):
        return corp_price_real(assump, pos["curve"], pos.get("rating"),
                               pos["coupon"], pos["maturity_years"]) * fx
    if pos["type"] == "equity":
        scale = float(assump["equity"][pos["index"]]) / \
            REF_INDEX_LEVELS[pos["index"]]
        return float(pos["ref_price"]) * scale * fx
    if pos["type"] == "cash":
        return fx
    raise ValueError(f"unknown position type {pos['type']}")


def position_units(pos: dict) -> float:
    return float(pos["notional"] if pos["type"] in ("govt_bond", "corp_bond")
                 else pos["quantity"] if pos["type"] == "equity"
                 else pos["amount"])


# ---------------------------------------------------------------------------
# Assemble, verify, write
# ---------------------------------------------------------------------------
def main():
    assump_feb = load_assumptions(PC_BASE_MONTH)
    assump_mar = load_assumptions(PAIR_CURR_MONTH)

    bonds = solve_fi_targets()
    positions = []
    pid = 0

    def next_id():
        nonlocal pid
        pid += 1
        return f"P{pid:03d}"

    fi_mv = fi_dur_mv = 0.0
    govt_mv = corp_mv = 0.0
    for b in bonds:
        isin = finish_isin(b["isin"])
        mv_gbp = b["notional"] * b["price"] * fx_to_gbp(b["ccy"])
        fi_mv += mv_gbp
        fi_dur_mv += mv_gbp * b["dur"]
        pos = {"id": next_id(),
               "type": "govt_bond" if b["kind"] == "govt" else "corp_bond",
               "name": b["name"], "isin": isin, "currency": b["ccy"],
               "notional": b["notional"], "coupon": b["coupon"],
               "maturity_years": b["mat"], "curve": b["curve"]}
        if b["kind"] == "corp":
            pos["rating"] = b["rating"]
            corp_mv += mv_gbp
        else:
            govt_mv += mv_gbp
        positions.append(pos)

    # PC proxies sit after the public corporates (PCF ids, outside the P-run).
    pc_positions, pc_nav, pc_details = build_pc_positions(assump_feb)
    positions.extend(pc_positions)

    eq_mv = 0.0
    for name, isin, ccy, index, price, target in EQUITY:
        qty = int(round(target / fx_to_gbp(ccy) / price / 100.0) * 100)
        mv_gbp = qty * price * fx_to_gbp(ccy)
        eq_mv += mv_gbp
        positions.append({"id": next_id(), "type": "equity", "name": name,
                          "isin": finish_isin(isin), "currency": ccy, "index": index,
                          "quantity": qty, "ref_price": price})

    cash_mv = 0.0
    for name, ccy, amount in CASH:
        cash_mv += amount * fx_to_gbp(ccy)
        positions.append({"id": next_id(), "type": "cash", "name": name,
                          "currency": ccy, "amount": amount})

    fi_dur = fi_dur_mv / fi_mv                       # PCF excluded (README)
    total_mv = fi_mv + pc_nav + eq_mv + cash_mv

    # ---- liabilities: four P&C cohorts + the March written-business file ----
    cohorts = build_liability_cohorts()
    cohorts_mar = build_liability_cohorts(growth=LIAB_GROWTH)
    liab_stats = [(c,) + cohort_metrics_flat(c) for c in cohorts]
    liab_pv = sum(pv for _, pv, _ in liab_stats)
    liab_dur = sum(pv * dur for _, pv, dur in liab_stats) / liab_pv
    gbp_pv = sum(pv for c, pv, _ in liab_stats if c["currency"] == "GBP")
    prop_pv = sum(pv for c, pv, _ in liab_stats if c["class"] == "property")
    liab_pv_feb = sum(cohort_pv_real(c, assump_feb) for c in cohorts)
    liab_pv_mar = sum(cohort_pv_real(c, assump_mar) for c in cohorts)
    liab_mar_stats = [(c,) + cohort_metrics_flat(c) for c in cohorts_mar]
    liab_mar_pv = sum(pv for _, pv, _ in liab_mar_stats)
    liab_mar_dur = sum(pv * dur for _, pv, dur in liab_mar_stats) / liab_mar_pv
    liab_pv_mar_grown = sum(cohort_pv_real(c, assump_mar) for c in cohorts_mar)

    # ---- the March book: ~GBP 25m premium inflow invested (SPEC section 7:
    # growth AND reallocation). PC notionals +15.0% exactly (the deliberate
    # tilt); the remainder of the inflow is spread pro-rata over every other
    # position, all measured at the 2026-03 base state (the state the money
    # arrives in). Assets GROW -- nothing is sold to fund the PC increase. ----
    positions_mar = json.loads(json.dumps(positions))  # deep copy
    by_id = {p["id"]: p for p in positions_mar}
    unit_mv = {p["id"]: position_unit_mv_real(p, assump_mar)
               for p in positions_mar}
    old_notionals = {p["id"]: p["notional"] for p in pc_positions}

    # (1) INCOME accrued over the month, approximated: one month of coupon on
    # the bond book, one month of dividend yield on equities. Real money in,
    # separate from any market movement (which the engine prices).
    coupon_income = sum(p["notional"] * p["coupon"] / 12.0
                        * (1.0 if p["currency"] == "GBP"
                           else 1.0 / float(assump_mar["fx"]["GBPUSD"]))
                        for p in positions_mar
                        if p["type"] in ("govt_bond", "corp_bond"))
    equity_income = sum(position_units(p) * unit_mv[p["id"]]
                        * EQUITY_DIV_YIELD / 12.0
                        for p in positions_mar if p["type"] == "equity")
    income = coupon_income + equity_income

    # (2) PREMIUM received on the business written this month (liabilities
    # grew LIAB_GROWTH); it lands in GBP cash before being deployed.
    # (3) DEPLOYMENT: the deliberate PC tilt, then two top-ups; the rest of
    # the money stays in cash. (4) One equity trim after the rally.
    for p in positions_mar:
        if p.get("asset_class") == "private_credit":
            assert p["notional"] % 20 == 0, f"{p['id']} notional not /20"
            p["notional"] = p["notional"] * 23 // 20
    pcf_mar = [p for p in positions_mar
               if p.get("asset_class") == "private_credit"]
    pc_slice_mv = pc_slice_value_gbp(pcf_mar, old_notionals, assump_mar)

    gilt = by_id[DEPLOY_GILT_ID]
    gilt["notional"] += DEPLOY_GILT_ADD
    gilt_mv = DEPLOY_GILT_ADD * unit_mv[DEPLOY_GILT_ID]

    corp = by_id[DEPLOY_CORP_ID]
    corp["notional"] += DEPLOY_CORP_ADD
    corp_add_mv = DEPLOY_CORP_ADD * unit_mv[DEPLOY_CORP_ID]

    eq = by_id[TRIM_EQUITY_ID]
    trim_units = int(round(TRIM_EQUITY_MV / unit_mv[TRIM_EQUITY_ID]))
    eq["quantity"] -= trim_units
    trim_mv = trim_units * unit_mv[TRIM_EQUITY_ID]

    # Everything else is untouched — held, and repriced by the engine.
    cash = by_id[GBP_CASH_ID]
    cash_delta = (income + PREMIUM_INFLOW + trim_mv
                  - pc_slice_mv - gilt_mv - corp_add_mv)
    cash["amount"] = int(round(cash["amount"] + cash_delta))

    changed = {DEPLOY_GILT_ID, DEPLOY_CORP_ID, TRIM_EQUITY_ID, GBP_CASH_ID}         | set(old_notionals)
    assert len(changed) == 8, changed
    inflow_exact = income + PREMIUM_INFLOW
    added_mv = pc_slice_mv + gilt_mv + corp_add_mv - trim_mv + cash_delta
    nonpc_mv_mar = sum(position_units(p) * unit_mv[p["id"]]
                       for p in positions_mar
                       if p.get("asset_class") != "private_credit")
    pc_mv_mar_base = sum(old_notionals[pid] * unit_mv[pid]
                         for pid in old_notionals)
    pc_share_prorata = pc_mv_mar_base / (nonpc_mv_mar + pc_mv_mar_base)
    pc_prorata_gbp = inflow_exact * pc_share_prorata

    # ---- report ----
    n_pc = sum(1 for p in positions if p.get("asset_class") == "private_credit")
    print(f"Positions:                {len(positions)}")
    print(f"  govt bonds             {sum(1 for p in positions if p['type']=='govt_bond')}")
    print(f"  public corp bonds      {sum(1 for p in positions if p['type']=='corp_bond') - n_pc}")
    print(f"  private credit proxies {n_pc}")
    print(f"  equities               {sum(1 for p in positions if p['type']=='equity')}")
    print(f"  cash                   {sum(1 for p in positions if p['type']=='cash')}")
    print(f"Sleeve MV (GBP m):  govt {govt_mv/1e6:8.1f}  corp {corp_mv/1e6:8.1f}  "
          f"pc {pc_nav/1e6:6.1f}  equity {eq_mv/1e6:8.1f}  cash {cash_mv/1e6:8.1f}")
    print(f"Total asset MV:           GBP {total_mv/1e6:,.1f}m  (target 1,000m; "
          "public sleeves flat-4%, PC at 2026-02 curves)")
    print(f"FI modified duration:     {fi_dur:.3f}  (flat 4%, target 5.0, PCF excluded)")
    print("PC proxies (2026-02 base curves):")
    for pid_, ccy, coupon, price, notional, nav_gbp in pc_details:
        print(f"  {pid_}  {ccy}  coupon {coupon*100:6.3f}%  price {price:8.6f}  "
              f"notional {notional:>12,}  NAV GBP {nav_gbp/1e6:6.2f}m")
    print(f"  total NAV GBP {pc_nav/1e6:.2f}m  (target 60m)")
    print(f"March book (SPEC s7 growth + reallocation), all at {PAIR_CURR_MONTH} "
          "base state:")
    print(f"  income accrued (1 month):   GBP {income:,.2f}  "
          f"(coupons {coupon_income:,.0f} + dividends {equity_income:,.0f})")
    print(f"  premium received:           GBP {PREMIUM_INFLOW:,.2f}")
    print(f"  cash in total:              GBP {inflow_exact:,.2f}")
    print("  deployed:")
    print(f"    PC slice (+15.0% sleeve): GBP {pc_slice_mv:,.2f}")
    print(f"    {DEPLOY_GILT_ID} gilt top-up:       GBP {gilt_mv:,.2f}")
    print(f"    {DEPLOY_CORP_ID} corp top-up:       GBP {corp_add_mv:,.2f}")
    print(f"    {TRIM_EQUITY_ID} equity trim:       GBP {-trim_mv:,.2f}")
    print(f"    left in GBP cash:         GBP {cash_delta:,.2f}")
    print(f"  positions changed:          8 of 50 "
          "(4 PC, 1 gilt, 1 corp, 1 equity, 1 cash) — the rest are held "
          "and repriced")
    print(f"  PC share of inflow:         {pc_slice_mv/inflow_exact*100:.1f}%  vs "
          f"pro-rata share {pc_share_prorata*100:.1f}% "
          f"(GBP {pc_prorata_gbp/1e6:.2f}m) -> "
          f"{pc_slice_mv/pc_prorata_gbp:.1f}x pro-rata")
    print("Liability cohorts (flat 4%, USD at GBPUSD_REF):")
    for c, pv, dur in liab_stats:
        print(f"  {c['id']:<11} {c['class']:<9} {c['currency']}  "
              f"PV GBP {pv/1e6:7.1f}m  mod dur {dur:.3f}")
    print(f"Liability PV:             GBP {liab_pv/1e6:,.1f}m  (flat 4%, target 810m)")
    print(f"Liability mod duration:   {liab_dur:.3f}  (flat 4%, target ~4.0, band 3.6-4.4)")
    print(f"  class split:            property {prop_pv/liab_pv*100:.1f}% / "
          f"casualty {(1-prop_pv/liab_pv)*100:.1f}%  (target 42/58)")
    print(f"  currency split:         GBP {gbp_pv/liab_pv*100:.1f}% / "
          f"USD {(1-gbp_pv/liab_pv)*100:.1f}%  (target 55/45)")
    print(f"  PV at real curves:      {PC_BASE_MONTH} GBP {liab_pv_feb/1e6:,.1f}m   "
          f"{PAIR_CURR_MONTH} GBP {liab_pv_mar/1e6:,.1f}m")
    print(f"March liabilities:        flat-4% PV GBP {liab_mar_pv/1e6:,.1f}m "
          f"(+{(liab_mar_pv/liab_pv-1)*100:.2f}%), mod dur {liab_mar_dur:.3f}; "
          f"real {PAIR_CURR_MONTH} PV GBP {liab_pv_mar_grown/1e6:,.1f}m")
    print(f"Surplus (sizing conv):    GBP {(total_mv-liab_pv)/1e6:,.1f}m")

    # ---- assertions (tolerances from the build contract) ----
    assert len(positions) == 50, "must be exactly 50 positions"
    assert len({p["id"] for p in positions}) == 50, "duplicate position ids"
    assert sum(1 for p in positions if p["type"] == "govt_bond") == 12
    assert sum(1 for p in positions if p["type"] == "corp_bond") - n_pc == 16
    assert n_pc == 4
    assert sum(1 for p in positions if p["type"] == "equity") == 13
    assert sum(1 for p in positions if p["type"] == "cash") == 5
    assert 4.5 <= fi_dur <= 5.5, f"FI duration {fi_dur:.3f} outside 4.5-5.5"
    assert abs(total_mv - ASSET_TOTAL_TARGET) <= 0.10 * ASSET_TOTAL_TARGET, \
        f"total MV {total_mv:,.0f} not within 10% of 1.0bn"
    assert abs(pc_nav - PC_NAV_TARGET) <= 0.02 * PC_NAV_TARGET, \
        f"PC NAV {pc_nav:,.0f} not within 2% of 60m"

    # ---- liability assertions (SPEC sections 3 and 7) ----
    for c, pv, dur in liab_stats:
        if c["class"] == "property":
            assert 2.0 <= dur <= 2.5, f"{c['id']} duration {dur:.3f} outside 2.0-2.5"
        else:
            assert 5.9 <= dur <= 7.0, f"{c['id']} duration {dur:.3f} outside 5.9-7.0"
    assert 3.6 <= liab_dur <= 4.4, \
        f"overall liability duration {liab_dur:.3f} outside 3.6-4.4"
    assert abs(liab_pv - LIAB_PV_TARGET) <= 0.01 * LIAB_PV_TARGET, \
        f"liability PV {liab_pv:,.0f} not within 1% of 810m (flat 4%)"
    assert abs(gbp_pv / liab_pv - LIAB_GBP_SHARE) < 0.005, \
        f"GBP share {gbp_pv/liab_pv:.4f} not ~{LIAB_GBP_SHARE}"
    assert abs(prop_pv / liab_pv - LIAB_PROP_SHARE) < 0.005, \
        f"property share {prop_pv/liab_pv:.4f} not ~{LIAB_PROP_SHARE}"
    for label, pv_real in ((PC_BASE_MONTH, liab_pv_feb),
                           (PAIR_CURR_MONTH, liab_pv_mar)):
        assert abs(pv_real - LIAB_PV_TARGET) <= 0.05 * LIAB_PV_TARGET, \
            f"real-curve liability PV at {label} {pv_real:,.0f} not within 5% of 810m"
    assert abs(liab_mar_pv / liab_pv - LIAB_GROWTH) < 0.001, \
        f"March liability growth {liab_mar_pv/liab_pv:.5f} not ~{LIAB_GROWTH}"
    assert abs(liab_mar_dur - liab_dur) < 0.01, \
        "March liability duration drifted (mix must be held constant)"
    for c in cohorts + cohorts_mar:
        assert [cf["t"] for cf in c["cashflows"]] == \
            list(range(1, (7 if c["class"] == "property" else 16))), \
            f"{c['id']} cashflow years wrong"
        assert all(cf["amount"] > 0 for cf in c["cashflows"])

    # ---- March book assertions (growth + reallocation, SPEC section 7) ----
    assert abs(inflow_exact - PREMIUM_INFLOW) <= 200_000, \
        f"premium inflow {inflow_exact:,.0f} not within 200k of 25m"
    assert pc_slice_mv > 3.0 * pc_prorata_gbp, \
        "PC must take a disproportionate share of the inflow"
    pub_corp = [p for p in positions if p["type"] == "corp_bond"
                and p.get("asset_class") != "private_credit"]
    assert {p["rating"] for p in pub_corp} == {"AA", "A", "BBB", "HY"}, \
        "public corp rating buckets wrong"
    for rating in ("AA", "A", "BBB", "HY"):
        ccys = {p["currency"] for p in pub_corp if p["rating"] == rating}
        assert ccys == {"GBP", "USD"}, f"rating {rating} not in both currencies: {ccys}"
    ford = next(p for p in positions if p["id"] == "P028")
    assert ford["isin"] == "US345397C353" and ford["rating"] == "HY", \
        "P028 must remain Ford US345397C353 rated HY (D2 target)"
    for p in positions:
        if p.get("asset_class") == "private_credit":
            assert p["id"].startswith("PCF-") and "isin" not in p
            assert p["rating"] == PC_RATING and p["maturity_years"] == PC_MATURITY
            assert p["strategy"] in {s for _, _, _, s, _ in PC_FUNDS}
            # near par at the 2026-02 base curves (coupon rounding only)
            pr = corp_price_real(assump_feb, p["curve"], p["rating"],
                                 p["coupon"], p["maturity_years"])
            assert abs(pr - 1.0) < 0.005, f"{p['id']} not near par: {pr}"
        elif "isin" in p:
            finish_isin(p["isin"])  # re-assert checksum validity

    # March book: same 50 ids; EVERY position grew (assets grow, nothing is
    # sold); PCF notionals exactly +15.0%.
    assert len(positions_mar) == 50
    assert [p["id"] for p in positions_mar] == [p["id"] for p in positions]
    by_id = {p["id"]: p for p in positions}
    for p in positions_mar:
        base = by_id[p["id"]]
        assert position_units(p) > position_units(base), \
            f"{p['id']} did not grow in the March book"
        # only the size field differs -- statics are untouched
        size_key = ("notional" if p["type"] in ("govt_bond", "corp_bond")
                    else "quantity" if p["type"] == "equity" else "amount")
        assert {k: v for k, v in p.items() if k != size_key} == \
            {k: v for k, v in base.items() if k != size_key}, \
            f"{p['id']} non-size fields changed"
    for p in pcf_mar:
        assert p["notional"] == round(old_notionals[p["id"]] * PC_SCALE)

    header = {"ref_asof": REF_ASOF, "ref_index_levels": REF_INDEX_LEVELS}
    (BOOK_DIR / "positions.json").write_text(
        json.dumps({**header, "positions": positions}, indent=2) + "\n")
    (BOOK_DIR / "positions_2026-03.json").write_text(
        json.dumps({**header, "positions": positions_mar}, indent=2) + "\n")
    (BOOK_DIR / "liabilities.json").write_text(
        json.dumps({"cohorts": cohorts}, indent=2) + "\n")
    (BOOK_DIR / "liabilities_2026-03.json").write_text(
        json.dumps({"cohorts": cohorts_mar}, indent=2) + "\n")
    print(f"\nWrote {BOOK_DIR / 'positions.json'}")
    print(f"Wrote {BOOK_DIR / 'positions_2026-03.json'}")
    print(f"Wrote {BOOK_DIR / 'liabilities.json'}")
    print(f"Wrote {BOOK_DIR / 'liabilities_2026-03.json'}")


if __name__ == "__main__":
    main()
