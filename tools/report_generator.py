"""Deals Daily Dashboard generator — fully self-contained HTML (no server).

Single dataset (sql/deals_daily.sql): STX (Seedtag delivery, EUR) + BFM
(Beachfront), last 30 closed days, all amounts in
USD (STX converted from EUR with monthly average rates).

UI (approved mockup): KPI strip → cascading filters (curator first) →
daily-evolution SVG chart (bar/line, color by origin or business line) →
field/metric pickers → paginated sortable table with totals row →
CSV download + mailto draft.

Data is embedded with _pack() (columnar + dictionary encoding) and rebuilt
client-side by unpack(). Template uses __TOKEN__ placeholders (plain
.replace(), no f-string brace escaping).
"""

from __future__ import annotations

import json


def _pack(rows: list[dict]) -> str:
    """Columnar + dictionary encoding for the big embed (same as reference).

    List-of-dicts JSON repeats every key and every string per row; packing
    stores each column as an array and dictionary-encodes string columns
    (values once, rows as int indexes) — ~7x smaller, rebuilt into the same
    list of dicts at load time by the page's unpack() (lossless).
    """
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


def generate_html(*, rows: list[dict], sql_text: str, now: str) -> str:
    html = _TEMPLATE
    for token, value in {
        "__ROWS_JSON__": _pack(rows),
        "__SQL_JSON__": json.dumps(sql_text, ensure_ascii=False),
        "__NOW__": now,
    }.items():
        html = html.replace(token, value)
    return html


_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en" data-theme="auto">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Deals Daily Dashboard</title>
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

.kpi-row{display:flex;gap:12px;margin-bottom:22px;flex-wrap:wrap}
.kpi-card{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:14px 18px;min-width:200px;flex:1}
.kpi-card .kpi-label{font-size:12px;font-weight:600;color:var(--text-muted);text-transform:uppercase;letter-spacing:.04em}
.kpi-card .kpi-value{font-size:26px;font-weight:700;color:var(--kpi-strong);margin-top:2px;font-variant-numeric:tabular-nums}
.kpi-card .kpi-sub{font-size:12px;color:var(--text-subtle);margin-top:2px}

/* chart */
.chart-legend{display:flex;gap:16px;flex-wrap:wrap;margin-top:10px;font-size:12px;color:var(--text-muted)}
.chart-legend .li{display:inline-flex;align-items:center;gap:6px}
.chart-legend .sw{width:11px;height:11px;border-radius:3px;display:inline-block}
.seg-btn{border:1px solid var(--border);background:var(--surface);color:var(--text-muted);font-family:'Instrument Sans',sans-serif;font-size:12px;font-weight:600;padding:5px 14px;border-radius:20px;cursor:pointer}
.seg-btn:hover{color:var(--text);border-color:var(--accent)}
.seg-btn.active{background:var(--accent);color:var(--accent-ink);border-color:var(--accent)}

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

/* filters */
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
.ms-search{padding:8px;border-bottom:1px solid var(--border)}
.ms-search input{width:100%;padding:6px 8px;background:var(--surface-2);border:1px solid var(--border);border-radius:5px;font-size:12px;color:var(--text);outline:none}
.ms-options{overflow-y:auto;padding:4px 0;flex:1}
.ms-option{display:flex;align-items:center;gap:8px;padding:6px 12px;cursor:pointer;font-size:13px}
.ms-option:hover{background:var(--surface-2)}
.ms-option input{accent-color:var(--accent);flex-shrink:0}
.ms-option span{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ms-footer{padding:8px;border-top:1px solid var(--border);display:flex;justify-content:flex-end}
.ms-footer button{padding:5px 14px;background:transparent;color:var(--text-muted);border:1px solid var(--border);border-radius:5px;font-size:12px;cursor:pointer}
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
    <h1>Deals Daily Dashboard</h1>
    <div class="subtitle">Analytics Team &middot; <span id="hdr-range"></span> &middot; STX (Seedtag delivery) + BFM (Beachfront) &middot; all amounts USD
      <span class="info-icon" title="View SQL" onclick="toggleTooltip(event,'sql-tip')" style="vertical-align:-4px;margin-left:4px">i</span>
      <div class="tooltip sql" id="sql-tip"></div>
    </div>
  </div>
</header>

<div class="page">

<div class="note-banner">💵 <strong>Currency:</strong> all amounts are <strong>USD</strong> — STX figures are converted from EUR using the monthly average rate (currency_rates_monthly).
&nbsp;·&nbsp; Funnel metrics naming differs between SSP (STX) and Beachfront (BFM) — see the <span class="info-icon" style="vertical-align:-4px" onclick="toggleTooltip(event,'funnel-tip')">i</span> tooltip.
&nbsp;·&nbsp; curator margin split comes from Salesforce (curator_margin_value); deals without a Salesforce record keep their full margin on the Seedtag side. · BFM brands covering 95% of window revenue keep their name; the tail shows as '(other)'.
<div class="tooltip" id="funnel-tip">Beachfront uses a different funnel naming convention (Swap, Dec-2025):

• bids = outgoing_bids (input bids): bids the DSP sends to the SSP. Top of the demand funnel.
• wins = total_bids_placed: bids returned by DSPs.
• requests = ads_served: placed − rejected ≈ auction wins.
Funnel: outgoing_bids &gt; total_bids_placed &gt; ads_served &gt; impressions.
Internal win rate = ads_served / total_bids_placed; external = impressions / ads_served.

⚠️ These BFM columns are NOT 1:1 comparable with STX requests / bids / wins (SSP naming).</div>
</div>

<div class="kpi-row" id="kpis"></div>

<div class="card">
  <div class="card-header">🔎 Filters <span class="muted" style="font-weight:400">— pick the curator first · options cascade (each list only shows values compatible with the other filters) · sorted by revenue</span>
    <div class="spacer"></div>
    <span class="flabel">Period</span>
    <button class="seg-btn period-btn active" data-days="7" onclick="setLastDays(7)">7d</button>
    <button class="seg-btn period-btn" data-days="30" onclick="setLastDays(30)">30d</button>
    <button class="seg-btn period-btn" data-days="14" onclick="setLastDays(14)">14d</button>
    <button class="seg-btn period-btn" data-days="0" onclick="setLastDays(0)">All</button>
    <input class="email-box" id="custom-days" type="number" min="1" step="1" placeholder="X days"
           style="min-width:90px;width:90px" onchange="setLastDays(parseInt(this.value)||0)">
    <button class="mini-btn" onclick="clearAllFilters()">Clear all</button>
  </div>
  <div class="curator-row" id="filter-curator"></div>
  <div class="filter-sep">More filters</div>
  <div class="filter-grid" id="filter-grid"></div>
</div>

<div class="card">
  <div class="card-header">📈 Daily revenue evolution
    <span class="muted" style="font-weight:400">— gross revenue by day · respects the filters above</span>
    <div class="spacer"></div>
    <span class="flabel">Chart</span>
    <button class="seg-btn active" id="ct-bar" onclick="setChartType('bar')">Bars</button>
    <button class="seg-btn" id="ct-line" onclick="setChartType('line')">Lines</button>
    <span class="flabel" style="margin-left:10px">Color by</span>
    <button class="seg-btn active" id="cb-origin" onclick="setColorBy('origin')">Origin (STX/BFM)</button>
    <button class="seg-btn" id="cb-bl" onclick="setColorBy('business_line')">Business line</button>
  </div>
  <div id="chart"></div>
  <div class="chart-legend" id="chart-legend"></div>
</div>

<div class="card">
  <div class="card-header">🧩 Build your view
    <span class="muted" style="font-weight:400">— pick fields (grain) and metrics; the table aggregates metrics (SUM) over date + chosen fields</span>
  </div>
  <div class="picker-grid">
    <div>
      <div class="flabel" style="margin-bottom:8px">Fields (dimensions) · date is always included</div>
      <div class="picker-opts" id="field-picker"></div>
      <div class="picker-actions">
        <button class="mini-btn" onclick="pickAll('field',true)">All</button>
        <button class="mini-btn" onclick="pickAll('field',false)">None</button>
      </div>
    </div>
    <div>
      <div class="flabel" style="margin-bottom:8px">Metrics (SUM-aggregated)
        <span class="info-icon" style="vertical-align:-4px;margin-left:4px" onclick="toggleTooltip(event,'metric-tip')">i</span>
        <div class="tooltip" id="metric-tip">requests / bids / wins: for BFM rows these come from Beachfront's own funnel (ads_served / outgoing_bids / total_bids_placed) and are not 1:1 comparable with the STX SSP metrics.
curator_margin_*: STX only (NULL for BFM); split % comes from Salesforce curator_margin_value. sf_product_lines: from Salesforce, STX only.</div>
      </div>
      <div class="picker-opts" id="metric-picker"></div>
      <div class="picker-actions">
        <button class="mini-btn" onclick="pickAll('metric',true)">All</button>
        <button class="mini-btn" onclick="pickAll('metric',false)">None</button>
      </div>
    </div>
  </div>
</div>

<div class="card">
  <div class="card-header">📋 Deals daily table
    <div class="spacer"></div>
    <input class="email-box" id="email-to" type="email" placeholder="colleague@seedtag.com">
    <button class="btn-csv" onclick="prepareEmail()">✉️ Prepare email</button>
    <button class="btn-csv" onclick="tableCSV()">📥 Download CSV</button>
  </div>
  <div class="table-wrapper"><table><thead id="tbl-head"></thead><tbody id="tbl-body"></tbody></table></div>
  <div class="table-meta"><span class="count" id="tbl-count"></span><div class="pagination" id="tbl-pag"></div></div>
</div>

</div>

<footer class="report-footer">
  <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="20" height="20" aria-label="Seedtag">
    <circle cx="50" cy="50" r="50" fill="#FF6B7C"/><circle cx="50" cy="27" r="10" fill="white"/>
    <path d="M50,54 C47,47 16,49 15,65 C14,79 35,84 50,79Z" fill="white"/>
    <path d="M50,54 C53,47 84,49 85,65 C86,79 65,84 50,79Z" fill="white"/>
  </svg>
  Analytics Team &middot; Deals Daily Report &middot; <span id="ftr-range"></span>
</footer>

<script>
'use strict';
// unpack: columnar + dictionary payload → list of row objects (see _pack in the generator)
function unpack(p){
  const cols=Object.keys(p.c), out=new Array(p.n);
  for(let i=0;i<p.n;i++){
    const o={};
    for(const c of cols){ const v=p.c[c][i]; o[c]=p.d[c]?p.d[c][v]:v; }
    out[i]=o;
  }
  return out;
}
const ROWS=unpack(__ROWS_JSON__);
const SQL_TEXT=__SQL_JSON__;

const DAYS=[...new Set(ROWS.map(r=>String(r.date).slice(0,10)))].sort();
ROWS.forEach(r=>r.date=String(r.date).slice(0,10));


/* ══════════════ config ══════════════ */
const FIELDS=['deal_id','salesforce_crm_id','currency','deal_name','name_source','business_line','brand','agency_group_name','agency','channel_id','dsp','connection_type','seat_id','country_served','country_sold','owner','am_csm','inventory_type','format'];
const METRICS=['platform_spend','gross_revenue','pub_cost','curator_margin_total','curator_margin_stx','curator_margin_curator','margin','requests','bids','wins','impressions','sf_product_lines'];
const MONEY=new Set(['platform_spend','gross_revenue','pub_cost','curator_margin_total','curator_margin_stx','curator_margin_curator','margin']);
const CHART_COLORS=['#5476FF','#E866F4','#948A8A','#67C8FE','#FFA071','#A36AFF','#F4D56D'];

const selFields=new Set(['deal_id','deal_name','business_line','dsp']);
const selMetrics=new Set(['gross_revenue','pub_cost','margin','impressions']);
const selected={}; FIELDS.forEach(f=>selected[f]=new Set());
let colorBy='origin', chartType='bar', sortCol=null, sortDir=-1, page=1; const PAGE_SIZE=25;
// Period filter: keep only the last N closed days (0 = all). Default 7.
let lastDays=7;
const activeDays=()=>lastDays?DAYS.slice(-lastDays):DAYS;
const dateCutoff=()=>lastDays?activeDays()[0]:null;
function setLastDays(n){
  lastDays=Math.max(0,n|0);
  document.querySelectorAll('.period-btn').forEach(b=>b.classList.toggle('active',+b.dataset.days===lastDays));
  const inp=document.getElementById('custom-days');
  if(![7,14,30,0].includes(lastDays)) inp.value=lastDays; else inp.value='';
  updateRangeLabels();
  applyFilters();
}
function updateRangeLabels(){
  const d=activeDays(), lbl=d.length?d[0]+' → '+d[d.length-1]:'(no data)';
  document.getElementById('hdr-range').textContent=lbl;
  document.getElementById('ftr-range').textContent=lbl;
}

const fmtMoney=n=>n==null?'—':(Number(n)).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});
const fmtInt=n=>n==null?'—':(Number(n)).toLocaleString('en-US');
const escapeHtml=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

/* theme */
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

/* SQL tooltip (click to copy, like the reference dashboard) */
(function(){
  const t=document.getElementById('sql-tip');
  t.innerHTML=escapeHtml(SQL_TEXT)+'<span class="copy-hint">Click to copy</span>';
  t.addEventListener('click',()=>{
    navigator.clipboard&&navigator.clipboard.writeText(SQL_TEXT);
    const h=t.querySelector('.copy-hint'); if(h){const o=h.textContent;h.textContent='Copied ✓';setTimeout(()=>h.textContent=o,1200);}
  });
})();

/* ══════════════ filters (ms-* pattern, cascading) ══════════════ */
// Curator filters go first and are visually prominent; the rest is ordered by usefulness.
const CURATOR_FIELDS=['agency_group_name','agency'];
const OTHER_FIELDS=['business_line','dsp','connection_type','channel_id','brand','deal_name','deal_id','seat_id',
  'country_served','country_sold','owner','am_csm','inventory_type','format',
  'name_source','currency','salesforce_crm_id'];
function rowPass(r){
  const cut=dateCutoff(); if(cut&&r.date<cut)return false;
  return FIELDS.every(f=>{const s=selected[f]; if(s.size===0)return true; const v=r[f]; return v!=null&&s.has(v);});}
const filteredRows=()=>ROWS.filter(rowPass);

// Cascading options: for filter f, only values present in rows passing every OTHER
// filter, sorted by gross revenue desc. Computed for ALL fields in ONE pass over
// ROWS (per-row failing-filter count trick) — refiltering per field is too slow
// at ~800k rows. Values already selected in f stay listed (pinned on top).
let _optCache=null;
function computeOptionRevs(){
  const active=FIELDS.filter(f=>selected[f].size>0);
  const revs={}; FIELDS.forEach(f=>revs[f]=new Map());
  const bump=(f,v,g)=>{ if(v==null||v==='')return; const m=revs[f]; m.set(v,(m.get(v)||0)+g); };
  const cut=dateCutoff();
  for(const r of ROWS){
    if(cut&&r.date<cut)continue;  // period filter applies to option lists too
    let fails=0, failField=null;
    for(const f of active){ const v=r[f]; if(v==null||!selected[f].has(v)){ if(++fails>1)break; failField=f; } }
    const g=r.gross_revenue||0;
    if(fails===0){ for(const f of FIELDS) bump(f,r[f],g); }
    else if(fails===1){ bump(failField,r[failField],g); }  // row passes all filters except its own
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
      <span class="flabel">${f.replace(/_/g,' ')}</span>
      <div class="ms-wrap">
        <div class="ms-trigger" id="ms-trig-${f}" onclick="msToggle('${f}',event)">
          <span class="ms-label" id="ms-label-${f}">All</span><span class="ms-arrow">▼</span>
        </div>
        <div class="ms-dropdown" id="ms-dd-${f}">
          <div class="ms-search"><input type="text" placeholder="Search…" oninput="msSearch('${f}',this.value)"></div>
          <div class="ms-options" id="ms-opts-${f}"></div>
          <div class="ms-footer"><button onclick="msClear('${f}')">Clear</button></div>
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
function buildOptions(f,q=''){
  q=q.toLowerCase();
  const all=optionsFor(f).filter(v=>!q||String(v).toLowerCase().includes(q));
  const shown=all.slice(0,MAX_OPTS);
  document.getElementById('ms-opts-'+f).innerHTML=shown.map(v=>
    `<label class="ms-option" data-val="${escapeHtml(v)}">
       <input type="checkbox" ${selected[f].has(v)?'checked':''} onchange="msPick('${f}','${encodeURIComponent(v)}',this.checked)">
       <span title="${escapeHtml(v)}">${escapeHtml(v)}</span></label>`).join('')
    +(all.length>MAX_OPTS?`<div class="ms-option muted">… ${fmtInt(all.length-MAX_OPTS)} more — refine the search</div>`:'');
}
function msToggle(f,e){
  e&&e.stopPropagation();
  FIELDS.forEach(x=>{if(x!==f){document.getElementById('ms-dd-'+x).classList.remove('open');document.getElementById('ms-trig-'+x).classList.remove('open');}});
  document.getElementById('ms-dd-'+f).classList.toggle('open');
  document.getElementById('ms-trig-'+f).classList.toggle('open');
}
document.addEventListener('click',e=>{if(!e.target.closest('.ms-wrap'))FIELDS.forEach(f=>{
  document.getElementById('ms-dd-'+f).classList.remove('open');
  document.getElementById('ms-trig-'+f).classList.remove('open');});});
function msSearch(f,q){buildOptions(f,q);}
function msPick(f,enc,on){const v=decodeURIComponent(enc); if(on)selected[f].add(v); else selected[f].delete(v); applyFilters();}
function msClear(f){selected[f].clear();buildOptions(f);
  const inp=document.querySelector(`#ms-dd-${f} .ms-search input`); if(inp){inp.value='';msSearch(f,'');} applyFilters();}
function clearAllFilters(){FIELDS.forEach(f=>{selected[f].clear();buildOptions(f);});applyFilters();}
function applyFilters(){
  _optCache=null;
  FIELDS.forEach(f=>{const s=selected[f];
    document.getElementById('ms-label-'+f).textContent=s.size===0?'All':(s.size===1?[...s][0]:s.size+' selected');
    document.getElementById('ms-trig-'+f).classList.toggle('active-filter',s.size>0);
    // cascade: every list reflects the other filters (keeps the search text)
    const inp=document.querySelector(`#ms-dd-${f} .ms-search input`);
    buildOptions(f,inp?inp.value:'');});
  page=1; rebuildAll();
}

/* ══════════════ pickers ══════════════ */
function buildPickers(){
  document.getElementById('field-picker').innerHTML=FIELDS.map(f=>
    `<label class="picker-opt"><input type="checkbox" ${selFields.has(f)?'checked':''} onchange="togglePick('field','${f}',this.checked)"><code>${f}</code></label>`).join('');
  document.getElementById('metric-picker').innerHTML=METRICS.map(m=>
    `<label class="picker-opt"><input type="checkbox" ${selMetrics.has(m)?'checked':''} onchange="togglePick('metric','${m}',this.checked)"><code>${m}</code></label>`).join('');
}
function togglePick(kind,name,on){
  const s=kind==='field'?selFields:selMetrics;
  if(on)s.add(name); else s.delete(name);
  page=1; sortCol=null; rebuildTable();
}
function pickAll(kind,on){
  const s=kind==='field'?selFields:selMetrics, all=kind==='field'?FIELDS:METRICS;
  s.clear(); if(on)all.forEach(x=>s.add(x));
  buildPickers(); page=1; sortCol=null; rebuildTable();
}

/* ══════════════ aggregation ══════════════ */
function aggregate(){
  const dims=['date',...FIELDS.filter(f=>selFields.has(f))];
  const mets=METRICS.filter(m=>selMetrics.has(m));
  const map=new Map();
  filteredRows().forEach(r=>{
    const key=dims.map(d=>r[d]??'∅').join('␟');
    let g=map.get(key);
    if(!g){g={}; dims.forEach(d=>g[d]=r[d]); mets.forEach(m=>g[m]=null); map.set(key,g);}
    mets.forEach(m=>{if(r[m]!=null)g[m]=(g[m]||0)+r[m];});
  });
  return {dims,mets,rows:[...map.values()]};
}

/* ══════════════ chart (hand-rolled SVG) ══════════════ */
function setColorBy(v){colorBy=v;
  document.getElementById('cb-origin').classList.toggle('active',v==='origin');
  document.getElementById('cb-bl').classList.toggle('active',v==='business_line');
  buildChart();}
function setChartType(v){chartType=v;
  document.getElementById('ct-bar').classList.toggle('active',v==='bar');
  document.getElementById('ct-line').classList.toggle('active',v==='line');
  buildChart();}
function buildChart(){
  const DAYS=activeDays();
  const rows=filteredRows();
  const series=new Map(); // key -> {day -> sum}
  rows.forEach(r=>{
    const k=colorBy==='origin'?r.origin:(r.business_line||'(none)');
    if(!series.has(k))series.set(k,new Map());
    const m=series.get(k); m.set(r.date,(m.get(r.date)||0)+(r.gross_revenue||0));
  });
  const keys=[...series.keys()].sort((a,b)=>{
    const t=k=>[...series.get(k).values()].reduce((x,y)=>x+y,0); return t(b)-t(a);});
  const W=1100,H=280,padL=70,padR=16,padT=14,padB=34;
  const dayTotals=DAYS.map(d=>keys.reduce((s,k)=>s+(series.get(k).get(d)||0),0));
  const maxSeries=Math.max(1,...keys.flatMap(k=>DAYS.map(d=>series.get(k).get(d)||0)));
  const maxY=(chartType==='bar'?Math.max(...dayTotals,1):maxSeries)*1.08;
  const bw=(W-padL-padR)/Math.max(DAYS.length,1), barW=Math.min(64,bw*0.62);
  const yOf=v=>H-padB-(H-padT-padB)*v/maxY;
  let svg=`<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto" xmlns="http://www.w3.org/2000/svg" font-family="Instrument Sans,sans-serif">`;
  for(let i=0;i<=4;i++){
    const y=padT+(H-padT-padB)*i/4, val=maxY*(1-i/4);
    svg+=`<line x1="${padL}" y1="${y}" x2="${W-padR}" y2="${y}" stroke="var(--border)" stroke-width="1"/>`;
    svg+=`<text x="${padL-8}" y="${y+4}" text-anchor="end" font-size="11" fill="var(--text-subtle)">${val>=1000?(val/1000).toFixed(0)+'k':val.toFixed(0)}</text>`;
  }
  const lblEvery=Math.max(1,Math.ceil(DAYS.length/16));
  DAYS.forEach((d,i)=>{
    if(i%lblEvery!==0&&i!==DAYS.length-1)return;
    svg+=`<text x="${padL+bw*i+bw/2}" y="${H-padB+16}" text-anchor="middle" font-size="11" fill="var(--text-muted)">${DAYS.length>90?d.slice(0,7):d.slice(5)}</text>`;
  });
  if(chartType==='bar'){
    DAYS.forEach((d,i)=>{
      const x0=padL+bw*i+(bw-barW)/2; let yCur=H-padB;
      keys.forEach((k,ki)=>{
        const v=series.get(k).get(d)||0; if(v<=0)return;
        const h=(H-padT-padB)*v/maxY; yCur-=h;
        svg+=`<rect x="${x0}" y="${yCur}" width="${barW}" height="${h}" fill="${CHART_COLORS[ki%CHART_COLORS.length]}" rx="2"><title>${d} · ${escapeHtml(k)} · ${fmtMoney(v)}</title></rect>`;
      });
      if(DAYS.length<=31) svg+=`<text x="${x0+barW/2}" y="${Math.max(padT+10,yOf(dayTotals[i])-6)}" text-anchor="middle" font-size="10" fill="var(--text-subtle)">${dayTotals[i]>=1000?(dayTotals[i]/1000).toFixed(1)+'k':dayTotals[i].toFixed(0)}</text>`;
    });
  } else {
    keys.forEach((k,ki)=>{
      const col=CHART_COLORS[ki%CHART_COLORS.length];
      const pts=DAYS.map((d,i)=>[padL+bw*i+bw/2,yOf(series.get(k).get(d)||0)]);
      svg+=`<polyline points="${pts.map(p=>p[0].toFixed(1)+','+p[1].toFixed(1)).join(' ')}" fill="none" stroke="${col}" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>`;
      pts.forEach((p,i)=>{const v=series.get(k).get(DAYS[i])||0;
        svg+=`<circle cx="${p[0].toFixed(1)}" cy="${p[1].toFixed(1)}" r="${DAYS.length>90?1.5:3.5}" fill="${col}"><title>${DAYS[i]} · ${escapeHtml(k)} · ${fmtMoney(v)}</title></circle>`;});
    });
  }
  svg+='</svg>';
  document.getElementById('chart').innerHTML=svg;
  document.getElementById('chart-legend').innerHTML=keys.map((k,i)=>
    `<span class="li"><span class="sw" style="background:${CHART_COLORS[i%CHART_COLORS.length]}"></span>${escapeHtml(k)}</span>`).join('')
    +'<span class="li muted">· USD</span>';
}

/* ══════════════ KPIs ══════════════ */
function buildKpis(){
  const rows=filteredRows();
  const gross=rows.reduce((s,r)=>s+(r.gross_revenue||0),0);
  const margin=rows.reduce((s,r)=>s+(r.margin||0),0);
  const imps=rows.reduce((s,r)=>s+(r.impressions||0),0);
  const deals=new Set(rows.map(r=>r.deal_id)).size;
  document.getElementById('kpis').innerHTML=`
    <div class="kpi-card"><div class="kpi-label">Gross revenue</div><div class="kpi-value">${fmtMoney(gross)}</div><div class="kpi-sub">USD · selected period</div></div>
    <div class="kpi-card"><div class="kpi-label">Margin</div><div class="kpi-value">${fmtMoney(margin)}</div><div class="kpi-sub">${gross?(100*margin/gross).toFixed(1):'0'}% of gross</div></div>
    <div class="kpi-card"><div class="kpi-label">Impressions</div><div class="kpi-value">${fmtInt(imps)}</div><div class="kpi-sub">where SSP metrics available</div></div>
    <div class="kpi-card"><div class="kpi-label">Active deals</div><div class="kpi-value">${fmtInt(deals)}</div><div class="kpi-sub">distinct deal_id in window</div></div>`;
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
function sortBy(col){
  if(sortCol===col)sortDir=-sortDir; else {sortCol=col;sortDir=-1;}
  page=1; rebuildTable();
}
function currentView(){
  const {dims,mets,rows}=aggregate();
  const cols=[...dims,...mets];
  let sorted=rows;
  if(sortCol&&cols.includes(sortCol)){
    const isMet=METRICS.includes(sortCol);
    sorted=[...rows].sort((a,b)=>{
      const va=a[sortCol],vb=b[sortCol];
      if(va==null&&vb==null)return 0; if(va==null)return 1; if(vb==null)return -1;
      return isMet?(va-vb)*sortDir:String(va).localeCompare(String(vb))*sortDir;});
  } else {
    sorted=[...rows].sort((a,b)=>a.date<b.date?-1:a.date>b.date?1:((b.gross_revenue||0)-(a.gross_revenue||0)));
  }
  return {dims,mets,cols,rows:sorted};
}
function rebuildTable(){
  const {dims,mets,cols,rows}=currentView();
  document.getElementById('tbl-head').innerHTML='<tr>'+cols.map(c=>
    `<th class="${METRICS.includes(c)?'number':''}" onclick="sortBy('${c}')">${c.replace(/_/g,' ')}${sortCol===c?(sortDir<0?' ▼':' ▲'):''}</th>`).join('')+'</tr>';
  const pages=Math.max(1,Math.ceil(rows.length/PAGE_SIZE));
  if(page>pages)page=pages;
  const slice=rows.slice((page-1)*PAGE_SIZE,page*PAGE_SIZE);
  const cell=(r,c)=>{
    const v=r[c];
    if(METRICS.includes(c))return `<td class="number">${MONEY.has(c)?fmtMoney(v):fmtInt(v)}</td>`;
    return `<td class="${v==null?'muted':''}" title="${escapeHtml(v)}">${v==null?'—':escapeHtml(v)}</td>`;};
  let body=slice.map(r=>'<tr>'+cols.map(c=>cell(r,c)).join('')+'</tr>').join('');
  const tot={}; mets.forEach(m=>tot[m]=rows.reduce((s,r)=>s+(r[m]||0),0));
  body+='<tr class="total-row">'+cols.map((c,i)=>{
    if(i===0)return `<td>Total (${fmtInt(rows.length)} rows)</td>`;
    if(METRICS.includes(c))return `<td class="number">${MONEY.has(c)?fmtMoney(tot[c]):fmtInt(tot[c])}</td>`;
    return '<td></td>';}).join('')+'</tr>';
  document.getElementById('tbl-body').innerHTML=body;
  document.getElementById('tbl-count').textContent=`${fmtInt(rows.length)} rows · grain: date + ${dims.slice(1).join(', ')||'(none)'}`;
  renderPagination('tbl-pag',pages,page,p=>{page=p;rebuildTable();});
}

/* ══════════════ CSV + email ══════════════ */
function csvMatrix(){
  const {cols,rows}=currentView();
  return [cols,...rows.map(r=>cols.map(c=>r[c]??''))];
}
function downloadCSV(matrix,name){
  const esc=v=>{if(v==null)return'';const s=String(v);return /[",\n]/.test(s)?'"'+s.replace(/"/g,'""')+'"':s;};
  const csv=matrix.map(r=>r.map(esc).join(',')).join('\n');
  const blob=new Blob([csv],{type:'text/csv;charset=utf-8;'});
  const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=name+'.csv';
  document.body.appendChild(a);a.click();a.remove();
}
const csvName=()=>{const d=activeDays();return 'deals_daily_'+(d[0]||'')+'_to_'+(d[d.length-1]||'');};
function tableCSV(){downloadCSV(csvMatrix(),csvName());}
function prepareEmail(){
  const to=document.getElementById('email-to').value.trim();
  if(!to){alert('Enter a recipient email first.');return;}
  downloadCSV(csvMatrix(),csvName());
  const d=activeDays();
  const subject=encodeURIComponent('Deals daily report '+(d[0]||'')+' → '+(d[d.length-1]||''));
  const body=encodeURIComponent(
    'Hi,\n\nPlease find attached the deals daily report ('+(d[0]||'')+' → '+(d[d.length-1]||'')+').\n\n'+
    'Note: the CSV ('+csvName()+'.csv) was just downloaded to your machine — attach it to this email before sending (mail links cannot attach files automatically).\n\n'+
    'Notes: all amounts are USD (STX converted from EUR at monthly average rates). BFM funnel metrics use Beachfront naming and are not 1:1 comparable with STX SSP metrics.\n\n'+
    'Analytics Team');
  window.location.href='mailto:'+encodeURIComponent(to)+'?subject='+subject+'&body='+body;
}

/* ══════════════ boot ══════════════ */
function rebuildAll(){buildKpis();buildChart();rebuildTable();}
updateRangeLabels();buildPickers();buildFilters();rebuildAll();
</script>
</body>
</html>
"""
