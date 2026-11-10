"""Runs the real stream SQL (sql/10_views.sql) in a local Flink over a file of
events, and checks every window result.

Needs Java and `pip install apache-flink` (1.17.x on Python 3.10 matches Dataproc 2.2).
Skipped automatically where PyFlink isn't installed.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

pyflink = pytest.importorskip("pyflink")
from pyflink.table import EnvironmentSettings, TableEnvironment  # noqa: E402

SQL_DIR = Path(__file__).resolve().parents[1] / "sql"
T0 = int(datetime(2026, 11, 10, 6, 30, tzinfo=timezone.utc).timestamp() * 1000)   # 12:00 India time
MIN, SEC = 60_000, 1_000


def event(at_ms, status="COMPLETED", *, frm="acct-normal", to="acct-shop", customer="cust-1", amount=50_000,
          code=None):
    return {"txn_id": f"t{at_ms}{frm}{to}{amount}", "customer_id": customer, "from_account_id": frm,
            "to_account_id": to, "amount": amount, "channel": "TRANSFER", "status": status,
            "decline_code": code, "risk_decision": "ALLOW", "risk_score": 0, "event_time_ms": T0 + at_ms}


def scenario():
    events = []
    # minute 0: ordinary traffic from three customers, plus two throttled and one blocked
    for i, customer in enumerate(["cust-1", "cust-2", "cust-3", "cust-1"]):
        events.append(event(i * 5 * SEC, customer=customer, frm=f"acct-{customer}", amount=100_000))
    events.append(event(21 * SEC, "DECLINED", customer="cust-2", frm="acct-cust-2", code="RATE_LIMITED"))
    events.append(event(22 * SEC, "DECLINED", customer="cust-2", frm="acct-cust-2", code="RATE_LIMITED"))
    events.append(event(23 * SEC, "DECLINED", customer="cust-3", frm="acct-cust-3", code="RISK_BLOCKED"))
    events.append(event(24 * SEC, "HELD", customer="cust-3", frm="acct-cust-3"))
    # minute 1: a bot fires 9 payments from one account (velocity burst), 6 of them declined (decline burst)
    for i in range(9):
        status, code = ("DECLINED", "INSUFFICIENT_FUNDS") if i >= 3 else ("COMPLETED", None)
        events.append(event(1 * MIN + i * 2 * SEC, status, frm="acct-bot", customer="cust-bot", code=code))
    # minutes 2-4: six different victims pay into one mule account
    for i in range(6):
        events.append(event(2 * MIN + i * 20 * SEC, frm=f"acct-victim-{i}", to="acct-mule",
                            customer=f"cust-victim-{i}", amount=900_000))
    # minute 5: three tiny payments from a stolen account (probing)
    for i in range(3):
        events.append(event(5 * MIN + i * 10 * SEC, frm="acct-stolen", customer="cust-stolen", amount=500))
    # minutes 6-26: three payments just under Rs 50,000 (structuring)
    for i, amount in enumerate([4_990_000, 4_950_000, 4_880_000]):
        events.append(event(6 * MIN + i * 10 * MIN, frm="acct-smurf", customer="cust-smurf", amount=amount))
    # a late event far in the future moves the watermark past every window above
    events.append(event(200 * MIN, customer="cust-1", frm="acct-cust-1"))
    return events


@pytest.fixture(scope="module")
def flink(tmp_path_factory):
    source = tmp_path_factory.mktemp("events") / "events.json"
    source.write_text("\n".join(json.dumps(e) for e in scenario()))
    env = TableEnvironment.create(EnvironmentSettings.in_streaming_mode())
    env.get_config().set("parallelism.default", "1")
    env.get_config().set("table.local-time-zone", "UTC")
    env.execute_sql(f"""
        CREATE TABLE txn_events (
            txn_id STRING, customer_id STRING, from_account_id STRING, to_account_id STRING, amount BIGINT,
            channel STRING, status STRING, decline_code STRING, risk_decision STRING, risk_score INT,
            event_time_ms BIGINT,
            event_time AS TO_TIMESTAMP_LTZ(event_time_ms, 3),
            WATERMARK FOR event_time AS event_time - INTERVAL '5' SECOND
        ) WITH ('connector' = 'filesystem', 'path' = '{source}', 'format' = 'json')
    """)
    for statement in (SQL_DIR / "10_views.sql").read_text().split(";"):
        lines = [line for line in statement.splitlines() if not line.strip().startswith("--")]
        if "".join(lines).strip():
            env.execute_sql("\n".join(lines))
    return env


def rows(flink, view):
    with flink.execute_sql(f"SELECT * FROM {view}").collect() as results:
        return [tuple(r) for r in results]


def test_analytics_per_minute(flink):
    """The required stateful, time-windowed aggregation, including unique users per minute."""
    windows = {r[0]: r for r in rows(flink, "analytics_per_minute")}
    first = windows["2026-11-10 06:30:00.000"]
    # (window_start, window_end, events, completed, declined, held, challenged, rate_limited, risk_blocked,
    #  unique_customers, completed_amount)
    assert first == ("2026-11-10 06:30:00.000", "2026-11-10 06:31:00.000", 8, 4, 3, 1, 0, 2, 1, 3, 400_000)
    bot_minute = windows["2026-11-10 06:31:00.000"]
    assert (bot_minute[2], bot_minute[3], bot_minute[4], bot_minute[9]) == (9, 3, 6, 1)
    assert "2026-11-10 06:38:00.000" not in windows                 # no events, no window


def signals(flink, signal_type):
    return [r for r in rows(flink, "risk_signals_all") if r[0] == signal_type]


def test_velocity_and_decline_bursts(flink):
    assert signals(flink, "VELOCITY_BURST") == [("VELOCITY_BURST", "acct-bot", "2026-11-10 06:32:00.000", 9)]
    assert signals(flink, "DECLINE_BURST") == [("DECLINE_BURST", "acct-bot", "2026-11-10 06:32:00.000", 6)]


def test_mule_fan_in(flink):
    found = signals(flink, "MULE_FAN_IN")
    assert found and {r[1] for r in found} == {"acct-mule"}
    assert max(r[3] for r in found) == 6                            # six distinct senders
    assert all(r[3] >= 5 for r in found)
    # the sliding window reports it once per slide while five or more senders are inside it
    assert len(found) > 1


def test_micro_probing(flink):
    found = signals(flink, "MICRO_PROBING")
    assert found and {r[1] for r in found} == {"acct-stolen"} and {r[3] for r in found} == {3}


def test_structuring(flink):
    found = signals(flink, "STRUCTURING")
    assert found and {r[1] for r in found} == {"acct-smurf"} and {r[3] for r in found} == {3}


def test_ordinary_customers_raise_no_signals(flink):
    flagged = {r[1] for r in rows(flink, "risk_signals_all")}
    assert flagged == {"acct-bot", "acct-mule", "acct-stolen", "acct-smurf"}


def test_job_statements_fit_the_sink_tables(flink):
    """Plans the real INSERTs (sql/20_inserts.sql) against the real sink schemas
    (sql/00_kafka_tables.sql), with the Kafka connector swapped for a blackhole,
    so a column or type mismatch is caught here and not on Dataproc."""
    import re

    ddl = (SQL_DIR / "00_kafka_tables.sql").read_text()
    for table in ("analytics_results", "risk_signals"):
        columns = re.search(rf"CREATE TABLE {table} \((.*?)\) WITH", ddl, re.S).group(1)
        flink.execute_sql(f"CREATE TABLE {table} ({columns}) WITH ('connector' = 'blackhole')")
    inserts = re.findall(r"(INSERT INTO .*?);", (SQL_DIR / "20_inserts.sql").read_text(), re.S)
    assert len(inserts) == 2
    for statement in inserts:
        assert "Sink" in flink.explain_sql(statement)
