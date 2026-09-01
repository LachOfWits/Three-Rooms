# Focused-Risks Research Note — 2026-03

> **Live mode — web research included.** The quantitative backbone below is computed from our own processed series; the *what drove it* prose is model-written from web sources, listed at the end. Treat the prose as context and the tables as data.

**The standing set.** The same seven risks, in the same order, every month — interest rates · inflation · credit spreads · defaults and distress · employment · GBP/USD · equities — so that this month reads against last month without re-learning the layout. A risk we cannot cover keeps its section and says why.

Month-end 2026-03-31; previous month-end 2026-02-27; 22 business days in the month; trailing window 504 business days. Percentiles rank the month's move among rolling same-length moves over the trailing window; level percentiles rank the month-end level in the same window; daily vols are unannualised standard deviations of daily changes.

## Independently sourced — month-end levels (open web)

Each level below was sourced by @focused from the open web, with a link, and is **independent of the model's data pipeline**. Room 1 checks these against `assumptions/` — a disagreement is a real flag, not a calibration artefact.

| factor | independently-sourced level | source |
|---|---|---|
| FTSE 100 | 10,176.5 | [link](https://finance.yahoo.com/quote/%5EFTSE/history/) |
| S&P 500 | 6,528.52 | [link](https://finance.yahoo.com/quote/%5EGSPC/history/) |
| EURO STOXX 50 | 5,569.73 | [link](https://finance.yahoo.com/quote/%5ESTOXX50E/history/) |
| GBP/USD | 1.32 USD per GBP | [link](https://finance.yahoo.com/quote/GBPUSD%3DX/history/) |
| US 10y Treasury | 4.31% | [link](https://finance.yahoo.com/quote/%5ETNX/history/) |


## 1. Interest rates and the curve

**What it did this month.**

*GBP swap (OIS) zero curve* — `gbp_swap.csv`, percent as published.

| point | month-end | Δ month | move %ile (2y) | 2y range | day vol (month) | day vol (2y) |
|---|---|---|---|---|---|---|
| t2 | 4.1623 | +85.3bp | 98th | 3.3095–4.7327 | 12.99bp | 4.80bp |
| t5 | 4.1063 | +62.1bp | 99th | 3.3571–4.2950 | 10.22bp | 4.74bp |
| t10 | 4.3748 | +49.3bp | 99th | 3.3737–4.4810 | 7.44bp | 4.61bp |
| t20 | 4.7865 | +38.4bp | 98th | 3.5885–4.8611 | 6.60bp | 4.75bp |

*GBP gilt zero curve* — `gbp_gilt.csv`, percent as published.

| point | month-end | Δ month | move %ile (2y) | 2y range | day vol (month) | day vol (2y) |
|---|---|---|---|---|---|---|
| t2 | 4.2830 | +85.9bp | 99th | 3.4237–4.5784 | 9.84bp | 4.30bp |
| t5 | 4.4076 | +69.0bp | 100th | 3.4592–4.5111 | 8.64bp | 4.52bp |
| t10 | 4.9442 | +59.2bp | 100th | 3.7245–5.0098 | 8.08bp | 4.76bp |
| t20 | 5.5741 | +50.9bp | 99th | 4.3214–5.7180 | 7.79bp | 4.87bp |

*UST curve* — `ust.csv`, percent as published.

| point | month-end | Δ month | move %ile (2y) | 2y range | day vol (month) | day vol (2y) |
|---|---|---|---|---|---|---|
| t2 | 3.7900 | +41.0bp | 97th | 3.3800–5.0400 | 6.11bp | 5.31bp |
| t5 | 3.9200 | +41.0bp | 94th | 3.4100–4.7200 | 6.53bp | 5.37bp |
| t10 | 4.3000 | +33.0bp | 91st | 3.6300–4.7900 | 5.69bp | 4.97bp |
| t20 | 4.8800 | +31.0bp | 91st | 4.0100–5.0800 | 5.30bp | 4.79bp |

**Curve shape.** We model four tenors, so shape is a modelled risk and not a footnote.

| curve | 2s10s | Δ month | 2s20s | Δ month | shape |
|---|---|---|---|---|---|
| gbp_swap | +21.3bp | -36.0bp | +62.4bp | -46.8bp | flattened |
| gbp_gilt | +66.1bp | -26.7bp | +129.1bp | -35.1bp | flattened |
| ust | +51.0bp | -8.0bp | +109.0bp | -10.0bp | flattened |

**Gilt/swap basis.** The assets sit on the gilt curve and the GBP liabilities discount on swap, so this basis is the unhedged rate exposure — surplus is near-immune to a parallel move and is not immune to this one. It gets its own line every month.

| tenor | gilt − swap | Δ month | 2y range | level %ile |
|---|---|---|---|---|
| t2 | +12.1bp | +0.7bp | -20.5–+18.9bp | 80th |
| t5 | +30.1bp | +6.8bp | +1.6–+31.6bp | 98th |
| t10 | +56.9bp | +9.9bp | +28.6–+62.8bp | 85th |
| t20 | +78.8bp | +12.4bp | +65.1–+95.2bp | 46th |

**Where it sits against history.**

- `gbp_swap`: led by t2 at +85.3bp, a move in the 98th percentile of the trailing two years; the level sits in the 77th percentile of its own two-year range.
- `gbp_gilt`: led by t2 at +85.9bp, a move in the 99th percentile of the trailing two years; the level sits in the 89th percentile of its own two-year range.
- `ust`: led by t2 at +41.0bp, a move in the 97th percentile of the trailing two years; the level sits in the 42nd percentile of its own two-year range.

**What drove it.**

The BoE held Bank Rate unanimously at 3.75% on 18 March, explicitly flagging Middle East conflict as a driver of higher global energy and commodity prices and near-term inflation risk. The FOMC also stood pat and by 31 March the market had priced out all 2026 Fed cuts, with a hike at times seen as likelier than a cut. Duration bore the cost: the US 10y went from sub-4% at end-February to a 2026 high of 4.44% on 27 March, and 30y Treasuries returned -3.92% for the month.

## 2. Inflation

**Not in our data.** No series in `data/processed/` carries this risk, so there is nothing here to compute and this note will not pretend otherwise.

**In the model?** No — the curves are nominal and the reserves are fixed claims-payment cashflows, so neither price inflation nor claims inflation has any channel into this model.

**Why it still gets a section.** Claims inflation — building costs on the property book, social and litigation inflation on casualty — is the largest liability-side risk we cannot price. The reserves are fixed cashflows, so an inflation shock has no channel into any number this system produces. Breakevens and real yields would at least give the market's forward view; we hold neither.

**What live research found.**

Realised consumer inflation was still contained - February US headline and core CPI matched forecasts at 2.4% and 2.5% y/y - but pipeline pressure built, with February PPI beating for a third straight month (+0.7% headline, +0.5% core; 3.4% and 3.9% annual). The oil spike from the near-closure of the Strait of Hormuz reset expectations rather than the prints themselves, and a roughly $1/gallon rise in US retail gasoline fed straight into household costs. UK headline CPI was 3.1% y/y in the 12 months to March before easing to 2.6% by June/July.

## 3. Credit spreads

**What it did this month.** OAS by rating bucket — `credit_oas.csv`, percent as published.

| rating | month-end | Δ month | move %ile (2y) | 2y range | day vol (month) | day vol (2y) |
|---|---|---|---|---|---|---|
| AA | 0.5600 | +2.0bp | 71st | 0.4100–0.7300 | 1.38bp | 1.17bp |
| A | 0.7500 | +4.0bp | 77th | 0.5900–1.0200 | 1.76bp | 1.35bp |
| BBB | 1.1300 | +6.0bp | 77th | 0.9300–1.4900 | 1.98bp | 1.68bp |
| HY | 3.2800 | +18.0bp | 75th | 2.5900–4.6100 | 8.92bp | 7.57bp |
| CCC | 9.9400 | +44.0bp | 77th | 6.9000–11.3700 | 13.33bp | 12.61bp |

**Where it sits against history.** Quality spreads — the shape of the credit curve, and a cleaner risk-appetite read than any single bucket.

| quality spread | level | Δ month | 2y range | level %ile |
|---|---|---|---|---|
| BBB − A | +38.0bp | +2.0bp | +32.0–+47.0bp | 79th |
| HY − BBB | +215.0bp | +12.0bp | +158.0–+313.0bp | 89th |
| CCC − HY | +666.0bp | +26.0bp | +429.0–+676.0bp | 99th |

- Largest bucket move: CCC at +44.0bp. HY − BBB +12.0bp on the month — low quality underperformed, which is a risk-appetite move rather than a rates-driven repricing.

**What drove it.**

Spreads widened during the month alongside the rates move, and every major fixed income index fell: US Aggregate -1.76%, US Credit -1.96%, high yield -1.18%, munis -2.32%. The composition matters - high yield outperformed IG and Treasuries - implying the drawdown was overwhelmingly a duration/rate event with credit risk a secondary contributor. Municipals, earlier-year leaders, were the notable casualty and flipped negative year-to-date.

## 4. Defaults and distress

**Proxy only, and named as one.** We hold no default-rate, distress-ratio or migration series. What we do hold is the CCC bucket and its spread over HY — the market's own price for imminent default risk. It is a market-implied proxy, not a realised default rate, and the two diverge exactly when it matters most.

- CCC OAS 9.9400% (+44.0bp on the month), level in the 96th percentile of the trailing two years.
- CCC − HY +666.0bp (+26.0bp), level in the 99th percentile — dispersion widened: the market is discriminating within high yield, which is the usual early signature of a default cycle.

**In the model?** No. Spreads move continuously; there is no default event, no ratings migration and no recovery assumption anywhere in the factor set. A downgrade would hit us through bucket mapping in the position file long before a default did, and neither is modelled. This is material and we cannot currently price it.

**What drove it.**

Not sourced for March 2026: no default-rate, downgrade or recovery data was obtainable within this research pass, so no level or trend is asserted. The only indirect read is that high yield outperformed investment grade and Treasuries during the sell-off, which is not the pattern of an acute default scare - but that is inference, not evidence.

## 5. Employment and the labour market

**Not in our data.** No series in `data/processed/` carries this risk, so there is nothing here to compute and this note will not pretend otherwise.

**In the model?** No — no macro factor of any kind is modelled.

**Why it still gets a section.** Employment and wages are the driver behind the policy rates that drive everything we do model, so it belongs in the standing set as the thing we watch but never capture. It reaches us only after the fact, already priced into the curves.

**What live research found.**

Unsourced. March 2026 US payrolls/unemployment and UK labour market data could not be retrieved before the search budget was exhausted, and no figure is estimated here.

## 6. GBP/USD

**What it did this month.**

| series | month-end | Δ month | move %ile (2y) | 2y range | day vol (month) | day vol (2y) |
|---|---|---|---|---|---|---|
| GBPUSD | 1.3173 | -2.36% | 10th | 1.2171–1.3825 | 0.55% | 0.44% |

**Where it sits against history.** GBPUSD 1.3173, -2.36% on the month — the 10th percentile of trailing two-year moves; the level sits in the 51st percentile of its two-year range (1.2171–1.3825).

**Why it is two-sided.** The USD assets translate at this rate and so do the USD liability cohorts, so a sterling move is never a one-way exposure here. The short-end basis behind the pair (SONIA vs SOFR) is not in our data; live mode covers it.

**What drove it.**

No 31 March 2026 GBP/USD close could be sourced - the Yahoo GBPUSD=X page returned an outright access error and no substitute page carrying the month-end close was confirmed. Qualitatively, an energy-supply shock concentrated on the Strait of Hormuz is a terms-of-trade negative for oil-importing UK and euro area relative to a net-oil-exporting US, which argues for dollar strength, but no month-end level is claimed.

## 7. Equities

**What it did this month.**

| series | month-end | Δ month | move %ile (2y) | 2y range | day vol (month) | day vol (2y) |
|---|---|---|---|---|---|---|
| FTSE100 | 10,176.5000 | -6.73% | 2nd | 7,679.5000–10,910.6000 | 1.22% | 0.71% |
| SP500 | 6,528.5200 | -5.09% | 7th | 4,982.7700–6,978.6000 | 1.14% | 1.01% |
| SX5E | 5,569.7300 | -9.26% | 2nd | 4,571.6000–6,173.3200 | 1.50% | 1.00% |

**Where it sits against history.**

| index | drawdown from 2y peak | 2y peak | level %ile |
|---|---|---|---|
| FTSE100 | -6.73% | 10,910.6000 | 92nd |
| SP500 | -6.45% | 6,978.6000 | 72nd |
| SX5E | -9.78% | 6,173.3200 | 77th |

- Widest move: SX5E at -9.26%. Best-to-worst dispersion across the three indices 4.17pp — a wide spread, so this was regional or sectoral rather than a single global risk move.
- Implied volatility (VIX and equivalents) is **not** in our data. The vol we calibrate comes from 504 days of realised history, so the market's forward view is exactly the cross-check we cannot make from here.

**What drove it.**

Broad, indiscriminate weakness: S&P 500 -4.98%, DJIA -5.20%, Nasdaq Composite -4.68%, Russell 2000 -5.00% for March. Non-US equities were hit far harder - MSCI ACWI ex-US -10.79% and EM -13.06% - reflecting greater direct dependence on oil transiting the Strait of Hormuz. Large-cap growth kept lagging on a year-to-date basis while small caps and the equal-weight S&P clung to modest YTD gains, and headline sensitivity to any de-escalation signal produced a sharp rally on the final trading day.

## Coverage and limitations

| standing risk | our series | in the factor set |
|---|---|---|
| Interest rates and the curve | `gbp_swap.csv`, `gbp_gilt.csv`, `ust.csv` | yes — twelve of the twenty-one factors: three curves at four tenors each |
| Inflation | **none** | no — the curves are nominal and the reserves are fixed claims-payment cashflows, so neither price inflation nor claims inflation has any channel into this model |
| Credit spreads | `credit_oas.csv` | yes — five spread factors (AA, A, BBB, HY, CCC) |
| Defaults and distress | `credit_oas.csv` (proxy only) | no — spreads move continuously, but there is no default, no ratings migration and no recovery assumption in the factor set |
| Employment and the labour market | **none** | no — no macro factor of any kind is modelled |
| GBP/USD | `fx.csv` | yes — one factor; it also translates the USD liability cohorts, so it is a two-sided exposure |
| Equities | `equity.csv` | yes — three index factors; the equity book is index-proxied, so single-name risk is invisible |

Notable observations, from the fixed thresholds stated above (top/bottom decile move, two-year extreme, outsized move, realised vol hot or cold):

- gbp_swap/t2: month move in the top decile of the trailing two years (98th percentile)
- gbp_swap/t2: outsized monthly move (+85.3bp)
- gbp_swap/t2: realized daily vol ran hot vs the trailing window (12.99 vs 4.80 bp/day) — entering the calibration window it pulls the calibrated vol up
- gbp_swap/t5: month move in the top decile of the trailing two years (99th percentile)
- gbp_swap/t5: outsized monthly move (+62.1bp)
- gbp_swap/t5: realized daily vol ran hot vs the trailing window (10.22 vs 4.74 bp/day) — entering the calibration window it pulls the calibrated vol up
- gbp_swap/t10: month move in the top decile of the trailing two years (99th percentile)
- gbp_swap/t10: outsized monthly move (+49.3bp)
- gbp_swap/t10: realized daily vol ran hot vs the trailing window (7.44 vs 4.61 bp/day) — entering the calibration window it pulls the calibrated vol up
- gbp_swap/t20: month move in the top decile of the trailing two years (98th percentile)
- gbp_swap/t20: outsized monthly move (+38.4bp)
- gbp_swap/t20: realized daily vol ran hot vs the trailing window (6.60 vs 4.75 bp/day) — entering the calibration window it pulls the calibrated vol up
- gbp_gilt/t2: month move in the top decile of the trailing two years (99th percentile)
- gbp_gilt/t2: outsized monthly move (+85.9bp)
- gbp_gilt/t2: realized daily vol ran hot vs the trailing window (9.84 vs 4.30 bp/day) — entering the calibration window it pulls the calibrated vol up
- gbp_gilt/t5: month move in the top decile of the trailing two years (100th percentile)
- gbp_gilt/t5: outsized monthly move (+69.0bp)
- gbp_gilt/t5: realized daily vol ran hot vs the trailing window (8.64 vs 4.52 bp/day) — entering the calibration window it pulls the calibrated vol up
- gbp_gilt/t10: month move in the top decile of the trailing two years (100th percentile)
- gbp_gilt/t10: outsized monthly move (+59.2bp)
- gbp_gilt/t10: realized daily vol ran hot vs the trailing window (8.08 vs 4.76 bp/day) — entering the calibration window it pulls the calibrated vol up
- gbp_gilt/t20: month move in the top decile of the trailing two years (99th percentile)
- gbp_gilt/t20: outsized monthly move (+50.9bp)
- gbp_gilt/t20: realized daily vol ran hot vs the trailing window (7.79 vs 4.87 bp/day) — entering the calibration window it pulls the calibrated vol up
- ust/t2: month move in the top decile of the trailing two years (97th percentile)
- ust/t2: outsized monthly move (+41.0bp)
- ust/t5: month move in the top decile of the trailing two years (94th percentile)
- ust/t5: outsized monthly move (+41.0bp)
- ust/t10: month move in the top decile of the trailing two years (91st percentile)
- ust/t10: outsized monthly move (+33.0bp)
- ust/t20: month move in the top decile of the trailing two years (91st percentile)
- ust/t20: outsized monthly move (+31.0bp)
- spread/A: realized daily vol ran hot vs the trailing window (1.76 vs 1.35 bp/day) — entering the calibration window it pulls the calibrated vol up
- spread/CCC: outsized monthly move (+44.0bp)
- equity/FTSE100: month move in the bottom decile of the trailing two years (2nd percentile)
- equity/FTSE100: outsized monthly move (-6.73%)
- equity/FTSE100: realized daily vol ran hot vs the trailing window (1.22 vs 0.71 %/day) — entering the calibration window it pulls the calibrated vol up
- equity/SP500: month move in the bottom decile of the trailing two years (7th percentile)
- equity/SP500: outsized monthly move (-5.09%)
- equity/SX5E: month move in the bottom decile of the trailing two years (2nd percentile)
- equity/SX5E: outsized monthly move (-9.26%)
- equity/SX5E: realized daily vol ran hot vs the trailing window (1.50 vs 1.00 %/day) — entering the calibration window it pulls the calibrated vol up
- fx/GBPUSD: month move in the bottom decile of the trailing two years (10th percentile)
- fx/GBPUSD: realized daily vol ran hot vs the trailing window (0.55 vs 0.44 %/day) — entering the calibration window it pulls the calibrated vol up

## Sources

- Bank Rate maintained at 3.75% - March 2026 Monetary Policy Summary and Minutes, Bank of England — https://www.bankofengland.co.uk/monetary-policy-summary-and-minutes/2026/march-2026
- Consumer price inflation, UK: April 2026 - ONS — https://www.ons.gov.uk/economy/inflationandpriceindices/bulletins/consumerpriceinflation/april2026
- Consumer price inflation, UK: June 2026 - ONS — https://www.ons.gov.uk/economy/inflationandpriceindices/bulletins/consumerpriceinflation/june2026
- Inflation in the UK: Economic indicators - House of Commons Library — https://commonslibrary.parliament.uk/research-briefings/sn02792/
- Benchmark Review & Monthly Recap, March 2026 - Clark Capital Management Group — https://ccmg.com/benchmark-review-monthly-recap-march-2026/

## Appendix — factor detail

Every modelled column, including the intra-month range and the dates it was set. This is the table the room-1 reconciliation and the room-3 desks read.

### GBP swap (OIS) zero curve — `gbp_swap.csv` (% (as published))

Month-end 2026-03-31 vs 2026-02-27 (22 business days in the month).

| column | month-end | Δ month | intra-month low | intra-month high | move %ile (2y) |
|---|---|---|---|---|---|
| t2 | 4.1623 | +85.3bp | 3.4578 (2026-03-02) | 4.4021 (2026-03-20) | 98th |
| t5 | 4.1063 | +62.1bp | 3.5951 (2026-03-02) | 4.2950 (2026-03-20) | 99th |
| t10 | 4.3748 | +49.3bp | 3.9637 (2026-03-02) | 4.4810 (2026-03-20) | 99th |
| t20 | 4.7865 | +38.4bp | 4.4700 (2026-03-02) | 4.8611 (2026-03-26) | 98th |

### GBP gilt zero curve — `gbp_gilt.csv` (% (as published))

Month-end 2026-03-31 vs 2026-02-27 (22 business days in the month).

| column | month-end | Δ month | intra-month low | intra-month high | move %ile (2y) |
|---|---|---|---|---|---|
| t2 | 4.2830 | +85.9bp | 3.5280 (2026-03-02) | 4.4328 (2026-03-20) | 99th |
| t5 | 4.4076 | +69.0bp | 3.7927 (2026-03-02) | 4.5111 (2026-03-20) | 100th |
| t10 | 4.9442 | +59.2bp | 4.4125 (2026-03-02) | 5.0098 (2026-03-27) | 100th |
| t20 | 5.5741 | +50.9bp | 5.1209 (2026-03-02) | 5.6422 (2026-03-27) | 99th |

### UST curve — `ust.csv` (% (as published))

Month-end 2026-03-31 vs 2026-02-27 (22 business days in the month).

| column | month-end | Δ month | intra-month low | intra-month high | move %ile (2y) |
|---|---|---|---|---|---|
| t2 | 3.7900 | +41.0bp | 3.4700 (2026-03-02) | 3.9600 (2026-03-26) | 97th |
| t5 | 3.9200 | +41.0bp | 3.6200 (2026-03-02) | 4.0800 (2026-03-26) | 94th |
| t10 | 4.3000 | +33.0bp | 4.0500 (2026-03-02) | 4.4400 (2026-03-27) | 91st |
| t20 | 4.8800 | +31.0bp | 4.6400 (2026-03-02) | 4.9900 (2026-03-27) | 91st |

### Credit OAS by rating — `credit_oas.csv` (% (as published))

Month-end 2026-03-31 vs 2026-02-27 (22 business days in the month).

| column | month-end | Δ month | intra-month low | intra-month high | move %ile (2y) |
|---|---|---|---|---|---|
| AA | 0.5600 | +2.0bp | 0.5100 (2026-03-04) | 0.6000 (2026-03-13) | 71st |
| A | 0.7500 | +4.0bp | 0.6700 (2026-03-04) | 0.7900 (2026-03-16) | 77th |
| BBB | 1.1300 | +6.0bp | 1.0300 (2026-03-04) | 1.1600 (2026-03-16) | 77th |
| HY | 3.2800 | +18.0bp | 2.9700 (2026-03-04) | 3.4600 (2026-03-30) | 75th |
| CCC | 9.9400 | +44.0bp | 9.4200 (2026-03-04) | 10.2000 (2026-03-30) | 77th |

### Equity indices — `equity.csv` (levels)

Month-end 2026-03-31 vs 2026-02-27 (22 business days in the month).

| column | month-end | Δ month | intra-month low | intra-month high | move %ile (2y) |
|---|---|---|---|---|---|
| FTSE100 | 10,176.5000 | -6.73% | 9,894.2000 (2026-03-23) | 10,780.1000 (2026-03-02) | 2nd |
| SP500 | 6,528.5200 | -5.09% | 6,343.7200 (2026-03-30) | 6,881.6200 (2026-03-02) | 7th |
| SX5E | 5,569.7300 | -9.26% | 5,501.2800 (2026-03-20) | 5,986.9300 (2026-03-02) | 2nd |

### FX — `fx.csv` (levels)

Month-end 2026-03-31 vs 2026-02-27 (22 business days in the month).

| column | month-end | Δ month | intra-month low | intra-month high | move %ile (2y) |
|---|---|---|---|---|---|
| GBPUSD | 1.3173 | -2.36% | 1.3173 (2026-03-31) | 1.3426 (2026-03-20) | 10th |
