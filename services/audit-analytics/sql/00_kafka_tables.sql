-- Audit Analytics: job settings and Kafka tables.
-- ${KAFKA_BOOTSTRAP} and ${CHECKPOINT_DIR} are filled in by submit.sh.

SET 'pipeline.name' = 'audit-analytics';
SET 'execution.runtime-mode' = 'streaming';
SET 'parallelism.default' = '1';
-- Also for every table operator: Dataproc computes the cluster's default parallelism from
-- its worker count, and on a single-node cluster that comes out as 0, which Flink rejects
-- ("Parallelism must be at least one").
SET 'table.exec.resource.default-parallelism' = '1';

-- State (open windows, distinct-count sets) is checkpointed to Cloud Storage,
-- so a restarted job resumes its windows instead of losing them.
SET 'execution.checkpointing.interval' = '30 s';
SET 'state.checkpoints.dir' = '${CHECKPOINT_DIR}';

-- A Kafka partition with no traffic must not hold back the watermark for the others.
SET 'table.exec.source.idle-timeout' = '10 s';

CREATE TABLE txn_events (
    txn_id          STRING,
    customer_id     STRING,
    from_account_id STRING,
    to_account_id   STRING,
    amount          BIGINT,
    channel         STRING,
    status          STRING,
    decline_code    STRING,
    risk_decision   STRING,
    risk_score      INT,
    event_time_ms   BIGINT,
    event_time AS TO_TIMESTAMP_LTZ(event_time_ms, 3),
    WATERMARK FOR event_time AS event_time - INTERVAL '5' SECOND
) WITH (
    'connector' = 'kafka',
    'topic' = 'txn-events',
    'properties.bootstrap.servers' = '${KAFKA_BOOTSTRAP}',
    'properties.group.id' = 'audit-analytics',
    'scan.startup.mode' = 'group-offsets',
    'properties.auto.offset.reset' = 'latest',
    'format' = 'json',
    'json.ignore-parse-errors' = 'true'
);

CREATE TABLE analytics_results (
    window_start     STRING,
    window_end       STRING,
    events           BIGINT,
    completed        BIGINT,
    declined         BIGINT,
    held             BIGINT,
    challenged       BIGINT,
    rate_limited     BIGINT,
    risk_blocked     BIGINT,
    unique_customers BIGINT,
    completed_amount BIGINT
) WITH (
    'connector' = 'kafka',
    'topic' = 'analytics-results',
    'properties.bootstrap.servers' = '${KAFKA_BOOTSTRAP}',
    'format' = 'json'
);

CREATE TABLE risk_signals (
    signal_type STRING,
    account_id  STRING,
    window_end  STRING,
    `value`     BIGINT
) WITH (
    'connector' = 'kafka',
    'topic' = 'risk-signals',
    'properties.bootstrap.servers' = '${KAFKA_BOOTSTRAP}',
    'key.format' = 'raw',
    'key.fields' = 'account_id',
    'value.format' = 'json'
);
