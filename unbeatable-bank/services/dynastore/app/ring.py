"""Consistent hashing ring with virtual nodes.

Each physical node owns many points ("tokens") on a 64-bit ring. A key is
stored on the first N distinct nodes found walking clockwise from the key's
position: its preference list. Virtual nodes spread keys evenly, and adding or
removing a node only moves the keys next to its tokens.
"""

from __future__ import annotations

import bisect
import hashlib


def _hash(value: str) -> int:
    return int.from_bytes(hashlib.md5(value.encode()).digest()[:8], "big")


class Ring:
    def __init__(self, nodes: list[str], vnodes: int = 64) -> None:
        self.nodes = sorted(nodes)
        self._tokens: list[tuple[int, str]] = sorted(
            (_hash(f"{node}#{i}"), node) for node in self.nodes for i in range(vnodes)
        )
        self._points = [t for t, _ in self._tokens]

    def walk(self, key: str) -> list[str]:
        """Every node, in the order they are met clockwise from the key."""
        start = bisect.bisect(self._points, _hash(key)) % len(self._tokens)
        seen: list[str] = []
        for i in range(len(self._tokens)):
            node = self._tokens[(start + i) % len(self._tokens)][1]
            if node not in seen:
                seen.append(node)
                if len(seen) == len(self.nodes):
                    break
        return seen

    def preference_list(self, key: str, n: int) -> list[str]:
        return self.walk(key)[:n]

    def ownership(self) -> dict[str, float]:
        """Share of the ring each node is first owner of (for the visualiser)."""
        total = 2 ** 64
        share = dict.fromkeys(self.nodes, 0)
        for i, (point, node) in enumerate(self._tokens):
            previous = self._tokens[i - 1][0]
            share[node] += (point - previous) % total
        return {node: round(span / total, 4) for node, span in share.items()}
