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


# For DynaStore, where each node is its own address and nodes restart:
#   - own connection pool: by default every channel in a process shares one connection per
#     address, so one failed call to a restarting node makes every other channel to it
#     fail at once too, until the shared connection's retry timer runs out;
#   - retry a lost connection within 2 s (gRPC's default backs off up to 120 s), so a node
#     that comes back is used again quickly.
NODE_OPTIONS = [
    ("grpc.use_local_subchannel_pool", 1),
    ("grpc.initial_reconnect_backoff_ms", 200),
    ("grpc.min_reconnect_backoff_ms", 200),
    ("grpc.max_reconnect_backoff_ms", 2000),
]


def channel(target: str) -> grpc.Channel:
    return grpc.insecure_channel(target, options=CLIENT_OPTIONS)
