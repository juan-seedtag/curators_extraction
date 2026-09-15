/**
 * Apps Script wrapper for the Deals Health Dashboard.
 *
 * Serves the daily-built HTML straight from Drive through HtmlService — this
 * is what makes google.script.run available inside the page, which the
 * dashboard uses for per-user saved views (see "saved views" in
 * tools/report_generator.py). The GitHub Actions build keeps overwriting the
 * same Drive file ID, so this script always serves the latest version.
 *
 * Deploy: script.google.com → new project → paste this file →
 * Deploy → New deployment → Web app →
 *   Execute as: "User accessing the web app"  (REQUIRED — saved views are
 *                stored in each user's own UserProperties)
 *   Who has access: anyone in seedtag.com
 *
 * NOTE: if there is an existing viewer script that iframes the Drive preview,
 * replace its doGet with this one — an iframe of the Drive preview does NOT
 * expose google.script.run, and views would fall back to per-browser
 * localStorage only.
 */

// Same file the daily build overwrites (DRIVE_FILE_ID in generate_report.py).
const DASHBOARD_FILE_ID = '1kPq3o3RoNHnabU7rZvA6piHgDE3yOgBO';

function doGet() {
  const html = DriveApp.getFileById(DASHBOARD_FILE_ID).getBlob().getDataAsString('UTF-8');
  return HtmlService.createHtmlOutput(html)
    .setTitle('Deals Health Dashboard')
    .setXFrameOptionsMode(HtmlService.XFrameOptionsMode.ALLOWALL);
}

/* ── saved views ─────────────────────────────────────────────────────────
 * The page stores its whole views store ({views:{name:state}, def:name})
 * as one JSON string. UserProperties is automatically scoped to the
 * signed-in Google account, so every user gets their own store with no
 * user table or auth code. Property values are capped at 9 KB — plenty
 * for filter snapshots, but guard anyway so a failed save is explicit.
 */
const VIEWS_PROP = 'deals-views';

function saveViews(json) {
  if (typeof json !== 'string') throw new Error('expected a JSON string');
  if (json.length > 9000) throw new Error('views store too large (' + json.length + ' chars, max 9000) — delete some views');
  JSON.parse(json); // reject malformed payloads instead of storing them
  PropertiesService.getUserProperties().setProperty(VIEWS_PROP, json);
  return 'ok';
}

function loadViews() {
  return PropertiesService.getUserProperties().getProperty(VIEWS_PROP); // null if none
}
