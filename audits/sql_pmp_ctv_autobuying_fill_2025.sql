-- Adds the autobuying 'PMP - Seedtag' rows (closing) that the old model excluded from
-- PMP CTV - O&O for 2025-01-01..2025-05-18. Derived from adex_demand_new.REBUILD_SQL
-- (BFM branch only). Idempotency: run ONCE; a re-run would duplicate. Expected ~327k USD.
INSERT INTO st_datalakehouse.analytics.reporting_adex_demand (date, dsp_group_name, connection_type, business_line, product_category, publisher_country, clearvu_account, channel_id, revenue_gross, total_impressions, total_response_bids)
WITH curation_bl AS (
    -- deal-level label from the curation table; LOWERCASED join key (some
    -- sources store deal ids in different case) and GROUP BY lower() so the
    -- lookup stays one row per deal (no join fan-out).
    SELECT lower(deal_id) AS deal_id_lc, max_by(business_line, date) AS bl
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
                -- BFM-native curation lines take the deal-level curation label
                WHEN a.business_line IN ('Select - BFM', 'DSP Marketplace - BFM')
                    THEN COALESCE(cbl.bl,
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
        WHERE a.business_line = 'PMP - Seedtag'
          AND a.deal_id IN (SELECT dealid FROM st_datalakehouse.analytics.reporting_bfm_autobying_deals)
          AND NOT (a.business_line = 'Open Auction - BFM'
                   AND a.deal_name IN ('SEEDTAG DON''USE', 'Seedtag'))
          -- Autobuying deals are NOT excluded anymore: they used to be carved
          -- out for the external/managed branch, which this unified table no
          -- longer has — excluding them here would drop them entirely
          -- (decided sep-2026).
          AND a.date >= timestamp '2025-01-01 00:00:00'
          AND a.date < timestamp '2025-05-19 00:00:00'
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
