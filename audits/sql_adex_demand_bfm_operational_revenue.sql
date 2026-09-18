-- =====================================================================
-- reporting_adex_demand — restate the Beachfront lines to Beachfront's
-- OPERATIONAL (unadjusted) revenue, without re-running the loader.
--
-- WHAT CHANGES
--   The BFM branch took its revenue from reporting_closing_bfm_demand.revenue,
--   which is net of the Select fee adjustments and the pro-rated Ent Aggregator
--   subtraction. The operational figure is reporting_bfm_demand.revenue_gross.
--   Everything else stays as it is: the seat-resolved dsp, channel, mapped
--   country, product category and connection type are derived in closing and are
--   not reproducible from reporting_bfm_demand (raw advertiser, country NAMES,
--   media_type). So only the VALUE moves. Gross runs ~6% above net.
--
-- WHY A DELTA AND NOT A REPLACEMENT — read before editing
--   reporting_adex_demand has no deal_id: its grain is
--     (date, dsp_group_name, connection_type, business_line, product_category,
--      publisher_country, clearvu_account, channel_id)
--   and a single row can be the SUM of the STX branch and the BFM branch — the
--   curation business lines ('DSP Marketplace', 'Curation 3rd Party', …) receive
--   rows from both. Overwriting such a row with the BFM figure would silently
--   delete the Seedtag-side revenue in it. So this computes, per grain key, how
--   much the BFM portion MOVES (new gross − old net) and adds that. Keys with no
--   BFM contribution are untouched; mixed keys keep their STX part intact.
--
-- ALTERNATIVE, AND WHEN TO PREFER IT
--   Re-running the loader recomputes instead of patching, so the table cannot
--   drift from the model:
--     cd bf_automations && : > adex_demand_new.log
--     python adex_demand_new.py 2025-01-01 2026-09-16      # ~2.5–3 h, resumable
--   Use this MERGE when you want a month restated in seconds. Use the loader for
--   full history, or after any other change to the model.
--
-- HOW TO RUN
--   Set the window on the two <<WINDOW>> lines (both CTEs must cover the same
--   days). Run step 1 first — it shows what would move and must be run with your
--   own token, since big_query_bdb is not visible to the service user. Then
--   step 2. Then the checks in step 3.
--
-- Written 2026-09-17 against the loader at bf_automations@a8a110f.
-- =====================================================================

-- ── the restated BFM branch, at the adex grain ───────────────────────
-- Identical to the loader's BFM branch (same mapping, same business-line
-- relabelling, same dsp/channel cleanup), carrying BOTH values so the delta is
-- explicit. Reused by step 1 and step 2 — keep them in sync.
WITH curation_bl AS (
    SELECT lower(deal_id) AS deal_id_lc,
           max_by(business_line, date) FILTER (WHERE origin = 'BFM') AS bl_bfm
    FROM st_datalakehouse.analytics.reporting_curation_deals
    GROUP BY 1
),
bfm_map AS (
    SELECT advertiser_key,
           max(dsp_label)     AS dsp_label,
           max(channel_label) AS channel_label
    FROM st_datalakehouse.analytics.reporting_dsp_and_channel_mappings
    GROUP BY 1
),
-- Beachfront's operational gross per deal-day. Verified 2026-09-17: every
-- closing key matches a bfm key, so no row loses revenue; 252 bfm-only deal-days
-- ($27k over Sept) have no closing counterpart and stay out, as they do today.
bfm_gross AS (
    SELECT date, deal_id, ad_name AS deal_name, sum(revenue_gross) AS gross
    FROM st_datalakehouse.analytics.reporting_bfm_demand
    WHERE business_line IN ('Open Auction - BFM', 'PMP - Seedtag',
                            'Select - BFM', 'DSP Marketplace - BFM')
      AND date >= DATE '2026-09-01'   -- <<WINDOW>>
      AND date <  DATE '2026-09-17'   -- <<WINDOW>>
    GROUP BY 1, 2, 3
),
bfm_rows AS (
    SELECT
        a.date,
        COALESCE(bfm_m.dsp_label, a.dsp_group_name) AS raw_dsp,
        CASE WHEN a.business_line = 'Select - BFM' THEN a.clearvu_account END AS clearvu_account,
        COALESCE(bfm_m.channel_label, a.channel_id) AS channel_id_raw,
        CASE
            WHEN a.business_line = 'PMP - Seedtag' THEN 'PMP CTV - O&O'
            WHEN a.business_line IN ('Select - BFM', 'DSP Marketplace - BFM')
                THEN COALESCE(cbl.bl_bfm,
                     CASE a.business_line WHEN 'Select - BFM' THEN 'Curation 3rd Party'
                                          ELSE 'DSP Marketplace' END)
            ELSE a.business_line
        END AS business_line,
        a.publisher_country,
        a.product_category,
        a.connection_type,
        a.revenue AS old_rev,                       -- closing, net of the adjustments
        -- the deal-day gross spread over this key's closing rows in proportion to
        -- their net, so the dsp/country/category split still follows closing while
        -- each deal-day totals Beachfront's gross. A key whose net sums to 0
        -- cannot be scaled → split equally. No bfm counterpart → keep closing.
        CASE
            WHEN g.gross IS NULL THEN a.revenue
            WHEN sum(a.revenue) OVER (PARTITION BY a.date, a.deal_id, a.deal_name) <> 0
                THEN g.gross * a.revenue
                     / sum(a.revenue) OVER (PARTITION BY a.date, a.deal_id, a.deal_name)
            ELSE g.gross / count(*) OVER (PARTITION BY a.date, a.deal_id, a.deal_name)
        END AS new_rev
    FROM st_datalakehouse.analytics.reporting_closing_bfm_demand a
    LEFT JOIN bfm_map bfm_m ON bfm_m.advertiser_key = a.dsp_group_name
    LEFT JOIN curation_bl cbl ON cbl.deal_id_lc = lower(a.deal_id)
    LEFT JOIN bfm_gross g
      ON  g.date      = a.date
      AND g.deal_id   = a.deal_id
      AND g.deal_name IS NOT DISTINCT FROM a.deal_name
    WHERE a.business_line IN ('Open Auction - BFM', 'PMP - Seedtag',
                              'Select - BFM', 'DSP Marketplace - BFM')
      -- Seedtag-named Open Auction deals arrive via the STX branch instead
      AND NOT (a.business_line = 'Open Auction - BFM'
               AND a.deal_name IN ('SEEDTAG DON''USE', 'Seedtag'))
      AND a.date >= DATE '2026-09-01'   -- <<WINDOW>>
      AND a.date <  DATE '2026-09-17'   -- <<WINDOW>>
),
-- the loader's dsp/channel cleanup, so these land on the SAME grain keys as the
-- rows already in the table
delta AS (
    SELECT
        date,
        CASE
            WHEN raw_dsp IS NULL OR raw_dsp IN ('', 'Null', '191919', 'ABC Mouse') OR raw_dsp LIKE '%_TV1' OR raw_dsp LIKE 'McDonald%'
                OR raw_dsp LIKE 'Wavemaker%' OR raw_dsp LIKE 'at&amp%' OR raw_dsp LIKE 'Alexandria%'
                OR raw_dsp LIKE 'Biden%' OR raw_dsp LIKE 'Mnet_bidder%' OR raw_dsp LIKE 'Tito%'
                THEN 'DSP Not Found'
            WHEN raw_dsp IN ('Blockboard DSP', 'Blockboard Inc. OpenRTB') THEN 'Blockboard DSP'
            WHEN raw_dsp = 'Bidswitch' THEN 'BidSwitch'
            WHEN raw_dsp = 'Deepintent' THEN 'DeepIntent'
            WHEN raw_dsp IN ('MarkArch DSP', 'Marketing Architects -RTB') THEN 'Marketing Architects'
            WHEN raw_dsp IN ('SmartAdServerORTB', 'SmartAdServerVideo') THEN 'Equativ Direct Demand'
            WHEN raw_dsp = 'AppNexus' THEN 'Xandr'
            WHEN raw_dsp = 'Epsilon (Conversant)' THEN 'Conversant'
            WHEN raw_dsp IN ('Moloco, Inc RTB', 'molocoads DSP') THEN 'Molocoads DSP'
            ELSE raw_dsp
        END AS dsp_group_name,
        CASE
            WHEN (CASE WHEN raw_dsp = 'Epsilon (Conversant)' THEN 'Conversant' ELSE raw_dsp END) = 'Conversant'
             AND (CASE WHEN channel_id_raw = 'Deepintent' THEN 'DeepIntent' ELSE channel_id_raw END) = 'Conversant'
                THEN 'Direct'
            ELSE COALESCE(connection_type, 'Reseller')
        END AS connection_type,
        business_line,
        product_category,
        publisher_country,
        clearvu_account,
        CASE
            WHEN channel_id_raw IS NULL OR channel_id_raw IN ('', 'Null', 'ABC Mouse', '191919') OR channel_id_raw LIKE '%_TV1' OR channel_id_raw LIKE 'McDonald%'
                OR channel_id_raw LIKE 'Wavemaker%' OR channel_id_raw LIKE 'at&amp%' OR channel_id_raw LIKE 'Alexandria%'
                OR channel_id_raw LIKE 'Biden%' OR channel_id_raw LIKE 'Mnet_bidder%' OR channel_id_raw LIKE 'Tito%'
                THEN 'Other'
            WHEN channel_id_raw IN ('Blockboard DSP', 'Blockboard Inc. OpenRTB') THEN 'Blockboard DSP'
            WHEN channel_id_raw = 'Deepintent' THEN 'DeepIntent'
            ELSE channel_id_raw
        END AS channel_id,
        round(sum(new_rev) - sum(old_rev), 6) AS delta_rev,
        round(sum(old_rev), 2) AS old_rev,
        round(sum(new_rev), 2) AS new_rev
    FROM bfm_rows
    GROUP BY 1, 2, 3, 4, 5, 6, 7, 8
)

-- ── 1. DRY RUN — what would move, before changing anything ───────────
SELECT business_line,
       count(*)                       AS grain_keys,
       round(sum(old_rev), 2)         AS revenue_now_net,
       round(sum(new_rev), 2)         AS revenue_after_gross,
       round(sum(delta_rev), 2)       AS delta,
       round(100.0 * sum(delta_rev) / nullif(sum(old_rev), 0), 2) AS pct
FROM delta
GROUP BY 1
ORDER BY delta DESC;

-- Also confirm every key exists in the target — a key here with no row in the
-- table means the loader and this query disagree, and that delta would be lost.
-- Expect zero rows back.
-- SELECT d.* FROM delta d
-- LEFT JOIN st_datalakehouse.analytics.reporting_adex_demand t
--   ON  t.date = d.date AND t.dsp_group_name = d.dsp_group_name
--   AND t.connection_type = d.connection_type AND t.business_line = d.business_line
--   AND t.product_category IS NOT DISTINCT FROM d.product_category
--   AND t.publisher_country IS NOT DISTINCT FROM d.publisher_country
--   AND t.clearvu_account IS NOT DISTINCT FROM d.clearvu_account
--   AND t.channel_id IS NOT DISTINCT FROM d.channel_id
-- WHERE t.date IS NULL;


-- ── 2. THE MERGE — re-declare the CTEs, then apply the delta ─────────
-- Trino has no statement-level WITH for MERGE, so paste the whole CTE block
-- above in place of <<CTES>> below, keeping the same <<WINDOW>> dates.
--
-- MERGE INTO st_datalakehouse.analytics.reporting_adex_demand t
-- USING (
--     <<CTES>>
--     SELECT * FROM delta WHERE abs(delta_rev) > 0.005
-- ) s
-- ON  t.date            = s.date
-- AND t.dsp_group_name  = s.dsp_group_name
-- AND t.connection_type = s.connection_type
-- AND t.business_line   = s.business_line
-- AND t.product_category   IS NOT DISTINCT FROM s.product_category
-- AND t.publisher_country  IS NOT DISTINCT FROM s.publisher_country
-- AND t.clearvu_account    IS NOT DISTINCT FROM s.clearvu_account
-- AND t.channel_id         IS NOT DISTINCT FROM s.channel_id
-- WHEN MATCHED THEN UPDATE SET revenue_gross = t.revenue_gross + s.delta_rev;


-- ── 3. checks, after the merge ───────────────────────────────────────
-- 3a. the BFM-only lines must now equal Beachfront's gross over the deal-days
--     closing carries. Expect a difference of 0.00.
-- WITH b AS (SELECT date, deal_id, ad_name dn, sum(revenue_gross) gross
--            FROM st_datalakehouse.analytics.reporting_bfm_demand
--            WHERE date >= DATE '2026-09-01' AND date < DATE '2026-09-17'
--              AND business_line IN ('Open Auction - BFM','PMP - Seedtag','Select - BFM','DSP Marketplace - BFM')
--            GROUP BY 1,2,3),
--      c AS (SELECT DISTINCT date, deal_id, deal_name dn
--            FROM st_datalakehouse.analytics.reporting_closing_bfm_demand
--            WHERE date >= DATE '2026-09-01' AND date < DATE '2026-09-17'
--              AND business_line IN ('Open Auction - BFM','PMP - Seedtag')
--              AND NOT (business_line='Open Auction - BFM' AND deal_name IN ('SEEDTAG DON''USE','Seedtag')))
-- SELECT (SELECT round(sum(revenue_gross),2) FROM st_datalakehouse.analytics.reporting_adex_demand
--         WHERE date >= DATE '2026-09-01' AND date < DATE '2026-09-17'
--           AND business_line IN ('Open Auction - BFM','PMP CTV - O&O')) adex_oa_pmp,
--        (SELECT round(sum(b.gross),2) FROM b JOIN c ON c.date=b.date AND c.deal_id=b.deal_id AND c.dn IS NOT DISTINCT FROM b.dn) beachfront_gross;
--
-- 3b. nothing outside the Beachfront lines moved: the STX-only lines
--     (Open Auction - Seedtag, Direct Web - O&O, PMP Web - O&O) must be
--     byte-identical to before. Compare against a figure you noted first.
-- SELECT business_line, round(sum(revenue_gross),2) usd
-- FROM st_datalakehouse.analytics.reporting_adex_demand
-- WHERE date >= DATE '2026-09-01' AND date < DATE '2026-09-17'
-- GROUP BY 1 ORDER BY 2 DESC;
