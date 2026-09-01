"""Month-end attribution CLI (SPEC section 5).

    python -m engine.attribution --prev outputs/2026-06/ --curr outputs/2026-07/ \
                         --prev-assumptions assumptions/2026-06.yaml \
                         --curr-assumptions assumptions/2026-07.yaml \
                         --book book/positions.json --liabilities book/liabilities.json \
                         --out outputs/attr_2026-06_to_2026-07/

Sequential re-pricing in a fixed order, one block updated at a time, starting
from the prior month-end state:

  1. gbp_swap base curve  2. gbp_gilt  3. ust  4. spread levels
  5. equity levels        6. fx        7. VCV (vol/correlation) update
  8. book changes         9. liability changes

Step 8 (SPEC section 5): the CLI accepts optional --prev-book/--curr-book in
place of the single --book. Steps 1-7 are priced on the prev book; after step
7 the state is (curr assumptions, prev book), and step 8 swaps to the curr
book — its delta MTM and delta VaR are the book-change contribution. With a
single --book, step 8 is structurally 0 and reported honestly as 0.

Step 9 (SPEC section 5): likewise, optional --prev-liabilities/
--curr-liabilities in place of the single --liabilities. Steps 1-8 are priced
on the prev liabilities; step 9 swaps to the curr liabilities — its delta MTM
and delta VaR are the liability-change contribution. With a single
--liabilities, step 9 is structurally 0 and reported honestly as 0.

Outputs attribution.json: MTM movement of surplus per step, VaR movement per
step, an explicit residual line (never absorbed), and a check that
steps + residual = total. Non-additivity is expected and reported, not hidden.

Totals are taken from the stored --prev/--curr run outputs; steps are
recomputed sequentially here, so the residual also captures any difference
between the stored runs and this recomputation.
"""

from __future__ import annotations

import argparse
import copy
import json
import os

from . import esg, pricing, var
from .run import (DEFAULT_SEED, DEFAULT_SIMS, load_inputs, sha256_file,
                  write_json, git_rev)

STEPS = [
    (1, "gbp_swap"),
    (2, "gbp_gilt"),
    (3, "ust"),
    (4, "spread"),
    (5, "equity"),
    (6, "fx"),
    (7, "vcv"),
    (8, "book"),
    (9, "liabilities"),
]


def _apply_step(assumptions: dict, curr: dict, name: str) -> dict:
    """Return a copy of `assumptions` with one block updated to `curr`."""
    a = copy.deepcopy(assumptions)
    if name == "gbp_swap":
        a["curves"]["gbp_swap"] = copy.deepcopy(curr["curves"]["gbp_swap"])
    elif name == "gbp_gilt":
        a["curves"]["gbp_gilt"] = copy.deepcopy(curr["curves"]["gbp_gilt"])
    elif name == "ust":
        a["curves"]["ust"] = copy.deepcopy(curr["curves"]["ust"])
    elif name == "spread":
        a["spreads"] = copy.deepcopy(curr["spreads"])
    elif name == "equity":
        a["equity"] = copy.deepcopy(curr["equity"])
    elif name == "fx":
        a["fx"] = copy.deepcopy(curr["fx"])
    elif name == "vcv":
        a["vols"] = copy.deepcopy(curr["vols"])
        a["correlation"] = copy.deepcopy(curr["correlation"])
    elif name == "book":
        pass  # book swap handled by the caller (positions, not assumptions)
    elif name == "liabilities":
        pass  # liability swap handled by the caller (cohorts, not assumptions)
    else:
        raise ValueError("unknown attribution step %r" % name)
    return a


def _mtm(assumptions, positions, liabilities, ref_levels) -> float:
    state = esg.base_state(assumptions)
    return float(pricing.surplus(positions, liabilities, state, ref_levels)[0])


def _agg_var(assumptions, positions, liabilities, ref_levels, seed, n_sims) -> float:
    shocks = esg.simulate_shocks(assumptions, n_sims, seed)
    return var.aggregate_var(assumptions, positions, liabilities, shocks,
                             ref_levels)


def _read_json(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def run_attribution(prev_dir, curr_dir, prev_assumptions_path,
                    curr_assumptions_path, book_path, liabilities_path,
                    out_dir, seed=None, n_sims=None,
                    prev_book_path=None, curr_book_path=None,
                    prev_liabilities_path=None,
                    curr_liabilities_path=None) -> dict:
    """Sequential attribution between two month-end runs.

    Book handling (SPEC section 5 step 8): pass either a single `book_path`
    (step 8 is structurally 0), or both `prev_book_path` and `curr_book_path`
    (steps 1-7 priced on the prev book; step 8 swaps prev book -> curr book).

    Liability handling (SPEC section 5 step 9): pass either a single
    `liabilities_path` (step 9 is structurally 0), or both
    `prev_liabilities_path` and `curr_liabilities_path` (steps 1-8 priced on
    the prev liabilities; step 9 swaps prev -> curr liabilities).
    """
    if prev_book_path is not None or curr_book_path is not None:
        if not (prev_book_path and curr_book_path):
            raise ValueError(
                "--prev-book and --curr-book must be given together")
    else:
        if book_path is None:
            raise ValueError(
                "either --book or both --prev-book/--curr-book are required")
        prev_book_path = curr_book_path = book_path

    if prev_liabilities_path is not None or curr_liabilities_path is not None:
        if not (prev_liabilities_path and curr_liabilities_path):
            raise ValueError(
                "--prev-liabilities and --curr-liabilities must be given "
                "together")
    else:
        if liabilities_path is None:
            raise ValueError(
                "either --liabilities or both --prev-liabilities/"
                "--curr-liabilities are required")
        prev_liabilities_path = curr_liabilities_path = liabilities_path

    prev_a, prev_book, prev_liabs = load_inputs(
        prev_assumptions_path, prev_book_path, prev_liabilities_path)
    curr_a, curr_book, curr_liabs = load_inputs(
        curr_assumptions_path, curr_book_path, curr_liabilities_path)
    positions_prev = prev_book["positions"]
    ref_levels_prev = prev_book.get("ref_index_levels")
    positions_curr = curr_book["positions"]
    ref_levels_curr = curr_book.get("ref_index_levels")

    prev_val = _read_json(os.path.join(prev_dir, "valuation.json"))
    curr_val = _read_json(os.path.join(curr_dir, "valuation.json"))
    prev_agg = _read_json(os.path.join(prev_dir, "var_aggregate.json"))
    curr_agg = _read_json(os.path.join(curr_dir, "var_aggregate.json"))

    # Simulation settings default to the current stored run's metadata.
    if seed is None:
        seed = int(curr_val["meta"].get("seed", DEFAULT_SEED))
    if n_sims is None:
        n_sims = int(curr_val["meta"].get("n_sims", DEFAULT_SIMS))

    # Totals from the stored month-end runs.
    mtm_prev_total = float(prev_val["surplus_gbp"])
    mtm_curr_total = float(curr_val["surplus_gbp"])
    var_prev_total = float(prev_agg["aggregate_var_gbp"])
    var_curr_total = float(curr_agg["aggregate_var_gbp"])
    mtm_total_change = mtm_curr_total - mtm_prev_total
    var_total_change = var_curr_total - var_prev_total

    # Sequential recomputation, starting from the prior month-end state.
    # Steps 1-7 are priced on the prev book and prev liabilities; step 8
    # swaps to the curr book; step 9 swaps to the curr liabilities.
    a = copy.deepcopy(prev_a)
    positions, ref_levels = positions_prev, ref_levels_prev
    liabilities = prev_liabs
    mtm_level = _mtm(a, positions, liabilities, ref_levels)
    var_level = _agg_var(a, positions, liabilities, ref_levels, seed, n_sims)
    start_mtm, start_var = mtm_level, var_level

    mtm_steps, var_steps = [], []
    for num, name in STEPS:
        a = _apply_step(a, curr_a, name)
        if name == "book":
            positions, ref_levels = positions_curr, ref_levels_curr
        elif name == "liabilities":
            liabilities = curr_liabs
        if (name == "book" and prev_book_path == curr_book_path) or \
           (name == "liabilities"
                and prev_liabilities_path == curr_liabilities_path):
            # Single --book / --liabilities: the step is structurally 0,
            # reported exactly as 0 (no recomputation noise).
            new_mtm, new_var = mtm_level, var_level
        else:
            new_mtm = _mtm(a, positions, liabilities, ref_levels)
            new_var = _agg_var(a, positions, liabilities, ref_levels,
                               seed, n_sims)
        mtm_steps.append({"step": num, "name": name,
                          "delta_gbp": new_mtm - mtm_level})
        var_steps.append({"step": num, "name": name,
                          "delta_gbp": new_var - var_level})
        mtm_level, var_level = new_mtm, new_var

    end_mtm, end_var = mtm_level, var_level

    mtm_sum = sum(s["delta_gbp"] for s in mtm_steps)
    var_sum = sum(s["delta_gbp"] for s in var_steps)
    # Explicit residuals: total (stored) minus the sum of sequential steps.
    mtm_residual = mtm_total_change - mtm_sum
    var_residual = var_total_change - var_sum

    def _check(sum_steps, residual, total):
        return {
            "sum_steps_gbp": sum_steps,
            "residual_gbp": residual,
            "sum_steps_plus_residual_gbp": sum_steps + residual,
            "total_gbp": total,
            "additive_within_1e-6": abs(sum_steps + residual - total) < 1e-6,
        }

    attribution = {
        "meta": {
            "prev_dir": str(prev_dir), "curr_dir": str(curr_dir),
            "prev_assumptions_path": str(prev_assumptions_path),
            "prev_assumptions_sha256": sha256_file(prev_assumptions_path),
            "curr_assumptions_path": str(curr_assumptions_path),
            "curr_assumptions_sha256": sha256_file(curr_assumptions_path),
            "prev_book_path": str(prev_book_path),
            "prev_book_sha256": sha256_file(prev_book_path),
            "curr_book_path": str(curr_book_path),
            "curr_book_sha256": sha256_file(curr_book_path),
            # Single-book compatibility aliases (equal to curr book).
            "book_path": str(curr_book_path),
            "book_sha256": sha256_file(curr_book_path),
            "prev_liabilities_path": str(prev_liabilities_path),
            "prev_liabilities_sha256": sha256_file(prev_liabilities_path),
            "curr_liabilities_path": str(curr_liabilities_path),
            "curr_liabilities_sha256": sha256_file(curr_liabilities_path),
            # Single-liabilities compatibility alias (equal to curr).
            "liabilities_sha256": sha256_file(curr_liabilities_path),
            "seed": int(seed), "n_sims": int(n_sims),
            "engine_git_rev": git_rev(),
            "step_order": [name for _, name in STEPS],
            "note": ("Totals are from the stored prev/curr outputs; steps are "
                     "sequential recomputations from the prev state. The "
                     "residual is explicit and never absorbed into a step."),
        },
        "mtm": {
            "prev_surplus_gbp": mtm_prev_total,
            "curr_surplus_gbp": mtm_curr_total,
            "total_change_gbp": mtm_total_change,
            "recomputed_prev_surplus_gbp": start_mtm,
            "recomputed_curr_surplus_gbp": end_mtm,
            "steps": mtm_steps,
            "residual_gbp": mtm_residual,
            "additivity_check": _check(mtm_sum, mtm_residual, mtm_total_change),
        },
        "var": {
            "prev_aggregate_var_gbp": var_prev_total,
            "curr_aggregate_var_gbp": var_curr_total,
            "total_change_gbp": var_total_change,
            "recomputed_prev_var_gbp": start_var,
            "recomputed_curr_var_gbp": end_var,
            "steps": var_steps,
            "residual_gbp": var_residual,
            "additivity_check": _check(var_sum, var_residual, var_total_change),
        },
    }

    os.makedirs(out_dir, exist_ok=True)
    write_json(os.path.join(out_dir, "attribution.json"), attribution)
    return attribution


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="python -m engine.attribution",
        description="Sequential month-end attribution of surplus MTM and VaR.")
    ap.add_argument("--prev", required=True, help="prior month-end output dir")
    ap.add_argument("--curr", required=True, help="current month-end output dir")
    ap.add_argument("--prev-assumptions", required=True)
    ap.add_argument("--curr-assumptions", required=True)
    ap.add_argument("--book", default=None,
                    help="single book for both month-ends (step 8 = 0)")
    ap.add_argument("--prev-book", default=None,
                    help="prior month-end book (with --curr-book; step 8 "
                         "carries the book change)")
    ap.add_argument("--curr-book", default=None,
                    help="current month-end book (with --prev-book)")
    ap.add_argument("--liabilities", default=None,
                    help="single liabilities file for both month-ends "
                         "(step 9 = 0)")
    ap.add_argument("--prev-liabilities", default=None,
                    help="prior month-end liabilities (with "
                         "--curr-liabilities; step 9 carries the liability "
                         "change)")
    ap.add_argument("--curr-liabilities", default=None,
                    help="current month-end liabilities (with "
                         "--prev-liabilities)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--sims", type=int, default=None)
    args = ap.parse_args(argv)
    if args.book is None and not (args.prev_book and args.curr_book):
        ap.error("either --book or both --prev-book/--curr-book are required")
    if args.liabilities is None and not (args.prev_liabilities
                                         and args.curr_liabilities):
        ap.error("either --liabilities or both --prev-liabilities/"
                 "--curr-liabilities are required")
    attr = run_attribution(args.prev, args.curr, args.prev_assumptions,
                           args.curr_assumptions, args.book, args.liabilities,
                           args.out, seed=args.seed, n_sims=args.sims,
                           prev_book_path=args.prev_book,
                           curr_book_path=args.curr_book,
                           prev_liabilities_path=args.prev_liabilities,
                           curr_liabilities_path=args.curr_liabilities)
    print("mtm_total_change_gbp=%.2f mtm_residual_gbp=%.6f "
          "var_total_change_gbp=%.2f var_residual_gbp=%.6f" % (
              attr["mtm"]["total_change_gbp"], attr["mtm"]["residual_gbp"],
              attr["var"]["total_change_gbp"], attr["var"]["residual_gbp"]))


if __name__ == "__main__":
    main()
