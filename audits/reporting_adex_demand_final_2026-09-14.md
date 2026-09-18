# reporting_adex_demand — full audit with source reconciliation (2026-09-14, after history update)

## State
- 11,220,438 rows · 620 dates · 2025-01-01 → 2026-09-12 · USD 531.27M · 10 business lines. No date gaps.
- 'DSP marketplace - Migrated' now starts 2026-07-28 (15,363 rows): the history UPDATE has been applied.
  2026-09-13 is no longer in the table (was STX-only); needs re-loading once Beachfront closing lands.
- Side effect of the UPDATE: 229,259 rows on 573 days (2025-01-01..2026-07-27) share a grain key with
  another row (USD 6.69M on those keys). Sums unaffected; grain uniqueness lost on those days.
- 854,107 bid-only rows with revenue_gross NULL (0 impressions); 90 rows with negative revenue (−0.09 USD).

## Totals vs reporting_adex_demand_backup (identical to _old), dates <= 2026-09-03
| | |
|---|---|
| new vs backup TOTAL | 72–88% every month — the four External/Managed lines (~USD 160M lifetime) are not in the new model |
| new vs backup excl. External | +0.0% (2025-01..04, identical), +0.2..+1.2% (2025-05..2026-07), +4.8% (2026-08, explained in the previous report), +0.5% (Sep 1-3) |

## Line-by-line reconciliation to the proper source
**Open Auction - BFM vs reporting_closing_bfm_demand (excl. deal_name 'Seedtag'/'SEEDTAG DON'USE'):**
100.0% in every full month Jan 2025 – Aug 2026 (Sep 91.5%: table ends 09-12, source runs later).
Vs reporting_bfm_demand (same exclusion): 90–97% every month. Structural: closing `revenue` is net of the
Select fee adjustments and the pro-rated Ent Aggregator subtraction; reporting_bfm_demand `revenue_gross`
is before them. The loader reads closing.

**PMP CTV - O&O vs reporting_closing_bfm_demand 'PMP - Seedtag':** 100.0% every month from Jun 2025.
Feb–Apr 2025 = 52–81%: those rows are the old model's and exclude autobuying deals (gap = autobuying
revenue to the dollar). Jan 2025 100%, May 2025 98%. Vs reporting_bfm_demand: 95–99.5%.

**Open Auction - Seedtag / PMP Web - O&O / Direct Web - O&O vs etl_ssp_responses_daily_enriched
(product_type OMP / PMP / Direct):** PMP 100.0% and Direct 100.0% every month; Open Auction 99.2–99.7%
(Jan–May 2025, old rows exclude channel 'Beachfront') and 100.0–101.4% (Jun 2025 onward, Beachfront-leg
rows included by design). September 91–93% only because the table ends 09-12 and the source 09-13.

**Curation lines (DSP Marketplace, Migrated, Curation 3rd Party, Agency, Test) vs reporting_curation_deals
converted to USD with fx_rates_daily:** 87–97% by month, 99% in Sep. Fully explained by the same fee
netting: on the Beachfront side adex uses closing revenue while the curation table uses
reporting_bfm_demand gross; closing/gross runs 95–98% and dips to 87–91% in Feb–May 2026, exactly the
months where adex/curation dips (88.7 / 86.9 / 90.7%). Seedtag side: adex measures SSP responses
(net_imp_paid) vs the curation table's delivery net_revenue; Sep 99.3%.
Label semantics: Aug 2026 adex 'Migrated' 417k vs curation Seedtag-side 350k USD → ~67k of Beachfront-side
rows still carry 'Migrated' in Aug–Sep. Expected until the loader's Beachfront branch stops emitting the
label and 2026-07-28 → today is re-run.

## Verdict
Every business line reconciles to its actual source at 100% (closing-sourced lines) or 99–101%
(responses-sourced lines) in every complete month since the new loader's era; the sub-100% readings
against reporting_bfm_demand and the curation table are fee-netting definitions, not missing data.
Residual items: 09-13 to reload; Feb–Apr 2025 PMP CTV on old-model rules; duplicate grain keys from the
UPDATE; Aug–Sep Beachfront-side 'Migrated' rows pending the loader fix.
