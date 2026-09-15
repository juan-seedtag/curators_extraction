-- SALESFORCE-ONLY refresh (mirrors curation.py::refresh_sf_only, exclusion via the table itself). Run STEP A, then STEP B.
-- STEP A  (run alone, no trailing semicolon)
DELETE FROM st_datalakehouse.analytics.reporting_curation_deals WHERE name_source = 'Salesforce only'
-- STEP B  (run alone)
INSERT INTO st_datalakehouse.analytics.reporting_curation_deals
(origin, date, deal_id, salesforce_crm_id, currency, deal_name, name_source, business_line, brand, agency_group_name, agency, channel_id, dsp, connection_type, seat_id, country_served, country_sold, owner, am_csm, inventory_type, format, record_type, platform_spend_lc, gross_revenue_lc, pub_cost_lc, curator_margin_total_lc, curator_margin_stx_lc, curator_margin_curator_lc, margin_lc, requests, bids, wins, impressions, sf_product_lines, first_seen, platform_spend_eur, gross_revenue_eur, pub_cost_eur, curator_margin_total_eur, curator_margin_stx_eur, curator_margin_curator_eur, margin_eur, bid_rate, win_rate, cpm_lc, cpm_eur, margin_pct, pct_of_total)
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
cur AS (
  SELECT DISTINCT deal_id,
    replace(agency_name, 'Sedtag ', 'Seedtag ') AS agency_name
  FROM st_datalakehouse.ad_exchange.curation_deal_channel_metrics_hourly
  WHERE agency_name IS NOT NULL
)
SELECT
  'STX', current_date - interval '1' day, sf.deal_id, cast(null as bigint), cast(null as varchar),
  coalesce(sf.sf_deal_name, '(unnamed)'), 'Salesforce only',
  CASE
    WHEN sf.deal_id in ('1b21334a-cf38-431e-9723-a45d0620dab9','71dc461d-ed39-4dfe-a1ae-94c3991c8561') THEN 'Curation Agency'
    WHEN upper(sf.deal_id) LIKE 'NEUROX%' AND sf.agency IS NOT NULL THEN 'Curation Agency'
    WHEN upper(sf.deal_id) LIKE 'NEUROX%' AND cur.agency_name IS NOT NULL AND cur.agency_name NOT LIKE 'DSP %'
         AND lower(cur.agency_name) NOT LIKE '%seedtag%prod%' AND lower(cur.agency_name) NOT LIKE '%test%' THEN 'Curation 3rd Party'
    WHEN upper(coalesce(sf.sf_deal_name,'')) LIKE '%TEST%' AND coalesce(cur.agency_name, sf.agency) NOT LIKE 'DSP%' THEN 'Curation Test'
    WHEN upper(sf.deal_id) LIKE 'NEUROX%' THEN 'DSP Marketplace'
    ELSE 'DSP marketplace - Migrated'
  END,
  sf.brand, coalesce(sf.agency_group_name, cur.agency_name, sf.agency), coalesce(cur.agency_name, sf.agency),
  cast(null as varchar), sf.dsp, 'Reseller', sf.seat_id, sf.country_served, sf.country_sold, sf.owner, sf.am_csm,
  CASE WHEN upper(coalesce(sf.sf_deal_name,'')) LIKE '%CTV%' OR upper(coalesce(sf.sf_deal_name,'')) LIKE '%CONNECTED TV%'
         OR upper(coalesce(sf.sf_deal_name,'')) LIKE '%BEACHFRONT%' THEN 'CTV' ELSE 'Web' END,
  sf.format, sf.record_type,
  cast(null as double), cast(null as double), cast(null as double), cast(null as double), cast(null as double), cast(null as double), cast(null as double),
  cast(null as bigint), cast(null as bigint), cast(null as bigint), cast(null as bigint),
  sf.sf_product_lines, cast(null as date),
  cast(null as double), cast(null as double), cast(null as double), cast(null as double), cast(null as double), cast(null as double), cast(null as double),
  cast(null as double), cast(null as double), cast(null as double), cast(null as double), cast(null as double), cast(null as double)
FROM sf
LEFT JOIN cur ON sf.deal_id = cur.deal_id
WHERE sf.deal_id NOT IN (SELECT deal_id FROM st_datalakehouse.analytics.reporting_curation_deals WHERE name_source <> 'Salesforce only')

