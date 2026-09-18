-- Re-resolve the DSP of Beachfront rows in reporting_curation_deals from the
-- (updated) seat table. Only rows whose stored dsp is the intermediary label
-- ('TheTradeDesk' / 'BidSwitch') and whose seat resolves to a real buyer change.
-- Idempotent: a second run matches nothing (dsp is no longer the intermediary).
-- Verified 2026-09-15: 11,333 rows / ~EUR 989k move; 0 grain collisions.
MERGE INTO st_datalakehouse.analytics.reporting_curation_deals t
USING (
    SELECT seat_id,
           advertiser,
           CASE advertiser WHEN 'The Trade Desk' THEN 'TheTradeDesk' ELSE 'BidSwitch' END AS intermediary_label,
           CASE WHEN advertiser = 'The Trade Desk' THEN 'Walmart' ELSE max(seat_name) END AS new_dsp
    FROM st_datalakehouse.analytics.reporting_beachfront_seat_name
    WHERE (advertiser = 'Bidswitch'
           OR (advertiser = 'The Trade Desk' AND (seat_name LIKE '%WMT%' OR seat_name LIKE '%Walmart%')))
      AND seat_id <> seat_name
      AND NOT regexp_like(seat_name, '^[0-9]+$')
    GROUP BY 1, 2
) s
ON  t.origin = 'BFM'
AND t.seat_id = s.seat_id
AND t.channel_id = s.intermediary_label
AND t.dsp = s.intermediary_label
WHEN MATCHED THEN UPDATE SET dsp = s.new_dsp
