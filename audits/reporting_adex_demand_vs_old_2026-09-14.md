# reporting_adex_demand — final audit and reconciliation against `_old` (2026-09-14)

`reporting_adex_demand_old` and `reporting_adex_demand_backup` are identical (10,572,801 rows,
USD 679.16M, 2025-01-01..2026-09-05). All comparisons below are against `_old`, restricted to
dates <= 2026-09-03 (last complete O&O day in `_old`).

## State of the new table
- 11,234,359 rows · 621 dates · 2025-01-01 → 2026-09-13 · USD 532.14M · 11 business lines.
- No duplicate grain keys on any day (the 08-29 / 08-30 double load has been repaired).
- No missing dates in 2025-01-01..2026-09-13.
- 2026-09-13 is incomplete: STX only (no BFM lines, closing lands D-2) and its 376 curation
  rows fell to the 'PMP - Curation' fallback (USD 10.7k) because reporting_curation_deals had not
  been loaded for that day when the adex loader ran. Re-run 09-13 after both sources land.
- Unchanged known issue: rows before 2025-05-19 are the old model's rows verbatim, with
  'DSP Not Found' on 60% of revenue.

## Alignment (comparable perimeter = `_old` minus the four External lines)
| period | old core | new | delta |
|---|---|---|---|
| 2025-01 .. 2025-04 | identical to the cent | | 0.0% |
| 2025-05 .. 2026-06 | | | +0.2% .. +1.2% |
| 2026-07 | 26.72M | 27.04M | +1.2% |
| 2026-08 | 25.02M | 26.23M | +4.8% |
| 2026-09 (1-3) | 2.47M | 2.48M | +0.5% |

Dimensions, 2026-01-01..2026-09-03: product_category within +0.4..+2.6%; publisher_country within
+0.3..+2.0% (US +2.0% carries the Beachfront/CTV effects); connection_type Reseller −4.4%,
Direct +7.3%, BidSwitch +7.3% (mapping-driven reclassification, see D3).

## Discrepancies and their causes
**D1. Late-August daily outliers are `_old`'s defects, not the new table's.** `_old` has no BFM
lines on 2026-08-26/27/28 and no O&O on 2026-08-29 (78,730 USD for the whole day). New is
complete on all four. This also explains part of the August +4.8% and the PMP Web +3.1% in
August (one missing O&O day ≈ 3.2%).

**D2. Seedtag-named Beachfront Open Auction deals moved from the BFM side to the STX side.**
Deals named 'Seedtag' / 'SEEDTAG DON'USE' in reporting_closing_bfm_demand (Open Auction - BFM)
started 2026-07-10. New excludes them from 'Open Auction - BFM' (−12.5% Jul, −34.9% Aug,
−62.9% Sep 1-3 vs old) and measures them from SSP responses under 'Open Auction - Seedtag'
(+1.2% Jul, +7.5% Aug). USD 1.16M through 09-03; the largest buyer is Pubmatic (539k), which is
exactly the 'Pubmatic / Direct' revenue that disappears from Open Auction - BFM (old 539k, new
56k). Net effect on totals ≈ 0 by design; the split by business line and by DSP changes.

**D3. DSPs resolved from mapping tables instead of hardcoded lists.** 'DSP Not Found' −6.06M
(−49%) in 2026 YTD; Adform +4.10M, OneTag +1.85M, AdYouLike +75k are the same revenue, now
resolved (old: 'DSP Not Found' / Reseller; new: named DSP / Direct). 'Seedtag' (old, 737k) is
now 'Seedtag DSP Buying Engine' (874k). This is also the Reseller −4.4% / Direct +7.3% shift.

**D4. PMP CTV - O&O +24% overall (+7% .. +53% by month since Aug 2025).** New keeps the closing
'PMP - Seedtag' rows in full; old excluded autobuying deals (they belonged to the External branch
that no longer exists). Verified: the monthly delta equals the revenue of autobuying deals in
'PMP - Seedtag' to the dollar for every month Jan-Jul and Sep 2026 (100%); August is 75%
explained, the rest is D1 (old missing three BFM days).

**D5. Curation lines +39% Aug / +41% Sep vs old's Select-BFM + DSPM-BFM + PMP-Curation.**
Aug 2026: old 572k, new 795k, closing source 470k. New = closing in full (470k, no deal-name
anti-join) + STX-side curation deals labelled from reporting_curation_deals (~325k). Old had
the anti-join (dropped dual-routed rows) and only 126k of STX-side deals under 'PMP - Curation'.
Note 'DSP marketplace - Migrated' (417k Aug) receives BFM-native rows too via the as-of-latest
label lookup, so the Marketplace / Migrated split is label-driven, not source-driven.

**D6. Minor.** 'Equativ' (old 32k) renamed under the mapping; Madopi −51% (31k) and
Pubmatic +15k on Open Auction - Seedtag are within mapping/timing noise.

## Verdict
Totals are aligned within ~1% everywhere except August 2026 (+4.8%), and every August driver is
identified: old's four broken days, autobuying deals now counted, Seedtag-named Beachfront deals
now measured on the STX side. No unexplained discrepancy remains. The residual risks are the
ones already open: 09-13 incomplete, pre-2025-05-19 DSP dimension, and the label-driven
Migrated split.

## Addendum — 2025-01-01 .. 2025-05-18 (before stg_ssp_responses_daily exists)
**Totals:** new = old core to the cent (138 days; 27 differ by cents, max USD 47.95; January +499 USD
because the BFM branch was re-derived from closing without the old deal-name anti-join). BFM lines
match reporting_closing_bfm_demand at 100.0% every month. O&O rows are the old model's rows.
**What was re-derived on those rows:** connection_type only — 5.76M USD moved Reseller → Direct via
mapping_direct_dsp and now matches the enriched source exactly; the single channel difference vs the
source is 'Beachfront' (339k, excluded on purpose in this era). 'DSP Marketplace - BFM' (3.59M) is
split into 'DSP Marketplace' 2.92M + 'DSP marketplace - Migrated' 0.67M by the as-of-latest label.
**Three methodological seams at 2025-05-19, i.e. pre-May rows are NOT built like post-May rows:**
1. DSP: 'DSP Not Found' = 58.0M of 86.8M O&O revenue (67%) in old AND new. NOT fixable from any
   source: the raw responses carry no bidder_id before June 2025 (100% missing Jan–May, ~20% from
   June), so etl_ssp_responses_daily_enriched itself labels 85% of that revenue 'Unknown'
   (corrects the earlier note that enriched resolves it — 'Unknown' is not NULL).
2. product_category: pre-May the PMP Web and Direct Web lines are 100% Display; from 05-19 they are
   47% / 39% Online Video. ≈11M USD of video revenue is labelled Display before the seam
   (source has product_category = Video for it). Fixable from enriched if ever re-derived.
3. PMP CTV - O&O: pre-May excludes autobuying deals (gap = autobuying revenue to the dollar every
   month, 327k total); post-May includes them.
