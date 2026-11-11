"""Runs a real multi-node cluster inside the test process: every node has its
own storage file and gRPC server, and they talk over localhost."""

import socket
import time

import pytest

from ubcommon.dynaclient import DynaClient

from app import server
from app.node import Node


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Cluster:
    def __init__(self, tmp_path, size=5, **node_kwargs):
        self.tmp_path = tmp_path
        self.ids = [f"node-{i}" for i in range(size)]
        used_ports = set()

        def unique_port() -> int:
            while True:
                port = free_port()
                if port not in used_ports:
                    used_ports.add(port)
                    return port

        self.peers = {node_id: f"127.0.0.1:{unique_port()}" for node_id in self.ids}
        self.kwargs = dict(n=3, r=2, w=2, heartbeat_interval=0.1, hint_interval=0.1, sync_interval=0,
                           sweep_interval=0, rpc_timeout=0.3)
        self.kwargs.update(node_kwargs)
        self.nodes: dict[str, Node] = {}
        self.servers = {}
        self.black_holes = []
        for node_id in self.ids:
            self.start(node_id)

    def path(self, node_id):
        return str(self.tmp_path / node_id / "dynastore.db")

    def start(self, node_id):
        node = Node(node_id, self.peers, self.path(node_id), **self.kwargs)
        self.nodes[node_id] = node
        self.servers[node_id] = server.start(node, int(self.peers[node_id].split(":")[1]))
        node.start_background()
        return node

    def stop(self, node_id):
        """Crash a node: its server and background work stop; its disk survives."""
        self.servers.pop(node_id).stop(0).wait()
        self.nodes.pop(node_id).stop()

    def hang(self, node_id):
        """A node that is there but never answers (a frozen process, a pod whose
        network went away): connections are accepted and then nothing happens."""
        self.stop(node_id)
        hole = socket.socket()
        hole.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        hole.bind(("127.0.0.1", int(self.peers[node_id].split(":")[1])))
        hole.listen(64)                      # never accept(): the kernel queues, nobody replies
        self.black_holes.append(hole)

    def wipe(self, node_id):
        import shutil
        shutil.rmtree(self.tmp_path / node_id, ignore_errors=True)

    def client(self, *node_ids, timeout=3.0) -> DynaClient:
        return DynaClient([self.peers[n] for n in (node_ids or self.ids)], timeout=timeout)

    def partition(self, side_a, side_b):
        for a in side_a:
            self.nodes[a].set_faults(list(side_b), False)
        for b in side_b:
            self.nodes[b].set_faults(list(side_a), False)

    def heal(self):
        for node in self.nodes.values():
            node.set_faults([], False)

    def replicas(self, key):
        return self.nodes[next(iter(self.nodes))].preference_list(key)

    def holders(self, key):
        """Nodes that currently store the key."""
        return sorted(n for n, node in self.nodes.items() if node.storage.get(key) is not None)

    def shutdown(self):
        for node_id in list(self.nodes):
            self.stop(node_id)
        for hole in self.black_holes:
            hole.close()


def wait_for(condition, timeout=5.0, interval=0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(interval)
    return condition()


@pytest.fixture
def cluster(tmp_path):
    c = Cluster(tmp_path)
    yield c
    c.shutdown()


@pytest.fixture
def make_cluster(tmp_path):
    made = []

    def _make(**kwargs):
        c = Cluster(tmp_path / f"c{len(made)}", **kwargs)
        made.append(c)
        return c

    yield _make
    for c in made:
        c.shutdown()
