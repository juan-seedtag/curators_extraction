# Audit: `st_datalakehouse.analytics.reporting_curation_deals` (2026-09-11)

Table requested as `reporting_deals_curation`; the warehouse name is `reporting_curation_deals`.
Definition = `sql/deals_daily.sql` (48 columns, identical). Materialized daily by
`bf_automations/curation.py` (external repo), loaded incrementally: full backfill on 2026-09-10
(07:00 UTC for 2025-01-01..2026-02-18, then hourly chunks 13:00-18:00 through 2026-09-09), and
today's 07:34 UTC run wrote only 2026-09-10. `generate_report.py` reads the table by default.

## Shape

| origin | rows | deals | min date | max date | dates | gross EUR | margin EUR |
|---|---|---|---|---|---|---|---|
| BFM | 817,894 | 1,470 | 2025-01-01 | 2026-09-09 | 614 of 617 | 16,087,961 | 3,028,618 |
| STX | 18,883 | 842 | 2026-02-19 | 2026-09-10 | 204 of 204 | 494,568 | 126,316 |

Row count equals today's `output/deals_daily.csv` (836,777).

## Passed

- STX money reconciles to `big_query_bdb.business.daily_curation_delivery_utc` at the cent for
  every month (gross, pub cost, curator margin, platform spend, deal counts).
- BFM reconciles exactly to `reporting_bfm_demand` (Select-BFM + DSP Marketplace-BFM) every month
  except March 2025 (see H1).
- STX funnel for 2026-09-09 (requests, bids, wins, impressions, 614 deals) matches
  `deal_channel_metrics_hourly` exactly; every NEUROX/CUR- deal with traffic that day is present.
- EUR: EUR rows lc == eur; implied USD 1.16 (STX) / 1.139 (BFM avg); BRL 6.0; fx_rates_daily has
  full daily coverage; no NULL eur where lc > 0.
- curator_margin_total = stx + curator in all rows; bid_rate, win_rate, cpm_lc, margin_pct recompute
  exactly; no negative gross or pub cost; first_seen <= date always.
- SF fan-out guards (multi deal_name, multi product line, split > 100%) all 0; delivery source grain
  (dt, deal_id) has one crm id and one currency.
- Freshness: STX D-1, BFM D-2 (same as its source). Salesforce-only sentinel rows (56) exist only on
  the latest date and did not accumulate vs the 2026-09-03 snapshot (51 rows on 2026-09-01 only).
  All 56 deals were created in SF on or after 2026-06-23.

## Status update 2026-09-14

- **H1 RESOLVED** — the three March 2025 days were backfilled on 2026-09-11; the table now holds
  1,819 / 1,831 / 1,811 rows on 03-28 / 03-29 / 03-30 and BFM gross rose to EUR 16,218,987
  (+131,025 vs this audit). Dashboard rebuilt on 2026-09-14 with 842,238 rows.
- **Still open:** H2 (margin definition per origin), H3 (Beachfront->STX overlap), H4 (frozen
  history / no rolling re-load), and every Medium and Low item below.
- **Freshness at 2026-09-14:** table max date is 2026-09-10; 09-11 and 09-12 are loadable now,
  09-13 is not (Beachfront sources reach 09-12 only).

## High

**H1. BFM missing 2025-03-28, 03-29, 03-30.** [RESOLVED 2026-09-11 — see status update above.] Source has 141,674 USD gross / 12.76M impressions on
those days; table has no rows. Incremental load will not self-heal. Backfill.

**H2. Margin is defined differently per origin.** STX margin = gross - curator share - PAD - pub cost.
BFM margin = gross - pub cost; the curator margin (reseller_revenue, 419,805 USD over the period,
~14% of BFM margin) is not subtracted. The SQL comment already flags this ("avisar si debe
restarse"). Needs a business decision; until then margin is not comparable across origins.

**H3. Beachfront -> STX migration overlap.** 55 deals appear in both origins (migrated Aug 2026,
BFM stops ~2026-08-19 for most). On 650 deal-days both origins carry revenue for the same deal:
STX 67,901 EUR + BFM 77,929 EUR. Top: 443119806437 (BFM 46.7k / STX 13.7k on 14 overlap days),
BFMSA003 (24.7k STX). Either dual routing during migration (fine) or double count; confirm with
Beachfront/curation owners. Side effect: 63 deals carry two business lines ('DSP Marketplace' in
BFM vs 'DSP marketplace - Migrated' in STX), 3.28M EUR of history split across labels for the
same deal.

**H4. Incremental load freezes history.** Source restatements and dimension changes after load do
not propagate. Already visible: 2026-09-09 publisher cost is 22.73 EUR lower in the table than in
the source today. SF attributes, business_line, inventory_type (traffic-based over full history),
agency normalisation and the dcm curation filter are all evaluated at load time. Today 0 dimension
drift within STX deals (backfill was yesterday), but it will accumulate. Recommend re-loading a
rolling window (e.g. last 14 days) plus a periodic full rebuild.

## Medium

**M1. Grain is not uniform.** STX = (date, deal_id). BFM = (date, deal_id, seat_id, ad_name,
agency, dsp, media_type): 200,135 of 298,605 BFM (date, deal) keys have >1 row; 178,423 have
multiple seats; 44 BFM deals span several DSPs (one 248k EUR deal spans 4); 14 BFM deals are both
CTV and Web. Document; consumers must not assume one row per deal-day.

**M2. STX duplicate key.** LEXUS deal 71dc461d-ed39-4dfe-a1ae-94c3991c8561 on 2026-07-27 has two
rows (dcm deal_name 'MAY 2026' vs 'JUL 2026'). The "0 deals" assumption in the dcm CTE is now
violated. No money duplicated (no delivery that day) but requests split. Fix: drop deal_name from
the dcm grain (take it from del/sf/arbitrary).

**M3. BFM funnel ratios are meaningless.** bids > requests in 633,450 BFM rows (77%); bid_rate
median 102%, max 6.6e11; win_rate > 100% in 1,000 rows. Beachfront convention (requests =
ads_served, bids = outgoing_bids). Null out bid_rate/win_rate for BFM or rename the columns.

**M4. Business-line classification edge cases.**
- 6 OMG NL deals named '*_Seedtag_Test*' are 'Curation Agency' (2,592 EUR) because the
  NEUROX + SF-agency rule precedes the TEST rule. Confirm whether agency tests count as revenue.
- 6 CUR- TEST deals with no agency fall to 'DSP marketplace - Migrated' (11.62 EUR): wrong bucket
  (the fallback label). 15 TEST-named deals sit in 'DSP Marketplace' (44 EUR) by design.
- Source defect: `reporting_bfm_demand.clearvu_account` = 'nan' on 2026-05-28..31 flips 12 deals to
  'Curation 3rd Party' with agency 'nan' (96 rows, 93 EUR) and creates 31 deal-days with two
  business lines. Fix upstream or map 'nan' -> NULL in bfx.

**M5. Negative margins in STX.** 164 rows, -7,529 EUR; 148 rows with pub cost > gross.
Concentrated in Curation 3rd Party: RtbHouse 'RTB_RON_Display_mlm_Global' (pub cost 192% of gross,
-5,961 EUR), Qrate DV360 deals (pub cost up to 713% of gross). Likely a pricing/cost-base issue in
the delivery source; raise with its owners.

**M6. Curator margin unsplit.** 181 STX rows (719 EUR, matches source) have curator_margin_total > 0
but stx/curator split NULL because the deal is not in SF (Seedtag test deals, DSP DV360 marketplace
deals). Margin treats the curator share as 0, probably right, but the split columns should be
total/0 rather than NULL.

## Low

- Labels: agency trailing spaces ('Lotame ', 'Test GB '), '' agency (4 BFM deals), deal_id 'nan'
  (201 BFM rows, 0 EUR). DSP names unnormalised on SF-only rows ('Yahoo' vs 'Yahoo DSP',
  'TTD'/'The Trade Desk'/'TheTradeDesk', 'Viant Technologies', 'Conversant' vs
  'Conversant/Epsilon'). `format` mixes BFM media_type ('Video','Display') with SF names
  ('Standard Display', ...). `mapping_direct_dsp` still has AppNexus twice (MSAN | Xandr).
- `pct_of_total` is NULL in every row (also in the CSV): dead column. `cpm_eur` for BFM is computed
  before rounding gross_eur (199k rows differ from a recompute, only when impressions < 5).
- 293 BFM rows with NULL channel_id/dsp (advertiser NULL upstream, 0.82 EUR); 6,403 BFM rows with
  all metrics zero.
- STX history starts 2026-02-19 (delivery source starts 2026-02-24) while BFM starts 2025-01-01.
- connection_type = 'Reseller' for all 56 SF-only rows is an artifact of missing channel_id.
- `requests` summed across deals (1.5T/day for 614 deals) is per-deal matching, not exchange
  volume; not additive across deals.
