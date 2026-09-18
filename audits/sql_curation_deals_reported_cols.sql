-- =====================================================================
-- reporting_curation_deals — add the four "reported" money columns and
-- backfill them for ALL existing rows.
--
--   reported_gross_revenue_lc / _eur
--   reported_pub_cost_lc      / _eur
--
-- SEMANTICS
--   name_source = 'Seedtag'         -> same as the existing gross_revenue_* / pub_cost_*
--   name_source = 'Salesforce only' -> same rule (53 rows, no delivery, values are NULL/0)
--   name_source = 'Beachfront'      -> what Beachfront REPORTS, i.e.
--                                      reporting_closing_bfm_demand.revenue / .publisher_cost
--                                      (gross net of Select fees + the pro-rated Ent
--                                      Aggregator subtraction), which is why it runs
--                                      92-95% of the curation gross.
--
-- THE JOIN — verified 2026-09-16, do not simplify
--   The grains differ. reporting_curation_deals BFM rows are deal-day x seat;
--   reporting_closing_bfm_demand is far finer (x dsp x channel x country x
--   category x adomain). Over 2026-08-01..09-14:
--       curation BFM rows                      35,964
--       distinct (date, deal_id, deal_name)    15,425   <- NOT the grain
--       distinct (.. , seat_id)                35,962   <- the grain
--       closing rows                          879,123
--   Joining on (date, deal_id, deal_name) alone inflates reported revenue
--   4.01x ($2,322,630 against a true $578,908). The closing side MUST be
--   pre-aggregated to (date, deal_id, deal_name, seat_id) first.
--
--   seat_id IS NULL on 38,546 curation BFM rows, so the key needs a sentinel
--   (coalesce(seat_id,'∅')); with it, ALL 826,184 curation BFM rows match a
--   closing key (0 unmatched over full history).
--
--   452 keys carry two curation rows (906 rows; they differ by clearvu
--   account). A plain join credits the seat-day amount to both, overcounting
--   reported revenue by $3,376 and cost by $2,827 over full history — 0.019%
--   of $17.4M. Accepted: a pro-rata split was tried and dropped as not worth
--   a self-referencing MERGE source for that.
--
--   Validated against the closing truth for matched keys: revenue and cost
--   equal closing except for that 0.019% on the 452 keys. (Closing holds a
--   further $7,266 on 583 keys with no curation row at all — deals that are
--   not curation deals; they are simply not merged.)
--
-- ⚠ THIS BACKFILLS EXISTING ROWS ONLY. Tomorrow's slice will write NULLs
--   until the same logic is added to, in this order:
--     1. bf_automations/curation.py          (the loader that writes the table)
--     2. de_dbt_lakehouse reporting_curation_deals.sql
--     3. curators_extraction/sql/deals_daily.sql (+ _superset.sql)
--   Once the loader is updated, re-running it from 2025-01-01 is the CLEANER
--   backfill than this MERGE (it recomputes rather than patches, so the table
--   cannot drift from the model). This file exists for the fast path.
--
-- Run order: step 1, then 2 and 3 (either order), then the checks in step 4.
-- =====================================================================

-- ── 1. schema ────────────────────────────────────────────────────────
-- Iceberg appends the columns; existing rows read back NULL.
ALTER TABLE st_datalakehouse.analytics.reporting_curation_deals
  ADD COLUMN reported_gross_revenue_lc double;
ALTER TABLE st_datalakehouse.analytics.reporting_curation_deals
  ADD COLUMN reported_gross_revenue_eur double;
ALTER TABLE st_datalakehouse.analytics.reporting_curation_deals
  ADD COLUMN reported_pub_cost_lc double;
ALTER TABLE st_datalakehouse.analytics.reporting_curation_deals
  ADD COLUMN reported_pub_cost_eur double;


-- ── 2. Seedtag rows: copy the existing figures ───────────────────────
-- 21,728 rows (21,675 'Seedtag' + 53 'Salesforce only').
UPDATE st_datalakehouse.analytics.reporting_curation_deals
SET reported_gross_revenue_lc  = gross_revenue_lc,
    reported_gross_revenue_eur = gross_revenue_eur,
    reported_pub_cost_lc       = pub_cost_lc,
    reported_pub_cost_eur      = pub_cost_eur
WHERE name_source <> 'Beachfront';


-- ── 3. Beachfront rows: what closing reports ─────────────────────────
-- Closing is summed to the curation grain (deal-day x seat) first; the MERGE
-- source is unique on that key (826,313 keys, verified), as MERGE requires.
-- EUR: fx_rates_daily gives USD per 1 EUR (≈1.16). Closing figures are USD,
-- the target columns are EUR, so it is a DIVIDE (usd / rate) — the same
-- direction every existing BFM _eur column in this table already uses
-- (gross_revenue_lc / gross_revenue_eur = 1.1618 on 2026-09-01 = that day's
-- rate). Multiplying would be the EUR→USD direction and put reported_*_eur
-- ~35% above gross_revenue_eur.
MERGE INTO st_datalakehouse.analytics.reporting_curation_deals t
USING (
    SELECT k.date,
           k.deal_id,
           k.deal_name,
           coalesce(k.seat_id, '∅') AS seat_key,
           sum(k.revenue)           AS rep_rev,
           sum(k.publisher_cost)    AS rep_cost,
           max(r.rate)              AS rate          -- one USD-per-EUR rate per day
    FROM st_datalakehouse.analytics.reporting_closing_bfm_demand k
    -- a missing day would leave _eur NULL (the table covers every calendar day)
    LEFT JOIN (
        SELECT dt_utc, currency, rate
        FROM big_query_bdb.business.fx_rates_daily
        WHERE currency = 'USD'
          AND dt_utc >= DATE '2025-01-01'
    ) r ON r.dt_utc = k.date
    WHERE k.business_line IN ('Select - BFM', 'DSP Marketplace - BFM')
    GROUP BY 1, 2, 3, 4
) s
ON  t.name_source = 'Beachfront'
AND t.date        = s.date
AND t.deal_id     = s.deal_id
AND t.deal_name   = s.deal_name
AND coalesce(t.seat_id, '∅') = s.seat_key
WHEN MATCHED THEN UPDATE SET
    reported_gross_revenue_lc  = round(s.rep_rev, 2),
    reported_pub_cost_lc       = round(s.rep_cost, 2),
    reported_gross_revenue_eur = round(s.rep_rev  / s.rate, 2),
    reported_pub_cost_eur      = round(s.rep_cost / s.rate, 2);


-- ── 4. checks — run all four ─────────────────────────────────────────

-- 4a. coverage: no Beachfront row should be left without a reported figure,
--     and Seedtag rows must equal their source columns exactly.
SELECT name_source,
       count(*)                                             AS rows,
       count(reported_gross_revenue_lc)                     AS have_rep_rev,
       count(reported_pub_cost_lc)                          AS have_rep_cost,
       count_if(reported_gross_revenue_eur IS NULL
                AND reported_gross_revenue_lc IS NOT NULL)  AS lc_without_eur,
       count_if(name_source <> 'Beachfront'
                AND reported_gross_revenue_lc IS DISTINCT FROM gross_revenue_lc) AS stx_mismatch
FROM st_datalakehouse.analytics.reporting_curation_deals
GROUP BY 1 ORDER BY 1;

-- 4b. totals vs the closing truth (expect merged_rev ≈ truth + $3,376 — the accepted 0.019% from 4d — and nothing more)
SELECT round(sum(c.reported_gross_revenue_lc), 2) AS merged_rev,
       round(sum(c.reported_pub_cost_lc), 2)      AS merged_cost,
       (SELECT round(sum(k.rev), 2) FROM (
            SELECT date, deal_id, deal_name, coalesce(seat_id,'∅') sk, sum(revenue) rev
            FROM st_datalakehouse.analytics.reporting_closing_bfm_demand
            WHERE business_line IN ('Select - BFM','DSP Marketplace - BFM')
            GROUP BY 1,2,3,4) k
        JOIN (SELECT DISTINCT date, deal_id, deal_name, coalesce(seat_id,'∅') sk
              FROM st_datalakehouse.analytics.reporting_curation_deals
              WHERE name_source='Beachfront') w
          ON w.date=k.date AND w.deal_id=k.deal_id AND w.deal_name=k.deal_name AND w.sk=k.sk)
                                                   AS closing_truth_rev
FROM st_datalakehouse.analytics.reporting_curation_deals c
WHERE c.name_source = 'Beachfront';

-- 4c. monthly ratio — reported should sit at 92-95% of curation gross on the
--     BFM side (Select fee + Ent Aggregator netting). A month far outside that
--     band means the allocation went wrong for it.
SELECT date_trunc('month', date)                                     AS mth,
       round(sum(gross_revenue_lc), 0)                               AS curation_gross_usd,
       round(sum(reported_gross_revenue_lc), 0)                      AS reported_gross_usd,
       round(100.0 * sum(reported_gross_revenue_lc)
                   / nullif(sum(gross_revenue_lc), 0), 1)            AS pct,
       round(sum(reported_pub_cost_lc), 0)                           AS reported_cost_usd
FROM st_datalakehouse.analytics.reporting_curation_deals
WHERE name_source = 'Beachfront'
GROUP BY 1 ORDER BY 1;

-- 4d. the accepted overcount: keys with two curation rows (expect 452 keys,
--     906 rows, ~$3,376 revenue) — this is the only reason 4b is not exact.
SELECT count(*) AS multi_row_keys,
       sum(rows_in_key) AS rows_affected,
       round(sum(rep_rev * (rows_in_key - 1)), 0) AS overcount_rev_usd
FROM (
    SELECT c.date, c.deal_id, c.deal_name, coalesce(c.seat_id,'∅') sk,
           count(*) rows_in_key, max(c.reported_gross_revenue_lc) rep_rev
    FROM st_datalakehouse.analytics.reporting_curation_deals c
    WHERE c.name_source = 'Beachfront'
    GROUP BY 1,2,3,4 HAVING count(*) > 1
);
