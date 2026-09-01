-- Deals daily evolution — STX (Seedtag delivery) + BFM (Beachfront), all in USD.
-- STX source amounts are EUR and are converted with the monthly average rate
-- (analytics.currency_rates_monthly: rate_euros = EUR per 1 USD → divide).
-- Window: last 30 closed days (today excluded). Brand (adomain) is part of the
-- BFM grain, but only brands covering 95% of window revenue keep their name —
-- the tail is bucketed into '(other)' to keep the embedded dashboard small
-- (full-brand grain at 90d was 1.2M rows / ~155MB; this is ~130k / ~17MB).
WITH sf AS (
  SELECT
    deal_id,
    arbitrary(deal_name) AS sf_deal_name,
    brand,
    agency_group_name,
    agency_short_name AS agency,
    dsp,
    -- connection type derived from the SF dsp name; unmapped DSPs stay NULL
    case
      when dsp in ('StackAdapt','DV360') then 'BidSwitch'
      when dsp in ('TTD','Conersant/Epsilon','GroungTruth','RtbHouse','TheTradeDesk','Viant Technologies','Yahoo') then 'Direct'
    end as connection_type,
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
  GROUP BY 1, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14
),
dcm AS (
  SELECT
    date(date_hour) AS date
    , deal_id
    , deal_name AS dcm_deal_name
    -- deal-day grain KEPT: putting channel_id in the grain would duplicate the
    -- del money for every channel of a deal-day. arbitrary() picks one; a deal
    -- spanning several channels shows just one of them.
    , arbitrary(channel_id) AS channel_id
    , sum(requests) as requests
    , sum(bids) as bids
    , sum(wins) as wins
    , sum(ssp_hb_connector_win) as hb_connector_wins
    , sum(ssp_hb_inserts) as hb_inserts
    , sum(impressions) as impressions
  FROM st_datalakehouse.ad_exchange.deal_channel_metrics_hourly
  WHERE date_hour >= current_date - interval '30' day
    AND date_hour < current_date  -- closed days only
    AND deal_name IS NOT NULL
  GROUP BY 1, 2, 3
),
-- Curation agency name per deal, deduped to ONE row per deal — joining the
-- hourly curation table straight onto del would fan out the summed metrics.
cur AS (
  SELECT deal_id, arbitrary(agency_name) AS agency_name
  FROM st_datalakehouse.ad_exchange.curation_deal_channel_metrics_hourly
  WHERE agency_name IS NOT NULL
  GROUP BY 1
),
del AS (
  SELECT
    dt,
    deal_id,
    salesforce_crm_id,
    currency,
    max(deal_name)                    AS del_deal_name,
    round(sum(gross_revenue_eur), 2)  AS gross_revenue_eur,
    round(sum(net_revenue_eur), 2)    AS net_revenue_eur,
    count(DISTINCT dt)                AS active_days,
    sum(platform_fee_eur) as platform_fee_eur,
    sum(post_auction_discount_eur) as post_auction_discount_eur,
    sum(curator_margin_eur) as curator_margin_total_eur,
    sum(publisher_cost_eur) as pub_cost_eur
  FROM big_query_bdb.business.daily_curation_delivery_utc
  WHERE dt >= current_date - interval '30' day
    AND dt < current_date  -- closed days only
  GROUP BY 1, 2, 3, 4
)

, fx as (
  -- USD per 1 EUR, per month. fx_latest is the fallback for months the rates
  -- table doesn't cover yet (better a slightly stale rate than NULL revenue).
  SELECT date(date) AS month_start, 1 / rate_euros AS usd_per_eur
  FROM st_datalakehouse.analytics.currency_rates_monthly
  WHERE currency = 'USD'
)
, fx_latest as (
  SELECT usd_per_eur FROM fx ORDER BY month_start DESC LIMIT 1
)

, stx as (
  SELECT
    del.dt as date,
    del.deal_id,
    del.salesforce_crm_id,
    'USD' as currency,
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
    -- new Seedtag agency name from curation metrics; SF short name as fallback
    coalesce(cur.agency_name, sf.agency) as agency,
    dcm.channel_id,
    sf.dsp,
    sf.connection_type,
    sf.seat_id,
    sf.country_served,
    sf.country_sold,
    sf.owner,
    sf.am_csm,
    sf.inventory_type,
    sf.format,
    round(del.gross_revenue_eur * coalesce(fx.usd_per_eur, fx_latest.usd_per_eur), 2) AS platform_spend,
    round(del.net_revenue_eur * coalesce(fx.usd_per_eur, fx_latest.usd_per_eur), 2)   AS gross_revenue,
    round(del.pub_cost_eur * coalesce(fx.usd_per_eur, fx_latest.usd_per_eur), 2)      AS pub_cost,
    round(del.curator_margin_total_eur * coalesce(fx.usd_per_eur, fx_latest.usd_per_eur), 2) AS curator_margin_total,
    round(del.curator_margin_total_eur * (1 - sf.curator_margin_split)
          * coalesce(fx.usd_per_eur, fx_latest.usd_per_eur), 2) AS curator_margin_stx,
    round(del.curator_margin_total_eur * sf.curator_margin_split
          * coalesce(fx.usd_per_eur, fx_latest.usd_per_eur), 2) AS curator_margin_curator,
    -- coalesce: sin curator margin / discount el margen es gross - pub cost,
    -- no NULL (NULL se propagaria por la resta). El margen sigue partiendo del
    -- platform spend (gross_revenue_eur de la tabla), como antes.
    round((del.gross_revenue_eur
          - coalesce(del.curator_margin_total_eur * sf.curator_margin_split, 0)
          - coalesce(del.post_auction_discount_eur, 0)
          - del.pub_cost_eur) * coalesce(fx.usd_per_eur, fx_latest.usd_per_eur), 2) AS margin,
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
  LEFT JOIN cur ON del.deal_id = cur.deal_id
  LEFT JOIN fx  ON date_trunc('month', del.dt) = fx.month_start
  CROSS JOIN fx_latest
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
-- Grano: deal-dia-seat-brand-mediatype (brand con cola agrupada en '(other)').
, brand_keep as (
  -- Brands covering 95% of window revenue keep their name; the tail becomes
  -- '(other)'. Classified over the WHOLE window so a brand never flips between
  -- its name and '(other)' from one day to the next.
  select adomain
  from (
    select adomain, sum(revenue_gross) tot,
           sum(sum(revenue_gross)) over (order by sum(revenue_gross) desc
                                         rows unbounded preceding) as cum,
           sum(sum(revenue_gross)) over () as grand
    from st_datalakehouse.analytics.reporting_bfm_demand
    where business_line in ('Select - BFM','DSP Marketplace - BFM')
      and date >= current_date - interval '30' day
      and date < current_date
    group by adomain
  )
  where cum - tot < 0.95 * grand
)
, bfx as (
  select
    a.date
    , case
        when a.business_line = 'Select - BFM' then 'Curation 3rd Party'
        when a.business_line = 'DSP Marketplace - BFM' then 'DSP Marketplace'
      end as business_line
    , a.deal_id
    , cast(null as bigint) as salesforce_crm_id
    , 'USD' as currency
    , a.ad_name as deal_name
    , 'Beachfront' as name_source
    , case when bk.adomain is not null then a.adomain else '(other)' end as brand
    , a.clearvu_account as agency_group_name
    , a.clearvu_account as agency
    -- dsp via seat mapping when available: numeric seat names fall back to the
    -- advertiser; TTD Walmart seats → Walmart; Bidswitch seats → the seat name
    , case
        when regexp_like(s.seat_name, '^[0-9]+$') then s.advertiser
        when s.seat_id is not null and s.advertiser = 'The Trade Desk' then 'Walmart'
        when s.seat_id is not null and s.advertiser = 'Bidswitch' then s.seat_name
        else a.advertiser
      end as dsp
    , case
        when a.advertiser = 'PubMatic ST' then 'Reseller'
        when s.seat_id is not null and s.advertiser = 'Bidswitch' then 'BidSwitch'
        else 'Direct'
      end as connection_type
    , a.seat_id
    , cast(null as varchar) as country_served
    , cast(null as varchar) as country_sold
    , cast(null as varchar) as owner
    , cast(null as varchar) as am_csm
    , case when a.media_type = 'Video' then 'CTV' else 'Web' end as inventory_type
    , a.media_type as format
    , cast(0 as double) as platform_spend
    , sum(a.revenue_gross) as gross_revenue
    , sum(a.revenue) as pub_cost
    , cast(null as double) as curator_margin_total
    , cast(null as double) as curator_margin_stx
    , cast(null as double) as curator_margin_curator
    -- BFM no tiene curator margin ni post auction discount: margin = gross - pub cost
    , sum(a.revenue_gross) - sum(a.revenue) as margin
    , sum(a.ads_served) as requests
    , sum(a.outgoing_bids) as bids
    , sum(a.total_bids_placed) as wins
    , sum(a.impressions) as impressions
    , cast(null as bigint) as sf_product_lines
  from st_datalakehouse.analytics.reporting_bfm_demand a
  left join brand_keep bk on bk.adomain = a.adomain
  left join (
      -- ONE row per (seat_id, advertiser): distinct still fans out when a seat
      -- has several seat_names, so collapse with max()
      select seat_id, advertiser, max(seat_name) as seat_name
      from st_datalakehouse.analytics.reporting_beachfront_seat_name
      where (advertiser = 'Bidswitch'
             or (advertiser = 'The Trade Desk' and (seat_name like '%WMT%' or seat_name like '%Walmart%')))
        and seat_id <> seat_name
      group by 1, 2
    ) s
      on s.seat_id = a.seat_id and s.advertiser = a.advertiser
  where a.business_line in ('Select - BFM','DSP Marketplace - BFM')
    and a.date >= current_date - interval '30' day
    and a.date < current_date  -- closed days only
  group by 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20
)

, unioned as (
  SELECT 'STX' AS origin, date, deal_id, salesforce_crm_id, currency, deal_name,
         name_source, business_line, brand, agency_group_name, agency, channel_id,
         dsp, connection_type, seat_id, country_served, country_sold, owner, am_csm,
         inventory_type, format, platform_spend, gross_revenue, pub_cost,
         curator_margin_total, curator_margin_stx, curator_margin_curator, margin,
         requests, bids, wins, impressions,
         sf_product_lines
  FROM stx
  UNION ALL
  SELECT 'BFM', bfx.date, bfx.deal_id, bfx.salesforce_crm_id, bfx.currency, bfx.deal_name,
         bfx.name_source, bfx.business_line, bfx.brand, bfx.agency_group_name, bfx.agency,
         coalesce(m.channel_label, bfx.dsp) as channel_id,
         bfx.dsp, bfx.connection_type, bfx.seat_id, bfx.country_served, bfx.country_sold,
         bfx.owner, bfx.am_csm,
         bfx.inventory_type, bfx.format, bfx.platform_spend, bfx.gross_revenue, bfx.pub_cost,
         bfx.curator_margin_total, bfx.curator_margin_stx, bfx.curator_margin_curator, bfx.margin,
         bfx.requests, bfx.bids, bfx.wins, bfx.impressions,
         bfx.sf_product_lines
  FROM bfx
  left join (
    select advertiser_key, max(dsp_label) as dsp_label, max(channel_label) as channel_label
    from st_datalakehouse.analytics.reporting_dsp_and_channel_mappings
    where channel_label is not null
    group by 1
  ) m
    on bfx.dsp = m.advertiser_key
)

SELECT
  u.*,
  -- Pct sobre el total combinado (todo USD).
  round(100 * gross_revenue / sum(gross_revenue) OVER (), 2) AS pct_of_qtd
FROM unioned u
ORDER BY gross_revenue DESC
