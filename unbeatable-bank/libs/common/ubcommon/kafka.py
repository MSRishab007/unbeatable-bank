"""Small Kafka consumer loop used by event-driven services."""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Callable

log = logging.getLogger("ubcommon.kafka")

Handler = Callable[[str, dict], None]  # (topic, event)


def consume(bootstrap: str, group: str, topics: list[str], handler: Handler,
            stop: threading.Event, max_attempts: int = 5) -> None:
    """Poll forever. Offsets are committed only after the handler succeeds, so a
    crash re-delivers the event; handlers must be idempotent (dedupe on event_id)."""
    from confluent_kafka import Consumer

    consumer = Consumer({
        "bootstrap.servers": bootstrap,
        "group.id": group,
        "enable.auto.commit": False,
        "auto.offset.reset": "earliest",
    })
    consumer.subscribe(topics)
    log.info("consuming %s as group %s", topics, group)
    try:
        while not stop.is_set():
            msg = consumer.poll(1.0)
            if msg is None:
                continue
            if msg.error():
                log.warning("kafka error: %s", msg.error())
                continue
            try:
                event = json.loads(msg.value())
            except (TypeError, ValueError):
                log.error("skipping non-JSON message on %s", msg.topic())
                consumer.commit(msg)
                continue
            for attempt in range(1, max_attempts + 1):
                try:
                    handler(msg.topic(), event)
                    break
                except Exception:
                    log.exception("handler failed (attempt %d/%d) for %s", attempt, max_attempts, msg.topic())
                    if attempt == max_attempts:
                        log.error("giving up on event %s", event.get("event_id"))
                    else:
                        time.sleep(min(2 ** attempt, 15))
            consumer.commit(msg)
    finally:
        consumer.close()


def start_consumer_thread(bootstrap: str, group: str, topics: list[str], handler: Handler) -> threading.Event:
    stop = threading.Event()
    threading.Thread(target=consume, args=(bootstrap, group, topics, handler, stop),
                     name=f"kafka-{group}", daemon=True).start()
    return stop
