-- =====================================================================
-- CHECK: reporting_adex_demand  vs  reporting_bfm_demand (Beachfront)
--
-- Since 2026-09-17 adex reports Beachfront's OPERATIONAL (unadjusted) revenue:
-- the value comes from reporting_bfm_demand.revenue_gross while every resolved
-- dimension still comes from reporting_closing_bfm_demand. This is the query to
-- prove that still holds.
--
-- ONLY TWO LINES ARE COMPARABLE
--   adex relabels the Beachfront business lines, and two of them end up mixed
--   with Seedtag-side rows:
--     Open Auction - BFM      ← 'Open Auction - BFM'      BFM only  ✓ comparable
--     PMP CTV - O&O           ← 'PMP - Seedtag'           BFM only  ✓ comparable
--     Curation 3rd Party      ← 'Select - BFM'            + STX rows  ✗
--     DSP Marketplace         ← 'DSP Marketplace - BFM'   + STX rows  ✗
--   The last two also receive Seedtag deals, so their adex total is NOT
--   Beachfront's figure and must not be compared here. For those, check the
--   deal level instead: reporting_curation_deals.gross_revenue_lc where
--   origin='BFM' equals reporting_bfm_demand.revenue_gross for Select + DSPM
--   (verified 0.00 difference).
--
-- THE INVARIANT
--     adex  =  Beachfront gross on the deal-days closing carries
--            +  orphan net (closing deal-days Beachfront no longer reports,
--               which keep closing's own figure — the loader's
--               `WHEN g.gross IS NULL THEN a.revenue` branch)
--   so the column to read is `unexplained`. It must be ~0.00.
--   Two informational columns explain the rest of the gap:
--     bfm_only_gross  Beachfront deal-days closing has no row for → never in
--                     adex (≈$27k/month; by design, not a loss)
--     orphan_net      the fallback above (≈$0–4k/month, mostly sub-cent rows)
--
-- Set the window on the three <<WINDOW>> lines (`to` is EXCLUSIVE). Swap the
-- GROUP BY for per-day output when a month looks wrong — see the note at the end.
-- Run with your own token: the service user cannot see big_query_bdb, and while
-- this query does not need it, the loader it checks does.
-- =====================================================================
WITH bfm AS (      -- Beachfront's own figures, at deal-day grain
    SELECT date, deal_id, ad_name AS dn, sum(revenue_gross) AS gross
    FROM st_datalakehouse.analytics.reporting_bfm_demand
    WHERE date >= DATE '2025-01-01' AND date < DATE '2026-09-17'   -- <<WINDOW>>
      AND business_line IN ('Open Auction - BFM', 'PMP - Seedtag')
      -- Seedtag-named Open Auction deals are counted on the Seedtag side instead
      AND NOT (business_line = 'Open Auction - BFM'
               AND ad_name IN ('SEEDTAG DON''USE', 'Seedtag'))
    GROUP BY 1, 2, 3
),
clo AS (           -- the same slice of closing: this is what adex is built from
    SELECT date, deal_id, deal_name AS dn, sum(revenue) AS net
    FROM st_datalakehouse.analytics.reporting_closing_bfm_demand
    WHERE date >= DATE '2025-01-01' AND date < DATE '2026-09-17'   -- <<WINDOW>>
      AND business_line IN ('Open Auction - BFM', 'PMP - Seedtag')
      AND NOT (business_line = 'Open Auction - BFM'
               AND deal_name IN ('SEEDTAG DON''USE', 'Seedtag'))
    GROUP BY 1, 2, 3
),
per_day AS (       -- reconcile the two sources deal-day by deal-day
    SELECT coalesce(b.date, c.date)                              AS date,
           sum(CASE WHEN c.dn IS NOT NULL THEN b.gross END)      AS gross_in_scope,
           sum(CASE WHEN c.dn IS NULL     THEN b.gross END)      AS bfm_only_gross,
           count_if(c.dn IS NULL)                                AS bfm_only_keys,
           sum(CASE WHEN b.dn IS NULL     THEN c.net   END)      AS orphan_net,
           count_if(b.dn IS NULL)                                AS orphan_keys,
           sum(c.net)                                            AS closing_net
    FROM bfm b
    FULL OUTER JOIN clo c
      ON  c.date    = b.date
      AND c.deal_id = b.deal_id
      AND c.dn      IS NOT DISTINCT FROM b.dn
    GROUP BY 1
),
adex AS (
    SELECT date, sum(revenue_gross) AS adex
    FROM st_datalakehouse.analytics.reporting_adex_demand
    WHERE date >= DATE '2025-01-01' AND date < DATE '2026-09-17'   -- <<WINDOW>>
      AND business_line IN ('Open Auction - BFM', 'PMP CTV - O&O')
    GROUP BY 1
)
SELECT
    CAST(date_trunc('month', coalesce(a.date, p.date)) AS date)      AS period,
    count(*)                                                         AS days,
    round(sum(p.closing_net), 2)                                     AS closing_net,
    round(sum(p.gross_in_scope), 2)                                  AS beachfront_gross,
    round(sum(a.adex), 2)                                            AS adex,
    round(sum(coalesce(p.orphan_net, 0)), 2)                         AS orphan_net,
    round(sum(a.adex) - sum(p.gross_in_scope)
          - sum(coalesce(p.orphan_net, 0)), 2)                       AS unexplained,
    count_if(abs(coalesce(a.adex, 0) - coalesce(p.gross_in_scope, 0)
                 - coalesce(p.orphan_net, 0)) > greatest(0.0005 * coalesce(p.gross_in_scope, 1), 0.5))
                                                                     AS days_off,
    round(sum(coalesce(p.bfm_only_gross, 0)), 2)                     AS bfm_only_gross,
    sum(p.bfm_only_keys)                                             AS bfm_only_keys,
    CASE WHEN count_if(abs(coalesce(a.adex, 0) - coalesce(p.gross_in_scope, 0)
                           - coalesce(p.orphan_net, 0)) > greatest(0.0005 * coalesce(p.gross_in_scope, 1), 0.5)) = 0
         THEN 'OK' ELSE 'CHECK' END                                  AS verdict
FROM adex a
FULL OUTER JOIN per_day p ON p.date = a.date
GROUP BY 1
ORDER BY 1;

-- PER-DAY: replace the two date_trunc('month', …) expressions above with
-- coalesce(a.date, p.date), and add
--   HAVING abs(sum(a.adex) - sum(p.gross_in_scope) - sum(coalesce(p.orphan_net,0))) > 0.5
-- to list only the days that fail.
--
-- Last full run 2026-09-18 over 2025-01-01 -> 2026-09-17: 624 days / 21 months,
-- every month OK, unexplained 0.00 and days_off 0 throughout.
-- orphan_net is confined to 2025-01..2025-07 ($19,638 in total, peak $6,477 in May)
-- and is exactly 0.00 from 2025-08 onward. bfm_only_gross runs $27k-$128k a month.
