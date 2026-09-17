# Sofia curation re-check — 2026-09-15

Re-verification of three issues flagged while analysing June 2026 curation deals
with Sofia. All numbers from live Trino queries (MCP execute_sql), USD unless noted.

## 1. business_line taxonomy in reporting_adex_demand

Distinct values 2026-06-17..2026-09-14 (identical set over 2025-01-01..2026-09-14):

| business_line | first date | last date | rev USD 90d |
|---|---|---|---|
| Open Auction - Seedtag | 2025-01-01 | 2026-09-14 | 55,088,220 |
| Direct Web - O&O | 2025-01-01 | 2026-09-14 | 12,066,177 |
| PMP Web - O&O | 2025-01-01 | 2026-09-14 | 8,612,109 |
| Open Auction - BFM | 2025-01-01 | 2026-09-14 | 3,534,262 |
| PMP CTV - O&O | 2025-01-01 | 2026-09-14 | 2,218,868 |
| DSP Marketplace | 2025-01-01 | 2026-09-14 | 1,055,921 |
| DSP marketplace - Migrated | 2026-07-31 | 2026-09-14 | 323,546 |
| Curation 3rd Party | 2025-01-01 | 2026-09-14 | 281,237 |
| Curation Agency | 2026-05-27 | 2026-09-14 | 153,827 |
| Curation Test | 2026-02-19 | 2026-09-13 | 2,425 |

No External/Managed lines and no `PMP - Curation` / `Select - BFM` /
`DSP Marketplace - BFM` anywhere in history (table rebuilt 2026-09-10 without the
external branch). The flag missed `DSP marketplace - Migrated` (lowercase m).

Sofia docs updated (analytics-ai/agents/sofia, uncommitted): business-context.md
(10-line table, perimeters OMP 2 / PMP 2 / Curation 5 / Direct 1 / NeuroX 9, change
note), data-sources.md (sources + caveats), guardrails.md, CLAUDE.md, README.md,
sql/deal_lookup.sql and consolidated_demand_base.sql headers. No tool or skill
hard-coded the old names.

## 2. Beachfront curation: base vs bfm_demand

| Month | adex line | adex | closing_bfm_demand.revenue | bfm_demand.revenue_gross | reseller_revenue |
|---|---|---|---|---|---|
| May | DSP Marketplace | 547,754 | 547,645 (DSPM-BFM) | 615,732 | 14,495 |
| May | Curation 3rd Party | 208,321 | 208,429 (Select-BFM) | 217,945 | 8,938 |
| Jun | DSP Marketplace | 389,958 | 389,958 | 406,562 | 7,160 |
| Jun | Curation 3rd Party | 123,716 | 123,208 | 129,137 | 5,941 |

adex == closing (residual ±0.05% = STX-side rows under the same label / per-origin
relabel of a deal). closing.revenue = bfm gross − reseller_revenue − pro-rated Ent
Aggregator (column comment). May DSPM Ent Aggregator ≈ 53.6k (8.7%) vs Jun ≈ 9.4k
(2.3%): that is why May's gap is larger and MoM is flattened. No rows dropped on
DSP/country/line mapping. Mapping Select-BFM→Curation 3rd Party,
DSPM-BFM→DSP Marketplace confirmed in sql/adex_demand_superset.sql (bl_bfm fallback).
Decision: documented caveat in Sofia; a basis change would be an owner decision.

## 3. reporting_curation_deals funnel counters

Definitions from sql/deals_daily.sql (this repo owns the table):
- BFM: requests = ads_served, bids = outgoing_bids, wins = total_bids_placed
  (Beachfront convention) → wins > requests and bids/request ≈ 1,900 are expected.
- STX: requests/bids/wins = deal_channel_metrics_hourly (SSP auction level).
- record_type = Salesforce curation product line; NULL by design for all BFM rows
  and for STX deals without a Salesforce match.
- dsp = 'BidSwitch': Beachfront Bidswitch seats not resolved by the seat mapping;
  bids but 0 imps / 0 revenue (May: 217 + 176 rows).
Verified May/Jun 2026: BFM gross_lc == bfm_demand.revenue_gross exactly.
Documented in Sofia data-sources.md as read-only reference; Sofia keeps using
sql/deal_lookup.sql for deals (no new template).
