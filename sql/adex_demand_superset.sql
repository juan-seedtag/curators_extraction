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
--   Two different queries can write st_datalakehouse.analytics.reporting_adex_demand:
--     * THIS one (new architecture), run by adex_demand_new.py. It is what
--       produced the data in the table today.
--     * The OLD dbt model `reporting_adex_demand` in de_dbt_lakehouse, still an
--       active task in the `adx_analytics_models` Airflow DAG. Different logic:
--       it has the External/Managed branch and the closing-BFM anti-join.
--   It has not fired since the swap on 2026-09-10, but it is not disabled.
--   You asked for the new one; this is it.
--
-- WHAT THE NEW ARCHITECTURE CHANGES (rebuilt from scratch 2026-09-18)
--   * No External/Managed branch at all.
--   * THREE branches with a hard cutover at 2025-06-01 so no day is counted
--     twice: STX modern (stg_ssp_responses_daily, from 2025-06-01), STX legacy
--     (ad_exchange.ssp_events_daily_simplified, before 2025-06-01 — the modern
--     source has no history before 2025-05-19), and BFM (all dates).
--   * BFM reads reporting_bfm_demand DIRECTLY, no longer
--     reporting_closing_bfm_demand: closing's `revenue` is net of the Select
--     fee adjustments, the pro-rated Ent Aggregator subtraction and the
--     audience-segment cost, and adex reports what Beachfront reports. Every
--     dimension closing used to supply is derived here with closing's OWN
--     logic (seat-resolved dsp, mapping labels, ISO-2 country, Display/CTV,
--     the PubMatic ST / BidSwitch-seat connection rule).
--   * Open Auction - BFM applies BOTH closing's 10-advertiser perimeter AND
--     the Seedtag-named deal exclusion (those arrive via STX).
--   * 'Select - BFM' and 'DSP Marketplace - BFM' are kept IN FULL (no
--     deal-name anti-join), so migrated deals deliberately appear on both
--     sides.
--   * business_line for P%/Curation% rows is a deal-level lookup against
--     reporting_curation_deals.
--   * inventory_type is part of the grain (added 2026-09-18): taken from the
--     source on both STX branches, derived from media_type on BFM
--     (Display -> Display, everything else -> CTV). The legacy STX source
--     never populated it, so it is NULL for every pre-June-2025 STX row.
--
-- SINGLE DAY vs WINDOW
--   The loader runs this one day at a time. Here the six lines marked
--   <<WINDOW>> are a RELATIVE 10-day window, `current_date - interval '10' day`
--   to `current_date` (exclusive, so it ends on the last closed day). Relative
--   on purpose: a hardcoded range silently goes stale. Widen or pin them if you
--   need a different period — but note the STX legacy branch only ever returns
--   rows for a window reaching before 2025-06-01.
-- =====================================================================
WITH curation_bl AS (
    -- deal-level label from the curation table; LOWERCASED join key (some
    -- sources store deal ids in different case) and GROUP BY lower() so the
    -- lookup stays one row per deal (no join fan-out).
    SELECT lower(deal_id) AS deal_id_lc,
        max_by(business_line, date) FILTER (WHERE origin = 'STX') AS bl_stx,
        max_by(business_line, date) FILTER (WHERE origin = 'BFM') AS bl_bfm
    FROM st_datalakehouse.analytics.reporting_curation_deals
    GROUP BY 1
),

-- ONE row per advertiser_key: the mapping table has duplicate keys and a raw
-- join would fan out and double-count revenue. `channel_label IS NOT NULL`
-- mirrors the closing model, whose labels this branch now reproduces.
bfm_map AS (
    SELECT advertiser_key,
           max(dsp_label)     AS dsp_label,
           max(channel_label) AS channel_label
    FROM st_datalakehouse.analytics.reporting_dsp_and_channel_mappings
    WHERE channel_label IS NOT NULL
    GROUP BY 1
),

-- Beachfront seat resolution, lifted from the closing model. Only the two
-- cases that actually rewrite the buyer are kept (BidSwitch seats carry the
-- real DSP in seat_name; a Trade Desk WMT/Walmart seat is Walmart), and
-- `seat_id <> seat_name` drops the rows where the seat adds nothing.
seat_names AS (
    SELECT DISTINCT seat_id, seat_name, advertiser
    FROM st_datalakehouse.analytics.reporting_beachfront_seat_name
    WHERE (advertiser = 'Bidswitch'
           OR (advertiser = 'The Trade Desk'
               AND (seat_name LIKE '%WMT%' OR seat_name LIKE '%Walmart%')))
      AND seat_id <> seat_name
),

-- Beachfront reports country NAMES ('United States'); adex and the STX branch
-- use ISO-2. mapping_region.country_name is lowercase, hence the lower() join.
-- Resolves 99.999% of Beachfront revenue; the remainder falls back to 'ZZ'.
country_map AS (
    SELECT country_name, max(country) AS iso
    FROM st_datalakehouse.analytics.mapping_region
    WHERE country_name IS NOT NULL
    GROUP BY 1
),

consolidated_raw AS (
    -- ---------- STX branch, modern: O&O SSP responses (from 2025-06-01) ----------
    SELECT
        CAST(r.date AS date) AS date,
        COALESCE(seed.direct_dsp_name, b.dsp_group_name, b.dsp_name) AS raw_dsp,
        -- Seedtag curators on O&O curation deals, identified by deal_name.
        -- Labels match the clearvu_account values emitted by the BFM branch so
        -- each curator stays a single account. Gated on Curation product_type
        -- so unrelated deal names can't leak in.
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
        r.inventory_type,
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
      -- this source starts 2025-05-19; the legacy branch below owns everything
      -- before 2025-06-01, so the two can never both emit a day.
      AND r.date >= timestamp '2025-06-01 00:00:00'
    GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9

    UNION ALL

    -- ---------- STX branch, legacy: ssp_events_daily_simplified (before 2025-06-01) ----------
    -- stg_ssp_responses_daily has no history before 2025-05-19, which left the
    -- whole Seedtag side of Jan-May 2025 missing (~88% of the table). This
    -- branch fills it from the older event table. It carries no deal_id, so
    -- there are no curation business lines before June 2025 and business_line
    -- comes from product_short_code alone; dsp/channel/connection are resolved
    -- from channel_id with the era's own mapping.
    SELECT
        s.date,
        CASE
            WHEN s.channel_id IN ('AdMixerBidswitch', 'Viant', 'NextRoll', 'StackAdapt', 'Nexxen',
                    'TheTradeDesk', 'Opera', 'Sportradar', 'RtbHouse', 'Beeswax', 'MediaForce',
                    'Stackadapt', 'Illumin', 'Madopi', 'Conversant', 'Deepintent', 'DeepIntent')
                THEN s.channel_id
            WHEN s.channel_id IN ('LoopMe', 'Adform', 'OneTag', 'AdYouLike') THEN 'DSP Not Found'
            WHEN s.channel_id IN ('DBM', 'GDN') THEN 'DV360'
            WHEN s.channel_id = 'AmazonBidswitch' THEN 'Amazon DSP'
            WHEN s.channel_id = 'Outbrain' THEN 'Outbrain/Teads'
            WHEN s.channel_id = 'StackAdaptDSP' THEN 'StackAdapt'
            WHEN s.product_short_code LIKE 'C%' THEN 'Xandr'
            ELSE 'DSP Not Found'
        END AS raw_dsp,
        CAST(NULL AS varchar) AS clearvu_account,
        s.channel_id,
        CASE
            WHEN s.product_short_code LIKE 'O%' THEN 'Open Auction - Seedtag'
            WHEN s.product_short_code LIKE 'P%' THEN 'PMP Web - O&O'
            WHEN s.product_short_code LIKE 'C%' THEN 'Direct Web - O&O'
        END AS business_line,
        s.publisher_country,
        CASE
            WHEN s.product_short_code = 'OMV' THEN 'Online Video'
            WHEN s.product_short_code = 'OMN' THEN 'Native'
            ELSE 'Display'
        END AS product_category,
        CASE
            WHEN s.channel_id = 'AppNexus'
                AND (s.product_short_code LIKE 'C%' OR s.channel_id IN ('Xandr', 'MSAN')) THEN 'Direct'
            WHEN s.channel_id IN ('Sovrn', 'Sharethrough', 'Rubicon', 'OpenX', 'Pubmatic',
                    'AppNexus', 'ImproveDigital', 'LoopMe', 'Adform', 'OneTag', 'AdYouLike') THEN 'Reseller'
            WHEN s.channel_id LIKE 'Smart%' THEN 'Reseller'
            WHEN s.channel_id IN ('DBM', 'GDN', 'Sportradar', 'StackAdapt', 'NextRoll',
                    'AdMixerBidswitch', 'Conversant', 'Madopi') THEN 'BidSwitch'
            WHEN s.channel_id IN ('RtbHouse', 'TheTradeDesk', 'Outbrain', 'StackAdaptDSP', 'Nexxen',
                    'Opera', 'NextRollPAAPI', 'Viant', 'Beeswax', 'Illumin', 'DeepIntent', 'Deepintent') THEN 'Direct'
        END AS connection_type,
        -- this source never populated inventory_type: NULL for every row of its
        -- 2025-01-01..2025-05-31 range. Passed through rather than invented.
        s.inventory_type,
        SUM(s.ssp_net_imp_paid) / 1000.0 AS revenue_gross,
        SUM(s.ssp_impressions) AS total_impressions,
        SUM(s.ssp_bids) AS total_response_bids
    FROM st_datalakehouse.ad_exchange.ssp_events_daily_simplified s
    WHERE s.date >= current_date - interval '10' day   -- <<WINDOW>>
      AND s.date < current_date   -- <<WINDOW>>
      AND s.date < DATE '2025-06-01'
    GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9

    UNION ALL

    -- ---------- BFM branch: Beachfront's own operational demand ----------
    -- Reads reporting_bfm_demand DIRECTLY (not reporting_closing_bfm_demand).
    -- revenue_gross is Beachfront's UNADJUSTED figure: closing's `revenue` is
    -- net of the Select fee adjustments, the pro-rated Ent Aggregator
    -- subtraction and the audience-segment cost, and adex reports what
    -- Beachfront reports. Every dimension closing used to supply (dsp via the
    -- seat table, channel, country, category, connection type) is derived here
    -- with closing's OWN logic, so nothing is lost by dropping the dependency.
    SELECT
        bfm.date,
        COALESCE(m.dsp_label, bfm.raw_dsp)     AS raw_dsp,
        bfm.clearvu_account,
        COALESCE(m.channel_label, bfm.raw_dsp) AS channel_id,
        bfm.business_line,
        bfm.publisher_country,
        bfm.product_category,
        bfm.connection_type,
        bfm.inventory_type,
        SUM(bfm.revenue_gross)       AS revenue_gross,
        SUM(bfm.total_impressions)   AS total_impressions,
        SUM(bfm.total_response_bids) AS total_response_bids
    FROM (
        SELECT
            d.date,
            CASE
                WHEN regexp_like(s.seat_name, '^[0-9]+$') THEN s.advertiser
                WHEN s.seat_id IS NOT NULL AND s.advertiser = 'The Trade Desk' THEN 'Walmart'
                WHEN s.seat_id IS NOT NULL AND s.advertiser = 'Bidswitch' THEN s.seat_name
                ELSE d.advertiser
            END AS raw_dsp,
            CASE WHEN d.business_line = 'Select - BFM' THEN d.clearvu_account END AS clearvu_account,
            -- BFM-native curation lines take the deal-level curation label
            CASE
                WHEN d.business_line = 'PMP - Seedtag' THEN 'PMP CTV - O&O'
                WHEN d.business_line IN ('Select - BFM', 'DSP Marketplace - BFM')
                    THEN COALESCE(cbl.bl_bfm,
                         CASE d.business_line WHEN 'Select - BFM' THEN 'Curation 3rd Party'
                                              ELSE 'DSP Marketplace' END)
                ELSE d.business_line
            END AS business_line,
            COALESCE(cm.iso, 'ZZ') AS publisher_country,
            CASE WHEN d.media_type = 'Display' THEN 'Display' ELSE 'CTV' END AS product_category,
            CASE
                WHEN d.advertiser = 'PubMatic ST' THEN 'Reseller'
                WHEN s.seat_id IS NOT NULL AND s.advertiser = 'Bidswitch' THEN 'BidSwitch'
                ELSE 'Direct'
            END AS connection_type,
            -- reporting_bfm_demand has no inventory_type; Beachfront is CTV
            -- except for its Display rows.
            CASE WHEN d.media_type = 'Display' THEN 'Display' ELSE 'CTV' END AS inventory_type,
            d.revenue_gross,
            d.impressions   AS total_impressions,
            d.outgoing_bids AS total_response_bids
        FROM st_datalakehouse.analytics.reporting_bfm_demand d
        LEFT JOIN seat_names s
            ON s.seat_id = d.seat_id AND s.advertiser = d.advertiser
        LEFT JOIN curation_bl cbl ON cbl.deal_id_lc = lower(d.deal_id)
        LEFT JOIN country_map cm ON cm.country_name = lower(d.country)
        WHERE d.business_line IN ('Open Auction - BFM', 'PMP - Seedtag',
                                  'Select - BFM', 'DSP Marketplace - BFM')
          -- Closing's own Open Auction perimeter. COALESCE is required: a bare
          -- IN () is NULL for a NULL advertiser and NOT(NULL) is NULL, which
          -- WHERE would silently drop. These advertisers are exactly the
          -- "bfm-only" keys the previous closing LEFT JOIN discarded.
          AND NOT (d.business_line = 'Open Auction - BFM'
                   AND COALESCE(d.advertiser, '') IN ('TrueX', 'FreeWheel', 'LowBrow Customs',
                       'SuperAwesome', 'tankee', 'NA', 'PlayWire', 'Initiative', 'Xandr', 'sky media'))
          -- the Seedtag-named Open Auction deals arrive via the STX branch
          AND NOT (d.business_line = 'Open Auction - BFM'
                   AND d.ad_name IN ('SEEDTAG DON''USE', 'Seedtag'))
          -- Autobuying deals are NOT excluded: they used to be carved out for
          -- the external/managed branch, which this unified table no longer
          -- has, so excluding them here would drop them entirely.
          AND d.date >= current_date - interval '10' day   -- <<WINDOW>>
          AND d.date < current_date   -- <<WINDOW>>
    ) bfm
    LEFT JOIN bfm_map m ON m.advertiser_key = bfm.raw_dsp
    GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9
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
    inventory_type,
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
        -- raw_dsp cleanup: junk buyer names -> 'DSP Not Found', plus the
        -- canonical renames. This is also where raw_dsp becomes dsp_group_name.
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
        inventory_type,
        revenue_gross,
        total_impressions,
        total_response_bids
    FROM consolidated_raw
)
GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9
