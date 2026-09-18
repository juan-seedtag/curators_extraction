#!/usr/bin/env python3
"""
Deals Daily Dashboard
=====================
Self-contained deals HEALTH report, last 30 closed days, funnel-first,
mixing two origins:

  STX — Seedtag delivery (big_query_bdb.business.daily_curation_delivery_utc,
        EUR) enriched with Salesforce curation product lines and SSP funnel
        metrics (deal_channel_metrics_hourly).
  BFM — Beachfront (st_datalakehouse.analytics.reporting_bfm_demand, USD),
        curator/Salesforce fields NULL, funnel metrics in Beachfront naming.

One Trino query (sql/deals_daily.sql); everything else (KPIs, chart,
field/metric pickers, cascading filters, table, CSV/email) is client-side.
The output file needs no server and can be shared (Drive, email) as-is.

NOTE: big_query_bdb works through the user's own Trino auth (the de-toolbox
service user lacks access) — a permission error there is an auth issue, not
a query bug.

Usage:
    uv run python generate_report.py                # query Trino + build
    uv run python generate_report.py --from-csv     # rebuild from cached CSV
    uv run python generate_report.py --upload       # build + publish to Drive
"""

from __future__ import annotations

import argparse
import csv as _csv
import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from tools._common import run_trino_query, save_csv
from tools.report_generator import generate_html

PROJECT_ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = PROJECT_ROOT / "output"
OUTPUT_DIR.mkdir(exist_ok=True)
SQL_PATH = PROJECT_ROOT / "sql/deals_daily.sql"
# Default source: the materialized table (loaded by bf_automations/curation.py,
# same query at daily grain). --full-query runs sql/deals_daily.sql instead.
DEALS_TABLE = os.getenv("DEALS_TABLE", "st_datalakehouse.analytics.reporting_curation_deals")
TABLE_SQL = f"SELECT * FROM {DEALS_TABLE}"
CSV_PATH = OUTPUT_DIR / "deals_daily.csv"
HTML_PATH = OUTPUT_DIR / "deals_dashboard.html"

# Google Drive upload — same service account + shared drive as adex_demand_dashboard;
# fixed filename so the share link is stable across rebuilds.
DRIVE_SA_JSON = os.getenv(
    "DRIVE_SA_JSON",
    str(PROJECT_ROOT.parent / "adex_demand_dashboard" / "prj-jdpa-560863a21518.json"),
)
DRIVE_ROOT_FOLDER_ID = os.getenv("DRIVE_ROOT_FOLDER_ID", "1TAFpUwZLeat4wNWPYeQGayLE56UMfBvl")
DRIVE_SUBFOLDER = os.getenv("DRIVE_SUBFOLDER", "Ad Exchange Dashboard")
DRIVE_FILENAME = os.getenv("DRIVE_FILENAME", "deals_dashboard.html")
# The shared link (Apps Script viewer) points at this exact file — update it in
# place by ID so the link always shows the latest build.
DRIVE_FILE_ID = os.getenv("DRIVE_FILE_ID", "1kPq3o3RoNHnabU7rZvA6piHgDE3yOgBO")

INT_FIELDS = ("salesforce_crm_id", "requests", "bids", "wins", "impressions",
              "sf_product_lines")
_MONEY = ("platform_spend", "gross_revenue", "pub_cost", "curator_margin_total",
          "curator_margin_stx", "curator_margin_curator", "margin")
FLOAT_FIELDS = tuple(m + s for m in _MONEY for s in ("_lc", "_eur")) + (
    "pct_of_total", "bid_rate", "win_rate", "cpm_lc", "cpm_eur", "margin_pct")
STR_FIELDS = ("origin", "deal_id", "currency", "deal_name", "name_source",
              "business_line", "brand", "agency_group_name", "agency", "channel_id",
              "dsp", "connection_type", "seat_id", "country_served", "country_sold",
              "owner", "am_csm", "inventory_type", "format", "record_type")


def _norm_row(r: dict) -> dict:
    """Normalise types in place (shared by Trino + CSV paths): Decimals/strs →
    float/int, empty strings → None, dates → ISO str."""
    r["date"] = str(r["date"])[:10]
    if r.get("first_seen"):
        r["first_seen"] = str(r["first_seen"])[:10]
    for k in FLOAT_FIELDS:
        v = r.get(k)
        r[k] = round(float(v), 2) if v not in (None, "") else None
    for k in INT_FIELDS:
        v = r.get(k)
        r[k] = int(float(v)) if v not in (None, "") else None
    # salesforce_crm_id is an id, not a metric — keep it a string for the UI
    if r.get("salesforce_crm_id") is not None:
        r["salesforce_crm_id"] = str(r["salesforce_crm_id"])
    for k in STR_FIELDS:
        if r.get(k) == "":
            r[k] = None
    return r


# Rolling grain, three tiers (env-overridable):
#   last DAILY_KEEP_DAYS days ......... daily (health states need this window)
#   MONTHLY_BEFORE .. daily cutoff .... weekly (rows dated on the week's Monday)
#   before MONTHLY_BEFORE (2025) ...... monthly (rows dated on the 1st)
# Quarter/year views aggregate client-side from monthly. Set DAILY_KEEP_DAYS=0
# to disable all aggregation.
DAILY_KEEP_DAYS = int(os.getenv("DAILY_KEEP_DAYS", "60"))
MONTHLY_BEFORE = os.getenv("MONTHLY_BEFORE", "2026-01-01")

_SUM_FLOAT = tuple(m + s for m in _MONEY for s in ("_lc", "_eur"))
_SUM_INT = ("requests", "bids", "wins", "impressions")
_DIM_KEYS = ("origin", *STR_FIELDS, "first_seen", "sf_product_lines")


def _week_start(day: str) -> str:
    import datetime as _dt
    dte = _dt.date.fromisoformat(day)
    return (dte - _dt.timedelta(days=dte.weekday())).isoformat()


def _bucket_rows(rows: list[dict], bucket_of) -> list[dict]:
    agg: dict[tuple, dict] = {}
    for r in rows:
        key = (bucket_of(r["date"]),) + tuple(r.get(k) for k in _DIM_KEYS)
        a = agg.get(key)
        if a is None:
            a = {k: r.get(k) for k in _DIM_KEYS}
            a["date"] = key[0]
            for m in _SUM_FLOAT + _SUM_INT + ("pct_of_total",):
                a[m] = None
            agg[key] = a
        for m in _SUM_FLOAT + _SUM_INT + ("pct_of_total",):
            v = r.get(m)
            if v is not None:
                a[m] = (a[m] or 0) + v
    out = list(agg.values())
    for a in out:
        for m in _SUM_FLOAT:
            if a[m] is not None:
                a[m] = round(a[m], 2)
        # derived metrics recomputed as ratio-of-sums over the bucket
        rq, b, imp = a.get("requests"), a.get("bids"), a.get("impressions")
        g_lc, g_eur, mg = a.get("gross_revenue_lc"), a.get("gross_revenue_eur"), a.get("margin_lc")
        a["bid_rate"] = round(100.0 * b / rq, 2) if b is not None and rq else None
        a["win_rate"] = round(100.0 * imp / b, 2) if imp is not None and b else None
        a["cpm_lc"] = round(1000.0 * g_lc / imp, 4) if g_lc is not None and imp else None
        a["cpm_eur"] = round(1000.0 * g_eur / imp, 4) if g_eur is not None and imp else None
        a["margin_pct"] = round(100.0 * mg / g_lc, 2) if mg is not None and g_lc else None
    return out


def apply_rolling_grain(rows: list[dict]) -> tuple[list[dict], dict]:
    """Returns (rows, grain_info) — grain_info feeds the client's view selector."""
    import datetime as _dt
    if not rows or DAILY_KEEP_DAYS <= 0:
        return rows, {"daily_from": None, "weekly_from": None}
    max_day = max(r["date"] for r in rows)
    daily_from = (_dt.date.fromisoformat(max_day)
                  - _dt.timedelta(days=DAILY_KEEP_DAYS - 1)).isoformat()
    daily = [r for r in rows if r["date"] >= daily_from]
    weekly_src = [r for r in rows if MONTHLY_BEFORE <= r["date"] < daily_from]
    monthly_src = [r for r in rows if r["date"] < MONTHLY_BEFORE]
    weekly = _bucket_rows(weekly_src, _week_start)
    monthly = _bucket_rows(monthly_src, lambda dstr: dstr[:7] + "-01")
    print(f"  rolling grain: daily since {daily_from} ({len(daily):,}) · "
          f"weekly {MONTHLY_BEFORE}..{daily_from} ({len(weekly_src):,}→{len(weekly):,}) · "
          f"monthly before {MONTHLY_BEFORE} ({len(monthly_src):,}→{len(monthly):,})")
    return monthly + weekly + daily, {"daily_from": daily_from, "weekly_from": MONTHLY_BEFORE}


def load_rows_from_csv(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return [_norm_row(r) for r in _csv.DictReader(f)]


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the Deals Daily Dashboard")
    ap.add_argument("--from-csv", action="store_true",
                    help=f"rebuild from cached {CSV_PATH.name} instead of querying Trino")
    ap.add_argument("--full-query", action="store_true",
                    help="run sql/deals_daily.sql instead of reading the materialized table")
    ap.add_argument("--upload", action="store_true", help="publish the HTML to Google Drive")
    args = ap.parse_args()

    sql_text = SQL_PATH.read_text(encoding="utf-8")

    if args.from_csv:
        if not CSV_PATH.exists():
            raise SystemExit(f"{CSV_PATH} not found — run once without --from-csv first.")
        print(f"Loading rows from {CSV_PATH} …")
        rows = load_rows_from_csv(CSV_PATH)
    elif args.full_query:
        print("Querying Trino (deals_daily.sql — full query) …")
        rows = [_norm_row(r) for r in run_trino_query(sql_text)]
        save_csv(rows, CSV_PATH)
        print(f"  ✓ {len(rows):,} rows → {CSV_PATH}")
    else:
        print(f"Reading {DEALS_TABLE} …")
        rows = [_norm_row(r) for r in run_trino_query(TABLE_SQL)]
        save_csv(rows, CSV_PATH)
        print(f"  ✓ {len(rows):,} rows → {CSV_PATH}")
        # the SQL tooltip shows what actually fed the dashboard
        sql_text = (f"-- Source: {DEALS_TABLE}\n-- (materialized daily by bf_automations/curation.py;"
                    f" logic = sql/deals_daily.sql at daily grain)\n{TABLE_SQL}\n\n" + sql_text)

    dates = sorted({r["date"] for r in rows})
    stx = sum(r["gross_revenue_eur"] or 0 for r in rows if r["origin"] == "STX")
    bfm = sum(r["gross_revenue_eur"] or 0 for r in rows if r["origin"] == "BFM")
    print(f"  {len(rows):,} rows · {dates[0] if dates else '—'} → {dates[-1] if dates else '—'}"
          f" · STX €{stx:,.2f} · BFM €{bfm:,.2f} (EUR)")

    rows, grain_info = apply_rolling_grain(rows)

    html = generate_html(rows=rows, sql_text=sql_text, grain_info=grain_info,
                         now=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    HTML_PATH.write_text(html, encoding="utf-8")
    print(f"  ✓ {HTML_PATH} ({HTML_PATH.stat().st_size/1024:,.0f} KB)")

    if args.upload:
        print("Uploading to Google Drive …")
        if DRIVE_FILE_ID:
            from tools.drive_upload import upload_to_drive_file_id
            upload_to_drive_file_id(DRIVE_SA_JSON, DRIVE_FILE_ID, str(HTML_PATH))
        else:
            from tools.drive_upload import upload_to_drive
            upload_to_drive(DRIVE_SA_JSON, DRIVE_ROOT_FOLDER_ID, DRIVE_SUBFOLDER,
                            DRIVE_FILENAME, str(HTML_PATH))


if __name__ == "__main__":
    main()
