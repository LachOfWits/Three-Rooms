# scenarios/reference — mock detection reference data

Reference files the agents MAY read (via `read_reference`). None of them is
ground truth; each encodes public knowledge or fixed experience priors used by
the deterministic mock checks (and available as live-mode cross-checks).

## ratings_ref.csv

Public senior-unsecured issuer ratings for all 16 public corporate bonds in
`book/positions.json` (P013–P028), keyed by ISIN. Used by the mock
`@book-warden` check to validate the `rating` field in the book, including
seeded variants such as `scenarios/seeded/positions_D2.json`. The four private
credit funds (PCF-001..004) are deliberately NOT here — they are unlisted, have
no ISIN and no public rating; their proxy-rating appropriateness is covered by
`pc_proxy_ref.csv` instead.

Columns: `issuer,isin,agency_rating`.

Provenance and precision: ratings are the approximate composite of S&P /
Moody's / Fitch senior unsecured ratings as publicly reported around 2025–2026,
expressed on the S&P letter scale. They are approximately correct by design
(one composite letter per issuer; agencies can differ by a notch) — the check
compares BUCKETS, not notches, so notch-level approximation is harmless.
Notable composites:

- Ford Motor Credit: S&P BB+ / Moody's Ba1 / Fitch BBB- (crossover credit;
  majority sub-investment-grade) -> BB+ here -> HY bucket.
- Lloyds Banking Group (holdco senior): Moody's A3 / Fitch A / S&P BBB+ ->
  A- here -> A bucket, matching the book.
- Barclays plc (holdco senior): Moody's Baa1 / S&P BBB+ / Fitch A -> BBB+ ->
  BBB bucket, matching the book.
- Ocado: deep sub-investment grade (single-B range from Moody's/Fitch) -> B.
- Virgin Media Secured Finance (secured notes): BB-range across agencies -> BB-.

Bucket mapping (agency letter -> the book's rating buckets):

| agency_rating           | book bucket |
|-------------------------|-------------|
| AAA, AA+, AA, AA-       | AA          |
| A+, A, A-               | A           |
| BBB+, BBB, BBB-         | BBB         |
| BB+ .. B-               | HY          |
| CCC+ and below          | CCC         |

Under this mapping every position in the base book agrees with its reference
bucket. Any disagreement between a book `rating` and the mapped
`agency_rating` bucket is a finding.

## pc_proxy_ref.csv

Strategy -> acceptable proxy-rating band for the private credit sleeve
(SPEC-APP §6, D4 detection route). Columns:
`strategy,acceptable_proxy_ratings,typical_proxy,notes` — the acceptable set
is a `;`-separated list of book rating buckets. A PC fund whose selected proxy
rating is outside its strategy's acceptable set is a finding (in the seeded
D4 book all four funds carry `CCC`, which no strategy admits). The bands
encode the standing judgement that these are PERFORMING lending strategies:
broad-HY (single-B-equivalent) is the accepted proxy, senior-secured and
real-estate debt may justify BBB, and CCC is a distressed bucket whose
market-calibrated spread level and vol would overstate the sleeve's risk.

## realist_priors.yaml

`@realist`'s fixed experience bands: standalone 99.5% VaR as a % of market
value per asset class / currency / rating band / maturity bucket, plus
aggregate- and block-VaR-as-%-of-assets bands. Calibration is documented in
the file header: bands enclose (with margin) the observed ratios of every
position across all eight clean month-end runs and both clean March-book
runs, so the clean base runs produce ZERO flags, while the D1 isolation run
breaches below band at the aggregate/ir_gbp-block level and the D4 book
pushes all four PCF positions (and the credit block) above band. Deliberately
independent of any month's assumptions.
