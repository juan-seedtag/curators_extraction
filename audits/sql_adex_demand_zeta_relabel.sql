-- Seat 119 (advertiser Bidswitch) was renamed 'Zeta DSP' -> 'Zeta Global' in reporting_beachfront_seat_name.
-- Beachfront rows in reporting_adex_demand carry the seat name in BOTH dsp_group_name and channel_id
-- (inherited from reporting_closing_bfm_demand), with connection_type = 'BidSwitch'.
-- Seedtag-side Zeta rows (channel Rubicon / Sharethrough / MagniteDirect / ZetaBidswitch) are NOT touched.
-- 2026-09-14 is excluded: that day is incomplete in the table and must be reloaded anyway.
-- Run alone, no trailing semicolon.

-- STEP 1 — reporting_adex_demand (the request)
UPDATE st_datalakehouse.analytics.reporting_adex_demand
SET dsp_group_name = 'Zeta Global', channel_id = 'Zeta Global'
WHERE dsp_group_name = 'Zeta DSP' AND channel_id = 'Zeta DSP' AND connection_type = 'BidSwitch' AND date <= DATE '2026-09-13'

-- STEP 2 (optional, keeps the upstream table consistent so a future adex re-run of any day does not bring 'Zeta DSP' back)
UPDATE st_datalakehouse.analytics.reporting_closing_bfm_demand
SET dsp_group_name = 'Zeta Global', channel_id = 'Zeta Global'
WHERE dsp_group_name = 'Zeta DSP' AND channel_id = 'Zeta DSP' AND connection_type = 'BidSwitch'
