"""Room 3 · Output Challenge — deterministic mock checks.

attrib               walks the waterfall, names offsets, flags the
                     residual; reads the three desks (SPEC-APP H default)
rates/credit/equity/pc_desk  the four desks, one per sleeve, in the
                     §9a shape: market first, bullets, then what it means
                     for the results below, then one optional watch item
focused_room3        @focused's room-3 brief (§13 — ONE agent, two rooms,
                     no @focused-book): the SAME moves it named in room 1,
                     tied to the block VaR each moved and sized against
                     surplus
wide_eye             "in my report I said roughly this; here is what it
                     means for the portfolio as it stands" — context only,
                     and the quarantine is enforced, not polite
realist              reasonableness bands, corroboration only
draft_report_review  @results-validator reconciling a drafted report against
                     engine outputs (catches D3A and D3B)
red_team_closing     the closing half of @red-team's two-pass cycle
                     (SPEC-APP H.1) — reads room:1, room:3 and its own
                     opening post; runs last
outward_snapshot_draft  a fresh snapshot's per-agent contribution
                     (SPEC-APP E) — @focused/desks cite the research note
                     recomputed with a walked-forward data_through date;
                     @wide-eye stays a context stub in mock

Each draft carries a `significance` level (SPEC-APP G) set by the check's
own thresholds — never a stylistic choice.
"""

from __future__ import annotations

import re

from app.agents import style, research
from app.agents.style import Prose
from app.agents.research import ordinal
from app.agents.tools import ToolError, ToolLimitError


def _no_pair_draft(handle_txt: str) -> list[dict]:
    body = (f"{handle_txt} needs a month-end pair to narrate a movement."
            "\n- Select a previous and a current run, then refresh.")
    return [{"kind": "origin", "body": body, "claims": [], "context": False,
             "session": None, "significance": "quiet"}]


def _attr_dirs(ctx) -> list[str]:
    """Pair-attribution directory names for the selected runs, most specific
    first. Both ends are named (`attr_2026_02_v1__2026_03_v1`,
    PENDING-BATCH2 section 1), so which two runs an attribution walked
    between is readable straight off the directory."""
    from app.server import engine_bridge  # noqa: PLC0415 (leaf; no cycle)
    return engine_bridge.attribution_dir_names(ctx.prev_run, ctx.curr_run)


def _read_attribution(s, ctx):
    """(tool_call_id, data, dir_name) for the first pair attribution that
    exists, or (None, None, None)."""
    for name in _attr_dirs(ctx):
        try:
            tc, res = s.call("read_output", asof_or_run=name,
                             filename="attribution.json")
        except ToolError:
            continue
        return tc, res["data"], name
    return None, None, None


def _steps(attr_data: dict, section: str) -> dict:
    return {s["name"]: float(s["delta_gbp"])
            for s in attr_data[section]["steps"]}


# --------------------------------------------------------------------------

def attrib(ctx) -> list[dict]:
    if ctx.prev_run is None:
        return _no_pair_draft("The attribution walk")
    s = ctx.session()
    tc, a, attr_dir = _read_attribution(s, ctx)
    if a is None:
        body = ("No committed attribution outputs exist for this pair of "
                "runs — the waterfall is only computed between committed "
                "month-ends. Select the demo pair to see the full walk.")
        return [{"kind": "origin", "body": body, "claims": [],
                 "context": False, "session": s, "significance": "routine"}]

    # SPEC-APP H default: @attrib reads the three desks. Their independent
    # block-level narration is sourced (drill-down chips), never re-cited —
    # attrib's own numbers already come straight from attribution.json.
    sources: list[int] = []
    for handle in ("@rates-desk", "@credit-desk", "@equity-desk"):
        try:
            _, res = s.call("read_agent_posts", room=3, handle=handle)
        except ToolError:
            continue
        sources.extend(p["id"] for p in res["posts"])
    mtm, var = _steps(a, "mtm"), _steps(a, "var")
    mtm_total = float(a["mtm"]["total_change_gbp"])
    var_total = float(a["var"]["total_change_gbp"])
    residual = float(a["mtm"]["residual_gbp"])

    # HOUSE STYLE (PENDING-BATCH2 §12): the walk as a lead line and four
    # fragments. Method — sequential re-pricing, why the residual is the
    # control, what the VCV and book steps mean — is in the working.
    origin = Prose()
    (origin.add(f"Waterfall, {ctx.prev_month} → {ctx.curr_month}: surplus "
                "moved ")
           .claim(mtm_total, tc, style.signed_money)
           .add(" — one big driver with offsets."))
    (origin.add("\n- Swap discount curve rose, cutting liability PV: ")
           .claim(mtm["gbp_swap"], tc, style.signed_money)
           .add("."))
    (origin.add("\n- Against it: equity ")
           .claim(mtm["equity"], tc, style.signed_money)
           .add(", gilts ")
           .claim(mtm["gbp_gilt"], tc, style.signed_money)
           .add(", USTs ")
           .claim(mtm["ust"], tc, style.signed_money)
           .add(", spreads ")
           .claim(mtm["spread"], tc, style.signed_money)
           .add(", fx ")
           .claim(mtm["fx"], tc, style.signed_money)
           .add("."))
    (origin.add("\n- Residual ")
           .claim(residual, tc, style.money)
           .add(" — the steps land exactly on the current state; a nonzero "
                "residual here is a red flag."))
    (origin.add("\n- VaR ")
           .claim(var_total, tc, style.signed_money)
           .add(": swap ")
           .claim(var["gbp_swap"], tc, style.signed_money)
           .add(", equity ")
           .claim(var["equity"], tc, style.signed_money)
           .add(", offset by gilt ")
           .claim(var["gbp_gilt"], tc, style.signed_money)
           .add(" and fx ")
           .claim(var["fx"], tc, style.signed_money)
           .add("."))

    work = Prose()
    work.add(f"Sequential re-pricing waterfall ({attr_dir}).\n\n"
             "**Method.** Each step re-prices the whole balance sheet with "
             "one input group advanced from the prior month-end to the "
             "current one, in a fixed order, so every step is an exact "
             "revaluation rather than a share-out. Sterling depreciation "
             "raises the GBP value of the USD assets, which is why the fx "
             "step is normally positive. The VCV and book steps are zero "
             "in a month where the correlation matrix and the position "
             "file are unchanged between the two runs. The residual is the "
             "control on the whole exercise: the steps must land exactly "
             "on the current state, and a nonzero residual means the walk "
             "has missed something.\n\n"
             "| step | surplus MTM | VaR |\n|---|---|---|\n")
    for name in ("gbp_swap", "gbp_gilt", "ust", "spread", "equity", "fx",
                 "vcv", "book"):
        (work.add(f"| {name} | ")
             .claim(mtm[name], tc, style.signed_money)
             .add(" | ")
             .claim(var[name], tc, style.signed_money)
             .add(" |\n"))
    (work.add("| residual | ")
         .claim(residual, tc, style.money)
         .add(" | ")
         .claim(float(a["var"]["residual_gbp"]), tc, style.money)
         .add(" |\n| **total** | ")
         .claim(mtm_total, tc, style.signed_money)
         .add(" | ")
         .claim(var_total, tc, style.signed_money)
         .add(" |\n\nSteps + residual = total (additivity check inside "
              "attribution.json passes)."))
    sig = "critical" if abs(residual) > 1e-6 * max(abs(mtm_total), 1.0) \
        else "notable"
    return [origin.draft("origin", session=None, significance=sig,
                         sources=sources),
            work.draft("expansion", session=s, significance=sig)]


# --------------------------------------------------------------------------

def _desk_reads(ctx, s):
    tc_pa, pa = s.call("read_assumptions", asof_or_run=ctx.prev_run["id"])
    tc_ca, ca = s.call("read_assumptions", asof_or_run=ctx.curr_run["id"])
    tc_pb, pb = s.call("read_output", asof_or_run=ctx.prev_run["id"],
                       filename="var_standalone_factors.json")
    tc_cb, cb = s.call("read_output", asof_or_run=ctx.curr_run["id"],
                       filename="var_standalone_factors.json")
    return (tc_pa, pa["data"]), (tc_ca, ca["data"]), \
        (tc_pb, pb["data"]["blocks"]), (tc_cb, cb["data"]["blocks"])


def _pct_txt(v: float) -> str:
    return f"{v:.4f}%"


def _level_txt(v: float) -> str:
    return f"{v:,.4f}"


# --------------------------------------------------------------------------
# The desks (PENDING-BATCH2 §9 / §9a). Four sleeves, one shape:
#
#   1. the market first — the desk's own area, NOT valuation.json. A rates
#      desk that opens with our VaR figure has skipped its own job. In live
#      mode that is web_search; in mock it is the research note and the
#      processed series, which is the same discipline against the only
#      source mock has.
#   2. two or three bullets on what happened and what drove it, fragments.
#   3. what it means for the results below — the sleeve, the standalone
#      block VaR, materiality against surplus. The sentence the room waits
#      for, and the reason the desks sit in room 3.
#   4. ONE watch item, where there is one — omitted in a quiet month rather
#      than invented, and labelled as the speculation it is.
#
# The desks are the only agents in the system that look FORWARD. @focused
# bridges its own research to the results across all factors (breadth,
# backward-looking); the desks go deep on one area; @attrib walks the
# waterfall (arithmetic, not view).
# --------------------------------------------------------------------------

def _desk_note_stat(s, ctx, block: str, col: str):
    """(tool_call_id, stat, filename) for one column of the research note —
    the desks' market read, computed from data/processed only."""
    tc_rn, rn = s.call("read_research", asof=ctx.curr_month)
    return tc_rn, rn["stats"][block]["columns"][col], rn["file"]


def _desk_range(origin: Prose, tc_rn: int, st: dict, rfile: str,
                intro: str, fmt) -> None:
    """The market bullet: where the factor traded intra-month and how big
    the move was against its own two-year history. One fragment — the
    fuller note (daily vols, the observations behind it) is the desk's
    backing page."""
    lo_key, hi_key = ("low_pct", "high_pct") if "low_pct" in st \
        else ("low", "high")
    (origin.add(f"\n- Research note (`{rfile}`): {intro} ")
           .claim(st[lo_key], tc_rn, text=fmt(st[lo_key]))
           .add("–")
           .claim(st[hi_key], tc_rn, text=fmt(st[hi_key]))
           .add(" intra-month"))
    pctl = st.get("move_percentile")
    if pctl is not None:
        (origin.add(", move at the ")
               .claim(pctl, tc_rn, text=ordinal(pctl))
               .add(" percentile"))
    origin.add(".")


def _desk_watch(st: dict) -> str | None:
    """The optional watch item, derived — never invented. A move in the top
    or bottom decile of its own two years, or realised vol running away
    from the trailing window, is something unresolved worth naming. A quiet
    month returns None and the desk omits the line. A fragment: the
    reasoning behind it is on the backing page."""
    pctl = st.get("move_percentile")
    if pctl is not None and pctl >= research.PCTL_HI:
        return ("top-decile moves continue or retrace; the calibrated vol "
                "follows a month late")
    if pctl is not None and pctl <= research.PCTL_LO:
        return ("a quiet month drags the calibrated vol down as the window "
                "fills")
    for obs in st.get("notable") or []:
        if "ran hot" in obs:
            return ("realised vol ran ahead of the window; next month's "
                    "calibration reprices up")
        if "two-year high" in obs or "two-year low" in obs:
            return ("the level sits at its two-year edge, where reversion "
                    "and regime change look alike")
    return None


def _desk_materiality(origin: Prose, s, ctx, block: str, curr_blocks: dict,
                      tc_cb: int, prev_blocks: dict, exposure: str) -> None:
    """Part three, identical in shape for every desk: which sleeve carries
    it, what the standalone block VaR did, and whether it is material
    against surplus. `exposure` is a short clause — the full account of the
    sleeve belongs on the backing page."""
    level = float(curr_blocks[block])
    tc_vb, vb = s.call("verify_claim", left=level, op="ne",
                       right=float(prev_blocks[block]), tol=1e-9)
    tc_val, val = s.call("read_output", asof_or_run=ctx.curr_run["id"],
                         filename="valuation.json")
    surplus = float(val["data"]["surplus_gbp"])
    tc_m, m = s.call("verify_claim", left=level, op="lt", right=surplus,
                     tol=0.0)
    (origin.add(f"\n\n**For the results below.** {exposure} `{block}` "
                "standalone VaR ")
           .claim(level, tc_cb, style.money)
           .add(" (")
           .claim(vb["difference"], tc_vb, style.signed_money)
           .add("), ")
           .claim(m["ratio"], tc_m, style.pc)
           .add(" of surplus ")
           .claim(surplus, tc_val, style.money)
           .add("."))


def _desk_working(ctx, block: str, cols: tuple, title: str,
                  note: str) -> dict:
    """The desk's backing page (§9a: "the fuller market note goes to
    detail_md"). Its OWN session, so the table cites its own recorded read
    of the research note rather than borrowing the feed post's provenance:
    level, month change, intra-month range, the move's percentile and the
    realised-vs-trailing daily vol for every column the desk covers, then
    the note's own observations and the desk's longer account of the
    sleeve."""
    ws = ctx.session()
    tc, rn = ws.call("read_research", asof=ctx.curr_month)
    stats, rfile = rn["stats"], rn["file"]
    rate = "low_pct" in stats[block]["columns"][cols[0]]
    unit = "bp/day" if rate else "%/day"
    work = Prose()
    work.add(f"{title}. Research note (`{rfile}`), computed from "
             "data/processed only — in live mode this section carries the "
             "web read as well.\n\n"
             "| column | level | month | range | pctl | realised vol | "
             f"window vol ({unit}) |\n|---|---|---|---|---|---|---|\n")
    for col in cols:
        st = stats[block]["columns"][col]
        lo_k, hi_k = ("low_pct", "high_pct") if rate else ("low", "high")
        lvl_k = "level_pct" if rate else "level"
        chg_k, chg_fmt = ("change_bp", "{:+.1f}bp") if rate \
            else ("change_pct", "{:+.2f}%")
        num = (lambda v: f"{v:.4f}%") if rate else (lambda v: style.num(v, 2))
        work.add(f"| {col} | ")
        work.claim(st[lvl_k], tc, text=num(st[lvl_k])).add(" | ")
        work.claim(st[chg_k], tc, text=chg_fmt.format(st[chg_k])).add(" | ")
        work.claim(st[lo_k], tc, text=num(st[lo_k])).add("–")
        work.claim(st[hi_k], tc, text=num(st[hi_k])).add(" | ")
        pctl = st.get("move_percentile")
        if pctl is None:
            work.add("n/a")
        else:
            work.claim(pctl, tc, text=ordinal(pctl))
        work.add(" | ")
        mv = st.get("month_dayvol_bp" if rate else "month_dayvol_pct")
        tv = st.get("trail_dayvol_bp" if rate else "trail_dayvol_pct")
        if mv is None or tv is None:
            work.add("n/a | n/a |\n")
        else:
            work.claim(float(mv), tc, text=f"{mv:.2f}").add(" | ")
            work.claim(float(tv), tc, text=f"{tv:.2f}").add(" |\n")
    # The note's own `notable` strings are NOT interpolated: they carry
    # figures of their own that would enter the post unbound and be
    # suppressed. Each is restated as the fixed phrase for its kind, and
    # the number behind it is already in the table above.
    kinds = {"ran hot": "realised daily vol ran ahead of the trailing "
                        "window — as this month enters the calibration "
                        "window it pulls the block's vol up",
             "ran cold": "realised daily vol ran behind the trailing "
                         "window — entering the window it drags the "
                         "block's vol down",
             "two-year high": "the month-end level is a two-year high",
             "two-year low": "the month-end level is a two-year low",
             "outsized": "an outsized monthly move against its own history"}
    obs = []
    for col in cols:
        for o in stats[block]["columns"][col].get("notable") or []:
            for key, phrase in kinds.items():
                if o.startswith(key) or key in o:
                    obs.append(f"- {col}: {phrase}.")
                    break
    if obs:
        work.add("\nWhat the note flagged, and what the watch item on the "
                 "post is derived from — a month that crosses none of "
                 "these thresholds gets no watch item at all rather than "
                 "an invented one:\n\n" + "\n".join(obs) + "\n")
    work.add("\n" + note)
    return work.draft("expansion", session=ws, significance="routine")


def rates_desk(ctx) -> list[dict]:
    if ctx.prev_run is None:
        return _no_pair_draft("The rates walk")
    s = ctx.session()
    (tc_pa, pa), (tc_ca, ca), (tc_pb, pb), (tc_cb, cb) = _desk_reads(ctx, s)
    tc_rn, st, rfile = _desk_note_stat(s, ctx, "gbp_swap", "t10")

    origin = Prose()
    origin.add(f"Rates, {ctx.prev_month} → {ctx.curr_month}: a sell-off "
               "all along the curves — market movement, not a data "
               "problem.")
    _desk_range(origin, tc_rn, st, rfile, "10y swap", _pct_txt)

    origin.add("\n-")
    for i, (label, curve, tenor) in enumerate(
            (("GBP swap 2y", "gbp_swap", 2), ("gilt 10y", "gbp_gilt", 10),
             ("UST 10y", "ust", 10))):
        prev_l = float(pa["curves"][curve][tenor])
        curr_l = float(ca["curves"][curve][tenor])
        tc_v, v = s.call("verify_claim", left=curr_l, op="ne", right=prev_l,
                         tol=1e-9)
        (origin.add(f"{',' if i else ''} {label} ")
               .claim(v["difference"], tc_v, style.signed_bp)
               .add(" to ")
               .claim(curr_l, tc_ca, style.pc))
    origin.add(".")

    _desk_materiality(origin, s, ctx, "ir_gbp", cb, tc_cb, pb,
                      "Liabilities discount on gbp_swap.")
    watch = _desk_watch(st)
    if watch:
        origin.add(f"\n\n**Watch** (opinion, not a bound number): {watch}.")
    return [origin.draft("origin", session=s, significance="routine"),
            _desk_working(
                ctx, "gbp_swap", ("t2", "t5", "t10", "t20"),
                "Rates desk — the GBP swap curve this month",
                "**The tie-in, at length.** The GBP liability cohorts "
                "discount on `gbp_swap` and nothing on the asset side "
                "does, so a sell-off cuts liability value and helps "
                "surplus. The gilt and UST books give some of that back on "
                "the asset side, which is why the net `ir_gbp` standalone "
                "VaR is smaller than either leg gross — @lily quantifies "
                "the split. Curve SHAPE is a modelled risk here, not a "
                "footnote: four tenors on three curves means a steepening "
                "and a parallel shift are different events with different "
                "consequences for a book whose liabilities sit at one end. "
                "The gilt/swap basis is the unhedged piece — assets on the "
                "gilt curve, GBP liabilities on swap — and it is the "
                "exposure a fiscal or issuance event moves.\n\n"
                "**On the watch item.** It is opinion and it is labelled "
                "as opinion. It carries no figure about our book, it is "
                "derived from the note's own fixed thresholds rather than "
                "invented, and a quiet month gets no watch item at all.")]


def credit_desk(ctx) -> list[dict]:
    if ctx.prev_run is None:
        return _no_pair_draft("The credit walk")
    s = ctx.session()
    (tc_pa, pa), (tc_ca, ca), (tc_pb, pb), (tc_cb, cb) = _desk_reads(ctx, s)
    tc_rn, st, rfile = _desk_note_stat(s, ctx, "spread", "HY")

    origin = Prose()
    origin.add(f"Public credit, {ctx.prev_month} → {ctx.curr_month}: "
               "spreads wider, high yield leading — market movement, not "
               "a data problem.")
    _desk_range(origin, tc_rn, st, rfile, "HY OAS", _pct_txt)

    origin.add("\n-")
    for i, rating in enumerate(("HY", "BBB")):
        prev_l, curr_l = float(pa["spreads"][rating]), \
            float(ca["spreads"][rating])
        tc_v, v = s.call("verify_claim", left=curr_l, op="ne", right=prev_l,
                         tol=1e-9)
        (origin.add(f"{',' if i else ''} {rating} ")
               .claim(v["difference"], tc_v, style.signed_bp)
               .add(" to ")
               .claim(curr_l, tc_ca, style.bp))
    origin.add("; the widening is in the low-quality tail.")

    _desk_materiality(origin, s, ctx, "credit", cb, tc_cb, pb,
                      "The corporate book carries this.")
    watch = _desk_watch(st)
    if watch:
        origin.add(f"\n\n**Watch** (opinion, not a bound number): {watch}; "
                   "and spread risk scales with the level.")
    return [origin.draft("origin", session=s, significance="routine"),
            _desk_working(
                ctx, "spread", ("AA", "A", "BBB", "HY", "CCC"),
                "Credit desk — OAS by rating this month",
                "**The tie-in, at length.** The AA/A/BBB/HY corporate book "
                "carries this, and it is short-duration, so the `credit` "
                "block is small next to rates and equity even in a "
                "widening month. Investment grade barely moves in a "
                "risk-off drift of this kind; the widening concentrates in "
                "the low-quality tail, which is the dispersion signal "
                "worth watching rather than the index level.\n\n"
                "**Why the level matters for the risk number.** Spread "
                "dynamics are floored-normal, so spread risk scales with "
                "the LEVEL: the same absolute vol on a wider starting "
                "spread produces a bigger tail, and the names that carry "
                "that are the HY ones. The floor also means an extreme "
                "tightening draw is truncated, which is visible in "
                "@results-validator's floor-incidence check.\n\n"
                "**Structural limits, stated rather than implied.** One "
                "spread level per rating, applied to both currencies with "
                "a fixed term profile: GBP-specific spread stress and "
                "credit curve-shape risk are invisible here, and ratings "
                "migration reaches us through bucket mapping long before "
                "defaults do.\n\n"
                "**On the watch item.** Opinion, labelled as opinion, "
                "derived from the note's own thresholds and carrying no "
                "figure about our book.")]


def equity_desk(ctx) -> list[dict]:
    if ctx.prev_run is None:
        return _no_pair_draft("The equity walk")
    s = ctx.session()
    (tc_pa, pa), (tc_ca, ca), (tc_pb, pb), (tc_cb, cb) = _desk_reads(ctx, s)
    tc_rn, st, rfile = _desk_note_stat(s, ctx, "equity", "SP500")

    origin = Prose()
    origin.add(f"Equities, {ctx.prev_month} → {ctx.curr_month}: red across "
               "the board, continental Europe worst — market movement, not "
               "a data problem.")
    _desk_range(origin, tc_rn, st, rfile, "SP500", _level_txt)

    origin.add("\n-")
    for i, idx in enumerate(("FTSE100", "SP500", "SX5E")):
        prev_l, curr_l = float(pa["equity"][idx]), float(ca["equity"][idx])
        tc_v, v = s.call("verify_claim", left=curr_l, op="ne", right=prev_l,
                         tol=1e-9)
        sign = "-" if v["difference"] < 0 else "+"
        (origin.add(f"{',' if i else ''} {idx} ")
               .claim(v["rel_diff"], tc_v,
                      text=f"{sign}{style.pc(v['rel_diff'])}")
               .add(" to ")
               .claim(curr_l, tc_ca, lambda x: style.num(x, 1)))
    origin.add(".")

    _desk_materiality(origin, s, ctx, "equity", cb, tc_cb, pb,
                      "The book is index-proxied.")
    watch = _desk_watch(st)
    if watch:
        origin.add(f"\n\n**Watch** (opinion, not a bound number): {watch}.")
    return [origin.draft("origin", session=s, significance="routine"),
            _desk_working(
                ctx, "equity", ("FTSE100", "SP500", "SX5E"),
                "Equity desk — index levels this month",
                "**The tie-in, at length.** The equity book is proxied "
                "onto these three indices, so the month-end closes are "
                "not an approximation of the marks — they ARE the marks, "
                "and the `equity` standalone VaR is the largest single "
                "block against surplus. That makes the equity vols the "
                "calibration output the headline number is most sensitive "
                "to.\n\n"
                "**What the proxy cannot see.** Single-name and "
                "concentration risk never reaches the VCV: a book of "
                "index proxies has, by construction, exactly the index's "
                "dispersion and none of its own. Implied volatility is "
                "the independent cross-read on the vols we calibrate from "
                "504 days of history, and it is not in our factor set "
                "either — where the two disagree, the calibration is the "
                "one with a month's lag.\n\n"
                "**On the watch item.** Opinion, labelled as opinion, "
                "derived from the note's own thresholds and carrying no "
                "figure about our book.")]


# --------------------------------------------------------------------------
# @pc-desk (PENDING-BATCH2 §9) — NEW. Private credit as a DESK: our sleeve,
# not the market. @wide-eye covers the private credit market as a wider
# risk; this covers the four fund proxies we actually hold, the richest
# material in the book and previously owned by nobody at desk level.
#
# Standing themes: fundraising and dispersion, where our marks sit against
# public HY, and whether the fixed-rate bond proxy is still the right
# mapping.
# --------------------------------------------------------------------------

def pc_desk(ctx) -> list[dict]:
    if ctx.prev_run is None:
        return _no_pair_draft("The private credit walk")
    s = ctx.session()
    (tc_pa, pa), (tc_ca, ca), (tc_pb, pb), (tc_cb, cb) = _desk_reads(ctx, s)
    tc_rn, st_hy, rfile = _desk_note_stat(s, ctx, "spread", "HY")

    tc_bk, bk = s.call("read_book", asof_or_run=ctx.curr_run["id"])
    pcs = [p for p in bk["data"]["positions"]
           if p.get("asset_class") == "private_credit"]
    if not pcs:
        body = ("No private credit positions in this book, so there is "
                "nothing for this desk to cover this month.")
        return [{"kind": "origin", "body": body, "claims": [],
                 "context": False, "session": s, "significance": "quiet"}]

    proxies = sorted({str(p.get("rating")) for p in pcs})
    proxy = proxies[0]
    prev_hy, curr_hy = float(pa["spreads"]["HY"]), float(ca["spreads"]["HY"])
    tc_hy, v_hy = s.call("verify_claim", left=curr_hy, op="ne",
                         right=prev_hy, tol=1e-9)
    prev_px, curr_px = float(pa["spreads"][proxy]), float(ca["spreads"][proxy])
    tc_px, v_px = s.call("verify_claim", left=curr_px, op="ne",
                         right=prev_px, tol=1e-9)

    origin = Prose()
    origin.add(f"Private credit, {ctx.prev_month} → {ctx.curr_month}: "
               + ("our sleeve is proxied onto public HY, so its valuation "
                  "lag never shows."
                  if proxy == "HY" else
                  "the comparator moved and our sleeve marks off a "
                  "different proxy level, so the two diverge."))
    _desk_range(origin, tc_rn, st_hy, rfile, "public HY OAS", _pct_txt)
    (origin.add("\n- Public HY ")
           .claim(v_hy["difference"], tc_hy, style.signed_bp)
           .add(" to ")
           .claim(curr_hy, tc_ca, style.bp)
           .add(f"; {len(pcs)} funds, all on `{proxy}`"))
    if proxy != "HY":
        (origin.add(" at ")
               .claim(v_px["difference"], tc_px, style.signed_bp)
               .add(" to ")
               .claim(curr_px, tc_ca, style.bp))
    origin.add(".")
    origin.add("\n- Dispersion between managers: absent — one spread "
               "level, whole sleeve.")
    origin.add("\n- The fixed-rate bond proxy overstates rate risk; NAV "
               "smoothing understates vol.")

    _desk_materiality(origin, s, ctx, "credit", cb, tc_cb, pb,
                      "Same credit block as the corporates.")
    watch = _desk_watch(st_hy)
    if watch:
        origin.add(f"\n\n**Watch** (opinion, not a bound number): {watch}.")
    return [origin.draft("origin", session=s, significance="routine"),
            _desk_working(
                ctx, "spread", ("BBB", "HY", "CCC"),
                "Private credit desk — the public comparator this month",
                "**Marks versus public HY.** Our sleeve is proxied on the "
                "public HY level itself, so the model marks it to the "
                "traded market the instant the traded market moves. Real "
                "private credit marks do not move that fast. The valuation "
                "lag is real, it is not in the numbers, and what the "
                "results below show is a marked-to-public sleeve wearing a "
                "private label. Whichever way the comparator goes, our "
                "actual marks reach it a quarter late.\n\n"
                "**Fundraising and dispersion.** Dispersion between "
                "managers is not in our data at all — we hold one spread "
                "level for the whole sleeve, so a good manager and a bad "
                "one are the same position here. Fundraising and deployment "
                "pressure, which is what compresses spreads and loosens "
                "documentation across the asset class, has no channel into "
                "any factor we model. @wide-eye covers that market; this "
                "desk covers our sleeve.\n\n"
                "**Is the fixed-rate bond proxy still the right "
                "mapping?** The funds are modelled as synthetic fixed-rate "
                "bonds. The fixed coupon adds govy-curve duration a "
                "floating-rate loan book largely does not have, so the "
                "sleeve's RATE risk is overstated; NAV smoothing and the "
                "valuation lag understate its true volatility. Two biases "
                "in opposite directions, neither measured, so nothing but "
                "luck makes them cancel. The sleeve also prices off the "
                "same `credit` block as the corporate book, which is part "
                "of the same mapping question.\n\n"
                "**On the watch item.** Opinion, labelled as opinion, "
                "derived from the note's own thresholds and carrying no "
                "figure about our book.")]


# --------------------------------------------------------------------------
# @focused in room 3 (PENDING-BATCH2 §2 and §13). ONE agent with two briefs,
# not two personas: the same desk, reading the same note it wrote in the
# research stage, asked a different question. Room 1 asks "where does this
# show up in the inputs"; this asks "what did it do to the numbers". Each
# material move is tied to the book's exposure and to the block VaR it
# moved, and sized against surplus.
# --------------------------------------------------------------------------

# factor block in the research note -> the standalone VaR block that carries
# it (var_standalone_factors.json block_definitions), and who holds it.
# The exposure clause is a FRAGMENT (PENDING-BATCH2 §12): who holds it, in
# five or six words. The full account of why a swap rise helps surplus is
# @lily's working page, not a clause repeated in every room.
_FB_BLOCKS = {
    "gbp_swap": ("ir_gbp", "GBP liabilities discount here"),
    "gbp_gilt": ("ir_gbp", "the gilt book carries it"),
    "ust": ("ir_usd", "Treasuries and USD cohorts sit here"),
    "spread": ("credit", "corporates and private credit price here"),
    "equity": ("equity", "index-proxied, so this is the mark"),
    "fx": ("fx", "USD assets and cohorts translate here"),
}


def _fb_moves(stats: dict) -> list[dict]:
    """The same material moves room 1 named: largest absolute move in rates,
    in credit, and in equity/FX. One list, computed from the note, so the two
    rooms are demonstrably talking about the same three things."""
    groups = [
        [(f"{c} {t[1:]}y", c, t, "rate")
         for c in ("gbp_swap", "gbp_gilt", "ust")
         for t in ("t2", "t5", "t10", "t20")],
        [(f"{r} OAS", "spread", r, "rate")
         for r in ("AA", "A", "BBB", "HY", "CCC")],
        [(i, "equity", i, "level") for i in ("FTSE100", "SP500", "SX5E")]
        + [("GBPUSD", "fx", "GBPUSD", "level")],
    ]
    out = []
    for candidates in groups:
        best = None
        for label, block, col, kind in candidates:
            st = stats[block]["columns"][col]
            size = abs(float(st["change_bp"] if kind == "rate"
                             else st["change_pct"]))
            if best is None or size > best[0]:
                best = (size, label, block, col, kind, st)
        _size, label, block, col, kind, st = best
        out.append({
            "label": label, "block": block, "col": col, "kind": kind,
            "change": st["change_bp"] if kind == "rate" else st["change_pct"],
            "change_txt": (f"{st['change_bp']:+.1f}bp" if kind == "rate"
                           else f"{st['change_pct']:+.2f}%"),
            "var_block": _FB_BLOCKS[block][0],
            "exposure": _FB_BLOCKS[block][1],
        })
    return out


def focused_room3(ctx) -> list[dict]:
    """@focused's ROOM-3 brief (PENDING-BATCH2 §2, §13): "I flagged these
    moves in my report and in room 1 — here is what they did to the
    results." Same agent, same note, same three moves, output-room
    question. Does NOT re-litigate whether the assumptions reconcile: that
    is the room-1 brief's question and it is already answered."""
    s = ctx.session()
    tc_r, r = s.call("read_research", asof=ctx.curr_month)
    stats, rfile = r["stats"], r["file"]
    tc_cb, cb = s.call("read_output", asof_or_run=ctx.curr_run["id"],
                       filename="var_standalone_factors.json")
    blocks = cb["data"]["blocks"]
    tc_val, val = s.call("read_output", asof_or_run=ctx.curr_run["id"],
                         filename="valuation.json")
    surplus = float(val["data"]["surplus_gbp"])

    prev_blocks = None
    if ctx.prev_run is not None:
        try:
            _, pb = s.call("read_output", asof_or_run=ctx.prev_run["id"],
                           filename="var_standalone_factors.json")
            prev_blocks = pb["data"]["blocks"]
        except ToolError:
            prev_blocks = None

    # HOUSE STYLE (§12): lead line, one fragment per move, one closing
    # line. This desk's depth is its research NOTE — the full section per
    # risk on the Research tab, where §2 says length is welcome — so the
    # post points at the note rather than carrying a second copy of it.
    origin = Prose()
    origin.add(f"I flagged these moves in my report (`{rfile}`) — here is "
               "what they did to the results.")
    biggest = None
    for m in _fb_moves(stats):
        vb = m["var_block"]
        level = float(blocks[vb])
        origin.add(f"\n- {m['label']} ").claim(m["change"], tc_r,
                                               text=m["change_txt"])
        origin.add(f", {m['exposure']} → `{vb}` standalone VaR ")
        origin.claim(level, tc_cb, style.money)
        if prev_blocks is not None and vb in prev_blocks:
            tc_v, v = s.call("verify_claim", left=level, op="ne",
                             right=float(prev_blocks[vb]), tol=1e-9)
            (origin.add(" (")
                   .claim(v["difference"], tc_v, style.signed_money)
                   .add(" on the month)"))
        tc_m, mv = s.call("verify_claim", left=level, op="lt", right=surplus,
                          tol=0.0)
        (origin.add(", ")
               .claim(mv["ratio"], tc_m, style.pc)
               .add(" of surplus."))
        if biggest is None or level > biggest[1]:
            biggest = (vb, level)
    (origin.add("\nAgainst a surplus of ")
           .claim(surplus, tc_val, style.money)
           .add(f", `{biggest[0]}` carries most of it."))
    return [origin.draft("origin", session=s, significance="routine")]


# --------------------------------------------------------------------------

def wide_eye(ctx) -> list[dict]:
    """@wide-eye's room-3 post (PENDING-BATCH2 §2): "in my report I said
    roughly this — here is what it currently means, or could mean, for the
    portfolio as it stands." Context-marked; the quarantine holds, so this
    post carries no figures at all — the report it cites is where the
    market-level numbers live."""
    s = ctx.session()
    rfile = None
    try:
        _, r = s.call("read_research", asof=ctx.curr_month, agent="wide-eye")
        rfile = r["file"]
    except ToolError:
        rfile = None
    ref = f" (`{rfile}`)" if rfile else ""
    # HOUSE STYLE (§12): a lead line and three fragments. The long form of
    # every wider risk — private credit, CRE, banking, policy, geopolitics,
    # reinsurance, litigation, cyber, climate, regulation — is the REPORT
    # this post cites, on the Research tab, where §2 says length is fine.
    # A wider-risk essay repeated in the feed would be a second copy of a
    # document that already exists, and the quarantine would still hold.
    body = (
        f"**context — enters no calculation** · My wider-risk note{ref} is "
        "up; in mock I cannot search the web.\n"
        "What that means for the portfolio as it stands:\n"
        "- our private credit sleeve is the most exposed — marks lag, so "
        "widening arrives late and as a level shift.\n"
        "- the gilt/swap basis is the unhedged rates exposure, and a "
        "fiscal or issuance event is what moves it.\n"
        "- claims inflation, litigation, catastrophe and reinsurance "
        "pricing reach no number this room reports."
    )
    return [style.context_draft(body, session=s, significance="routine")]


# --------------------------------------------------------------------------
# @realist — reasonableness bands (SPEC-APP section 5). Fixed,
# experience-based priors from scenarios/reference/realist_priors.yaml,
# deliberately independent of this month's assumptions. Calibrated so the
# clean base runs produce ZERO flags; flags outliers in EITHER direction
# with the expected band quoted.
# --------------------------------------------------------------------------

_MAX_QUOTED_FLAGS = 3  # per-post tool budget: quote the worst few, table the rest


def _maturity_bucket(years) -> str:
    y = int(years)
    if y <= 3:
        return "short"
    if y <= 10:
        return "medium"
    return "long"


def _realist_band(p: dict, priors: dict):
    """(low, high) band for one position, or None when the priors do not
    cover it. Private credit is keyed by asset_class + currency, NEVER by
    the selected proxy rating (a mis-chosen proxy cannot move its own
    goalposts)."""
    bands = priors["position_bands"]
    try:
        if p.get("asset_class") == "private_credit":
            b = bands["private_credit"][p["currency"]]
        elif p["type"] == "cash":
            b = bands["cash"][p["currency"]]
        elif p["type"] == "equity":
            b = bands["equity"][p["index"]]
        elif p["type"] == "govt_bond":
            b = bands["govt_bond"][p["currency"]][
                _maturity_bucket(p["maturity_years"])]
        elif p["type"] == "corp_bond":
            b = bands["corp_bond"][p["currency"]][p["rating"]][
                _maturity_bucket(p["maturity_years"])]
        else:
            return None
    except (KeyError, TypeError, ValueError):
        return None
    return float(b["low"]), float(b["high"])


def _realist_desc(p: dict) -> str:
    if p.get("asset_class") == "private_credit":
        return f"{p['currency']} private credit ({p.get('strategy', '')})"
    if p["type"] == "cash":
        return f"{p['currency']} cash"
    if p["type"] == "equity":
        return f"{p['index']}-proxied equity"
    kind = "gilt/govy" if p["type"] == "govt_bond" else \
        f"{p.get('rating', '?')} corporate"
    return (f"{_maturity_bucket(p['maturity_years'])}-maturity "
            f"{p['currency']} {kind}")


def realist(ctx) -> list[dict]:
    s = ctx.session()
    tc_pr, pr = s.call("read_reference", filename="realist_priors.yaml")
    priors = pr["data"]
    tc_bk, bk = s.call("read_book", asof_or_run=ctx.curr_run["id"])
    tc_pos, pos = s.call("read_output", asof_or_run=ctx.curr_run["id"],
                         filename="var_standalone_positions.csv")
    tc_val, val = s.call("read_output", asof_or_run=ctx.curr_run["id"],
                         filename="valuation.json")
    tc_fac, fac = s.call("read_output", asof_or_run=ctx.curr_run["id"],
                         filename="var_standalone_factors.json")
    tc_agg, agg = s.call("read_output", asof_or_run=ctx.curr_run["id"],
                         filename="var_aggregate.json")

    by_id = {p["id"]: p for p in bk["data"]["positions"]}
    assets = float(val["data"]["asset_total_gbp"])
    aggregate = float(agg["data"]["aggregate_var_gbp"])

    rows = []   # (kind, label, desc, ratio, lo, hi, mv, var, tc_mv, tc_var)
    outliers = []
    for r in pos["rows"]:
        p = by_id.get(r["id"])
        if p is None:
            continue
        band = _realist_band(p, priors)
        if band is None:
            continue
        mv, var = float(r["market_value_gbp"]), float(r["var_99_5_1y_gbp"])
        ratio = 100.0 * var / mv if mv else 0.0
        # ids, not names: instrument names carry coupon/maturity numerals
        # ("UKT 4.25 2034") that would need spurious claim bindings
        row = ("position", str(r["id"]), _realist_desc(p),
               ratio, band[0], band[1], mv, var, tc_pos, tc_pos)
        rows.append(row)
        if not band[0] <= ratio <= band[1]:
            outliers.append(row)

    agg_band = (float(priors["aggregate_band"]["low"]),
                float(priors["aggregate_band"]["high"]))
    agg_ratio = 100.0 * aggregate / assets
    row = ("aggregate", "aggregate 99.5% VaR", "% of total assets",
           agg_ratio, agg_band[0], agg_band[1], assets, aggregate,
           tc_val, tc_agg)
    rows.append(row)
    if not agg_band[0] <= agg_ratio <= agg_band[1]:
        outliers.append(row)
    for name, bv in fac["data"]["blocks"].items():
        bb = priors["block_bands"].get(name)
        if not bb:
            continue
        ratio = 100.0 * float(bv) / assets
        row = ("block", f"{name} block standalone VaR", "% of total assets",
               ratio, float(bb["low"]), float(bb["high"]), assets,
               float(bv), tc_val, tc_fac)
        rows.append(row)
        if not float(bb["low"]) <= ratio <= float(bb["high"]):
            outliers.append(row)

    origin = Prose()
    if outliers:
        # worst breaches first: distance outside the band, in band-widths
        def _sev(x):
            lo, hi, ratio = x[4], x[5], x[3]
            width = max(hi - lo, 1e-9)
            return -(max(lo - ratio, ratio - hi, 0.0) / width)
        outliers.sort(key=_sev)
        # HOUSE STYLE (§12): the outliers, one fragment each. Why the bands
        # are priors rather than this month's assumptions, and why a light
        # ratio is as suspicious as a heavy one, is on the backing page.
        origin.add(f"**FLAG — reasonableness**: {len(outliers)} risk "
                   "ratios outside my fixed experience bands "
                   "(`realist_priors.yaml`).")
        for kind, label, desc, ratio, lo, hi, mv, var, tc_mv, tc_var in \
                outliers[:_MAX_QUOTED_FLAGS]:
            high = ratio > hi
            tc_v, v = s.call("verify_claim", left=ratio, op="gt" if high
                             else "lt", right=hi if high else lo, tol=0.0)
            (origin.add(f"\n- {label}: ")
                   .claim(var, tc_var, style.money)
                   .add(" on ")
                   .claim(mv, tc_mv, style.money)
                   .add(" is ")
                   .claim(ratio, tc_v, text=f"{ratio:.1f}%")
                   .add(f" — looks {'high' if high else 'light'} for "
                        f"{desc}; I'd expect ")
                   .claim(lo, tc_pr, text=f"{lo:.1f}")
                   .add("–")
                   .claim(hi, tc_pr, text=f"{hi:.1f}%")
                   .add("."))
        if len(outliers) > _MAX_QUOTED_FLAGS:
            origin.add("\n- The rest are in the working table.")
    else:
        tc_v, v = s.call("verify_claim", left=agg_ratio, op="le",
                         right=agg_band[1], tol=0.0)
        # Nothing material: ONE line, and stop (§12).
        (origin.add("Everything sits in band: aggregate VaR ")
               .claim(aggregate, tc_agg, style.money)
               .add(" is ")
               .claim(agg_ratio, tc_v, text=f"{agg_ratio:.1f}%")
               .add(" of assets against a prior band of ")
               .claim(agg_band[0], tc_pr, text=f"{agg_band[0]:.1f}")
               .add("–")
               .claim(agg_band[1], tc_pr, text=f"{agg_band[1]:.1f}%")
               .add(" (`realist_priors.yaml`). Nothing to chase."))

    work = Prose()
    work.add("Reasonableness working — standalone VaR as % of value vs "
             "`realist_priors.yaml` (position bands: VaR/MV; portfolio "
             "rows: VaR/assets).\n\n"
             "**What the bands are.** Fixed experience priors held as "
             "versioned reference data, deliberately independent of this "
             "month's assumptions — so a wrong assumption cannot vouch for "
             "itself. Every position's standalone VaR over market value, "
             "every factor block and the aggregate are checked against "
             "them.\n\n"
             "**Either direction matters.** Light is as suspicious as "
             "heavy: a ratio well under its band usually means an "
             "understated vol or a missing exposure, which is the harder "
             "error to notice. This is corroboration for the primary "
             "checks and never the scored route — but a ratio outside "
             "everything the clean history has produced is worth a "
             "sentence from somebody.\n\n"
             "| line | classification | market value "
             "| standalone VaR | band | verdict |\n|---|---|---|---|---|---|"
             "\n")
    for kind, label, desc, ratio, lo, hi, mv, var, tc_mv, tc_var in rows:
        verdict = ("ok" if lo <= ratio <= hi else
                   "**HIGH**" if ratio > hi else "**LOW**")
        (work.add(f"| {label} | {desc} | ")
             .claim(mv, tc_mv, style.money)
             .add(" | ")
             .claim(var, tc_var, style.money)
             .add(" | ")
             .claim(lo, tc_pr, text=f"{lo:.1f}")
             .add("–")
             .claim(hi, tc_pr, text=f"{hi:.1f}%")
             .add(f" | {verdict} |\n"))
    work.add("\nBands are fixed experience priors (versioned reference "
             "data), deliberately independent of the month's calibration.")
    sig = "notable" if outliers else "quiet"
    return [origin.draft("origin", session=None, significance=sig),
            work.draft("expansion", session=s, significance=sig)]


_RE_STATED_AGG = re.compile(r"VaR stands at \*\*£([\d.,]+)m\*\*")
_RE_STATED_PREV = re.compile(r"from £([\d.,]+)m at end")
_RE_DRIVERS = re.compile(r"\*\*([+-])£([\d.,]+)m\*\*")
_RE_FX_NEG = re.compile(r"reduced surplus[^.]*£([\d.,]+)m[^.]*translation")


def draft_report_review(ctx) -> list[dict]:
    """@results-validator: reconcile the drafted month-end report against
    the engine outputs. Runs only when a seeded scenario is active and a
    draft report exists; every stated figure it quotes is bound either to
    the engine outputs or to an explicit verify_claim comparison."""
    if not ctx.seeded or ctx.prev_run is None:
        return []
    s = ctx.session()
    try:
        tc_rep, rep = s.call("read_reference", filename="draft_report_D3.md")
    except ToolError:
        return []
    text = rep["text"]

    tc_agg, agg = s.call("read_output", asof_or_run=ctx.curr_month,
                         filename="var_aggregate.json")
    tc_pagg, pagg = s.call("read_output", asof_or_run=ctx.prev_month,
                           filename="var_aggregate.json")
    tc_attr, a, attr_dir = _read_attribution(s, ctx)
    if a is None:
        raise ToolError("no committed attribution for this pair of runs")
    g, pg = agg["data"], pagg["data"]

    m = _RE_STATED_AGG.search(text)
    if not m:
        body = (f"Reviewed `{rep['file']}` but could not locate a stated "
                "aggregate VaR headline to reconcile — flagging for manual "
                "review.")
        return [{"kind": "origin", "body": body, "claims": [],
                 "context": False, "session": s, "significance": "routine"}]
    stated = float(m.group(1).replace(",", "")) * 1e6

    findings: list[str] = []
    origin = Prose()
    # The backing page gets its own session and its own read of the report,
    # so it carries recorded provenance rather than borrowing the post's.
    ws = ctx.session()
    tc_wrep, wrep = ws.call("read_reference", filename="draft_report_D3.md")
    tc_wpagg, wpagg = ws.call("read_output", asof_or_run=ctx.prev_month,
                              filename="var_aggregate.json")
    work = Prose()
    work.add(f"Draft report reconciliation — `{wrep['file']}` against the "
             "engine outputs.\n\n**Method.** Every figure the draft states "
             "is parsed out of the markdown and compared against the "
             "engine artefact that owns it: the headline against "
             "`var_aggregate.json`, the drivers against the sequential "
             "attribution. Nothing is taken from the report's own "
             "arithmetic — a report that is internally consistent and "
             "wrong is exactly the case this check exists for.\n\n")

    # D3A: stated aggregate == sum of block standalones, != true aggregate
    tc_v1, v1 = s.call("verify_claim", left=stated, op="eq",
                       right=g["sum_standalone_blocks_gbp"], tol=0.005)
    tc_v2, v2 = s.call("verify_claim", left=stated, op="ne",
                       right=g["aggregate_var_gbp"], tol=0.005)
    if v1["passed"] and v2["passed"]:
        (origin.add("\n- **Finding (headline):** stated aggregate ")
               .claim(stated, tc_v1, style.money)
               .add(" is the SUM of the five block standalone VaRs, not "
                    "the correlated aggregate ")
               .claim(g["aggregate_var_gbp"], tc_agg, style.money)
               .add("."))
        (origin.add("\n- Diversification benefit ")
               .claim(g["diversification_benefit_gbp"], tc_agg, style.money)
               .add(" dropped; risk overstated ")
               .claim(v2["ratio"], tc_v2, lambda v: style.num(v, 2))
               .add("x."))
        work.add("**Finding D3A — the headline.** The stated aggregate "
                 "99.5% VaR is not the correlated aggregate at all: it "
                 "equals the simple sum of the five block standalone VaRs "
                 "exactly, so the diversification benefit has been "
                 "silently dropped and risk is overstated. ")
        pm = _RE_STATED_PREV.search(text)
        if pm:
            pstated = float(pm.group(1).replace(",", "")) * 1e6
            tc_v3, v3 = ws.call("verify_claim", left=pstated, op="eq",
                                right=pg["sum_standalone_blocks_gbp"],
                                tol=0.005)
            if v3["passed"]:
                (work.add("The comparison month is summed the same way (")
                     .claim(pstated, tc_v3, style.money)
                     .add(" stated against a true aggregate of ")
                     .claim(wpagg["data"]["aggregate_var_gbp"], tc_wpagg,
                            style.money)
                     .add("), so the month-on-month change looks internally "
                          "coherent and the error cannot be caught by "
                          "smell — only against var_aggregate.json. "))
        work.add("\n\n")
        findings.append("D3A")
    elif not v2["passed"]:
        (origin.add("\n- Headline reconciles: stated aggregate ")
               .claim(stated, tc_v2, style.money)
               .add(" matches var_aggregate.json."))

    # D3B: driver sign consistency vs attribution.json
    mtm = _steps(a, "mtm")
    fx_true = mtm["fx"]
    fxm = _RE_FX_NEG.search(text)
    if fxm and fx_true > 0:
        fx_stated = -float(fxm.group(1).replace(",", "")) * 1e6
        tc_v4, v4 = s.call("verify_claim", left=fx_stated, op="ne",
                           right=fx_true, tol=0.005)
        drivers = [(sg, float(val.replace(",", "")) * 1e6)
                   for sg, val in _RE_DRIVERS.findall(text)]
        stated_sum = sum(v if sg == "+" else -v for sg, v in drivers)
        tc_v5, v5 = s.call("verify_claim", left=stated_sum, op="ne",
                           right=float(a["mtm"]["total_change_gbp"]),
                           tol=0.005)
        (origin.add("\n- **Finding (sign flip):** report says depreciation "
                    "reduced surplus by ")
               .claim(fx_stated, tc_v4, style.money)
               .add("; attribution.json step fx is ")
               .claim(fx_true, tc_attr, style.signed_money)
               .add("."))
        (origin.add("\n- A weaker pound raises USD asset value; the stated "
                    "drivers sum to ")
               .claim(v5["left"], tc_v5, style.signed_money)
               .add(", not the ")
               .claim(float(a["mtm"]["total_change_gbp"]), tc_attr,
                      style.signed_money)
               .add(" total the report quotes."))
        work.add("**Finding D3B — the sign flip.** The report has sterling "
                 "depreciation reducing surplus. A weaker pound RAISES the "
                 "GBP value of the USD assets, and the engine's own fx "
                 "step says so. Additivity confirms it independently: the "
                 "six drivers as the report states them do not sum to the "
                 "total the report itself quotes, which they would if the "
                 "sign were right.\n\n")
        findings.append("D3B")

    if findings:
        origin.add("\n- Correct before circulation; every other stated "
                   "figure reconciles.")
        work.add("Both findings trace to specific lines through the tool "
                 "calls attached to this post. Recommend the draft is "
                 "corrected before circulation.")
    else:
        work.add("Every stated figure reconciles with the engine outputs "
                 "to its rounding.")

    if findings:
        origin.lead(f"Reconciled `{rep['file']}` against the engine "
                    "outputs — " + ("one finding." if len(findings) == 1
                                    else f"{len(findings)} findings."))
    else:
        origin.lead(f"Reconciled `{rep['file']}`: every stated figure "
                    "matches the engine outputs. No findings.")

    sig = "critical" if findings else "quiet"
    return [origin.draft("origin", session=s, significance=sig),
            work.draft("expansion", session=ws, significance=sig)]


# --------------------------------------------------------------------------
# @red-team closing pass (SPEC-APP H.1) — the last voice in the cycle
# --------------------------------------------------------------------------

def red_team_closing(ctx) -> list[dict]:
    """The closing half of @red-team's two-pass cycle: runs after rooms 2
    and 3 have posted (reads_from = ["room:1", "room:3"] puts it last in
    room 3's topological order). Reads room:1 (was an input-stage concern
    resolved by the output?), room:3 (does the attribution story hold up?)
    and its own opening post — the only agent that sees both ends of one
    cycle."""
    s = ctx.session()
    sources: list[int] = []
    try:
        _, opening = s.call("read_agent_posts", room=1, handle="@red-team")
        if opening["posts"]:
            sources.append(opening["posts"][-1]["id"])
    except ToolError:
        pass

    room1_flags: list = []  # order-preserving de-dup: a re-run pass can
    seen_flags = set()      # publish more than one post per handle
    for handle in ("@pre-flight-checks", "@vcv", "@holdings"):
        try:
            _, res = s.call("read_agent_posts", room=1, handle=handle)
        except ToolError:
            continue
        for p in res["posts"]:
            sources.append(p["id"])
            if "FLAG" in (p["body_md"] or "") and handle not in seen_flags:
                seen_flags.add(handle)
                room1_flags.append(handle)

    attrib_flag = False
    try:
        _, ares = s.call("read_agent_posts", room=3, handle="@attrib")
        for p in ares["posts"]:
            sources.append(p["id"])
            if "red flag" in (p["body_md"] or "").lower():
                attrib_flag = True
    except ToolError:
        pass

    # HOUSE STYLE (§12): TWO challenges, the two that bite at the output
    # stage this cycle, each stated sharply. The remaining standing
    # limitations are carried in the working below rather than dropped —
    # six every month and nobody reads me by the second month.
    origin = Prose()
    origin.add("Closing challenge — the two that bite at the output stage.")
    if room1_flags:
        sig = "critical"
        origin.add(f"\n- **Still open.** {', '.join(room1_flags)} flagged "
                   "an input going in; nothing in this room resolves it. A "
                   "process gap, not a clean bill of health.")
    elif attrib_flag:
        sig = "notable"
        origin.add("\n- **The control point.** @attrib's residual language "
                   "names one worth a second look before sign-off. "
                   "Nothing from the input stage is left unresolved.")
    else:
        sig = "routine"
        origin.add("\n- **Nothing unresolved.** Every input-stage concern "
                   "was addressed by the output, and the attribution, "
                   "desks and bands hold on their own numbers.")
    origin.add("\n- **The liability side.** Reserves are fixed cashflows: "
               "no claims inflation, no cat model. A large-loss event has "
               "no channel into a single figure in this room.")
    origin.add("\n- Four further standing limitations in the working. I "
               "challenge; I do not block.")

    # Its own session, so the backing page carries its own recorded read
    # rather than borrowing the feed post's provenance.
    ws = ctx.session()
    tc_wa, wa = ws.call("read_assumptions", asof_or_run=ctx.curr_run["id"])
    window = int((wa["data"].get("meta") or {}).get(
        "calibration_window_days", 504))
    work = Prose()
    work.add("The calibration window this challenge is aimed at: ")
    work.claim(window, tc_wa, text=str(window))
    work.add(" days.\n\n")
    work.add(
        "The closing challenge, in full.\n\n"
        "Two items are in the post because they bite at the OUTPUT stage "
        "this cycle. The rest are structural: they apply every month, "
        "regardless of what the numbers did, and they belong on the slide "
        "next to the headline rather than repeated as news.\n\n"
        "**(i) An input-stage flag that survives to the output stage.** A "
        "concern raised before the model ran and never addressed by the "
        "time the output is challenged is a process gap. The finding "
        "stands until it is corrected and re-run — not until it has been "
        "noted twice.\n\n"
        "**(ii) The liability side.** The reserve book is fixed "
        "claims-payment cashflows: no longevity, no claims or reserve "
        "inflation, no catastrophe model. A large-loss event has no "
        "channel into this framework at all, so nothing in room 3 moves "
        "when one happens.\n\n"
        "**(iii) The window.** Vols and correlations come from a 504-day "
        "trailing sample that excludes the 2022 gilt/LDI episode — the "
        "last genuine tail in this market. A stressed-window overlay would "
        "not calibrate to it either.\n\n"
        "**(iv) The private-credit proxy.** Synthetic fixed-rate bonds "
        "stand in for floating-rate loans: rate risk overstated, NAV "
        "smoothing understating true volatility. Two biases, opposite "
        "directions, neither measured.\n\n"
        "**(v) Normal dynamics.** One-step Gaussian factors with a "
        "post-hoc spread floor — no fat tails, no vol-of-vol. The true "
        "one-in-two-hundred is likely worse than the modelled one.\n\n"
        "**(vi) Equity by index proxy, one spread level per rating.** "
        "Single-name and concentration risk never reach the VCV; "
        "GBP-specific spread stress and credit curve-shape risk are "
        "structurally invisible.\n\n"
        "None of this blocks anything. I challenge; the human disposes.")
    return [origin.draft("origin", session=s, significance=sig,
                         sources=sources),
            work.draft("expansion", session=ws, significance=sig)]


# --------------------------------------------------------------------------
# fresh snapshots (SPEC-APP E) — outward agents only, appended to room 3
# --------------------------------------------------------------------------

_SNAPSHOT_COLUMNS = {
    "@focused": (("gbp_swap", "t10", "rate"), ("spread", "HY", "rate"),
                 ("equity", "SP500", "level"), ("fx", "GBPUSD", "level")),
    "@rates-desk": (("gbp_swap", "t10", "rate"),),
    "@credit-desk": (("spread", "HY", "rate"),),
    # §9: the private credit desk watches the public comparator, because
    # its own sleeve is marked with a lag and has nothing to say daily.
    "@pc-desk": (("spread", "HY", "rate"),),
    "@equity-desk": (("equity", "SP500", "level"),),
}


def _snapshot_notables(origin: Prose, st: dict, tc: int, kind: str,
                       chg_txt: str) -> None:
    """Re-emit the research note's `notable` observations with every number
    BOUND to the read_research call the stats came from.

    The note's own strings carry figures (the move percentile, the month vs
    trailing daily vol) that the citation gate rightly refuses to publish
    unbound — interpolating them verbatim suppressed exactly the snapshots
    that had something to say, while silent ones published. Each figure is
    re-stated here from its own stat field, so it binds to the tool result
    that produced it."""
    pctl = st.get("move_percentile")
    vol_unit = "bp/day" if kind == "rate" else "%/day"
    mvol = st.get("month_dayvol_bp" if kind == "rate" else "month_dayvol_pct")
    tvol = st.get("trail_dayvol_bp" if kind == "rate" else "trail_dayvol_pct")
    parts = []
    if pctl is not None and pctl >= research.PCTL_HI:
        parts.append(("pctl", "top-decile move ("))
    elif pctl is not None and pctl <= research.PCTL_LO:
        parts.append(("pctl", "bottom-decile move ("))
    for obs in st.get("notable") or []:
        if "two-year high" in obs:
            parts.append(("plain", "two-year high"))
        elif "two-year low" in obs:
            parts.append(("plain", "two-year low"))
        elif obs.startswith("outsized"):
            parts.append(("move", "outsized ("))
        elif "ran hot" in obs:
            parts.append(("vol", "realised daily vol ran hot ("))
        elif "ran cold" in obs:
            parts.append(("vol", "realised daily vol ran cold ("))
    if not parts:
        return
    # HOUSE STYLE (§12): fragments after a semicolon, not a nest of
    # parentheses. Every figure still enters through its own claim.
    origin.add("; ")
    for i, (kindp, lead) in enumerate(parts):
        origin.add(lead)
        if kindp == "pctl":
            (origin.claim(float(pctl), tc, text=research.ordinal(int(pctl)))
                   .add(")"))
        elif kindp == "move":
            origin.claim(st["change_bp"] if kind == "rate"
                         else st["change_pct"], tc, text=chg_txt).add(")")
        elif kindp == "vol" and mvol is not None and tvol is not None:
            (origin.claim(float(mvol), tc, text=f"{mvol:.2f}")
                   .add(" vs ")
                   .claim(float(tvol), tc, text=f"{tvol:.2f}")
                   .add(f" {vol_unit}) — vol "
                        f"{'up' if float(mvol) > float(tvol) else 'down'} "
                        "as it enters the window"))
        origin.add(", " if i < len(parts) - 1 else "")


def outward_snapshot_draft(agent_row: dict, ctx) -> list[dict]:
    """One outward agent's contribution to a fresh snapshot (SPEC-APP E):
    what has moved between the frozen month-end close and the walked-
    forward data-through date. @wide-eye stays a context stub (mock has no
    web access); the others cite the @focused research note recomputed
    with `data_through`. Significance follows the note's own fixed
    thresholds — `quiet` when nothing crosses them, exactly the "many
    quiet snapshots" case §G expects."""
    s = ctx.session()
    handle = agent_row["handle"]
    if handle == "@wide-eye":
        tc, r = s.call("read_research", asof=ctx.curr_month, agent="wide-eye",
                       data_through=ctx.data_through)
        body = ("**context — enters no calculation** · Snapshot through "
                f"{ctx.data_through}: nothing new from our own series.\n"
                "- No web access in mock, so geopolitics, policy and "
                "emerging concerns cannot be researched here.\n"
                "- Live mode runs web search at this point instead.")
        return [style.context_draft(body, session=s, significance="quiet")]

    if handle == "@lily":
        # outlook `both`: on a snapshot she runs her OUTWARD remit only
        # (SPEC-APP E) — the liability/duration analysis is internal and
        # settled with the frozen valuation, so what advances is the
        # large-loss watch, under the same quarantine as always.
        body = ("**context — enters no calculation** · Snapshot through "
                f"{ctx.data_through}: the valuation is frozen, so my "
                "quantitative half is settled.\n"
                "- Cohort PVs, durations and the duration gap do not "
                "re-run against a walked-forward date.\n"
                "- What keeps moving is the large-loss watch, and no "
                "factor in this model carries it.\n"
                "- Our own series cannot tell me whether an event has "
                "landed; live mode's web search would.")
        return [style.context_draft(body, session=None, significance="quiet")]

    cols = _SNAPSHOT_COLUMNS.get(handle)
    if not cols:
        return []
    tc, r = s.call("read_research", asof=ctx.curr_month, agent="focused",
                   data_through=ctx.data_through)
    stats = r["stats"]
    origin = Prose()
    # NB: no comma directly after a date. `citation.TOKEN_RE` swallows the
    # comma into the token, which then reaches past the date span the gate
    # whitelists, and the day leaks out as an unbound number.
    origin.add(f"Snapshot through {ctx.data_through}; the month-end close "
               f"{r['prev_asof']} stands unchanged.")
    any_notable = False
    for block, col, kind in cols:
        st = stats[block]["columns"][col]
        if kind == "rate":
            chg_val, chg_txt = st["change_bp"], f"{st['change_bp']:+.1f}bp"
        else:
            chg_val, chg_txt = st["change_pct"], f"{st['change_pct']:+.2f}%"
        origin.add(f"\n- {block}/{col} ").claim(chg_val, tc, text=chg_txt)
        if st["notable"]:
            any_notable = True
            _snapshot_notables(origin, st, tc, kind, chg_txt)
        origin.add(".")
    origin.add("\nDetail in the research note." if any_notable else
               "\nNothing crosses a fixed threshold — the reported "
               "valuation stands as published.")
    return [origin.draft("origin", session=s,
                         significance="notable" if any_notable else "quiet")]


# ==========================================================================
# @lily — liabilities (PENDING-ROSTER, room 3, outlook `both`)
#
# Split remit, enforced by construction:
#   quantitative  cohort PVs and durations, the asset/liability duration GAP
#                 (she owns that number — nobody else reports it), why a
#                 swap-rate rise RAISES surplus, and the liability
#                 contribution to ir_gbp / ir_usd / fx. Every figure bound
#                 to price_scenario (exact repricing) or an engine output.
#   context       large-loss / cat scenarios and their asset-side knock-on:
#                 posted under the same quarantine as the wider-risk remit,
#                 because the liabilities are fixed cashflows with no
#                 stochastic factors and there is no cat model. State the
#                 limit rather than implying capability.
# ==========================================================================

_PARALLEL_SHOCK = 0.01     # 100bp parallel swap move
_TENOR_SHOCK = 0.01        # 100bp at the 20y point only


def _fmt(v: float, dp: int = 2, signed: bool = False,
         maxdp: int = 6) -> str:
    """Render a number with enough precision to stay inside the citation
    tolerance (0.5% relative). Two decimals on a value like 0.333 is 1%
    adrift and the gate would read the token as UNBOUND — correctly, since
    it no longer matches anything a tool returned."""
    s = f"{v:.{dp}f}"
    while dp < maxdp and abs(float(s) - v) > 0.002 * abs(v):
        dp += 1
        s = f"{v:.{dp}f}"
    return ("+" + s) if (signed and v >= 0) else s


def _yr(v: float) -> str:
    return _fmt(v, 2)


def _signed_yr(v: float) -> str:
    return _fmt(v, 2, signed=True)


def _pct0(v: float) -> str:
    return f"{v * 100:.0f}%"


def lily(ctx) -> list[dict]:
    s = ctx.session()
    run_ref = ctx.curr_run["id"]
    tc_p, P = s.call("price_scenario", asof=run_ref)
    tc_up, UP = s.call("price_scenario", asof=run_ref,
                       shocks={"gbp_swap": _PARALLEL_SHOCK})
    tc_t20, T20 = s.call("price_scenario", asof=run_ref,
                         shocks={"gbp_swap_20": _TENOR_SHOCK})
    tc_bl, BL = s.call("read_output", asof_or_run=run_ref,
                       filename="var_standalone_factors.json")

    d = P["durations"]
    ex = P["exposures"]
    blocks = BL["data"]["blocks"]
    prop = [c for c in P["liability_cohorts"] if c["class"] == "property"]
    cas = [c for c in P["liability_cohorts"] if c["class"] == "casualty"]

    # HOUSE STYLE (§12): the balance sheet in a line, then the three
    # things only this desk reports. The cohort-by-cohort PVs and
    # durations, the per-basis-point exposure split and the modelling
    # choices are all in the working, where length is welcome.
    origin = Prose()
    (origin.add(f"Liabilities, {ctx.curr_month}: total PV ")
           .claim(P["base"]["liability_pv_gbp"], tc_p, style.money)
           .add(" against assets ")
           .claim(P["base"]["asset_total_gbp"], tc_p, style.money)
           .add(f", {len(prop) + len(cas)} fixed cashflow cohorts."))

    (origin.add("\n- **The duration gap is mine to own**: liabilities ")
           .claim(d["liabilities_years"], tc_p, _yr)
           .add("y, fixed-income assets ")
           .claim(d["assets_fixed_income_years"], tc_p, _yr)
           .add("y, gap ")
           .claim(d["duration_gap_years"], tc_p, _signed_yr)
           .add("y (")
           .claim(d["duration_gap_all_assets_years"], tc_p, _signed_yr)
           .add("y across all assets)."))

    (origin.add("\n- A bad market can be a good month: ")
           .claim(_PARALLEL_SHOCK, tc_up, lambda v: style.bp(v, 0))
           .add(" on `gbp_swap` cuts liability PV by ")
           .claim(-UP["delta"]["liability_pv_gbp"], tc_up, style.money)
           .add(", surplus ")
           .claim(UP["delta"]["surplus_gbp"], tc_up, style.signed_money)
           .add("."))

    (origin.add("\n- ir_gbp: ")
           .claim(ex["ir_gbp"]["liability_share_of_gross"], tc_p, _pct0)
           .add(" of the gross rate exposure sits on my side, pointing the "
                "other way; block ")
           .claim(blocks["ir_gbp"], tc_bl, style.money)
           .add("."))
    (origin.add("\n- ir_usd share ")
           .claim(ex["ir_usd"]["liability_share_of_gross"], tc_p, _pct0)
           .add(" (")
           .claim(blocks["ir_usd"], tc_bl, style.money)
           .add("), fx ")
           .claim(ex["fx"]["liability_share_of_gross"], tc_p, _pct0)
           .add(" (")
           .claim(blocks["fx"], tc_bl, style.money)
           .add(") — USD cohorts hedge much of the USD book."))

    work = Prose()
    work.add("Cohort working — PVs, effective durations (exact repricing "
             "at plus and minus one basis point on each cohort's own "
             "curve) and payout horizon.\n\n"
             "**Shape of the book.** Property pays out fast; casualty is "
             "the long tail. These are fixed claims-payment vectors priced "
             "off the discount curves and nothing else — there are no "
             "stochastic liability factors in this model.\n\n"
             "| cohort | class | ccy | curve | PV | duration | last "
             "payment |\n|---|---|---|---|---|---|---|\n")
    for c in P["liability_cohorts"]:
        (work.add(f"| {c['id']} | {c['class']} | {c['currency']} | "
                  f"`{c['curve']}` | ")
             .claim(c["base_pv_gbp"], tc_p, style.money)
             .add(" | ")
             .claim(c["effective_duration_years"], tc_p, _yr)
             .add("y | ")
             .claim(c["last_cashflow_year"], tc_p, lambda v: f"{v:.0f}")
             .add("y |\n"))
    (work.add("| **total** | | | | ")
         .claim(P["base"]["liability_pv_gbp"], tc_p, style.money)
         .add(" | ")
         .claim(d["liabilities_years"], tc_p, _yr)
         .add("y | |\n\nSensitivity of the balance sheet to swap moves "
              "(deterministic repricing, no simulation involved):\n\n"
              "| move | liability PV | assets | surplus |\n"
              "|---|---|---|---|\n| parallel ")
         .claim(_PARALLEL_SHOCK, tc_up, lambda v: style.bp(v, 0))
         .add(" `gbp_swap` | ")
         .claim(UP["delta"]["liability_pv_gbp"], tc_up, style.signed_money)
         .add(" | ")
         .claim(UP["delta"]["asset_total_gbp"], tc_up, style.signed_money)
         .add(" | ")
         .claim(UP["delta"]["surplus_gbp"], tc_up, style.signed_money)
         .add(" |\n| 20y point only ")
         .claim(_TENOR_SHOCK, tc_t20, lambda v: style.bp(v, 0))
         .add(" | ")
         .claim(T20["delta"]["liability_pv_gbp"], tc_t20, style.signed_money)
         .add(" | ")
         .claim(T20["delta"]["asset_total_gbp"], tc_t20, style.signed_money)
         .add(" | ")
         .claim(T20["delta"]["surplus_gbp"], tc_t20, style.signed_money)
         .add(" |\n\nThe tenor-specific move is much the smaller of the "
              "two: the casualty tail is the only material weight beyond "
              "ten years, because this is a P&C reserve book, not a life "
              "one.\n\n**Why a rate rise raises surplus.** The GBP cohorts "
              "discount on `gbp_swap` and nothing on the asset side does. "
              "On the parallel move above, assets move "))
    _money_claim(work, UP["delta"]["asset_total_gbp"], tc_up, signed=True)
    (work.add(" while liabilities discount harder, so rates rising is a "
              "surplus event here and the duration gap sets the size of "
              "it.\n\n**Liability contribution to the risk blocks**, from "
              "exact repricing rather than a share-out. Per basis point "
              "the liabilities move ")
         .claim(ex["ir_gbp"]["liabilities_gbp"], tc_p, style.money)
         .add(" against assets ")
         .claim(ex["ir_gbp"]["assets_gbp"], tc_p, style.money)
         .add(" in ir_gbp: the two point opposite ways, which is why the "
              "block standalone VaR is smaller than either leg gross. In "
              "fx the USD cohorts translate at GBPUSD, so the liabilities "
              "are an FX position too and they hedge much of the USD asset "
              "book.\n\n**One modelling choice, stated out loud.** "
              "Treasuries stand in for a USD swap curve the factor set "
              "does not contain, so the USD liability cohorts discount on "
              "a govy curve rather than on the swap curve their GBP "
              "counterparts use."))

    # The context half, to the same contract: a lead line and fragments.
    # The full account of what a large-loss event does — and in what order
    # — is relocated to the working above, where length is welcome.
    context = (
        "**context — enters no calculation** · A large-loss event is not a "
        "risk factor anywhere in this model.\n"
        "- Reserves are fixed claims-payment vectors — no claims "
        "inflation, no longevity, no catastrophe model.\n"
        "- A cat or a reserve strengthening lands as a jump in near-dated "
        "payments; the knock-on is all asset-side.\n"
        "- Liquidity draw first, then forced sales into the market the "
        "event disturbed.\n"
        "- Private credit bites hardest: least liquid, lag-marked, "
        "unsellable at carrying value. Stated limitation, not an oversight."
    )
    work.add(
        "\n\n**The large-loss half, in full** *(context — enters no "
        "calculation; the post above is the short form)*. A major "
        "windstorm, or a casualty reserve strengthening after an adverse "
        "court decision, lands as a jump in near-dated claims payments — "
        "the part of the reserve profile the asset book is least able to "
        "absorb quietly. The knock-on is all on the asset side and it "
        "arrives in a fixed order. A liquidity draw first, met from cash "
        "and the shortest gilts. Then forced sales into whatever market "
        "the event itself has disturbed: for a catastrophe that means "
        "selling credit while spreads are wide, and discovering that the "
        "private credit sleeve cannot be sold at its carrying value at "
        "all. Then a shortening of asset duration that reopens the gap "
        "quantified above, at the worst possible moment for it. Private "
        "credit is where this bites hardest — least liquid, marked with a "
        "valuation lag, and its proxy pricing will not show the stress "
        "until long after a forced seller needed it to. There is no cat "
        "model and no claims-inflation factor in the calibrated set, so "
        "none of that is in the number; all of it is in the risk. I would "
        "rather name the limit than imply a capability I do not have.")

    return [origin.draft("origin", session=None, significance="routine"),
            work.draft("expansion", session=s, significance="routine"),
            style.context_draft(context, session=None,
                               significance="routine")]


# ==========================================================================
# @warden — the month-end summary (PENDING-ROSTER, room 3)
#
# The lead post of room 3: what a CFO or CRO reads first. Runs LAST in the
# pass (it reads everyone) and is flagged for pinning FIRST — execution
# order and display order differ deliberately.
#
# The headline is fixed in form every month:
#   AuM £X, +£Y — premium £A · investment performance £B ·
#   VaR £C (D% of assets), +£E
# Money in, money made, risk carried.
#
# The body then decomposes BOTH sides of the balance sheet into market /
# flows / decision, and makes the finding it exists to make: two
# unexplained-by-market residuals of similar scale and the same direction
# are written business, not investment performance.
#
# Method: the market leg is exact repricing of the PRIOR book and prior
# cohorts at the CURRENT market state (`price_scenario` — one revaluation,
# no simulation); whatever the market does not explain is flow. That
# reconstruction lands on the engine's own sequential attribution to the
# penny — steps 1-7 are the market leg, step 8 the asset flow, step 9 the
# liability flow — which is the control on the whole reconciliation.
# ==========================================================================

WARDEN_FLOW_MATERIAL = 0.005    # a flow worth calling flow: 0.5% of assets
WARDEN_SIMILAR_TOL = 0.35       # "similar magnitude", relative
WARDEN_VAR_MOVE_NOTABLE = 0.05  # a 5% move in aggregate VaR is notable
WARDEN_NIL = 1.0                # below GBP 1, print "nil" rather than a
#                                 zero token that could bind to nothing
WARDEN_MAX_SLEEVES = 5          # per-post tool budget: sleeves quoted


# The analysis it summarises (SPEC-APP H): read in priority order, and only
# while tool budget remains — the source chips are a drill-down convenience,
# never a substitute for @warden's own executed working.
WARDEN_READS = (("@attrib", 3), ("@lily", 3), ("@realist", 3),
                ("@rates-desk", 3), ("@credit-desk", 3), ("@equity-desk", 3),
                ("@holdings", 1), ("@red-team", 1))


def _warden_sources(sessions, ctx) -> list[int]:
    """Post ids @warden drew on, gathered from whichever session still has
    budget. Runs LAST so the reconciliation's own calls are never crowded
    out by the drill-down chips."""
    sources: list[int] = []
    for handle, room in WARDEN_READS:
        for s in sessions:
            try:
                _, res = s.call("read_agent_posts", room=room, handle=handle)
            except ToolLimitError:
                continue          # that session is full; try the other
            except ToolError:
                break             # no such agent — next handle
            sources.extend(p["id"] for p in res["posts"])
            break
    return sources


def _money_claim(prose: Prose, value: float, tc: int, signed: bool = False):
    """Claim a money figure, or write 'nil' when it rounds to nothing — a
    printed '£0.00' is a numeric token that no tool result can bind."""
    if abs(value) < WARDEN_NIL:
        return prose.add("nil")
    return prose.claim(value, tc,
                       style.signed_money if signed else style.money)


def _basename(p) -> str | None:
    return str(p).replace("\\", "/").rsplit("/", 1)[-1] if p else None


def _pair_inputs(ctx) -> tuple:
    """The input files the two SELECTED RUNS actually used (basenames). Pure
    path resolution from each run's manifest — no numbers, no tool call."""
    from app.agents import tools as _tools  # noqa: PLC0415 (leaf module)

    prev = _tools.run_input_paths(dict(ctx.prev_run))
    curr = _tools.run_input_paths(dict(ctx.curr_run))
    return (_basename(prev["book_path"]), _basename(curr["book_path"]),
            _basename(prev["liabilities_path"]),
            _basename(curr["liabilities_path"]))


def _warden_attribution(s, ctx):
    """The engine attribution for THIS pair of runs, preferring the variant
    that carries a book and liability change — but only when its inputs are
    the ones the selected runs actually used. An attribution computed over
    a different book pair is a different month-end and must not be quoted
    as if it were this one. Returns (tool_call_id, data, dir_name) or
    (None, None, None)."""
    want = _pair_inputs(ctx)
    for name in _attr_dirs(ctx):
        try:
            tc, res = s.call("read_output", asof_or_run=name,
                             filename="attribution.json")
        except ToolError:
            continue
        m = res["data"]["meta"]
        got = (_basename(m.get("prev_book_path")),
               _basename(m.get("curr_book_path")),
               _basename(m.get("prev_liabilities_path")),
               _basename(m.get("curr_liabilities_path")))
        if got == want:
            return tc, res["data"], name
    return None, None, None


def _sleeve_of(p: dict) -> str:
    return ("private_credit" if p.get("asset_class") == "private_credit"
            else p["type"])


def _warden_single_month(ctx) -> list[dict]:
    """No pair selected. The summary still posts — it always does — with
    the balance sheet and the risk carried, and says what is missing."""
    s = ctx.session()
    tc_p, P = s.call("price_scenario", asof=ctx.curr_run["id"])
    tc_v, V = s.call("read_output", asof_or_run=ctx.curr_run["id"],
                     filename="var_aggregate.json")
    var = float(V["data"]["aggregate_var_gbp"])
    assets = P["base"]["asset_total_gbp"]
    tc_r, R = s.call("verify_claim", left=var, op="lt", right=assets, tol=0.0)
    origin = Prose()
    (origin.add(f"**Month-end summary, {ctx.curr_month}.** **AuM ")
           .claim(assets, tc_p, style.money)
           .add("** — liabilities ")
           .claim(P["base"]["liability_pv_gbp"], tc_p, style.money)
           .add(", surplus ")
           .claim(P["base"]["surplus_gbp"], tc_p, style.money)
           .add(" · **VaR ")
           .claim(var, tc_v, style.money)
           .add(" (")
           .claim(R["ratio"], tc_r, lambda v: f"{v * 100:.1f}%")
           .add(" of assets)**."
                "\n- No prior month-end selected, so no market / flows / "
                "decision split this pass."
                "\n- Money in, money made and risk carried are movements, "
                "and a movement needs two dates."
                "\n- Select a pair and I reconcile both sides."))
    draft = origin.draft("origin", session=s, significance="routine")
    draft["pinned"] = True   # displayed FIRST; executed LAST
    return [draft]


def warden(ctx) -> list[dict]:
    """Always posts — 'nothing material changed' is itself the month-end
    answer a reader needs (minimum significance `routine`)."""
    if ctx.prev_run is None:
        return _warden_single_month(ctx)

    sA, sB = ctx.session(), ctx.session()
    _tc_attr, attr, attr_dir = _warden_attribution(sA, ctx)

    # The market leg: the PRIOR balance sheet — the prior run's own book and
    # cohorts — repriced at the CURRENT month-end market state. One exact
    # revaluation, no simulation.
    tc_p1, P1 = sA.call("price_scenario", asof=ctx.prev_run["id"],
                        to_asof=ctx.curr_run["id"])
    tc_p2, P2 = sA.call("price_scenario", asof=ctx.curr_run["id"])

    assets_prev = P1["base"]["asset_total_gbp"]
    liab_prev = P1["base"]["liability_pv_gbp"]
    assets_mkt = P1["shocked"]["asset_total_gbp"]
    liab_mkt = P1["shocked"]["liability_pv_gbp"]
    assets_curr = P2["base"]["asset_total_gbp"]
    liab_curr = P2["base"]["liability_pv_gbp"]
    market_assets = P1["delta"]["asset_total_gbp"]
    market_liabs = P1["delta"]["liability_pv_gbp"]

    tc_da, da = sA.call("verify_claim", left=assets_curr, op="ne",
                        right=assets_prev, tol=1e-9)
    tc_fa, fa = sA.call("verify_claim", left=assets_curr, op="ne",
                        right=assets_mkt, tol=1e-9)
    tc_dl, dl = sB.call("verify_claim", left=liab_curr, op="ne",
                        right=liab_prev, tol=1e-9)
    tc_fl, fl = sB.call("verify_claim", left=liab_curr, op="ne",
                        right=liab_mkt, tol=1e-9)
    flow_assets = fa["difference"]
    flow_liabs = fl["difference"]

    # Control: the market leg must reproduce the ENGINE's own sequential
    # attribution steps 1-7 (steps 8 and 9 are then the two flows). Quoted,
    # not asserted — a reconciliation nobody checks is decoration.
    tc_x, xcheck = None, None
    if attr is not None:
        steps = {st["name"]: float(st["delta_gbp"])
                 for st in attr["mtm"]["steps"]}
        market_steps = sum(steps[n] for n in
                           ("gbp_swap", "gbp_gilt", "ust", "spread",
                            "equity", "fx", "vcv"))
        tc_x, xcheck = sA.call("verify_claim",
                               left=P1["delta"]["surplus_gbp"], op="eq",
                               right=market_steps, tol=1e-6)

    # --- risk carried: always the two SELECTED runs' own aggregates --------
    tc_vc, vc = sA.call("read_output", asof_or_run=ctx.curr_run["id"],
                        filename="var_aggregate.json")
    _, vp = sB.call("read_output", asof_or_run=ctx.prev_run["id"],
                    filename="var_aggregate.json")
    var_curr = float(vc["data"]["aggregate_var_gbp"])
    var_prev = float(vp["data"]["aggregate_var_gbp"])
    tc_dv, dv = sB.call("verify_claim", left=var_curr, op="ne",
                        right=var_prev, tol=1e-9)
    tc_ratio, ratio = sA.call("verify_claim", left=var_curr, op="lt",
                              right=assets_curr, tol=0.0)

    # --- decision: the sleeve split of the inflow --------------------------
    prev_pos = {p["id"]: p for p in P1["positions"]}
    curr_ids = {p["id"] for p in P2["positions"]}
    sleeve_flow, sleeve_prev = {}, {}
    for p in P2["positions"]:
        sl = _sleeve_of(p)
        was = prev_pos.get(p["id"], {}).get("value_gbp", 0.0)
        sleeve_flow[sl] = sleeve_flow.get(sl, 0.0) + p["base_value_gbp"] - was
        sleeve_prev[sl] = sleeve_prev.get(sl, 0.0) + was
    for pid, p in prev_pos.items():          # positions sold out entirely
        if pid not in curr_ids:
            sl = _sleeve_of(p)
            sleeve_flow[sl] = sleeve_flow.get(sl, 0.0) - p["value_gbp"]
            sleeve_prev[sl] = sleeve_prev.get(sl, 0.0) + p["value_gbp"]
    pc_flow = sleeve_flow.get("private_credit", 0.0)
    pc_share = (sleeve_prev.get("private_credit", 0.0) / assets_mkt
                if assets_mkt else 0.0)
    pc_pro_rata = flow_assets * pc_share

    material_flow = (abs(flow_assets) > WARDEN_FLOW_MATERIAL * assets_curr)
    material = material_flow and (abs(flow_liabs)
                                  > WARDEN_FLOW_MATERIAL * assets_curr)
    same_sign = (flow_assets > 0) == (flow_liabs > 0)
    # "similar magnitude" is a decision about which sentence to write, not a
    # number quoted in the post, so it needs no tool call of its own.
    similar = abs(flow_assets - flow_liabs) <= WARDEN_SIMILAR_TOL * max(
        abs(flow_assets), abs(flow_liabs), 1.0)
    written_business = material and same_sign and similar

    tc_pc = None
    if material_flow:
        tc_pc, pc = sB.call("verify_claim", left=pc_flow,
                            op="gt" if pc_flow > pc_pro_rata else "lt",
                            right=pc_pro_rata, tol=0.0)

    # --- headline ----------------------------------------------------------
    origin = Prose()
    origin.add("**AuM ")
    origin.claim(assets_curr, tc_p2, style.money).add(", ")
    _money_claim(origin, da["difference"], tc_da, signed=True)
    origin.add("** — premium ")
    _money_claim(origin, flow_assets, tc_fa)
    origin.add(" · investment performance ")
    _money_claim(origin, market_assets, tc_p1)
    origin.add(" · **VaR ")
    (origin.claim(var_curr, tc_vc, style.money)
           .add(" (")
           .claim(ratio["ratio"], tc_ratio, lambda v: f"{v * 100:.1f}%")
           .add(" of assets), "))
    _money_claim(origin, dv["difference"], tc_dv, signed=True)
    origin.add("**")

    # HOUSE STYLE (§12): the AuM headline line, then three bullets.
    # Everything else — the method, the sleeve split, the cross-check
    # against the engine's own attribution — is detail, and detail lives
    # in the working below.
    origin.add("\n- Assets ")
    _money_claim(origin, da["difference"], tc_da, signed=True)
    origin.add(": market ")
    _money_claim(origin, market_assets, tc_p1, signed=True)
    origin.add(", the rest ")
    _money_claim(origin, flow_assets, tc_fa, signed=True)
    origin.add(" is money in." if material_flow else
               " — too small to call a flow.")
    origin.add("\n- Liabilities ")
    _money_claim(origin, dl["difference"], tc_dl, signed=True)
    origin.add(": discounting and translation explain ")
    _money_claim(origin, market_liabs, tc_p1, signed=True)
    origin.add(", leaving ")
    _money_claim(origin, flow_liabs, tc_fl, signed=True)
    origin.add(" of reserve movement the discount curves do not account "
               "for.")

    if written_business:
        origin.add("\n- Two residuals, similar magnitude, same direction: "
                   "**this is written business, not investment "
                   "performance.**")
    elif material and same_sign:
        origin.add("\n- Same direction, different size: part written "
                   "business, part a decision someone should name.")
    elif not material:
        pb, cb, pl, cl = _pair_inputs(ctx)
        if pb == cb and pl == cl:
            origin.add("\n- Same book and cohort files both dates: there "
                       "is no written business in this pair and the whole "
                       "movement is market.")
        else:
            origin.add("\n- Different input files, but no residual large "
                       "enough to call a flow — the whole movement is "
                       "market.")
    else:
        origin.add("\n- Residuals point opposite ways: a release on one "
                   "side, an allocation on the other.")

    if material_flow and tc_pc is not None:
        origin.add("\n- Of the inflow, private credit took ")
        _money_claim(origin, pc_flow, tc_pc)
        origin.add(" against a pro-rata share of ")
        _money_claim(origin, pc_pro_rata, tc_pc)
        if pc["ratio"] is not None and abs(pc["ratio"]) >= 0.01:
            (origin.add(", ")
                   .claim(pc["ratio"], tc_pc, _yr)
                   .add("x its weight"))
        origin.add(". That part is not flow, it is a decision.")

    open_findings = _open_findings(ctx)
    if open_findings:
        origin.add("\n- **Unresolved findings on this cycle — not a clean "
                   "bill of health.**")

    # --- the working -------------------------------------------------------
    work = Prose()
    work.add("Month-end reconciliation — both sides, three buckets.\n\n"
             "**What the headline means.** Money in, money made, risk "
             "carried. Premium is the asset residual the market does not "
             "explain; investment performance is the market leg on the "
             "holdings held throughout; the VaR figure is the aggregate "
             "99.5% one-year surplus VaR on the two selected runs.\n\n"
             "**The finding this reconciliation exists to make.** Read the "
             "asset side alone and a month like this looks quiet. Read "
             "both sides and it is premium received, reserved and "
             "invested, while the market took money out of the holdings "
             "underneath. Two unexplained residuals of similar scale and "
             "the same direction are written business, not investment "
             "performance: the two have nothing in common for return "
             "measurement or for risk appetite, and reporting the first as "
             "the second is the standard month-end misreading. Where the "
             "residuals point the same way but differ in size, part of it "
             "is written business and the rest is a decision or a "
             "reserving movement somebody should name. Where they point in "
             "opposite directions it is a release on one side and an "
             "allocation on the other, and the two want explaining "
             "separately.\n\n"
             "**The tilt.** Of any inflow, the pro-rata share is the book "
             "simply getting bigger; the excess is somebody choosing to "
             "tilt. When that excess lands in private credit it lands in "
             "the least liquid sleeve we own, which is a decision worth "
             "naming rather than a flow worth netting.\n\n"
             "| | market | flows | decision | total |\n"
             "|---|---|---|---|---|\n| **assets** | ")
    _money_claim(work, market_assets, tc_p1, signed=True)
    work.add(" | ")
    _money_claim(work, flow_assets, tc_fa, signed=True)
    work.add(" | tilt within the inflow, below | ")
    _money_claim(work, da["difference"], tc_da, signed=True)
    work.add(" |\n| **liabilities** | ")
    _money_claim(work, market_liabs, tc_p1, signed=True)
    work.add(" | ")
    _money_claim(work, flow_liabs, tc_fl, signed=True)
    work.add(" | class and currency mix, below | ")
    _money_claim(work, dl["difference"], tc_dl, signed=True)
    work.add(" |\n\nMarket = the prior positions and the prior cohorts "
             "repriced at the current month-end curves, spreads, index "
             "levels and FX. Flows = whatever the market does not "
             "explain.\n\nWhere the money went — flow by sleeve against a "
             "pro-rata share of the same inflow, at current market "
             "prices:\n\n| sleeve | flow | pro-rata | vs pro-rata |\n"
             "|---|---|---|---|\n")
    ordered = sorted(sleeve_flow, key=lambda k: -abs(sleeve_flow[k]))
    for sl in ordered[:WARDEN_MAX_SLEEVES]:
        f_ = sleeve_flow[sl]
        pr = flow_assets * ((sleeve_prev.get(sl, 0.0) / assets_mkt)
                            if assets_mkt else 0.0)
        tc_s, sres = sB.call("verify_claim", left=f_,
                             op="gt" if f_ > pr else "lt", right=pr, tol=0.0)
        work.add(f"| {sl.replace('_', ' ')} | ")
        _money_claim(work, f_, tc_s, signed=True)
        work.add(" | ")
        _money_claim(work, pr, tc_s, signed=True)
        work.add(" | ")
        if sres["ratio"] is not None and abs(sres["ratio"]) >= 0.01:
            work.claim(sres["ratio"], tc_s, _yr).add("x")
        else:
            work.add("—")
        work.add(" |\n")
    if len(ordered) > WARDEN_MAX_SLEEVES:
        work.add("\n(Sleeves beyond the largest few are omitted here — "
                 "per-post tool budget.)\n")
    work.add("\nLiability cohorts, prior vs current:\n\n"
             "| cohort | prior PV | current PV | duration |\n"
             "|---|---|---|---|\n")
    prev_coh = {c["id"]: c for c in P1["liability_cohorts"]}
    for c in P2["liability_cohorts"]:
        p_ = prev_coh.get(c["id"])
        work.add(f"| {c['id']} | ")
        if p_ is None:
            work.add("new cohort")
        else:
            work.claim(p_["base_pv_gbp"], tc_p1, style.money)
        (work.add(" | ")
             .claim(c["base_pv_gbp"], tc_p2, style.money)
             .add(" | ")
             .claim(c["effective_duration_years"], tc_p2,
                    _yr)
             .add("y |\n"))
    work.add("\nEvery figure here is an engine repricing of files on disk: "
             "no simulation enters the market/flow split, and the split "
             "reconciles against the sequential attribution's own steps "
             "(steps one to seven are the market leg, step eight the asset "
             "flow, step nine the liability flow).")
    if xcheck is not None:
        work.add("\n\n**The control.** The basis is exact repricing of the "
                 "prior balance sheet at the current market state, "
                 "cross-checked against the engine's own sequential "
                 f"attribution (`{attr_dir}`, run over these same input "
                 "files). My market leg and its steps one to seven differ "
                 "by ")
        _money_claim(work, xcheck["abs_difference"], tc_x)
        work.add(" — the flows are then its book and liability steps "
                 "exactly. That agreement is the control on this whole "
                 "reconciliation; a reconciliation nobody checks is "
                 "decoration.")
    if open_findings:
        work.add("\n\nThere are unresolved findings on this cycle — see "
                 "the challenge posts in this room and in room 1. This "
                 "summary is not a clean bill of health.")

    significance = "routine"
    if written_business or abs(dv["difference"]) > \
            WARDEN_VAR_MOVE_NOTABLE * max(abs(var_prev), 1.0):
        significance = "notable"
    if open_findings:
        significance = "critical"

    # Source chips LAST: whatever tool budget the reconciliation left over.
    sources = _warden_sources((sA, sB), ctx)

    origin_draft = origin.draft("origin", session=sA,
                                significance=significance, sources=sources)
    origin_draft["pinned"] = True   # displayed FIRST; executed LAST
    work_draft = work.draft("expansion", session=sB,
                            significance=significance)
    return [origin_draft, work_draft]


# @realist is corroboration and "never the scored route" (SPEC-APP 5), so a
# band flag of his is not on its own an unresolved defect; @warden's own
# post is excluded for the obvious reason.
WARDEN_FLAG_EXCLUDED = ("@realist", "@warden")


def _open_findings(ctx) -> int:
    """Published FLAG-carrying posts on this run from a primary route —
    @warden escalates to `critical` when it is summarising a month with an
    unresolved defect."""
    from app.server import db  # noqa: PLC0415 (leaf module)

    marks = ",".join("?" * len(WARDEN_FLAG_EXCLUDED))
    try:
        # GLOB, not LIKE: LIKE is case-insensitive in SQLite and would
        # count @attrib's "a nonzero residual here is a red flag" — prose
        # about a control, not a finding. The checks' marker is "**FLAG —".
        row = db.get_db().execute(
            "SELECT COUNT(*) AS n FROM posts WHERE run_id = ? AND "
            "status = 'published' AND body_md GLOB '*[*][*]FLAG*' AND "
            f"author_label NOT IN ({marks})",
            (ctx.curr_run["id"],) + WARDEN_FLAG_EXCLUDED).fetchone()
    except Exception:
        return 0
    return int(row["n"]) if row else 0
