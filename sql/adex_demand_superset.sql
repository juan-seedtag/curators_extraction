-- =====================================================================
-- reporting_adex_demand — REBUILD QUERY, NEW ARCHITECTURE (Superset / Trino)
--
-- Extracted verbatim from REBUILD_SQL in bf_automations/adex_demand_new.py,
-- with the loader's placeholders resolved:
--   {catalog}        -> st_datalakehouse
--   {curation_table} -> st_datalakehouse.analytics.reporting_curation_deals
--   {d}              -> a DATE WINDOW instead of a single day (see below)
--
-- WHICH QUERY IS THIS? — read this before trusting the output
--   Two different queries currently write st_datalakehouse.analytics.reporting_adex_demand:
--     * THIS one (new architecture), run by adex_demand_new.py. It is what
--       produced the data in the table today.
--     * The OLD dbt model `reporting_adex_demand` in de_dbt_lakehouse, still an
--       active task in the `adx_analytics_models` Airflow DAG. Different logic:
--       it has the External/Managed branch and the closing-BFM anti-join.
--   It has not fired since the swap on 2026-09-10, but it is not disabled.
--   You asked for the new one; this is it.
--
-- WHAT THE NEW ARCHITECTURE CHANGES
--   * No External/Managed branch at all.
--   * O&O/STX reads stg_ssp_responses_daily with NO Beachfront/SpringServe
--     exclusion, so BFM traffic with a Seedtag leg is measured here too.
--   * BFM reads reporting_closing_bfm_demand; 'Select - BFM' and
--     'DSP Marketplace - BFM' are kept IN FULL (no deal-name anti-join), so
--     migrated deals deliberately appear on both sides.
--   * business_line for P%/Curation% rows is a deal-level lookup against
--     reporting_curation_deals.
--
-- SINGLE DAY vs WINDOW
--   The loader runs one day at a time ({d}); here the four bounds tagged
--   <<WINDOW>> are a RELATIVE 10-day window, `current_date - interval '10' day`
--   to `current_date` (exclusive, so it ends on the last closed day). Relative
--   on purpose: a hardcoded range silently goes stale — this file sat at
--   2026-09-01..09-11 and was missing five loaded days when checked on
--   2026-09-17. Widening `date` is safe (it is the partition column and the two
--   branches are filtered independently), but keep it modest:
--   stg_ssp_responses_daily holds ~430M rows PER DAY. To pin an exact range for
--   a comparison, replace the four tagged lines with explicit timestamps.
--
-- COLUMNS RETURNED (loader INSERT order)
--   date, dsp_group_name, connection_type, business_line, product_category, publisher_country, clearvu_account, channel_id, revenue_gross, total_impressions, total_response_bids
--
-- If you only want the DATA rather than the definition, read the table:
--   SELECT * FROM st_datalakehouse.analytics.reporting_adex_demand
--   WHERE date >= DATE '2026-09-01'
-- =====================================================================

WITH curation_bl AS (
    -- deal-level label from the curation table; LOWERCASED join key (some
    -- sources store deal ids in different case) and GROUP BY lower() so the
    -- lookup stays one row per deal (no join fan-out).
    --   bl_stx: latest label among the deal's Seedtag-side rows (origin STX)
    --   bl_bfm: latest label among the deal's Beachfront-side rows (origin BFM)
    SELECT lower(deal_id) AS deal_id_lc,
           max_by(business_line, date) FILTER (WHERE origin = 'STX') AS bl_stx,
           max_by(business_line, date) FILTER (WHERE origin = 'BFM') AS bl_bfm
    FROM st_datalakehouse.analytics.reporting_curation_deals
    GROUP BY 1
),

-- ONE row per advertiser_key: the mapping table has duplicate keys and a raw
-- join would fan out and double-count revenue.
bfm_map AS (
    SELECT advertiser_key,
           max(dsp_label)     AS dsp_label,
           max(channel_label) AS channel_label
    FROM st_datalakehouse.analytics.reporting_dsp_and_channel_mappings
    GROUP BY 1
),

consolidated_raw AS (
    SELECT
        CAST(r.date AS date) AS date,
        COALESCE(seed.direct_dsp_name, b.dsp_group_name, b.dsp_name) AS raw_dsp,
        -- Seedtag curators on O&O curation deals, identified by deal_name.
        -- Labels match the clearvu_account values already emitted by the BFM
        -- branch so each curator stays a single account. Gated on Curation
        -- product_type so unrelated deal names can't leak in.
        CASE
            WHEN r.product_type LIKE 'Curation%' AND lower(r.deal_name) LIKE '%multilocal%' THEN 'MultiLocal'
            WHEN r.product_type LIKE 'Curation%' AND lower(r.deal_name) LIKE '%mavern%' THEN 'Mavern Media'
            WHEN r.product_type LIKE 'Curation%' AND lower(r.deal_name) LIKE '%onyx%' THEN 'Onyx'
            WHEN r.product_type LIKE 'Curation%' AND lower(r.deal_name) LIKE '%bidfoundry%' THEN 'BidFoundry'
        END AS clearvu_account,
        r.channel_id,
        -- business_line: O%/D% keep their product-type label; everything else
        -- (P%, Curation%) takes the deal-level curation label when the deal is
        -- known to reporting_curation_deals, else the product-type fallback.
        CASE
            WHEN r.product_type LIKE 'O%' THEN 'Open Auction - Seedtag'
            WHEN r.product_type LIKE 'D%' THEN 'Direct Web - O&O'
            ELSE COALESCE(cbl.bl_stx, cbl.bl_bfm,
                CASE
                    WHEN r.product_type LIKE 'Curation%' THEN 'PMP - Curation'
                    ELSE 'PMP Web - O&O'
                END)
        END AS business_line,
        r.publisher_country,
        CASE
            WHEN r.product_category = 'Video' THEN 'Online Video'
            WHEN r.product_category = 'Other' THEN 'Display'
            ELSE r.product_category
        END AS product_category,
        -- connection type from mapping_direct_dsp; AppNexus/MSAN and
        -- AppNexus/Xandr are Direct
        CASE
            WHEN r.channel_id = 'AppNexus'
                AND COALESCE(seed.direct_dsp_name, b.dsp_group_name, b.dsp_name) IN ('MSAN', 'Xandr')
                THEN 'Direct'
            ELSE COALESCE(seed.connection_type, 'Reseller')
        END AS connection_type,
        SUM(r.net_imp_paid) / 1000.0 AS revenue_gross,
        SUM(r.total_impressions) AS total_impressions,
        SUM(r.total_response_bids) AS total_response_bids
    FROM st_datalakehouse.analytics.stg_ssp_responses_daily r
    LEFT JOIN st_datalakehouse.analytics.bidder_dsp_mapping b
        ON r.bidder_id = b.bidder_id AND r.channel_id = b.channel_name
    LEFT JOIN st_datalakehouse.analytics.mapping_direct_dsp seed
        ON lower(seed.channel_id) = lower(r.channel_id)
        AND (
            lower(r.channel_id) <> 'appnexus'
            OR seed.direct_dsp_name = b.dsp_group_name  -- prevents fan-out when multiple DSPs share a channel_id (MSAN + Xandr on AppNexus)
        )
    LEFT JOIN curation_bl cbl ON cbl.deal_id_lc = lower(r.deal_id)
    -- NO Beachfront/SpringServe exclusion: BFM traffic with a Seedtag leg is
    -- measured here (SSP responses) in this table.
    WHERE r.date >= current_date - interval '10' day   -- <<WINDOW>>
      AND r.date < current_date   -- <<WINDOW>>
    GROUP BY 1, 2, 3, 4, 5, 6, 7, 8

    UNION ALL

    SELECT
        date,
        raw_dsp,
        clearvu_account,
        channel_id,
        business_line,
        publisher_country,
        product_category,
        connection_type,
        SUM(revenue_gross) AS revenue_gross,
        SUM(total_impressions) AS total_impressions,
        SUM(total_response_bids) AS total_response_bids
    FROM (
        SELECT
            a.date AS date,
            -- mapping label first, raw name as fallback (a.dsp_group_name is
            -- rarely NULL, so raw-first would make the mapping dead code)
            COALESCE(bfm_m.dsp_label, a.dsp_group_name)     AS raw_dsp,
            CASE
                WHEN a.business_line = 'Select - BFM' THEN a.clearvu_account
                ELSE NULL
            END AS clearvu_account,
            COALESCE(bfm_m.channel_label, a.channel_id)     AS channel_id,
            CASE
                WHEN a.business_line = 'PMP - Seedtag' THEN 'PMP CTV - O&O'
                -- BFM-native curation lines take the BEACHFRONT-side label only
                -- (never 'DSP marketplace - Migrated': that label is Seedtag-side)
                WHEN a.business_line IN ('Select - BFM', 'DSP Marketplace - BFM')
                    THEN COALESCE(cbl.bl_bfm,
                         CASE a.business_line WHEN 'Select - BFM' THEN 'Curation 3rd Party'
                                              ELSE 'DSP Marketplace' END)
                ELSE a.business_line
            END AS business_line,
            a.publisher_country,
            a.product_category,
            a.connection_type,
            a.revenue AS revenue_gross,
            a.total_impressions,
            a.total_response_bids
        FROM st_datalakehouse.analytics.reporting_closing_bfm_demand a
        LEFT JOIN bfm_map bfm_m
            ON bfm_m.advertiser_key = a.dsp_group_name
        LEFT JOIN curation_bl cbl ON cbl.deal_id_lc = lower(a.deal_id)
        WHERE a.business_line IN ('Open Auction - BFM', 'PMP - Seedtag',
                                  'Select - BFM', 'DSP Marketplace - BFM')
          AND NOT (a.business_line = 'Open Auction - BFM'
                   AND a.deal_name IN ('SEEDTAG DON''USE', 'Seedtag'))
          -- Autobuying deals are NOT excluded anymore: they used to be carved
          -- out for the external/managed branch, which this unified table no
          -- longer has — excluding them here would drop them entirely
          -- (decided sep-2026).
          AND a.date >= current_date - interval '10' day   -- <<WINDOW>>
          AND a.date < current_date   -- <<WINDOW>>
    ) bfm
    GROUP BY 1, 2, 3, 4, 5, 6, 7, 8
)

SELECT
    date,
    dsp_group_name,
    CASE
        WHEN dsp_group_name = 'Conversant' AND channel_id = 'Conversant' THEN 'Direct'
        ELSE connection_type
    END AS connection_type,
    business_line,
    product_category,
    publisher_country,
    clearvu_account,
    channel_id,
    SUM(revenue_gross) AS revenue_gross,
    SUM(total_impressions) AS total_impressions,
    SUM(total_response_bids) AS total_response_bids
FROM (
    SELECT
        date,
        COALESCE(connection_type, 'Reseller') AS connection_type,
        business_line,
        publisher_country,
        product_category,
        -- raw_dsp cleanup: junk buyer names → 'DSP Not Found', plus the
        -- canonical renames. This is also where raw_dsp becomes dsp_group_name
        -- (your draft referenced dsp_group_name here, which doesn't exist in
        -- consolidated_raw).
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
        clearvu_account,
        CASE
            WHEN channel_id IS NULL OR channel_id IN ('', 'Null', 'ABC Mouse', '191919') OR channel_id LIKE '%_TV1' OR channel_id LIKE 'McDonald%'
                OR channel_id LIKE 'Wavemaker%' OR channel_id LIKE 'at&amp%' OR channel_id LIKE 'Alexandria%'
                OR channel_id LIKE 'Biden%' OR channel_id LIKE 'Mnet_bidder%' OR channel_id LIKE 'Tito%'
                THEN 'Other'
            WHEN channel_id IN ('Blockboard DSP', 'Blockboard Inc. OpenRTB') THEN 'Blockboard DSP'
            WHEN channel_id = 'Deepintent' THEN 'DeepIntent'
            ELSE channel_id
        END AS channel_id,
        revenue_gross,
        total_impressions,
        total_response_bids
    FROM consolidated_raw
)
GROUP BY 1, 2, 3, 4, 5, 6, 7, 8
