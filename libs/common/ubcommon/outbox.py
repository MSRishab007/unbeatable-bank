"""Transactional outbox relay.

Services write events to their own `<schema>.outbox` table in the same database
transaction as the business change. This relay publishes those rows to Kafka
and marks them published. Delivery is at-least-once: every message carries an
`event_id`, and consumers ignore ids they have already processed.

Run next to a service (same image, same database user):
    python -m ubcommon.outbox --schema ledger

Environment:
    DATABASE_URL         the service's own database URL
    KAFKA_BOOTSTRAP      Kafka bootstrap servers; if unset, events are only logged
    OUTBOX_HTTP_SINKS    optional "topic=url,topic=url": POST events to HTTP endpoints
                         instead of Kafka (Kafka-free local runs)
    OUTBOX_METRICS_PORT  Prometheus port (default 9102, 0 disables)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import threading
import time
from typing import Protocol

from prometheus_client import Counter, start_http_server

log = logging.getLogger("ubcommon.outbox")

PUBLISHED = Counter("outbox_published_total", "Events published from the outbox", ["schema", "topic"])
FAILURES = Counter("outbox_publish_failures_total", "Failed publish attempts", ["schema"])


class EventPublisher(Protocol):
    def send(self, topic: str, key: str, value: bytes, headers: dict[str, str]) -> None: ...

    def flush(self) -> None:
        """Block until everything sent so far is confirmed; raise if anything failed."""


class LogPublisher:
    def send(self, topic, key, value, headers):
        log.info("outbox[%s] key=%s %s", topic, key, value.decode())

    def flush(self):
        pass


class MemoryPublisher:
    """For tests."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, dict]] = []
        self.fail = False

    def send(self, topic, key, value, headers):
        if self.fail:
            raise RuntimeError("broker down")
        self.sent.append((topic, key, json.loads(value)))

    def flush(self):
        pass


class KafkaPublisher:
    def __init__(self, bootstrap: str) -> None:
        from confluent_kafka import Producer

        self._errors: list[str] = []
        self._producer = Producer({
            "bootstrap.servers": bootstrap,
            "enable.idempotence": True,   # no duplicates from producer retries
            "acks": "all",
            "linger.ms": 5,
        })

    def _on_delivery(self, err, _msg):
        if err is not None:
            self._errors.append(str(err))

    def send(self, topic, key, value, headers):
        self._producer.produce(topic, key=key, value=value, headers=list(headers.items()),
                               on_delivery=self._on_delivery)

    def flush(self):
        remaining = self._producer.flush(10)
        errors, self._errors = self._errors, []
        if remaining or errors:
            raise RuntimeError(f"kafka publish failed: {remaining} undelivered, errors={errors[:3]}")


class HttpPublisher:
    """POST each event to an HTTP endpoint chosen by topic."""

    def __init__(self, sinks: dict[str, str]) -> None:
        import httpx

        self._sinks = sinks
        self._http = httpx.Client(timeout=5)

    def send(self, topic, key, value, headers):
        url = self._sinks.get(topic)
        if url is None:
            return
        r = self._http.post(url, json={"topic": topic, "key": key, "value": json.loads(value)})
        r.raise_for_status()

    def flush(self):
        pass


def relay_once(conn, schema: str, publisher: EventPublisher, batch: int = 200) -> int:
    """Publish one batch. Rows stay unpublished (and are retried) if publishing fails."""
    with conn.transaction():
        rows = conn.execute(
            f"""
            SELECT event_id, topic, event_key, event_type, payload
            FROM {schema}.outbox
            WHERE published_at IS NULL
            ORDER BY created_at, event_id
            LIMIT %s
            FOR UPDATE SKIP LOCKED
            """,
            (batch,),
        ).fetchall()
        if not rows:
            return 0
        for event_id, topic, key, event_type, payload in rows:
            value = {**payload, "event_id": str(event_id), "event_type": event_type}
            publisher.send(topic, key, json.dumps(value).encode(),
                           {"event_id": str(event_id), "event_type": event_type})
        publisher.flush()
        conn.execute(f"UPDATE {schema}.outbox SET published_at = now() WHERE event_id = ANY(%s)",
                     ([r[0] for r in rows],))
    for _, topic, *_ in rows:
        PUBLISHED.labels(schema, topic).inc()
    return len(rows)


def make_publisher() -> EventPublisher:
    sinks = os.getenv("OUTBOX_HTTP_SINKS")
    if sinks:
        return HttpPublisher(dict(pair.split("=", 1) for pair in sinks.split(",") if pair))
    bootstrap = os.getenv("KAFKA_BOOTSTRAP")
    return KafkaPublisher(bootstrap) if bootstrap else LogPublisher()


def run(schema: str, stop: threading.Event | None = None, idle_sleep: float = 0.5) -> None:
    import psycopg

    stop = stop or threading.Event()
    publisher = make_publisher()
    log.info("outbox relay for schema %s using %s", schema, type(publisher).__name__)
    while not stop.is_set():
        try:
            with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
                while not stop.is_set():
                    if relay_once(conn, schema, publisher) == 0:
                        stop.wait(idle_sleep)
        except Exception:
            FAILURES.labels(schema).inc()
            log.exception("outbox relay error; retrying in 2s")
            stop.wait(2)


def main() -> None:
    from .logging_setup import setup_logging

    parser = argparse.ArgumentParser()
    parser.add_argument("--schema", required=True)
    args = parser.parse_args()
    if not args.schema.isidentifier():
        raise SystemExit("invalid schema name")
    setup_logging(f"{args.schema}-outbox")
    port = int(os.getenv("OUTBOX_METRICS_PORT", "9102"))
    if port:
        start_http_server(port)
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    run(args.schema, stop)


if __name__ == "__main__":
    main()
