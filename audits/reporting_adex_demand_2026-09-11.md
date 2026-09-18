# Audit: `st_datalakehouse.analytics.reporting_adex_demand` (2026-09-11, ~15:30 UTC)

**Caveat: the table was being rewritten while this audit ran.** `bf_automations/adex_demand_new.py`
(PID 79819, started 12:00, `python3 adex_demand_new.py 2025-05-19`) is re-slicing the table day by
day with DELETE + INSERT and had reached 2026-04-29 at the time of writing. Rows for
2025-05-19..~2026-04-29 reflect today's rewrite; everything else is the bulk write of 2026-09-10 07:00
(the swap from `_new`). The `_new` table no longer exists; `reporting_adex_demand_old` holds the
previous model.

Definition = the loader's REBUILD_SQL (new architecture: no External/Managed branch; STX from
`stg_ssp_responses_daily` without the Beachfront/SpringServe exclusion; BFM from
`reporting_closing_bfm_demand` with Select/DSPM kept in full; deal-level curation labels looked up
from `reporting_curation_deals` via `max_by(business_line, date)` per lower(deal_id)).

## Shape

| rows | dates | min | max | revenue USD | business lines | DSPs | channels | countries |
|---|---|---|---|---|---|---|---|---|
| 11,038,847 | 612 of 615 | 2025-01-01 | 2026-09-07 | 523,990,437 | 11 | 237 | 126 | 165 |

## Passed

- Declared grain (date, connection_type, business_line, publisher_country, product_category,
  dsp_group_name, clearvu_account, channel_id) is unique: 0 duplicate keys.
- No NULL business_line / product_category / connection_type / dsp_group_name; no negative
  revenue of note (90 rows, -0.09 USD total); impressions <= bids except 1,080 rows.
- BFM branch reconciles to `reporting_closing_bfm_demand` to the cent (Open Auction - BFM excl.
  Seedtag-named deals, PMP - Seedtag -> PMP CTV - O&O) for Jan-Apr and Jun-Aug 2026.
- Old-vs-new parity: after removing the External lines from `_old`, the new table is within
  +0.0% (2025 Jan-Apr, identical to the cent), +0.15..1.0% (May-Dec 2025), +0.5..0.6% (Jan-Jun 2026),
  +1.2% (Jul), +4.6% (Aug). The Aug uplift (+1.14M USD) matches the design change: Seedtag-named
  Open Auction - BFM rows (853k USD in Aug) now measured on the STX side plus dual-routed
  Select/DSPM no longer anti-joined (~252k USD).
- Known broken days of the old model are fixed: 2026-01-18 is no longer duplicated (1.166x enriched,
  in line with neighbours); 2026-08-29 is complete (old had 258k, new 858k); Aug 26-28 BFM present.
- All main business lines present on every day Jul 1 - Sep 7 2026.

## Status update 2026-09-14

- **The re-slice finished** on 2026-09-11 17:11 (through 2026-09-10). The table now holds
  11,220,558 rows / 618 dates / USD 530,958,740.
- **H3 RESOLVED** — 2026-04-12, 04-13 and 04-14 are present; the only gaps are 09-11..09-13.
- **H4 partially resolved** — the re-slice picked up the restated May 2026 closing figures, but
  the daily run still has no rolling re-load window, so it will recur.
- **Still open:** H1 (two writers, no scheduled daily), H2 (DSP dimension unusable pre-2025-05-19),
  and every Medium and Low item below.
- **Freshness at 2026-09-14:** max date 2026-09-10; 09-11 and 09-12 loadable now, 09-13 blocked on
  `reporting_closing_bfm_demand`, which reaches 09-12 only.

## High

**H1. Stale: max date 2026-09-07 while sources have 09-08, 09-09, 09-10.** The new loader's
`--daily` mode is not scheduled anywhere (manual run); the old dbt model `reporting_adex_demand`
is still an active task in DAG `adx_analytics_models` (de_composer_dags) writing the SAME table
with the OLD logic (External lines, closing BFM with anti-join). It has not written since the swap
(no rows dated after 09-07, no External lines), but if it runs it will append old-semantics days
into the new-semantics table. Decide one writer: disable the dbt task or point it at the new SQL,
and schedule `adex_demand_new.py --daily` after `curation.py`.

**H2. DSP dimension unusable before 2025-05-19.** 'DSP Not Found' = 60% of revenue Jan-Apr 2025,
31% in May 2025, ~2.5% afterwards (58.0M USD unresolved pre-2025-05-19; Reseller 99.99% unresolved,
Direct 16%). These rows are byte-identical to the old model's core rows (copied, not rebuilt);
the enriched-based fill (`adex_stx_2025.py`) targeted `_new` and its result is not what is in prod.
Any DSP cut over 2025 H1 (sofia skill default table) is wrong.

**H3. Missing / in-flight days.** [RESOLVED — all three loaded when the re-slice finished.]
2026-04-12, 04-13, 04-14 had no rows at audit time (deleted by the
running re-slice, insert pending). Verify after the loader finishes. Sources have all three days
(enriched 669k-718k USD/day).

**H4. Restatements do not propagate (day-sliced load).** May 2026 BFM lines are below the closing
source: Open Auction - BFM -81,854 USD, PMP CTV - O&O -27,621 USD, curation lines -33,821 USD, i.e.
the closing tables were restated after May was loaded. The running re-slice will fix May, but the
same will recur (the DAG comment documents that closing repricings do not propagate). Needs a
rolling re-load window (e.g. 45 days) in the daily run.

## Medium

**M1. Curation labels are as-of-latest, not as-of-date.** `max_by(business_line, date)` gives every
historical row of a deal its latest label: 'DSP marketplace - Migrated' carries 3.03M USD back to
2025-01-01 although migration started Aug 2026 (retro-labeling), and 76 deals in the lookup table
have two labels. Aug 2026 by label vs `reporting_curation_deals`: adex 'DSP Marketplace' 219k USD vs
386k EUR in curation; adex 'Migrated' 329k USD vs 171k EUR. Totals agree (~85%); the split does not.
Needs a per-deal migration_date (open item in the curation audit). Re-measured 2026-09-14 by
re-running the rebuild query and diffing against the table: on 2026-09-10 alone, 135 rows and
853.99 USD had already moved from 'DSP marketplace - Migrated' to 'DSP Marketplace' in the four
days since that day was loaded, with every other business line unchanged and totals identical.
The label is a run-time lookup, so the split drifts on its own.

**M2. STX-side curation revenue ~72% of the curation table's STX gross (Aug: 252k USD vs 301k EUR).**
Labels are only applied when product_type is P%/Curation%; curation deals delivering under O%
product types land in 'Open Auction - Seedtag'. Document or re-key the label on deal_id first.

**M3. `revenue_gross` NULL in 838,970 rows** (bids-only rows, 0 impressions, mostly Open Auction -
Seedtag). Sums are unaffected but `revenue_gross IS NULL` vs 0 is inconsistent; COALESCE to 0.
Plus 210,757 rows with revenue 0, imps 0, bids 0 (91k in Open Auction - BFM): pure noise rows.

**M4. Seedtag-named Beachfront Open Auction revenue moved to the STX side is untraceable.**
853k USD (Aug) excluded from 'Open Auction - BFM' is supposed to be measured in STX, but only
117k USD of 2026 STX revenue carries channel_id = 'Beachfront' (all 'DSP Not Found'); the rest is
under the buyer's channel with no source marker. Add a source/supply dimension or accept that the
BFM total is not reconstructable from this table.

## Low

- `clearvu_account` comment says "Select - BFM only" but it is populated on 'Curation 3rd Party'
  (466k rows) and 'DSP Marketplace' (608 rows) after relabeling; the STX branch also derives it
  from deal names (MultiLocal, Mavern, Onyx, BidFoundry).
- `publisher_country` NULL in 5,135 rows (Jan-May 2025 only, 50k USD) though the source defaults
  to 'XX'; 'ZZ' 845k USD; mixed ISO-2 and rollup buckets (BE and BNL coexist, documented).
- `channel_id` = 'Other' 144,732 rows / 46.7k USD.
- Sept 2026 'PMP - Curation' fallback label exists only 15 dates / 108 USD (fine, fallback).
- `reporting_closing_bfm_demand.seedtag_migrated` is NULL or '' everywhere: unusable for the
  migration split (upstream).
- 'DSP Not Found' still 2.5-3.3% of monthly revenue from mid-2025 (Rubicon, LoopMe, Pubmatic,
  SmartAdServer, Sovrn reseller channels): 11.6M USD since 2025-05-19.
