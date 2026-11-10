"""gRPC settings that make autoscaling work.

A gRPC channel keeps one long-lived HTTP/2 connection. Behind a normal
Kubernetes Service every request would then go to the same pod forever, and
new pods added by the Horizontal Pod Autoscaler would get no traffic.

So: clients resolve a *headless* Service (DNS returns every pod IP) and
round-robin across them, and servers close connections after a short time,
which makes clients re-resolve and discover new pods.
"""

from __future__ import annotations

import grpc

CLIENT_OPTIONS = [
    ("grpc.lb_policy_name", "round_robin"),
    ("grpc.dns_min_time_between_resolutions_ms", 5000),
]

SERVER_OPTIONS = [
    ("grpc.max_connection_age_ms", 30_000),
    ("grpc.max_connection_age_grace_ms", 10_000),
]


def channel(target: str) -> grpc.Channel:
    return grpc.insecure_channel(target, options=CLIENT_OPTIONS)
