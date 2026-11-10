# Audit Analytics (Flink SQL)

The real-time stream processing service. It is a Flink SQL job, not a Python
service, and runs on a managed Dataproc cluster.

| File | What it does |
|---|---|
| `sql/00_kafka_tables.sql` | Source table on the `txn-events` Kafka topic; sink tables on `analytics-results` and `risk-signals` |
| `sql/10_views.sql` | The stateful, time-windowed aggregations (event time, watermarks) |
| `sql/20_inserts.sql` | Wires the views to the sink topics as one statement set |
| `submit.sh` | Run on the Dataproc master: fetches the Kafka connector and submits the job |

Windows:

- **Per minute (tumbling):** events, completed, declined, held, challenged,
  rate-limited, risk-blocked, **unique customers**, completed amount.
- **Velocity (1 min tumbling, per account):** many attempts from one account.
- **Micro-payment probing (5 min hopping, per account):** several tiny payments.
- **Structuring (1 h hopping, per account):** repeated amounts just under Rs 50,000.
- **Mule fan-in (10 min hopping, per receiving account):** sizeable payments from many senders.

Results go to `analytics-results` (dashboard) and `risk-signals` (Fraud Alert
raises alerts and feeds them back to Risk Detection through DynaStore).

## Test locally

Needs Java 11+ and Python 3.11 (PyFlink 1.20 does not support newer Pythons):

```bash
pip install -r requirements-dev.txt
pytest -q tests
```

The tests run the same SQL files on a local Flink mini-cluster, with the
Kafka tables swapped for files. They already caught one real bug:
`COUNT(*) FILTER (...)` gave wrong counts next to `COUNT(DISTINCT ...)`.
