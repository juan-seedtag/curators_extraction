{{
    config(
        materialized="incremental",
        incremental_strategy="append",
        properties={
            "partitioning": "ARRAY['day(date)']",
        },
        pre_hook=[
            "{{ delete_partition_data(
                table_name=this,
                partition_columns=['date'],
                from_timestamp=var('from_timestamp'),
                to_timestamp=var('to_timestamp')
            ) }}            SET SESSION fault_tolerant_execution_task_memory = '4GB';
",
            "SET SESSION prefer_partial_aggregation = false",
        ]
    )
}}

-- The session properties above are load-bearing (CLUSTER_OUT_OF_MEMORY on the
-- 2026-07-28 first MTD load, ~57GB single-task peak):
--   * a gradual task_memory_growth_factor: without one, FTE task retries grow
--     memory ~3x per attempt (4GB -> 12 -> 36 -> 108GB) and overshoot the node
--     ceiling. This used to be pinned here at 1.2; it now comes from
--     profiles.yml at 1.1 for every model, which grows more slowly still, so
--     this model stays covered. Do not re-add it to the pre_hook -- a pre_hook
--     runs after the connection properties and would override that default.
--   * prefer_partial_aggregation = false: the wide GROUP BY over a month of
--     stg_ssp_responses_daily barely reduces rows pre-shuffle, so partial
--     aggregation just buffers the scan in memory. Mirrors
--     etl_ssp_responses_daily_enriched, which reads the same source at
--     similar grain.
--
-- UNIFIED logic (sep-2026, ported from the validated bf_automations loader
-- adex_demand_new.py that rebuilt the table):
--   * NO external/managed branch: the table covers O&O SSP + Beachfront only.
--   * Curation deals carry the DEAL-LEVEL business line from
--     reporting_curation_deals (Curation Agency / Curation 3rd Party /
--     Curation Test / DSP Marketplace / DSP marketplace - Migrated) instead of
--     the flat 'PMP - Curation' / 'Select - BFM' / 'DSP Marketplace - BFM'.
--   * The STX branch keeps Beachfront/SpringServe traffic; the BFM branch keeps
--     its curation lines IN FULL. Each branch reports what its own source
--     measured, so a migrated deal shows on both sides (sep-2026: the deal_name
--     anti-join was removed — it dropped real dual-routing revenue and mis-fired
--     on name collisions).
--   * 'DSP marketplace - Migrated' is a SEEDTAG-SIDE label only: it means
--     "revenue of a migrated deal measured by Seedtag (SSP responses)". Beachfront
--     rows of the same deal stay 'DSP Marketplace' because a migrated deal may
--     keep serving on Beachfront. The curation table already follows this rule
--     (its BFM-origin rows are never 'Migrated'), so each branch reads the label
--     of ITS OWN origin: bl_stx for the STX branch, bl_bfm for the BFM branch.
--     A single "latest label per deal" lookup flipped between the two labels
--     for deals serving on both sides the same day, and stamped 'Migrated' on
--     Beachfront rows back to 2025 (fixed 2026-09-14).

WITH curation_bl AS (
    -- deal-level labels from the curation table, ONE PER ORIGIN; LOWERCASED
    -- join key (some sources store deal ids in different case) and GROUP BY
    -- lower() so the lookup stays one row per deal (no join fan-out).
    --   bl_stx: latest label among the deal's Seedtag-side rows (origin STX)
    --   bl_bfm: latest label among the deal's Beachfront-side rows (origin BFM)
    SELECT lower(deal_id) AS deal_id_lc,
           max_by(business_line, date) FILTER (WHERE origin = 'STX') AS bl_stx,
           max_by(business_line, date) FILTER (WHERE origin = 'BFM') AS bl_bfm
    FROM {{ ref('reporting_curation_deals') }}
    GROUP BY 1
),

consolidated_raw AS (
    SELECT
        -- stg_ssp_responses_daily.date is timestamp(6); cast so the UNION (and
        -- the prod table's date column) stays a plain date.
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
    FROM {{ ref('stg_ssp_responses_daily') }} r
    LEFT JOIN {{ ref('bidder_dsp_mapping') }} b
        ON r.bidder_id = b.bidder_id AND r.channel_id = b.channel_name
    LEFT JOIN {{ ref('mapping_direct_dsp') }} seed
        ON lower(seed.channel_id) = lower(r.channel_id)
        AND (
            lower(r.channel_id) <> 'appnexus'
            OR seed.direct_dsp_name = b.dsp_group_name  -- prevents fan-out when multiple DSPs share a channel_id (MSAN + Xandr on AppNexus)
        )
    LEFT JOIN curation_bl cbl ON cbl.deal_id_lc = lower(r.deal_id)
    -- NO Beachfront/SpringServe exclusion: BFM traffic with a Seedtag leg is
    -- measured here (SSP responses) in this table.
    WHERE r.date >= timestamp '{{ var("from_timestamp") }}'
      AND r.date < timestamp '{{ var("to_timestamp") }}'
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
            a.dsp_group_name AS raw_dsp,
            CASE
                WHEN a.business_line = 'Select - BFM' THEN a.clearvu_account
                ELSE NULL
            END AS clearvu_account,
            a.channel_id,
            CASE
                WHEN a.business_line = 'PMP - Seedtag' THEN 'PMP CTV - O&O'
                -- BFM-native curation lines take the deal-level label of the
                -- BEACHFRONT side only (never 'DSP marketplace - Migrated': a
                -- migrated deal that still serves on Beachfront stays
                -- 'DSP Marketplace' here; its Seedtag revenue is 'Migrated' in
                -- the STX branch above)
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
        FROM {{ ref('reporting_closing_bfm_demand') }} a
        LEFT JOIN curation_bl cbl ON cbl.deal_id_lc = lower(a.deal_id)
        WHERE a.business_line IN ('Open Auction - BFM', 'PMP - Seedtag',
                                  'Select - BFM', 'DSP Marketplace - BFM')
          AND NOT (a.business_line = 'Open Auction - BFM'
                   AND a.deal_name IN ('SEEDTAG DON''USE', 'Seedtag'))
          -- Autobuying deals are NOT excluded anymore: they used to be carved
          -- out for the external/managed branch, which this unified model no
          -- longer has — excluding them here would drop them from the table
          -- entirely (decided sep-2026).
          AND a.date >= timestamp '{{ var("from_timestamp") }}'
          AND a.date < timestamp '{{ var("to_timestamp") }}'
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
