-- Deals daily evolution — STX (Seedtag delivery, EUR) + BFM (Beachfront, USD).
-- Window: full history (no date filter; only today is excluded so the last
-- day is always closed).
WITH sf AS (
  SELECT
    deal_id,
    arbitrary(deal_name) AS sf_deal_name,
    brand,
    agency_group_name,
    agency_short_name AS agency,
    dsp,
    dsp_seat_id AS seat_id,
    country_served,
    country_sold,
    owner,
    am_csm,
    CASE
      -- is_ctv is boolean in the current table (was varchar 'true' when first
      -- validated); cast keeps it working either way.
      WHEN cast(is_ctv as varchar) = 'true' then 'CTV'
      ELSE 'Web'
    END AS inventory_type,
    format,
    -- curator_margin_value viene en porcentaje (80-100 en la tabla hoy) → /100
    -- para dejarlo como fraccion 0-1, que es lo que esperan las formulas
    -- curator_margin_total * split y * (1 - split). Sin valor → 0 (todo a STX).
    -- OJO: sum() sobre las product lines del deal; hoy todas tienen 1 linea con
    -- valor, pero un deal multi-linea sumaria >1 — revisar si aparece el caso.
    coalesce(sum(curator_margin_value), 0) / 100.0 AS curator_margin_split,
    count(*) AS sf_product_lines
  FROM big_query_bdb.business.salesforce_curation_product_lines
  WHERE deal_id IS NOT NULL
  GROUP BY 1, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13
),
dcm AS (
  SELECT
    date(date_hour) AS date
    , deal_id
    , deal_name AS dcm_deal_name
    , sum(requests) as requests
    , sum(bids) as bids
    , sum(wins) as wins
    , sum(ssp_hb_connector_win) as hb_connector_wins
    , sum(ssp_hb_inserts) as hb_inserts
    , sum(impressions) as impressions
  FROM st_datalakehouse.ad_exchange.deal_channel_metrics_hourly
  WHERE date_hour < current_date  -- exclude today (partial day)
    AND deal_name IS NOT NULL
  GROUP BY 1, 2, 3
),
del AS (
  SELECT
    dt,
    deal_id,
    salesforce_crm_id,
    currency,
    max(deal_name)                    AS del_deal_name,
    round(sum(gross_revenue_eur), 2)  AS gross_revenue_eur,
    count(DISTINCT dt)                AS active_days,
    sum(platform_fee_eur) as platform_fee_eur,
    sum(post_auction_discount_eur) as post_auction_discount_eur,
    sum(curator_margin_eur) as curator_margin_total_eur,
    sum(publisher_cost_eur) as pub_cost_eur
  FROM big_query_bdb.business.daily_curation_delivery_utc
  WHERE dt < current_date  -- exclude today (partial day)
  GROUP BY 1, 2, 3, 4
)

, stx as (
  SELECT
    del.dt as date,
    del.deal_id,
    del.salesforce_crm_id,
    del.currency,
    COALESCE(del.del_deal_name, sf.sf_deal_name, dcm.dcm_deal_name, '(unnamed)') AS deal_name,
    'Seedtag' as name_source,
    CASE
      -- Excepcion explicita (Barbara, 26-ago): el deal LEXUS (Team One) es
      -- Curation agency aunque no empiece por NEUROX ni este en SF.
      WHEN del.deal_id = '1b21334a-cf38-431e-9723-a45d0620dab9'             THEN 'Curation Agency'
      WHEN upper(COALESCE(del.del_deal_name, sf.sf_deal_name, dcm.dcm_deal_name, ''))
           LIKE '%TEST%'                                                    THEN 'excluida - test'
      WHEN upper(del.deal_id) LIKE 'NEUROX%' AND sf.agency LIKE '%Curator%' THEN 'Curation 3rd Party'
      WHEN upper(del.deal_id) LIKE 'NEUROX%' AND sf.agency IS NOT NULL      THEN 'Curation Agency'
      WHEN upper(del.deal_id) LIKE 'NEUROX%'                                THEN 'DSP Marketplace'
      ELSE 'DSP marketplace - Migrated'
    END AS business_line,
    sf.brand,
    sf.agency_group_name,
    sf.agency,
    sf.dsp,
    sf.seat_id,
    sf.country_served,
    sf.country_sold,
    sf.owner,
    sf.am_csm,
    sf.inventory_type,
    del.gross_revenue_eur            AS gross_revenue,
    del.pub_cost_eur                 AS pub_cost,
    del.curator_margin_total_eur     AS curator_margin_total,
    round(del.curator_margin_total_eur * (1 - sf.curator_margin_split), 2) AS curator_margin_stx,
    round(del.curator_margin_total_eur * sf.curator_margin_split, 2)       AS curator_margin_curator,
    -- coalesce: sin curator margin / discount el margen es gross - pub cost,
    -- no NULL (NULL se propagaria por la resta)
    round(del.gross_revenue_eur
          - coalesce(del.curator_margin_total_eur * sf.curator_margin_split, 0)
          - coalesce(del.post_auction_discount_eur, 0)
          - del.pub_cost_eur, 2)     AS margin,
    -- dcm es diario (join por dia): metricas sumables sin deduplicar
    dcm.requests,
    dcm.bids,
    dcm.wins,
    dcm.impressions,
    del.active_days,
    sf.sf_product_lines
  FROM del
  LEFT JOIN sf  ON del.deal_id = sf.deal_id
  LEFT JOIN dcm ON del.deal_id = dcm.deal_id
    AND dcm.date = del.dt
)

-- Beachfront usa otra convencion de nombres (Swap, dic-2025):
--   outgoing_bids = input bids: pujas que el DSP envia al SSP (el publisher envia
--   el request al SSP; el DSP responde con estos bid inputs). Top del funnel demand.
--   total_bids_placed = bids devueltos por DSPs; total_bids_rejected = rechazados
--   ads_served (= requests en supply) = placed - rejected ~ wins de subasta
--   Funnel: outgoing_bids > total_bids_placed > total_bids_rejected > ads_served > impressions
--   Win rate interno = ads_served/total_bids_placed; externo = impressions/ads_served.
--   OJO: estas columnas NO son comparables 1:1 con requests/bids/wins de dcm (SSP).
-- Una sola tabla: ads_served en demand == requests en supply (verificado
-- 26-ago-2026: 3545/3548 deal-dias identicos, diferencia total de 2 requests
-- sobre 75.9M, solo filas basura deal_name='0'). Sin join a supply, sin fan-out.
-- Grano: deal-dia-seat (seat_id es dimension); adomain/media_type via arbitrary().
, bfx as (
  select
    date
    , case
        when business_line = 'Select - BFM' then 'Curation 3rd Party'
        when business_line = 'DSP Marketplace - BFM' then 'DSP Marketplace'
      end as business_line
    , deal_id
    , cast(null as bigint) as salesforce_crm_id
    , 'USD' as currency
    , ad_name as deal_name
    , 'Beachfront' as name_source
    , arbitrary(adomain) as brand
    , clearvu_account as agency_group_name
    , clearvu_account as agency
    , advertiser as dsp
    , seat_id
    , cast(null as varchar) as country_served
    , cast(null as varchar) as country_sold
    , cast(null as varchar) as owner
    , cast(null as varchar) as am_csm
    , arbitrary(case when media_type = 'Video' then 'CTV' else 'Web' end) as inventory_type
    , sum(revenue_gross) as gross_revenue
    , sum(revenue) as pub_cost
    , cast(null as double) as curator_margin_total
    , cast(null as double) as curator_margin_stx
    , cast(null as double) as curator_margin_curator
    -- BFM no tiene curator margin ni post auction discount: margin = gross - pub cost
    , sum(revenue_gross) - sum(revenue) as margin
    , sum(ads_served) as requests
    , sum(outgoing_bids) as bids
    , sum(total_bids_placed) as wins
    , sum(impressions) as impressions
    , cast(null as bigint) as sf_product_lines
  from st_datalakehouse.analytics.reporting_bfm_demand
  where business_line in ('Select - BFM','DSP Marketplace - BFM')
    and date < current_date  -- exclude today (partial day)
  group by date, business_line, deal_id, ad_name, clearvu_account, advertiser, seat_id
)

, unioned as (
  SELECT 'STX' AS origin, date, deal_id, salesforce_crm_id, currency, deal_name,
         name_source, business_line, brand, agency_group_name, agency,
         dsp, seat_id, country_served, country_sold, owner, am_csm,
         inventory_type, gross_revenue, pub_cost,
         curator_margin_total, curator_margin_stx, curator_margin_curator, margin,
         requests, bids, wins, impressions,
         sf_product_lines
  FROM stx
  UNION ALL
  SELECT 'BFM', date, deal_id, salesforce_crm_id, currency, deal_name,
         name_source, business_line, brand, agency_group_name, agency,
         dsp, seat_id, country_served, country_sold, owner, am_csm,
         inventory_type, gross_revenue, pub_cost,
         curator_margin_total, curator_margin_stx, curator_margin_curator, margin,
         requests, bids, wins, impressions,
         sf_product_lines
  FROM bfx
)

SELECT
  u.*,
  -- Pct sobre el total combinado; ojo: mezcla EUR (STX) y USD (BFM).
  round(100 * gross_revenue / sum(gross_revenue) OVER (), 2) AS pct_of_qtd
FROM unioned u
ORDER BY gross_revenue DESC
