"""Pub/Sub publisher: Google Pub/Sub in the cloud, a log line or a list locally."""

from __future__ import annotations

import json
import logging
import os
from typing import Callable, Protocol

log = logging.getLogger("ubcommon.pubsub")


class Publisher(Protocol):
    def publish(self, topic: str, data: dict, **attributes: str) -> None: ...


class GcpPublisher:
    def __init__(self, project: str | None = None) -> None:
        from google.cloud import pubsub_v1

        self._client = pubsub_v1.PublisherClient()
        self._project = project or os.environ["GCP_PROJECT"]

    def publish(self, topic, data, **attributes):
        path = self._client.topic_path(self._project, topic)
        self._client.publish(path, json.dumps(data, default=str).encode(), **attributes).result(timeout=10)


class LocalPublisher:
    """Keeps messages in memory and optionally hands them to a callback.

    Locally there is no Cloud Function, so a service can pass a callback that
    does what the function would do.
    """

    def __init__(self, sink: Callable[[str, dict, dict], None] | None = None) -> None:
        self.messages: list[tuple[str, dict, dict]] = []
        self.sink = sink

    def publish(self, topic, data, **attributes):
        self.messages.append((topic, data, attributes))
        log.info("pubsub[%s] %s", topic, json.dumps(data, default=str))
        if self.sink:
            self.sink(topic, data, attributes)


def get_publisher(sink: Callable[[str, dict, dict], None] | None = None) -> Publisher:
    """PUBSUB=gcp in GCP; anything else publishes locally."""
    if os.getenv("PUBSUB", "local") == "gcp":
        return GcpPublisher()
    return LocalPublisher(sink)
