"""business_line_v2 on reporting_adex_demand — FULL-day rebuild.

RUN FROM bf_automations (move this file there: it imports its trino_upload).

v2 scope is DEAL-based, not product_type-based: any O&O or BFM row whose
deal_id exists in reporting_curation_deals gets that table's deal-level label
(Curation Agency / Curation 3rd Party / Curation Test / DSP Marketplace /
DSP marketplace - Migrated); every other row gets business_line_v2 =
business_line. This captures the Migrated deals that trade under plain PMP
product types (inside 'PMP Web - O&O'), which a scoped rebuild cannot reach —
hence the WHOLE day is rebuilt (DELETE day + INSERT full model query + v2).
business_line (v1) is reproduced verbatim, so v1 totals are invariant.

One-off first: python adex_bl_v2.py --setup   (ALTER TABLE ADD COLUMN)
Backfill:      python adex_bl_v2.py 2025-01-01 2026-09-03   (cap at the last
               dbt-loaded day — do NOT rebuild days dbt hasn't produced yet)
Daily patch:   python adex_bl_v2.py --daily   (yesterday only; schedule AFTER
               the dbt load AND curation.py; retire when dbt writes v2)

NOTE if re-running after the earlier curation-scoped version: delete
adex_bl_v2.log first so every day rebuilds under the new scope.
Query = de_dbt_lakehouse/.../reporting_adex_demand.sql ported verbatim
(external + O&O + BFM branches, final normalization), day-parameterized.
"""
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from trino_upload import *   # engine, text, time, pd, CATALOG, SCHEMA

TARGET = f"{CATALOG}.analytics.reporting_adex_demand"
CURATION_TABLE = f"{CATALOG}.analytics.reporting_curation_deals"
DONE_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "adex_bl_v2.log")
WORKERS = 3
log_lock = threading.Lock()
delete_lock = threading.Lock()  # Iceberg rejects concurrent DELETE commits

INSERT_COLS = ("date, dsp_group_name, connection_type, business_line, "
               "product_category, publisher_country, clearvu_account, channel_id, "
               "revenue_gross, total_impressions, total_response_bids, business_line_v2")

REBUILD_SQL = """
WITH curation_bl AS (
    -- deal-level label from the curation table (deal-BASED scope: covers the
    -- Migrated deals that trade under plain PMP product types too)
    SELECT deal_id, max_by(business_line, date) AS bl_v2
    FROM {curation_table}
    GROUP BY 1
),

currency_rates AS (
    SELECT dt_utc, currency, rate AS rate
    FROM big_query_bdb.business.fx_rates_daily
    WHERE currency = 'USD'
      AND dt_utc >= date '2025-01-01'
),

external AS (
    SELECT
        dt AS date,
        CASE
            WHEN a.platform = 'AppNexus' AND (channel_type = 'Direct' OR dsp IN ('Xandr', 'MSAN')) THEN 'Direct'
            WHEN a.platform IN ('Sovrn', 'Sharethrough', 'Rubicon', 'OpenX', 'Pubmatic', 'AppNexus', 'ImproveDigital', 'LoopMe', 'Adform', 'OneTag', 'AdYouLike') THEN 'Reseller'
            WHEN a.platform LIKE 'Smart%' THEN 'Reseller'
            WHEN a.platform IN ('DBM', 'GDN', 'Sportradar', 'StackAdapt', 'NextRoll', 'AdMixerBidswitch', 'Conversant', 'Madopi') THEN 'BidSwitch'
            WHEN a.platform IN ('RtbHouse', 'TheTradeDesk', 'Outbrain', 'StackAdaptDSP', 'Nexxen', 'Opera', 'NextRollPAAPI', 'Viant', 'Beeswax', 'Illumin', 'Deepintent', 'DeepIntent') THEN 'Direct'
        END AS connection_type,
        CASE
            WHEN channel_type = 'Direct' AND product_category = 'CTV' THEN 'Direct External CTV'
            WHEN channel_type = 'PMP' AND product_category = 'CTV' THEN 'PMP External CTV'
            WHEN channel_type = 'Direct' AND product_category <> 'CTV' THEN 'Direct External Web'
            WHEN channel_type = 'PMP' AND product_category <> 'CTV' THEN 'PMP External Web'
        END AS business_line,
        publisher_country,
        product_category,
        CASE
            WHEN channel_type IS NULL AND dsp IS NULL THEN 'DSP Not Found'
            ELSE dsp
        END AS dsp_group_name,
        NULL AS clearvu_account,
        CASE
            WHEN channel_type = 'Direct' THEN NULL
            ELSE a.platform
        END AS channel_id,
        platform,
        revenue_usd AS revenue_gross,
        CAST(NULL AS bigint) AS total_impressions,
        CAST(NULL AS bigint) AS total_response_bids
    FROM (
        SELECT
            dt,
            CASE
                WHEN country_served_name IN ('United States') THEN 'US'
                WHEN country_served_name IN ('Canada') THEN 'CA'
                WHEN country_served_name IN ('Brazil') THEN 'BR'
                WHEN country_served_name IN ('Mexico') THEN 'MX'
                WHEN country_served_name IN ('Australia') THEN 'AU'
                WHEN country_served_name IN ('India') THEN 'IN'
                WHEN country_served_name IN ('United Kingdom') THEN 'GB'
                WHEN country_served_name IN ('Germany') THEN 'DE'
                WHEN country_served_name IN ('France') THEN 'FR'
                WHEN country_served_name IN ('Italy') THEN 'IT'
                WHEN country_served_name IN ('Spain', 'Portugal') THEN 'ES'
                WHEN country_served_name IN ('Belgium', 'Netherlands', 'Luxembourg') THEN 'BNL'
                WHEN country_served_name IN (
                    'United Arab Emirates', 'Bahrain', 'Algeria', 'Egypt', 'Israel',
                    'Jordan', 'Kuwait', 'Lebanon', 'Libyan Arab Jamahiriya', 'Morocco',
                    'Oman', 'Qatar', 'Saudi Arabia', 'Syria', 'Tunisia', 'Turkey', 'Yemen'
                ) THEN 'MENA'
                WHEN country_served_name IN (
                    'Argentina', 'Anguilla', 'Aruba', 'Barbados', 'Bermuda',
                    'Bolivia, Plurinational State of', 'Belize', 'Caribbean Netherlands',
                    'Cayman Islands', 'Chile', 'Costa Rica', 'Curaçao', 'Dominican Republic',
                    'El Salvador', 'French Guiana', 'Grenada', 'Guatemala', 'Guyana',
                    'Haiti', 'Honduras', 'Jamaica', 'Nicaragua', 'Panama', 'Paraguay',
                    'Puerto Rico', 'Saint Martin', 'Sint Maarten (Dutch part)', 'Suriname',
                    'Trinidad and Tobago', 'Turks and Caicos Islands', 'Uruguay',
                    'Venezuela, Bolivarian Republic of'
                ) THEN 'ROLA'
                WHEN country_served_name IN ('Colombia', 'Ecuador', 'Peru') THEN 'AND'
                ELSE 'ROW'
            END AS publisher_country,
            CASE
                WHEN platform = 'DV360' THEN 'DV360'
                WHEN platform = 'Equativ Buyer Connect' AND (format LIKE '%V%' OR format IN ('In Stream')) THEN 'SmartAdServerVideo'
                WHEN platform = 'Equativ Buyer Connect' THEN 'SmartAdServerORTB'
                WHEN platform LIKE '%Pubmatic%' THEN 'Pubmatic'
                WHEN platform = 'The Trade Desk' THEN 'TheTradeDesk'
                WHEN platform LIKE '%Xandr%' THEN 'AppNexus'
            END AS platform,
            channel_type,
            dsp,
            CASE
                WHEN format LIKE '%CTV%' THEN 'CTV'
                WHEN format LIKE '%N%' THEN 'Native'
                WHEN upper(format) LIKE '%D%' OR format IN ('Pulse', 'Skin', 'Social', 'Stories') THEN 'Display'
                WHEN format LIKE '%V%' OR format IN ('In Stream') THEN 'Online Video'
                ELSE 'Other'
            END AS product_category,
            delivery.currency,
            revenue_eur * rate AS revenue_usd
        FROM {catalog}.analytics.etl_managed_business AS delivery
        LEFT JOIN currency_rates c
            ON c.dt_utc = delivery.dt
        WHERE delivery.inventory_type = 'External'
          AND delivery.dt >= timestamp '{d} 00:00:00'
          AND delivery.dt < timestamp '{d} 00:00:00' + interval '1' day
    ) a
),

consolidated_raw AS (
    -- O&O traffic: ALL product types; v2 = deal-level curation label when the
    -- deal is known to the curation table, else the row's own business_line
    SELECT
        CAST(r.date AS date) AS date,
        COALESCE(
            CASE
                WHEN r.channel_id IN ('AdMixerBidswitch', 'Viant', 'NextRoll', 'StackAdapt', 'Nexxen', 'TheTradeDesk', 'Opera', 'Sportradar', 'RtbHouse', 'Beeswax', 'MediaForce', 'Stackadapt', 'Illumin', 'Madopi', 'Conversant', 'Deepintent', 'DeepIntent') THEN r.channel_id
                WHEN r.channel_id IN ('LoopMe', 'Adform', 'OneTag', 'AdYouLike') THEN 'DSP Not Found'
                WHEN r.channel_id IN ('DBM', 'GDN') THEN 'DV360'
                WHEN r.channel_id = 'AmazonBidswitch' THEN 'Amazon DSP'
                WHEN r.channel_id = 'Outbrain' THEN 'Outbrain/Teads'
                WHEN r.channel_id = 'StackAdaptDSP' THEN 'StackAdapt'
                WHEN b.dsp_group_name = 'Madopi Media' THEN 'Madopi'
                ELSE LTRIM(b.dsp_group_name) END,
            b.dsp_name
        ) AS raw_dsp,
        NULL AS clearvu_account,
        r.channel_id,
        CASE
            WHEN product_type LIKE 'O%' THEN 'Open Auction - Seedtag'
            WHEN product_type LIKE 'P%' THEN 'PMP Web - O&O'
            WHEN product_type LIKE 'D%' THEN 'Direct Web - O&O'
            WHEN product_type LIKE 'Curation%' THEN 'PMP - Curation'
        END AS business_line,
        COALESCE(cbl.bl_v2,
            CASE
                WHEN product_type LIKE 'O%' THEN 'Open Auction - Seedtag'
                WHEN product_type LIKE 'P%' THEN 'PMP Web - O&O'
                WHEN product_type LIKE 'D%' THEN 'Direct Web - O&O'
                WHEN product_type LIKE 'Curation%' THEN 'PMP - Curation'
            END) AS business_line_v2,
        r.publisher_country,
        CASE
            WHEN r.product_category = 'Video' THEN 'Online Video'
            WHEN r.product_category = 'Other' THEN 'Display'
            ELSE r.product_category
        END AS product_category,
        CASE
            WHEN channel_id = 'AppNexus' AND (product_type LIKE 'D%' OR b.dsp_group_name IN ('Xandr', 'MSAN')) THEN 'Direct'
            WHEN channel_id IN ('Sovrn', 'Sharethrough', 'Rubicon', 'OpenX', 'Pubmatic', 'AppNexus', 'ImproveDigital', 'LoopMe', 'Adform', 'OneTag', 'AdYouLike') THEN 'Reseller'
            WHEN channel_id LIKE 'Smart%' THEN 'Reseller'
            WHEN channel_id IN ('DBM', 'GDN', 'Sportradar', 'StackAdapt', 'NextRoll', 'AdMixerBidswitch', 'Madopi', 'Conversant', 'AmazonBidswitch', 'Conversant', 'Madopi') THEN 'BidSwitch'
            WHEN channel_id IN ('RtbHouse', 'TheTradeDesk', 'Outbrain', 'StackAdaptDSP', 'Nexxen', 'Opera', 'NextRollPAAPI', 'Viant', 'Beeswax', 'Illumin', 'DeepIntent', 'Deepintent') THEN 'Direct'
        END AS connection_type,
        SUM(r.net_imp_paid) / 1000.0 AS revenue_gross,
        SUM(r.total_impressions) AS total_impressions,
        SUM(r.total_response_bids) AS total_response_bids
    FROM {catalog}.analytics.stg_ssp_responses_daily r
    LEFT JOIN {catalog}.analytics.bidder_dsp_mapping b
        ON r.bidder_id = b.bidder_id AND r.channel_id = b.channel_name
    LEFT JOIN curation_bl cbl ON cbl.deal_id = r.deal_id
    WHERE r.channel_id <> 'Beachfront'
      AND r.source_type NOT IN ('Beachfront', 'SpringServe')
      AND r.date >= timestamp '{d} 00:00:00'
      AND r.date < timestamp '{d} 00:00:00' + interval '1' day
    GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9

    UNION ALL

    -- BFM traffic (all its business lines; autobuying deals excluded as in the model)
    SELECT
        date, raw_dsp, clearvu_account, channel_id, business_line,
        business_line_v2, publisher_country, product_category, connection_type,
        SUM(revenue_gross), SUM(total_impressions), SUM(total_response_bids)
    FROM (
        SELECT
            a.date AS date,
            a.dsp_group_name AS raw_dsp,
            CASE WHEN a.business_line = 'Select - BFM' THEN a.clearvu_account END AS clearvu_account,
            a.channel_id,
            CASE
                WHEN a.business_line = 'PMP - Seedtag' THEN 'PMP CTV - O&O'
                ELSE a.business_line
            END AS business_line,
            COALESCE(cbl.bl_v2,
                CASE
                    WHEN a.business_line = 'Select - BFM' THEN 'Curation 3rd Party'
                    WHEN a.business_line = 'DSP Marketplace - BFM' THEN 'DSP Marketplace'
                    WHEN a.business_line = 'PMP - Seedtag' THEN 'PMP CTV - O&O'
                    ELSE a.business_line
                END) AS business_line_v2,
            a.publisher_country,
            a.product_category,
            a.connection_type,
            a.revenue AS revenue_gross,
            a.total_impressions,
            a.total_response_bids
        FROM {catalog}.analytics.reporting_closing_bfm_demand a
        LEFT JOIN curation_bl cbl ON cbl.deal_id = a.deal_id
        WHERE NOT EXISTS (
              SELECT 1 FROM {catalog}.analytics.reporting_bfm_autobying_deals ab
              WHERE ab.dealid = a.deal_id
          )
          AND a.date >= timestamp '{d} 00:00:00'
          AND a.date < timestamp '{d} 00:00:00' + interval '1' day
    ) bfm
    GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9
)

-- final normalization (ported verbatim from the model's final SELECT)
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
    SUM(total_response_bids) AS total_response_bids,
    business_line_v2
FROM (
    SELECT
        date,
        COALESCE(connection_type, 'Reseller') AS connection_type,
        business_line,
        business_line_v2,
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

    UNION ALL

    SELECT
        date,
        connection_type,
        business_line,
        business_line AS business_line_v2,   -- managed/external: no curation deals
        publisher_country,
        product_category,
        COALESCE(m.dsp_label, external.platform) AS dsp_group_name,
        clearvu_account,
        CASE
            WHEN channel_id IN ('Deepintent', 'DeepIntent') THEN 'DeepIntent'
            ELSE channel_id
        END AS channel_id,
        revenue_gross,
        total_impressions,
        total_response_bids
    FROM external
    LEFT JOIN (
        SELECT advertiser_key, MAX(dsp_label) AS dsp_label
        FROM {catalog}.analytics.reporting_dsp_and_channel_mappings
        GROUP BY 1
    ) m
        ON m.advertiser_key = external.dsp_group_name
)
GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 12
"""


def load_done():
    if not os.path.exists(DONE_LOG):
        return set()
    with open(DONE_LOG) as f:
        return set(line.strip() for line in f if line.strip())


def mark_done(date_str):
    with log_lock:
        with open(DONE_LOG, "a") as f:
            f.write(date_str + "\n")


def _exec_write(sql):
    raw_conn = engine.raw_connection()
    try:
        cursor = raw_conn.cursor()
        cursor.execute(sql)
        cursor.fetchall()   # drain so the statement actually completes
    finally:
        raw_conn.close()


def rebuild_date(date_str):
    delete_sql = f"DELETE FROM {TARGET} WHERE date = DATE '{date_str}'"
    insert_sql = f"INSERT INTO {TARGET} ({INSERT_COLS})\n" + REBUILD_SQL.format(
        d=date_str, catalog=CATALOG, curation_table=CURATION_TABLE)
    for attempt in range(3):
        try:
            # DELETE inside the retry loop: a timed-out INSERT may still have
            # committed — re-deleting first keeps the day exact.
            with delete_lock:
                _exec_write(delete_sql)
            start = time.time()
            _exec_write(insert_sql)
            print(f"   ✅ {date_str} loaded in {time.time()-start:.2f}s.")
            mark_done(date_str)
            return
        except Exception as e:
            print(f"   ❌ {date_str} attempt {attempt+1} failed: {e}")
            time.sleep(5)
    print(f"   ❌ {date_str} FAILED 3x — re-run to retry")


def setup():
    try:
        _exec_write(f"ALTER TABLE {TARGET} ADD COLUMN business_line_v2 varchar")
        print("✅ column business_line_v2 added")
    except Exception as e:
        print(f"column add skipped ({str(e)[:120]})")


if __name__ == "__main__":
    from datetime import date, timedelta

    if "--setup" in sys.argv:
        setup()
        sys.exit(0)

    yesterday = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
    if "--daily" in sys.argv:
        # daily patch mode: no done-log skip (the day must re-run after each load)
        rebuild_date(yesterday)
        sys.exit(0)

    start = sys.argv[1] if len(sys.argv) > 1 else "2025-01-01"
    end = sys.argv[2] if len(sys.argv) > 2 else yesterday
    dates = pd.date_range(start=start, end=end, freq="D")
    done = load_done()
    todo = [d.strftime("%Y-%m-%d") for d in dates if d.strftime("%Y-%m-%d") not in done]
    print(f"Range {start} → {end}: {len(done)} done, {len(todo)} to go with {WORKERS} workers.")
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        list(pool.map(rebuild_date, todo))
    remaining = [d for d in todo if d not in load_done()]
    print(f"{len(remaining)} dates failed: {remaining}" if remaining else "All dates done.")
