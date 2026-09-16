-- =====================================================================
-- reporting_curation_deals — FULL REBUILD QUERY (Superset / Trino SQL Lab)
--
-- This is the definition of st_datalakehouse.analytics.reporting_curation_deals,
-- verbatim from sql/deals_daily.sql in the curators_extraction repo. The
-- production table is written by bf_automations/curation.py, which runs this
-- same logic ONE DAY AT A TIME; this file is the full-window equivalent.
--
-- Three known differences vs the materialized table:
--   1. pct_of_total is computed here; in the table it is NULL (a whole-window
--      share cannot be produced by a per-day loader).
--   2. Salesforce-only rows (deals with no delivery and no traffic anywhere)
--      come from the FULL OUTER JOIN on sf here; the loader refreshes them in a
--      separate pass at the end of a run.
--   3. The dcm CTE here also selects hb_connector_wins / hb_inserts, which
--      nothing downstream consumes.
--
-- BEFORE YOU RUN IT IN SUPERSET
--   * Your Superset Trino connection needs BOTH catalogs: st_datalakehouse AND
--     big_query_bdb. Missing big_query_bdb access is the most likely failure
--     ("Cannot access catalog big_query_bdb") — several service accounts do not
--     have it even though personal tokens do.
--   * Full window returns ~842k rows and scans deal_channel_metrics_hourly,
--     which holds roughly 6 billion rows PER DAY. Narrow the window first:
--     edit the eight lines tagged <<WINDOW>> below (four pairs: dcm, del, closing_rep, bfx).
--     Keep them as literal DATE constants — moving them into a CTE or a Jinja
--     variable stops Trino pruning partitions and the query will scan
--     everything.
--   * The bounds NOT tagged <<WINDOW>> are deliberate and should stay at
--     2025-01-01: they define the curation deal population, the traffic-based
--     inventory_type, and first_seen, all of which need full history.
--   * pct_of_total (last column) is a window function over the entire result.
--     Drop that line if you only need a slice; it forces a full pass.
--   * No Jinja in this query, so Superset's templating leaves it alone. If your
--     Superset build raises a parameter error on the LIKE patterns, double the
--     percent signs ('NEUROX%' -> 'NEUROX%%').
--   * No trailing semicolon on purpose — Superset appends its own LIMIT.
--
-- If you only want the DATA rather than the definition, read the table instead:
--   SELECT * FROM st_datalakehouse.analytics.reporting_curation_deals
--   WHERE date >= DATE '2026-09-01'
-- =====================================================================

WITH sf AS (
  SELECT
    deal_id,
    deal_name AS sf_deal_name,
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
    record_type,
    -- curator_margin_value viene en porcentaje (80-100 en la tabla hoy) → /100
    -- para dejarlo como fraccion 0-1, que es lo que esperan las formulas
    -- curator_margin_total * split y * (1 - split). Sin valor → 0 (todo a STX).
    -- OJO: sum() sobre las product lines del deal; hoy todas tienen 1 linea con
    -- valor, pero un deal multi-linea sumaria >1 — revisar si aparece el caso.
    coalesce(sum(curator_margin_value), 0) / 100.0 AS curator_margin_split,
    count(*) AS sf_product_lines
  FROM big_query_bdb.business.salesforce_curation_product_lines
  WHERE deal_id IS NOT NULL
  -- OJO: deal_name ahora en el grano (sin arbitrary/max) — si un deal tuviera
  -- product lines con nombres distintos saldrian varias filas sf y el join
  -- duplicaria el dinero de del. Verificado hoy: 0 deals en ese caso.
  GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14
),
dcm AS (
  SELECT
    date(date_hour) AS date
    , deal_id
    , deal_name AS dcm_deal_name
    -- channel_id ahora en el grano (sin arbitrary/max). OJO: un deal-dia con
    -- varios channels genera varias filas dcm y el join duplicaria el dinero
    -- de del en esos dias. Verificado hoy: 0 deal-dias con >1 channel.
    , channel_id
    , sum(requests) as requests
    , sum(bids) as bids
    , sum(wins) as wins
    , sum(ssp_hb_connector_win) as hb_connector_wins
    , sum(ssp_hb_inserts) as hb_inserts
    , sum(impressions) as impressions
  FROM st_datalakehouse.ad_exchange.deal_channel_metrics_hourly
  WHERE date_hour >= date '2025-01-01'   -- <<WINDOW>>
    AND date_hour < current_date   -- <<WINDOW>>
    AND deal_name IS NOT NULL
    -- dcm cubre TODO el exchange: solo deals de curation — conocidos en SF o
    -- con delivery. Cero identidades nuevas vs la poblacion del
    -- dashboard; solo recupera los dias con trafico sin delivery (no bids).
    AND (deal_id IN (SELECT deal_id FROM big_query_bdb.business.salesforce_curation_product_lines
                     WHERE deal_id IS NOT NULL)
         OR deal_id IN (SELECT DISTINCT deal_id FROM big_query_bdb.business.daily_curation_delivery_utc
                        WHERE dt >= date '2025-01-01'))
  GROUP BY 1, 2, 3, 4
),
-- Curation agency name per deal — DISTINCT, sin arbitrary/max. OJO: un deal
-- con mas de un agency_name generaria varias filas y el join duplicaria las
-- metricas sumadas. Verificado hoy: los unicos 15 deals con 2 nombres eran el
-- typo 'Sedtag ES Prod agency' / 'Seedtag ES Prod agency' → se normaliza aqui
-- y queda exactamente 1 fila por deal. Si aparecen variantes nuevas el join
-- volveria a duplicar — vigilar.
cur AS (
  SELECT DISTINCT deal_id,
    replace(agency_name, 'Sedtag ', 'Seedtag ') AS agency_name
  FROM st_datalakehouse.ad_exchange.curation_deal_channel_metrics_hourly
  WHERE agency_name IS NOT NULL
),
-- inventory_type por TRAFICO (no por is_ctv de SF, aun sin rellenar): un deal
-- con impresiones via Beachfront (unica fuente CTV del exchange) es CTV; el
-- resto Web. Deals sin trafico → NULL aqui (fallback al is_ctv de SF).
traffic AS (
  SELECT deal_id,
         SUM(impressions) AS total_imps,
         SUM(CASE WHEN source_type = 'Beachfront' THEN impressions ELSE 0 END) AS ctv_imps
  FROM st_datalakehouse.ad_exchange.deal_channel_metrics_hourly
  WHERE date_hour >= date '2025-01-01'  -- full history ON PURPOSE (inventory_type)
  GROUP BY deal_id
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
    sum(publisher_cost)               AS pub_cost_lc,
    -- EUR nativo de la tabla (mismas definiciones que las _lc de arriba):
    -- STX no pasa por fx_rates_daily — el rate solo se usa para Beachfront.
    round(sum(gross_revenue_eur), 2)  AS platform_spend_eur,
    round(sum(net_revenue_eur), 2)    AS gross_revenue_eur,
    sum(post_auction_discount_eur)    AS post_auction_discount_eur,
    sum(curator_margin_eur)           AS curator_margin_total_eur,
    sum(publisher_cost_eur)           AS pub_cost_eur
  FROM big_query_bdb.business.daily_curation_delivery_utc
  WHERE dt >= date '2025-01-01'   -- <<WINDOW>>
    AND dt < current_date   -- <<WINDOW>>
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
      -- La fuente renombro los agency_name (antes '... - Curator', ahora nombre
      -- plano): la señal de 3rd party ya NO es '%Curator%'. Regla derivada de
      -- los datos (verificado 08-sep-2026): las agencias vienen con
      -- agency_short_name en SF; los curators externos estan en SF con agency
      -- NULL y un agency_name de curation que no empieza por 'DSP '.
      WHEN upper(coalesce(del.deal_id, dcm.deal_id, sf.deal_id)) LIKE 'NEUROX%'
           AND sf.agency IS NOT NULL                                         THEN 'Curation Agency'
      WHEN upper(coalesce(del.deal_id, dcm.deal_id, sf.deal_id)) LIKE 'NEUROX%'
           AND cur.agency_name IS NOT NULL
           AND cur.agency_name NOT LIKE 'DSP %'
           AND lower(cur.agency_name) NOT LIKE '%seedtag%prod%'
           AND lower(cur.agency_name) NOT LIKE '%test%'                      THEN 'Curation 3rd Party'
      -- TEST antes de la regla NEUROX generica: deals llamados TEST cuyo
      -- partner no es un DSP son tests de curation. OJO: agency NULL hace el
      -- NOT LIKE falso → un TEST sin agency cae a DSP Marketplace / Migrated.
      WHEN upper(coalesce(del.del_deal_name, sf.sf_deal_name, dcm.dcm_deal_name, '')) LIKE '%TEST%' AND coalesce(cur.agency_name, sf.agency) NOT LIKE 'DSP%' THEN 'Curation Test'
      WHEN upper(coalesce(del.deal_id, dcm.deal_id, sf.deal_id)) LIKE 'NEUROX%' THEN 'DSP Marketplace'
      ELSE 'DSP marketplace - Migrated'
    END AS business_line,
    sf.brand,
    -- grupo: SF cuando existe; si no, mismo fallback que agency (curation
    -- metrics / SF short name) para que el grupo nunca quede vacio con partner
    coalesce(sf.agency_group_name, cur.agency_name, sf.agency) as agency_group_name,
    -- Seedtag agency name from curation metrics; SF short name as fallback
    coalesce(cur.agency_name, sf.agency) as agency,
    dcm.channel_id,
    sf.dsp,
    sf.seat_id,
    sf.country_served,
    sf.country_sold,
    sf.owner,
    sf.am_csm,
    -- inventory_type: (1) el TRAFICO decide cuando el deal tiene impresiones
    -- (Beachfront = unica fuente CTV del exchange); (2) sin impresiones decide el
    -- NOMBRE (CTV / Connected TV / Beachfront — los TEST tambien cuentan como CTV
    -- si lo llevan en el nombre); (3) sin pista -> Web. is_ctv de SF no se usa:
    -- el campo sigue sin rellenarse en el origen.
    CASE
      WHEN coalesce(tr.total_imps, 0) > 0
        THEN CASE WHEN coalesce(tr.ctv_imps, 0) > 0 THEN 'CTV' ELSE 'Web' END
      WHEN upper(coalesce(del.del_deal_name, sf.sf_deal_name, dcm.dcm_deal_name, '')) LIKE '%CTV%'
        OR upper(coalesce(del.del_deal_name, sf.sf_deal_name, dcm.dcm_deal_name, '')) LIKE '%CONNECTED TV%'
        OR upper(coalesce(del.del_deal_name, sf.sf_deal_name, dcm.dcm_deal_name, '')) LIKE '%BEACHFRONT%'                              THEN 'CTV'
      ELSE 'Web'
    END as inventory_type,
    sf.format,
    sf.record_type,
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
    -- EUR nativo (misma logica que las _lc, sobre las columnas *_eur de del)
    del.platform_spend_eur,
    del.gross_revenue_eur,
    del.pub_cost_eur,
    del.curator_margin_total_eur,
    round(del.curator_margin_total_eur * (1 - sf.curator_margin_split), 2) AS curator_margin_stx_eur,
    round(del.curator_margin_total_eur * sf.curator_margin_split, 2)       AS curator_margin_curator_eur,
    round(del.gross_revenue_eur
          - coalesce(del.curator_margin_total_eur * sf.curator_margin_split, 0)
          - coalesce(del.post_auction_discount_eur, 0)
          - del.pub_cost_eur, 2)    AS margin_eur,
    del.gross_revenue_lc  AS reported_gross_revenue_lc,
    del.pub_cost_lc       AS reported_pub_cost_lc,
    del.gross_revenue_eur AS reported_gross_revenue_eur,
    del.pub_cost_eur      AS reported_pub_cost_eur,
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
  LEFT JOIN traffic tr ON coalesce(del.deal_id, dcm.deal_id) = tr.deal_id
)

-- Reported figures for Beachfront = what closing reports (revenue net of Select
-- fees + Ent Aggregator, publisher_cost), summed to the bfx grain (deal-day x
-- seat) FIRST — closing is far finer and joining raw rows inflates ~4x. NULL
-- seat_id on both sides → sentinel key. 452 seat-days carry two bfx rows: both
-- get the amount, an accepted +0.019% overcount (audits/sql_curation_deals_reported_cols.sql).
, closing_rep as (
  select date, deal_id, deal_name, coalesce(seat_id, '∅') as seat_key,
         sum(revenue) as rep_rev, sum(publisher_cost) as rep_cost
  from st_datalakehouse.analytics.reporting_closing_bfm_demand
  where business_line in ('Select - BFM', 'DSP Marketplace - BFM')
    and date >= date '2025-01-01'   -- <<WINDOW>>
    and date < current_date   -- <<WINDOW>>
  group by 1, 2, 3, 4
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
    -- clave cruda para reporting_dsp_and_channel_mappings (los nombres
    -- derivados de seat — Walmart, seats de Bidswitch — no matchean advertiser_key)
    , a.advertiser as advertiser_raw
    , a.seat_id
    , cast(null as varchar) as country_served
    , cast(null as varchar) as country_sold
    , cast(null as varchar) as owner
    , cast(null as varchar) as am_csm
    , case when a.media_type = 'Video' then 'CTV' else 'Web' end as inventory_type
    , a.media_type as format
    , cast(NULL as varchar) as record_type
    , cast(0 as double) as platform_spend_lc
    , sum(a.revenue_gross) as gross_revenue_lc
    , sum(a.revenue) as pub_cost_lc
    -- BFM curator margin = reseller_revenue; todo va al curator (stx NULL)
    , sum(a.reseller_revenue) as curator_margin_total_lc
    , cast(null as double) as curator_margin_stx_lc
    , sum(a.reseller_revenue) as curator_margin_curator_lc
    -- BFM sin post auction discount: margin = gross - pub cost (el curator
    -- margin NO se resta aqui — misma formula que antes; avisar si debe restarse)
    , sum(a.revenue_gross) - sum(a.revenue) as margin_lc
    -- EUR se calcula al final via fx_rates_daily (solo Beachfront)
    , cast(null as double) as platform_spend_eur
    , cast(null as double) as gross_revenue_eur
    , cast(null as double) as pub_cost_eur
    , cast(null as double) as curator_margin_total_eur
    , cast(null as double) as curator_margin_stx_eur
    , cast(null as double) as curator_margin_curator_eur
    , cast(null as double) as margin_eur
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
    and a.date >= date '2025-01-01'   -- <<WINDOW>>
    and a.date < current_date   -- <<WINDOW>>
  group by 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20
)

-- STX dsp/channel/connection desde mapping_direct_dsp. OJO: la tabla trae
-- 'AppNexus' DUPLICADO (MSAN y Xandr, ambos Direct) — sin dedupe el join
-- duplicaria el dinero de esas filas. min() elige MSAN; corregir en la tabla.
, dsp_map as (
  select channel_id,
         min(direct_dsp_name) as direct_dsp_name,
         min(connection_type) as connection_type
  from st_datalakehouse.analytics.mapping_direct_dsp
  group by 1
)

, unioned as (
  -- STX mapea contra dsp_map (mapping_direct_dsp): el nombre de la tabla manda,
  -- SF/dcm como fallback. Sin mapping → 'Reseller' (OJO: tambien las filas
  -- 'Salesforce only', que no tienen channel_id).
  SELECT 'STX' AS origin, stx.date, stx.deal_id, stx.salesforce_crm_id, stx.currency, stx.deal_name,
         stx.name_source, stx.business_line, stx.brand, stx.agency_group_name, stx.agency,
         stx.channel_id,
         coalesce(seed.direct_dsp_name, stx.dsp)     AS dsp,
         coalesce(seed.connection_type, 'Reseller')  AS connection_type,
         stx.seat_id, stx.country_served, stx.country_sold, stx.owner, stx.am_csm,
         stx.inventory_type, stx.format,
         platform_spend_lc, gross_revenue_lc, pub_cost_lc,
         curator_margin_total_lc, curator_margin_stx_lc, curator_margin_curator_lc, margin_lc,
         platform_spend_eur, gross_revenue_eur, pub_cost_eur,
         curator_margin_total_eur, curator_margin_stx_eur, curator_margin_curator_eur, margin_eur,
         reported_gross_revenue_lc, reported_pub_cost_lc, reported_gross_revenue_eur, reported_pub_cost_eur,
         requests, bids, wins, impressions,
         sf_product_lines, record_type
  FROM stx
  LEFT JOIN dsp_map seed
    ON lower(seed.channel_id) = lower(stx.channel_id)
  UNION ALL
  -- BFM mapea contra reporting_dsp_and_channel_mappings por el advertiser
  -- CRUDO (advertiser_key es unico → sin fan-out). Labels de la tabla mandan;
  -- fallback al dsp derivado de seats. Sin mapping → 'Direct'.
  SELECT 'BFM', bfx.date, bfx.deal_id, bfx.salesforce_crm_id, bfx.currency, bfx.deal_name,
         bfx.name_source, bfx.business_line, bfx.brand,
         -- Cuentas genericas de clearvu -> 'DSP <channel>' (el nombre generico
         -- no identifica al comprador; el channel si). Sin channel -> se queda
         -- el nombre generico (no romper con NULL).
         case when (bfx.agency in ('BFM Internal','Beachfront Deal Curation','Beachfront Deal Migration')
                   or nullif(bfx.agency, '') is null)
                   and coalesce(bfm_m.channel_label, bfx.advertiser_raw) is not null
              then 'DSP ' || coalesce(bfm_m.channel_label, bfx.advertiser_raw)
              else bfx.agency_group_name end as agency_group_name,
         case when (bfx.agency in ('BFM Internal','Beachfront Deal Curation','Beachfront Deal Migration')
                   or nullif(bfx.agency, '') is null)
                   and coalesce(bfm_m.channel_label, bfx.advertiser_raw) is not null
              then 'DSP ' || coalesce(bfm_m.channel_label, bfx.advertiser_raw)
              else bfx.agency end as agency,
         coalesce(bfm_m.channel_label, bfx.advertiser_raw) as channel_id,
         -- dsp: a seat-resolved buyer (TTD Walmart seats -> 'Walmart', Bidswitch
         -- seats -> the seat's DSP) wins; otherwise the mapping label, raw as last resort
         case when bfx.dsp is distinct from bfx.advertiser_raw then bfx.dsp
              else coalesce(bfm_m.dsp_label, bfx.dsp) end as dsp,
         coalesce(bfm_m.connection_type, 'Direct')         as connection_type,
         bfx.seat_id, bfx.country_served, bfx.country_sold,
         bfx.owner, bfx.am_csm,
         bfx.inventory_type, bfx.format,
         bfx.platform_spend_lc, bfx.gross_revenue_lc, bfx.pub_cost_lc,
         bfx.curator_margin_total_lc, bfx.curator_margin_stx_lc, bfx.curator_margin_curator_lc, bfx.margin_lc,
         bfx.platform_spend_eur, bfx.gross_revenue_eur, bfx.pub_cost_eur,
         bfx.curator_margin_total_eur, bfx.curator_margin_stx_eur, bfx.curator_margin_curator_eur, bfx.margin_eur,
         round(cr.rep_rev, 2)  as reported_gross_revenue_lc,
         round(cr.rep_cost, 2) as reported_pub_cost_lc,
         cast(null as double)  as reported_gross_revenue_eur,
         cast(null as double)  as reported_pub_cost_eur,
         bfx.requests, bfx.bids, bfx.wins, bfx.impressions,
         bfx.sf_product_lines, bfx.record_type
  FROM bfx
  LEFT JOIN st_datalakehouse.analytics.reporting_dsp_and_channel_mappings bfm_m
    ON bfm_m.advertiser_key = bfx.advertiser_raw
  LEFT JOIN closing_rep cr
    ON  cr.date      = bfx.date
    AND cr.deal_id   = bfx.deal_id
    AND cr.deal_name = bfx.deal_name
    AND cr.seat_key  = coalesce(bfx.seat_id, '∅')
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

-- EUR conversion — SOLO Beachfront. STX trae el EUR nativo de sus tablas;
-- para BFM se usa la DAILY rate de fx_rates_daily (rate = units of the row's
-- currency per 1 EUR, USD≈1.16 → DIVIDE lc / rate). The table covers every
-- calendar day, so the exact-date join needs no fallback; a missing
-- (currency, day) would show as NULL _eur.
, rates as (
  select dt_utc, currency, rate
  from big_query_bdb.business.fx_rates_daily
  where dt_utc >= date '2025-01-01'
)

-- EUR final: STX = columnas nativas (ya en unioned); BFM = _lc / rate.
-- coalesce funciona porque las _eur de bfx son NULL y el rate solo se junta
-- en filas BFM (las STX quedan con r.rate NULL → el termino /rate es NULL).
, final as (
  SELECT
    u.origin, u.date, u.deal_id, u.salesforce_crm_id, u.currency, u.deal_name,
    u.name_source, u.business_line, u.brand, u.agency_group_name, u.agency,
    u.channel_id, u.dsp, u.connection_type,
    u.seat_id, u.country_served, u.country_sold, u.owner, u.am_csm,
    u.inventory_type, u.format, u.record_type,
    u.platform_spend_lc, u.gross_revenue_lc, u.pub_cost_lc,
    u.curator_margin_total_lc, u.curator_margin_stx_lc, u.curator_margin_curator_lc, u.margin_lc,
    u.requests, u.bids, u.wins, u.impressions,
    u.sf_product_lines,
    fs.first_seen,
    coalesce(u.platform_spend_eur,         round(u.platform_spend_lc         / r.rate, 2)) AS platform_spend_eur,
    coalesce(u.gross_revenue_eur,          round(u.gross_revenue_lc          / r.rate, 2)) AS gross_revenue_eur,
    coalesce(u.pub_cost_eur,               round(u.pub_cost_lc               / r.rate, 2)) AS pub_cost_eur,
    coalesce(u.curator_margin_total_eur,   round(u.curator_margin_total_lc   / r.rate, 2)) AS curator_margin_total_eur,
    coalesce(u.curator_margin_stx_eur,     round(u.curator_margin_stx_lc     / r.rate, 2)) AS curator_margin_stx_eur,
    coalesce(u.curator_margin_curator_eur, round(u.curator_margin_curator_lc / r.rate, 2)) AS curator_margin_curator_eur,
    coalesce(u.margin_eur,                 round(u.margin_lc                 / r.rate, 2)) AS margin_eur,
    u.reported_gross_revenue_lc,
    u.reported_pub_cost_lc,
    coalesce(u.reported_gross_revenue_eur, round(u.reported_gross_revenue_lc / r.rate, 2)) AS reported_gross_revenue_eur,
    coalesce(u.reported_pub_cost_eur,      round(u.reported_pub_cost_lc      / r.rate, 2)) AS reported_pub_cost_eur
  FROM unioned u
  LEFT JOIN first_seen fs ON fs.deal_id = u.deal_id
  -- rate SOLO para Beachfront: STX usa el EUR nativo de sus tablas
  LEFT JOIN rates r
    ON u.origin = 'BFM'
    AND r.currency = u.currency
    AND r.dt_utc = u.date
)

SELECT
  f.*,
  -- Metricas derivadas A NIVEL DE FILA (ratio de las sumas de esa fila;
  -- nullif evita division por cero → NULL). OJO al agregar: promediar estas
  -- columnas NO da el ratio agregado — el dashboard las recalcula como
  -- ratio-of-sums sobre las filas visibles; estas sirven para CSV/consultas
  -- por fila. En BFM el funnel usa la convencion Beachfront (bids=outgoing_bids,
  -- requests=ads_served) — no comparable 1:1 con STX.
  round(100.0 * f.bids        / nullif(f.requests, 0), 2)          AS bid_rate,
  round(100.0 * f.impressions / nullif(f.bids, 0), 2)              AS win_rate,
  round(1000.0 * f.gross_revenue_lc  / nullif(f.impressions, 0), 4) AS cpm_lc,
  round(1000.0 * f.gross_revenue_eur / nullif(f.impressions, 0), 4) AS cpm_eur,
  round(100.0 * f.margin_lc / nullif(f.gross_revenue_lc, 0), 2)    AS margin_pct,
  -- Pct sobre el total combinado, en EUR (las _lc mezclan divisas).
  round(100 * f.gross_revenue_eur / sum(f.gross_revenue_eur) OVER (), 2) AS pct_of_total
FROM final f
ORDER BY gross_revenue_eur DESC
