-- Audit Analytics: the stream queries.
--
-- Input: the table txn_events (one row per payment decision, see 00_kafka_tables.sql).
-- These views don't depend on where the rows come from, so the tests run the same
-- SQL over a file instead of Kafka.
--
-- All windows are event-time windows: they are driven by when a payment was
-- decided (event_time), not by when Flink happened to read it, so replaying the
-- topic gives the same results.

-- ---------------------------------------------------------------------------
-- 1. System statistics per 1-minute tumbling window  ->  topic analytics-results
--    Throughput, outcomes, rate-limit and risk decisions, and unique customers.
-- ---------------------------------------------------------------------------
-- Conditional counts are written as COUNT(CASE ...) rather than COUNT(*) FILTER (...):
-- combined with COUNT(DISTINCT ...) in one window aggregate, FILTER gave wrong
-- counts on Flink 1.20 (caught by tests/test_queries.py).
CREATE TEMPORARY VIEW analytics_per_minute AS
SELECT
    CAST(window_start AS STRING)                                             AS window_start,
    CAST(window_end AS STRING)                                               AS window_end,
    COUNT(*)                                                                 AS events,
    COUNT(CASE WHEN status = 'COMPLETED' THEN 1 END)                        AS completed,
    COUNT(CASE WHEN status = 'DECLINED' THEN 1 END)                         AS declined,
    COUNT(CASE WHEN status = 'HELD' THEN 1 END)                             AS held,
    COUNT(CASE WHEN status = 'CHALLENGE' THEN 1 END)                        AS challenged,
    COUNT(CASE WHEN decline_code = 'RATE_LIMITED' THEN 1 END)               AS rate_limited,
    COUNT(CASE WHEN decline_code = 'RISK_BLOCKED' THEN 1 END)               AS risk_blocked,
    COUNT(DISTINCT customer_id)                                              AS unique_customers,
    SUM(CASE WHEN status = 'COMPLETED' THEN amount ELSE 0 END)               AS completed_amount
FROM TABLE(TUMBLE(TABLE txn_events, DESCRIPTOR(event_time), INTERVAL '1' MINUTE))
GROUP BY window_start, window_end;

-- ---------------------------------------------------------------------------
-- 2. Fraud patterns no single payment reveals  ->  topic risk-signals
--    Fraud Alert turns these into alerts and cases, and feeds them back to the
--    real-time risk engine.
-- ---------------------------------------------------------------------------

-- A burst of attempts from one account inside a minute (scripts, bots).
CREATE TEMPORARY VIEW signal_velocity_burst AS
SELECT 'VELOCITY_BURST' AS signal_type, from_account_id AS account_id,
       CAST(window_end AS STRING) AS window_end, COUNT(*) AS `value`
FROM TABLE(TUMBLE(TABLE txn_events, DESCRIPTOR(event_time), INTERVAL '1' MINUTE))
WHERE status IN ('COMPLETED', 'DECLINED', 'HELD', 'CHALLENGE')
GROUP BY window_start, window_end, from_account_id
HAVING COUNT(*) >= 8;

-- Many declines from one account inside a minute (guessing limits, probing rules).
CREATE TEMPORARY VIEW signal_decline_burst AS
SELECT 'DECLINE_BURST' AS signal_type, from_account_id AS account_id,
       CAST(window_end AS STRING) AS window_end, COUNT(*) AS `value`
FROM TABLE(TUMBLE(TABLE txn_events, DESCRIPTOR(event_time), INTERVAL '1' MINUTE))
WHERE status = 'DECLINED' AND decline_code <> 'RATE_LIMITED'
GROUP BY window_start, window_end, from_account_id
HAVING COUNT(*) >= 5;

-- Sizeable payments (Rs 5,000 or more) arriving in one account from many different
-- senders within 10 minutes: the shape of a mule account collecting stolen funds.
-- The amount floor keeps ordinary shops, which receive many small payments, out of it.
CREATE TEMPORARY VIEW signal_mule_fan_in AS
SELECT 'MULE_FAN_IN' AS signal_type, to_account_id AS account_id,
       CAST(window_end AS STRING) AS window_end, COUNT(DISTINCT from_account_id) AS `value`
FROM TABLE(HOP(TABLE txn_events, DESCRIPTOR(event_time), INTERVAL '1' MINUTE, INTERVAL '10' MINUTE))
WHERE status = 'COMPLETED' AND to_account_id IS NOT NULL AND amount >= 500000
GROUP BY window_start, window_end, to_account_id
HAVING COUNT(DISTINCT from_account_id) >= 5;

-- Several tiny payments (Rs 10 or less) in 5 minutes: testing whether a stolen account works.
CREATE TEMPORARY VIEW signal_micro_probing AS
SELECT 'MICRO_PROBING' AS signal_type, from_account_id AS account_id,
       CAST(window_end AS STRING) AS window_end, COUNT(*) AS `value`
FROM TABLE(HOP(TABLE txn_events, DESCRIPTOR(event_time), INTERVAL '1' MINUTE, INTERVAL '5' MINUTE))
WHERE status = 'COMPLETED' AND amount <= 1000
GROUP BY window_start, window_end, from_account_id
HAVING COUNT(*) >= 3;

-- Repeated payments just under Rs 50,000 within an hour: splitting a large sum to stay below a threshold.
CREATE TEMPORARY VIEW signal_structuring AS
SELECT 'STRUCTURING' AS signal_type, from_account_id AS account_id,
       CAST(window_end AS STRING) AS window_end, COUNT(*) AS `value`
FROM TABLE(HOP(TABLE txn_events, DESCRIPTOR(event_time), INTERVAL '5' MINUTE, INTERVAL '60' MINUTE))
WHERE status IN ('COMPLETED', 'HELD') AND amount BETWEEN 4750000 AND 4999999
GROUP BY window_start, window_end, from_account_id
HAVING COUNT(*) >= 3;

CREATE TEMPORARY VIEW risk_signals_all AS
SELECT * FROM signal_velocity_burst
UNION ALL SELECT * FROM signal_decline_burst
UNION ALL SELECT * FROM signal_mule_fan_in
UNION ALL SELECT * FROM signal_micro_probing
UNION ALL SELECT * FROM signal_structuring;
