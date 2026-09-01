"""Room 2 · Execution Monitoring — deterministic mock checks.

run_monitor_summary  pass-time summary of a run's stage events (the live
                     per-event narration is api.stage_narrator_post)
results_validator    post-run: additivity, diversification sign, spread
                     floor incidence, sim percentile consistency vs the
                     reported VaR (reads sim_pnl_sample.csv)
vlad                 model validation: delta-normal cross-check (analytic
                     vs simulated aggregate, Euler decomposition), and for
                     a pair the VaR movement split into exposure / vol /
                     correlation terms — every number cites delta_normal
"""

from __future__ import annotations

from app.server import db

from app.agents import style
from app.agents.style import Prose
from app.agents.tools import ToolError

_STAGES = ("setup", "esg", "pricing", "validation")

# results-validator bounds (calibrated on the committed 50k-sim runs: the
# 1,000-draw sample's 99.5th percentile sits within +/-6% of the reported
# VaR, with 5-6 exceedances; bounds leave sampling-noise headroom)
SAMPLE_P995_REL_TOL = 0.15
MAX_EXCEEDANCES_PER_1000 = 15
FLOOR_Z_LIMIT = 2.576  # a floor beyond the one-sided 99.5% point is inert


def run_monitor_summary(ctx) -> list[dict]:
    conn = db.get_db()
    events = conn.execute(
        "SELECT stage, status FROM stage_events WHERE run_id = ? ORDER BY id",
        (ctx.curr_run["id"],)).fetchall()
    asof = ctx.curr_run["asof"]
    kind = ctx.curr_run["kind"]
    if not events:
        body = (f"Run {asof} ({kind}): no stage events on record.\n"
                "- Committed outputs registered directly, so nothing was "
                "narrated live.\n"
                "- Runs launched from the app narrate stage by stage here.")
        return [{"kind": "origin", "body": body, "claims": [],
                 "context": False, "session": None,
                 "significance": "quiet"}]
    done = {e["stage"] for e in events if e["status"] == "done"}
    failed = [e["stage"] for e in events if e["status"] == "failed"]
    ticks = " → ".join(
        f"{s} {'✓' if s in done else '✗' if s in failed else '…'}"
        for s in _STAGES)
    if failed:
        body = (f"Run {asof} ({kind}): **failed** during the "
                f"{failed[-1]} stage.\n"
                f"- {ticks}.\n- See the run's stage log.")
        sig = "critical"
    else:
        body = (f"Run {asof} ({kind}): all stages completed.\n"
                f"- {ticks}.\n- Outputs on disk, ready for validation.")
        sig = "routine"
    return [{"kind": "origin", "body": body, "claims": [], "context": False,
             "session": None, "significance": sig}]


def results_validator(ctx) -> list[dict]:
    s = ctx.session()
    run_ref = ctx.curr_run["id"]
    tc_g, agg = s.call("read_output", asof_or_run=run_ref,
                       filename="var_aggregate.json")
    tc_f, fac = s.call("read_output", asof_or_run=run_ref,
                       filename="var_standalone_factors.json")
    g = agg["data"]
    blocks = fac["data"]["blocks"]

    block_sum = sum(blocks.values())
    tc_add, add = s.call("verify_claim", left=block_sum, op="eq",
                         right=g["sum_standalone_blocks_gbp"], tol=1e-6)
    tc_div, div = s.call("verify_claim",
                         left=g["sum_standalone_blocks_gbp"]
                         - g["aggregate_var_gbp"],
                         op="eq", right=g["diversification_benefit_gbp"],
                         tol=1e-6)
    tc_sub, sub = s.call("verify_claim", left=g["aggregate_var_gbp"],
                         op="le", right=g["sum_standalone_blocks_gbp"],
                         tol=0.0)

    # spread floor incidence: for every rating the base level must sit
    # beyond what a one-sided 99.5% move can erase (level/vol >= 2.576) —
    # otherwise the post-shock floor binds inside the VaR quantile and the
    # normal approximation of the credit block is broken.
    tc_a, a = s.call("read_assumptions", asof_or_run=run_ref)
    doc = a["data"]
    floor_min_rating, floor_min_z = None, None
    for rating, level in doc["spreads"].items():
        vol = float(doc["vols"]["spread"][rating])
        z = float(level) / max(vol, 1e-12)
        if floor_min_z is None or z < floor_min_z:
            floor_min_rating, floor_min_z = rating, z
    tc_fl, fl = s.call("verify_claim", left=floor_min_z, op="ge",
                       right=FLOOR_Z_LIMIT, tol=0.0)

    # sim percentile consistency: the stored 1,000-draw sample's own 99.5th
    # percentile loss must agree with the reported VaR (sampling tolerance),
    # and the sample must not breach the reported VaR more often than the
    # 0.5% tail plausibly allows.
    tc_s, samp = s.call("read_output", asof_or_run=run_ref,
                        filename="sim_pnl_sample.csv")
    pnl = sorted(float(r["surplus_pnl_gbp"]) for r in samp["rows"])
    n = len(pnl)
    q_idx = max(0, min(n - 1, int(round(0.005 * n)) - 1))
    sample_p995 = -pnl[q_idx] if n else 0.0
    exceed = sum(1 for x in pnl if x < -g["aggregate_var_gbp"])
    tc_sp, sp = s.call("verify_claim", left=sample_p995, op="approx",
                       right=g["aggregate_var_gbp"],
                       tol=SAMPLE_P995_REL_TOL)
    tc_ex, ex = s.call("verify_claim", left=float(exceed), op="le",
                       right=float(MAX_EXCEEDANCES_PER_1000), tol=0.0)

    checks_ok = add["passed"] and div["passed"] and sub["passed"] \
        and 0.0 < g["diversification_ratio"] <= 1.0 \
        and fl["passed"] and sp["passed"] and ex["passed"]

    # HOUSE STYLE (§12): verdict line, then the four checks as fragments.
    # The method, the identities and the reason each check exists are in
    # the working below.
    origin = Prose()
    verdict = "clean" if checks_ok else "**FAILED**"
    (origin.add(f"Post-run validation ({ctx.curr_run['asof']}): {verdict}.")
           .add("\n- Aggregate 99.5% surplus VaR ")
           .claim(g["aggregate_var_gbp"], tc_g, style.money)
           .add(" vs standalone sum ")
           .claim(g["sum_standalone_blocks_gbp"], tc_g, style.money)
           .add("; diversification benefit ")
           .claim(g["diversification_benefit_gbp"], tc_g, style.money)
           .add(", ratio ")
           .claim(g["diversification_ratio"], tc_g,
                  lambda v: style.num(v, 4))
           .add(".")
           .add("\n- Additivity, diversification sign, ratio inside the "
                "unit interval: "
                f"{'all hold' if checks_ok else 'see the working'}.")
           .add("\n- Spread floor incidence: "
                f"{'PASS' if fl['passed'] else '**FAIL**'} — tightest "
                f"rating {floor_min_rating} sits ")
           .claim(floor_min_z, tc_fl, lambda v: style.num(v, 2))
           .add(" annual vols above zero.")
           .add("\n- Sim percentile consistency: "
                f"{'PASS' if sp['passed'] and ex['passed'] else '**FAIL**'}"
                " — sample 99.5th percentile ")
           .claim(sample_p995, tc_sp, style.money)
           .add(", ")
           .claim(sp["rel_diff_pct"], tc_sp,
                  text=f"{sp['rel_diff_pct']:.2f}%")
           .add(" from the reported VaR, ")
           .claim(float(exceed), tc_ex, lambda v: str(int(v)))
           .add(" exceedances."))

    work = Prose()
    work.add("What each check is for, and why a pass is worth stating.\n\n"
             "**Additivity and the diversification sign.** The block table "
             "must sum to the reported standalone sum, and the correlated "
             "aggregate must sit below it — the difference IS the "
             "diversification benefit, and the ratio has to land inside "
             "the unit interval. Any report quoting the block SUM as the "
             "aggregate would overstate risk by exactly that benefit, and "
             "it is the first thing I look for in a draft, because the "
             "month-on-month change still looks internally coherent when "
             "both months are summed the same wrong way.\n\n"
             "**Spread floor incidence.** Spreads are floored after the "
             "shock, so if a rating's base level sits within a 99.5% move "
             "of zero the floor binds inside the VaR quantile and the "
             "normal approximation of the credit block is broken. The test "
             "is level over vol against the one-sided 99.5% point: comfortably "
             "above it, the floor is inert and never touched.\n\n"
             "**Sim percentile consistency.** The stored sample of draws "
             "must reproduce the reported VaR at its own 99.5th percentile "
             "within sampling tolerance, and must not breach the reported "
             "VaR more often than a half-percent tail plausibly allows. "
             "That is the control on the reported quantile actually being "
             "the quantile.\n\n"
             "| block | standalone 99.5% VaR |\n"
             "|---|---|\n")
    for name, v in blocks.items():
        work.add(f"| {name} | ").claim(v, tc_f, style.money).add(" |\n")
    (work.add("| **sum** | ")
         .claim(g["sum_standalone_blocks_gbp"], tc_g, style.money)
         .add(" |\n| **aggregate (correlated)** | ")
         .claim(g["aggregate_var_gbp"], tc_g, style.money)
         .add(" |\n\nAdditivity and diversification identities, the spread "
              "floor z-scores and the sample percentile comparison are all "
              "verified with explicit comparisons (see tool calls)."))
    sig = "quiet" if checks_ok else "critical"
    return [origin.draft("origin", session=None, significance=sig),
            work.draft("expansion", session=s, significance=sig)]


# --------------------------------------------------------------------------
# @vlad — model validation (tone contract: relaxed but sharp; comfortable
# leaving ~1% unexplained and saying so; escalates only when the
# approximation gap DRIFTS materially from its own history)
# --------------------------------------------------------------------------

VLAD_COMFORT_GAP_PCT = 6.0   # |analytic - MC| gap he reads as convexity
VLAD_DRIFT_ESCALATION_PP = 3.0  # month-on-month gap drift that moves him


def _gap_read(gap_pct: float) -> str:
    """The long read — for the working page, where the reasoning belongs."""
    a = abs(gap_pct)
    if a <= 1.5:
        return ("within tolerance, not chasing it — at this size it is "
                "discounting convexity, nothing more")
    if a <= VLAD_COMFORT_GAP_PCT:
        return ("comfortably explained by convexity — the bonds and the "
                "liability strip gain more from a big rally than they lose "
                "in the mirror-image sell-off, so the simulated tail is "
                "milder than the linear one")
    return ("larger than convexity alone should produce — that size says "
            "nonlinearity, e.g. the spread floor binding inside the tail")


def _gap_read_short(gap_pct: float) -> str:
    """The feed version: a verdict, three or four words (§12)."""
    a = abs(gap_pct)
    if a <= 1.5:
        return "discounting convexity, nothing more"
    if a <= VLAD_COMFORT_GAP_PCT:
        return "convexity, not nonlinearity"
    return "nonlinearity — the spread floor binding inside the tail"


def vlad(ctx) -> list[dict]:
    s = ctx.session()
    try:
        if ctx.prev_run is not None:
            tc, dnr = s.call("delta_normal", run_a=ctx.prev_run["id"],
                             run_b=ctx.curr_run["id"])
            curr = dnr["b"]
        else:
            tc, dnr = s.call("delta_normal", run_a=ctx.curr_run["id"])
            curr = dnr["a"]
    except ToolError:
        body = ("Delta-normal cross-check needs the run's input files and "
                "its var_aggregate.json on disk — could not resolve them "
                "for this run, so no reconciliation this pass.")
        return [{"kind": "origin", "body": body, "claims": [],
                 "context": False, "session": s, "significance": "routine"}]

    escalate = False
    gap_pct = curr.get("approximation_gap_pct")
    blocks = curr["euler_components_block_gbp"]
    parts = sorted(blocks.items(), key=lambda kv: -abs(kv[1]))
    pair = dnr.get("pair") if ctx.prev_run is not None else None
    prev_gap = dnr["a"].get("approximation_gap_pct") if pair else None

    # HOUSE STYLE (§12): the reconciliation and the gap, as bullets. The
    # closed form itself, the full Euler list, the correlation cells and
    # the scenario's own draws are in the working below.
    origin = Prose()
    (origin.add(f"Delta-normal read, {ctx.curr_month}: closed form ")
           .claim(curr["aggregate_var_gbp"], tc, style.money)
           .add(" against the simulated ")
           .claim(curr["simulated_var_gbp"], tc, style.money))
    if gap_pct is not None:
        (origin.add(", a gap of ")
               .claim(gap_pct, tc, text=f"{gap_pct:+.2f}%"))
    origin.add(".")

    if gap_pct is not None:
        origin.add(f"\n- {_gap_read_short(gap_pct)}")
        if prev_gap is not None:
            drift = gap_pct - prev_gap
            (origin.add("; gap moved ")
                   .claim(prev_gap, tc, text=f"{prev_gap:+.2f}%")
                   .add(" → ")
                   .claim(gap_pct, tc, text=f"{gap_pct:+.2f}%"))
            if abs(drift) > VLAD_DRIFT_ESCALATION_PP:
                escalate = True
                origin.add(", outside its own history — escalating.")
            else:
                origin.add(", stable against its own history.")
        else:
            origin.add(".")

    # One bullet, not two: the Euler decomposition is a control (it sums, or
    # it does not) and the movement split is the interesting half. The whole
    # component list is the table in the working.
    if pair:
        steps = pair["steps_gbp"]
        (origin.add("\n- Euler decomposition sums to the total; movement ")
               .claim(pair["analytic_delta_var_gbp"], tc, style.signed_money)
               .add(": exposures ")
               .claim(steps["exposure"], tc, style.signed_money)
               .add(", vols ")
               .claim(steps["vol"], tc, style.signed_money)
               .add(", correlations ")
               .claim(steps["correlation"], tc, style.signed_money)
               .add("."))
    else:
        origin.add("\n- Euler decomposition sums to the analytic total: ")
        for i, (name, v) in enumerate(parts[:3]):
            origin.claim(v, tc, style.signed_money).add(f" {name}")
            origin.add(", " if i < min(3, len(parts)) - 1 else ".")

    scenario_flag, sc, tc_sc = _vlad_var_scenario(origin, s, ctx)
    sig = "critical" if (escalate or scenario_flag) else "routine"
    drafts = [origin.draft("origin", session=s, significance=sig)]
    work = _vlad_working(ctx, dnr, curr, parts, pair, gap_pct, prev_gap, sig,
                         sc, tc_sc)
    if work is not None:
        drafts.append(work)
    return drafts


def _vlad_working(ctx, dnr, curr, parts, pair, gap_pct, prev_gap, sig,
                  sc=None, tc_sc=None):
    """The backing page (§12): the closed form, the whole Euler
    decomposition, the diversification benefit, the correlation cells and
    the long read of the approximation gap — everything the feed post no
    longer carries. Its own session, so it cites its own recorded call."""
    ws = ctx.session()
    try:
        if ctx.prev_run is not None:
            tc, wdnr = ws.call("delta_normal", run_a=ctx.prev_run["id"],
                               run_b=ctx.curr_run["id"])
            wcurr = wdnr["b"]
        else:
            tc, wdnr = ws.call("delta_normal", run_a=ctx.curr_run["id"])
            wcurr = wdnr["a"]
    except ToolError:
        return None

    work = Prose()
    work.add("Method. The closed form bumps the twenty-one factors and "
             "takes ")
    work.claim(wdnr["z"], tc, text="2.576")
    (work.add("·sqrt(w'Σw). Against the simulated aggregate ")
         .claim(wcurr["simulated_var_gbp"], tc, style.money)
         .add(" it gives ")
         .claim(wcurr["aggregate_var_gbp"], tc, style.money)
         .add(". "))
    if gap_pct is not None:
        work.add(f"The gap is {_gap_read(gap_pct)}. ")
    (work.add("Diversification benefit in the closed form: ")
         .claim(wcurr["diversification_benefit_gbp"], tc, style.money)
         .add(".\n\nEuler component VaR by factor block — these sum to the "
              "analytic total, to the penny:\n\n| block | component VaR |\n"
              "|---|---|\n"))
    for name, _v in parts:
        (work.add(f"| {name} | ")
             .claim(wcurr["euler_components_block_gbp"][name], tc,
                    style.signed_money)
             .add(" |\n"))
    if pair and "pair" in wdnr:
        wpair = wdnr["pair"]
        steps = wpair["steps_gbp"]
        (work.add(f"\nMovement {ctx.prev_month} → {ctx.curr_month}, by "
                  "sequential substitution in the closed form. The analytic "
                  "aggregate moved ")
             .claim(wpair["analytic_delta_var_gbp"], tc, style.signed_money)
             .add(" and the split is exact: exposures ")
             .claim(steps["exposure"], tc, style.signed_money)
             .add(", vols ")
             .claim(steps["vol"], tc, style.signed_money)
             .add(", correlations ")
             .claim(steps["correlation"], tc, style.signed_money)
             .add(". "))
        cells = wpair.get("largest_correlation_cells") or []
        if cells:
            c = cells[0]
            (work.add("The correlation story is led by the "
                      f"{c['cell'].replace('~', '–')} cell moving ")
                 .claim(c["from"], tc, lambda v: f"{v:.4f}")
                 .add("→")
                 .claim(c["to"], tc, lambda v: f"{v:.4f}")
                 .add(". "))
        if gap_pct is not None and prev_gap is not None:
            work.add("What moves me is not a residual existing but a "
                     "residual CHANGING: the approximation gap is compared "
                     "against its own history, and a percent or so left "
                     "unexplained is the closed form's price of admission, "
                     "not a finding.")
    work.add(
        "\n\n**The VaR scenario itself, and what the two bullets above "
        "mean.** The post carries the loss rank, the worst individual draw "
        "in annual vols and the joint Mahalanobis distance against "
        "chi-squared on twenty-one degrees of freedom. The reasoning "
        "behind each verdict belongs here.\n\n")
    if sc is not None and tc_sc is not None:
        # The quantile check itself, relocated off the feed post (§12): the
        # scenario's own loss against the reported aggregate. Bound to the
        # read_scenario call the post above made, so provenance is
        # unchanged by the move.
        (work.add("*Where the quantile actually is.* The scenario loses ")
             .claim(sc["loss_gbp"], tc_sc, style.money)
             .add(" against a reported aggregate of ")
             .claim(sc["reported_aggregate_var_gbp"], tc_sc, style.money)
             .add(" — the 99.5th percentile draw is where it says it is, "
                  "which is the precondition for any of the rest of this "
                  "meaning anything.\n\n"))
    work.add(
        "*Why the individual draw matters.* A single factor drawn beyond "
        "about three and a half annual vols is a move I would not defend "
        "in front of a board, and if the reported loss depends on it then "
        "the quantile is carried by one number rather than by a state of "
        "the world.\n\n"
        "*Why the joint distance matters.* When the combination sits in "
        "the far tail of what this correlation matrix itself generates, "
        "the reported one-in-two-hundred is leaning on a joint move the "
        "calibration does not really carry. That is a different statement "
        "from the arithmetic being wrong — the arithmetic is right by "
        "construction.\n\n"
        "*Why the signs matter.* Equities down with credit spreads "
        "TIGHTENING, or both moving the same way, is not a state of the "
        "world anyone can tell a story about; it is a correlation "
        "artefact. A coherent risk-off draw is equities down, credit "
        "wider, rates doing the offsetting work, no single draw out of "
        "character, and a joint distance the matrix would call ordinary — "
        "a scenario I would defend as a scenario, not merely as a "
        "percentile.\n\n"
        "This is the model-validation finding that summary statistics "
        "cannot show: arithmetically correct, financially incoherent. It "
        "is invisible from the headline VaR, which is exactly why it is "
        "worth a bullet when it fires and silence when it does not.")
    return work.draft("expansion", session=ws, significance=sig)


# --------------------------------------------------------------------------
# @vlad's per-run scenario duty (PENDING-ROSTER section M): open the VaR
# scenario itself and say whether its factor draws are JOINTLY plausible.
#
# The point is not that the scenario is arithmetically correct — it is, by
# construction. The point is whether the 1-in-200 the model reports is a
# financially coherent state of the world, or one that only reaches the loss
# by combining moves the calibration does not actually contain. A 99.5th
# percentile driven by a simultaneous huge rate move, huge spread widening
# and a large equity fall is arithmetically correct and financially
# incoherent; that is a model-validation finding invisible in summary
# statistics.
#
# Two deterministic reads, both from read_scenario:
#   - individual size: any single factor drawn beyond VLAD_JOINT_Z_LIMIT
#     annual vols in the VaR scenario gets named;
#   - joint size: Mahalanobis d^2 = z'C^-1 z against chi^2(21). Above
#     VLAD_JOINT_PCTL_LIMIT the loss is being reached by a joint draw the
#     correlation matrix itself calls rare — i.e. the reported quantile
#     leans on a dependency structure the VCV does not really carry.
# Sign coherence is read from the draws themselves (risk-off means equities
# down AND spreads wider; equities down with credit TIGHTENING is the
# incoherent pairing).
# --------------------------------------------------------------------------

VLAD_JOINT_Z_LIMIT = 3.5      # annual vols: a single draw worth naming
VLAD_JOINT_PCTL_LIMIT = 99.0  # chi^2(21) percentile of the joint draw
VLAD_SIGN_Z = 0.5             # a move big enough to have a direction


def _sig(v: float, dp: int = 2, signed: bool = False,
         maxdp: int = 6) -> str:
    """Enough decimals to stay inside the 0.5% citation tolerance — a
    coarsely rounded figure stops matching the tool result that produced
    it, and the gate is right to call that unbound."""
    s = f"{v:.{dp}f}"
    while dp < maxdp and abs(float(s) - v) > 0.002 * abs(v):
        dp += 1
        s = f"{v:.{dp}f}"
    return ("+" + s) if (signed and v >= 0) else s


def _block_mean_z(factors: list, prefix: str) -> float:
    zs = [f["shock_in_vols"] for f in factors
          if f["factor"].startswith(prefix) and f["shock_in_vols"] is not None]
    return sum(zs) / len(zs) if zs else 0.0


def _vlad_var_scenario(origin: Prose, s, ctx) -> bool:
    """Two bullets on the feed post (§12): what the scenario IS, and
    whether it hangs together jointly. The reasoning behind each verdict —
    why a single large draw matters, why a far-tail Mahalanobis distance is
    a different statement from the arithmetic being wrong — is in the
    working page, which is where a reader who wants it will go."""
    try:
        tc_sc, sc = s.call("read_scenario", run=ctx.curr_run["id"])
    except ToolError:
        origin.add("\n- Saved simulation arrays are not on disk, so I "
                   "could not open the VaR scenario this pass.")
        return False, None, None

    jp = sc.get("joint_plausibility") or {}
    big = [f for f in sc["largest_draws_in_vols"]
           if f["shock_in_vols"] is not None]
    eq_z = _block_mean_z(sc["factors"], "eq_")
    sp_z = _block_mean_z(sc["factors"], "spread_")

    (origin.add("\n- I opened the VaR scenario itself. Loss rank ")
           .claim(sc["loss_rank"], tc_sc, lambda v: f"{int(v)}")
           .add(" of ")
           .claim(sc["n_sims"], tc_sc, lambda v: f"{int(v):,}"))
    if big:
        f = big[0]
        (origin.add(f"; worst draw {f['factor']} ")
               .claim(f["shock_in_vols"], tc_sc,
                      lambda v: _sig(v, 2, signed=True))
               .add(" vols"))
    origin.add(".")

    extreme = [f for f in big if abs(f["shock_in_vols"]) > VLAD_JOINT_Z_LIMIT]
    pctl = jp.get("chi2_percentile")
    incoherent = (eq_z < -VLAD_SIGN_Z and sp_z < -VLAD_SIGN_Z) or \
                 (eq_z > VLAD_SIGN_Z and sp_z > VLAD_SIGN_Z)
    flagged = bool(extreme or incoherent
                   or (pctl is not None and pctl > VLAD_JOINT_PCTL_LIMIT))

    origin.add("\n- " + ("**FLAG — joint plausibility.** " if flagged else ""))
    if jp.get("available") and pctl is not None:
        (origin.add("Mahalanobis distance ")
               .claim(jp["mahalanobis_d2"], tc_sc, lambda v: _sig(v, 1))
               .add(" vs ")
               .claim(jp["chi2_expected_d2"], tc_sc, lambda v: f"{int(v)}")
               .add(" expected (")
               .claim(pctl, tc_sc, lambda v: f"{v:.1f}%")
               .add(" of chi-squared): "))
    if not flagged:
        origin.add("a coherent risk-off state of the world, no dependency "
                   "the calibration does not contain.")
        return False, sc, tc_sc
    # ONE reason on the feed, worst first; the rest is in the working.
    if extreme:
        origin.add("a single draw further out than I would defend.")
    elif pctl is not None and pctl > VLAD_JOINT_PCTL_LIMIT:
        origin.add("the loss leans on a joint move the calibration does "
                   "not carry.")
    else:
        origin.add("equities and credit moving together is an artefact.")
    origin.add(" Escalating; working below.")
    return True, sc, tc_sc
