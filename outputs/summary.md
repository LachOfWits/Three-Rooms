# Full-chain results summary

**Regenerated 2026-08-30** into the new run layout (PENDING-BATCH2 section 1)
and **pruned to the two months the demo actually uses**. Everything under
`outputs/` was deleted and rebuilt; `assumptions/*.yaml` on disk are
untouched (regenerating another month is one command -- they just no longer
clutter the pickers).

## Layout

    outputs/
      2026_02/
        v1/
          inputs/    manifest.json (+ any derived assumptions YAML)
          stage_log.jsonl
          esg/       assumptions_used.yaml, sim_factors.npy, sim_index.json
          pricing/   valuation.json, var_standalone_positions.csv,
                     var_standalone_factors.json, var_aggregate.json,
                     sim_pnl_positions.npy, sim_surplus.npy,
                     sim_pnl_sample.csv
      2026_03/
        v1/ ...      (same shape)
        v2/ ...      (the March book + March cohorts -- the demo book change)
      attr_2026_02_v1__2026_03_v1/   attribution.json
      attr_2026_02_v1__2026_03_v2/   attribution.json
      research/      2026_02_focused.md, 2026_02_wide-eye.md,
                     2026_03_focused.md, 2026_03_wide-eye.md
      summary.md

Three facts about this layout, all deliberate:

1. **Month, version, stage.** A directory reads as an identity, and the run
   label is the same identity spelled short: `outputs/2026_03/v1` is
   `2603_v1`. **No integer run id appears in a path or in the UI** -- the
   `runs.id` column stays internal to the database, and every API response
   carries `label`, `run_dir`, `esg_dir` and `pricing_dir` instead.
2. **The engine did not change.** `engine/run.py` still writes every
   artefact into whatever single directory `--out` names.
   `app/server/engine_bridge.py` points it at `vN/pricing/`, then moves the
   ESG-stage artefacts across into `vN/esg/` and copies the assumptions the
   run actually priced on in beside them as `assumptions_used.yaml`. The two
   stages **partition** the run's files -- nothing is duplicated.
3. **Both ends of an attribution are named.** `attr_2026_02_v1__2026_03_v2`
   says which two runs were walked between without opening the file. The old
   `attr_<prev>_to_<curr>` form could not tell the single-book pair from the
   two-book one except by a `_books` suffix bolted on the end.

`outputs/` is now **44 MB** across three runs (it was ~188 MB across nine).

## The three runs

All at **seed 20260831, 50,000 sims**, on the 21-factor assumptions (SPEC
section 2, spread factors AA/A/BBB/HY/CCC), the 50-position book including
the four private-credit fund proxies PCF-001..004, and the four-cohort P&C
liability book (overall modified duration **4.33**, 55/45 GBP/USD).

| Run | label | assumptions | book | liabilities |
|---|---|---|---|---|
| `2026_02/v1` | `2602_v1` | `assumptions/2026-02.yaml` | `book/positions.json` | `book/liabilities.json` |
| `2026_03/v1` | `2603_v1` | `assumptions/2026-03.yaml` | `book/positions.json` | `book/liabilities.json` |
| `2026_03/v2` | `2603_v2` | `assumptions/2026-03.yaml` | `book/positions_2026-03.json` | `book/liabilities_2026-03.json` |

`2603_v2` is what used to be `outputs/2026-03_marbook/`: the same month-end
market data priced on the March book (private-credit notionals x1.15, funded
from ~£25m of premium inflow) and the matching March cohorts (x1.025). It
is a **version of March**, not a different month -- which is exactly what the
version axis is for.

All figures GBP millions unless stated.

| Run | Assets | Liab PV | Surplus | Agg VaR | ir_gbp | ir_usd | credit | equity | fx | VaR/assets | VaR/surplus |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `2602_v1` | 977.5 | 818.8 | 158.7 | 71.6 | 14.1 | 6.7 | 12.7 | 58.1 | 5.4 | 7.3% | 45.1% |
| `2603_v1` | 954.7 | 810.0 | 144.8 | 70.4 | 14.6 | 6.8 | 12.4 | 58.4 | 5.1 | 7.4% | 48.6% |
| `2603_v2` | 979.7 | 830.2 | 149.5 | 72.3 | 14.9 | 6.9 | 13.3 | 59.5 | 5.4 | 7.4% | 48.3% |

Block VaRs are standalone (only that block's factors live); their sum exceeds
the aggregate in every run -- the gap is diversification (`2603_v1`:
aggregate £70.4m vs sum of blocks £97.4m, benefit £27.0m, ratio
0.72).

Every figure above is **identical to the pre-restructure regeneration** of the
same three runs. That is the point of a deterministic engine with a fixed
seed, and it is the check that this restructure moved files without touching
numbers.

## Sanity checks

1. **Aggregate <= sum of standalone blocks, every run.** True in all three
   (aggregate £70.4-72.3m vs sum of blocks £97.0-100.1m; the margin is
   never closer than ~£25m). Asserted in
   `tests/test_verify_numbers.py::test_aggregate_var_leq_sum_of_blocks`,
   which now walks **every run directory on disk** rather than a hand-kept
   list of months -- including `2603_v2`, which the old month list never
   reached.
2. **VaR/surplus ratio.** 45.1-48.6%, well below the implausible ~94-110%
   the old long-duration annuity liability book produced. The duration gap
   is closed (liabilities 4.33 vs assets ~5).
3. **The fx block is small, and that is correct.** £5.1-5.4m. The 45% USD
   liability share was *sized to match* the asset book's USD share
   (book/README.md), so a GBPUSD move shifts both legs together and they
   largely cancel at the surplus level. Corroborated by the credit block
   being untouched by the liability model (liabilities carry no spread
   risk).

## The pair

Attribution steps in SPEC section 5 order. **MTM and VaR residuals are 0.00
in both attributions** (`additive_within_1e-6` true on both sections of both
files); the residual line is still reported explicitly, never absorbed into a
step.

| Attribution | dSurplus | Top MTM steps | dVaR | Top VaR steps |
|---|---|---|---|---|
| `attr_2026_02_v1__2026_03_v1` | -13.92 | equity -14.82, gbp_gilt -11.85, gbp_swap +11.70, ust +1.13, spread -0.88, fx +0.79 | -1.21 | equity -3.86, vcv +2.36, fx +0.73, gbp_gilt -0.23 |
| `attr_2026_02_v1__2026_03_v2` | -9.17 | as above **plus book +25.00, liabilities -20.25** | +0.65 | as above **plus book +1.42, liabilities +0.44** |

The two differ only in steps 8 and 9. In the single-run pair both are
**structurally 0 and reported as 0** (one book, one liabilities file, no
recomputation noise); in the demo pair both carry.

### Private credit -- the step 8 / step 9 story

`attr_2026_02_v1__2026_03_v2` is the only attribution with a live allocation
decision in it:

- **Step 8 (book) MTM +£24,998,927.26** -- exactly the premium inflow
  (book/README.md target £25.0m, matched to the penny) -- **dVaR
  +£1,424,638.51**.
- **Step 9 (liabilities) MTM -£20,249,564.25** -- the new reserves --
  **dVaR +£439,829.10**.
- Residuals **0.00** on both MTM and VaR.

Under the hood: the PC sleeve's market value goes £59,104,177 ->
£67,969,804 (+£8.87m, +15.0% exactly) and the four funds' full-factor
standalone VaRs £7,806,124 -> £8,977,043 (+15.0%); the credit block
standalone VaR rises £12.4m -> £13.3m (+7.2%). Exactly the profile
`must_flag_changes` requires: a deliberate allocation decision with near-zero
*net* MTM (the PC tilt sits inside the +£25m inflow) and a real, positive
risk impact -- shown alongside the separately quantified liability-side
growth.

### Reading the pair

**2026-02 -> 2026-03 is a risk-off month the balance sheet does not escape.**
Equity (-14.8) and gilt (-11.9) losses are only partly offset by the swap
sell-off discounting liabilities (+11.7), and surplus falls
**-£13.9m**. An earlier, overstated liability duration (~9.4 against the
correct 4.33) made that swap step roughly three times larger and turned the
same month into "bad market, good month" -- an artefact, not a hedge. Worth
saying out loud in the room: the old headline depended on a model error.

On the demo book (`2603_v2`) the same market steps are joined by £25.0m
of new business on the asset side and -£20.25m of new reserves against
it: the month's market loss is partly clawed back (total MTM
-£9.17m), and **both** sides add risk (total VaR +£0.65m, against
-£1.21m for the market alone).

## Research notes

`outputs/research/<YYYY_MM>_<agent>.md`, the month underscored to match the
run directories. Deterministic and regenerated on every read from
`data/processed/*.csv` -- they never read `assumptions/` or any engine
output, which is what makes an assumptions-vs-research mismatch evidence of
an error between them. `2026_02_wide-eye.md` and `2026_03_wide-eye.md` are
the honest mock stubs (wider risks need live web search).

## Test status

`pytest -q`: **251 passed, 1 skipped, 2 xfailed** -- green.

The count moved from 240 for two reasons, both accounted for:

- **-10** from `tests/test_verify_numbers.py`: its per-month and per-pair
  parametrisations shrank with the pruning (8 months -> 3 runs, 7 pairs -> 2
  attributions). No assertion was weakened; the walks now cover every run
  and every attribution that exists on disk, which the old hand-kept month
  list did not -- it missed the March-book run entirely.
- **+21** from the new `tests/test_run_layout.py`, which pins the layout
  itself: the helpers; the ESG/pricing partition (including that it IS a
  partition and not a copy, and that it leaves a flat sensitivity-run
  directory alone); the committed shape on disk; that no directory anywhere
  under `outputs/` is named by an integer; that the agents' tools resolve a
  single simulation across both stages; that `/api/runs` hands out the label
  and the directories rather than the id; and that a run outside the layout
  (seeded, ad-hoc, or a row left over from the old flat directories) still
  gets a label that collides with nobody.
