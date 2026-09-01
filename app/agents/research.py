"""Research notes — the research STAGE (SPEC-APP 5.1, PENDING-BATCH2 s2).

Research is a first-class stage and it runs FIRST in a cycle:
research -> room 1 -> room 2 -> room 3. Two agents produce it, one report
each, and their room posts are written afterwards against those reports.

  - `@focused` — the STANDING SET of focused risks, the same seven in the
    same order every month so months are comparable: interest rates,
    inflation, credit spreads, defaults and distress, employment, GBP/USD,
    equities. Per risk: what it did this month, where it sits against
    history, and what drove it.
  - `@wide-eye` — the wider risks, market level, deliberately NOT our
    factor set: private credit, commercial real estate, banking stress,
    fixed income conditions, equity fears, policy, geopolitics, reinsurance,
    litigation, cyber, climate, regulation, AI.

Both notes are computed DIRECTLY from data/processed/*.csv — never from
assumptions or engine outputs. That independence is the point: research and
calibration consume the same source data through separate paths, so an
assumptions-vs-research mismatch is evidence of an error between them.

MOCK vs LIVE. Cause and forward context need `web_search`, which exists
only in live mode. In mock, each note says so AT THE TOP and then covers
exactly what our own series support — @focused computes the real
month-on-month moves and their percentiles; @wide-eye names, theme by
theme, what it cannot research and then computes the market-level reads our
series DO carry (risk appetite, fixed-income conditions, equity stress).
Nothing is stubbed and nothing is invented. In live mode the caller passes
`web_context` (searched prose, per risk, with its sources) and it is
rendered into the same skeleton, marked as model-written from web sources.

Per factor block and column (the deterministic backbone of both notes):
  - month-end level (last business day <= month-end) and month change,
  - intra-month high/low with dates,
  - the percentile of the month's move vs rolling same-length moves over
    the trailing ~2 years (TRAILING_DAYS observations),
  - realized daily vol this month vs the trailing window (what the month
    does to the calibrated vols as it enters the window),
  - notable observations from fixed thresholds (deterministic).

Derived, cross-series reads (`stats["derived"]`), because a level table is
not research: curve SHAPE (2s10s/2s20s per curve), the GILT/SWAP BASIS at
every tenor (assets on gilts, GBP liabilities on swap — the unhedged rate
exposure), credit QUALITY spreads (HY-BBB, CCC-HY: the distress signal we
have in place of a default series), and equity DRAWDOWN and dispersion.

`generate_note(asof, agent="focused")` recomputes everything and (re)writes
outputs/research/<YYYY_MM>_<agent>.md atomically (the month is underscored
to match the run directories, PENDING-BATCH2 section 1) — regenerated on every
call, which is the "regenerated on refresh" behaviour SPEC-APP 5.1 asks
for; the computation is deterministic, so the file is byte-stable for a
given data set. Exposed to agents via the `read_research` tool and to the
UI via `GET /api/research?asof=&agent=` (read-only).

Fresh snapshots (SPEC-APP E) advance the market-data window past month-end
without moving the frozen valuation: pass `data_through` to compute what
has happened between the month-end close and that later date instead of
between two month-ends. Snapshot notes are NOT persisted to the canonical
`<YYYY_MM>_<agent>.md` file (that file is always the settled month-end note);
they are computed and returned in memory only.

This module imports nothing from the rest of the app — it must never read
`assumptions/`, `book/`, `scenarios/` or any engine output.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data" / "processed"
# Overridable so the test suite cannot write mock notes over the real ones
# (tests/conftest.py points it at a temp dir).
_RESEARCH_DIR_ENV = os.environ.get("APP_RESEARCH_DIR")
RESEARCH_DIR = (Path(_RESEARCH_DIR_ENV) if _RESEARCH_DIR_ENV
                else PROJECT_ROOT / "outputs" / "research")

AGENTS = ("focused", "wide-eye")  # the research stage, in run order


def note_filename(month: str, agent: str) -> str:
    """`2026_03_focused.md` — the research note for one month and agent.
    The month is underscored so research files sort and read alongside the
    `outputs/<YYYY_MM>/` run directories (PENDING-BATCH2 section 1)."""
    return f"{str(month)[:7].replace('-', '_')}_{agent}.md"


TRAILING_DAYS = 504  # ~2y of daily observations (matches the calibration window)
MIN_SAMPLE = 60      # fewer trailing observations than this -> no percentile

# (block, csv series, columns, kind). kind "rate": percent-quoted levels,
# absolute month changes reported in bp, daily vols in bp/day. kind "level":
# index/FX levels, proportional month changes in percent, daily vols in %/day.
BLOCKS = [
    ("gbp_swap", "gbp_swap", ("t2", "t5", "t10", "t20"), "rate"),
    ("gbp_gilt", "gbp_gilt", ("t2", "t5", "t10", "t20"), "rate"),
    ("ust", "ust", ("t2", "t5", "t10", "t20"), "rate"),
    ("spread", "credit_oas", ("AA", "A", "BBB", "HY", "CCC"), "rate"),
    ("equity", "equity", ("FTSE100", "SP500", "SX5E"), "level"),
    ("fx", "fx", ("GBPUSD",), "level"),
]

BLOCK_TITLES = {
    "gbp_swap": "GBP swap (OIS) zero curve",
    "gbp_gilt": "GBP gilt zero curve",
    "ust": "UST curve",
    "spread": "Credit OAS by rating",
    "equity": "Equity indices",
    "fx": "FX",
}

CURVE_BLOCKS = ("gbp_swap", "gbp_gilt", "ust")
TENORS = ("t2", "t5", "t10", "t20")

# Fixed notable-observation thresholds (deterministic; stated, not hidden).
PCTL_HI, PCTL_LO = 90, 10       # top / bottom decile of trailing 2y moves
VOL_HOT, VOL_COLD = 1.25, 0.80  # month daily vol vs trailing daily vol
BIG_RATE_BP = 25.0              # |month change|, bp
BIG_LEVEL_PC = 5.0              # |month change|, percent

_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")


# --------------------------------------------------------------------------
# the standing risk set — the SAME seven, in the same order, every month, so
# that March is readable against February without re-learning the layout
# (PENDING-BATCH2 s2). `coverage` is honest about which of them our own
# series can actually speak to.
# --------------------------------------------------------------------------

FOCUSED_RISKS: list[dict] = [
    {
        "key": "rates", "title": "Interest rates and the curve",
        "coverage": "full",
        "series": ["gbp_swap.csv", "gbp_gilt.csv", "ust.csv"],
        "in_model": ("yes — twelve of the twenty-one factors: three curves "
                     "at four tenors each"),
    },
    {
        "key": "inflation", "title": "Inflation",
        "coverage": "none", "series": [],
        "in_model": ("no — the curves are nominal and the reserves are fixed "
                     "claims-payment cashflows, so neither price inflation "
                     "nor claims inflation has any channel into this model"),
    },
    {
        "key": "credit", "title": "Credit spreads",
        "coverage": "full", "series": ["credit_oas.csv"],
        "in_model": "yes — five spread factors (AA, A, BBB, HY, CCC)",
    },
    {
        "key": "defaults", "title": "Defaults and distress",
        "coverage": "proxy", "series": ["credit_oas.csv"],
        "in_model": ("no — spreads move continuously, but there is no "
                     "default, no ratings migration and no recovery "
                     "assumption in the factor set"),
    },
    {
        "key": "employment", "title": "Employment and the labour market",
        "coverage": "none", "series": [],
        "in_model": "no — no macro factor of any kind is modelled",
    },
    {
        "key": "fx", "title": "GBP/USD",
        "coverage": "full", "series": ["fx.csv"],
        "in_model": ("yes — one factor; it also translates the USD "
                     "liability cohorts, so it is a two-sided exposure"),
    },
    {
        "key": "equities", "title": "Equities",
        "coverage": "full", "series": ["equity.csv"],
        "in_model": ("yes — three index factors; the equity book is "
                     "index-proxied, so single-name risk is invisible"),
    },
]

# @wide-eye's standing menu (PENDING-BATCH2 s2). Not our factor set — the
# world around it. Each carries what live mode would research and, crucially,
# whether the risk has ANY channel into the model. "This is material and we
# cannot currently price it" is the most useful sentence in the note and it
# feeds @red-team's standing limitations.
WIDER_RISKS: list[dict] = [
    {
        "key": "private_credit", "group": "Markets and credit",
        "title": "Private credit",
        "live": ("fundraising and dry powder, manager marks, dispersion "
                 "between marks and traded comparables, and the "
                 "valuation-lag question"),
        "channel": ("PARTIAL. The sleeve is modelled as a synthetic "
                    "fixed-rate bond on our own curves and spread levels. "
                    "Fund-level marks, NAV smoothing and valuation lag have "
                    "no channel at all, and the fixed-rate proxy adds "
                    "govy duration a floating-rate loan book does not have."),
    },
    {
        "key": "cre", "group": "Markets and credit",
        "title": "Commercial real estate",
        "live": ("office and retail valuations, refinancing walls, regional "
                 "bank exposure, transaction volumes"),
        "channel": ("INDIRECT ONLY. CRE stress reaches us through BBB and HY "
                    "spreads if it reaches us at all; there is no property "
                    "factor and no sector split inside a rating bucket."),
    },
    {
        "key": "banking", "group": "Markets and credit",
        "title": "Banking sector stress",
        "live": ("deposit flows, funding costs, unrealised securities "
                 "losses, regulator interventions"),
        "channel": ("INDIRECT ONLY, and fast. 2023 showed the transmission "
                    "runs in days; our spread factors would register it only "
                    "after the fact, and our calibration window would take "
                    "months to reflect it."),
    },
    {
        "key": "fi_conditions", "group": "Markets and credit",
        "title": "Fixed income conditions",
        "live": "issuance calendars, dealer balance sheets, duration demand",
        "channel": ("PARTIAL. Curve level and shape are modelled; liquidity, "
                    "issuance and market functioning are not. See the "
                    "computed section below — the gilt/swap basis is the one "
                    "market-functioning read our own data carries."),
    },
    {
        "key": "equity_fears", "group": "Markets and credit",
        "title": "Equity market fears",
        "live": ("index concentration, positioning, valuation dispersion, "
                 "implied volatility"),
        "channel": ("PARTIAL. Index levels and a vol calibrated from 504 "
                    "days of history are modelled; concentration, "
                    "positioning and implied vol are not. Realised vol is "
                    "computed below as the only vol read we own."),
    },
    {
        "key": "us_policy", "group": "Policy and politics",
        "title": "US administration policy, trade and tariffs",
        "live": ("tariff announcements and exemptions, fiscal measures, "
                 "the appointments and guidance that move the front end"),
        "channel": ("NONE DIRECTLY. It arrives, if at all, already priced "
                    "into the UST curve, the dollar and credit spreads — "
                    "which means we see the consequence and never the cause."),
    },
    {
        "key": "uk_policy", "group": "Policy and politics",
        "title": "UK fiscal policy and gilt market functioning",
        "live": ("fiscal events, remit and issuance changes, LDI and "
                 "pension-fund demand, auction tails"),
        "channel": ("PARTIAL, AND THE GAP IS THE POINT. Gilt levels are "
                    "modelled. A 2022-style dislocation is not: it sits "
                    "outside the trailing window, so it is precisely the "
                    "tail this calibration cannot see."),
    },
    {
        "key": "geopolitics", "group": "Policy and politics",
        "title": "Geopolitics and conflict",
        "live": "conflicts, sanctions, energy and shipping disruption",
        "channel": "NONE. No commodity, energy or event factor exists.",
    },
    {
        "key": "reinsurance", "group": "Insurance-specific",
        "title": "Reinsurance and retrocession",
        "live": ("renewal pricing, capacity, retention levels, cat bond "
                 "spreads and issuance"),
        "channel": ("NONE — and we are a specialty insurer. Reinsurance "
                    "pricing is a first-order driver of this firm's economics "
                    "and it is completely absent from the factor set. This is "
                    "material and we cannot currently price it."),
    },
    {
        "key": "litigation", "group": "Insurance-specific",
        "title": "Litigation environment and social inflation",
        "live": ("US tort verdict trends, litigation funding, class action "
                 "activity, jurisdictional shifts"),
        "channel": ("NONE. The reserves are deterministic cashflows, so the "
                    "dominant long-tail casualty risk cannot move a single "
                    "number in this model. Material and unpriceable here."),
    },
    {
        "key": "cyber", "group": "Insurance-specific", "title": "Cyber",
        "live": ("major incidents, accumulation and aggregation concerns, "
                 "wording and exclusion developments"),
        "channel": ("NONE, twice over: not as an insured peril (no cat or "
                    "large-loss model) and not as an operational risk to "
                    "this firm."),
    },
    {
        "key": "climate", "group": "Structural",
        "title": "Climate — physical and transition",
        "live": ("catastrophe frequency and severity, transition policy, "
                 "stranded-asset repricing, disclosure regimes"),
        "channel": ("NONE. Physical risk needs a cat model we do not have; "
                    "transition risk needs a sector view our index proxies "
                    "cannot express."),
    },
    {
        "key": "regulation", "group": "Structural",
        "title": "Regulatory change",
        "live": ("Solvency UK, IFRS 17, PRA and BMA developments, and the "
                 "EU AI Act — which a system like this one would itself fall "
                 "under"),
        "channel": ("NONE. Regulatory change moves capital requirements and "
                    "reporting, neither of which this framework computes."),
    },
    {
        "key": "ai", "group": "Structural",
        "title": "AI disruption — to insureds and to the profession",
        "live": ("adoption in underwriting and claims, model-risk "
                 "expectations, exposure changes in insured industries"),
        "channel": ("NONE, and the exposure is two-sided: it changes what we "
                    "insure and how work like this is done."),
    },
]


def ordinal(n: int) -> str:
    """1 -> '1st', 2 -> '2nd', 11 -> '11th', 97 -> '97th'."""
    n = int(n)
    if 10 <= n % 100 <= 20:
        suf = "th"
    else:
        suf = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


def _load(series: str) -> pd.DataFrame:
    p = DATA_DIR / f"{series}.csv"
    if not p.exists():
        raise FileNotFoundError(f"processed data series not found: {p.name}")
    df = pd.read_csv(p, parse_dates=["date"], index_col="date")
    return df.sort_index()


def _column_stats(s: pd.Series, prev_end: pd.Timestamp,
                  curr_end: pd.Timestamp, kind: str) -> dict:
    s = s.dropna()
    upto = s.loc[:curr_end]
    before = s.loc[:prev_end]
    month = s[(s.index > prev_end) & (s.index <= curr_end)]
    k = len(month)
    level = float(upto.iloc[-1])
    prev_level = float(before.iloc[-1])

    high = float(month.max())
    low = float(month.min())
    high_date = str(month.idxmax().date())
    low_date = str(month.idxmin().date())

    # month move percentile vs rolling k-day moves over the trailing window
    if kind == "rate":
        rolls = upto.diff(k).dropna()
    else:
        rolls = (upto / upto.shift(k) - 1.0).dropna()
    sample = rolls.tail(TRAILING_DAYS)
    pctl = None
    if len(sample) >= MIN_SAMPLE:
        curr_move = float(rolls.iloc[-1])
        pctl = int(round(100.0 * float((sample <= curr_move).mean())))

    # realized daily vol: this month's daily changes vs the trailing window
    if kind == "rate":
        daily = upto.diff().dropna() * 100.0        # percent points -> bp
    else:
        daily = upto.pct_change().dropna() * 100.0  # proportional -> percent
    month_daily = daily.tail(k)
    trail_daily = daily.tail(TRAILING_DAYS)
    mvol = (round(float(month_daily.std(ddof=1)), 2)
            if len(month_daily) >= 2 else None)
    tvol = (round(float(trail_daily.std(ddof=1)), 2)
            if len(trail_daily) >= MIN_SAMPLE else None)
    pressure = "n/a"
    if mvol is not None and tvol not in (None, 0.0):
        ratio = mvol / tvol
        pressure = ("up" if ratio >= VOL_HOT
                    else "down" if ratio <= VOL_COLD else "in line")

    trailing_levels = upto.tail(TRAILING_DAYS)
    at_high = level >= float(trailing_levels.max())
    at_low = level <= float(trailing_levels.min())
    lo_2y = round(float(trailing_levels.min()), 4)
    hi_2y = round(float(trailing_levels.max()), 4)
    level_pctl = int(round(100.0 * float((trailing_levels <= level).mean())))

    if kind == "rate":
        change = round((level - prev_level) * 100.0, 1)  # bp
        stat = {
            "level_pct": round(level, 4),
            "change_bp": change,
            "low_pct": round(low, 4), "low_date": low_date,
            "high_pct": round(high, 4), "high_date": high_date,
            "move_percentile": pctl,
            "month_dayvol_bp": mvol, "trail_dayvol_bp": tvol,
            "vol_pressure": pressure,
        }
        big_move = abs(change) >= BIG_RATE_BP
        chg_txt = f"{change:+.1f}bp"
        vol_unit = "bp/day"
    else:
        change = round((level / prev_level - 1.0) * 100.0, 2)  # percent
        stat = {
            "level": round(level, 4),
            "change_pct": change,
            "low": round(low, 4), "low_date": low_date,
            "high": round(high, 4), "high_date": high_date,
            "move_percentile": pctl,
            "month_dayvol_pct": mvol, "trail_dayvol_pct": tvol,
            "vol_pressure": pressure,
        }
        big_move = abs(change) >= BIG_LEVEL_PC
        chg_txt = f"{change:+.2f}%"
        vol_unit = "%/day"

    # where the LEVEL sits against the trailing window (the "vs history"
    # half of the standing question — the move percentile answers the other)
    stat["trailing_low"] = lo_2y
    stat["trailing_high"] = hi_2y
    stat["level_percentile"] = level_pctl

    notable: list[str] = []
    if pctl is not None and pctl >= PCTL_HI:
        notable.append(f"month move in the top decile of the trailing two "
                       f"years ({ordinal(pctl)} percentile)")
    elif pctl is not None and pctl <= PCTL_LO:
        notable.append(f"month move in the bottom decile of the trailing "
                       f"two years ({ordinal(pctl)} percentile)")
    if at_high:
        notable.append("month-end level is a two-year high")
    elif at_low:
        notable.append("month-end level is a two-year low")
    if big_move:
        notable.append(f"outsized monthly move ({chg_txt})")
    if pressure == "up":
        notable.append(f"realized daily vol ran hot vs the trailing window "
                       f"({mvol:.2f} vs {tvol:.2f} {vol_unit}) — entering "
                       f"the calibration window it pulls the calibrated "
                       f"vol up")
    elif pressure == "down":
        notable.append(f"realized daily vol ran cold vs the trailing window "
                       f"({mvol:.2f} vs {tvol:.2f} {vol_unit}) — entering "
                       f"the calibration window it drags the calibrated "
                       f"vol down")
    stat["notable"] = notable
    return stat


# --------------------------------------------------------------------------
# derived, cross-series reads — a level table is not research
# --------------------------------------------------------------------------

def _derived_stats(s: pd.Series, prev_end: pd.Timestamp,
                   curr_end: pd.Timestamp) -> dict:
    """One derived spread/slope series, quoted in percent, reported in bp:
    level, month change, and where the LEVEL sits in the trailing window."""
    s = s.dropna()
    upto = s.loc[:curr_end]
    before = s.loc[:prev_end]
    if len(upto) == 0 or len(before) == 0:
        return {}
    level = float(upto.iloc[-1])
    prev_level = float(before.iloc[-1])
    trailing = upto.tail(TRAILING_DAYS)
    return {
        "level_bp": round(level * 100.0, 1),
        "prev_bp": round(prev_level * 100.0, 1),
        "change_bp": round((level - prev_level) * 100.0, 1),
        "trailing_low_bp": round(float(trailing.min()) * 100.0, 1),
        "trailing_high_bp": round(float(trailing.max()) * 100.0, 1),
        "level_percentile": int(round(
            100.0 * float((trailing <= level).mean()))),
    }


def _aligned(a: pd.Series, b: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Two series on their common dates — a basis computed off mismatched
    calendars would be an artefact of the calendar, not of the market."""
    idx = a.dropna().index.intersection(b.dropna().index)
    return a.loc[idx], b.loc[idx]


def _compute_derived(frames: dict, prev_end: pd.Timestamp,
                     curr_end: pd.Timestamp) -> dict:
    """Curve shape, gilt/swap basis, credit quality spreads, equity
    drawdown and dispersion. All from the same source frames, all
    deterministic."""
    out: dict = {"curve_shape": {}, "basis": {}, "quality": {}, "equity": {}}

    for curve in CURVE_BLOCKS:
        df = frames[curve]
        out["curve_shape"][curve] = {
            "s2s10": _derived_stats(df["t10"] - df["t2"], prev_end, curr_end),
            "s2s20": _derived_stats(df["t20"] - df["t2"], prev_end, curr_end),
        }

    gilt, swap = frames["gbp_gilt"], frames["gbp_swap"]
    for t in TENORS:
        g, w = _aligned(gilt[t], swap[t])
        out["basis"][t] = _derived_stats(g - w, prev_end, curr_end)

    oas = frames["spread"]
    for name, (hi, lo) in (("HY_BBB", ("HY", "BBB")),
                           ("CCC_HY", ("CCC", "HY")),
                           ("BBB_A", ("BBB", "A"))):
        out["quality"][name] = _derived_stats(oas[hi] - oas[lo],
                                              prev_end, curr_end)

    eq = frames["equity"]
    for col in ("FTSE100", "SP500", "SX5E"):
        s = eq[col].dropna().loc[:curr_end]
        trailing = s.tail(TRAILING_DAYS)
        level, peak = float(s.iloc[-1]), float(trailing.max())
        out["equity"][col] = {
            "drawdown_pct": round(100.0 * (level / peak - 1.0), 2),
            "trailing_peak": round(peak, 4),
        }
    return out


def _dispersion_pp(stats: dict) -> float:
    """Best minus worst index month move, in percentage points — cheap,
    real, and the one cross-sectional read three indices can support."""
    moves = [float(stats["equity"]["columns"][c]["change_pct"])
             for c in ("FTSE100", "SP500", "SX5E")]
    return round(max(moves) - min(moves), 2)


def _leader(stats: dict, block: str, cols) -> tuple[str, float]:
    """Which column moved most, and by how much (bp for rate blocks)."""
    key = "change_bp" if "change_bp" in stats[block]["columns"][cols[0]] \
        else "change_pct"
    best = max(cols, key=lambda c: abs(float(stats[block]["columns"][c][key])))
    return best, float(stats[block]["columns"][best][key])


# --------------------------------------------------------------------------
# live-mode web context — untrusted prose, rendered as prose, never as data
# --------------------------------------------------------------------------

_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
MAX_WEB_SECTION_CHARS = 4000


def clean_web_text(text) -> str:
    """Web-derived prose is UNTRUSTED (it reaches us from pages a model
    read). It is rendered into a markdown document, so: strip control
    characters, neutralise raw HTML and fence breakers, and cap the length.
    It is never parsed for numbers and never becomes a claim."""
    s = str(text or "")
    s = _CTRL_RE.sub("", s)
    s = s.replace("<", "&lt;").replace(">", "&gt;").replace("```", "'''")
    s = s.strip()
    if len(s) > MAX_WEB_SECTION_CHARS:
        s = s[:MAX_WEB_SECTION_CHARS].rstrip() + " …[truncated]"
    return s


def _web_sections(web_context) -> dict:
    if not isinstance(web_context, dict):
        return {}
    raw = web_context.get("risks") or web_context.get("sections") or {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): clean_web_text(v) for k, v in raw.items()
            if str(v or "").strip()}


def _web_sources(web_context) -> list[dict]:
    if not isinstance(web_context, dict):
        return []
    out = []
    for src in (web_context.get("sources") or [])[:40]:
        if isinstance(src, dict):
            out.append({"title": clean_web_text(src.get("title") or "source"),
                        "url": clean_web_text(src.get("url") or "")})
        else:
            out.append({"title": clean_web_text(src), "url": ""})
    return out


def _mode_banner(agent: str, web: dict | None,
                 unavailable: str | None = None) -> list[str]:
    """The line at the TOP of every note saying what it could and could not
    research.

    There are two states and no third: the web research came back, or it
    did not. There is no "mock" note — a note that could not reach the web
    says so plainly and still carries every computed figure.
    """
    if web:
        return [
            "> **Live mode — web research included.** The quantitative "
            "backbone below is computed from our own processed series; the "
            "*what drove it* prose is model-written from web sources, listed "
            "at the end. Treat the prose as context and the tables as data.",
            "",
        ]
    return [
        "> **Web research did not complete for this note.** "
        + (f"{unavailable} " if unavailable else "")
        + "Everything below is still computed in full from our own processed "
        "series (`data/processed/*.csv`); what is missing is the *why* — "
        "cause and forward context — which is left out rather than guessed.",
        "",
    ]


# --------------------------------------------------------------------------
# rendering — @focused: the standing set, one section per risk, every month
# --------------------------------------------------------------------------

def _rate_table(lines: list[str], stats: dict, block: str, cols,
                label: str = "point") -> None:
    lines.append(f"| {label} | month-end | Δ month | move %ile (2y) "
                 "| 2y range | day vol (month) | day vol (2y) |")
    lines.append("|---|---|---|---|---|---|---|")
    for c in cols:
        st = stats[block]["columns"][c]
        pct = (ordinal(st["move_percentile"])
               if st["move_percentile"] is not None else "n/a")
        mv = (f"{st['month_dayvol_bp']:.2f}bp"
              if st["month_dayvol_bp"] is not None else "n/a")
        tv = (f"{st['trail_dayvol_bp']:.2f}bp"
              if st["trail_dayvol_bp"] is not None else "n/a")
        lines.append(
            f"| {c} | {st['level_pct']:.4f} | {st['change_bp']:+.1f}bp "
            f"| {pct} | {st['trailing_low']:.4f}–{st['trailing_high']:.4f} "
            f"| {mv} | {tv} |")
    lines.append("")


def _level_table(lines: list[str], stats: dict, block: str, cols) -> None:
    lines.append("| series | month-end | Δ month | move %ile (2y) "
                 "| 2y range | day vol (month) | day vol (2y) |")
    lines.append("|---|---|---|---|---|---|---|")
    for c in cols:
        st = stats[block]["columns"][c]
        pct = (ordinal(st["move_percentile"])
               if st["move_percentile"] is not None else "n/a")
        mv = (f"{st['month_dayvol_pct']:.2f}%"
              if st["month_dayvol_pct"] is not None else "n/a")
        tv = (f"{st['trail_dayvol_pct']:.2f}%"
              if st["trail_dayvol_pct"] is not None else "n/a")
        lines.append(
            f"| {c} | {st['level']:,.4f} | {st['change_pct']:+.2f}% "
            f"| {pct} | {st['trailing_low']:,.4f}–{st['trailing_high']:,.4f} "
            f"| {mv} | {tv} |")
    lines.append("")


# What mock CAN say about cause, per risk: the mechanical read our own
# series support, and the named gap live mode's web search would close.
_MOCK_DRIVERS = {
    "rates": ("Mechanically, our series place the move: which tenor led, "
              "whether the curve steepened or flattened, and whether gilts "
              "moved against swaps (a UK-specific signature) or with them "
              "(a global one). They cannot supply the cause — policy rates "
              "and guidance from the BoE and the Fed, the inflation prints "
              "behind them, and the issuance calendar. Live mode's web "
              "search covers exactly that."),
    "credit": ("Mechanically, our series say whether the widening was led "
               "by low quality (an appetite move) or spread evenly across "
               "buckets (a level move). They cannot say what caused it — "
               "issuance, fund flows, a sector event, dealer liquidity — "
               "nor separate spread widening from ratings migration, which "
               "we do not observe at all."),
    "defaults": ("Our series carry a market-implied price for distress and "
                 "nothing else. Realised default and recovery rates, "
                 "migration counts and the distress ratio are not here, and "
                 "the market's price and the realised rate diverge most "
                 "sharply at exactly the turning points that matter."),
    "fx": ("Mechanically, our series give the move and its place in the "
           "two-year range. The rate differential driving it, the "
           "positioning behind it and the short-end basis (SONIA vs SOFR) "
           "are outside our data."),
    "equities": ("Mechanically, our series give the moves, the dispersion "
                 "between the three indices and realised volatility. What "
                 "drove them — earnings, concentration in a handful of "
                 "names, positioning, the implied vol the options market "
                 "was charging — is not in our data."),
}


def _drivers(lines: list[str], key: str, web: dict) -> None:
    """The *what drove it* half. Live mode fills it from web search; mock
    says what it can from our own series and names the gap, once, plainly."""
    lines.append("**What drove it.**")
    lines.append("")
    lines.append(web.get(key) or _MOCK_DRIVERS[key])
    lines.append("")


def _section_rates(lines: list[str], stats: dict, web: dict) -> None:
    d = stats["derived"]
    lines.append("**What it did this month.**")
    lines.append("")
    for curve in CURVE_BLOCKS:
        lines.append(f"*{BLOCK_TITLES[curve]}* — `{curve}.csv`, percent as "
                     "published.")
        lines.append("")
        _rate_table(lines, stats, curve, TENORS)

    lines.append("**Curve shape.** We model four tenors, so shape is a "
                 "modelled risk and not a footnote.")
    lines.append("")
    lines.append("| curve | 2s10s | Δ month | 2s20s | Δ month | shape |")
    lines.append("|---|---|---|---|---|---|")
    for curve in CURVE_BLOCKS:
        a = d["curve_shape"][curve]["s2s10"]
        b = d["curve_shape"][curve]["s2s20"]
        shape = ("steepened" if a["change_bp"] > 0.5
                 else "flattened" if a["change_bp"] < -0.5
                 else "unchanged")
        lines.append(f"| {curve} | {a['level_bp']:+.1f}bp "
                     f"| {a['change_bp']:+.1f}bp | {b['level_bp']:+.1f}bp "
                     f"| {b['change_bp']:+.1f}bp | {shape} |")
    lines.append("")

    lines.append("**Gilt/swap basis.** The assets sit on the gilt curve and "
                 "the GBP liabilities discount on swap, so this basis is the "
                 "unhedged rate exposure — surplus is near-immune to a "
                 "parallel move and is not immune to this one. It gets its "
                 "own line every month.")
    lines.append("")
    lines.append("| tenor | gilt − swap | Δ month | 2y range | level %ile |")
    lines.append("|---|---|---|---|---|")
    for t in TENORS:
        b = d["basis"][t]
        lines.append(f"| {t} | {b['level_bp']:+.1f}bp "
                     f"| {b['change_bp']:+.1f}bp "
                     f"| {b['trailing_low_bp']:+.1f}–"
                     f"{b['trailing_high_bp']:+.1f}bp "
                     f"| {ordinal(b['level_percentile'])} |")
    lines.append("")

    lines.append("**Where it sits against history.**")
    lines.append("")
    for curve in CURVE_BLOCKS:
        led, amount = _leader(stats, curve, TENORS)
        st = stats[curve]["columns"][led]
        pctl = st["move_percentile"]
        lines.append(
            f"- `{curve}`: led by {led} at {amount:+.1f}bp"
            + (f", a move in the {ordinal(pctl)} percentile of the trailing "
               f"two years" if pctl is not None else "")
            + f"; the level sits in the {ordinal(st['level_percentile'])} "
              f"percentile of its own two-year range.")
    lines.append("")
    _drivers(lines, "rates", web)


def _section_absent(lines: list[str], risk: dict, web: dict,
                    extra: str) -> None:
    """A standing risk with no series behind it. The section still appears
    every month — a silently missing section is indistinguishable from a
    quiet month, which is exactly the confusion the standing set exists to
    prevent."""
    lines.append("**Not in our data.** No series in `data/processed/` "
                 "carries this risk, so there is nothing here to compute "
                 "and this note will not pretend otherwise.")
    lines.append("")
    in_model = risk["in_model"]
    lines.append(f"**In the model?** {in_model[:1].upper()}{in_model[1:]}.")
    lines.append("")
    lines.append(extra)
    lines.append("")
    if web.get(risk["key"]):
        lines.append("**What live research found.**")
        lines.append("")
        lines.append(web[risk["key"]])
        lines.append("")
    else:
        lines.append("**What live mode would research.** " + risk["live_ask"])
        lines.append("")


def _section_credit(lines: list[str], stats: dict, web: dict) -> None:
    d = stats["derived"]
    cols = ("AA", "A", "BBB", "HY", "CCC")
    lines.append("**What it did this month.** OAS by rating bucket — "
                 "`credit_oas.csv`, percent as published.")
    lines.append("")
    _rate_table(lines, stats, "spread", cols, label="rating")

    lines.append("**Where it sits against history.** Quality spreads — the "
                 "shape of the credit curve, and a cleaner risk-appetite "
                 "read than any single bucket.")
    lines.append("")
    lines.append("| quality spread | level | Δ month | 2y range | level %ile |")
    lines.append("|---|---|---|---|---|")
    for name, label in (("BBB_A", "BBB − A"), ("HY_BBB", "HY − BBB"),
                        ("CCC_HY", "CCC − HY")):
        q = d["quality"][name]
        lines.append(f"| {label} | {q['level_bp']:+.1f}bp "
                     f"| {q['change_bp']:+.1f}bp "
                     f"| {q['trailing_low_bp']:+.1f}–"
                     f"{q['trailing_high_bp']:+.1f}bp "
                     f"| {ordinal(q['level_percentile'])} |")
    lines.append("")
    led, amount = _leader(stats, "spread", cols)
    hy_bbb = d["quality"]["HY_BBB"]["change_bp"]
    lines.append(
        f"- Largest bucket move: {led} at {amount:+.1f}bp. HY − BBB "
        f"{hy_bbb:+.1f}bp on the month — "
        + ("low quality underperformed, which is a risk-appetite move rather "
           "than a rates-driven repricing." if hy_bbb > 0.5 else
           "low quality outperformed, so the widening is not appetite-driven."
           if hy_bbb < -0.5 else
           "the curve moved in parallel across quality, which points at a "
           "level move rather than a change in appetite."))
    lines.append("")
    _drivers(lines, "credit", web)


def _section_defaults(lines: list[str], stats: dict, web: dict) -> None:
    d = stats["derived"]
    ccc = stats["spread"]["columns"]["CCC"]
    q = d["quality"]["CCC_HY"]
    lines.append("**Proxy only, and named as one.** We hold no default-rate, "
                 "distress-ratio or migration series. What we do hold is the "
                 "CCC bucket and its spread over HY — the market's own price "
                 "for imminent default risk. It is a market-implied proxy, "
                 "not a realised default rate, and the two diverge exactly "
                 "when it matters most.")
    lines.append("")
    lines.append(f"- CCC OAS {ccc['level_pct']:.4f}% "
                 f"({ccc['change_bp']:+.1f}bp on the month), level in the "
                 f"{ordinal(ccc['level_percentile'])} percentile of the "
                 f"trailing two years.")
    lines.append(f"- CCC − HY {q['level_bp']:+.1f}bp "
                 f"({q['change_bp']:+.1f}bp), level in the "
                 f"{ordinal(q['level_percentile'])} percentile — "
                 + ("dispersion widened: the market is discriminating within "
                    "high yield, which is the usual early signature of a "
                    "default cycle." if q["change_bp"] > 0.5 else
                    "dispersion narrowed: distress is being priced out, not "
                    "in." if q["change_bp"] < -0.5 else
                    "dispersion is flat — no discrimination signal either "
                    "way."))
    lines.append("")
    lines.append("**In the model?** No. Spreads move continuously; there is "
                 "no default event, no ratings migration and no recovery "
                 "assumption anywhere in the factor set. A downgrade would "
                 "hit us through bucket mapping in the position file long "
                 "before a default did, and neither is modelled. This is "
                 "material and we cannot currently price it.")
    lines.append("")
    _drivers(lines, "defaults", web)


def _section_fx(lines: list[str], stats: dict, web: dict) -> None:
    st = stats["fx"]["columns"]["GBPUSD"]
    lines.append("**What it did this month.**")
    lines.append("")
    _level_table(lines, stats, "fx", ("GBPUSD",))
    pctl = st["move_percentile"]
    lines.append(
        f"**Where it sits against history.** GBPUSD {st['level']:,.4f}, "
        f"{st['change_pct']:+.2f}% on the month"
        + (f" — the {ordinal(pctl)} percentile of trailing two-year moves"
           if pctl is not None else "")
        + f"; the level sits in the {ordinal(st['level_percentile'])} "
          f"percentile of its two-year range "
          f"({st['trailing_low']:,.4f}–{st['trailing_high']:,.4f}).")
    lines.append("")
    lines.append("**Why it is two-sided.** The USD assets translate at this "
                 "rate and so do the USD liability cohorts, so a sterling "
                 "move is never a one-way exposure here. The short-end basis "
                 "behind the pair (SONIA vs SOFR) is not in our data; live "
                 "mode covers it.")
    lines.append("")
    _drivers(lines, "fx", web)


def _section_equities(lines: list[str], stats: dict, web: dict) -> None:
    d = stats["derived"]
    cols = ("FTSE100", "SP500", "SX5E")
    lines.append("**What it did this month.**")
    lines.append("")
    _level_table(lines, stats, "equity", cols)
    lines.append("**Where it sits against history.**")
    lines.append("")
    lines.append("| index | drawdown from 2y peak | 2y peak | level %ile |")
    lines.append("|---|---|---|---|")
    for c in cols:
        e = d["equity"][c]
        st = stats["equity"]["columns"][c]
        lines.append(f"| {c} | {e['drawdown_pct']:+.2f}% "
                     f"| {e['trailing_peak']:,.4f} "
                     f"| {ordinal(st['level_percentile'])} |")
    lines.append("")
    disp = _dispersion_pp(stats)
    led, amount = _leader(stats, "equity", cols)
    lines.append(f"- Widest move: {led} at {amount:+.2f}%. Best-to-worst "
                 f"dispersion across the three indices {disp:.2f}pp — "
                 + ("a wide spread, so this was regional or sectoral rather "
                    "than a single global risk move." if disp >= 2.0 else
                    "narrow, so the three indices moved as one market."))
    lines.append("- Implied volatility (VIX and equivalents) is **not** in "
                 "our data. The vol we calibrate comes from 504 days of "
                 "realised history, so the market's forward view is exactly "
                 "the cross-check we cannot make from here.")
    lines.append("")
    _drivers(lines, "equities", web)


_ABSENT_EXTRA = {
    "inflation": (
        "**Why it still gets a section.** Claims inflation — building costs "
        "on the property book, social and litigation inflation on casualty — "
        "is the largest liability-side risk we cannot price. The reserves "
        "are fixed cashflows, so an inflation shock has no channel into any "
        "number this system produces. Breakevens and real yields would at "
        "least give the market's forward view; we hold neither."),
    "employment": (
        "**Why it still gets a section.** Employment and wages are the "
        "driver behind the policy rates that drive everything we do model, "
        "so it belongs in the standing set as the thing we watch but never "
        "capture. It reaches us only after the fact, already priced into "
        "the curves."),
}

_ABSENT_ASK = {
    "inflation": ("headline and core CPI for the UK and US, breakevens and "
                  "real yields, and the claims-inflation trends — building "
                  "costs and litigation severity — that the reserves cannot "
                  "see."),
    "employment": ("payrolls and unemployment, wage growth, vacancies, and "
                   "the PMIs and growth prints the central banks are "
                   "reading."),
}


# Human labels for the independently-sourced factor keys.
_INDEP_LABELS = {
    "bank_rate": "BoE Bank Rate", "fed_funds": "Fed funds (upper)",
    "gilt_10y": "UK 10y gilt", "ust_10y": "US 10y Treasury",
    "ftse100": "FTSE 100", "sp500": "S&P 500", "sx5e": "EURO STOXX 50",
    "gbpusd": "GBP/USD", "uk_cpi": "UK CPI (YoY)", "us_cpi": "US CPI (YoY)",
}


def _independent_levels_section(meta: dict) -> list[str]:
    """The month-end factor levels @focused sourced INDEPENDENTLY on the web,
    each with a source. This is what makes the note an independent check
    rather than a second read of the model's own pipeline: room 1 compares
    these against the assumptions file. Absent (mock, or web unavailable) the
    section explains what it would carry."""
    levels = meta.get("independent_levels") or {}
    unsourced = meta.get("independent_unsourced") or []
    out = ["## Independently sourced — month-end levels (open web)", ""]
    if not levels:
        out += [
            "> Not present on this note. These are the input risk factors "
            "re-sourced from primary web sources (central banks, exchanges, "
            "official statistics) — a level reached by a DIFFERENT route "
            "than the model's own data pipeline, so a disagreement with the "
            "assumptions is real evidence rather than a bug in one shared "
            "source. Room 1 compares them against `assumptions/`. Live mode "
            "with web access populates this section.", "",
        ]
        return out
    out += [
        "Each level below was sourced by @focused from the open web, with a "
        "link, and is **independent of the model's data pipeline**. Room 1 "
        "checks these against `assumptions/` — a disagreement is a real "
        "flag, not a calibration artefact.", "",
        "| factor | independently-sourced level | source |",
        "|---|---|---|",
    ]
    for key, rec in levels.items():
        if not isinstance(rec, dict):
            continue
        label = _INDEP_LABELS.get(key, key)
        val = rec.get("value")
        unit = clean_web_text(str(rec.get("unit") or ""))
        url = clean_web_text(str(rec.get("source_url") or ""))
        # Index levels are thousands and rates are small decimals, so a
        # single significant-figure format serves neither: %g turned
        # 10176.5 into "1.018e+04". Two decimals, thousands separated.
        vtxt = (f"{val:,.2f}".rstrip("0").rstrip(".")
                if isinstance(val, (int, float))
                else clean_web_text(str(val)))
        if unit == "%":
            vtxt += "%"
        elif unit and unit != "index":
            vtxt += f" {unit}"
        src = f"[link]({url})" if url.startswith("http") else "—"
        out.append(f"| {label} | {vtxt} | {src} |")
    out.append("")
    if unsourced:
        names = ", ".join(_INDEP_LABELS.get(k, k) for k in unsourced)
        out += [f"*Not independently sourced this month: {names} — flagged "
                "rather than guessed.*", ""]
    return out


def _render_focused(month: str, stats: dict, web: dict) -> str:
    meta = stats["meta"]
    lines: list[str] = [f"# Focused-Risks Research Note — {month}", ""]
    lines += _mode_banner("focused", web, meta.get("web_unavailable"))
    lines += [
        "**The standing set.** The same seven risks, in the same order, "
        "every month — interest rates · inflation · credit spreads · "
        "defaults and distress · employment · GBP/USD · equities — so that "
        "this month reads against last month without re-learning the "
        "layout. A risk we cannot cover keeps its section and says why.",
        "",
        f"Month-end {meta['asof']}; previous month-end {meta['prev_asof']}; "
        f"{meta['month_business_days']} business days in the month; "
        f"trailing window {meta['trailing_window_days']} business days. "
        "Percentiles rank the month's move among rolling same-length moves "
        "over the trailing window; level percentiles rank the month-end "
        "level in the same window; daily vols are unannualised standard "
        "deviations of daily changes.",
        "",
    ]
    lines += _independent_levels_section(meta)

    for i, risk in enumerate(FOCUSED_RISKS, start=1):
        lines.append(f"## {i}. {risk['title']}")
        lines.append("")
        key = risk["key"]
        if key == "rates":
            _section_rates(lines, stats, web)
        elif key == "credit":
            _section_credit(lines, stats, web)
        elif key == "defaults":
            _section_defaults(lines, stats, web)
        elif key == "fx":
            _section_fx(lines, stats, web)
        elif key == "equities":
            _section_equities(lines, stats, web)
        else:
            r = dict(risk)
            r["live_ask"] = _ABSENT_ASK[key]
            _section_absent(lines, r, web, _ABSENT_EXTRA[key])

    lines.append("## Coverage and limitations")
    lines.append("")
    lines.append("| standing risk | our series | in the factor set |")
    lines.append("|---|---|---|")
    for risk in FOCUSED_RISKS:
        series = ", ".join(f"`{s}`" for s in risk["series"]) or "**none**"
        if risk["coverage"] == "proxy":
            series += " (proxy only)"
        lines.append(f"| {risk['title']} | {series} | {risk['in_model']} |")
    lines.append("")
    lines.append("Notable observations, from the fixed thresholds stated "
                 "above (top/bottom decile move, two-year extreme, outsized "
                 "move, realised vol hot or cold):")
    lines.append("")
    any_notable = False
    for block, series, cols, _kind in BLOCKS:
        for c in cols:
            for n in stats[block]["columns"][c]["notable"]:
                any_notable = True
                lines.append(f"- {block}/{c}: {n}")
    if not any_notable:
        lines.append("- Nothing crosses the fixed thresholds this month.")
    lines.append("")

    if web:
        lines.append("## Sources")
        lines.append("")
        srcs = _web_sources(stats["meta"].get("web") or {})
        if srcs:
            for s in srcs:
                lines.append(f"- {s['title']}"
                             + (f" — {s['url']}" if s["url"] else ""))
        else:
            lines.append("- (the live pass returned no source list)")
        lines.append("")

    lines.append("## Appendix — factor detail")
    lines.append("")
    lines.append("Every modelled column, including the intra-month range and "
                 "the dates it was set. This is the table the room-1 "
                 "reconciliation and the room-3 desks read.")
    lines.append("")
    for block, series, cols, kind in BLOCKS:
        b = stats[block]
        unit = "% (as published)" if kind == "rate" else "levels"
        lines.append(f"### {BLOCK_TITLES[block]} — `{series}.csv` ({unit})")
        lines.append("")
        lines.append(f"Month-end {b['asof']} vs {b['prev_asof']} "
                     f"({b['n_days']} business days in the month).")
        lines.append("")
        lines.append("| column | month-end | Δ month | intra-month low "
                     "| intra-month high | move %ile (2y) |")
        lines.append("|---|---|---|---|---|---|")
        for c in cols:
            st = b["columns"][c]
            pct = (ordinal(st["move_percentile"])
                   if st["move_percentile"] is not None else "n/a")
            if kind == "rate":
                lines.append(
                    f"| {c} | {st['level_pct']:.4f} "
                    f"| {st['change_bp']:+.1f}bp "
                    f"| {st['low_pct']:.4f} ({st['low_date']}) "
                    f"| {st['high_pct']:.4f} ({st['high_date']}) | {pct} |")
            else:
                lines.append(
                    f"| {c} | {st['level']:,.4f} | {st['change_pct']:+.2f}% "
                    f"| {st['low']:,.4f} ({st['low_date']}) "
                    f"| {st['high']:,.4f} ({st['high_date']}) | {pct} |")
        lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# rendering — @wide-eye: the wider risks, and an honest account of what mock
# cannot reach
# --------------------------------------------------------------------------

def _render_wide_eye(month: str, stats: dict, web: dict) -> str:
    meta = stats["meta"]
    d = stats["derived"]
    lines: list[str] = [f"# Wide-Eye Research Note — {month}", ""]
    lines += _mode_banner("wide-eye", web, meta.get("web_unavailable"))
    lines += [
        f"Month-end {meta['asof']}; previous month-end {meta['prev_asof']}. "
        "This note carries no portfolio numbers by construction — the "
        "room-3 post it backs is context-marked and quarantined, and the "
        "market-level figures below are market data, never book data.",
        "",
    ]

    groups: list[str] = []
    for r in WIDER_RISKS:
        if r["group"] not in groups:
            groups.append(r["group"])

    lines.append("## The standing menu")
    lines.append("")
    lines.append("Not a fixed schema — the menu is a floor, not a ceiling — "
                 "but every theme gets a line every month, with its channel "
                 "into the model stated. Where a wider risk has no channel, "
                 "saying so is the point: *this is material and we cannot "
                 "currently price it* is the most useful sentence in this "
                 "note, and it feeds @red-team's standing limitations.")
    lines.append("")
    for g in groups:
        lines.append(f"### {g}")
        lines.append("")
        for r in WIDER_RISKS:
            if r["group"] != g:
                continue
            lines.append(f"**{r['title']}**")
            lines.append("")
            if web.get(r["key"]):
                lines.append(web[r["key"]])
            else:
                lines.append(f"*Not covered this month — the web research "
                             f"did not return anything for this theme.* It "
                             f"would look at: {r['live']}.")
            lines.append("")
            lines.append(f"> Channel into the model — {r['channel']}")
            lines.append("")

    lines.append("## What our own series can say")
    lines.append("")
    lines.append("The market-level reads that do not need the web. These are "
                 "market data, computed from `data/processed/*.csv`; none of "
                 "it is a portfolio number.")
    lines.append("")

    lines.append("### Risk appetite, from the credit curve")
    lines.append("")
    for name, label in (("HY_BBB", "HY − BBB"), ("CCC_HY", "CCC − HY")):
        q = d["quality"][name]
        lines.append(f"- {label}: {q['level_bp']:+.1f}bp, "
                     f"{q['change_bp']:+.1f}bp on the month, level in the "
                     f"{ordinal(q['level_percentile'])} percentile of the "
                     f"trailing two years.")
    lines.append("- Read: quality dispersion is the cleanest appetite signal "
                 "our data carries. It is a market price, not a default "
                 "rate — and the private-credit sleeve, whose marks lag, "
                 "will not show any of it until well after the traded market "
                 "has.")
    lines.append("")

    lines.append("### Fixed income conditions and gilt market functioning")
    lines.append("")
    lines.append("| tenor | gilt − swap basis | Δ month | level %ile (2y) |")
    lines.append("|---|---|---|---|")
    for t in TENORS:
        b = d["basis"][t]
        lines.append(f"| {t} | {b['level_bp']:+.1f}bp "
                     f"| {b['change_bp']:+.1f}bp "
                     f"| {ordinal(b['level_percentile'])} |")
    lines.append("")
    lines.append("- Read: the gilt/swap basis is the one market-functioning "
                 "signal we own. It widens when gilts cheapen against swaps "
                 "— supply, dealer balance sheet, forced selling — and it is "
                 "the same basis the balance sheet is unhedged against. "
                 "Issuance calendars, auction tails and dealer positioning "
                 "are not in our data; live mode covers them.")
    swap_2s10 = d["curve_shape"]["gbp_swap"]["s2s10"]
    lines.append(f"- GBP swap 2s10s {swap_2s10['level_bp']:+.1f}bp "
                 f"({swap_2s10['change_bp']:+.1f}bp on the month), level in "
                 f"the {ordinal(swap_2s10['level_percentile'])} percentile "
                 "of the trailing two years.")
    lines.append("")

    lines.append("### Equity market stress")
    lines.append("")
    lines.append("| index | drawdown from 2y peak | realised day vol "
                 "(month) | vs trailing |")
    lines.append("|---|---|---|---|")
    for c in ("FTSE100", "SP500", "SX5E"):
        e = d["equity"][c]
        st = stats["equity"]["columns"][c]
        mv = (f"{st['month_dayvol_pct']:.2f}%"
              if st["month_dayvol_pct"] is not None else "n/a")
        lines.append(f"| {c} | {e['drawdown_pct']:+.2f}% | {mv} "
                     f"| {st['vol_pressure']} |")
    lines.append("")
    lines.append(f"- Best-to-worst dispersion across the three indices "
                 f"{_dispersion_pp(stats):.2f}pp on the month.")
    lines.append("- Read: realised vol is the only volatility read we own. "
                 "Concentration, positioning and implied vol — the things "
                 "that actually characterise an equity fear episode — are "
                 "not in our data and not in the factor set.")
    lines.append("")

    lines.append("### The tail our window cannot see")
    lines.append("")
    lines.append(f"- The calibration window is {meta['trailing_window_days']} "
                 "business days, about two years. The 2022 gilt/LDI episode "
                 "sits outside it entirely, which means the most recent "
                 "genuine dislocation in the market we are most exposed to "
                 "contributes nothing to the vols and correlations we "
                 "capitalise against.")
    lines.append("- Anything on the menu above that has never happened "
                 "inside the window is, for this model, a risk of zero "
                 "probability. That is a property of the calibration, not of "
                 "the world.")
    lines.append("")

    lines.append("## Standing limitations — material and unpriceable here")
    lines.append("")
    for r in WIDER_RISKS:
        if r["channel"].startswith("NONE"):
            lines.append(f"- **{r['title']}** — {r['channel']}")
    lines.append("")
    lines.append("These are handed to @red-team as standing items, not "
                 "re-derived each month. They change when the model changes, "
                 "not when the market does.")
    lines.append("")

    if web:
        lines.append("## Sources")
        lines.append("")
        srcs = _web_sources(stats["meta"].get("web") or {})
        if srcs:
            for s in srcs:
                lines.append(f"- {s['title']}"
                             + (f" — {s['url']}" if s["url"] else ""))
        else:
            lines.append("- (the live pass returned no source list)")
        lines.append("")
    return "\n".join(lines)


_AGENT_ALIASES = {"focused": "focused", "@focused": "focused",
                  "focused-risks": "focused", "@focused-risks": "focused",
                  "focused-book": "focused", "@focused-book": "focused",
                  "wide-eye": "wide-eye", "@wide-eye": "wide-eye",
                  "wide_eye": "wide-eye", "@wider-risk": "wide-eye"}


def resolve_agent(agent: str) -> str:
    """Handle or bare name -> the note's producer ("focused"/"wide-eye").
    @focused-book reads the SAME note as @focused — one report, two rooms."""
    return _AGENT_ALIASES.get(str(agent).strip().lower(), "focused")


def _compute_stats(month: str, agent: str, p_end: pd.Timestamp,
                   prev_target: pd.Timestamp,
                   data_through: str | None) -> dict:
    frames: dict = {}
    stats: dict = {}
    for block, series, cols, kind in BLOCKS:
        df = _load(series)
        frames[block] = df
        curr_idx = df.index[df.index <= p_end]
        prev_idx = df.index[df.index <= prev_target]
        if len(prev_idx) == 0:
            raise ValueError(f"no {series} data before month {month}")
        if len(curr_idx) == 0 or curr_idx.max() <= prev_idx.max():
            raise ValueError(f"no {series} data through {p_end.date()}")
        curr_end, prev_end = curr_idx.max(), prev_idx.max()
        month_rows = df[(df.index > prev_end) & (df.index <= curr_end)]
        stats[block] = {
            "asof": str(curr_end.date()),
            "prev_asof": str(prev_end.date()),
            "n_days": int(len(month_rows)),
            "columns": {c: _column_stats(df[c], prev_end, curr_end, kind)
                        for c in cols},
        }
    ref = stats["gbp_swap"]
    stats["derived"] = _compute_derived(
        frames, pd.Timestamp(ref["prev_asof"]), pd.Timestamp(ref["asof"]))
    stats["meta"] = {
        "month": month,
        "agent": agent,
        "asof": ref["asof"],
        "prev_asof": ref["prev_asof"],
        "month_business_days": ref["n_days"],
        "trailing_window_days": TRAILING_DAYS,
        "generator": "app/agents/research.py",
        "sources": [f"data/processed/{series}.csv"
                    for _, series, _, _ in BLOCKS],
        "independence": ("computed directly from data/processed/*.csv; "
                         "never reads assumptions/ or engine outputs"),
        "snapshot_data_through": str(p_end.date()) if data_through else None,
    }
    return stats


def generate_note(asof: str, agent: str = "focused",
                  out_dir: Path | str | None = None,
                  data_through: str | None = None,
                  web_context: dict | None = None) -> dict:
    """Compute the research note for `asof`'s month and (re)write
    outputs/research/<YYYY_MM>_<agent>.md. Accepts 'YYYY-MM' or a full ISO
    date. `agent` selects the producer (SPEC-APP 5.1, PENDING-BATCH2 s2):
    "focused" (default, the standing focused-risk set) or "wide-eye" (the
    wider, market-level risks).

    `data_through` (fresh snapshots, SPEC-APP E): compute what has happened
    from the month-end close to this later date instead of month-on-month;
    the result is NOT persisted to the canonical file.

    `web_context` (live mode only): {"risks": {risk_key: prose}, "sources":
    [...]} from a web-search pass. It is untrusted prose — sanitised,
    rendered as prose, never parsed for numbers. Absent (mock), the note
    says at the top that it has no web access and covers exactly what our
    own series support.

    Returns {month, asof, prev_asof, path, markdown, stats}."""
    agent = resolve_agent(agent)
    month = str(asof)[:7]
    if not _MONTH_RE.match(month):
        raise ValueError(f"asof must be 'YYYY-MM' or an ISO date, got {asof!r}")

    period = pd.Period(month, freq="M")
    month_end = period.end_time.normalize()
    if data_through is not None:
        p_end = pd.Timestamp(str(data_through)).normalize()
        prev_target = month_end
        if p_end <= prev_target:
            raise ValueError("data_through must be after the month-end "
                             f"close ({month_end.date()})")
    else:
        p_end = month_end
        prev_target = (period - 1).end_time.normalize()

    stats = _compute_stats(month, agent, p_end, prev_target, data_through)
    web = _web_sections(web_context)
    stats["meta"]["web_research"] = bool(web)
    stats["meta"]["web_unavailable"] = (
        clean_web_text(web_context.get("unavailable"))
        if isinstance(web_context, dict) and web_context.get("unavailable")
        and not web else None)
    if web_context:
        stats["meta"]["web"] = {"sources": _web_sources(web_context)}
    # INDEPENDENTLY SOURCED LEVELS (@focused only). The month-end level of
    # each input factor as @focused found it on the open web, each with a
    # source URL. Carried on the note so the room-1 pass can compare these
    # against the assumptions file — a genuinely independent check, because
    # these figures did not come from the pipeline the model calibrates on.
    if isinstance(web_context, dict):
        lv = web_context.get("sourced_levels")
        stats["meta"]["independent_levels"] = lv if isinstance(lv, dict) else {}
        un = web_context.get("unsourced")
        stats["meta"]["independent_unsourced"] = un if isinstance(un, list) else []
    stats["meta"]["standing_set"] = (
        [r["key"] for r in FOCUSED_RISKS] if agent == "focused"
        else [r["key"] for r in WIDER_RISKS])
    stats["coverage"] = [
        {"risk": r["key"], "title": r["title"], "coverage": r["coverage"],
         "series": list(r["series"]), "in_model": r["in_model"]}
        for r in FOCUSED_RISKS]

    md = (_render_focused(month, stats, web) if agent == "focused"
          else _render_wide_eye(month, stats, web))
    result = {"month": month, "asof": stats["meta"]["asof"],
              "prev_asof": stats["meta"]["prev_asof"], "path": None,
              "markdown": md, "stats": stats}
    if data_through is not None:
        return result  # snapshot note: computed only, never persisted

    out = Path(out_dir) if out_dir is not None else RESEARCH_DIR
    out.mkdir(parents=True, exist_ok=True)
    path = out / note_filename(month, agent)
    tmp = out / (note_filename(month, agent) + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(md)
    os.replace(tmp, path)  # atomic: a concurrent reader never sees a torn file
    result["path"] = str(path)

    # Sidecar: the independently-sourced factor levels as STRUCTURED data,
    # beside the human-readable note. read_research (which recomputes the
    # note without web context) reloads these so the room-1 pass can compare
    # them to the assumptions and bind to them — the markdown table alone is
    # lossy and cannot be bound against.
    levels = stats["meta"].get("independent_levels") or {}
    side = out / (note_filename(month, agent) + ".levels.json")
    if levels:
        side.write_text(json.dumps({
            "independent_levels": levels,
            "independent_unsourced": stats["meta"].get(
                "independent_unsourced") or []}, indent=1), encoding="utf-8")
    elif side.exists():
        side.unlink()          # stale sidecar from a prior web run
    return result


def generate_wide_eye_stub(asof: str, out_dir: Path | str | None = None,
                           data_through: str | None = None) -> dict:
    """Back-compatible alias. The @wide-eye note is no longer a stub — it is
    a full report (standing menu + what our own series support) — but the
    old entry point still resolves."""
    return generate_note(asof, agent="wide-eye", out_dir=out_dir,
                         data_through=data_through)


def generate_all(asof: str, out_dir: Path | str | None = None,
                 web_context: dict | None = None) -> list[dict]:
    """The research STAGE: both notes for one month, in run order
    (PENDING-BATCH2 s2). `web_context` may be a per-agent mapping
    {"focused": {...}, "wide-eye": {...}} or a single context applied to
    both."""
    notes = []
    for agent in AGENTS:
        wc = web_context
        if isinstance(web_context, dict) and (
                set(web_context) & set(AGENTS)):
            wc = web_context.get(agent)
        notes.append(generate_note(asof, agent=agent, out_dir=out_dir,
                                   web_context=wc))
    return notes


if __name__ == "__main__":  # regeneration: python -m app.agents.research 2026-02 2026-03
    import sys
    for arg in sys.argv[1:]:
        for note in generate_all(arg):
            print(f"{note['month']}: wrote {note['path']} "
                  f"(asof {note['asof']}, prev {note['prev_asof']})")
