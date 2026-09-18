"""Deals Health Dashboard generator — fully self-contained HTML (no server).

Single dataset (sql/deals_daily.sql): STX (Seedtag delivery + SSP funnel) + BFM
(Beachfront), last 30 closed days, funnel-first (deal-days with activity but no
revenue are included). Money metrics come in local currency (_lc) and EUR
(_eur); the UI toggles between them.

Layout (BIR-722 health view): clickable KPI strip (deals / new / monetizing /
no bids / went dark) → health matrix by business line (clickable cells) →
cascading filters (curator first) → evolution chart with metric selector →
field/metric pickers → deals table with state chips → CSV/email.

Deal states are computed client-side over the selected period:
  new         first_seen (full-history) falls inside the period
  monetizing  gross revenue > 0 in period
  no bids     requests > 0 and bids = 0 in period
  no requests active in period but zero requests
  went dark   monetized in the previous equal-length window, no activity now

Data is embedded with _pack() (columnar + dictionary encoding) and rebuilt
client-side by unpack(). Template uses __TOKEN__ placeholders (plain
.replace(), no f-string brace escaping).
"""

from __future__ import annotations

import base64
import gzip
import json


def _pack(rows: list[dict]) -> str:
    """Columnar + dictionary encoding for the big embed (same as reference)."""
    if not rows:
        return json.dumps({"n": 0, "d": {}, "c": {}})
    cols = list(rows[0].keys())
    dicts: dict[str, list] = {}
    data: dict[str, list] = {}
    for col in cols:
        # dates etc. → str, so fresh-Trino and --from-csv builds pack identically
        vals = [v if v is None or isinstance(v, (str, int, float, bool)) else str(v)
                for v in (r.get(col) for r in rows)]
        if all(v is None or isinstance(v, str) for v in vals):
            idx = {v: i for i, v in enumerate(dict.fromkeys(vals))}
            dicts[col] = list(idx)
            data[col] = [idx[v] for v in vals]
        else:
            data[col] = vals
    return json.dumps({"n": len(rows), "d": dicts, "c": data},
                      default=str, ensure_ascii=False)


def generate_html(*, rows: list[dict], sql_text: str, now: str,
                  grain_info: dict | None = None) -> str:
    # The packed JSON is embedded gzipped+base64 and inflated in the browser
    # with the native DecompressionStream — ~10x smaller file, same content.
    packed = _pack(rows).encode("utf-8")
    b64 = base64.b64encode(gzip.compress(packed, 9)).decode("ascii")
    html = _TEMPLATE
    for token, value in {
        "__ROWS_B64__": b64,
        "__SQL_JSON__": json.dumps(sql_text, ensure_ascii=False),
        "__GRAIN_JSON__": json.dumps(grain_info or {"daily_from": None, "weekly_from": None}),
        "__NOW__": now,
    }.items():
        html = html.replace(token, value)
    return html


_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en" data-theme="auto">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Deals Health Dashboard</title>
<script>(function(){var s=localStorage.getItem('seedtag-theme')||'auto';document.documentElement.setAttribute('data-theme',s);})();</script>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Instrument+Sans:wght@400;500;600;700&family=Instrument+Serif:ital@0;1&display=swap" rel="stylesheet">
<style>
:root {
  --bg:#EBE6E4; --surface:#FFFFFF; --surface-2:#F7F4F2; --border:#D4D0CE;
  --text:#2F2E2E; --text-muted:#5E5C5B; --text-subtle:#8D8A89;
  --accent:#FF6B7C; --accent-ink:#FFFFFF; --kpi-strong:#000000; color-scheme:light;
}
html[data-theme="dark"] {
  --bg:#2F2E2E; --surface:#3D3B3A; --surface-2:#4A4847; --border:#5E5C5B;
  --text:#EBE6E4; --text-muted:#D4D0CE; --text-subtle:#8D8A89;
  --accent:#FF6B7C; --accent-ink:#2F2E2E; --kpi-strong:#FFFFFF; color-scheme:dark;
}
@media (prefers-color-scheme: dark) {
  html[data-theme="auto"] {
    --bg:#2F2E2E;--surface:#3D3B3A;--surface-2:#4A4847;--border:#5E5C5B;
    --text:#EBE6E4;--text-muted:#D4D0CE;--text-subtle:#8D8A89;
    --accent-ink:#2F2E2E;--kpi-strong:#FFFFFF;color-scheme:dark;
  }
}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:'Instrument Sans',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:14px;transition:background 200ms,color 200ms}
h1{font-family:'Instrument Serif',Georgia,serif;font-weight:400;letter-spacing:-0.01em}
h2,h3,h4{font-family:'Instrument Sans',sans-serif;font-weight:600}

.report-header{display:flex;align-items:center;gap:16px;padding:24px 32px;border-bottom:1px solid var(--border);background:var(--surface);position:relative}
.report-header h1{font-size:26px}
.report-header .subtitle{color:var(--text-subtle);font-size:13px;margin-top:3px}
#updated-badge{position:fixed;top:16px;right:60px;height:36px;display:inline-flex;align-items:center;gap:7px;background:var(--surface);color:var(--text);border:1px solid var(--border);border-radius:18px;padding:0 14px;z-index:10000;box-shadow:0 2px 8px rgba(0,0,0,.08);font-size:12px;white-space:nowrap}
#updated-badge .lbl{font-size:10px;color:var(--text-subtle)}
#updated-badge .val{font-weight:600}

#theme-toggle{position:fixed;top:16px;right:16px;width:36px;height:36px;display:inline-flex;align-items:center;justify-content:center;background:var(--surface);color:var(--text);border:1px solid var(--border);border-radius:50%;cursor:pointer;z-index:10000;box-shadow:0 2px 8px rgba(0,0,0,.08);transition:transform 150ms}
#theme-toggle:hover{transform:scale(1.05)}
#theme-toggle .icon-moon{display:none}
html[data-theme="dark"] #theme-toggle .icon-sun{display:none}
html[data-theme="dark"] #theme-toggle .icon-moon{display:inline}
@media(prefers-color-scheme:dark){html[data-theme="auto"] #theme-toggle .icon-sun{display:none}html[data-theme="auto"] #theme-toggle .icon-moon{display:inline}}

.page{padding:24px 32px}
.card{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:20px;margin-bottom:22px}
.card-header{display:flex;align-items:center;gap:8px;margin-bottom:14px;font-weight:600;font-size:14px;flex-wrap:wrap}
.card-header .spacer{flex:1}
.note-banner{background:var(--surface-2);border:1px dashed var(--border);border-radius:8px;padding:10px 14px;font-size:12px;color:var(--text-muted);margin-bottom:16px}

/* KPI cards — clickable state filters */
.kpi-row{display:flex;gap:12px;margin-bottom:22px;flex-wrap:wrap}
.kpi-card{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:14px 18px;min-width:170px;flex:1;text-align:center}
.kpi-card.clickable{cursor:pointer;transition:border-color 120ms, box-shadow 120ms}
.kpi-card.clickable:hover{border-color:var(--accent)}
.kpi-card.active{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent)}
.kpi-card .kpi-label{font-size:12px;font-weight:600;color:var(--text-muted);text-transform:uppercase;letter-spacing:.04em}
.kpi-card .kpi-value{font-size:26px;font-weight:700;color:var(--kpi-strong);margin-top:2px;font-variant-numeric:tabular-nums;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.kpi-card .kpi-sub{font-size:12px;color:var(--text-subtle);margin-top:2px}

/* health matrix */
#matrix td.cnt{cursor:pointer;text-align:right;font-variant-numeric:tabular-nums}
#matrix td.cnt:hover{color:var(--accent);font-weight:700}
#matrix td.cnt.sel{color:var(--accent);font-weight:700;text-decoration:underline}

/* chart */
.chart-legend{display:flex;gap:16px;flex-wrap:wrap;margin-top:10px;font-size:12px;color:var(--text-muted)}
.chart-legend .li{display:inline-flex;align-items:center;gap:6px}
.chart-legend .sw{width:11px;height:11px;border-radius:3px;display:inline-block}
.seg-btn{border:1px solid var(--border);background:var(--surface);color:var(--text-muted);font-family:'Instrument Sans',sans-serif;font-size:12px;font-weight:600;padding:5px 14px;border-radius:20px;cursor:pointer}
.seg-btn:hover{color:var(--text);border-color:var(--accent)}
.seg-btn.active{background:var(--accent);color:var(--accent-ink);border-color:var(--accent)}
select.sel-box{padding:6px 10px;border:1px solid var(--border);background:var(--surface);color:var(--text);border-radius:6px;font-size:13px;outline:none;cursor:pointer}
select.sel-box:focus{border-color:var(--accent)}

/* big table tabs */
.tabs-bar{display:flex;gap:6px;margin-bottom:0}
.tab-big{flex:1;min-width:220px;border:1px solid var(--border);border-bottom:none;background:var(--surface-2);color:var(--text-muted);font-family:'Instrument Sans',sans-serif;font-size:15px;font-weight:700;padding:14px 26px;border-radius:12px 12px 0 0;cursor:pointer}
.tab-big:hover{color:var(--text)}
.tab-big.active{background:var(--accent);color:var(--accent-ink);border-color:var(--accent)}
.card.tabbed{border-top-left-radius:0;border-top-right-radius:0;margin-top:0}

/* range calendar */
.cal-month{border-collapse:collapse}
.cal-month caption{font-size:12px;font-weight:600;padding-bottom:6px;color:var(--text)}
.cal-month th{position:static;background:none;border:none;font-size:10px;color:var(--text-subtle);padding:2px 0;text-align:center;cursor:default}
.cal-month td{border:none;padding:0;max-width:none}
.cal-day{width:30px;height:26px;text-align:center;font-size:12px;border-radius:6px;cursor:pointer;color:var(--text)}
.cal-day:hover{background:var(--surface-2)}
.cal-day.dis{color:var(--border);cursor:default;background:none}
.cal-day.inr{background:var(--surface-2)}
.cal-day.endp{background:var(--accent);color:var(--accent-ink);font-weight:700}

/* drill-down */
.deal-chev{cursor:pointer;color:var(--text-subtle);width:26px;text-align:center;user-select:none}
.deal-chev:hover{color:var(--accent)}
tr.drill-row>td{background:var(--surface-2);padding:0}
.drill-panel{margin:10px 14px 14px;background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:12px 14px}
.drill-panel table{font-size:12px}
.drill-panel th,.drill-panel td{padding:5px 10px}

/* state chips + dots */
.chip{border:1px solid var(--border);background:var(--surface);color:var(--text-muted);font-size:12px;font-weight:600;padding:5px 12px;border-radius:20px;cursor:pointer}
.chip:hover{border-color:var(--accent);color:var(--text)}
.chip.active{background:var(--accent);color:var(--accent-ink);border-color:var(--accent)}
.dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:6px;vertical-align:0}
.dot-mon{background:#1A7F37}.dot-nobid{background:#F4D56D}.dot-noreq{background:#CF222E}
.dot-noimp{background:#CF222E}
.dot-dark{background:#8D8A89}.dot-new{background:#5476FF}.dot-act{background:#67C8FE}

/* pickers */
.picker-grid{display:grid;grid-template-columns:1fr 1fr;gap:22px}
@media (max-width:900px){.picker-grid{grid-template-columns:1fr}}
.picker-opts{display:flex;flex-wrap:wrap;gap:4px 14px}
.picker-opt{display:inline-flex;align-items:center;gap:6px;font-size:13px;padding:4px 6px;border-radius:6px;cursor:pointer;white-space:nowrap}
.picker-opt:hover{background:var(--surface-2)}
.picker-opt input{accent-color:var(--accent)}
.picker-opt code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px}
.picker-actions{margin-top:10px;display:flex;gap:8px}
.mini-btn{padding:4px 12px;background:transparent;color:var(--text-muted);border:1px solid var(--border);border-radius:5px;font-size:12px;cursor:pointer}
.mini-btn:hover{border-color:var(--accent);color:var(--accent)}
.info-icon{width:18px;height:18px;background:#238636;color:#fff;border-radius:50%;display:inline-flex;align-items:center;justify-content:center;font-size:11px;font-weight:bold;cursor:pointer;flex-shrink:0;font-style:italic}
.tooltip{display:none;position:absolute;background:var(--surface);border:1px solid var(--border);padding:12px;border-radius:8px;z-index:9999;font-size:12px;max-width:620px;max-height:420px;overflow:auto;box-shadow:0 8px 24px rgba(0,0,0,.15);margin-top:6px;line-height:1.55;white-space:pre-wrap}
.tooltip.active{display:block}
.tooltip.sql{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;word-break:break-word}
.tooltip .copy-hint{display:block;margin-top:8px;color:var(--text-subtle);font-family:'Instrument Sans',sans-serif;font-size:11px}

/* filters — flow with the page (no sticky, no collapse) */
#filters-card{position:static}
/* view selector — two big cards that pick which table (and which setup) is shown */
.view-switch{display:flex;gap:14px;margin-bottom:22px;flex-wrap:wrap}
.view-card{flex:1;min-width:260px;display:flex;align-items:center;gap:14px;background:var(--surface);
  border:1px solid var(--border);border-radius:12px;padding:16px 20px;cursor:pointer;
  transition:border-color 120ms, box-shadow 120ms, background 120ms}
.view-card:hover{border-color:var(--accent)}
.view-card.active{border-color:var(--accent);box-shadow:0 0 0 2px var(--accent);background:var(--surface-2)}
.view-card .vsw-icon{font-size:30px;line-height:1}
.view-card .vsw-title{font-size:17px;font-weight:700;color:var(--kpi-strong)}
.view-card .vsw-sub{font-size:12px;color:var(--text-subtle);margin-top:3px}
/* tables get a viewport of their own instead of growing the page forever */
.tbl-scroll{max-height:560px;overflow:auto}
.filter-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:12px 14px}
.filter-item{display:flex;flex-direction:column;gap:4px;min-width:0}
.filter-item .ms-trigger{width:100%}
.curator-row{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:12px 14px;padding:12px;border:1px solid var(--accent);border-radius:10px;background:var(--surface-2);margin-bottom:14px}
.curator-row .flabel{color:var(--accent)}
.filter-sep{font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.05em;color:var(--text-subtle);margin:2px 0 8px}
.flabel{font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.05em;color:var(--text-subtle)}
.ms-wrap{position:relative}
.ms-trigger{display:flex;align-items:center;justify-content:space-between;gap:6px;padding:7px 10px;background:var(--surface);border:1px solid var(--border);border-radius:6px;cursor:pointer;height:32px;font-size:13px;color:var(--text);min-width:150px}
.ms-trigger:hover,.ms-trigger.open{border-color:var(--accent)}
.ms-trigger .ms-label{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:170px}
.ms-trigger .ms-arrow{color:var(--text-subtle);font-size:10px;flex-shrink:0}
.ms-trigger.active-filter{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent)}
.ms-dropdown{display:none;position:absolute;top:calc(100% + 4px);left:0;min-width:260px;max-width:340px;background:var(--surface);border:1px solid var(--border);border-radius:8px;box-shadow:0 8px 24px rgba(0,0,0,.15);z-index:10001;max-height:380px;flex-direction:column}
.ms-dropdown.open{display:flex}
.ms-search{padding:8px;border-bottom:1px solid var(--border);position:relative}
.ms-search input{width:100%;padding:6px 26px 6px 8px;background:var(--surface-2);border:1px solid var(--border);border-radius:5px;font-size:12px;color:var(--text);outline:none}
.ms-search .ms-x{position:absolute;right:14px;top:50%;transform:translateY(-50%);border:none;background:transparent;color:var(--text-subtle);cursor:pointer;font-size:12px;line-height:1;padding:2px;display:none}
.ms-search .ms-x.show{display:block}
.ms-search .ms-x:hover{color:var(--accent)}
.ms-options{overflow-y:auto;padding:4px 0;flex:1}
.ms-option{display:flex;align-items:center;gap:8px;padding:6px 12px;cursor:pointer;font-size:13px}
.ms-option:hover{background:var(--surface-2)}
.ms-option input{accent-color:var(--accent);flex-shrink:0}
.ms-option span{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ms-footer{padding:8px;border-top:1px solid var(--border);display:flex;justify-content:space-between;align-items:center;gap:8px}
.ms-footer .ms-count{font-size:11px;color:var(--text-subtle);white-space:nowrap}
.ms-footer .ms-btns{display:flex;gap:6px}
.ms-footer button{padding:5px 10px;background:transparent;color:var(--text-muted);border:1px solid var(--border);border-radius:5px;font-size:12px;cursor:pointer;white-space:nowrap}
.ms-footer button:hover{border-color:var(--accent);color:var(--accent)}

/* table */
.table-wrapper{overflow-x:auto;border:1px solid var(--border);border-radius:10px}
table{width:100%;border-collapse:collapse;font-size:13px;background:var(--surface)}
th{text-align:left;padding:10px 14px;border-bottom:1px solid var(--border);font-weight:600;background:var(--surface-2);color:var(--text-muted);text-transform:uppercase;font-size:11px;letter-spacing:.04em;white-space:nowrap;position:sticky;top:0;cursor:pointer;user-select:none}
th:hover{color:var(--accent)}
td{padding:9px 14px;border-bottom:1px solid var(--border);max-width:420px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
tr:last-child td{border-bottom:none}
tr:hover td{background:var(--surface-2)}
.number{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.muted{color:var(--text-subtle)}
tr.total-row td{background:var(--surface-2);font-weight:700;border-top:2px solid var(--border)}
tr.total-row:hover td{background:var(--surface-2)}
.table-meta{display:flex;align-items:center;justify-content:space-between;margin:14px 2px 0;flex-wrap:wrap;gap:8px}
.table-meta .count{font-size:13px;color:var(--text-subtle)}
.pagination{display:flex;align-items:center;gap:6px}
.pagination button{padding:4px 10px;background:var(--surface);border:1px solid var(--border);border-radius:5px;font-size:12px;cursor:pointer;color:var(--text)}
.pagination button:hover{border-color:var(--accent)}
.pagination button.active{background:var(--accent);color:var(--accent-ink);border-color:var(--accent)}
.pagination button:disabled{opacity:.4;cursor:default}
.pagination .pg-info{font-size:12px;color:var(--text-subtle)}

.btn-csv{padding:6px 14px;background:var(--accent);color:var(--accent-ink);border:none;border-radius:6px;font-size:12px;cursor:pointer;font-weight:600}
.btn-csv:hover{opacity:.9}
.email-box{padding:7px 12px;border:1px solid var(--border);background:var(--surface);color:var(--text);border-radius:6px;font-size:13px;min-width:250px;outline:none}
.email-box:focus{border-color:var(--accent)}

footer.report-footer{padding:16px 32px;color:var(--text-subtle);font-size:12px;display:flex;align-items:center;gap:8px}
footer.report-footer svg{opacity:.75}
@media (max-width:768px){.page{padding:18px 16px}.report-header{padding:16px}}
</style>
</head>
<body>

<button id="theme-toggle" aria-label="Toggle dark mode">
  <svg class="icon-sun" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41"/></svg>
  <svg class="icon-moon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>
</button>

<div id="updated-badge" title="Last data refresh">🕐 <span class="lbl">Last updated</span> <span class="val">__NOW__</span></div>

<header class="report-header">
  <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="32" height="32" aria-label="Seedtag">
    <circle cx="50" cy="50" r="50" fill="#FF6B7C"/><circle cx="50" cy="27" r="10" fill="white"/>
    <path d="M50,54 C47,47 16,49 15,65 C14,79 35,84 50,79Z" fill="white"/>
    <path d="M50,54 C53,47 84,49 85,65 C86,79 65,84 50,79Z" fill="white"/>
  </svg>
  <div>
    <h1>Deals Health Dashboard</h1>
    <div class="subtitle">Analytics Team &middot; <span id="hdr-range"></span> &middot; STX (Seedtag) + BFM (Beachfront) &middot; funnel-first
      <span class="info-icon" title="View SQL" onclick="toggleTooltip(event,'sql-tip')" style="vertical-align:-4px;margin-left:4px">i</span>
      <div class="tooltip sql" id="sql-tip"></div>
    </div>
  </div>
</header>

<div class="page">

<div class="note-banner">💶 <strong>Currency:</strong> toggle EUR ⇄ local currency below — EUR uses monthly average rates (currency_rates_monthly); local mixes EUR/BRL/USD in totals.
&nbsp;·&nbsp; Granularity: DAILY for the last 60 days, WEEKLY for the rest of 2026, MONTHLY for 2025 (rows dated on the bucket's first day). &nbsp;·&nbsp; Deal-days with SSP activity but no delivery revenue are included (money shows —).
&nbsp;·&nbsp; BFM funnel naming differs from STX — see the <span class="info-icon" style="vertical-align:-4px" onclick="toggleTooltip(event,'funnel-tip')">i</span> tooltip.
&nbsp;·&nbsp; BFM brands covering 95% of revenue keep their name; the tail shows as '(other)'.
<div class="tooltip" id="funnel-tip">Beachfront uses a different funnel naming convention (Swap, Dec-2025):

• bids = outgoing_bids (input bids): bids the DSP sends to the SSP. Top of the demand funnel.
• wins = total_bids_placed: bids returned by DSPs.
• requests = ads_served: placed − rejected ≈ auction wins.
Funnel: outgoing_bids &gt; total_bids_placed &gt; ads_served &gt; impressions.

⚠️ These BFM columns are NOT 1:1 comparable with STX requests / bids / wins (SSP naming).
Deal states (new / no bids / monetizing / went dark) are computed per origin with its own columns.</div>
</div>

<div class="card" id="filters-card">
  <div class="card-header">🔎 Filters <span class="muted" style="font-weight:400">— pick the partner (curator) first · options cascade · sorted by revenue</span>
    <span class="muted" id="filters-summary" style="font-weight:600"></span>
    <div class="spacer"></div>
    <span class="flabel">Currency</span>
    <button class="seg-btn cur-btn active" data-cur="eur" onclick="setCurMode('eur')">EUR</button>
    <button class="seg-btn cur-btn" data-cur="lc" onclick="setCurMode('lc')">Local</button>
    <span class="flabel" style="margin-left:10px">Period</span>
    <div class="ms-wrap">
      <div class="ms-trigger" id="cal-trigger" onclick="calToggle(event)" style="min-width:215px">
        <span class="ms-label" id="cal-label" style="max-width:none">—</span><span class="ms-arrow">📅</span>
      </div>
      <div class="ms-dropdown" id="cal-dd" style="min-width:auto;max-width:none;left:auto;right:0;padding:10px">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px">
          <button class="mini-btn" id="cal-prev" onclick="calNav(-1,event)">‹ prev</button>
          <button class="mini-btn" id="cal-next" onclick="calNav(1,event)">next ›</button>
        </div>
        <div id="cal-months" style="display:flex;gap:16px"></div>
        <div class="ms-footer" style="justify-content:space-between;align-items:center">
          <span class="muted" id="cal-hint" style="font-size:11px"></span>
          <button onclick="calAll(event)">Full window</button>
        </div>
      </div>
    </div>
    <button class="mini-btn" onclick="clearAllFilters()">Clear all</button>
    <div class="ms-wrap">
      <div class="ms-trigger" id="views-trigger" onclick="viewsToggle(event)" style="min-width:130px">
        <span class="ms-label" id="views-label">⭐ Views</span><span class="ms-arrow">▼</span>
      </div>
      <div class="ms-dropdown" id="views-dd" style="min-width:300px" onclick="event.stopPropagation()">
        <div class="ms-options" id="views-list"></div>
        <div class="ms-footer" style="flex-direction:column;align-items:stretch;gap:8px">
          <input class="email-box" id="view-name" placeholder="Name this view…" style="min-width:0;font-size:12px;padding:6px 10px">
          <span class="ms-btns" style="display:flex;justify-content:space-between">
            <button onclick="viewSave(event)">💾 Save current view</button>
            <button onclick="viewShare(event)" title="Copy a link that opens the dashboard with the current view applied">🔗 Copy link</button>
          </span>
          <span class="muted" id="views-sync" style="font-size:10px"></span>
        </div>
      </div>
    </div>
  </div>
  <div id="filters-body">
    <div class="curator-row" id="filter-curator"></div>
    <div class="filter-sep">More filters</div>
    <div class="filter-grid" id="filter-grid"></div>
  </div>
</div>

<div class="kpi-row" id="kpis"></div>

<div class="card">
  <div class="card-header">🩺 Health by
    <button class="seg-btn mxb active" data-f="business_line" onclick="setMatrixBy('business_line')">Business line</button>
    <button class="seg-btn mxb" data-f="agency" onclick="setMatrixBy('agency')">Partner (curator)</button>
    <button class="seg-btn mxb" data-f="agency_group_name" onclick="setMatrixBy('agency_group_name')">Partner group</button>
    <button class="seg-btn mxb" data-f="dsp" onclick="setMatrixBy('dsp')">DSP</button>
    <button class="seg-btn mxb" data-f="owner" onclick="setMatrixBy('owner')">Owner</button>
    <span class="muted" style="font-weight:400">— click any count to filter · a deal spanning several groups is counted in each</span>
  </div>
  <div class="table-wrapper"><table id="matrix"><thead id="mx-head"></thead><tbody id="mx-body"></tbody></table></div>
</div>

<div class="card">
  <div class="card-header">🆕 New deals by owner
    <span class="muted" style="font-weight:400">— deals first seen each week (week starts Monday) · respects filters & date range</span>
    <div class="spacer"></div>
    <button class="mini-btn" id="nd-toggle" onclick="toggleNewDeals()">▲ Hide</button>
  </div>
  <div id="nd-body">
    <div class="table-wrapper"><table id="nd-table"><thead id="nd-head"></thead><tbody id="nd-tbody"></tbody></table></div>
  </div>
</div>

<div class="card">
  <div class="card-header">📈 Evolution
    <span class="muted" style="font-weight:400">— daily, respects filters & state selection</span>
    <div class="spacer"></div>
    <span class="flabel">Metric</span>
    <select class="sel-box" id="chart-metric" onchange="buildChart()"></select>
    <span class="flabel" style="margin-left:8px">Grain</span>
    <button class="seg-btn active cgb" data-g="day" onclick="setChartGran('day')">Day</button>
    <button class="seg-btn cgb" data-g="week" onclick="setChartGran('week')">Week</button>
    <button class="seg-btn cgb" data-g="month" onclick="setChartGran('month')">Month</button>
    <span class="flabel" style="margin-left:8px">Chart</span>
    <button class="seg-btn active" id="ct-bar" onclick="setChartType('bar')">Bars</button>
    <button class="seg-btn" id="ct-line" onclick="setChartType('line')">Lines</button>
    <span class="flabel" style="margin-left:8px">Scale</span>
    <button class="seg-btn active" id="sc-lin" onclick="setChartScale('linear')">Linear</button>
    <button class="seg-btn" id="sc-log" onclick="setChartScale('log')">Log</button>
    <span class="flabel" style="margin-left:8px">Color by</span>
    <button class="seg-btn active" id="cb-origin" onclick="setColorBy('origin')">Origin</button>
    <button class="seg-btn" id="cb-bl" onclick="setColorBy('business_line')">Business line</button>
  </div>
  <div id="chart-wrap" style="position:relative">
    <div id="chart"></div>
    <div id="chart-tip" style="display:none;position:absolute;z-index:50;background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:8px 12px;font-size:12px;box-shadow:0 8px 24px rgba(0,0,0,.18);pointer-events:none;white-space:nowrap"></div>
  </div>
  <div class="chart-legend" id="chart-legend"></div>
</div>

<div class="view-switch">
  <div class="view-card active" id="vsw-details" onclick="setTableTab('details')">
    <div class="vsw-icon">📋</div>
    <div><div class="vsw-title">Details</div>
         <div class="vsw-sub">row-level table — choose fields &amp; metrics</div></div>
  </div>
  <div class="view-card" id="vsw-evo" onclick="setTableTab('evo')">
    <div class="vsw-icon">📈</div>
    <div><div class="vsw-title">Evolution</div>
         <div class="vsw-sub">periods as columns — choose segments &amp; metrics</div></div>
  </div>
</div>

<div class="card" id="cfg-details">
  <div class="card-header">🧩 Build your view
    <span class="muted" style="font-weight:400">— fields = table grain (date always included); metrics are SUM-aggregated
      <span class="info-icon" style="vertical-align:-4px;margin-left:4px" onclick="toggleTooltip(event,'metric-tip')">i</span>
      <div class="tooltip" id="metric-tip">Money metrics follow the EUR/Local toggle. requests / bids / wins: BFM uses Beachfront's funnel columns — not 1:1 with STX. curator_margin_*: STX only; split % from Salesforce. platform_spend: STX only (the delivery table's gross).</div>
    </span>
  </div>
  <div class="picker-grid">
    <div>
      <div class="flabel" style="margin-bottom:8px">Fields (dimensions)</div>
      <div class="picker-opts" id="field-picker"></div>
      <div class="picker-actions">
        <button class="mini-btn" onclick="pickAll('field',true)">All</button>
        <button class="mini-btn" onclick="pickAll('field',false)">None</button>
      </div>
    </div>
    <div>
      <div class="flabel" style="margin-bottom:8px">Metrics</div>
      <div class="picker-opts" id="metric-picker"></div>
      <div class="picker-actions">
        <button class="mini-btn" onclick="pickAll('metric',true)">All</button>
        <button class="mini-btn" onclick="pickAll('metric',false)">None</button>
      </div>
    </div>
  </div>
</div>

<div class="card" id="cfg-evo" style="display:none">
  <div class="card-header">🧩 Build your evolution
    <span class="muted" style="font-weight:400">— segments become rows, periods become columns; derived metrics are ratio-of-sums per period</span>
  </div>
  <div class="picker-grid">
    <div>
      <div class="flabel" style="margin-bottom:8px">Segments (rows — max 2)</div>
      <div class="picker-opts" id="evd-rowbtns"></div>
    </div>
    <div>
      <div class="flabel" style="margin-bottom:8px">Metrics (max 3)</div>
      <div class="picker-opts" id="evd-metbtns"></div>
      <div class="picker-actions">
        <span class="flabel" style="align-self:center">Grain</span>
        <select class="sel-box" id="evd-gran" onchange="evdSetGran(this.value)">
          <option value="day">Daily</option><option value="week">Weekly</option>
          <option value="month" selected>Monthly</option><option value="quarter">Quarterly</option>
        </select>
      </div>
    </div>
  </div>
</div>

<div class="card">
  <div class="card-header">📋 Deals
    <span class="muted" style="font-weight:400" id="tbl-hint">— click ▸ on any row to see its full-window evolution</span>
    <span id="state-chips"></span>
    <div class="spacer"></div>
    <input class="email-box" id="email-to" type="email" placeholder="colleague@seedtag.com">
    <button class="btn-csv" onclick="prepareEmail()">✉️ Prepare email</button>
    <button class="btn-csv" id="csv-btn" onclick="tableCSV()">📥 Download CSV</button>
  </div>
  <div id="pane-details">
    <div class="table-wrapper tbl-scroll"><table><thead id="tbl-head"></thead><tbody id="tbl-body"></tbody></table></div>
    <div class="table-meta"><span class="count" id="tbl-count"></span><div class="pagination" id="tbl-pag"></div></div>
  </div>
  <div id="pane-evo" style="display:none">
    <div class="table-wrapper tbl-scroll"><table><thead id="evd-head"></thead><tbody id="evd-body"></tbody></table></div>
    <div class="table-meta"><span class="count" id="evd-count"></span></div>
  </div>
</div>

</div>

<footer class="report-footer">
  <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="20" height="20" aria-label="Seedtag">
    <circle cx="50" cy="50" r="50" fill="#FF6B7C"/><circle cx="50" cy="27" r="10" fill="white"/>
    <path d="M50,54 C47,47 16,49 15,65 C14,79 35,84 50,79Z" fill="white"/>
    <path d="M50,54 C53,47 84,49 85,65 C86,79 65,84 50,79Z" fill="white"/>
  </svg>
  Analytics Team &middot; Deals Health Report &middot; <span id="ftr-range"></span>
</footer>

<script>
'use strict';
// unpack: columnar + dictionary payload → list of row objects (see _pack)
function unpack(p){
  const cols=Object.keys(p.c), out=new Array(p.n);
  for(let i=0;i<p.n;i++){
    const o={};
    for(const c of cols){ const v=p.c[c][i]; o[c]=p.d[c]?p.d[c][v]:v; }
    out[i]=o;
  }
  return out;
}
// Data is embedded gzipped+base64; inflate with the native DecompressionStream
// (any modern browser). Everything data-dependent initializes in boot().
const ROWS_B64="__ROWS_B64__";
const SQL_TEXT=__SQL_JSON__;
const GRAIN=__GRAIN_JSON__;
// Pure-JS gzip inflate fallback (puff-style) — used when DecompressionStream is
// unavailable or blocked (e.g. the Apps Script iframe sandbox). Slower but correct.
function gunzipJS(src){
  let p=10; const flg=src[3];
  if(flg&4){p+=2+(src[p]|src[p+1]<<8);}
  if(flg&8){while(src[p++]!==0);}
  if(flg&16){while(src[p++]!==0);}
  if(flg&2){p+=2;}
  let out=new Uint8Array(1<<22), olen=0;
  const ensure=n=>{ if(olen+n>out.length){ let s=out.length; while(olen+n>s)s*=2; const t=new Uint8Array(s); t.set(out.subarray(0,olen)); out=t; } };
  let bitbuf=0,bitcnt=0;
  const bits=n=>{ while(bitcnt<n){bitbuf|=src[p++]<<bitcnt;bitcnt+=8;} const v=bitbuf&((1<<n)-1); bitbuf>>>=n; bitcnt-=n; return v; };
  function buildHuff(lengths){
    const count=new Array(16).fill(0); for(const l of lengths)count[l]++;
    count[0]=0;
    const offs=new Array(16).fill(0);
    for(let i=1;i<16;i++)offs[i]=offs[i-1]+count[i-1];
    const symbol=new Array(lengths.length);
    lengths.forEach((l,s)=>{if(l)symbol[offs[l]++]=s;});
    return {count,symbol};
  }
  function decode(h){
    let code=0,first=0,index=0;
    for(let len=1;len<16;len++){
      code|=bits(1);
      const c=h.count[len];
      if(code-first<c)return h.symbol[index+(code-first)];
      index+=c; first=(first+c)<<1; code<<=1;
    }
    throw new Error('bad huffman code');
  }
  const LBASE=[3,4,5,6,7,8,9,10,11,13,15,17,19,23,27,31,35,43,51,59,67,83,99,115,131,163,195,227,258];
  const LEXT=[0,0,0,0,0,0,0,0,1,1,1,1,2,2,2,2,3,3,3,3,4,4,4,4,5,5,5,5,0];
  const DBASE=[1,2,3,4,5,7,9,13,17,25,33,49,65,97,129,193,257,385,513,769,1025,1537,2049,3073,4097,6145,8193,12289,16385,24577];
  const DEXT=[0,0,0,0,1,1,2,2,3,3,4,4,5,5,6,6,7,7,8,8,9,9,10,10,11,11,12,12,13,13];
  let fixedL=null,fixedD=null;
  for(;;){
    const last=bits(1), type=bits(2);
    if(type===0){
      bitbuf=0;bitcnt=0;
      const len=src[p]|src[p+1]<<8; p+=4;
      ensure(len); out.set(src.subarray(p,p+len),olen); olen+=len; p+=len;
    }else{
      let hl,hd;
      if(type===1){
        if(!fixedL){
          const l=new Array(288);
          for(let i=0;i<144;i++)l[i]=8; for(let i=144;i<256;i++)l[i]=9;
          for(let i=256;i<280;i++)l[i]=7; for(let i=280;i<288;i++)l[i]=8;
          fixedL=buildHuff(l); fixedD=buildHuff(new Array(30).fill(5));
        }
        hl=fixedL; hd=fixedD;
      }else{
        const hlit=bits(5)+257, hdist=bits(5)+1, hclen=bits(4)+4;
        const ord=[16,17,18,0,8,7,9,6,10,5,11,4,12,3,13,2,14,1,15];
        const cl=new Array(19).fill(0);
        for(let i=0;i<hclen;i++)cl[ord[i]]=bits(3);
        const clh=buildHuff(cl);
        const lens=[];
        while(lens.length<hlit+hdist){
          const s=decode(clh);
          if(s<16)lens.push(s);
          else if(s===16){const prev=lens[lens.length-1];let n=bits(2)+3;while(n--)lens.push(prev);}
          else if(s===17){let n=bits(3)+3;while(n--)lens.push(0);}
          else {let n=bits(7)+11;while(n--)lens.push(0);}
        }
        hl=buildHuff(lens.slice(0,hlit)); hd=buildHuff(lens.slice(hlit));
      }
      for(;;){
        const s=decode(hl);
        if(s<256){ensure(1);out[olen++]=s;}
        else if(s===256)break;
        else{
          const li=s-257, len=LBASE[li]+bits(LEXT[li]);
          const ds=decode(hd), dist=DBASE[ds]+bits(DEXT[ds]);
          ensure(len);
          let from=olen-dist;
          for(let i=0;i<len;i++)out[olen++]=out[from++];
        }
      }
    }
    if(last)break;
  }
  return out.subarray(0,olen);
}
async function inflate(b64){
  const bytes=Uint8Array.from(atob(b64),c=>c.charCodeAt(0));
  let text;
  try{
    if(typeof DecompressionStream!=='function')throw new Error('no DecompressionStream');
    const stream=new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'));
    text=await new Response(stream).text();
  }catch(e){
    text=new TextDecoder().decode(gunzipJS(bytes));
  }
  return JSON.parse(text);
}
let ROWS=[], DAYS=[];
function initData(){
  DAYS=[...new Set(ROWS.map(r=>String(r.date).slice(0,10)))].sort();
  ROWS.forEach(r=>{r.date=String(r.date).slice(0,10); if(r.first_seen)r.first_seen=String(r.first_seen).slice(0,10);});
}

/* ══════════════ config ══════════════ */
const FIELDS=['deal_id','salesforce_crm_id','currency','deal_name','name_source','business_line','brand','agency_group_name','agency','channel_id','dsp','connection_type','seat_id','country_served','country_sold','owner','am_csm','inventory_type','format','record_type','first_seen'];
const TABLE_FIELDS=['date',...FIELDS];   // date is a table dimension, not a dropdown filter
// display names (data keys unchanged): Agency → Partner
const FIELD_LABELS={agency:'partner',agency_group_name:'partner group'};
const fLabel=f=>FIELD_LABELS[f]||f.replace(/_/g,' ');
const MONEY_BASE=['platform_spend','gross_revenue','pub_cost','curator_margin_total','curator_margin_stx','curator_margin_curator','margin'];
const METRICS=[...MONEY_BASE,'requests','bids','wins','impressions','sf_product_lines'];
// Derived rate metrics — never stored: computed as RATIO OF SUMS at render time.
const DERIVED={
  cpm:       {num:'gross_revenue', den:'impressions', mult:1000, kind:'money'},
  bid_rate:  {num:'bids',          den:'requests',    mult:100,  kind:'pct'},
  win_rate:  {num:'impressions',   den:'bids',        mult:100,  kind:'pct'},
  margin_pct:{num:'margin',        den:'gross_revenue',mult:100, kind:'pct'},
};
const DERIVED_METRICS=Object.keys(DERIVED);
const ALL_METRICS=[...METRICS,...DERIVED_METRICS];
function fmtMetric(m,v){
  if(v==null)return '—';
  const d=DERIVED[m];
  if(d)return d.kind==='pct'?v.toFixed(2)+'%':fmtMoney(v);
  return MONEY_BASE.includes(m)?fmtMoney(v):fmtInt(Math.round(v));
}
const CHART_COLORS=['#5476FF','#E866F4','#948A8A','#67C8FE','#FFA071','#A36AFF','#F4D56D'];
const STATES={
  new:       {label:'🆕 New',        dot:'dot-new',  desc:'first appearance falls inside the period'},
  monetizing:{label:'💰 Monetizing', dot:'dot-mon',  desc:'gross revenue > 0 in period'},
  no_bids:   {label:'⚠️ No bids',    dot:'dot-nobid',desc:'requests > 0 and 0 bids in period'},
  no_requests:{label:'🚫 No requests',dot:'dot-noreq',desc:'active in period but 0 requests'},
  no_imps:   {label:'👁 No impressions',dot:'dot-noimp',desc:'requests > 0 and 0 impressions in period'},
  dark:      {label:'🔻 Went dark',  dot:'dot-dark', desc:'monetized in the previous window, no activity now (table shows their previous-window rows)'},
};

const selFields=new Set(['date','deal_id','deal_name','business_line','agency','dsp']);
const selMetrics=new Set(['gross_revenue','margin','requests','bids','impressions']);
const selected={}; FIELDS.forEach(f=>selected[f]=new Set());
let curMode='eur', colorBy='origin', chartType='bar', chartScale='linear', sortCol=null, sortDir=-1, page=1, stateFilter=null;
let matrixBy='business_line', drillKey=null, drillGran='day';
let rangeFrom=null, rangeTo=null;   // explicit calendar range (overrides the last-N presets)
let _chartCtx=null;   // last-rendered chart data, for the hover tooltip
const PAGE_SIZE=25;

// money metric → the actual embedded column for the current currency mode
const col=m=>MONEY_BASE.includes(m)?m+'_'+curMode:m;
const fmtMoney=n=>n==null?'—':(curMode==='eur'?'€':'')+Number(n).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});
// KPI card: sin decimales (el importe completo con centimos desbordaba la tarjeta)
const fmtMoney0=n=>n==null?'—':(curMode==='eur'?'€':'')+Math.round(Number(n)).toLocaleString('en-US',{maximumFractionDigits:0});
const fmtInt=n=>n==null?'—':Number(n).toLocaleString('en-US');
const escapeHtml=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

/* ══════════════ period ══════════════ */
const activeDays=()=>DAYS.filter(d=>d>=rangeFrom&&d<=rangeTo);
// current-window bounds; a row is "in period" iff lo<=date<=hi
function curWin(){const d=activeDays();return {lo:d[0]||null,hi:d.length?d[d.length-1]:null};}
const inWinR=(r,w)=>(!w.lo)||(r.date>=w.lo&&r.date<=w.hi);
// previous window of equal length ENDING right before the current one ("went dark")
function prevWindow(){
  const d=activeDays(); if(!d.length)return null;
  const i=DAYS.indexOf(d[0]); if(i<=0)return null;
  return DAYS.slice(Math.max(0,i-d.length),i);
}
/* single range calendar — click a start day, then an end day */
let calStart=null, calPage=null;   // calPage = index of the LEFT visible month
let MIND, MAXD, CAL_MONTHS=[];
function calNav(step,e){
  e&&e.stopPropagation();
  calPage=Math.max(0,Math.min(CAL_MONTHS.length-2,calPage+step*2));
  calRender();
}
function calToggle(e){
  e&&e.stopPropagation(); viewsClose();
  FIELDS.forEach(x=>{document.getElementById('ms-dd-'+x)?.classList.remove('open');document.getElementById('ms-trig-'+x)?.classList.remove('open');});
  const dd=document.getElementById('cal-dd');
  dd.classList.toggle('open');
  document.getElementById('cal-trigger').classList.toggle('open');
  if(dd.classList.contains('open')){
    const i=CAL_MONTHS.indexOf((rangeFrom||MAXD).slice(0,7));
    calPage=Math.max(0,Math.min(i<0?CAL_MONTHS.length-2:i,CAL_MONTHS.length-2));
  }
  calRender();
}
function calClose(){
  document.getElementById('cal-dd').classList.remove('open');
  document.getElementById('cal-trigger').classList.remove('open');
}
function calRender(){
  if(calPage===null)calPage=Math.max(0,CAL_MONTHS.length-2);   // open on the two latest months
  const months=CAL_MONTHS.slice(calPage,calPage+2);
  document.getElementById('cal-prev').disabled=calPage<=0;
  document.getElementById('cal-next').disabled=calPage>=CAL_MONTHS.length-2;
  const dow=['Mo','Tu','We','Th','Fr','Sa','Su'];
  document.getElementById('cal-months').innerHTML=months.map(mo=>{
    const [y,m]=mo.split('-').map(Number);
    const first=new Date(Date.UTC(y,m-1,1));
    const startPad=(first.getUTCDay()+6)%7;
    const nDays=new Date(Date.UTC(y,m,0)).getUTCDate();
    let cells='';
    for(let i=0;i<startPad;i++)cells+='<td></td>';
    for(let d=1;d<=nDays;d++){
      const iso=mo+'-'+String(d).padStart(2,'0');
      const ok=iso>=MIND&&iso<=MAXD;
      const endp=iso===rangeFrom||iso===rangeTo||(calStart&&iso===calStart);
      const inr=!endp&&!calStart&&iso>rangeFrom&&iso<rangeTo;
      cells+=`<td><div class="cal-day ${ok?'':'dis'} ${endp?'endp':''} ${inr?'inr':''}" ${ok?`onclick="calPick('${iso}',event)"`:''}>${d}</div></td>`;
      if((startPad+d)%7===0&&d<nDays)cells+='</tr><tr>';
    }
    return `<table class="cal-month"><caption>${first.toLocaleDateString('en-US',{month:'long',year:'numeric',timeZone:'UTC'})}</caption>
      <tr>${dow.map(w=>`<th>${w}</th>`).join('')}</tr><tr>${cells}</tr></table>`;
  }).join('');
  document.getElementById('cal-hint').textContent=calStart
    ? 'now pick the END date'
    : 'pick the START date (data: '+MIND+' → '+MAXD+', daily)';
}
function calPick(d,e){
  e&&e.stopPropagation();
  if(!calStart){ calStart=d; calRender(); return; }
  rangeFrom=calStart<=d?calStart:d;
  rangeTo=calStart<=d?d:calStart;
  calStart=null;
  calApply();
}
function calAll(e){
  e&&e.stopPropagation();
  rangeFrom=DAYS[0]; rangeTo=DAYS[DAYS.length-1]; calStart=null;
  calApply();
}
function calApply(){
  document.getElementById('cal-label').textContent=rangeFrom+' → '+rangeTo;
  calClose(); updateRangeLabels(); applyFilters();
}
function updateRangeLabels(){
  const d=activeDays(), lbl=d.length?d[0]+' → '+d[d.length-1]:'(no data)';
  document.getElementById('hdr-range').textContent=lbl;
  document.getElementById('ftr-range').textContent=lbl;
}
let filtersOpen=true;
// filters are always visible now (no collapse control)
function toggleFilters(){}
function updateFiltersSummary(){
  const n=FIELDS.filter(f=>selected[f].size>0).length;
  document.getElementById('filters-summary').textContent=
    (n>0)?`· ${n} filter${n>1?'s':''} active`:'';
}
function setCurMode(m){
  curMode=m;
  document.querySelectorAll('.cur-btn').forEach(b=>b.classList.toggle('active',b.dataset.cur===m));
  rebuildAll();
}

/* theme / tooltips */
document.getElementById('theme-toggle').addEventListener('click',()=>{
  const h=document.documentElement, cur=h.getAttribute('data-theme')||'auto';
  const next=cur==='auto'?'light':cur==='light'?'dark':'auto';
  h.setAttribute('data-theme',next); localStorage.setItem('seedtag-theme',next);
});
function toggleTooltip(e,id){
  e.stopPropagation();
  const t=document.getElementById(id);
  document.querySelectorAll('.tooltip.active').forEach(x=>{if(x!==t)x.classList.remove('active');});
  t.classList.toggle('active');
}
document.addEventListener('click',()=>document.querySelectorAll('.tooltip.active').forEach(t=>t.classList.remove('active')));
(function(){
  const t=document.getElementById('sql-tip');
  t.innerHTML=escapeHtml(SQL_TEXT)+'<span class="copy-hint">Click to copy</span>';
  t.addEventListener('click',()=>{
    navigator.clipboard&&navigator.clipboard.writeText(SQL_TEXT);
    const h=t.querySelector('.copy-hint'); if(h){const o=h.textContent;h.textContent='Copied ✓';setTimeout(()=>h.textContent=o,1200);}
  });
})();

/* ══════════════ filters (cascading, curator first) ══════════════ */
const CURATOR_FIELDS=['agency_group_name','agency'];
const OTHER_FIELDS=FIELDS.filter(f=>!CURATOR_FIELDS.includes(f));
// dimension filters only (no period/state) — used to build per-deal stats
function dimPass(r){return FIELDS.every(f=>{const s=selected[f]; if(s.size===0)return true; const v=r[f]; return v!=null&&s.has(v);});}

/* ══════════════ deal states ══════════════ */
// Per-deal aggregates over the current + previous windows, dimension filters applied.
let _stats=null;
function dealStats(){
  if(_stats)return _stats;
  const w=curWin(), prev=prevWindow(), prevSet=prev?new Set(prev):null;
  const m=new Map();
  for(const r of ROWS){
    if(!dimPass(r))continue;
    const inCur=inWinR(r,w);
    const inPrev=prevSet?prevSet.has(r.date):false;
    if(!inCur&&!inPrev)continue;
    let s=m.get(r.deal_id);
    if(!s){s={req:0,bids:0,imps:0,gross:0,rows:0,prevGross:0,prevRows:0,first_seen:r.first_seen}; m.set(r.deal_id,s);}
    if(inCur){s.rows++; s.req+=r.requests||0; s.bids+=r.bids||0; s.imps+=r.impressions||0; s.gross+=r['gross_revenue_'+curMode]||0;}
    else {s.prevRows++; s.prevGross+=r['gross_revenue_'+curMode]||0;}
  }
  const start=w.lo||DAYS[0];
  const sets={all:new Set(),new:new Set(),monetizing:new Set(),no_bids:new Set(),no_requests:new Set(),no_imps:new Set(),dark:new Set()};
  for(const [id,s] of m){
    if(s.rows>0){
      sets.all.add(id);
      if(s.first_seen&&s.first_seen>=start)sets.new.add(id);
      if(s.gross>0)sets.monetizing.add(id);
      if(s.req>0&&s.bids===0)sets.no_bids.add(id);
      if(s.req===0)sets.no_requests.add(id);
      // deals que piden y no sirven nada: 0 impresiones con requests > 0
      if(s.req>0&&s.imps===0)sets.no_imps.add(id);
    } else if(s.prevGross>0){
      sets.dark.add(id);
    }
  }
  _stats={map:m,sets};
  return _stats;
}
function dealState(id){
  const {sets}=dealStats();
  if(sets.dark.has(id))return 'dark';
  if(sets.no_requests.has(id))return 'no_requests';
  if(sets.no_bids.has(id))return 'no_bids';
  if(sets.no_imps.has(id))return 'no_imps';
  if(sets.monetizing.has(id))return 'monetizing';
  return null;
}

// rows feeding KPI-gross/chart/table: dimension filters + period + state selection.
// 'dark' deals have no rows in the period, so their previous-window rows are shown.
function filteredRows(){
  const w=curWin();
  if(stateFilter==='dark'){
    const prev=prevWindow(); if(!prev)return [];
    const pset=new Set(prev), deals=dealStats().sets.dark;
    return ROWS.filter(r=>dimPass(r)&&pset.has(r.date)&&deals.has(r.deal_id));
  }
  const deals=stateFilter?dealStats().sets[stateFilter]:null;
  return ROWS.filter(r=>dimPass(r)&&inWinR(r,w)&&(!deals||deals.has(r.deal_id)));
}
function chartDays(){
  if(stateFilter==='dark'){const p=prevWindow();return p||[];}
  return activeDays();
}
function setStateFilter(s){
  stateFilter=(s===null)?null:(stateFilter===s?null:s);
  page=1; rebuildAll();
}

/* cascading filter options (single pass over ROWS) */
let _optCache=null;
function computeOptionRevs(){
  const active=FIELDS.filter(f=>selected[f].size>0);
  const w=curWin();
  const revs={}; FIELDS.forEach(f=>revs[f]=new Map());
  const bump=(f,v,g)=>{ if(v==null||v==='')return; const m=revs[f]; m.set(v,(m.get(v)||0)+g); };
  const gcol='gross_revenue_'+curMode;
  for(const r of ROWS){
    if(!inWinR(r,w))continue;
    let fails=0, failField=null;
    for(const f of active){ const v=r[f]; if(v==null||!selected[f].has(v)){ if(++fails>1)break; failField=f; } }
    const g=r[gcol]||0;
    if(fails===0){ for(const f of FIELDS) bump(f,r[f],g); }
    else if(fails===1){ bump(failField,r[failField],g); }
  }
  _optCache=revs;
}
function optionsFor(f){
  if(!_optCache)computeOptionRevs();
  const opts=[..._optCache[f].entries()].sort((a,b)=>b[1]-a[1]).map(e=>e[0]);
  const pinned=[...selected[f]].filter(v=>!_optCache[f].has(v));
  return [...pinned,...opts];
}
function msHtml(f){
  return `
    <div class="filter-item">
      <span class="flabel">${fLabel(f)}</span>
      <div class="ms-wrap">
        <div class="ms-trigger" id="ms-trig-${f}" onclick="msToggle('${f}',event)">
          <span class="ms-label" id="ms-label-${f}">All</span><span class="ms-arrow">▼</span>
        </div>
        <div class="ms-dropdown" id="ms-dd-${f}">
          <div class="ms-search">
            <input type="text" placeholder="Search…" oninput="msSearch('${f}',this.value)">
            <button class="ms-x" id="ms-x-${f}" onclick="msClearSearch('${f}')" title="Clear search">✕</button>
          </div>
          <div class="ms-options" id="ms-opts-${f}"></div>
          <div class="ms-footer">
            <span class="ms-count" id="ms-count-${f}"></span>
            <span class="ms-btns">
              <button id="ms-selall-${f}" onclick="msSelectAll('${f}')">Select all</button>
              <button id="ms-clear-${f}" onclick="msClear('${f}')">Clear</button>
            </span>
          </div>
        </div>
      </div>
    </div>`;
}
function buildFilters(){
  document.getElementById('filter-curator').innerHTML=CURATOR_FIELDS.map(msHtml).join('');
  document.getElementById('filter-grid').innerHTML=OTHER_FIELDS.map(msHtml).join('');
  FIELDS.forEach(f=>buildOptions(f));
}
const MAX_OPTS=500;
function msQuery(f){
  const inp=document.querySelector(`#ms-dd-${f} .ms-search input`);
  return inp?inp.value:'';
}
// options currently visible in the dropdown = search-matched (all of them if no search)
function msVisible(f){
  const q=msQuery(f).toLowerCase();
  return optionsFor(f).filter(v=>!q||String(v).toLowerCase().includes(q));
}
function buildOptions(f,q=''){
  const raw=q; q=q.toLowerCase();
  const all=optionsFor(f).filter(v=>!q||String(v).toLowerCase().includes(q));
  const shown=all.slice(0,MAX_OPTS);
  document.getElementById('ms-opts-'+f).innerHTML=shown.map(v=>
    `<label class="ms-option" data-val="${escapeHtml(v)}">
       <input type="checkbox" ${selected[f].has(v)?'checked':''} onchange="msPick('${f}','${encodeURIComponent(v)}',this.checked)">
       <span title="${escapeHtml(v)}">${escapeHtml(v)}</span></label>`).join('')
    +(all.length>MAX_OPTS?`<div class="ms-option muted">… ${fmtInt(all.length-MAX_OPTS)} more — refine the search</div>`:'');
  // ✕ visibility, selected counter, and Select all / Clear scoped to the search results
  document.getElementById('ms-x-'+f).classList.toggle('show',raw.length>0);
  const n=selected[f].size;
  document.getElementById('ms-count-'+f).textContent=n?fmtInt(n)+' selected':'';
  document.getElementById('ms-selall-'+f).textContent=q?'Select '+fmtInt(all.length):'Select all';
  document.getElementById('ms-clear-'+f).textContent=q?'Clear these':'Clear';
}
function msToggle(f,e){
  e&&e.stopPropagation(); calClose(); viewsClose();
  FIELDS.forEach(x=>{if(x!==f){document.getElementById('ms-dd-'+x).classList.remove('open');document.getElementById('ms-trig-'+x).classList.remove('open');}});
  document.getElementById('ms-dd-'+f).classList.toggle('open');
  document.getElementById('ms-trig-'+f).classList.toggle('open');
}
document.addEventListener('click',e=>{if(!e.target.closest('.ms-wrap')){FIELDS.forEach(f=>{
  document.getElementById('ms-dd-'+f).classList.remove('open');
  document.getElementById('ms-trig-'+f).classList.remove('open');});
  calClose(); calStart=null; viewsClose();}});
function msSearch(f,q){buildOptions(f,q);}
function msClearSearch(f){
  const inp=document.querySelector(`#ms-dd-${f} .ms-search input`);
  if(inp){inp.value='';inp.focus();}
  buildOptions(f,'');
}
function msPick(f,enc,on){const v=decodeURIComponent(enc); if(on)selected[f].add(v); else selected[f].delete(v); applyFilters();}
// With an active search, Select all / Clear act only on the matching options;
// without one they act on everything.
function msSelectAll(f){msVisible(f).forEach(v=>selected[f].add(v)); applyFilters();}
function msClear(f){
  if(msQuery(f)){msVisible(f).forEach(v=>selected[f].delete(v));}
  else selected[f].clear();
  applyFilters();}
function clearAllFilters(){FIELDS.forEach(f=>selected[f].clear());stateFilter=null;applyFilters();}
function applyFilters(){
  _optCache=null; _stats=null;
  FIELDS.forEach(f=>{const s=selected[f];
    document.getElementById('ms-label-'+f).textContent=s.size===0?'All':(s.size===1?[...s][0]:s.size+' selected');
    document.getElementById('ms-trig-'+f).classList.toggle('active-filter',s.size>0);
    const inp=document.querySelector(`#ms-dd-${f} .ms-search input`);
    buildOptions(f,inp?inp.value:'');});
  updateFiltersSummary();
  page=1; rebuildAll();
}

/* ══════════════ KPI strip (clickable) ══════════════ */
function buildKpis(){
  const {sets}=dealStats();
  const rows=filteredRows();
  const gross=rows.reduce((s,r)=>s+(r['gross_revenue_'+curMode]||0),0);
  const card=(key,label,val,sub,click)=>`
    <div class="kpi-card ${click?'clickable':''} ${stateFilter===key?'active':''}" ${click?`onclick="setStateFilter('${key}')"`:''} title="${click?STATES[key]?STATES[key].desc+' · click to filter':'':''}">
      <div class="kpi-label">${label}</div><div class="kpi-value">${val}</div><div class="kpi-sub">${sub}</div></div>`;
  document.getElementById('kpis').innerHTML=
    `<div class="kpi-card clickable ${stateFilter===null?'active':''}" onclick="setStateFilter(null)" title="click to clear the state filter and see everything">
       <div class="kpi-label">Deals</div><div class="kpi-value">${fmtInt(sets.all.size)}</div><div class="kpi-sub">active in period · shows all</div></div>`+
    card('new','🆕 New deals',fmtInt(sets.new.size),'first seen in period',true)+
    card('monetizing','💰 Monetizing',fmtInt(sets.monetizing.size),'gross &gt; 0',true)+
    card('no_bids','⚠️ No bids',fmtInt(sets.no_bids.size),'requests but 0 bids',true)+
    card('no_requests','🚫 No requests',fmtInt(sets.no_requests.size),'active but 0 requests',true)+
    card('no_imps','👁 No impressions',fmtInt(sets.no_imps.size),'requests but 0 impressions',true)+
    card('dark','🔻 Went dark',fmtInt(sets.dark.size),'monetized before, silent now',true)+
    card(null,'Gross revenue',fmtMoney0(gross),(curMode==='eur'?'EUR':'local — mixed currencies')+(stateFilter?' · '+STATES[stateFilter].label:''),false);
}

/* ══════════════ health matrix ══════════════ */
const MX_LABELS={business_line:'business line',agency:'partner (curator)',agency_group_name:'partner group',dsp:'DSP',owner:'owner'};
const MX_TOP=15;
function setMatrixBy(f){
  matrixBy=f;
  document.querySelectorAll('.mxb').forEach(b=>b.classList.toggle('active',b.dataset.f===f));
  buildMatrix();
}
function buildMatrix(){
  const w=curWin(), prev=prevWindow(), pset=prev?new Set(prev):null;
  const {sets}=dealStats();
  const gcol='gross_revenue_'+curMode;
  // deal → groups it appears under (period rows; dark deals via prev rows)
  const per=new Map();
  const get=bl=>{let o=per.get(bl); if(!o){o={deals:new Set(),nw:new Set(),mon:new Set(),nob:new Set(),nor:new Set(),noi:new Set(),dark:new Set(),gross:0}; per.set(bl,o);} return o;};
  for(const r of ROWS){
    if(!dimPass(r))continue;
    const bl=r[matrixBy]||'(none)';
    const inCur=inWinR(r,w);
    if(inCur){
      const o=get(bl), id=r.deal_id;
      if(sets.all.has(id))o.deals.add(id);
      if(sets.new.has(id))o.nw.add(id);
      if(sets.monetizing.has(id))o.mon.add(id);
      if(sets.no_bids.has(id))o.nob.add(id);
      if(sets.no_requests.has(id))o.nor.add(id);
      if(sets.no_imps.has(id))o.noi.add(id);
      o.gross+=r[gcol]||0;
    } else if(pset&&pset.has(r.date)&&sets.dark.has(r.deal_id)){
      get(bl).dark.add(r.deal_id);
    }
  }
  const COLS=[['deals',null,'deals'],['nw','new','new'],['mon','monetizing','monetizing'],
              ['nob','no_bids','no bids'],['nor','no_requests','no requests'],
              ['noi','no_imps','no impressions'],['dark','dark','went dark']];
  document.getElementById('mx-head').innerHTML=`<tr><th>${MX_LABELS[matrixBy]}</th>`+
    COLS.map(c=>`<th class="number">${c[2]}</th>`).join('')+'<th class="number">gross revenue</th><th class="number">pct</th></tr>';
  let bls=[...per.entries()].sort((a,b)=>b[1].gross-a[1].gross);
  const totG=bls.reduce((s,[,o])=>s+o.gross,0);
  // high-cardinality keys (curators, DSPs): top N + one non-clickable rollup row
  let others=null, hidden=0;
  if(bls.length>MX_TOP+1){
    const tail=bls.slice(MX_TOP); bls=bls.slice(0,MX_TOP); hidden=tail.length;
    others={deals:new Set(),nw:new Set(),mon:new Set(),nob:new Set(),nor:new Set(),noi:new Set(),dark:new Set(),gross:0};
    tail.forEach(([,o])=>{['deals','nw','mon','nob','nor','noi','dark'].forEach(k=>o[k].forEach(x=>others[k].add(x))); others.gross+=o.gross;});
  }
  const cell=(o,c,bl)=>{
    const n=o[c[0]].size, st=c[1];
    const sel=st&&stateFilter===st&&selected[matrixBy].size===1&&selected[matrixBy].has(bl);
    return `<td class="cnt ${sel?'sel':''}" onclick="matrixClick('${encodeURIComponent(bl)}',${st?`'${st}'`:'null'})">${fmtInt(n)}</td>`;};
  let html=bls.map(([bl,o])=>'<tr><td title="'+escapeHtml(bl)+'">'+escapeHtml(bl)+'</td>'
    +COLS.map(c=>cell(o,c,bl)).join('')
    +`<td class="number">${fmtMoney(o.gross)}</td><td class="number">${totG?(100*o.gross/totG).toFixed(1):'0'}%</td></tr>`).join('');
  if(others){
    html+='<tr><td class="muted">(others — '+fmtInt(hidden)+' more, use the filters)</td>'
      +COLS.map(c=>`<td class="number muted">${fmtInt(others[c[0]].size)}</td>`).join('')
      +`<td class="number muted">${fmtMoney(others.gross)}</td><td class="number muted">${totG?(100*others.gross/totG).toFixed(1):'0'}%</td></tr>`;
  }
  const t={deals:new Set(),nw:new Set(),mon:new Set(),nob:new Set(),nor:new Set(),noi:new Set(),dark:new Set()};
  const allGroups=others?[...bls,['(others)',others]]:bls;
  allGroups.forEach(([,o])=>Object.keys(t).forEach(k=>o[k].forEach(x=>t[k].add(x))));
  html+='<tr class="total-row"><td>TOTAL</td>'+COLS.map(c=>`<td class="number">${fmtInt(t[c[0]].size)}</td>`).join('')
    +`<td class="number">${fmtMoney(totG)}</td><td class="number">100%</td></tr>`;
  document.getElementById('mx-body').innerHTML=html;
}
function matrixClick(blEnc,st){
  const bl=decodeURIComponent(blEnc);
  const already=selected[matrixBy].size===1&&selected[matrixBy].has(bl)&&stateFilter===st;
  selected[matrixBy].clear();
  if(!already){selected[matrixBy].add(bl); stateFilter=st;}
  else stateFilter=null;
  applyFilters();
}

/* ══════════════ new deals by owner (weekly) ══════════════ */
let ndOpen=true;
function toggleNewDeals(){
  ndOpen=!ndOpen;
  document.getElementById('nd-body').style.display=ndOpen?'':'none';
  document.getElementById('nd-toggle').textContent=ndOpen?'▲ Hide':'▼ Show';
  if(ndOpen)buildNewDeals(); // se salta el rebuild mientras está plegada
}
function weekStart(d){ // lunes de la semana ISO de una fecha YYYY-MM-DD
  const dt=new Date(d+'T00:00:00Z');
  dt.setUTCDate(dt.getUTCDate()-(dt.getUTCDay()+6)%7);
  return dt.toISOString().slice(0,10);
}
function buildNewDeals(){
  if(!ndOpen)return;
  // deals cuyo first_seen cae en el rango, agrupados por owner y semana de first_seen.
  // Respeta filtros de dimensión; el filtro de estado no aplica (es una serie temporal).
  const per=new Map(); // owner → Map(week → Set(deal_id))
  for(const r of ROWS){
    const fs=r.first_seen;
    if(!fs||fs<rangeFrom||fs>rangeTo||!dimPass(r))continue;
    const o=r.owner||'(none)', wk=weekStart(fs);
    let m=per.get(o); if(!m){m=new Map(); per.set(o,m);}
    let s=m.get(wk); if(!s){s=new Set(); m.set(wk,s);}
    s.add(r.deal_id);
  }
  // columnas: todas las semanas del rango, también las vacías
  const weeks=[];
  for(let wk=weekStart(rangeFrom);wk<=rangeTo;){
    weeks.push(wk);
    const dt=new Date(wk+'T00:00:00Z'); dt.setUTCDate(dt.getUTCDate()+7);
    wk=dt.toISOString().slice(0,10);
  }
  document.getElementById('nd-head').innerHTML='<tr><th>owner</th>'
    +weeks.map(w=>`<th class="number">${w}</th>`).join('')+'<th class="number">total</th></tr>';
  const rowTotal=m=>{const ids=new Set(); m.forEach(s=>s.forEach(id=>ids.add(id))); return ids.size;};
  const owners=[...per.entries()].sort((a,b)=>rowTotal(b[1])-rowTotal(a[1]));
  const cnt=(m,w)=>{const s=m.get(w); return s?s.size:0;};
  let html=owners.map(([o,m])=>'<tr><td title="'+escapeHtml(o)+'">'+escapeHtml(o)+'</td>'
    +weeks.map(w=>{const n=cnt(m,w);return `<td class="number${n?'':' muted'}">${n?fmtInt(n):'·'}</td>`;}).join('')
    +`<td class="number">${fmtInt(rowTotal(m))}</td></tr>`).join('');
  // total por semana: deals únicos (un deal con varios owners cuenta una vez)
  const tot=new Map(); per.forEach(m=>m.forEach((s,w)=>{let t=tot.get(w); if(!t){t=new Set();tot.set(w,t);} s.forEach(id=>t.add(id));}));
  const allIds=new Set(); tot.forEach(s=>s.forEach(id=>allIds.add(id)));
  html+='<tr class="total-row"><td>TOTAL</td>'
    +weeks.map(w=>`<td class="number">${fmtInt(tot.get(w)?tot.get(w).size:0)}</td>`).join('')
    +`<td class="number">${fmtInt(allIds.size)}</td></tr>`;
  document.getElementById('nd-tbody').innerHTML=owners.length?html:
    '<tr><td class="muted">No new deals in the selected range</td></tr>';
}

/* ══════════════ pickers ══════════════ */
function buildPickers(){
  document.getElementById('field-picker').innerHTML=TABLE_FIELDS.map(f=>
    `<label class="picker-opt"><input type="checkbox" ${selFields.has(f)?'checked':''} onchange="togglePick('field','${f}',this.checked)"><code>${fLabel(f)}</code></label>`).join('');
  const opt=m=>`<label class="picker-opt"><input type="checkbox" ${selMetrics.has(m)?'checked':''} onchange="togglePick('metric','${m}',this.checked)"><code>${m}</code></label>`;
  document.getElementById('metric-picker').innerHTML=METRICS.map(opt).join('')
    +'<span class="flabel" style="width:100%;margin-top:6px">derived (ratio of sums)</span>'
    +DERIVED_METRICS.map(opt).join('');
}
function togglePick(kind,name,on){
  const s=kind==='field'?selFields:selMetrics;
  if(on)s.add(name); else s.delete(name);
  page=1; sortCol=null; rebuildTable();
}
function pickAll(kind,on){
  const s=kind==='field'?selFields:selMetrics, all=kind==='field'?TABLE_FIELDS:ALL_METRICS;
  s.clear(); if(on)all.forEach(x=>s.add(x));
  buildPickers(); page=1; sortCol=null; rebuildTable();
}

/* ══════════════ aggregation ══════════════ */
function aggregate(){
  const dims=TABLE_FIELDS.filter(f=>selFields.has(f));
  const mets=ALL_METRICS.filter(m=>selMetrics.has(m));
  // raw columns to sum: plain metrics + the components of any derived metric
  const raw=new Set();
  mets.forEach(m=>{const d=DERIVED[m]; if(d){raw.add(d.num);raw.add(d.den);} else raw.add(m);});
  const map=new Map();
  filteredRows().forEach(r=>{
    const key=dims.map(d=>r[d]??'∅').join('␟');
    let g=map.get(key);
    if(!g){g={}; dims.forEach(d=>g[d]=r[d]); map.set(key,g);}
    raw.forEach(rm=>{const v=r[col(rm)]; if(v!=null)g['_'+rm]=(g['_'+rm]||0)+v;});
  });
  const rows=[...map.values()];
  rows.forEach(g=>mets.forEach(m=>{
    const d=DERIVED[m];
    if(d){const den=g['_'+d.den]; g[m]=den?(g['_'+d.num]||0)/den*d.mult:null;}
    else g[m]=g['_'+m]??null;
  }));
  return {dims,mets,rows};
}

/* ══════════════ chart ══════════════ */
function buildChartMetricSelect(){
  const el=document.getElementById('chart-metric');
  const opts=[...ALL_METRICS.map(m=>[m,m.replace(/_/g,' ')]),
              ['__deals','# active deals'],['__no_bids','# deals with no bids'],['__monetizing','# deals monetizing']];
  el.innerHTML=opts.map(([v,l])=>`<option value="${v}">${l}</option>`).join('');
  el.value='gross_revenue';
}
function setColorBy(v){colorBy=v;
  document.getElementById('cb-origin').classList.toggle('active',v==='origin');
  document.getElementById('cb-bl').classList.toggle('active',v==='business_line');
  buildChart();}
function setChartType(v){chartType=v;
  document.getElementById('ct-bar').classList.toggle('active',v==='bar');
  document.getElementById('ct-line').classList.toggle('active',v==='line');
  buildChart();}
function setChartScale(v){chartScale=v;
  document.getElementById('sc-lin').classList.toggle('active',v==='linear');
  document.getElementById('sc-log').classList.toggle('active',v==='log');
  buildChart();}
// chart granularity: data is daily only for the last ~60 days (GRAIN.daily_from),
// weekly back to GRAIN.weekly_from, monthly before that — day/week views clip
// to where that grain exists; month/quarter/year cover everything.
let chartGran='day';
function setChartGran(g){
  chartGran=g;
  document.querySelectorAll('.cgb').forEach(b=>b.classList.toggle('active',b.dataset.g===g));
  buildChart();
}
function bucketOf(d,g){
  if(g==='day')return d;
  if(g==='week')return weekStart(d);
  if(g==='month')return d.slice(0,7)+'-01';
  if(g==='quarter'){const m=Math.floor((+d.slice(5,7)-1)/3)*3+1;return d.slice(0,4)+'-'+String(m).padStart(2,'0')+'-01';}
  return d.slice(0,4)+'-01-01';
}
const granMin=g=>g==='day'?GRAIN.daily_from:(g==='week'?GRAIN.weekly_from:null);
function buildChart(){
  const metric=document.getElementById('chart-metric').value;
  const gran=chartGran, minD=granMin(gran);
  const rawDays=chartDays();
  const visDays=minD?rawDays.filter(d=>d>=minD):rawDays;
  const clipped=minD&&rawDays.length&&rawDays[0]<minD;
  const DAYSw=[...new Set(visDays.map(d=>bucketOf(d,gran)))].sort();
  const rows=filteredRows().filter(r=>!minD||r.date>=minD);
  const isCount=metric.startsWith('__');
  const dv=DERIVED[metric];
  const series=new Map(); // key -> day -> number | Set (counts) | {n,d} (derived)
  rows.forEach(r=>{
    const k=colorBy==='origin'?r.origin:(r.business_line||'(none)');
    if(!series.has(k))series.set(k,new Map());
    const m=series.get(k);
    if(isCount){
      const req=r.requests||0,b=r.bids||0,g=r['gross_revenue_'+curMode]||0;
      const ok=metric==='__deals'||(metric==='__no_bids'?(req>0&&b===0):g>0);
      const bk=bucketOf(r.date,gran);
      if(ok){let s=m.get(bk); if(!s){s=new Set();m.set(bk,s);} s.add(r.deal_id);}
    } else if(dv){
      const bk=bucketOf(r.date,gran);
      let o=m.get(bk); if(!o){o={n:0,d:0};m.set(bk,o);}
      o.n+=r[col(dv.num)]||0; o.d+=r[col(dv.den)]||0;
    } else {
      const bk=bucketOf(r.date,gran);
      m.set(bk,(m.get(bk)||0)+(r[col(metric)]||0));
    }
  });
  const val=(k,d)=>{const v=series.get(k).get(d); if(v==null)return 0;
    if(isCount)return v.size; if(dv)return v.d?v.n/v.d*dv.mult:0; return v;};
  const keys=[...series.keys()].sort((a,b)=>{
    const t=k=>DAYSw.reduce((s,d)=>s+val(k,d),0); return t(b)-t(a);});
  const isMoney=dv?dv.kind==='money':MONEY_BASE.includes(metric);
  const isLog=chartScale==='log';
  const drawBars=chartType==='bar'&&!isLog;  // stacked bars are meaningless in log space → lines
  const W=1100,H=280,padL=70,padR=16,padT=14,padB=34;
  const dayTotals=DAYSw.map(d=>keys.reduce((s,k)=>s+val(k,d),0));
  const maxSeries=Math.max(1,...keys.flatMap(k=>DAYSw.map(d=>val(k,d))));
  const maxY=(drawBars?Math.max(...dayTotals,1):maxSeries)*1.08;
  const bw=(W-padL-padR)/Math.max(DAYSw.length,1), barW=Math.min(64,bw*0.62);
  const L=v=>Math.log10(Math.max(v,0)+1);
  const yOf=isLog?(v=>H-padB-(H-padT-padB)*L(v)/L(maxY)):(v=>H-padB-(H-padT-padB)*v/maxY);
  const fmtV=v=>(dv&&dv.kind==='pct')?v.toFixed(2)+'%':(isMoney?fmtMoney(v):fmtInt(Math.round(v)));
  const shortN=v=>v>=1e9?(v/1e9).toFixed(0)+'B':v>=1e6?(v/1e6).toFixed(0)+'M':v>=1000?(v/1000).toFixed(0)+'k':v.toFixed(0);
  let svg=`<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto" xmlns="http://www.w3.org/2000/svg" font-family="Instrument Sans,sans-serif">`;
  if(isLog){
    for(let e=0;Math.pow(10,e)<=maxY;e++){
      const v=Math.pow(10,e), y=yOf(v);
      svg+=`<line x1="${padL}" y1="${y}" x2="${W-padR}" y2="${y}" stroke="var(--border)" stroke-width="1"/>`;
      svg+=`<text x="${padL-8}" y="${y+4}" text-anchor="end" font-size="11" fill="var(--text-subtle)">${shortN(v)}</text>`;
    }
  } else {
    for(let i=0;i<=4;i++){
      const y=padT+(H-padT-padB)*i/4, v=maxY*(1-i/4);
      svg+=`<line x1="${padL}" y1="${y}" x2="${W-padR}" y2="${y}" stroke="var(--border)" stroke-width="1"/>`;
      svg+=`<text x="${padL-8}" y="${y+4}" text-anchor="end" font-size="11" fill="var(--text-subtle)">${shortN(v)}</text>`;
    }
  }
  const lblEvery=Math.max(1,Math.ceil(DAYSw.length/16));
  DAYSw.forEach((d,i)=>{
    if(i%lblEvery!==0&&i!==DAYSw.length-1)return;
    svg+=`<text x="${padL+bw*i+bw/2}" y="${H-padB+16}" text-anchor="middle" font-size="11" fill="var(--text-muted)">${d.slice(5)}</text>`;
  });
  if(drawBars){
    DAYSw.forEach((d,i)=>{
      const x0=padL+bw*i+(bw-barW)/2; let yCur=H-padB;
      keys.forEach((k,ki)=>{
        const v=val(k,d); if(v<=0)return;
        const h=(H-padT-padB)*v/maxY; yCur-=h;
        svg+=`<rect x="${x0}" y="${yCur}" width="${barW}" height="${h}" fill="${CHART_COLORS[ki%CHART_COLORS.length]}" rx="2"><title>${d} · ${escapeHtml(k)} · ${fmtV(v)}</title></rect>`;
      });
      if(DAYSw.length<=31) svg+=`<text x="${x0+barW/2}" y="${Math.max(padT+10,yOf(dayTotals[i])-6)}" text-anchor="middle" font-size="10" fill="var(--text-subtle)">${dayTotals[i]>=1000?(dayTotals[i]/1000).toFixed(1)+'k':dayTotals[i].toFixed(0)}</text>`;
    });
  } else {
    keys.forEach((k,ki)=>{
      const c=CHART_COLORS[ki%CHART_COLORS.length];
      const pts=DAYSw.map((d,i)=>[padL+bw*i+bw/2,yOf(val(k,d))]);
      svg+=`<polyline points="${pts.map(p=>p[0].toFixed(1)+','+p[1].toFixed(1)).join(' ')}" fill="none" stroke="${c}" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>`;
      pts.forEach((p,i)=>{svg+=`<circle cx="${p[0].toFixed(1)}" cy="${p[1].toFixed(1)}" r="${DAYSw.length>90?1.5:3.5}" fill="${c}"><title>${DAYSw[i]} · ${escapeHtml(k)} · ${fmtV(val(k,DAYSw[i]))}</title></circle>`;});
    });
  }
  svg+='</svg>';
  document.getElementById('chart').innerHTML=svg;
  _chartCtx={days:DAYSw,keys,val,fmtV,W,padL,padR};
  const sTot=keys.map(k=>DAYSw.reduce((a,d)=>a+val(k,d),0)).filter(v=>v>0);
  const ratio=sTot.length>1?Math.max(...sTot)/Math.min(...sTot):1;
  const hint=(!isLog&&ratio>100)?` <span style="color:var(--accent);font-weight:600">⚠ series differ by ~10^${Math.round(Math.log10(ratio))} — the small one is invisible; use Log scale or filter by origin</span>`:'';
  document.getElementById('chart-legend').innerHTML=keys.map((k,i)=>
    `<span class="li"><span class="sw" style="background:${CHART_COLORS[i%CHART_COLORS.length]}"></span>${escapeHtml(k)}</span>`).join('')
    +`<span class="li muted">· per ${gran} · ${dv&&dv.kind==='pct'?'% (ratio of sums)':isMoney?(curMode==='eur'?'EUR':'local currency (mixed)'):(isCount?'distinct deals':'units')}${isLog?' · log scale'+(chartType==='bar'?' (bars shown as lines)':''):''}${stateFilter==='dark'?' · showing previous window':''}${clipped?` · ⚠️ ${gran} grain only exists since ${minD} — earlier range clipped (use Month/Quarter/Year)`:''}</span>`+hint;
}

/* ══════════════ state chips ══════════════ */
function buildChips(){
  const {sets}=dealStats();
  document.getElementById('state-chips').innerHTML=
    `<button class="chip ${stateFilter===null?'active':''}" onclick="setStateFilter(null)">All</button>`+
    Object.entries(STATES).map(([k,v])=>
      `<button class="chip ${stateFilter===k?'active':''}" onclick="setStateFilter('${k}')" title="${v.desc}">${v.label} (${fmtInt(sets[k].size)})</button>`).join(' ');
}

/* ══════════════ table ══════════════ */
function renderPagination(elId,pages,cur,go){
  const el=document.getElementById(elId);
  if(pages<=1){el.innerHTML='';return;}
  const range=[];const delta=2;
  for(let i=Math.max(1,cur-delta);i<=Math.min(pages,cur+delta);i++)range.push(i);
  if(range[0]>1)range.unshift(1); if(range[range.length-1]<pages)range.push(pages);
  let html=`<button ${cur===1?'disabled':''} data-p="${cur-1}">←</button>`; let last=0;
  range.forEach(p=>{if(p-last>1)html+=`<span class="pg-info">…</span>`;html+=`<button class="${p===cur?'active':''}" data-p="${p}">${p}</button>`;last=p;});
  html+=`<button ${cur===pages?'disabled':''} data-p="${cur+1}">→</button><span class="pg-info">${cur} / ${pages}</span>`;
  el.innerHTML=html;
  el.querySelectorAll('button[data-p]').forEach(b=>b.onclick=()=>{const p=+b.dataset.p;if(p>=1&&p<=pages)go(p);});
}
function sortBy(c){
  if(sortCol===c)sortDir=-sortDir; else {sortCol=c;sortDir=-1;}
  page=1; rebuildTable();
}
function currentView(){
  const {dims,mets,rows}=aggregate();
  const withState=selFields.has('deal_id');
  const cols=[...dims,...(withState?['state']:[]),...mets];
  let sorted;
  if(sortCol&&cols.includes(sortCol)&&sortCol!=='state'){
    const isMet=ALL_METRICS.includes(sortCol);
    sorted=[...rows].sort((a,b)=>{
      const va=a[sortCol],vb=b[sortCol];
      if(va==null&&vb==null)return 0; if(va==null)return 1; if(vb==null)return -1;
      return isMet?(va-vb)*sortDir:String(va).localeCompare(String(vb))*sortDir;});
  } else if(dims.includes('date')){
    sorted=[...rows].sort((a,b)=>a.date<b.date?-1:a.date>b.date?1:((b[mets[0]]||0)-(a[mets[0]]||0)));
  } else {
    sorted=mets.length?[...rows].sort((a,b)=>(b[mets[0]]||0)-(a[mets[0]]||0)):rows;
  }
  return {dims,mets,cols,rows:sorted,withState};
}
function stateBadge(id){
  if(dealStats().sets.new.has(id)) {
    const s=dealState(id);
    return `<span class="dot dot-new"></span>new${s?' · '+s.replace(/_/g,' '):''}`;
  }
  const s=dealState(id);
  if(!s)return '—';
  return `<span class="dot ${STATES[s].dot}"></span>${s.replace(/_/g,' ')}`;
}
function rebuildTable(){
  const {dims,mets,cols,rows,withState}=currentView();
  document.getElementById('tbl-head').innerHTML='<tr><th style="width:26px"></th>'+cols.map(c=>
    `<th class="${ALL_METRICS.includes(c)?'number':''}" onclick="sortBy('${c}')" title="click to sort">${fLabel(c)} ${sortCol===c?(sortDir<0?'▼':'▲'):'<span style="opacity:.35">↕</span>'}</th>`).join('')+'</tr>';
  const pages=Math.max(1,Math.ceil(rows.length/PAGE_SIZE));
  if(page>pages)page=pages;
  const slice=rows.slice((page-1)*PAGE_SIZE,page*PAGE_SIZE);
  const cell=(r,c)=>{
    if(c==='state')return `<td>${stateBadge(r.deal_id)}</td>`;
    const v=r[c];
    if(ALL_METRICS.includes(c))return `<td class="number">${fmtMetric(c,v)}</td>`;
    return `<td class="${v==null?'muted':''}" title="${escapeHtml(v)}">${v==null?'—':escapeHtml(v)}</td>`;};
  let body=slice.map(r=>{
    const key=JSON.stringify(dims.map(d=>r[d]??null));
    const open=drillKey===key;
    let h=`<tr><td class="deal-chev" onclick="toggleDrill('${encodeURIComponent(key)}')">${open?'▾':'▸'}</td>`
      +cols.map(c=>cell(r,c)).join('')+'</tr>';
    if(open)h+=`<tr class="drill-row"><td colspan="${cols.length+1}">${buildDrillPanel(r,dims)}</td></tr>`;
    return h;
  }).join('');
  // totals: derived metrics as ratio of the summed components, not sum of ratios
  const tot={}; mets.forEach(m=>{const d=DERIVED[m];
    if(d){const n=rows.reduce((a,r)=>a+(r['_'+d.num]||0),0), dd=rows.reduce((a,r)=>a+(r['_'+d.den]||0),0);
      tot[m]=dd?n/dd*d.mult:null;}
    else tot[m]=rows.reduce((a,r)=>a+(r[m]||0),0);});
  body+='<tr class="total-row"><td></td>'+cols.map((c,i)=>{
    if(i===0)return `<td>Total (${fmtInt(rows.length)} rows)</td>`;
    if(ALL_METRICS.includes(c))return `<td class="number">${fmtMetric(c,tot[c])}</td>`;
    return '<td></td>';}).join('')+'</tr>';
  document.getElementById('tbl-body').innerHTML=body;
  document.getElementById('tbl-count').textContent=
    `${fmtInt(rows.length)} rows · grain: ${dims.map(fLabel).join(', ')||'(none)'}`+
    ` · ${curMode==='eur'?'EUR':'⚠ local currency — totals mix EUR/BRL/USD'}`+
    (stateFilter?` · state: ${stateFilter.replace(/_/g,' ')}${stateFilter==='dark'?' (previous window shown)':''}`:'');
  renderPagination('tbl-pag',pages,page,p=>{page=p;rebuildTable();});
}


/* ══════════════ Details / Evolution tabs ══════════════ */
let tableTab='details';
function setTableTab(t){
  tableTab=t;
  const det=t==='details';
  // each view shows only its own table and its own setup card
  document.getElementById('pane-details').style.display = det?'':'none';
  document.getElementById('pane-evo').style.display     = det?'none':'';
  document.getElementById('cfg-details').style.display  = det?'':'none';
  document.getElementById('cfg-evo').style.display      = det?'none':'';
  document.getElementById('vsw-details').classList.toggle('active',det);
  document.getElementById('vsw-evo').classList.toggle('active',!det);
  document.getElementById('tbl-hint').textContent = det
    ? '— click ▸ on any row to see its full-window evolution'
    : '— periods as columns; up to 2 segments and 3 metrics';
  document.getElementById('csv-btn').setAttribute('onclick', det?'tableCSV()':'evdCSV()');
  if(det)rebuildTable(); else evdRender();
}

/* ══════════════ Evolution pivot (periods as columns) ══════════════ */
const EVD_ROW_FIELDS=['business_line','connection_type','agency','agency_group_name','dsp','channel_id',
                      'origin','name_source','inventory_type','country_served','owner','deal_name'];
const EVD_METRIC_CHOICES=['gross_revenue','margin','requests','bids','impressions','cpm','bid_rate','win_rate','margin_pct'];
let evdRows=['business_line'], evdMets=['gross_revenue'], evdGran='month';

function evdInit(){
  document.getElementById('evd-rowbtns').innerHTML=EVD_ROW_FIELDS.map(f=>
    `<button class="mini-btn evd-r ${evdRows.includes(f)?'active':''}" data-f="${f}" onclick="evdToggleRow('${f}')">${fLabel(f)}</button>`).join('');
  document.getElementById('evd-metbtns').innerHTML=EVD_METRIC_CHOICES.map(m=>
    `<button class="mini-btn evd-m ${evdMets.includes(m)?'active':''}" data-m="${m}" onclick="evdToggleMet('${m}')">${fLabel(m)}</button>`).join('');
  document.getElementById('evd-gran').value=evdGran;
}
function evdSyncBtns(){
  document.querySelectorAll('.evd-r').forEach(b=>b.classList.toggle('active',evdRows.includes(b.dataset.f)));
  document.querySelectorAll('.evd-m').forEach(b=>b.classList.toggle('active',evdMets.includes(b.dataset.m)));
}
// max 2 row fields / max 3 metrics: a further pick drops the oldest instead of dead-clicking
function evdToggleRow(f){
  const i=evdRows.indexOf(f);
  if(i>=0){ if(evdRows.length>1)evdRows.splice(i,1); }
  else { evdRows.push(f); if(evdRows.length>2)evdRows.shift(); }
  evdSyncBtns(); evdRender();
}
function evdToggleMet(m){
  const i=evdMets.indexOf(m);
  if(i>=0){ if(evdMets.length>1)evdMets.splice(i,1); }
  else { evdMets.push(m); if(evdMets.length>3)evdMets.shift(); }
  evdSyncBtns(); evdRender();
}
function evdSetGran(g){evdGran=g; evdRender();}

let _evdMatrix=null;
function evdRender(){
  const rows=filteredRows();
  const periods=[...new Set(rows.map(r=>periodOf(r.date,evdGran)))].sort();
  // derived metrics need their numerator/denominator summed, never the ratio
  const need=new Set();
  evdMets.forEach(m=>{const d=DERIVED[m]; if(d){need.add(d.num);need.add(d.den);} else need.add(m);});
  const agg=new Map(), tot=new Map(), combo=new Map();
  const SEP='\u0001';
  for(const r of rows){
    const per=periodOf(r.date,evdGran);
    const parts=evdRows.map(f=>r[f]??'(none)');
    const ck=parts.join(SEP);
    if(!combo.has(ck))combo.set(ck,parts);
    const key=ck+SEP+per;
    let o=agg.get(key); if(!o){o={}; agg.set(key,o);}
    // money metrics live as <name>_eur / <name>_lc in the payload
    need.forEach(n=>{const col=MONEY_BASE.includes(n)?n+'_'+curMode:n; o[n]=(o[n]||0)+(r[col]||0);});
    tot.set(ck,(tot.get(ck)||0)+(r['gross_revenue_'+curMode]||0));
  }
  const val=(cell,m)=>{
    if(!cell)return null;
    const d=DERIVED[m];
    if(d){const den=cell[d.den]||0; return den?(cell[d.num]||0)/den*d.mult:null;}
    return cell[m]||0;
  };
  const cks=[...tot.entries()].sort((a,b)=>b[1]-a[1]).map(e=>e[0]);
  document.getElementById('evd-head').innerHTML='<tr>'
    +evdRows.map(f=>`<th>${fLabel(f)}</th>`).join('')
    +'<th>metric</th>'
    +periods.map(p=>`<th class="number">${p}</th>`).join('')+'<th class="number">total</th></tr>';
  let html='';
  for(const ck of cks){
    const parts=combo.get(ck);
    html+=evdMets.map((m,i)=>{
      const tds=periods.map(p=>{const v=val(agg.get(ck+SEP+p),m);
        return `<td class="number">${v==null?'·':fmtMetric(m,v)}</td>`;}).join('');
      const d=DERIVED[m]; let tv;
      if(d){let n=0,dd=0; periods.forEach(p=>{const c=agg.get(ck+SEP+p); if(c){n+=c[d.num]||0; dd+=c[d.den]||0;}}); tv=dd?n/dd*d.mult:null;}
      else {tv=periods.reduce((a,p)=>{const c=agg.get(ck+SEP+p); return a+(c?(c[m]||0):0);},0);}
      return '<tr>'+parts.map(x=>`<td title="${escapeHtml(x)}">${i===0?escapeHtml(x):'<span class="muted">〃</span>'}</td>`).join('')
        +`<td class="muted">${fLabel(m)}</td>`+tds+`<td class="number">${tv==null?'·':fmtMetric(m,tv)}</td></tr>`;
    }).join('');
  }
  document.getElementById('evd-body').innerHTML=html||`<tr><td class="muted" colspan="${evdRows.length+periods.length+2}">No data for the current filters</td></tr>`;
  document.getElementById('evd-count').textContent=
    `${fmtInt(cks.length)} ${evdRows.map(fLabel).join(' × ')} combinations · ${periods.length} ${evdGran}s`
    +` · ${curMode==='eur'?'EUR':'⚠ local currency'}`+(stateFilter?` · state: ${stateFilter.replace(/_/g,' ')}`:'');
  _evdMatrix=[[...evdRows.map(fLabel),'metric',...periods,'total']];
  for(const ck of cks){
    const parts=combo.get(ck);
    for(const m of evdMets){
      const cells=periods.map(p=>{const v=val(agg.get(ck+SEP+p),m); return v==null?'':Math.round(v*100)/100;});
      const d=DERIVED[m]; let tv;
      if(d){let n=0,dd=0; periods.forEach(p=>{const c=agg.get(ck+SEP+p); if(c){n+=c[d.num]||0; dd+=c[d.den]||0;}}); tv=dd?Math.round(n/dd*d.mult*100)/100:'';}
      else {tv=Math.round(periods.reduce((a,p)=>{const c=agg.get(ck+SEP+p); return a+(c?(c[m]||0):0);},0)*100)/100;}
      _evdMatrix.push([...parts,fLabel(m),...cells,tv]);
    }
  }
}
function evdCSV(){ if(_evdMatrix)downloadCSV(_evdMatrix,'deals_evolution_'+evdGran); }

/* ══════════════ drill-down: full-window evolution of one row ══════════════ */
function periodOf(d,gran){
  if(gran==='day')return d;
  if(gran==='month')return d.slice(0,7);
  if(gran==='quarter'){const m=+d.slice(5,7);return d.slice(0,4)+' Q'+(Math.floor((m-1)/3)+1);}
  const dt=new Date(d+'T00:00:00Z'); const wd=(dt.getUTCDay()+6)%7;   // ISO week → Monday
  dt.setUTCDate(dt.getUTCDate()-wd); return 'wk '+dt.toISOString().slice(0,10);
}
function toggleDrill(keyEnc){
  const key=decodeURIComponent(keyEnc);
  drillKey=(drillKey===key)?null:key;
  rebuildTable();
}
function setDrillGran(g){drillGran=g;rebuildTable();}
let _drillMatrix=null;
function drillCSV(){if(_drillMatrix)downloadCSV(_drillMatrix,'deal_evolution');}
// panel for one aggregated row: match its non-date dims, IGNORE period & state —
// full embedded window, so a 7d view still shows the whole month.
function buildDrillPanel(row,dims){
  const dd=dims.filter(d=>d!=='date');
  const rows=ROWS.filter(r=>dimPass(r)&&dd.every(f=>(r[f]??null)===(row[f]??null)));
  const perOf=r=>periodOf(r.date,drillGran);
  const periods=[...new Set(rows.map(perOf))].sort();
  const mets=ALL_METRICS.filter(m=>selMetrics.has(m));
  if(!mets.length)mets.push('gross_revenue','requests','bids','impressions');
  const agg={};
  rows.forEach(r=>{
    const pd=perOf(r);
    let o=agg[pd]; if(!o){o={};agg[pd]=o;}
    mets.forEach(m=>{const dv=DERIVED[m];
      if(dv){o['_n'+m]=(o['_n'+m]||0)+(r[col(dv.num)]||0); o['_d'+m]=(o['_d'+m]||0)+(r[col(dv.den)]||0);}
      else {const v=r[col(m)]; if(v!=null)o[m]=(o[m]||0)+v;}});
  });
  const valOf=(m,pd)=>{const o=agg[pd]; if(!o)return null; const dv=DERIVED[m];
    if(dv)return o['_d'+m]?(o['_n'+m]||0)/o['_d'+m]*dv.mult:null;
    return o[m]??null;};
  const spark=vals=>{
    const nums=vals.map(v=>v||0), mx=Math.max(...nums,1);
    const w=120,h=22;
    const pts=nums.map((v,i)=>`${(i*(w-4)/Math.max(nums.length-1,1)+2).toFixed(1)},${(h-2-(h-4)*v/mx).toFixed(1)}`).join(' ');
    return `<svg width="${w}" height="${h}"><polyline points="${pts}" fill="none" stroke="var(--accent)" stroke-width="1.5"/></svg>`;};
  const label=dd.map(f=>row[f]).filter(v=>v!=null).join(' · ')||'(all)';
  let html=`<div class="drill-panel">
    <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:8px">
      <strong style="font-size:13px">${escapeHtml(label)}</strong>
      <span class="muted" style="font-size:11px">· full window since ${MIND} (daily — ignores the period selector)</span>
      <span class="spacer" style="flex:1"></span>
      ${['day','week','month','quarter'].map(g=>`<button class="seg-btn ${drillGran===g?'active':''}" onclick="setDrillGran('${g}')">${g}</button>`).join('')}
      <button class="mini-btn" onclick="drillCSV()">📥 CSV</button>
    </div>
    <div class="table-wrapper"><table><thead><tr><th>metric</th>${periods.map(pd=>`<th class="number">${drillGran==='day'?pd.slice(5):pd}</th>`).join('')}<th>trend</th></tr></thead><tbody>`;
  _drillMatrix=[['metric',...periods]];
  mets.forEach(m=>{
    const vals=periods.map(pd=>valOf(m,pd));
    _drillMatrix.push([m,...vals.map(v=>v??'')]);
    html+=`<tr><td>${m.replace(/_/g,' ')}</td>${vals.map(v=>`<td class="number">${fmtMetric(m,v)}</td>`).join('')}<td>${spark(vals)}</td></tr>`;
  });
  html+='</tbody></table></div></div>';
  return html;
}

/* ══════════════ CSV + email ══════════════ */
function csvMatrix(){
  const {cols,rows}=currentView();
  return [cols,...rows.map(r=>cols.map(c=>c==='state'?(dealState(r.deal_id)||''):r[c]??''))];
}
function downloadCSV(matrix,name){
  const esc=v=>{if(v==null)return'';const s=String(v);return /[",\n]/.test(s)?'"'+s.replace(/"/g,'""')+'"':s;};
  const csv=matrix.map(r=>r.map(esc).join(',')).join('\n');
  const blob=new Blob([csv],{type:'text/csv;charset=utf-8;'});
  const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=name+'.csv';
  document.body.appendChild(a);a.click();a.remove();
}
const csvName=()=>{const d=chartDays();return 'deals_health_'+(d[0]||'')+'_to_'+(d[d.length-1]||'');};
function tableCSV(){downloadCSV(csvMatrix(),csvName());}
function prepareEmail(){
  const to=document.getElementById('email-to').value.trim();
  if(!to){alert('Enter a recipient email first.');return;}
  downloadCSV(csvMatrix(),csvName());
  const d=chartDays();
  const range=(d[0]||'')+' → '+(d[d.length-1]||'');
  const subject=encodeURIComponent('Deals health report '+range);
  const body=encodeURIComponent(
    'Hi,\n\nPlease find attached the deals health report ('+range+').\n\n'+
    'Note: the CSV ('+csvName()+'.csv) was just downloaded to your machine — attach it before sending (mail links cannot attach files).\n\n'+
    'Notes: amounts are '+(curMode==='eur'?'EUR (monthly average rates)':'in each deal\'s local currency (mixed in totals)')+
    '. BFM funnel metrics use Beachfront naming and are not 1:1 comparable with STX SSP metrics.\n\nAnalytics Team');
  window.location.href='mailto:'+encodeURIComponent(to)+'?subject='+subject+'&body='+body;
}

/* ══════════════ saved views ══════════════
   A view = JSON snapshot of every user-tweakable control. Persisted in
   localStorage (per browser) AND — when served through the Apps Script
   wrapper — in UserProperties via google.script.run (per Google account,
   cross-device). The daily rebuild replaces the HTML, so views never live
   in the file itself. applyState() is defensive: unknown fields/metrics
   are dropped, dates are clamped to the embedded window, and a range that
   ended on the latest day is saved as a relative lastN so it stays
   anchored to "the most recent N days" on tomorrow's file. */
function getState(){
  const f={}; FIELDS.forEach(k=>{if(selected[k].size)f[k]=[...selected[k]];});
  const range=rangeTo===MAXD
    ? {lastN:DAYS.indexOf(rangeTo)-DAYS.indexOf(rangeFrom)+1}
    : {from:rangeFrom,to:rangeTo};
  return {v:1,filters:f,stateFilter,curMode,matrixBy,
    selFields:[...selFields],selMetrics:[...selMetrics],
    chartMetric:document.getElementById('chart-metric').value,
    chartGran,chartType,chartScale,colorBy,sortCol,sortDir,
    filtersOpen,ndOpen,range};
}
function applyState(s){
  if(!s||typeof s!=='object')return;
  try{
    FIELDS.forEach(k=>selected[k].clear());
    if(s.filters&&typeof s.filters==='object')
      for(const k in s.filters){ if(selected[k]&&Array.isArray(s.filters[k]))s.filters[k].forEach(v=>selected[k].add(String(v))); }
    stateFilter=STATES[s.stateFilter]?s.stateFilter:null;
    if(s.curMode==='eur'||s.curMode==='lc')curMode=s.curMode;
    if(MX_LABELS[s.matrixBy])matrixBy=s.matrixBy;
    if(Array.isArray(s.selFields)){
      const ok=s.selFields.filter(x=>TABLE_FIELDS.includes(x));
      if(ok.length){selFields.clear();ok.forEach(x=>selFields.add(x));}
    }
    if(Array.isArray(s.selMetrics)){selMetrics.clear();s.selMetrics.filter(x=>ALL_METRICS.includes(x)).forEach(x=>selMetrics.add(x));}
    if(['day','week','month','quarter','year'].includes(s.chartGran))chartGran=s.chartGran;
    if(['bar','line'].includes(s.chartType))chartType=s.chartType;
    if(['linear','log'].includes(s.chartScale))chartScale=s.chartScale;
    if(['origin','business_line'].includes(s.colorBy))colorBy=s.colorBy;
    sortCol=typeof s.sortCol==='string'?s.sortCol:null; sortDir=s.sortDir===1?1:-1;
    const r=s.range||{};
    if(r.lastN>0){rangeTo=MAXD;rangeFrom=DAYS[Math.max(0,DAYS.length-r.lastN)];}
    else if(typeof r.from==='string'&&typeof r.to==='string'){
      rangeFrom=r.from<MIND?MIND:(r.from>MAXD?MAXD:r.from);
      rangeTo=r.to>MAXD?MAXD:(r.to<MIND?MIND:r.to);
      if(rangeFrom>rangeTo){rangeFrom=DAYS[Math.max(0,DAYS.length-7)];rangeTo=MAXD;}
    }
    const cm=document.getElementById('chart-metric');
    if(s.chartMetric&&[...cm.options].some(o=>o.value===s.chartMetric))cm.value=s.chartMetric;
    if(typeof s.filtersOpen==='boolean')filtersOpen=s.filtersOpen;
    if(typeof s.ndOpen==='boolean')ndOpen=s.ndOpen;
    page=1; drillKey=null;
    syncStateUI();
  }catch(e){console.warn('applyState failed',e);}
}
// push in-memory state back into every control, then re-render everything
function syncStateUI(){
  document.querySelectorAll('.cur-btn').forEach(b=>b.classList.toggle('active',b.dataset.cur===curMode));
  document.querySelectorAll('.mxb').forEach(b=>b.classList.toggle('active',b.dataset.f===matrixBy));
  document.querySelectorAll('.cgb').forEach(b=>b.classList.toggle('active',b.dataset.g===chartGran));
  document.getElementById('ct-bar').classList.toggle('active',chartType==='bar');
  document.getElementById('ct-line').classList.toggle('active',chartType==='line');
  document.getElementById('sc-lin').classList.toggle('active',chartScale==='linear');
  document.getElementById('sc-log').classList.toggle('active',chartScale==='log');
  document.getElementById('cb-origin').classList.toggle('active',colorBy==='origin');
  document.getElementById('cb-bl').classList.toggle('active',colorBy==='business_line');
  document.getElementById('cal-label').textContent=rangeFrom+' → '+rangeTo;
  document.getElementById('filters-body').style.display=filtersOpen?'':'none';
  document.getElementById('filters-toggle').textContent=filtersOpen?'▲ Hide':'▼ Show';
  document.getElementById('nd-body').style.display=ndOpen?'':'none';
  document.getElementById('nd-toggle').textContent=ndOpen?'▲ Hide':'▼ Show';
  buildPickers(); updateRangeLabels();
  applyFilters();   // refreshes ms labels + options and calls rebuildAll()
}

/* views store: {views:{name:state}, def:name|null} */
const VIEWS_KEY='deals-views';
let VSTORE={views:{},def:null};
const gasAvailable=()=>typeof google!=='undefined'&&google.script&&google.script.run;
function vLoadLocal(){
  try{const s=localStorage.getItem(VIEWS_KEY); if(s){const o=JSON.parse(s); if(o&&o.views)VSTORE=o;}}catch(e){}
}
function vPersist(){
  try{localStorage.setItem(VIEWS_KEY,JSON.stringify(VSTORE));}catch(e){}
  if(gasAvailable()){
    try{
      google.script.run
        .withSuccessHandler(()=>vSyncMsg('synced to your Google account'))
        .withFailureHandler(e=>vSyncMsg('⚠ account sync failed — saved in this browser only'))
        .saveViews(JSON.stringify(VSTORE));
    }catch(e){vSyncMsg('saved in this browser only');}
  } else vSyncMsg('saved in this browser (open via the shared link for account sync)');
}
function vSyncMsg(t){const el=document.getElementById('views-sync'); if(el)el.textContent=t;}
// server copy wins over the local mirror (it's the cross-device source of truth)
function vLoadRemote(){
  if(!gasAvailable())return;
  try{
    google.script.run.withSuccessHandler(json=>{
      if(!json)return;
      try{
        const o=JSON.parse(json);
        if(o&&o.views){
          const hadLocal=Object.keys(VSTORE.views).length>0;
          VSTORE=o;
          try{localStorage.setItem(VIEWS_KEY,JSON.stringify(VSTORE));}catch(e){}
          viewsRenderMenu();
          // apply the account default only if this session hasn't shown one yet
          if(!hadLocal&&!location.hash.startsWith('#v=')&&VSTORE.def&&VSTORE.views[VSTORE.def])
            applyState(VSTORE.views[VSTORE.def]);
          vSyncMsg('synced to your Google account');
        }
      }catch(e){}
    }).loadViews();
  }catch(e){}
}
function viewsClose(){
  document.getElementById('views-dd')?.classList.remove('open');
  document.getElementById('views-trigger')?.classList.remove('open');
}
function viewsToggle(e){
  e&&e.stopPropagation(); calClose();
  FIELDS.forEach(x=>{document.getElementById('ms-dd-'+x)?.classList.remove('open');document.getElementById('ms-trig-'+x)?.classList.remove('open');});
  viewsRenderMenu();
  document.getElementById('views-dd').classList.toggle('open');
  document.getElementById('views-trigger').classList.toggle('open');
}
function viewsRenderMenu(){
  const names=Object.keys(VSTORE.views).sort();
  document.getElementById('views-list').innerHTML=names.length?names.map(n=>{
    const enc=encodeURIComponent(n), isDef=VSTORE.def===n;
    return `<div class="ms-option" style="justify-content:space-between" onclick="viewApply('${enc}',event)">
      <span title="${escapeHtml(n)}">${escapeHtml(n)}</span>
      <span style="flex-shrink:0;display:inline-flex;gap:8px">
        <span title="${isDef?'default view (applied on load)':'make default'}" onclick="viewSetDefault('${enc}',event)" style="opacity:${isDef?1:.35}">⭐</span>
        <span title="delete view" onclick="viewDelete('${enc}',event)">🗑</span>
      </span></div>`;
  }).join(''):'<div class="ms-option muted">No saved views yet — set your filters and save below</div>';
  document.getElementById('views-label').textContent=VSTORE.def?('⭐ '+VSTORE.def):'⭐ Views';
}
function viewSave(e){
  e&&e.stopPropagation();
  const inp=document.getElementById('view-name');
  const name=(inp.value||'').trim()||VSTORE.def||'My view';
  VSTORE.views[name]=getState();
  if(!VSTORE.def)VSTORE.def=name;
  inp.value='';
  vPersist(); viewsRenderMenu();
}
function viewApply(enc,e){
  e&&e.stopPropagation();
  const s=VSTORE.views[decodeURIComponent(enc)];
  if(s){applyState(s); viewsClose();}
}
function viewSetDefault(enc,e){
  e&&e.stopPropagation();
  const n=decodeURIComponent(enc);
  VSTORE.def=(VSTORE.def===n)?null:n;
  vPersist(); viewsRenderMenu();
}
function viewDelete(enc,e){
  e&&e.stopPropagation();
  const n=decodeURIComponent(enc);
  delete VSTORE.views[n];
  if(VSTORE.def===n)VSTORE.def=null;
  vPersist(); viewsRenderMenu();
}
/* share: state → base64 in the URL hash; applied on load, beats the default view */
const stateToHash=s=>btoa(unescape(encodeURIComponent(JSON.stringify(s))));
function hashToState(){
  if(!location.hash.startsWith('#v='))return null;
  try{return JSON.parse(decodeURIComponent(escape(atob(location.hash.slice(3)))));}catch(e){return null;}
}
function viewShare(e){
  e&&e.stopPropagation();
  const h='#v='+stateToHash(getState());
  const url=location.origin==='null'?h:location.href.split('#')[0]+h;
  const done=()=>{vSyncMsg('link copied — paste it to a colleague');};
  if(navigator.clipboard)navigator.clipboard.writeText(url).then(done,()=>prompt('Copy this view link:',url));
  else prompt('Copy this view link:',url);
}

/* ══════════════ boot ══════════════ */
function rebuildAll(){_stats=null;buildKpis();buildMatrix();buildNewDeals();buildChips();buildChart();
  if(tableTab==='evo')evdRender(); else rebuildTable();}
// hover tooltip: nearest day column → all series values
(function(){
  const wrap=document.getElementById('chart-wrap'), tip=document.getElementById('chart-tip');
  wrap.addEventListener('mousemove',e=>{
    const c=_chartCtx, svg=wrap.querySelector('svg');
    if(!c||!svg||!c.days.length){tip.style.display='none';return;}
    const rect=svg.getBoundingClientRect();
    if(!rect.width){tip.style.display='none';return;}
    const xView=(e.clientX-rect.left)/rect.width*c.W;
    const bw=(c.W-c.padL-c.padR)/Math.max(c.days.length,1);
    const i=Math.floor((xView-c.padL)/bw);
    if(i<0||i>=c.days.length){tip.style.display='none';return;}
    const d=c.days[i];
    tip.innerHTML='<strong>'+d+'</strong><br>'+c.keys.map((k,ki)=>
      `<span style="display:inline-block;width:9px;height:9px;border-radius:2px;background:${CHART_COLORS[ki%CHART_COLORS.length]};margin-right:5px"></span>${escapeHtml(k)}: <strong>${c.fmtV(c.val(k,d))}</strong>`).join('<br>');
    tip.style.display='block';
    const wr=wrap.getBoundingClientRect();
    let tx=e.clientX-wr.left+14;
    if(tx+tip.offsetWidth+8>wr.width)tx=Math.max(0,tx-tip.offsetWidth-28);
    tip.style.left=tx+'px'; tip.style.top=(e.clientY-wr.top+12)+'px';
  });
  wrap.addEventListener('mouseleave',()=>tip.style.display='none');
})();
(async function boot(){
  try{
  ROWS=unpack(await inflate(ROWS_B64));
  initData();
  MIND=DAYS[0]; MAXD=DAYS[DAYS.length-1];
  CAL_MONTHS=[...new Set(DAYS.map(d=>d.slice(0,7)))].sort();
  rangeFrom=DAYS[Math.max(0,DAYS.length-7)]; rangeTo=DAYS[DAYS.length-1];
  document.getElementById('cal-label').textContent=rangeFrom+' → '+rangeTo;
  updateRangeLabels();buildChartMetricSelect();buildPickers();buildFilters();evdInit();
  // saved views: URL hash beats the saved default beats the vanilla layout
  vLoadLocal(); viewsRenderMenu();
  const boot_state=hashToState()||(VSTORE.def&&VSTORE.views[VSTORE.def])||null;
  if(boot_state)applyState(boot_state); else rebuildAll();
  vLoadRemote();   // async reconcile with the per-account copy (Apps Script only)
  // a pasted #v= link on an already-open page changes only the hash (no reload)
  window.addEventListener('hashchange',()=>{const s=hashToState(); if(s)applyState(s);});
  }catch(e){
    // Un booteo fallido dejaba la página en blanco — mostramos el error para poder diagnosticar
    document.body.insertAdjacentHTML('afterbegin',
      '<div style="margin:24px;padding:16px;border:1px solid #FF6B7C;border-radius:8px;font-family:monospace">'+
      'Dashboard failed to load: '+escapeHtml(String(e&&e.message||e))+'</div>');
    throw e;
  }
})();
</script>
</body>
</html>
"""
