# Month-End Market Risk Report — 31 March 2026

**Status: DRAFT for review** · Prepared 2 April 2026 · Comparison month-end: 27 February 2026
Model run: seed 20260831, 50,000 simulations, assumptions `assumptions/2026-03.yaml`, static book.

## 1. Balance sheet and surplus

| £m | Feb 2026 | Mar 2026 | Change |
|---|---:|---:|---:|
| Assets | 977.5 | 954.7 | -22.8 |
| Liabilities (PV) | 818.8 | 810.0 | -8.8 |
| **Surplus** | **158.7** | **144.8** | **-13.9** |

## 2. Market backdrop

March was a broad risk-off month with a sharp rates sell-off. The GBP swap
curve rose 85bp at 2y (to 4.16%) and 49bp at 10y (to 4.37%); the 10y gilt
yield rose 59bp to 4.94% and the 10y UST 33bp to 4.30%. Credit spreads
widened, led by high yield (+18bp to 328bp; BBB +6bp to 113bp). Equity
markets fell: FTSE 100 -6.7%, S&P 500 -5.1%, Euro Stoxx 50 -9.3%. Sterling
weakened against the dollar, with GBPUSD moving from 1.3491 to 1.3173 (-2.4%).

## 3. Surplus attribution (Feb → Mar)

The surplus decreased £13.9m over the month. The largest single positive
contribution was the rise in the swap discount curve, which cut the PV of the
GBP claims cohorts and added **+£11.7m**. On the asset side, higher gilt
yields took **-£11.9m** off the GBP bond portfolio, while higher UST yields
were a net **+£1.1m** — the USD claims cohorts discount on the Treasury curve
and fell by more than the USD bonds did; spread widening cost a further
**-£0.9m** on the short-duration credit book. Lower equity index levels
reduced asset values by **-£14.8m**. The depreciation of sterling over the
month reduced surplus by a further **-£0.8m** on the translation of
USD-denominated assets. The vol/correlation update and book changes had no
MTM effect, and the attribution residual is zero — the steps fully explain
the movement.

## 4. Risk position

Aggregate 99.5% 1-year surplus VaR stands at **£97.4m**, computed across the
five risk blocks below, up £0.4m from £97.0m at end-February. GBP rates and
equity both rose slightly on the month, with credit risk a little lower.

| Block (standalone 99.5% VaR, £m) | Feb 2026 | Mar 2026 |
|---|---:|---:|
| IR GBP (swap + gilt) | 14.1 | 14.6 |
| IR USD | 6.7 | 6.9 |
| Credit | 12.7 | 12.5 |
| Equity | 58.1 | 58.4 |
| FX | 5.4 | 5.2 |
| **Aggregate 99.5% VaR** | **97.0** | **97.4** |

Totals may not sum due to rounding.

## 5. Notes for sign-off

- Attribution additivity check passes (steps + residual = total, residual 0.0).
- Spread floor incidence and simulation percentile consistency to be appended
  by the validation pass.
- No book changes in the month; step 8 of the attribution is structurally zero.

*Draft — numbers subject to validation review before circulation.*
