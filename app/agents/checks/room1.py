"""Room 1 · Assumption Challenge — deterministic mock checks.

pre_flight_checks  the go/no-go before compute is spent (PENDING-BATCH2 §7).
                Five check families — reconciliation against source
                (absorbed whole from the retired @curve-check), structural
                integrity, bounds and plausibility, internal consistency,
                and an external check that only live mode can run. Output
                is a VERDICT line, then the failing items. It flags; it
                never blocks.
vcv             what happened to the vols and the correlations this month,
                with the 21-factor matrix attached (§8). The recomputation
                check still runs (it catches D1 by methodology) but it is a
                bullet WHEN IT FAILS, not the subject of the post.
holdings        input verification ONLY: ISINs, ratings vs public
                reference, bucket mappings, coupon plausibility (catches
                D2). Allocation/sleeve-change analysis lives in room 3's
                @warden now (PENDING-ROSTER renames).
red_team_opening  opening pass of the two-part standing challenge (H.1):
                always at least one challenge, reads room:1
focused         light context on the model inputs, drawn from the report
                @focused wrote in the research stage (PENDING-BATCH2 s2):
                the month's material moves, each tied to the assumptions
                field carrying it, plus a one-line base-level cross-check
                against the note (1bp / 0.1%) that catches the wrong-file,
                stale-snapshot and fat-fingered class

Each draft carries a `significance` level (SPEC-APP G) set by the check's
own thresholds — never a stylistic choice: a genuine mismatch/flag is
`critical`, a clean pass is `quiet`.
"""

from __future__ import annotations

import datetime

from app.agents import style
from app.agents.style import Prose
from app.agents.research import ordinal
from app.agents.tools import ToolError

TENORS = [2, 5, 10, 20]
CURVES = ["gbp_swap", "gbp_gilt", "ust"]

# rating letter -> the book's four buckets (scenarios/reference/README.md)
_BUCKETS = {
    "AAA": "AA", "AA+": "AA", "AA": "AA", "AA-": "AA",
    "A+": "A", "A": "A", "A-": "A",
    "BBB+": "BBB", "BBB": "BBB", "BBB-": "BBB",
}
# implausibility ceilings: a coupon above this for the bucket is a smell
_COUPON_CEILING = {"AA": 0.055, "A": 0.065, "BBB": 0.075, "HY": 0.13}
VOL_FLAG_REL = 0.05  # >5% off the recomputed value is a discrepancy

# @focused tolerances (SPEC-APP 5.1): assumptions base levels must
# match the research note's month-end values to 1bp (rates/spreads) or
# 0.1% relative (equity/FX levels).
RESEARCH_RATE_TOL_BP = 1.0
RESEARCH_LEVEL_TOL = 0.001
SPREAD_RATINGS = ("AA", "A", "BBB", "HY", "CCC")
EQUITY_INDICES = ("FTSE100", "SP500", "SX5E")


def _bucket(agency_rating: str) -> str:
    return _BUCKETS.get(str(agency_rating).strip(), "HY")


def _shift_days(iso: str, days: int) -> str:
    d = datetime.date.fromisoformat(str(iso))
    return (d - datetime.timedelta(days=days)).isoformat()


# ==========================================================================
# @pre-flight-checks (PENDING-BATCH2 §7) — the go/no-go before compute is
# spent. Every other agent in this room analyses a domain; this one asks a
# narrower question: is this input set fit to run at all?
#
# @curve-check is REMOVED and its reconciliation absorbed here, so input
# validation has one owner end to end rather than a split with no seam.
#
# It FLAGS, it does not BLOCK — nothing is prevented from running, per the
# agent-proposes-human-disposes rule. A DO NOT RUN verdict is a loud post
# and a notification, not a lock.
# ==========================================================================

PF_RATE_TOL = 1e-4          # one basis point, on rates and spreads
PF_LEVEL_TOL = 1e-3         # a tenth of a percent, on index and FX levels
PF_MAX_QUOTED = 2           # per-post tool budget: the worst few in the post

# The 21 factors in SPEC order. The assumptions file's `correlation.order`
# must equal this exactly — same members AND same order, since the Cholesky
# factorisation and every downstream block mapping index into it positionally.
SPEC_FACTOR_ORDER = (
    [f"gbp_swap_{t}" for t in TENORS] + [f"gbp_gilt_{t}" for t in TENORS]
    + [f"ust_{t}" for t in TENORS]
    + [f"spread_{r}" for r in SPREAD_RATINGS]
    + [f"eq_{i}" for i in EQUITY_INDICES] + ["fx_GBPUSD"]
)

# Bounds a defensible input cannot leave. Deliberately WIDE: this family
# catches the absurd (a negative spread, a rate of 400%, a vol off by an
# order of magnitude), not the merely surprising — that is @realist's job
# and he has experience bands for it.
PF_RATE_BOUNDS = (-0.02, 0.20)          # -200bp .. 2000bp
PF_SPREAD_BOUNDS = (1e-5, 0.30)         # >0 .. 3000bp
PF_FX_BOUNDS = (0.80, 2.50)
PF_VOL_BOUNDS = {"rate": (5e-5, 0.10), "spread": (2e-5, 0.20),
                 "equity": (0.02, 1.00), "fx": (0.005, 0.50)}
PF_LEVEL_MOVE_MAX = 0.35    # a 35% month on an index level is not a month


class _Item:
    """One failing check. `blocking` decides the verdict; `claims` are the
    (value, tool_call_id, text) triples the post quotes when it names it."""

    __slots__ = ("family", "label", "blocking", "found", "expected",
                 "found_tc", "expected_tc", "found_txt", "expected_txt")

    def __init__(self, family, label, blocking, found=None, expected=None,
                 found_tc=None, expected_tc=None, found_txt=None,
                 expected_txt=None):
        self.family, self.label, self.blocking = family, label, blocking
        self.found, self.expected = found, expected
        self.found_tc, self.expected_tc = found_tc, expected_tc
        self.found_txt, self.expected_txt = found_txt, expected_txt


def _pf_reconciliation(s, doc, tc_a, asof) -> tuple[list, list]:
    """Family 1 — every base level against data/processed at the calibration
    date. Absorbed whole from @curve-check and widened past the curves to
    spreads, equity levels and FX. Returns (all rows, failing items).

    Rows carry the source value TWICE: `source` in the assumptions file's
    own unit (decimal for rates) for the comparison, and `source_cited` as
    the number the tool result actually contains (percent for rates), which
    is the one a claim may bind to."""
    rows, items = [], []
    start = _shift_days(asof, 14)

    def _add(label, assumed, source, source_cited, tc_src, kind, source_txt):
        if kind == "rate":
            ok = abs(assumed - source) <= PF_RATE_TOL
        else:
            ok = abs(assumed - source) <= PF_LEVEL_TOL * max(abs(source), 1e-12)
        row = {"label": label, "assumed": assumed, "source": source,
               "source_cited": source_cited, "tc_src": tc_src, "kind": kind,
               "ok": ok, "source_txt": source_txt}
        rows.append(row)
        if not ok:
            items.append(_Item(
                "reconciliation", f"{label} does not match source", True,
                found=assumed, expected=source_cited, found_tc=tc_a,
                expected_tc=tc_src,
                found_txt=(style.pc4(assumed) if kind == "rate"
                           else style.num(assumed, 4)),
                expected_txt=source_txt))

    for series in CURVES:
        _tc, d = s.call("read_data_series", series=series, start=start,
                        end=asof)
        last = d["rows"][-1]
        for t in TENORS:
            src_pct = float(last[f"t{t}"])
            _add(f"{series} {t}y", float(doc["curves"][series][t]),
                 src_pct / 100.0, src_pct, _tc, "rate", f"{src_pct:.4f}%")
    _tc, d = s.call("read_data_series", series="credit_oas", start=start,
                    end=asof)
    last = d["rows"][-1]
    for rating in SPREAD_RATINGS:
        src_pct = float(last[rating])
        _add(f"spread {rating}", float(doc["spreads"][rating]),
             src_pct / 100.0, src_pct, _tc, "rate", f"{src_pct:.4f}%")
    _tc, d = s.call("read_data_series", series="equity", start=start,
                    end=asof)
    last = d["rows"][-1]
    for idx in EQUITY_INDICES:
        src = float(last[idx])
        _add(f"equity {idx}", float(doc["equity"][idx]), src, src, _tc,
             "level", style.num(src, 2))
    _tc, d = s.call("read_data_series", series="fx", start=start, end=asof)
    last = d["rows"][-1]
    src = float(last["GBPUSD"])
    _add("fx GBPUSD", float(doc["fx"]["GBPUSD"]), src, src, _tc, "level",
         style.num(src, 4))
    return rows, items


def _pf_structure(doc, book, liab, tc_b, tc_l) -> list[_Item]:
    """Family 2 — structural integrity. Shape, not value: the things that
    make a file loadable and a matrix usable."""
    items: list[_Item] = []
    order = list((doc.get("correlation") or {}).get("order") or [])
    matrix = (doc.get("correlation") or {}).get("matrix") or []
    if order != SPEC_FACTOR_ORDER:
        if sorted(order) == sorted(SPEC_FACTOR_ORDER):
            items.append(_Item("structure", "correlation.order carries the "
                               "right factors in the WRONG order — every "
                               "block mapping indexes into it positionally",
                               True))
        else:
            missing = [f for f in SPEC_FACTOR_ORDER if f not in order]
            extra = [f for f in order if f not in SPEC_FACTOR_ORDER]
            detail = (f"missing {', '.join(missing[:3])}" if missing
                      else f"unexpected {', '.join(extra[:3])}")
            items.append(_Item("structure", "correlation.order does not "
                               f"match the SPEC factor set ({detail})", True))
    if len(matrix) != len(order) or any(len(r) != len(order) for r in matrix):
        items.append(_Item("structure", "correlation matrix is not square "
                           "against the factor count", True))
    else:
        for i, row in enumerate(matrix):
            if abs(float(row[i]) - 1.0) > 1e-9:
                items.append(_Item("structure", "correlation diagonal is not "
                                   "unity", True))
                break
    if "psd_repaired" not in (doc.get("meta") or {}):
        items.append(_Item("structure", "meta.psd_repaired flag absent — no "
                           "record of whether the matrix was repaired",
                           False))
    for series in CURVES:
        for t in TENORS:
            if (doc.get("curves", {}).get(series, {}).get(t)) is None:
                items.append(_Item("structure",
                                   f"curves.{series}.{t} missing or null",
                                   True))
    for block in ("gbp_swap", "gbp_gilt", "ust"):
        for t in TENORS:
            if (doc.get("vols", {}).get(block, {}).get(t)) is None:
                items.append(_Item("structure",
                                   f"vols.{block}.{t} missing or null", True))
    positions = book.get("positions") or []
    seen, dupes = set(), []
    for p in positions:
        if p.get("id") in seen:
            dupes.append(str(p.get("id")))
        seen.add(p.get("id"))
    if dupes:
        items.append(_Item("structure",
                           f"duplicate position ids: {', '.join(dupes[:3])}",
                           True))
    ref = book.get("ref_index_levels") or {}
    missing_ref = [i for i in EQUITY_INDICES if i not in ref]
    if missing_ref:
        items.append(_Item("structure", "ref_index_levels missing "
                           f"{', '.join(missing_ref)} — equity positions "
                           "cannot be marked", True, found_tc=tc_b))
    empty = [str(c.get("id")) for c in (liab.get("cohorts") or [])
             if not (c.get("cashflows") or [])]
    if empty:
        items.append(_Item("structure", "liability cohort with an empty "
                           f"cashflow vector: {', '.join(empty)}", True,
                           found_tc=tc_l))
    return items


def _pf_bounds(doc, prev_doc, tc_a, tc_pa) -> list[_Item]:
    """Family 3 — bounds and plausibility. Nothing here is a judgement about
    the market; it is the set of values that cannot be right."""
    items: list[_Item] = []

    def _bounded(label, value, lo, hi, kind, blocking=True):
        if lo <= value <= hi:
            return
        # The band itself is never quoted in the post: it is a constant in
        # this module, not something a tool returned, so a figure for it
        # could not bind. The working names it in prose instead.
        items.append(_Item(
            "bounds", f"{label} outside a defensible range", blocking,
            found=value, found_tc=tc_a,
            found_txt=(style.pc4(value) if kind == "rate"
                       else style.num(value, 4))))

    for series in CURVES:
        for t in TENORS:
            _bounded(f"curves.{series}.{t}",
                     float(doc["curves"][series][t]), *PF_RATE_BOUNDS, "rate")
    for rating in SPREAD_RATINGS:
        _bounded(f"spreads.{rating}", float(doc["spreads"][rating]),
                 *PF_SPREAD_BOUNDS, "rate")
    _bounded("fx.GBPUSD", float(doc["fx"]["GBPUSD"]), *PF_FX_BOUNDS, "level")
    for block, kind in (("gbp_swap", "rate"), ("gbp_gilt", "rate"),
                        ("ust", "rate")):
        for t in TENORS:
            _bounded(f"vols.{block}.{t}", float(doc["vols"][block][t]),
                     *PF_VOL_BOUNDS["rate"], "rate")
    for rating in SPREAD_RATINGS:
        _bounded(f"vols.spread.{rating}",
                 float(doc["vols"]["spread"][rating]),
                 *PF_VOL_BOUNDS["spread"], "rate")
    for idx in EQUITY_INDICES:
        _bounded(f"vols.equity.{idx}", float(doc["vols"]["equity"][idx]),
                 *PF_VOL_BOUNDS["equity"], "rate")
    _bounded("vols.fx.GBPUSD", float(doc["vols"]["fx"]["GBPUSD"]),
             *PF_VOL_BOUNDS["fx"], "rate")

    if prev_doc is not None:
        for idx in EQUITY_INDICES:
            now, was = float(doc["equity"][idx]), float(prev_doc["equity"][idx])
            if was and abs(now - was) / abs(was) > PF_LEVEL_MOVE_MAX:
                items.append(_Item(
                    "bounds", f"equity.{idx} moved implausibly since the "
                              "prior month-end", True, found=now,
                    expected=was, found_tc=tc_a, expected_tc=tc_pa,
                    found_txt=style.num(now, 2), expected_txt=style.num(was, 2)))
    return items


def _pf_consistency(doc, book, liab) -> list[_Item]:
    """Family 4 — internal consistency. Every cross-reference in the input
    set resolving to something that exists."""
    items: list[_Item] = []
    spread_set = set(doc.get("spreads") or {})
    curve_set = set(doc.get("curves") or {})
    for p in book.get("positions") or []:
        r = p.get("rating")
        if r is not None and r not in spread_set:
            items.append(_Item("consistency",
                               f"{p.get('id')} is rated {r}, which has no "
                               "spread level in the assumptions", True))
        c = p.get("curve")
        if c is not None and c not in curve_set:
            items.append(_Item("consistency",
                               f"{p.get('id')} prices off `{c}`, which is "
                               "not a curve in this file", True))
    for c in liab.get("cohorts") or []:
        curve = c.get("curve")
        if curve not in curve_set:
            items.append(_Item("consistency",
                               f"cohort {c.get('id')} discounts on `{curve}`, "
                               "which is not a curve in this file", True))
        elif (c.get("currency") == "GBP") != str(curve).startswith("gbp"):
            items.append(_Item("consistency",
                               f"cohort {c.get('id')} is "
                               f"{c.get('currency')} but discounts on "
                               f"`{curve}`", True))
    return items


_PF_FAMILIES = ("reconciliation", "structure", "bounds", "consistency")


def pre_flight_checks(ctx) -> list[dict]:
    """The first post in the room-1 pass, and the first thing written before
    the model runs. Leads with a verdict; the body is the failing items and
    nothing else.

    The fifth family — the independent external check — needs the web. In
    mock there is no web, so it says so plainly and reports the other four
    rather than implying a verification it did not make."""
    from app.agents import runtime  # noqa: PLC0415 (leaf module)

    s = ctx.session()
    tc_a, a = s.call("read_assumptions", asof_or_run=ctx.curr_run["id"])
    doc, afile = a["data"], a["file"]
    asof = str(doc["meta"]["asof"])

    rows, items = _pf_reconciliation(s, doc, tc_a, asof)          # 4 calls
    tc_b, b = s.call("read_book", asof_or_run=ctx.curr_run["id"])
    tc_l, liab = s.call("read_liabilities")
    book, liab = b["data"], liab["data"]
    items += _pf_structure(doc, book, liab, tc_b, tc_l)

    prev_doc, tc_pa = None, None
    if ctx.prev_run is not None:
        try:
            tc_pa, pa = s.call("read_assumptions",
                               asof_or_run=ctx.prev_run["id"])
            prev_doc = pa["data"]
        except ToolError:
            prev_doc, tc_pa = None, None
    items += _pf_bounds(doc, prev_doc, tc_a, tc_pa)
    items += _pf_consistency(doc, book, liab)

    external = runtime.agent_mode() == "live"
    blocking = [i for i in items if i.blocking]
    concerns = [i for i in items if not i.blocking]
    if blocking:
        verdict, sig = f"DO NOT RUN — {len(blocking)} blocking items", \
            "critical"
    elif concerns:
        verdict, sig = f"RUN WITH CONCERNS — {len(concerns)} items, none " \
            "blocking", "notable"
    else:
        verdict, sig = "CLEAR TO RUN", "quiet"

    # HOUSE STYLE (PENDING-BATCH2 §12): verdict line, then only the items
    # that failed. Method, the reconciliation table and the full item list
    # live in the working below, where length is welcome.
    origin = Prose()
    origin.add(f"**{verdict}** — `{afile}`, {asof}.")
    quoted = (blocking + concerns)[:PF_MAX_QUOTED]
    for it in quoted:
        origin.add(f"\n- {'**FLAG** ' if it.blocking else ''}{it.label}")
        if it.found_txt is not None and it.found_tc is not None:
            origin.add(" — found ").claim(it.found, it.found_tc,
                                          text=it.found_txt)
            if it.expected_txt is not None and it.expected is not None \
                    and it.expected_tc is not None:
                (origin.add(", expected ")
                       .claim(it.expected, it.expected_tc,
                              text=it.expected_txt))
        origin.add(".")
    if len(items) > len(quoted):
        origin.add("\n- Rest of the items in the working.")
    if not items:
        origin.add("\n- Levels, structure, bounds, consistency: clean.")
    origin.add("\n- External check: "
               + ("verified against public sources."
                  if external else
                  "**not run** — no web access in style. Four families of "
                  "five completed.")
               + "\n\nI flag; I do not block.")

    # The working page gets its own session (bind_post attaches a session's
    # calls to the post that binds it), so the table's figures cite their
    # own recorded reads rather than the feed post's.
    ws = ctx.session()
    tc_wa, _wa = ws.call("read_assumptions", asof_or_run=ctx.curr_run["id"])
    wrows, _ = _pf_reconciliation(ws, doc, tc_wa, asof)

    work = Prose()
    work.add(f"Pre-flight working — `{afile}` at {asof}.\n\n"
             "**Family 1 · reconciliation against source** "
             "(data/processed/*.csv; tolerance one basis point on rates and "
             "spreads, a tenth of a percent on levels).\n\n"
             "| factor | assumed | source | verdict |\n|---|---|---|---|\n")
    for row in wrows:
        (work.add(f"| {row['label']} | ")
             .claim(row["assumed"], tc_wa,
                    style.pc4 if row["kind"] == "rate"
                    else (lambda v: style.num(v, 4)))
             .add(" | ")
             .claim(row["source_cited"], row["tc_src"],
                    text=row["source_txt"])
             .add(f" | {'ok' if row['ok'] else '**MISMATCH**'} |\n"))
    work.add("\n**Families 2-4 · structure, bounds, internal consistency.** "
             "Bounds are wide constants held in this check, not figures from "
             "any file, so they are named rather than quoted: rates within "
             "roughly minus two hundred to two thousand basis points, "
             "spreads strictly positive and under three thousand, GBPUSD "
             "between four fifths and two and a half, vols within an order "
             "of magnitude of their block's plausible range."
             "\n\n| family | item | blocking |\n|---|---|---|\n")
    if items:
        for it in items:
            work.add(f"| {it.family} | {it.label} | "
                     f"{'yes' if it.blocking else 'no'} |\n")
    else:
        for fam in _PF_FAMILIES:
            work.add(f"| {fam} | no items | — |\n")
    work.add("\n**Family 5 · independent external check.** "
             + ("Run: headline levels checked against public sources."
                if external else
                "Not run in mock mode — no web access. Stated rather than "
                "silently skipped: an external check nobody made must never "
                "read as one that passed.")
             + "\n\nThis is a flag, not a gate. Nothing here stops the run: "
               "a DO NOT RUN verdict is a loud post and a notification, and "
               "the run is still the human's to start. The verdict follows "
               "the items mechanically — any reconciliation break is "
               "blocking, a missing `psd_repaired` flag is a concern, and a "
               "pass with neither is CLEAR TO RUN.")

    return [origin.draft("origin", session=s, significance=sig),
            work.draft("expansion", session=ws, significance=sig)]


# ==========================================================================
# @vcv (PENDING-BATCH2 §8) — renamed from @vcv-sentinel and reframed. The
# post is a mini-summary of what happened to the VOLATILITIES and the major
# CORRELATION changes, which is informative every month; the recomputation
# check still runs but appears as a bullet WHEN IT FAILS rather than as the
# subject of a post that says "all fine" eleven months in twelve.
#
# The post carries the matrix as an ATTACHMENT so a reader can look at it
# rather than take a summary on trust. The attachment is engine data read
# through a tool call like any other number, so it inherits provenance — it
# is not a second, unchecked channel.
# ==========================================================================

VCV_CORR_MOVER = 0.05   # |change| in a correlation cell worth outlining


def _vcv_factor_vols(doc: dict) -> dict:
    """factor name -> vol, in SPEC order, flattened out of the nested
    `vols` block the assumptions file stores them in."""
    out: dict = {}
    for block in ("gbp_swap", "gbp_gilt", "ust"):
        for t in TENORS:
            out[f"{block}_{t}"] = float(doc["vols"][block][t])
    for r in SPREAD_RATINGS:
        out[f"spread_{r}"] = float(doc["vols"]["spread"][r])
    for i in EQUITY_INDICES:
        out[f"eq_{i}"] = float(doc["vols"]["equity"][i])
    out["fx_GBPUSD"] = float(doc["vols"]["fx"]["GBPUSD"])
    return out


def _vcv_attachment(doc: dict, prev_doc: dict | None) -> dict:
    """The `vcv_table` attachment payload (§8): the 21 factors in SPEC
    order, each vol current/prior/change, and the correlation matrix with
    the prior month's alongside when a prior run exists."""
    corr = (doc.get("correlation") or {})
    order = list(corr.get("order") or SPEC_FACTOR_ORDER)
    vols = _vcv_factor_vols(doc)
    prev_vols = _vcv_factor_vols(prev_doc) if prev_doc else {}
    payload = {
        "factors": order,
        "vols": [{"factor": f,
                  "current": vols.get(f),
                  "prior": prev_vols.get(f),
                  "change": (None if prev_vols.get(f) is None
                             else vols.get(f) - prev_vols[f])}
                 for f in order],
        "corr": [[float(x) for x in row] for row in (corr.get("matrix") or [])],
        "mover_threshold": VCV_CORR_MOVER,
    }
    if prev_doc is not None:
        pm = (prev_doc.get("correlation") or {}).get("matrix") or []
        if len(pm) == len(payload["corr"]):
            payload["corr_prior"] = [[float(x) for x in row] for row in pm]
    return {"type": "vcv_table", "payload": payload}


def _vcv_corr_movers(doc: dict, prev_doc: dict | None) -> list[tuple]:
    """(factor_a, factor_b, current, prior, change) for the cells that moved
    most, biggest first. Upper triangle only — the matrix is symmetric and
    naming a cell twice is noise."""
    if prev_doc is None:
        return []
    corr = (doc.get("correlation") or {})
    order = list(corr.get("order") or [])
    m = corr.get("matrix") or []
    pm = (prev_doc.get("correlation") or {}).get("matrix") or []
    if len(pm) != len(m) or not m:
        return []
    out = []
    for i in range(len(order)):
        for j in range(i + 1, len(order)):
            now, was = float(m[i][j]), float(pm[i][j])
            out.append((order[i], order[j], now, was, now - was))
    out.sort(key=lambda x: -abs(x[4]))
    return out


def vcv(ctx) -> list[dict]:
    s = ctx.session()
    tc_a, a = s.call("read_assumptions", asof_or_run=ctx.curr_run["id"])
    doc, afile = a["data"], a["file"]
    asof = str(doc["meta"]["asof"])
    window = int(doc["meta"].get("calibration_window_days", 504))

    prev_doc, tc_pa = None, None
    if ctx.prev_run is not None:
        try:
            tc_pa, pa = s.call("read_assumptions",
                               asof_or_run=ctx.prev_run["id"])
            prev_doc = pa["data"]
        except ToolError:
            prev_doc, tc_pa = None, None

    # The recomputation check still runs — it is simply no longer the point
    # of the post unless it fails.
    results = []  # (series, tenor, assumed, recomputed, tc_r, rel)
    for series in ("gbp_swap", "gbp_gilt"):
        for t in TENORS:
            tc_r, r = s.call("recompute_vol", series=series, column=f"t{t}",
                             asof=asof, window_days=window)
            assumed = float(doc["vols"][series][t])
            recomputed = float(r["vol_annualized"])
            rel = abs(assumed - recomputed) / max(recomputed, 1e-12)
            results.append((series, t, assumed, recomputed, tc_r, rel))
    flagged = [x for x in results if x[5] > VOL_FLAG_REL]

    vols_now = _vcv_factor_vols(doc)
    vols_prev = _vcv_factor_vols(prev_doc) if prev_doc else {}
    # Ranked RELATIVE to the factor's own level. Equity vols run an order
    # of magnitude above rate vols, so ranking by absolute change would
    # name an equity factor every single month regardless of what happened.
    vol_moves = sorted(
        ((f, vols_now[f], vols_prev[f], vols_now[f] - vols_prev[f])
         for f in vols_now if f in vols_prev),
        key=lambda x: -abs(x[3]) / max(abs(x[2]), 1e-12))
    movers = _vcv_corr_movers(doc, prev_doc)
    big_movers = [m for m in movers if abs(m[4]) >= VCV_CORR_MOVER]

    # HOUSE STYLE (§12): headline vol move + headline correlation move on
    # one line, then terse bullets. The window mechanics, the full riser and
    # faller lists and the recomputation table are in the working.
    origin = Prose()
    if vol_moves and movers:
        f, now, was, chg = vol_moves[0]
        ca, cb, cnow, cwas, cchg = movers[0]
        (origin.add(f"Vols: `{f}` moved most, ")
               .claim(now, tc_a, style.pc4)
               .add(" from ")
               .claim(was, tc_pa, style.pc4)
               .add(f" ({'up' if chg > 0 else 'down'}). "
                    f"Correlations: `{ca}`/`{cb}`, now ")
               .claim(cnow, tc_a, lambda v: style.num(v, 4))
               .add(" from ")
               .claim(cwas, tc_pa, lambda v: style.num(v, 4))
               .add("."))
    elif vol_moves:
        f, now, was, chg = vol_moves[0]
        (origin.add(f"Vols: `{f}` moved most, ")
               .claim(now, tc_a, style.pc4)
               .add(" from ")
               .claim(was, tc_pa, style.pc4)
               .add(". Correlations: no prior matrix to compare."))
    else:
        f = max(vols_now, key=lambda k: vols_now[k])
        (origin.add(f"Vols: first month-end on this pair — largest is `{f}` "
                    "at ")
               .claim(vols_now[f], tc_a, style.pc4)
               .add(". Correlations: no prior. Matrix attached."))

    if vol_moves:
        risers = [m for m in vol_moves[:6] if m[3] > 0]
        fallers = [m for m in vol_moves[:6] if m[3] < 0]
        if risers:
            origin.add(f"\n- Up: {', '.join('`' + m[0] + '`' for m in risers[:3])}"
                       f" — this month entering the {window}-day window.")
        if fallers:
            origin.add(f"\n- Down: "
                       f"{', '.join('`' + m[0] + '`' for m in fallers[:3])}"
                       " — noisier days leaving it.")
    if big_movers:
        names = ", ".join(f"`{m[0]}`/`{m[1]}`" for m in big_movers[:3])
        tightening = sum(1 for m in big_movers if m[4] > 0) >= \
            sum(1 for m in big_movers if m[4] < 0)
        origin.add(f"\n- Correlation movers: {names} — "
                   + ("rising, so a smaller diversification benefit."
                      if tightening else
                      "falling, so a wider diversification benefit."))
    elif movers:
        origin.add("\n- No cell moved more than a few points; the "
                   "diversification benefit carries over on shape.")

    if flagged:
        series, t, assumed, recomputed, tc_r, _rel = max(flagged,
                                                         key=lambda x: x[5])
        gate_field = f"vols.{series}.{t}"
        rationale = (f"{afile}: {gate_field} = {assumed} disagrees with the "
                     f"deterministic recomputation {recomputed} from source "
                     f"data ({window}-day window). Rerun with the recomputed "
                     "value.")
        _, _gate = s.call("propose_rerun", asof=asof,
                          adjustments_json={gate_field: recomputed},
                          rationale=rationale)
        (origin.add(f"\n- **FLAG — `{afile}`** `{gate_field}`: file carries ")
               .claim(assumed, tc_a, style.pc4)
               .add(", recomputation from source gives ")
               .claim(recomputed, tc_r, style.pc4)
               .add(". **Corrected rerun proposed** through the human "
                    "gate."))

    # The working page gets its OWN session: bind_post attaches a session's
    # tool calls to the post that binds it, and the feed post has to be the
    # one carrying the assumptions read the ATTACHMENT came from. Both
    # sessions run the same recomputation, so the table's figures cite
    # their own recorded calls rather than the feed post's.
    ws = ctx.session()
    tc_wa, _wa = ws.call("read_assumptions", asof_or_run=ctx.curr_run["id"])
    work = Prose()
    work.add(f"Vol recomputation, `{afile}` vs calibration methodology, "
             f"asof {asof}. Silence in the post above means this table "
             "reconciled.\n\n| series | tenor | assumed | recomputed | "
             "status |\n|---|---|---|---|---|\n")
    for series, t, assumed, _recomputed, _tc_r, _rel in results:
        tc_wr, wr = ws.call("recompute_vol", series=series, column=f"t{t}",
                            asof=asof, window_days=window)
        recomputed = float(wr["vol_annualized"])
        rel = abs(assumed - recomputed) / max(recomputed, 1e-12)
        status = "**DISCREPANT**" if rel > VOL_FLAG_REL else "ok"
        (work.add(f"| {series} | {t}y | ")
             .claim(assumed, tc_wa, style.pc4)
             .add(" | ")
             .claim(recomputed, tc_wr, style.pc4)
             .add(f" | {status} |\n"))
    work.add("\nRecomputed = stdev of daily changes over the stated window, "
             "annualised, exactly as calibration/calibrate.py. The full "
             "twenty-one-factor vol and correlation table is attached to "
             "the post above, read from the same assumptions file — same "
             "provenance, no second channel.\n\n"
             "Why the vols move at all: the calibration window is a rolling "
             "sample, so each month the newest days enter it and the oldest "
             "leave. A factor's vol rises when the month just gone was "
             "noisier than the month that dropped out, and falls when it "
             "was quieter — the level of the factor has nothing to do with "
             "it. Correlation cells move the same way, and it is the "
             "CROSS-BLOCK cells that matter: rising cross-block correlation "
             "shrinks the diversification benefit between the standalone "
             "blocks and the aggregate, falling correlation widens it. A "
             "flagged recomputation, when it happens, is a different animal "
             "entirely — it means the file disagrees with what the same "
             "methodology computes from the same source series, which is a "
             "defect rather than a market move.")

    sig = "critical" if flagged else ("notable" if big_movers else "routine")
    origin_draft = origin.draft("origin", session=s, significance=sig)
    origin_draft["attachment"] = _vcv_attachment(doc, prev_doc)
    return [origin_draft,
            work.draft("expansion", session=ws, significance=sig)]


# --------------------------------------------------------------------------

def holdings(ctx) -> list[dict]:
    s = ctx.session()
    tc_b, b = s.call("read_book", asof_or_run=ctx.curr_run["id"])
    bfile = b["file"]
    tc_ref, ref = s.call("read_reference", filename="ratings_ref.csv")
    ref_by_isin = {r["isin"]: r for r in ref["rows"]}

    corp = [p for p in b["data"]["positions"] if p.get("rating")]
    mismatches, rich_coupons, rows = [], [], []
    for p in corp:
        rr = ref_by_isin.get(p.get("isin"))
        expected = _bucket(rr["agency_rating"]) if rr else None
        agree = (expected == p["rating"]) if expected else None
        coupon_rich = float(p.get("coupon", 0)) > _COUPON_CEILING.get(
            p["rating"], 1.0)
        rows.append((p, rr, expected, agree, coupon_rich))
        if agree is False:
            mismatches.append(rows[-1])
        if coupon_rich:
            rich_coupons.append(rows[-1])

    # HOUSE STYLE (§12): what changed in the book, or one line saying
    # nothing did. The evidence and the full reconciliation are below.
    origin = Prose()
    if mismatches:
        p, rr, expected, _, coupon_rich = mismatches[0]
        (origin.add(f"**FLAG — `{bfile}`**: {p['id']} {p['name']} "
                    f"({p['isin']}) is mis-bucketed.")
               .add(f"\n- Booked `rating: {p['rating']}`; the reference "
                    f"gives {rr['issuer']} as {rr['agency_rating']} → "
                    f"**{expected} bucket**.")
               .add("\n- Coupon ")
               .claim(float(p["coupon"]), tc_b, style.pc)
               .add(f" is far rich for the {p['rating']} bucket.")
               .add(f"\n- Priced on the {p['rating']} spread level: value "
                    f"overstated, spread risk out of the {expected} factor.")
               .add(f"\n- Recommend re-bucketing to {expected} and "
                    "re-running."))
    else:
        hy = [r for r in rows if r[0]["rating"] == "HY"]
        example = max(hy, key=lambda r: float(r[0].get("coupon", 0))) if hy \
            else rows[0]
        p = example[0]
        (origin.add("Position hygiene clean — nothing in the book changed "
                    "hands with the reference this month.")
               .add("\n- Every rating bucket agrees with the public "
                    "issuer-rating reference.")
               .add(f"\n- Richest coupon, {p['name']}, at ")
               .claim(float(p["coupon"]), tc_b, style.pc)
               .add(f": consistent with its {p['rating']} bucket."))

    work = Prose()
    work.add("Bucket mapping, not notches: the reference gives an agency "
             "rating per ISIN and this check maps it into the book's four "
             "buckets before comparing, so a one-notch difference inside a "
             "bucket is not a finding. A coupon far rich to its bucket is "
             "corroboration, not the primary route — a genuine name in that "
             "bucket does not pay anywhere near it, so the pairing of a "
             "mis-bucketed rating with an implausible coupon is what makes "
             "a mis-bucketing legible rather than arguable.\n\n"
             f"Rating-bucket reconciliation, `{bfile}` vs "
             "`ratings_ref.csv`.\n\n| id | name | coupon | book | reference "
             "| verdict |\n|---|---|---|---|---|---|\n")
    for p, rr, expected, agree, coupon_rich in rows:
        verdict = ("no reference row" if agree is None else
                   "ok" if agree and not coupon_rich else
                   "**MIS-BUCKETED**" if not agree else "coupon rich")
        reftxt = f"{rr['agency_rating']} → {expected}" if rr else "—"
        (work.add(f"| {p['id']} | {p['name']} | ")
             .claim(float(p["coupon"]), tc_b, style.pc)
             .add(f" | {p['rating']} | {reftxt} | {verdict} |\n"))

    sig = "critical" if mismatches else "quiet"
    drafts = [origin.draft("origin", session=None, significance=sig),
              work.draft("expansion", session=s, significance=sig)]
    drafts.extend(_pc_proxy_drafts(ctx, s, b, tc_b))
    return drafts


def _pc_proxy_drafts(ctx, s, b, tc_b) -> list[dict]:
    """@holdings duty: the private-credit funds' SELECTED PROXY RATING
    must sit inside the strategy's acceptable band
    (scenarios/reference/pc_proxy_ref.csv). A proxy outside the band — e.g.
    CCC for performing loan strategies — reprices the sleeve at a distressed
    spread: the direction of the mis-statement is quantified and named."""
    bfile = b["file"]
    pcs = [p for p in b["data"]["positions"]
           if p.get("asset_class") == "private_credit"]
    if not pcs:
        return []
    tc_ref, ref = s.call("read_reference", filename="pc_proxy_ref.csv")
    by_strategy = {r["strategy"]: r for r in ref["rows"]}
    bad = []
    for p in pcs:
        row = by_strategy.get(str(p.get("strategy")))
        if row is None:
            continue
        acceptable = [x.strip() for x in
                      str(row["acceptable_proxy_ratings"]).split(";")]
        if p.get("rating") not in acceptable:
            bad.append((p, row, acceptable))
    if not bad:
        return []  # in-band proxies are covered by the hygiene one-liner

    tc_a, a = s.call("read_assumptions", asof_or_run=ctx.curr_run["id"])
    doc = a["data"]
    seeded_r = str(bad[0][0].get("rating"))
    ref_r = str(by_strategy[str(bad[0][0]["strategy"])]["typical_proxy"])
    lvl_bad = float(doc["spreads"].get(seeded_r, 0.0))
    lvl_ref = float(doc["spreads"].get(ref_r, 0.0))
    vol_bad = float(doc["vols"]["spread"].get(seeded_r, 0.0))
    vol_ref = float(doc["vols"]["spread"].get(ref_r, 0.0))
    tc_lvl, v_lvl = s.call("verify_claim", left=lvl_bad, op="gt",
                           right=lvl_ref, tol=0.0)
    tc_vol, v_vol = s.call("verify_claim", left=vol_bad, op="gt",
                           right=vol_ref, tol=0.0)

    names = ", ".join(f"{p['id']} ({p['strategy']})" for p, _, _ in bad)
    flag = Prose()
    (flag.add(f"**FLAG — `{bfile}`**: the private credit funds are proxied "
              f"`{seeded_r}`, outside the band in `pc_proxy_ref.csv`.")
         .add(f"\n- Affected: {names}.")
         .add(f"\n- Performing loan strategies; the band tops out at "
              f"{ref_r}. `{seeded_r}` prices the sleeve as distressed.")
         .add(f"\n- {seeded_r} spread ")
         .claim(lvl_bad, tc_a, style.pc)
         .add(" sits ")
         .claim(v_lvl["difference"], tc_lvl, style.signed_bp)
         .add(f" above {ref_r} at ")
         .claim(lvl_ref, tc_a, style.pc)
         .add("; vol ")
         .claim(vol_bad, tc_a, style.pc4)
         .add(" is ")
         .claim(v_vol["ratio"], tc_vol, lambda v: f"{v:,.2f}x")
         .add(f" the {ref_r} vol ")
         .claim(vol_ref, tc_a, style.pc4)
         .add(".")
         .add("\n- Spread risk and credit block VaR **overstated**, NAV "
              "marked down. Recommend reverting and re-running."))
    work = Prose()
    work.add(
        "Why a proxy rating outside the strategy's band is a mis-pricing "
        "and not a conservatism.\n\n"
        f"`pc_proxy_ref.csv` holds, per strategy, the band of proxy ratings "
        "a fund of that kind can defensibly be marked against, and a "
        "typical proxy inside it. The funds affected here — "
        f"{names} — are performing loan strategies, so the acceptable band "
        f"tops out at {ref_r}. Marking them `{seeded_r}` reprices them at a "
        "distressed spread, and the mis-statement runs in two directions "
        "at once.\n\n"
        "1. The sleeve's NAV is marked DOWN, because the discount spread "
        "applied to its cashflows is far wider than the strategy warrants.\n"
        "2. The sleeve's RISK is marked UP, because spread risk scales with "
        "the level under the floored-normal dynamics — a wider base level "
        "carries a bigger absolute move at the 99.5th percentile, and the "
        "proxy's own vol is the higher one too.\n\n"
        "The second is the point: this is an overstatement of risk, not "
        "prudence. A control that overstates is as wrong as one that "
        "understates, and it is harder to argue with, which is why it "
        "survives. Recommend reverting the proxy to the "
        "strategy-consistent rating and re-running.")
    return [flag.draft("origin", session=s, significance="critical"),
            work.draft("expansion", session=None, significance="critical")]


# --------------------------------------------------------------------------

def red_team_opening(ctx) -> list[dict]:
    """SPEC-APP H.1: the opening half of @red-team's two-pass cycle — runs
    with the room-1 pass, before the model, reading room:1 (topologically
    ordered last among the room-1 checks so the other agents' findings for
    this run already exist to read). The closing half (`red_team_closing`,
    room3.py) runs after rooms 2 and 3 and is the last voice in the
    cycle."""
    s = ctx.session()
    tc_a, a = s.call("read_assumptions", asof_or_run=ctx.curr_run["id"])
    doc = a["data"]
    window = int(doc["meta"].get("calibration_window_days", 504))
    wstart = str(doc["meta"].get("window_start", ""))

    # reads_from = ["room:1"]: pull in what the room already found this
    # pass, so the challenge can note whether anything upstream was flagged
    # rather than duplicating it (SPEC-APP H — provenance still binds to
    # the ORIGINAL tool_call if any of it is quoted numerically; here it is
    # only referenced qualitatively, so nothing is re-cited).
    sources: list[int] = []
    flagged_elsewhere = False
    for handle in ("@pre-flight-checks", "@vcv", "@holdings"):
        try:
            _, res = s.call("read_agent_posts", room=1, handle=handle)
        except ToolError:
            continue
        for p in res["posts"]:
            sources.append(p["id"])
            if "FLAG" in (p["body_md"] or ""):
                flagged_elsewhere = True

    # HOUSE STYLE (PENDING-BATCH2 §12): TWO challenges, the two that
    # actually bite this month, each stated sharply. The remaining standing
    # limitations — normal dynamics, one spread level per rating, equity by
    # index proxy, deterministic liabilities — are carried in the working
    # below rather than dropped. Six every month and nobody reads me by the
    # second month; two is enough to be worth reading and few enough to act
    # on.
    origin = Prose()
    (origin.add("Opening challenge — the two gaps that bite this month.")
           .add("\n- **The window.** Vols and correlations come from a ")
           .claim(window, tc_a, text=str(window))
           .add(f"-day sample starting {wstart}: it excludes the 2022 "
                "gilt/LDI episode, the last genuine tail in this market.")
           .add("\n- **The private-credit proxy.** A fixed-rate bond stands "
                "in for floating-rate loans: rate risk is overstated, NAV "
                "smoothing understates its true volatility. Two biases, "
                "opposite directions."))
    if flagged_elsewhere:
        origin.add("\n- An input defect is already flagged in this room, so "
                   "everything below it is provisional.")
    origin.add("\n- Four further standing limitations in the working. "
               "None of this blocks the run.")

    # The working page gets its OWN session, so the backing page carries its
    # own recorded read rather than borrowing the feed post's provenance.
    ws = ctx.session()
    tc_wa, _wa = ws.call("read_assumptions", asof_or_run=ctx.curr_run["id"])
    work = Prose()
    work.add("The calibration window this challenge is aimed at: ")
    work.claim(window, tc_wa, text=str(window))
    work.add(f" days from {wstart}.\n\n")
    work.add(
        "The standing limitations, in full. Two of them bite hard enough "
        "this month to be worth a reader's attention and they are in the "
        "post above; the rest are structural, they apply every month, and "
        "they belong on the slide next to the headline number.\n\n"
        "**(i) The window.** A two-year trailing sample cannot contain a "
        "regime it does not reach. The tail we capitalise against is "
        "estimated from days that exclude the last genuine tail event in "
        "this market, and a stressed-window overlay would not calibrate to "
        "it.\n\n"
        "**(ii) The private-credit proxy.** The funds are modelled as "
        "synthetic fixed-rate bonds. The fixed coupon adds govy-curve "
        "duration a floating-rate loan book largely does not have, so the "
        "sleeve's RATE risk is overstated; NAV smoothing and valuation lag "
        "understate its true volatility. Two biases in opposite directions, "
        "neither measured, so nothing but luck makes them cancel.\n\n"
        "**(iii) Normal dynamics.** One-step Gaussian factors with a "
        "post-hoc spread floor — no fat tails, no vol-of-vol. The true "
        "one-in-two-hundred is likely worse than the modelled one.\n\n"
        "**(iv) One spread level per rating.** USD OAS applied to both "
        "currencies with a fixed term profile: GBP-specific spread stress "
        "and credit curve-shape risk are structurally invisible.\n\n"
        "**(v) Equity by index proxy.** Single-name and concentration risk "
        "in the equity book never reaches the VCV.\n\n"
        "**(vi) Deterministic liabilities.** The reserve book is fixed "
        "claims-payment cashflows: no longevity, no claims or inflation "
        "risk, no cat model. A large-loss event has no channel into this "
        "framework at all.\n\n"
        "I challenge; I do not block.")
    sig = "notable" if flagged_elsewhere else "routine"
    return [origin.draft("origin", session=s, significance=sig,
                         sources=sources),
            work.draft("expansion", session=ws, significance=sig)]


# --------------------------------------------------------------------------

def _fr_rows(doc: dict, stats: dict) -> list[dict]:
    """All base levels: assumptions (decimals/levels) vs the research
    note's month-end values (percent for rates/spreads, levels otherwise)."""
    rows: list[dict] = []
    for series in CURVES:
        for t in TENORS:
            a = float(doc["curves"][series][t])
            rp = float(stats[series]["columns"][f"t{t}"]["level_pct"])
            rows.append({"label": f"{series} {t}y", "kind": "rate",
                         "assumed": a, "research": rp,
                         "dev_bp": abs(a * 1e4 - rp * 100.0)})
    for rating in SPREAD_RATINGS:
        a = float(doc["spreads"][rating])
        rp = float(stats["spread"]["columns"][rating]["level_pct"])
        rows.append({"label": f"spread {rating}", "kind": "rate",
                     "assumed": a, "research": rp,
                     "dev_bp": abs(a * 1e4 - rp * 100.0)})
    for idx in EQUITY_INDICES:
        a = float(doc["equity"][idx])
        rl = float(stats["equity"]["columns"][idx]["level"])
        rows.append({"label": f"equity {idx}", "kind": "level",
                     "assumed": a, "research": rl,
                     "dev_rel": abs(a - rl) / max(abs(rl), 1e-12)})
    a = float(doc["fx"]["GBPUSD"])
    rl = float(stats["fx"]["columns"]["GBPUSD"]["level"])
    rows.append({"label": "fx GBPUSD", "kind": "level", "assumed": a,
                 "research": rl, "dev_rel": abs(a - rl) / max(abs(rl), 1e-12)})
    for r in rows:
        r["ok"] = (r["dev_bp"] <= RESEARCH_RATE_TOL_BP if r["kind"] == "rate"
                   else r["dev_rel"] <= RESEARCH_LEVEL_TOL)
    return rows


_FR_PRESSURE_TXT = {
    "up": "entering the calibration window it pulls the calibrated vol up",
    "down": "entering the calibration window it drags the calibrated vol down",
    "in line": ("entering the calibration window it leaves the calibrated "
                "vol roughly where it was"),
    "n/a": "there is too little history this month for a vol read",
}

# The room-1 post names the input field each move actually lands in — that
# is the whole content of the post (PENDING-BATCH2 §2: "here's what I said
# in my report, and here's where it shows up in the inputs").
_FR_MOVE_GROUPS = [
    ("rates", [(f"{curve} {t}y", curve, f"t{t}", "rate",
                f"curves.{curve}.{t}")
               for curve in CURVES for t in TENORS]),
    ("credit", [(f"{r} OAS", "spread", r, "rate", f"spreads.{r}")
                for r in SPREAD_RATINGS]),
    ("equity/fx", [(i, "equity", i, "level", f"equity.{i}")
                   for i in EQUITY_INDICES]
     + [("GBPUSD", "fx", "GBPUSD", "level", "fx.GBPUSD")]),
]


def _fr_material_moves(doc: dict, stats: dict) -> list[dict]:
    """The month's material moves — the biggest in each of rates, credit and
    equity/FX, so the post reads across the book rather than three times down
    one curve. Deterministic: largest absolute month change per group, ties
    broken by declaration order."""
    out: list[dict] = []
    for _group, candidates in _FR_MOVE_GROUPS:
        best = None
        for label, block, col, kind, field in candidates:
            st = stats[block]["columns"][col]
            size = abs(float(st["change_bp"] if kind == "rate"
                             else st["change_pct"]))
            if best is None or size > best[0]:
                best = (size, label, block, col, kind, field, st)
        if best is None:
            continue
        _size, label, block, col, kind, field, st = best
        if kind == "rate":
            chg, chg_txt = st["change_bp"], f"{st['change_bp']:+.1f}bp"
        else:
            chg, chg_txt = st["change_pct"], f"{st['change_pct']:+.2f}%"
        assumed = _fr_assumed(doc, field)
        out.append({"label": label, "block": block, "col": col, "kind": kind,
                    "field": field, "change": chg, "change_txt": chg_txt,
                    "pctl": st.get("move_percentile"), "assumed": assumed,
                    "pressure": st.get("vol_pressure", "n/a")})
    return out


def _fr_assumed(doc: dict, field: str):
    """The assumptions-file value the move lands in — `curves.gbp_gilt.10`,
    `spreads.HY`, `equity.SX5E`, `fx.GBPUSD`."""
    parts = field.split(".")
    node = doc
    for p in parts:
        key = int(p) if p.isdigit() else p
        node = node[key]
    return float(node)


def _fr_moves_narration(origin: Prose, moves: list[dict], tc_a: int,
                        tc_r: int) -> None:
    """One bullet per move: figure, where it lands, verdict. The FIRST
    bullet carries the percentile and the vol-pressure read in full; the
    rest are fragments, and the whole picture is in the working (§12)."""
    for i, m in enumerate(moves):
        origin.add(f"\n- {m['label']} ").claim(m["change"], tc_r,
                                               text=m["change_txt"])
        if i == 0 and m["pctl"] is not None:
            (origin.add(", ")
                   .claim(m["pctl"], tc_r, text=ordinal(m["pctl"]))
                   .add(" percentile of the trailing two years"))
        origin.add(f" — `{m['field']}` at ")
        if m["kind"] == "rate":
            origin.claim(m["assumed"], tc_a, style.pc4)
        else:
            origin.claim(m["assumed"], tc_a, text=f"{m['assumed']:,.4f}")
        if i == 0:
            origin.add("; " + _FR_PRESSURE_TXT.get(m["pressure"],
                                                   _FR_PRESSURE_TXT["n/a"]))
        origin.add(".")


def _fr_moves_working(work: Prose, moves: list[dict], tc_r: int) -> None:
    """The move-by-move read that used to sit in the feed post: percentile
    against the factor's own two years, and what each move does to the
    calibrated vol as it enters the window."""
    if not moves:
        return
    work.add("\nWhat each of the month's material moves does to the "
             "calibration as it enters the window:\n\n")
    for m in moves:
        work.add(f"- {m['label']} ").claim(m["change"], tc_r,
                                           text=m["change_txt"])
        if m["pctl"] is not None:
            (work.add(", ")
                 .claim(m["pctl"], tc_r, text=ordinal(m["pctl"]))
                 .add(" percentile of the trailing two years"))
        work.add(" — " + _FR_PRESSURE_TXT.get(m["pressure"],
                                              _FR_PRESSURE_TXT["n/a"])
                 + ".\n")


# @focused's independently web-sourced levels ↔ the assumptions field each
# should match. Rates compared in %, levels/FX as-is. Tolerances are looser
# than the file reconciliation's 1bp: a web-sourced month-end level is an
# independent human-published figure, not the same series to the basis point.
_INDEP_MAP = {
    "gilt_10y": (("curves", "gbp_gilt", 10), 100.0, "UK 10y gilt", "rate"),
    "ust_10y":  (("curves", "ust", 10),      100.0, "US 10y Treasury", "rate"),
    "ftse100":  (("equity", "FTSE100"),        1.0, "FTSE 100", "level"),
    "sp500":    (("equity", "SP500"),          1.0, "S&P 500", "level"),
    "sx5e":     (("equity", "SX5E"),           1.0, "EURO STOXX 50", "level"),
    "gbpusd":   (("fx", "GBPUSD"),             1.0, "GBP/USD", "level"),
}
_INDEP_RATE_TOL_BP = 10.0     # rates: 10bp
_INDEP_LEVEL_TOL = 0.01       # levels/FX: 1%


def _fr_independent_gaps(stats: dict, doc: dict) -> list[dict]:
    """Compare @focused's independently web-sourced levels against the
    matching assumptions field. Only factors that were sourced AND map to an
    input are returned."""
    levels = (stats.get("meta") or {}).get("independent_levels") or {}
    out: list[dict] = []
    for key, rec in levels.items():
        if key not in _INDEP_MAP or not isinstance(rec, dict):
            continue
        val = rec.get("value")
        if not isinstance(val, (int, float)):
            continue
        path, scale, label, kind = _INDEP_MAP[key]
        node = doc
        try:
            for p in path:
                node = node[p]
            inp = float(node) * scale                 # assumptions, in the
        except (KeyError, TypeError, ValueError):      # sourced factor's unit
            continue
        sourced = float(val)
        if kind == "rate":
            gap_bp = abs(inp - sourced) * 100.0        # both in %
            material = gap_bp > _INDEP_RATE_TOL_BP
            rel = gap_bp / _INDEP_RATE_TOL_BP
            inp_txt, src_txt = f"{inp:.2f}%", f"{sourced:.2f}%"
        else:
            rel_gap = abs(inp - sourced) / max(abs(sourced), 1e-9)
            material = rel_gap > _INDEP_LEVEL_TOL
            rel = rel_gap / _INDEP_LEVEL_TOL
            inp_txt, src_txt = f"{inp:,.2f}", f"{sourced:,.2f}"
        out.append({"key": key, "label": label, "kind": kind,
                    "input": inp, "sourced": sourced, "material": material,
                    "rel": rel, "input_txt": inp_txt, "sourced_txt": src_txt,
                    "url": str(rec.get("source_url") or "")})
    return out


def focused(ctx) -> list[dict]:
    """@focused in room 1 (PENDING-BATCH2 §2). Light context on the model
    inputs, drawn from the research report this agent produced in the
    research stage: two or three of the month's material moves, each tied to
    the input field that carries it, plus the base-level cross-check against
    the note (one basis point on rates and spreads, a tenth of a percent on
    levels) that catches the wrong-file / stale-snapshot / fat-fingered class.

    The post is the SMALLEST in the room and the register is conversational —
    it points, it does not police. The reconciliation table itself lives in
    the working, and the report it is drawn from is cited by a read_research
    call so the source chip links straight to it."""
    s = ctx.session()
    tc_a, a = s.call("read_assumptions", asof_or_run=ctx.curr_run["id"])
    doc, afile = a["data"], a["file"]
    tc_r, r = s.call("read_research", asof=ctx.curr_month)
    stats, rfile = r["stats"], r["file"]

    rows = _fr_rows(doc, stats)
    mismatches = [x for x in rows if not x["ok"]]
    moves = _fr_material_moves(doc, stats)

    origin = Prose()
    if mismatches:
        w = max(mismatches,
                key=lambda x: (x["dev_bp"] / RESEARCH_RATE_TOL_BP
                               if x["kind"] == "rate"
                               else x["dev_rel"] / RESEARCH_LEVEL_TOL))
        if w["kind"] == "rate":
            tc_v, v = s.call(
                "verify_claim", left=w["assumed"] * 1e4, op="approx",
                right=w["research"] * 100.0,
                tol=RESEARCH_RATE_TOL_BP / max(abs(w["research"]) * 100.0,
                                               1e-9))
            (origin.add(f"**FLAG — `{afile}`** vs the independent research "
                        f"note (`{rfile}`): {w['label']} is booked at ")
                   .claim(w["assumed"], tc_a, style.pc4)
                   .add(" but the note — computed directly from the source "
                        "series — has the month-end at ")
                   .claim(w["research"], tc_r, text=f"{w['research']:.4f}%")
                   .add(", a gap of ")
                   .claim(v["difference"], tc_v,
                          text=f"{v['difference']:+.1f}bp")
                   .add(" against a tolerance of one basis point."))
        else:
            tc_v, v = s.call("verify_claim", left=w["assumed"], op="approx",
                             right=w["research"], tol=RESEARCH_LEVEL_TOL)
            (origin.add(f"**FLAG — `{afile}`** vs the independent research "
                        f"note (`{rfile}`): {w['label']} is booked at ")
                   .claim(w["assumed"], tc_a, text=f"{w['assumed']:,.4f}")
                   .add(" but the note — computed directly from the source "
                        "series — has the month-end at ")
                   .claim(w["research"], tc_r, text=f"{w['research']:,.4f}")
                   .add(", ")
                   .claim(v["rel_diff_pct"], tc_v,
                          text=f"{v['rel_diff_pct']:.2f}%")
                   .add(" apart, tolerance a tenth of a percent."))
        origin.add(" A wrong file or a stale snapshot, not a judgement "
                   "call.")
    else:
        origin.add(f"My {ctx.curr_month} research note is up (`{rfile}`) — "
                   "here is where this month shows up in the inputs.")
    _fr_moves_narration(origin, moves, tc_a, tc_r)
    if not mismatches:
        origin.add(f"\n\nBase levels in `{afile}` tie back to the note "
                   "inside tolerance — no wrong file, no stale snapshot. "
                   "Table in the working.")
    else:
        origin.add("\n\nRe-derive before anyone uses this run. Table in "
                   "the working.")

    # The working page gets its OWN session: bind_post attaches a session's
    # tool calls to the post that binds it, and the feed post must be the one
    # carrying the read_research chip that links to the report (PENDING-BATCH2
    # §2). Both sessions read the same two files, so the table's claims bind
    # to their own recorded calls.
    ws = ctx.session()
    tc_wa, _wa = ws.call("read_assumptions", asof_or_run=ctx.curr_run["id"])
    tc_wr, _wr = ws.call("read_research", asof=ctx.curr_month)

    work = Prose()
    work.add(f"Base-level reconciliation, `{afile}` vs `{rfile}` (research "
             "computed from data/processed/*.csv only; tolerance one basis "
             "point on rates and spreads, a tenth of a percent on equity "
             "and FX).\n\n| factor | assumed | research month-end | verdict "
             "|\n|---|---|---|---|\n")
    for row in rows:
        if row["kind"] == "rate":
            (work.add(f"| {row['label']} | ")
                 .claim(row["assumed"], tc_wa, style.pc4)
                 .add(" | ")
                 .claim(row["research"], tc_wr,
                        text=f"{row['research']:.4f}%"))
        else:
            (work.add(f"| {row['label']} | ")
                 .claim(row["assumed"], tc_wa, text=f"{row['assumed']:,.4f}")
                 .add(" | ")
                 .claim(row["research"], tc_wr,
                        text=f"{row['research']:,.4f}"))
        work.add(f" | {'ok' if row['ok'] else '**MISMATCH**'} |\n")
    work.add("\nAssumed values are decimals/levels from the assumptions "
             "file; research values are percent as published / levels from "
             "the note.\n\n"
             "Why this comparison catches a class of error rather than a "
             "single one: the research note and the calibration read the "
             "same source files down completely separate paths, so a "
             "disagreement between them cannot be a modelling judgement. It "
             "is a wrong file, a stale snapshot, or a fat-fingered edit "
             "between the two — which is exactly the class that survives "
             "every plausibility check, because a stale number is a "
             "perfectly plausible number. I point; I do not police: the "
             "field-by-field verdict is @pre-flight-checks' job, and this "
             "line is a pointer to it.\n")
    _fr_moves_working(work, moves, tc_wr)

    # INDEPENDENT WEB CHECK. @focused re-sourced the input factors from the
    # open web itself (in the research stage); here that independent read is
    # compared against the assumptions. Unlike the reconciliation above —
    # note vs assumptions, both off the same source files — this reaches the
    # figure by a genuinely different route, so a gap is real evidence.
    indep = _fr_independent_gaps(stats, doc)
    indep_material = False
    if indep:
        worst = max(indep, key=lambda x: x["rel"])
        n_ok = sum(1 for x in indep if not x["material"])
        indep_material = worst["material"]
        origin.add(f"\n\n**Independent web check.** I re-sourced {len(indep)} "
                   "input factors from primary web sources myself — these did "
                   "not come through the model's data pipeline. ")
        if worst["material"]:
            tc_iv, iv = s.call(
                "verify_claim", left=worst["input"], op="approx",
                right=worst["sourced"],
                tol=(_INDEP_RATE_TOL_BP / 100.0 if worst["kind"] == "rate"
                     else _INDEP_LEVEL_TOL * abs(worst["sourced"])))
            (origin.add(f"{n_ok} of {len(indep)} agree with the inputs. "
                        f"**FLAG — {worst['label']}**: assumptions carry ")
                   .claim(worst["input"], tc_a, text=worst["input_txt"])
                   .add(", my independent source has ")
                   .claim(worst["sourced"], tc_r, text=worst["sourced_txt"]))
            if worst["url"].startswith("http"):
                origin.add(f" ([source]({worst['url']}))")
            origin.add(" — a real gap, not a shared-source artefact. Worth a "
                       "look before this run is used.")
        else:
            (origin.add(f"all {len(indep)} agree with the inputs within "
                        "tolerance — the widest is ")
                   .claim(worst["sourced"], tc_r, text=worst["sourced_txt"])
                   .add(f" for {worst['label']}, matching the assumptions. "
                        "Independent confirmation, not just internal "
                        "consistency."))

    sig = "critical" if (mismatches or indep_material) else "routine"
    return [origin.draft("origin", session=s, significance=sig),
            work.draft("expansion", session=ws, significance=sig)]
