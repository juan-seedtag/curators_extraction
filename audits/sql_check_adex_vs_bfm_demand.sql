-- =====================================================================
-- CHECK: reporting_adex_demand  vs  reporting_bfm_demand (Beachfront)
--
-- Since the 2026-09-18 rebuild, adex's BFM branch reads reporting_bfm_demand
-- DIRECTLY — reporting_closing_bfm_demand is no longer involved at all. Both
-- the value (revenue_gross, unadjusted) and every dimension now come from
-- Beachfront's own table, so the relationship is a straight EQUALITY rather
-- than the old "gross on closing-carried keys + orphan net" invariant:
--
--     adex(Open Auction - BFM, PMP CTV - O&O)
--        ==  reporting_bfm_demand.revenue_gross   (same lines, same exclusions)
--
-- exactly, per day. Read the `diff` column; it must be ~0.00.
--
-- THE EXCLUSIONS MUST MATCH ON BOTH SIDES
--   adex applies closing's 10-advertiser Open Auction perimeter AND the
--   Seedtag-named deal exclusion. Drop the advertiser list from this query and
--   it reports a FALSE shortfall of $27k-77k a month — those advertisers are
--   precisely the rows the old closing-based LEFT JOIN used to discard.
--
-- ONLY TWO LINES ARE COMPARABLE
--   adex relabels the Beachfront business lines, and two of them end up mixed
--   with Seedtag-side rows:
--     Open Auction - BFM      <- 'Open Auction - BFM'      BFM only  OK
--     PMP CTV - O&O           <- 'PMP - Seedtag'           BFM only  OK
--     Curation 3rd Party      <- 'Select - BFM'            + STX rows  NO
--     DSP Marketplace         <- 'DSP Marketplace - BFM'   + STX rows  NO
--   The last two also receive Seedtag deals, so their adex total is NOT
--   Beachfront's figure and must not be compared here. For those, check the
--   deal level instead: reporting_curation_deals.gross_revenue_lc where
--   origin = 'BFM' equals reporting_bfm_demand.revenue_gross for Select + DSPM.
--
-- Set the window on the two <<WINDOW>> lines (`to` is EXCLUSIVE). Swap the
-- GROUP BY for per-day output when a month looks wrong — see the note at the end.
-- =====================================================================
WITH bfm AS (      -- Beachfront's own figures, exactly as adex filters them
    SELECT date, sum(revenue_gross) AS gross
    FROM st_datalakehouse.analytics.reporting_bfm_demand
    WHERE date >= DATE '2025-01-01' AND date < DATE '2026-09-18'   -- <<WINDOW>>
      AND business_line IN ('Open Auction - BFM', 'PMP - Seedtag')
      -- closing's Open Auction perimeter (COALESCE: NOT(NULL) would drop the row)
      AND NOT (business_line = 'Open Auction - BFM'
               AND COALESCE(advertiser, '') IN ('TrueX', 'FreeWheel', 'LowBrow Customs',
                   'SuperAwesome', 'tankee', 'NA', 'PlayWire', 'Initiative', 'Xandr', 'sky media'))
      -- Seedtag-named Open Auction deals are counted on the Seedtag side instead
      AND NOT (business_line = 'Open Auction - BFM'
               AND ad_name IN ('SEEDTAG DON''USE', 'Seedtag'))
    GROUP BY 1
),
adex AS (
    SELECT date, sum(revenue_gross) AS adex
    FROM st_datalakehouse.analytics.reporting_adex_demand
    WHERE date >= DATE '2025-01-01' AND date < DATE '2026-09-18'   -- <<WINDOW>>
      AND business_line IN ('Open Auction - BFM', 'PMP CTV - O&O')
    GROUP BY 1
)
SELECT
    CAST(date_trunc('month', coalesce(a.date, b.date)) AS date)  AS period,
    count(*)                                                     AS days,
    round(sum(b.gross), 2)                                       AS beachfront_gross,
    round(sum(a.adex), 2)                                        AS adex,
    round(sum(a.adex) - sum(b.gross), 2)                         AS diff,
    count_if(abs(coalesce(a.adex, 0) - coalesce(b.gross, 0))
             > greatest(0.0005 * coalesce(b.gross, 1), 0.5))      AS days_off,
    CASE WHEN count_if(abs(coalesce(a.adex, 0) - coalesce(b.gross, 0))
                       > greatest(0.0005 * coalesce(b.gross, 1), 0.5)) = 0
         THEN 'OK' ELSE 'CHECK' END                               AS verdict
FROM adex a
FULL OUTER JOIN bfm b ON b.date = a.date
GROUP BY 1
ORDER BY 1;

-- PER-DAY: replace the date_trunc('month', …) expression with
-- coalesce(a.date, b.date), and add
--   HAVING abs(sum(a.adex) - sum(b.gross)) > 0.5
-- to list only the days that fail.
