-- Audit Analytics: start both pipelines as one Flink job.

EXECUTE STATEMENT SET
BEGIN
    INSERT INTO analytics_results SELECT * FROM analytics_per_minute;
    INSERT INTO risk_signals SELECT * FROM risk_signals_all;
END;
