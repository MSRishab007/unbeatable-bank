"""Helpers for tests that need another service's real code.

Every service's package is called `app`, so a test that wants a second service
in the same process loads it under another name:

    ledger = load_app("ledger", "ledger_app")
    from ledger_app import posting
"""

from __future__ import annotations

import importlib.util
import socket
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def load_app(service: str, alias: str):
    """Import services/<service>/app as a package named `alias`."""
    if alias in sys.modules:
        return sys.modules[alias]
    path = REPO_ROOT / "services" / service / "app"
    spec = importlib.util.spec_from_file_location(alias, path / "__init__.py",
                                                  submodule_search_locations=[str(path)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[alias] = module
    spec.loader.exec_module(module)
    return module


_handed_out: set[int] = set()


def free_port() -> int:
    """A port for a test server, chosen BELOW the range Linux uses for outgoing connections.

    Asking the OS for "any free port" returns one from the ephemeral range (32768-60999),
    the same range outgoing connections get their source ports from. Test nodes start
    talking to each other at once, so one of those connections could take a port picked
    for a node that hasn't started yet, and that node could not listen on it (seen on
    GitHub's runners as "connection refused"). Ports below 32768 are never handed to
    outgoing connections.
    """
    import random

    for _ in range(500):
        port = random.randint(20000, 32000)
        if port in _handed_out:
            continue
        try:
            with socket.socket() as s:                          # the test clients use 127.0.0.1
                s.bind(("0.0.0.0", port))
        except OSError:
            continue
        _handed_out.add(port)
        return port
    raise RuntimeError("no free port between 20000 and 32000")


class LocalDynaCluster:
    """A real DynaStore cluster (N nodes, gRPC over localhost) inside the test process."""

    def __init__(self, tmp_path, size: int = 3, **node_kwargs) -> None:
        load_app("dynastore", "dynastore_app")
        from dynastore_app import server
        from dynastore_app.node import Node

        self.peers = {f"node-{i}": f"127.0.0.1:{free_port()}" for i in range(size)}
        kwargs = dict(n=3, r=2, w=2, heartbeat_interval=0.1, hint_interval=0.1, sync_interval=0,
                      sweep_interval=0, rpc_timeout=0.3)
        kwargs.update(node_kwargs)
        self.nodes, self.servers = {}, {}
        for node_id, address in self.peers.items():
            node = Node(node_id, self.peers, str(Path(tmp_path) / node_id / "dynastore.db"), **kwargs)
            self.nodes[node_id] = node
            self.servers[node_id] = server.start(node, int(address.split(":")[1]), host="127.0.0.1")
        for node in self.nodes.values():        # every node listens before any starts calling the others
            node.start_background()

    @property
    def targets(self) -> str:
        return ",".join(self.peers.values())

    def client(self, timeout: float = 3.0):
        from ubcommon.dynaclient import DynaClient

        return DynaClient(self.targets, timeout=timeout)

    def set_down(self, down: bool) -> None:
        """Make the whole store unreachable (or bring it back)."""
        for node in self.nodes.values():
            node.set_faults([], down)

    def wait_healthy(self, timeout: float = 5.0) -> None:
        """Block until every node sees every other node as alive again (after a fault test),
        so the next test starts from a settled cluster."""
        import time

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if all(n["alive"] for node in self.nodes.values() for n in node.status()["nodes"]):
                return
            time.sleep(0.05)
        raise TimeoutError("DynaStore test cluster did not become healthy")

    def shutdown(self) -> None:
        for node_id in list(self.nodes):
            self.servers.pop(node_id).stop(0).wait()
            self.nodes.pop(node_id).stop()
