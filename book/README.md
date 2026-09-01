# book/ — synthetic asset book and liabilities

Built by `book/build_book.py` (deterministic, no network, no AI, no randomness).
Regenerate with:

```
"C:/Users/lachl/Documents/AI Symposium 2026 IFoA/prototype/.venv/Scripts/python.exe" \
    "C:/Users/lachl/Documents/AI Symposium 2026 IFoA/prototype/book/build_book.py"
```

The script computes total market value, fixed-income duration, per-cohort and
overall liability PV and duration at a flat 4% zero curve (public sleeves),
sizes the four private credit proxies at the real 2026-02 base curves, builds
the March book as **premium growth + PC reallocation** and asserts everything
is within tolerance (FI duration 4.5–5.5; property cohort duration 2.0–2.5,
casualty 5.9–7.0, overall liability duration 3.6–4.4; class/currency splits;
totals; PC NAV within 2% of £60m; premium inflow within £200k of £25m; PC
share of the inflow > 3× pro-rata) and only then writes `positions.json`,
`positions_2026-03.json`, `liabilities.json` and `liabilities_2026-03.json`.

## Contents

- `positions.json` — exactly 50 positions: 12 government bonds (6 UK gilts,
  6 US Treasuries), 16 public corporate bonds (AA/A/BBB/HY, each rating held
  in both GBP and USD), 4 private credit fund proxies (`PCF-001…PCF-004`),
  13 equities (5 FTSE100, 5 SP500, 3 SX5E), 5 cash lines (3 GBP, 2 USD).
  Top-level `ref_asof: 2026-07-31` is the base month-end for equity ref
  prices (SPEC §7). Ids: P001–P012 govt, P013–P028 corp (Ford deliberately
  last so it keeps id **P028** — seeded defect D2's target), PCF-001–004,
  P029–P041 equity, P042–P046 cash.
- `positions_2026-03.json` — the March book of the SPEC §7 demo pair:
  **growth + reallocation**. The baseline book plus ~£25m of premium inflow
  invested: the four PCF notionals scaled **+15.0%** exactly (the deliberate
  tilt, ~£8.87m of the inflow) and the remaining ~£16.13m spread pro-rata
  across every other position. Assets GROW; nothing is sold. Same 50 ids;
  only size fields differ (asserted field-for-field). Exact figures below.
- `liabilities.json` — four P&C reserve cohorts (SPEC §3/§7):
  `L-PROP-GBP`, `L-PROP-USD` (property, t = 1…6), `L-CAS-GBP`, `L-CAS-USD`
  (casualty, t = 1…15). GBP cohorts discount on `gbp_swap`; USD cohorts on
  `ust` in USD, converted at GBPUSD — liabilities carry ir_gbp, ir_usd AND
  fx risk.
- `liabilities_2026-03.json` — the same four cohorts scaled **+2.5%**
  (the demo period's written business), class mix and duration exactly
  constant (uniform scaling; amounts re-rounded to £1,000).

## As-built numbers (flat 4% sizing convention, GBPUSD 1.34; PC at 2026-02 curves)

| Metric | Value | Target |
|---|---|---|
| Total asset MV | £999.8m | ~£1.0bn (±10%) |
| — govt / corp / PC / equity / cash | £340.2m / £280.0m / £60.0m / £230.0m / £89.6m | — |
| FI modified duration (excl. PCF) | 5.00 | ~5.0 (4.5–5.5) |
| PC proxy NAV (2026-02 curves) | £59.98m | ~£60m (±2%) |
| Liability PV | £810.0m | ~£810m (±1%) |
| — property / casualty | £340.2m (42.0%) / £469.8m (58.0%) | 42/58 |
| — GBP / USD | £445.5m (55.0%) / £364.5m (45.0%) | 55/45 |
| Liability modified duration | 4.33 | ~4.0 (3.6–4.4) |
| — property cohorts / casualty cohorts | 2.03 / 6.00 | 2.0–2.5 / 5.9–7.0 |
| Surplus | £189.8m | — (see liability sizing note) |

These are *sizing* numbers. The engine reprices everything from the
assumptions YAML (real curves, spreads, FX), so engine valuations will differ
somewhat — the 10% tolerance is there to absorb exactly that.

## Private credit proxies (SPEC §3, §7)

Four unlisted private credit funds, modelled as synthetic **4-year
annual-coupon corporate bond proxies** priced through the standard corporate
DCF — no bespoke pricing model. Base proxy rating `HY` for all four (the
selection the app layer challenges — SPEC-APP D4 re-proxies them `CCC`).
Internal identifiers `PCF-…`, no ISIN (they are not listed instruments).
Each carries `asset_class: "private_credit"` and a `strategy` label —
`senior_direct_lending`, `mid_market_lending`, `opportunistic_credit`,
`real_estate_debt` — both fields inert to the engine (locked in by engine
tests) and consumed by the app layer for proxy-appropriateness checks.

Sizing at the **2026-02 base curves** (the demo pair's base month-end,
`assumptions/2026-02.yaml`: gilt/UST curves + HY spread level 0.0310 × term
profile M, GBPUSD 1.3491 — the engine's exact interpolation and DCF
conventions, mirrored in `build_book.py`):

- Coupon = the 4y par yield on govy + HY spread, rounded to the nearest 5bp,
  so each proxy prices near par (within 0.5%, asserted): GBP funds 6.550%
  (price 1.000721), USD funds 6.400% (price 1.000546).
- Notional set so DCF value ≈ target NAV, rounded to 100k local currency.

| id | strategy | ccy | coupon | notional | NAV (GBP, 2026-02 curves) |
|---|---|---|---|---|---|
| PCF-001 | senior_direct_lending | GBP | 6.550% | 18,000,000 | £18,012,977 |
| PCF-002 | mid_market_lending | USD | 6.400% | 20,200,000 | £14,981,126 |
| PCF-003 | opportunistic_credit | USD | 6.400% | 17,500,000 | £12,978,698 |
| PCF-004 | real_estate_debt | GBP | 6.550% | 14,000,000 | £14,010,093 |
| **total** | | | | | **£59,982,894** |

(NAVs verified to the penny against a real engine run on
`assumptions/2026-02.yaml`.)

**PCF excluded from the flat-4% FI duration statistic.** The 5.0 duration
target describes the public bond portfolio's rate positioning; the PC proxies
are NAV-sized stand-ins for floating-rate loan books whose fixed-rate form is
itself an admitted distortion (below), so letting them steer the public-book
duration tilt would compound one approximation with another. They still carry
full govy-curve and HY-spread risk in the engine.

Stated limitations (on the slide, not hidden): the fixed-rate proxy adds
govy-curve duration a floating-rate loan book largely does not have (rate
risk **overstated**); NAV smoothing / valuation lag understates true
volatility; the proxy-rating selection is a judgement — exactly what the app
layer challenges (SPEC-APP D4).

## The book pair: positions_2026-03.json (growth + reallocation, SPEC §7)

The March book is the baseline book plus **~£25m of premium inflow
invested** — an insurer receiving and allocating premium, not a manager
rotating. Two distinguishable components, both measured at the **2026-03
base state** (`assumptions/2026-03.yaml`: real curves, HY spread 0.0328,
GBPUSD 1.3173, equity levels for the index-scaled equity marks):

1. **The deliberate tilt** — PCF notionals ×1.15 (exact integer arithmetic,
   23/20): PCF-001 18,000,000 → 20,700,000; PCF-002 20,200,000 → 23,230,000;
   PCF-003 17,500,000 → 20,125,000; PCF-004 14,000,000 → 16,100,000.
   Market value of the added slice: **£8,865,626.56**.
2. **The pro-rata remainder** — £25m − the PC slice = £16,134,373 target,
   spread over every non-PC position in proportion to its 2026-03 market
   value (uniform scale **+1.8014%** on each position's size field: bond
   notionals rounded to £/$1,000, equity quantities to whole shares, cash
   to whole units). Realised value **£16,133,300.70** after rounding.

Exact premium inflow: **£24,998,927.26** (target £25,000,000; asserted
within £200k). PC takes **35.5%** of the inflow against a pro-rata share of
**6.2%** (£1.55m) — **5.7× pro-rata**. That split is @warden's finding to
make (PENDING-ROSTER): growth vs decision.

Nothing but position sizes differs between the two books; PCF +15.0% is
exact (both asserted at build time).

**Validation runs** (engine, seed 20260831, sims **5000**, the full demo
pair with the new liabilities):

| | 2026-02 base pair | 2026-03 base book + base liabs | 2026-03 March pair |
|---|---|---|---|
| Assets | £977.53m | £954.75m | £979.75m |
| Liabilities | £818.82m | £809.96m | £830.21m |
| Surplus | £158.71m | £144.79m | £149.54m |
| Aggregate VaR | £69.09m | £68.35m | £69.97m |
| ir_gbp / ir_usd | £14.2m / £6.8m | £14.9m / £7.1m | £15.3m / £7.2m |
| credit / equity / fx | £12.7m / £58.5m / £5.6m | £12.5m / £57.5m / £5.3m | £13.3m / £58.5m / £5.5m |

Attribution across the full pair (both book and liability pairs, sims 5000):
steps 1–7 (market) MTM −£13.9m net (equity −£14.8m the largest), **step 8
(book) MTM +£25.0m** — the premium inflow — with ΔVaR +£1.76m, **step 9
(liabilities) MTM −£20.25m** — the written reserves — with ΔVaR −£0.14m;
MTM and VaR residuals exactly 0. Two unexplained-by-market residuals, both
positive-growth, similar magnitude: written business, not performance.

**VaR impact of this rebuild** (vs the previous long-duration GBP-only
liability book, old outputs at 50k sims): aggregate VaR falls **£144.9m →
£69.1m** at 2026-02. The old 25y annuity (duration 9.4, GBP only) left a
4.4-year duration gap and an unhedged USD asset position; the new P&C book
(duration 4.3 vs assets ~5) closes most of the surplus rate exposure
(ir_gbp standalone £134.1m → £14.2m) and its 45% USD share offsets USD
asset FX exposure (fx standalone £60.6m → £5.6m; ir_usd £24.1m → £6.8m).
Equity (~£58m) is now the dominant risk block. Sims differ (5k vs 50k) but
the shift is structural, not noise.

**Downstream artefacts are stale until re-run.** Every directory under
`outputs/` was produced before this rebuild: its `valuation.json` files
carry no `liability_cohorts` block, the old liability PV (£846.1m at
2026-02 vs £818.8m now) and — for `outputs/2026-03_marbook` — the previous
cash-funded March book, whose asset total (£954.75m) is identical to the
base book because nothing grew. Anything that reads `outputs/` rather than
re-running the engine (the app's tools, `@holdings`/`@warden` sleeve
analysis, the delta-normal MC cross-check) will compare new book files
against old results until `outputs/` is regenerated for all eight
month-ends plus the March-pair run. That regeneration is outside `book/`.

## Liability construction (four P&C cohorts)

A specialty P&C reserve book — four cohorts by class and currency, each a
fixed annual claims-payment vector (SPEC §3/§7):

| id | class | ccy | curve | years | payment pattern | flat-4% PV | mod dur |
|---|---|---|---|---|---|---|---|
| L-PROP-GBP | property | GBP | gbp_swap | 1–6 | 40/28/16/9/5/2% | £187.1m | 2.026 |
| L-PROP-USD | property | USD | ust | 1–6 | 40/28/16/9/5/2% | £153.1m | 2.026 |
| L-CAS-GBP | casualty | GBP | gbp_swap | 1–15 | t^1.5·e^(−0.33t), peak yr 5 | £258.4m | 5.998 |
| L-CAS-USD | casualty | USD | ust | 1–15 | t^1.5·e^(−0.33t), peak yr 5 | £211.4m | 5.998 |

- **Property pays fast**: bulk in years 1–3 (84% of nominal), tail to 6y.
- **Casualty is long-tailed**: gamma-shaped pattern peaking in year 5, ~2% of
  nominal still paying in year 15.
- **Blend**: 42% property / 58% casualty by PV, overall modified duration
  **4.33** (band 3.6–4.4). Note the geometry: with cohort durations at the
  bottom of their SPEC bands (2.0 and 6.0), a 40/60 blend cannot get much
  below 4.4 — 42/58 is the "roughly 40/60" split that lands the blend
  inside the band. P&C shape, not life.
- **Currency split 55% GBP / 45% USD** (documented choice): a London-market
  specialty book writing substantial US business; 45% USD liabilities also
  broadly match the book's USD asset share, so FX risk largely nets at the
  surplus level (visible in the fx block VaR falling from £60.6m to £5.6m).
- **Sizing**: each cohort's amounts are calibrated so its flat-4% PV (USD
  cohorts converted at GBPUSD 1.34) equals its share of **£810m** — the
  liability PV the balance sheet already showed under real curves — then
  rounded to £1,000. Real-curve PVs: £818.8m at 2026-02, £810.0m at
  2026-03 (asserted within 5%). This replaces SPEC §3's older "≈£850m"
  annuity figure; the sizing-convention surplus is accordingly ~£190m while
  real-curve surplus stays ~£145–160m.
- **Discounting** (engine): GBP cohorts on `gbp_swap`; USD cohorts on `ust`
  in USD converted at GBPUSD (no USD swap curve in the factor set — stated
  simplification and `@red-team` standing item). Liabilities respond in the
  ir_gbp, ir_usd and fx blocks.
- **March file**: all amounts ×1.025 before rounding — PV +2.50% at every
  curve, durations identical to 3dp (asserted: growth within 0.1%, duration
  drift < 0.01).
- No claims/inflation/longevity risk and no cat model — reserves are fixed
  cashflows (stated limitation, `@red-team` standing item).

## Instrument provenance

Government bond ISINs were verified against public records on 2026-08-28
(UK DMO / giltsyield.com / Euronext / Lloyds Bank bond pages for gilts;
TreasuryDirect securities API and cbonds/BondbloX pages for Treasuries).
Corporate lines are real, publicly identifiable issues verified against public
bond-database pages (cbonds.com, BondbloX, bondsupermart, Börse Frankfurt,
WiseAlpha, TreasuryDirect, issuer/SEC records) on the same date.

Verified real lines used:

- Gilts: 4¼% 2027 `GB00B16NNR78`, 4¾% 2030 `GB00B24FF097`, 4¼% 2032
  `GB0004893086`, 4¼% 2036 `GB0032452392`, 4½% 2042 `GB00B1VWPJ53`,
  4¼% 2046 `GB00B128DP45`.
- Treasuries: 2.875% May-2028 `US9128284N73`, 0.625% May-2030 `US912828ZQ64`,
  3.875% Aug-2033 `US91282CHT18`, 4.0% Feb-2034 `US91282CJZ59`,
  2.0% Feb-2050 `US912810SL35`, 4.0% Nov-2052 `US912810TL26`.
- Corporates: Apple 3.85% 2043 `US037833AL42`; Microsoft 3.45% 2036
  `US594918BS26`; Nestlé Holdings 1.375% 2033 GBP `XS2354308194`; Wellcome
  Trust Finance 4.625% 2036 `XS0261559594`; HSBC Holdings 6% 2040 GBP
  `XS0498768315`; JPMorgan Chase 3.625% 2027 `US46625HRX07`; Total Capital
  International 3.455% 2029 `US89153VAQ23`; Unilever 1.875% 2029 GBP
  `XS1684780205`; Lloyds Banking Group 2.707% 2035 GBP `XS2265524640`;
  Barclays 3.25% 2033 GBP `XS1748699011`; AT&T 4.3% 2042 `US00206RBH49`;
  Verizon 4.329% 2028 `US92343VER15`; Vodafone 5.9% 2032 GBP `XS0158715713`;
  Ocado 10.5% 2029 GBP `XS2871478058`; Virgin Media Secured Finance 4.25%
  2030 GBP `XS2062666602`; Ford Motor Credit 7.35% 2027 `US345397C353`.
- Equity ISINs are the standard primary-listing ISINs for each name.
- The private credit funds are deliberately **not** real identifiable funds:
  they are unlisted synthetic proxies with internal `PCF-` identifiers and no
  ISIN (SPEC §3/§7 — "funds are unlisted").

All 46 ISINs held pass the ISIN checksum, asserted at build time (the four
PCF lines carry none by design).

### Sleeve trims in the 50-position rebuild (making room for the PC sleeve)

- Corporates 18 → 16 (the strongest 16 kept; every rating bucket retains at
  least one GBP and one USD line, asserted): dropped **Diageo Finance 2.875%
  2029** (`XS2147890607` — duplicated the 3y A GBP consumer slot held by
  Unilever 1.875% 2029) and **British Telecom 3.125% 2031** (`XS1720922415`
  — duplicated the mid-tenor BBB GBP telecom slot held by the larger
  benchmark Vodafone 5.90% 2032). Ford (P028, D2's target) retained.
- Equities 15 → 13: dropped **BP PLC** and **Exxon Mobil** (the smallest
  lines in their index buckets); the FTSE100/SP500/SX5E spread is preserved
  (5/5/3).

## Approximations (every one of them)

1. **Integer maturities.** `maturity_years` is years remaining from
   2026-07-31 rounded to the nearest whole year (SPEC §3 prices integer-year
   annual-pay bonds). E.g. Verizon Sep-2028 → 2y; Ocado Aug-2029 → 3y;
   UST Nov-2052 → 26y.
2. **Annual coupons.** US Treasuries and USD corporates actually pay
   semi-annually; the engine convention (SPEC §9) is annual. Coupon rates are
   the real annual rates applied annually.
3. **Three check digits computed, not transcribed.** `US912828ZQ64`,
   `US91282CJZ59`, `US46625HRX07` were built from publicly verified 9-char
   CUSIP bases (TreasuryDirect API; Public.com listing for the JPM line) with
   the ISIN check digit computed by the build script. The rest were
   transcribed in full from the sources above.
4. **Rating buckets are issuer-level approximations.** Split/composite
   ratings are mapped to one of AA/A/BBB/HY: Microsoft (AAA) and Wellcome
   Trust (AAA) sit in AA; Lloyds HoldCo senior (A3/BBB+/A) sits in A;
   Barclays HoldCo senior (Baa1/BBB+) in BBB; Ford (BB+), Ocado (B/B+) and
   Virgin Media (BB+) in HY. No security-level rating differentiation.
5. **Structural features ignored.** Callable/fixed-to-float features (JPMorgan
   2027, Lloyds 2.707% 2035, Ocado 2029, Virgin Media 2030) are modelled as
   fixed-coupon bullets to their stated maturity. Verizon 4.329% 2028 was
   called for redemption in Sep-2026 in the real world; at the 2026-07-31
   book date it is still outstanding and is modelled to its 2028 maturity.
6. **Ford Motor Credit 7.35% 2027**: the exact maturity month within 2027 was
   not confirmed (public record confirms the line and coupon); either way it
   rounds to 1 integer year remaining.
7. **Equity ref prices are approximate**, not actual 2026-07-31 closes: they
   are plausible late-July-2026 levels chosen from early-2026 market levels
   (no free machine-readable source for those closes was reachable at build
   time). `ref_asof = 2026-07-31`; the engine scales by
   index(asof)/index(ref_asof), which equals 1 for the base run, so any level
   error only shifts the equity sleeve's size, absorbed by the ±10% total
   tolerance. UK prices are quoted in GBP per share (pounds, not pence).
8. **SX5E names are carried as GBP-denominated lines.** SPEC §2 has only one
   FX factor (GBPUSD), so ASML, SAP and TotalEnergies are booked in GBP with
   `ref_price` = EUR price × assumed EURGBP 0.845. EURGBP risk is therefore
   deliberately not modelled (they still carry SX5E equity risk).
9. **Sizing FX.** GBPUSD = 1.34 was assumed for sizing public USD positions.
   The engine converts at the assumptions-file GBPUSD, so GBP sleeve values
   will move with the real rate (within tolerance). The PC proxies instead
   use the real 2026-02 GBPUSD (1.3491) because their sizing convention is
   NAV at the 2026-02 base curves (SPEC §3), not the flat-4% convention.
10. **Flat 4% sizing curve** for the public sleeves. All public-bond
    prices/durations in the build script use a flat 4% zero curve (govvies
    and corporates alike — no spread in the sizing convention). The engine
    uses real curves + rated spread curves. Exception: the PC proxies are
    sized at the real 2026-02 curves (see the PC section above).
11. **Duration measure** is modified duration (Macaulay / 1.04) at that flat
    curve, MV-weighted across govt + public corp for the FI figure. The PC
    proxies are excluded from this statistic (rationale in the PC section);
    the target applies to the public bond book only.
12. **Notional rounding** to the nearest 100,000 (local currency) in the
    baseline book; equity quantities to the nearest 100 shares; liability
    cashflows to the nearest £1,000 (local currency for USD cohorts). The
    March book's pro-rata additions round bond notionals to 1,000 and equity
    quantities to whole shares. This moves totals a fraction of a percent
    off exact targets (asserted well inside tolerance).
13. **Coupons/maturities are approximately real.** Coupons match the verified
    lines; maturity years are subject to (1). Where a real line's first call
    or amortisation would change its economics, see (5).
14. **PC proxies are wholly synthetic.** A single 4y fixed-coupon HY-rated
    bullet stands in for each fund's NAV: rate risk overstated (fixed-rate
    proxy for largely floating-rate loans), volatility understated (NAV
    smoothing / valuation lag not modelled), no default/migration/illiquidity
    modelling, one proxy rating per fund chosen by judgement (base `HY`).
    Coupons are par yields at the 2026-02 curves rounded to 5bp, so the
    proxies price within 0.5% of par there (asserted); their values drift
    with the market at other month-ends, which is expected.
15. **March-book inflow rounding.** The realised premium inflow is
    £24,998,927.26 against the £25m target: the pro-rata scale is computed
    exactly, then each position's addition is rounded to its lot size
    (£/$1,000 notional, whole shares, whole cash units). Asserted within
    £200k of target. The inflow is valued at the 2026-03 base state — the
    equity legs use the index-scaled marks (index(2026-03)/index(ref_asof)
    × ref_price), mirroring the engine's convention.
16. **Liability shares are flat-4% shares.** The 42/58 class split and 55/45
    currency split are exact at the flat-4% sizing convention (GBPUSD 1.34);
    at real month-end curves and FX they drift a fraction of a percent,
    which is expected.
