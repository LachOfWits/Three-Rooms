# Wide-Eye Research Note — 2026-03

> **Live mode — web research included.** The quantitative backbone below is computed from our own processed series; the *what drove it* prose is model-written from web sources, listed at the end. Treat the prose as context and the tables as data.

Month-end 2026-03-31; previous month-end 2026-02-27. This note carries no portfolio numbers by construction — the room-3 post it backs is context-marked and quarantined, and the market-level figures below are market data, never book data.

## The standing menu

Not a fixed schema — the menu is a floor, not a ceiling — but every theme gets a line every month, with its channel into the model stated. Where a wider risk has no channel, saying so is the point: *this is material and we cannot currently price it* is the most useful sentence in this note, and it feeds @red-team's standing limitations.

### Markets and credit

**Private credit**

Late-March commentary framed private credit as recalibrating rather than breaking: fundamentals still look sound, but recently-arrived retail money is redeeming, creating outflows and technical weakness. The structural question is semi-liquid wrapper design rather than headline defaults, since the sub-2% reported default rate understates a 'true' rate nearer 5% once selective defaults and liability management exercises are included, and PIK usage has risen notably. Software and sponsor-backed lending are the named watch areas.

> Channel into the model — PARTIAL. The sleeve is modelled as a synthetic fixed-rate bond on our own curves and spread levels. Fund-level marks, NAV smoothing and valuation lag have no channel at all, and the fixed-rate proxy adds govy duration a floating-rate loan book does not have.

**Commercial real estate**

March set a fresh cycle high in securitised distress, with the CMBS delinquency rate up 41bp to roughly 7.6%, erasing February's decline and reaching the worst level of this distress cycle, up about 90bp year on year. Roughly $5.1bn of loans newly turned delinquent, with the five largest accounting for just over $2bn. Agency multifamily paper remains the clear outlier on the strong side.

> Channel into the model — INDIRECT ONLY. CRE stress reaches us through BBB and HY spreads if it reaches us at all; there is no property factor and no sector split inside a rating bucket.

**Banking sector stress**

The transmission channel to watch is concentrated CRE lending rather than deposit flight, with concentrated CRE books, rising auto delinquencies and still-large unrealised securities losses converging on regional balance sheets. First-quarter disclosures show reserve-building at some regionals and reserve release at others, so the picture at month-end is idiosyncratic rather than systemic.

> Channel into the model — INDIRECT ONLY, and fast. 2023 showed the transmission runs in days; our spread factors would register it only after the fact, and our calibration window would take months to reflect it.

**Fixed income conditions**

Conditions tightened during the quarter for geopolitical rather than credit-cycle reasons, with the Iran war contributing roughly 50bp of US high yield spread widening and about 80bp in European high yield across February and March. Even so, investment grade and high yield spreads have been notably resilient through 2026, implying markets still weight solid fundamentals over macro noise. Read this as a volatility problem rather than a repricing of credit.

> Channel into the model — PARTIAL. Curve level and shape are modelled; liquidity, issuance and market functioning are not. See the computed section below — the gilt/swap basis is the one market-functioning read our own data carries.

**Equity market fears**

Concentration risk dominates, with valuation comparisons to 1999-2000 now mainstream: expected long-term S&P 500 earnings growth has reached 20.2%, above the 18.6% peak recorded in 2000. Excluding AI-linked names the underlying growth story is materially thinner, which is what makes index drawdowns highly correlated.

> Channel into the model — PARTIAL. Index levels and a vol calibrated from 504 days of history are modelled; concentration, positioning and implied vol are not. Realised vol is computed below as the only vol read we own.

### Policy and politics

**US administration policy, trade and tariffs**

The March FOMC held rates but was not unanimous, with Stephen Miran dissenting in favour of a lower target range. The tariff inflation impulse appears to have crested around this point: Fed research finds tariffs explained a large share of above-target inflation before February 2026, but since March other factors have been the main drivers of the excess.

> Channel into the model — NONE DIRECTLY. It arrives, if at all, already priced into the UST curve, the dollar and credit spreads — which means we see the consequence and never the cause.

**UK fiscal policy and gilt market functioning**

The Spring Statement was deliberately low-event, containing no major tax or policy changes and simply confirming previously announced pension, benefits and savings measures. Gilt supply is set to fall from over GBP300bn in 2025/26 to around GBP250bn in 2026/27 - still historically high but improving - with the remit skewed to short and medium maturities and a record 12.1% unallocated reserve giving the DMO flexibility.

> Channel into the model — PARTIAL, AND THE GAP IS THE POINT. Gilt levels are modelled. A 2022-style dislocation is not: it sits outside the trailing window, so it is precisely the tail this calibration cannot see.

**Geopolitics and conflict**

This is the month's dominant risk. Brent surged more than 55% from the war's outbreak, peaking near $120, as Iranian attacks targeted regional oil infrastructure and vessels in the Strait of Hormuz, through which around 20% of world oil passes. Policy mitigation had begun by month-end, with OFAC authorising on 20 March 2026 the delivery and sale of already-loaded Iranian-origin crude and products, while second-order energy shortages spread to importing economies.

> Channel into the model — NONE. No commodity, energy or event factor exists.

### Insurance-specific

**Reinsurance and retrocession**

No supported reading this month - searches for this theme could not be completed in this session, so no view is recorded rather than manufacturing one.

> Channel into the model — NONE — and we are a specialty insurer. Reinsurance pricing is a first-order driver of this firm's economics and it is completely absent from the factor set. This is material and we cannot currently price it.

**Litigation environment and social inflation**

No supported reading this month - searches for this theme could not be completed in this session, so no view is recorded rather than manufacturing one.

> Channel into the model — NONE. The reserves are deterministic cashflows, so the dominant long-tail casualty risk cannot move a single number in this model. Material and unpriceable here.

**Cyber**

No supported reading this month - searches for this theme could not be completed in this session, so no view is recorded rather than manufacturing one.

> Channel into the model — NONE, twice over: not as an insured peril (no cat or large-loss model) and not as an operational risk to this firm.

### Structural

**Climate — physical and transition**

No supported reading this month - searches for this theme could not be completed in this session, so no view is recorded rather than manufacturing one.

> Channel into the model — NONE. Physical risk needs a cat model we do not have; transition risk needs a sector view our index proxies cannot express.

**Regulatory change**

No supported reading this month - searches for this theme could not be completed in this session, so no view is recorded rather than manufacturing one.

> Channel into the model — NONE. Regulatory change moves capital requirements and reporting, neither of which this framework computes.

**AI disruption — to insureds and to the profession**

Beyond equity valuation, the live question is whether AI capex and the private-credit financing built around it are pro-cyclical together, so that a growth disappointment hits equity beta, spreads and lender concentration simultaneously. Regulatory intervention via privacy or antitrust action is separately flagged as a business-model risk to the sector. Treat AI as a cross-cutting exposure rather than a sector.

> Channel into the model — NONE, and the exposure is two-sided: it changes what we insure and how work like this is done.

## What our own series can say

The market-level reads that do not need the web. These are market data, computed from `data/processed/*.csv`; none of it is a portfolio number.

### Risk appetite, from the credit curve

- HY − BBB: +215.0bp, +12.0bp on the month, level in the 89th percentile of the trailing two years.
- CCC − HY: +666.0bp, +26.0bp on the month, level in the 99th percentile of the trailing two years.
- Read: quality dispersion is the cleanest appetite signal our data carries. It is a market price, not a default rate — and the private-credit sleeve, whose marks lag, will not show any of it until well after the traded market has.

### Fixed income conditions and gilt market functioning

| tenor | gilt − swap basis | Δ month | level %ile (2y) |
|---|---|---|---|
| t2 | +12.1bp | +0.7bp | 80th |
| t5 | +30.1bp | +6.8bp | 98th |
| t10 | +56.9bp | +9.9bp | 85th |
| t20 | +78.8bp | +12.4bp | 46th |

- Read: the gilt/swap basis is the one market-functioning signal we own. It widens when gilts cheapen against swaps — supply, dealer balance sheet, forced selling — and it is the same basis the balance sheet is unhedged against. Issuance calendars, auction tails and dealer positioning are not in our data; live mode covers them.
- GBP swap 2s10s +21.3bp (-36.0bp on the month), level in the 51st percentile of the trailing two years.

### Equity market stress

| index | drawdown from 2y peak | realised day vol (month) | vs trailing |
|---|---|---|---|
| FTSE100 | -6.73% | 1.22% | up |
| SP500 | -6.45% | 1.14% | in line |
| SX5E | -9.78% | 1.50% | up |

- Best-to-worst dispersion across the three indices 4.17pp on the month.
- Read: realised vol is the only volatility read we own. Concentration, positioning and implied vol — the things that actually characterise an equity fear episode — are not in our data and not in the factor set.

### The tail our window cannot see

- The calibration window is 504 business days, about two years. The 2022 gilt/LDI episode sits outside it entirely, which means the most recent genuine dislocation in the market we are most exposed to contributes nothing to the vols and correlations we capitalise against.
- Anything on the menu above that has never happened inside the window is, for this model, a risk of zero probability. That is a property of the calibration, not of the world.

## Standing limitations — material and unpriceable here

- **US administration policy, trade and tariffs** — NONE DIRECTLY. It arrives, if at all, already priced into the UST curve, the dollar and credit spreads — which means we see the consequence and never the cause.
- **Geopolitics and conflict** — NONE. No commodity, energy or event factor exists.
- **Reinsurance and retrocession** — NONE — and we are a specialty insurer. Reinsurance pricing is a first-order driver of this firm's economics and it is completely absent from the factor set. This is material and we cannot currently price it.
- **Litigation environment and social inflation** — NONE. The reserves are deterministic cashflows, so the dominant long-tail casualty risk cannot move a single number in this model. Material and unpriceable here.
- **Cyber** — NONE, twice over: not as an insured peril (no cat or large-loss model) and not as an operational risk to this firm.
- **Climate — physical and transition** — NONE. Physical risk needs a cat model we do not have; transition risk needs a sector view our index proxies cannot express.
- **Regulatory change** — NONE. Regulatory change moves capital requirements and reporting, neither of which this framework computes.
- **AI disruption — to insureds and to the profession** — NONE, and the exposure is two-sided: it changes what we insure and how work like this is done.

These are handed to @red-team as standing items, not re-derived each month. They change when the model changes, not when the market does.

## Sources

- The Outlook for Private Credit amid Rising Market Stress — https://www.goldmansachs.com/insights/articles/the-outlook-for-private-credit-amid-rising-market-stress
- Private Credit Outlook 2026: Market Faces First Big Test — https://www.withintelligence.com/insights/private-credit-outlook-2026/
- Private Credit Redemptions, Defaults, and Wrappers, Oh My! (CAIA) — https://caia.org/blog/2026/04/20/private-credit-redemptions-defaults-and-wrappers-oh-my/
- CMBS Delinquency Rate Rises In March — https://matthewsreisresearch.substack.com/p/cmbs-delinquency-rate-rises-in-march
- 2026 CMBS Delinquency Rates - Multi-Housing News — https://www.multihousingnews.com/cmbs-delinquency-rates/
- Delinquency Rates for Commercial Properties Increased in Q1 2026 - MBA — https://www.mba.org/news-and-research/newsroom/news/2026/04/27/delinquency-rates-for-commercial-properties-increased-in-the-first-quarter-of-2026
- Regional Banks Under Strain: CRE, Autos, and the Unrealized Losses — https://contrarianunicus.substack.com/p/regional-banks-under-strain-cre-autos
- Fixed Income Outlook 2Q 2026: Steering Through the Turn - Neuberger — https://www.nb.com/insights/fixed-income-investment-outlook-2q-2026
- Q3 2026 Credit Research Outlook - State Street — https://www.ssga.com/us/en/institutional/insights/q3-2026-credit-research-outlook
- Top analyst fears bubble popping - Fortune — https://fortune.com/2026/06/08/ai-boom-tech-stocks-bubble-fears-earnings-growth-chipmakers-ipo/
- AI Bubble 2026: Is Your Portfolio at Risk — https://www.aequifin.com/en/blog/ai-bubble-2026-is-the-tech-rally-about-to-burst/
- FOMC statement, 18 March 2026 - Federal Reserve — https://www.federalreserve.gov/monetarypolicy/files/monetary20260318a1.pdf
- Tariff Effects on Inflation Stabilize in Recent Months - St. Louis Fed — https://www.stlouisfed.org/on-the-economy/2026/aug/tariff-effects-inflation-stabilize-recent-months
- March 2026 United Kingdom spring statement — https://en.wikipedia.org/wiki/March_2026_United_Kingdom_spring_statement
- What to Watch Out For in the Spring Statement - NIESR — https://niesr.ac.uk/blog/what-watch-out-spring-statement
- Bond navigators: Spring Statement 2026 and what really matters for gilts - RLAM — https://www.rlam.com/uk/intermediaries/our-views/2026/bond-navigators-spring-statement-2026-and-what-really-matters-for-gilts/
- A timeline of how the Iran war shook oil prices - CNBC — https://www.cnbc.com/2026/04/21/oil-price-iran-war-middle-east.html
- 2026 Iran war - Britannica — https://www.britannica.com/event/2026-Iran-war
- The Strait of Hormuz: Security Developments and Impacts - CRS — https://www.congress.gov/crs-product/R45281
