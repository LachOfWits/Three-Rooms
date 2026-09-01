"""Engine run CLI (SPEC section 4).

    python -m engine.run --assumptions assumptions/2026-07.yaml \
                         --book book/positions.json \
                         --liabilities book/liabilities.json \
                         --out outputs/2026-07/ [--seed N] [--sims N]

Writes to --out:
  valuation.json                per-position MV (GBP), asset total, liability
                                PV, surplus, run metadata (seed, n_sims,
                                assumptions path + sha256, book sha256,
                                engine git rev if available)
  var_standalone_positions.csv  per position full-factor standalone 99.5% 1y VaR
  var_standalone_factors.json   standalone VaR per factor block
  var_aggregate.json            full-correlation aggregate surplus VaR +
                                diversification vs sum of standalone blocks
  sim_pnl_sample.csv            first 1,000 simulated surplus P&Ls
  sim_pnl_positions.npy         (n_sims, n_pos+1) float32 P&L vs base
  sim_factors.npy               (n_sims, 21) float32 factor shocks
  sim_surplus.npy               (n_sims,) float32 surplus P&L
  sim_index.json                column names for the arrays above
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess

import numpy as np
import yaml

from . import esg, pricing, var

DEFAULT_SEED = 20260831
DEFAULT_SIMS = 50000
SAMPLE_ROWS = 1000


def _stage_log(path, stage: str, status: str) -> None:
    """Append one JSON line to the optional stage log (no-op when path is None).

    Purely observational: never touches inputs, outputs, or the RNG.
    """
    if path is None:
        return
    import datetime
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    line = json.dumps({
        "stage": stage,
        "status": status,
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    })
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(line + "\n")
        f.flush()
        os.fsync(f.fileno())


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def git_rev() -> str | None:
    """Engine git revision if available; None otherwise."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def load_inputs(assumptions_path, book_path, liabilities_path):
    with open(assumptions_path, "r", encoding="utf-8") as f:
        assumptions = yaml.safe_load(f)
    with open(book_path, "r", encoding="utf-8") as f:
        book = json.load(f)
    with open(liabilities_path, "r", encoding="utf-8") as f:
        liabilities = json.load(f)
    return assumptions, book, liabilities


def write_json(path: str, obj) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, indent=2)
        f.write("\n")


def run_engine(assumptions_path, book_path, liabilities_path, out_dir,
               seed=DEFAULT_SEED, n_sims=DEFAULT_SIMS, stage_log=None,
               save_sims=True) -> dict:
    """Full deterministic engine run; writes all SPEC section 4 outputs.

    stage_log: optional path; when given, JSON lines marking real stage
    boundaries (setup|esg|pricing|validation x started|done) are appended.
    Outputs are bit-identical whether or not it is used.
    """
    _stage_log(stage_log, "setup", "started")
    assumptions, book, liabilities = load_inputs(
        assumptions_path, book_path, liabilities_path)
    positions = book["positions"]
    ref_levels = book.get("ref_index_levels")
    if ref_levels is None and any(p["type"] == "equity" for p in positions):
        import sys
        print("WARNING: book has equity positions but no ref_index_levels; "
              "equity scale defaults to 1 and equities carry no index risk "
              "(SPEC section 7).", file=sys.stderr)

    os.makedirs(out_dir, exist_ok=True)

    # --- Valuation ---------------------------------------------------------
    state0 = esg.base_state(assumptions)
    values = pricing.value_positions(positions, state0, ref_levels)[0]
    cohorts, cohort_pvs = pricing.pv_liability_cohorts(liabilities, state0)
    liab_pv = float(cohort_pvs[0].sum())
    asset_total = float(values.sum())
    surplus0 = asset_total - liab_pv

    valuation = {
        "meta": {
            "seed": int(seed),
            "n_sims": int(n_sims),
            "assumptions_path": str(assumptions_path),
            "assumptions_sha256": sha256_file(assumptions_path),
            "book_path": str(book_path),
            "book_sha256": sha256_file(book_path),
            "liabilities_path": str(liabilities_path),
            "liabilities_sha256": sha256_file(liabilities_path),
            "engine_git_rev": git_rev(),
            "asof": str(assumptions.get("meta", {}).get("asof")),
        },
        "positions": [
            {"id": p["id"], "name": p.get("name"), "type": p["type"],
             "currency": p["currency"], "market_value_gbp": float(v)}
            for p, v in zip(positions, values)
        ],
        "asset_total_gbp": asset_total,
        "liability_pv_gbp": liab_pv,
        "liability_cohorts": [
            {"id": c["id"], "class": c["class"], "currency": c["currency"],
             "curve": c["curve"], "pv_gbp": float(v)}
            for c, v in zip(cohorts, cohort_pvs[0])
        ],
        "surplus_gbp": surplus0,
    }
    write_json(os.path.join(out_dir, "valuation.json"), valuation)
    _stage_log(stage_log, "setup", "done")

    # --- Simulation --------------------------------------------------------
    _stage_log(stage_log, "esg", "started")
    shocks = esg.simulate_shocks(assumptions, n_sims, seed)
    _stage_log(stage_log, "esg", "done")

    # Per-position full-factor standalone VaR.
    _stage_log(stage_log, "pricing", "started")
    pos_vars = var.position_standalone_vars(assumptions, positions, shocks,
                                            ref_levels)
    with open(os.path.join(out_dir, "var_standalone_positions.csv"),
              "w", encoding="utf-8", newline="\n") as f:
        f.write("id,name,type,currency,market_value_gbp,var_99_5_1y_gbp\n")
        for p, mv, v in zip(positions, values, pos_vars):
            f.write("%s,%s,%s,%s,%.6f,%.6f\n" % (
                p["id"], str(p.get("name", "")).replace(",", ";"),
                p["type"], p["currency"], mv, v))

    # Factor-block standalone VaR (liabilities in the P&L for every block).
    block_vars = var.block_standalone_vars(assumptions, positions, liabilities,
                                           shocks, ref_levels)
    write_json(os.path.join(out_dir, "var_standalone_factors.json"), {
        "level": var.VAR_LEVEL,
        "horizon_years": 1,
        "blocks": {k: float(v) for k, v in block_vars.items()},
        "block_definitions": {
            "ir_gbp": "gbp_swap + gbp_gilt", "ir_usd": "ust",
            "credit": "spread", "equity": "equity", "fx": "fx",
        },
    })

    # Aggregate surplus VaR + diversification.
    pnl = var.surplus_pnl(assumptions, positions, liabilities, shocks,
                          ref_levels)
    agg = var.var_from_pnl(pnl)
    sum_blocks = float(sum(block_vars.values()))
    write_json(os.path.join(out_dir, "var_aggregate.json"), {
        "level": var.VAR_LEVEL,
        "horizon_years": 1,
        "aggregate_var_gbp": float(agg),
        "sum_standalone_blocks_gbp": sum_blocks,
        "diversification_benefit_gbp": float(sum_blocks - agg),
        "diversification_ratio": float(agg / sum_blocks) if sum_blocks else None,
    })
    _stage_log(stage_log, "pricing", "done")

    # First 1,000 simulated surplus P&Ls (human-readable verification aid).
    _stage_log(stage_log, "validation", "started")
    with open(os.path.join(out_dir, "sim_pnl_sample.csv"),
              "w", encoding="utf-8", newline="\n") as f:
        f.write("sim,surplus_pnl_gbp\n")
        for i in range(min(SAMPLE_ROWS, len(pnl))):
            f.write("%d,%.6f\n" % (i, pnl[i]))

    # Full simulation retention (SPEC §4). Float32 to halve the footprint;
    # ~14 MB per 50k-sim run. Enables scenario drill-down, tail analysis and
    # reverse stress testing without re-running the engine.
    if save_sims:
        pos_pnl = var.position_pnl_matrix(assumptions, positions, shocks,
                                          ref_levels)
        liab_pnl = (pnl - pos_pnl.sum(axis=1)).reshape(-1, 1)
        np.save(os.path.join(out_dir, "sim_pnl_positions.npy"),
                np.hstack([pos_pnl, liab_pnl]).astype(np.float32))
        np.save(os.path.join(out_dir, "sim_factors.npy"),
                shocks.astype(np.float32))
        np.save(os.path.join(out_dir, "sim_surplus.npy"),
                pnl.astype(np.float32))
        write_json(os.path.join(out_dir, "sim_index.json"), {
            "n_sims": int(shocks.shape[0]),
            "pnl_columns": [p["id"] for p in positions] + ["LIABILITIES"],
            "factor_columns": list(esg.FACTOR_ORDER),
            "dtype": "float32",
            "note": "P&L vs base state, GBP. Factor columns are the "
                    "simulated shocks in SPEC §2 order.",
        })
    _stage_log(stage_log, "validation", "done")

    return {"valuation": valuation, "block_vars": block_vars,
            "aggregate_var": agg}


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="python -m engine.run",
        description="Deterministic month-end market risk engine run.")
    ap.add_argument("--assumptions", required=True)
    ap.add_argument("--book", required=True)
    ap.add_argument("--liabilities", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--sims", type=int, default=DEFAULT_SIMS)
    ap.add_argument("--no-save-sims", dest="save_sims",
                    action="store_false",
                    help="skip writing the full simulation arrays")
    ap.add_argument("--stage-log", default=None, metavar="FILE",
                    help="append JSON lines marking stage boundaries "
                         "(observational only; outputs unchanged)")
    args = ap.parse_args(argv)
    res = run_engine(args.assumptions, args.book, args.liabilities, args.out,
                     seed=args.seed, n_sims=args.sims,
                     stage_log=args.stage_log, save_sims=args.save_sims)
    v = res["valuation"]
    print("asset_total_gbp=%.2f liability_pv_gbp=%.2f surplus_gbp=%.2f "
          "aggregate_var_gbp=%.2f" % (
              v["asset_total_gbp"], v["liability_pv_gbp"], v["surplus_gbp"],
              res["aggregate_var"]))


if __name__ == "__main__":
    main()
