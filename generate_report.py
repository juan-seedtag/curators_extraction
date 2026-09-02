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
FLOAT_FIELDS = tuple(m + s for m in _MONEY for s in ("_lc", "_eur")) + ("pct_of_total",)
STR_FIELDS = ("origin", "deal_id", "currency", "deal_name", "name_source",
              "business_line", "brand", "agency_group_name", "agency", "channel_id",
              "dsp", "connection_type", "seat_id", "country_served", "country_sold",
              "owner", "am_csm", "inventory_type", "format")


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


def load_rows_from_csv(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return [_norm_row(r) for r in _csv.DictReader(f)]


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the Deals Daily Dashboard")
    ap.add_argument("--from-csv", action="store_true",
                    help=f"rebuild from cached {CSV_PATH.name} instead of querying Trino")
    ap.add_argument("--upload", action="store_true", help="publish the HTML to Google Drive")
    args = ap.parse_args()

    sql_text = SQL_PATH.read_text(encoding="utf-8")

    if args.from_csv:
        if not CSV_PATH.exists():
            raise SystemExit(f"{CSV_PATH} not found — run once without --from-csv first.")
        print(f"Loading rows from {CSV_PATH} …")
        rows = load_rows_from_csv(CSV_PATH)
    else:
        print("Querying Trino (deals_daily.sql) …")
        rows = [_norm_row(r) for r in run_trino_query(sql_text)]
        save_csv(rows, CSV_PATH)
        print(f"  ✓ {len(rows):,} rows → {CSV_PATH}")

    dates = sorted({r["date"] for r in rows})
    stx = sum(r["gross_revenue_eur"] or 0 for r in rows if r["origin"] == "STX")
    bfm = sum(r["gross_revenue_eur"] or 0 for r in rows if r["origin"] == "BFM")
    print(f"  {len(rows):,} rows · {dates[0] if dates else '—'} → {dates[-1] if dates else '—'}"
          f" · STX €{stx:,.2f} · BFM €{bfm:,.2f} (EUR)")

    html = generate_html(rows=rows, sql_text=sql_text,
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
