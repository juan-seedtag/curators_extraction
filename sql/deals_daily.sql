-- Deals daily evolution — STX (Seedtag delivery) + BFM (Beachfront).
-- Metrics are carried in LOCAL CURRENCY (_lc: STX currency ∈ EUR/BRL/USD today,
-- BFM always USD) and converted to EUR (_eur) at the end via the DAILY rate
-- (business.fx_rates_daily: rate = units of the currency per 1 EUR → DIVIDE
-- lc / rate). Both _lc and _eur are returned so the
-- dashboard can toggle. gross_revenue = net_revenue in the source table (the
-- source's "gross_revenue" is really platform spend).
-- Window: since 2026-01-01 (today excluded), DAILY grain throughout — viable
-- once BFM adomain left the grain; the HTML embeds the payload gzipped.
-- BFM brand (adomain) removed for now — NULL until a curated mapping exists
-- (it multiplied the grain; see git history for the 95%-coverage version).
-- HEALTH VIEW: STX base = delivery ∪ curation-filtered SSP funnel ∪ Salesforce
-- (FULL OUTER). dcm is pre-filtered to deals known to SF or with 2026 delivery,
-- so no exchange noise enters while traffic-without-delivery days keep their
-- funnel metrics ("no bids"/"no requests" states). Salesforce-only deals show
-- once, dated on the last closed day. BFM zero-revenue rows come from the
-- source as-is. first_seen = first date the deal ever appeared (full history).
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
  WHERE date_hour >= date '2026-01-01'
    AND date_hour < current_date  -- closed days only
    AND deal_name IS NOT NULL
    -- dcm cubre TODO el exchange: solo deals de curation — conocidos en SF o
    -- con delivery este año. Cero identidades nuevas vs la poblacion del
    -- dashboard; solo recupera los dias con trafico sin delivery (no bids).
    AND (deal_id IN (SELECT deal_id FROM big_query_bdb.business.salesforce_curation_product_lines
                     WHERE deal_id IS NOT NULL)
         OR deal_id IN (SELECT DISTINCT deal_id FROM big_query_bdb.business.daily_curation_delivery_utc
                        WHERE dt >= date '2026-01-01'))
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
  -- Local-currency base: the table's gross_revenue is really platform spend;
  -- net_revenue is our gross revenue.
  SELECT
    dt,
    deal_id,
    salesforce_crm_id,
    currency,                          -- EUR / BRL / USD today
    max(deal_name)                    AS del_deal_name,
    round(sum(gross_revenue), 2)      AS platform_spend_lc,
    round(sum(net_revenue), 2)        AS gross_revenue_lc,
    count(DISTINCT dt)                AS active_days,
    sum(platform_fee)                 AS platform_fee_lc,
    sum(post_auction_discount)        AS post_auction_discount_lc,
    sum(curator_margin)               AS curator_margin_total_lc,
    sum(publisher_cost)               AS pub_cost_lc
  FROM big_query_bdb.business.daily_curation_delivery_utc
  WHERE dt >= date '2026-01-01'
    AND dt < current_date  -- closed days only
  GROUP BY 1, 2, 3, 4
)

-- STX = delivery FULL OUTER curation-filtered funnel FULL OUTER Salesforce:
-- every population keeps whatever fields its sources have; the dcm filter
-- guarantees no non-curation deal can enter.
, stx as (
  SELECT
    coalesce(del.dt, dcm.date, current_date - interval '1' day) as date,
    coalesce(del.deal_id, dcm.deal_id, sf.deal_id) as deal_id,
    del.salesforce_crm_id,
    del.currency,
    COALESCE(del.del_deal_name, sf.sf_deal_name, dcm.dcm_deal_name, '(unnamed)') AS deal_name,
    case when del.deal_id is null and dcm.deal_id is null
         then 'Salesforce only' else 'Seedtag' end as name_source,
    CASE
      -- Excepcion explicita (Barbara, 26-ago): el deal LEXUS (Team One) es
      -- Curation agency aunque no empiece por NEUROX ni este en SF.
      WHEN coalesce(del.deal_id, dcm.deal_id, sf.deal_id)
           in ('1b21334a-cf38-431e-9723-a45d0620dab9','71dc461d-ed39-4dfe-a1ae-94c3991c8561') THEN 'Curation Agency'
      WHEN upper(coalesce(del.deal_id, dcm.deal_id, sf.deal_id)) LIKE 'NEUROX%'
           AND sf.agency LIKE '%Curator%'                                    THEN 'Curation 3rd Party'
      WHEN upper(coalesce(del.deal_id, dcm.deal_id, sf.deal_id)) LIKE 'NEUROX%'
           AND sf.agency IS NOT NULL                                         THEN 'Curation Agency'
      WHEN upper(coalesce(del.deal_id, dcm.deal_id, sf.deal_id)) LIKE 'NEUROX%' THEN 'DSP Marketplace'
      -- TEST despues de las reglas NEUROX: un deal NEUROX llamado TEST cuenta
      -- como negocio real; solo los no-NEUROX se excluyen como test.
      WHEN upper(COALESCE(del.del_deal_name, sf.sf_deal_name, dcm.dcm_deal_name, '')) LIKE '%TEST%' THEN 'excluida - test'
      ELSE 'DSP marketplace - Migrated'
    END AS business_line,
    sf.brand,
    sf.agency_group_name,
    -- Seedtag agency name from curation metrics; SF short name as fallback
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
    del.platform_spend_lc,
    del.gross_revenue_lc,
    del.pub_cost_lc,
    del.curator_margin_total_lc,
    round(del.curator_margin_total_lc * (1 - sf.curator_margin_split), 2) AS curator_margin_stx_lc,
    round(del.curator_margin_total_lc * sf.curator_margin_split, 2)       AS curator_margin_curator_lc,
    -- margen desde el gross correcto (net_revenue de la tabla).
    -- coalesce: sin curator margin / discount el margen es gross - pub cost,
    -- no NULL (NULL se propagaria por la resta). Filas sin delivery → NULL.
    round(del.gross_revenue_lc
          - coalesce(del.curator_margin_total_lc * sf.curator_margin_split, 0)
          - coalesce(del.post_auction_discount_lc, 0)
          - del.pub_cost_lc, 2)     AS margin_lc,
    -- dcm es diario (join por dia): metricas sumables sin deduplicar
    dcm.requests,
    dcm.bids,
    dcm.wins,
    dcm.impressions,
    del.active_days,
    sf.sf_product_lines
  FROM del
  -- FULL OUTER es seguro porque dcm ya viene filtrado a deals de curation:
  -- recupera los dias con trafico SSP pero sin delivery (estado "no bids").
  FULL OUTER JOIN dcm ON del.deal_id = dcm.deal_id AND del.dt = dcm.date
  FULL OUTER JOIN sf  ON coalesce(del.deal_id, dcm.deal_id) = sf.deal_id
  LEFT JOIN cur ON coalesce(del.deal_id, dcm.deal_id, sf.deal_id) = cur.deal_id
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
-- 26-ago-2026). Grano: deal-dia-seat-mediatype.
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
    -- adomain fuera por ahora — brand NULL en BFM
    , cast(null as varchar) as brand
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
    , cast(0 as double) as platform_spend_lc
    , sum(a.revenue_gross) as gross_revenue_lc
    , sum(a.revenue) as pub_cost_lc
    , cast(null as double) as curator_margin_total_lc
    , cast(null as double) as curator_margin_stx_lc
    , cast(null as double) as curator_margin_curator_lc
    -- BFM no tiene curator margin ni post auction discount: margin = gross - pub cost
    , sum(a.revenue_gross) - sum(a.revenue) as margin_lc
    , sum(a.ads_served) as requests
    , sum(a.outgoing_bids) as bids
    , sum(a.total_bids_placed) as wins
    , sum(a.impressions) as impressions
    , cast(null as bigint) as sf_product_lines
  from st_datalakehouse.analytics.reporting_bfm_demand a
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
    and a.date >= date '2026-01-01'
    and a.date < current_date  -- closed days only
  group by 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20
)

, unioned as (
  SELECT 'STX' AS origin, date, deal_id, salesforce_crm_id, currency, deal_name,
         name_source, business_line, brand, agency_group_name, agency, channel_id,
         dsp, connection_type, seat_id, country_served, country_sold, owner, am_csm,
         inventory_type, format,
         platform_spend_lc, gross_revenue_lc, pub_cost_lc,
         curator_margin_total_lc, curator_margin_stx_lc, curator_margin_curator_lc, margin_lc,
         requests, bids, wins, impressions,
         sf_product_lines
  FROM stx
  UNION ALL
  SELECT 'BFM', bfx.date, bfx.deal_id, bfx.salesforce_crm_id, bfx.currency, bfx.deal_name,
         bfx.name_source, bfx.business_line, bfx.brand, bfx.agency_group_name, bfx.agency,
         coalesce(m.channel_label, bfx.dsp) as channel_id,
         bfx.dsp, bfx.connection_type, bfx.seat_id, bfx.country_served, bfx.country_sold,
         bfx.owner, bfx.am_csm,
         bfx.inventory_type, bfx.format,
         bfx.platform_spend_lc, bfx.gross_revenue_lc, bfx.pub_cost_lc,
         bfx.curator_margin_total_lc, bfx.curator_margin_stx_lc, bfx.curator_margin_curator_lc, bfx.margin_lc,
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

-- First date each deal EVER appeared in any source (full history, cheap
-- aggregations) — powers the "new deals" KPI regardless of the window.
, first_seen as (
  select deal_id, min(d) as first_seen from (
    select deal_id, min(date(date_hour)) d
    from st_datalakehouse.ad_exchange.deal_channel_metrics_hourly
    where deal_name is not null group by 1
    union all
    select deal_id, min(dt) from big_query_bdb.business.daily_curation_delivery_utc group by 1
    union all
    select deal_id, min(date) from st_datalakehouse.analytics.reporting_bfm_demand
    where business_line in ('Select - BFM','DSP Marketplace - BFM') group by 1
  ) group by 1
)

-- EUR conversion — DAILY rates from fx_rates_daily. rate = UNITS of the row's
-- currency per 1 EUR (USD≈1.16, BRL≈6.0, EUR=1.0) → DIVIDE lc / rate.
-- The table covers every calendar day (weekends included), so the exact-date
-- join needs no fallback; a missing (currency, day) would show as NULL _eur.
, rates as (
  select dt_utc, currency, rate
  from big_query_bdb.business.fx_rates_daily
  where dt_utc >= date '2026-01-01'
)

SELECT
  u.*,
  fs.first_seen,
  round(u.platform_spend_lc          / r.rate, 2) AS platform_spend_eur,
  round(u.gross_revenue_lc           / r.rate, 2) AS gross_revenue_eur,
  round(u.pub_cost_lc                / r.rate, 2) AS pub_cost_eur,
  round(u.curator_margin_total_lc    / r.rate, 2) AS curator_margin_total_eur,
  round(u.curator_margin_stx_lc      / r.rate, 2) AS curator_margin_stx_eur,
  round(u.curator_margin_curator_lc  / r.rate, 2) AS curator_margin_curator_eur,
  round(u.margin_lc                  / r.rate, 2) AS margin_eur,
  -- Pct sobre el total combinado, en EUR (las _lc mezclan divisas).
  round(100 * (u.gross_revenue_lc / r.rate)
        / sum(u.gross_revenue_lc / r.rate) OVER (), 2) AS pct_of_total
FROM unioned u
LEFT JOIN first_seen fs ON fs.deal_id = u.deal_id
LEFT JOIN rates r
  ON r.currency = u.currency
  AND r.dt_utc = u.date
ORDER BY gross_revenue_eur DESC
