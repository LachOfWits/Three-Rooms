# Data sources and provenance

One section per processed file / data block. See SPEC section 8 for schemas.

## GBP rates

Covers `data/processed/gbp_gilt.csv` and `data/processed/gbp_swap.csv`
(schema `date,t2,t5,t10,t20`, daily, rates in **percent**, tenors = 2/5/10/20y
zero/spot rates). Window: 2023-08-01 to 2026-08-21, Monday-Friday business-day
grid, 799 rows each.

**Source (worked on first attempt): Bank of England yield curve archive**
<https://www.bankofengland.co.uk/statistics/yield-curves>, downloaded
2026-08-28 by `data/raw/fetch_gbp.py`. Raw ZIPs kept in `data/raw/`:

| File | Contents used |
|---|---|
| `glcnominalddata.zip` | `GLC Nominal daily data_2016 to 2024.xlsx`, `GLC Nominal daily data_2025 to present.xlsx` |
| `oisddata.zip` | `OIS daily data_2016 to 2024.xlsx`, `OIS daily data_2025 to present.xlsx` |
| `latest-yield-curve-data.zip` | `GLC Nominal daily data current month.xlsx`, `OIS daily data current month.xlsx` |

- `gbp_gilt.csv` = BoE **nominal government liability curve (GLC)** spot rates —
  gilt zero-coupon yields.
- `gbp_swap.csv` = BoE **overnight index swap (OIS, SONIA-based)** spot rates,
  used as the **proxy for the TRS/swap reference curve** (SPEC section 2 block 1).
  This is a deliberate, documented proxy: OIS zeros are the cleanest freely
  available daily GBP risk-free swap curve; no free daily SONIA par/zero swap
  curve at all four tenors was found. Marked as a proxy here, but the numbers
  themselves are genuine published BoE data, not constructed.

**Extraction.** Sheet `4. spot curve` of each workbook (rows = dates as Excel
serials, columns = maturities in years); columns 2.0/5.0/10.0/20.0 taken as
`t2/t5/t10/t20`. The venv has no openpyxl, so `fetch_gbp.py` parses the xlsx
XML directly with the standard library (zipfile + ElementTree); one value was
cross-checked against the workbook by hand (gilt 2y on 2025-01-02 = 4.198192).
Rates are kept in percent exactly as published (rounded to 6 dp). BoE spot
rates are continuously-compounded zero rates; the engine's annual-compounding
discounting (SPEC section 3) treats them as annually compounded — an accepted
approximation at these rate levels (< 0.1 pp effect), consistent for both
curves.

**Duplicates/overlaps.** Where the "2025 to present" and "current month"
workbooks overlap, the later file wins (values are identical in practice).

**Gaps and filling.** Reindexed onto the Mon-Fri grid and forward-filled with
a 5-business-day limit (SPEC section 8). No gap exceeded the limit; every
filled date is a UK bank holiday or BoE non-publication day (about 8-9 per
year: New Year, Good Friday, Easter Monday, early-May, late-May and August
bank holidays, Christmas, Boxing Day; plus 2024-05-01 for OIS only). Exact
lists are printed by the fetch script on each run: 24 dates filled for
gbp_gilt, 25 for gbp_swap.

**Validation** (asserted by the script on every run): no NaNs, dates strictly
ascending and unique, all values within 0-8%. Observed ranges (%):

| Series | t2 | t5 | t10 | t20 |
|---|---|---|---|---|
| gbp_gilt | 3.42-5.17 | 3.26-4.76 | 3.48-5.21 | 4.07-5.89 |
| gbp_swap | 3.31-5.78 | 3.25-5.07 | 3.14-4.71 | 3.27-5.16 |

**Reproduce:** `python data/raw/fetch_gbp.py` (add `--no-download` to reuse the
ZIPs in `data/raw/`, `--force-download` to refresh them).

## USD rates and credit

Covers `data/processed/ust.csv` (schema `date,t2,t5,t10,t20`) and
`data/processed/credit_oas.csv` (schema `date,AA,A,BBB,HY,CCC`), daily, values
in **percent**. Window: 2023-08-01 to 2026-08-21, Monday-Friday business-day
grid, 799 rows each. Downloaded 2026-08-28 by `data/raw/fetch_usd.py`.
The `CCC` column was added 2026-08-28 for the 21-factor SPEC extension
(private-credit proxy factor, SPEC section 2); the pre-existing columns were
regenerated from the same cached raw files and are unchanged.

**Source: FRED (St. Louis Fed) CSV endpoint** — no API key,
`https://fred.stlouisfed.org/graph/fredgraph.csv?id=<SERIES>&cosd=...&coed=...`.
Raw responses kept verbatim in `data/raw/<SERIES>.csv`.

| Column | FRED series | Description |
|---|---|---|
| ust.t2 | `DGS2` | Market yield on UST at 2y constant maturity (H.15) |
| ust.t5 | `DGS5` | ... 5y constant maturity |
| ust.t10 | `DGS10` | ... 10y constant maturity |
| ust.t20 | `DGS20` | ... 20y constant maturity |
| oas.AA | `BAMLC0A2CAA` | ICE BofA AA US Corporate Index OAS |
| oas.A | `BAMLC0A3CA` | ICE BofA Single-A US Corporate Index OAS |
| oas.BBB | `BAMLC0A4CBBB` | ICE BofA BBB US Corporate Index OAS |
| oas.HY | `BAMLH0A0HYM2` | ICE BofA US High Yield Index OAS |
| oas.CCC | `BAMLH0A3HYC` | ICE BofA CCC & Lower US High Yield Index OAS |

**Stated proxies.**

1. **Par yields as zero rates.** DGS* are constant-maturity **par** yields
   (semiannual-coupon convention); `ust.csv` uses them directly as the 2/5/10/20y
   **zero-curve proxy** required by SPEC sections 2 and 8. No bootstrapping is
   applied. The par-zero gap is small at these curve slopes (roughly 0-15 bp at
   20y) and the same convention is used consistently across the whole window.
2. **USD index OAS for both currencies.** The BAML series are **USD** corporate
   index OAS. Per SPEC, the `spread` block has one level per rating applied to
   both USD and GBP corporates, so these USD OAS levels are used for **both
   currencies**. GBP credit typically trades within a few tens of bp of USD at
   the same rating; accepted simplification.
3. **AA before 2023-08-29 proxied from A** — see gap notes below.

**FRED 3-year clamp on ICE BofA series and Wayback recovery.** As of the
download date, the fredgraph.csv endpoint serves only the trailing ~3 years for
BAML series (from 2023-08-29) regardless of the requested `cosd` — a licensing
restriction; the old full-history `/downloaddata/` endpoint has been retired.
The window's first 20 business days (2023-08-01 to 2023-08-28) were recovered
from **Wayback Machine snapshots of the identical FRED endpoint** (raw copies in
`data/raw/wayback_<SERIES>.csv`; permalinks hardcoded in `fetch_usd.py`):

- A: `web.archive.org/web/20260804130915/.../fredgraph.csv?id=BAMLC0A3CA` (data from 2023-08-01)
- BBB: `web.archive.org/web/20260422045714/.../fredgraph.csv?id=BAMLC0A4CBBB` (data from 2023-04-24)
- HY: `web.archive.org/web/20251104204105/.../fredgraph.csv?id=BAMLH0A0HYM2` (full history from 1996)
- CCC: `web.archive.org/web/20260422045816/.../fredgraph.csv?id=BAMLH0A3HYC` (data from 2023-04-24; found via the Wayback CDX index, one of only two snapshots of this series)

Live values win where both exist; on every overlapping date live and archived
values agree exactly (max abs diff 0.0000 pp, checked on each run; the script
fails if they differ by more than 5 bp). **No archived snapshot of the AA OAS
series exists anywhere** (verified via the Wayback CDX index), so the 20
business days 2023-08-01 to 2023-08-28 of the AA column are a **constructed
proxy**: `AA(t) = A(t) x 0.6250`, the multiplier being the median AA/A ratio
over the first 20 overlapping observations (2023-08-29 onward), rounded to 2 dp
like the source. The proxy joins the real series smoothly (proxied 0.64 on
2023-08-28 vs published 0.66 on 2023-08-29). These 20 proxied rows sit outside
every calibration window implied by SPEC section 8 (a 504-business-day window
from the earliest as-of 2025-12-31 reaches back only to ~2024-01).

**Gaps and filling.** Reindexed onto the Mon-Fri grid and forward-filled with a
5-business-day limit (SPEC section 8). No gap exceeded the limit. Filled dates
are US federal holidays / non-publication days: 34 dates per DGS column
(H.15 skips ~11 holidays/yr), 8 per OAS column including CCC (ICE publishes on
most US holidays; misses e.g. Good Friday, Christmas — for CCC the 8 filled
dates were verified to be exactly Christmas, New Year and Good Friday
2023-2026). The AA column additionally has the 20 proxied dates described
above (proxied before filling, so holiday fill never crosses the proxy
boundary with stale proxy values beyond 5 days).

**Validation** (asserted by the script on every run): no NaNs after filling,
exactly 799 unique ascending business days, all values within plausibility
bounds (yields 0-10%; AA 0.1-4%, A 0.2-5%, BBB 0.4-8%, HY 1-15%, CCC 3-25%).
Observed ranges (%):

| Series | min-max |
|---|---|
| t2 | 3.38-5.19 |
| t5 | 3.41-4.95 |
| t10 | 3.63-4.98 |
| t20 | 4.01-5.30 |
| AA | 0.41-0.73 |
| A | 0.59-1.15 |
| BBB | 0.92-1.63 |
| HY | 2.59-4.61 |
| CCC | 6.90-11.37 |

The CCC level sitting 4-8 pp above HY throughout the window (and its daily-change
vol roughly 1.7x HY's) is the economic content the 21st factor exists to carry:
it is the stress proxy for private-credit proxy-rating challenges (SPEC-APP D4).
On every one of the 693 overlapping dates the live and archived CCC values agree
exactly (max abs diff 0.0000 pp, checked on each run).

**Reproduce:** `python data/raw/fetch_usd.py` (reuses cached raw files in
`data/raw/` if present; `--refresh` forces re-download — note the live FRED
endpoint can no longer serve pre-2023-08-29 OAS history, which is why the
Wayback raw files should be kept under version control).

## Equity and FX

Covers `data/processed/equity.csv` (`date,FTSE100,SP500,SX5E`, daily index
**levels**) and `data/processed/fx.csv` (`date,GBPUSD`, **USD per 1 GBP** per
SPEC section 1). Window 2023-08-01 to 2026-08-21 on the Monday-Friday
business-day grid, 799 rows each — same grid as the rates files.

**Source: Yahoo Finance v8 chart API** (daily closes), downloaded 2026-08-28 by
`data/raw/fetch_eqfx.py`; raw JSON responses kept in `data/raw/`:

| Column | Ticker | Raw file |
|---|---|---|
| FTSE100 | `^FTSE` | `yahoo_FTSE100.json` |
| SP500 | `^GSPC` | `yahoo_SP500_gspc.json` |
| SX5E | `^STOXX50E` | `yahoo_SX5E.json` |
| GBPUSD | `GBPUSD=X` | `yahoo_GBPUSD_x.json` |

Endpoint: `https://query1.finance.yahoo.com/v8/finance/chart/<ticker>?period1=...&period2=...&interval=1d`.
Timestamps are converted to local trading dates using the `gmtoffset` in each
response's meta (this also collapses the FX feed's Sunday-evening opens onto
weekdays); null closes dropped; duplicate dates keep the last observation.

**FRED was tried first for SP500 (id `SP500`) and GBPUSD (id `DEXUSUK`,
USD per GBP — same orientation as the SPEC convention) via
`fredgraph.csv`, but every attempt from this machine timed out at download
time (3 retries x 60 s per series; other FRED pulls had succeeded ~15 min
earlier, so this looks like transient rate-limiting). The script prefers FRED
when reachable, verifies DEXUSUK against Yahoo `GBPUSD=X` (max abs diff must
be < 0.05), and falls back to Yahoo otherwise — the published files are
entirely Yahoo. `GBPUSD=X` is definitionally USD per 1 GBP (about 1.21-1.38
over the window), matching SPEC section 1.**

**Not proxies.** All four series are the actual index/FX closes, not rebased
ETF proxies. Spot-checked against published closes on 2023-08-01: FTSE100
7666.27, SP500 4576.73 (files: 7666.3, 4576.73).

**Gaps and filling.** Reindexed onto the Mon-Fri grid and forward-filled with
a 5-business-day limit (SPEC section 8); the fill is seeded from the last
pre-window observation so a hole on the first in-window day can fill. Filled
dates (exact lists printed by the script on each run): FTSE100 24 (UK
holidays), SP500 31 (US holidays plus Yahoo data holes on 2025-01-09 — the
Carter national day of mourning, a real market closure — and none besides),
SX5E 34 (TARGET/Eurex holidays and half-day gaps in Yahoo's feed, including
2023-08-01, a real trading day missing from Yahoo — true close 4468.51 vs
filled 4471.31, a 0.06% discrepancy on that one date), GBPUSD 5 (global FX
holidays). No gap exceeded 5 business days; the script aborts if one does.

**Validation** (asserted on every run): no NaNs after filling, dates strictly
ascending and unique, levels within plausibility bands (FTSE100 6500-12500,
SP500 3800-9000, SX5E 3800-7500, GBPUSD 1.05-1.60). Observed ranges:

| Column | min | max | first (2023-08-01) | last (2026-08-21) |
|---|---|---|---|---|
| FTSE100 | 7257.8 | 10910.6 | 7666.3 | 10816.6 |
| SP500 | 4117.37 | 7798.99 | 4576.73 | 7674.37 |
| SX5E | 4014.36 | 6551.22 | 4471.31 | 6462.22 |
| GBPUSD | 1.2078 | 1.3825 | 1.2834 | 1.3643 |

Levels rounded to 2 dp (indices) / 4 dp (FX).

**Reproduce:** `python data/raw/fetch_eqfx.py` (add `--no-download` to rebuild
from the cached raw JSON in `data/raw/`).
