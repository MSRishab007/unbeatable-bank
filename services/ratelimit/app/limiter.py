"""Sliding-window rate limiting on top of DynaStore's CRDT counters.

For a rule "at most L per W seconds" each W-second window has its own counter
key. The rate is estimated from the current window plus the part of the
previous window that still overlaps the last W seconds:

    used = previous * (1 - elapsed_fraction) + current

Every coordinator pod increments the same counters, through any DynaStore node.
The counters are CRDTs written with W=1, so limiting stays available during
node failures and partitions. The price is that, briefly, each side of a
partition counts only what it has seen, so a little over-admission is possible.
That is the right trade for rate limiting; it would not be for balances.
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Protocol

import yaml
from prometheus_client import Counter

from ubcommon.dynaclient import DynaUnavailable

DECISIONS = Counter("ratelimit_decisions_total", "Rate-limit decisions",
                    ["policy_set", "result", "scope"])  # result: allowed, limited, degraded_allow, degraded_deny
STORE_CALLS = Counter("ratelimit_store_calls_total", "Calls to DynaStore", ["op"])


class CounterStore(Protocol):
    def incr(self, key: str, delta: int = 1, *, ttl: int = 0, w: int = 0) -> int: ...

    def counter(self, key: str, r: int = 0) -> int: ...


@dataclass(frozen=True)
class Rule:
    limit: int
    window: int
    lease: int = 0


@dataclass
class Policies:
    fail_open_max_amount: int = 200_000
    sets: dict[str, dict[str, list[Rule]]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str) -> "Policies":
        with open(path) as f:
            raw = yaml.safe_load(f)
        sets = {
            name: {scope: [Rule(**rule) for rule in rules] for scope, rules in scopes.items()}
            for name, scopes in raw.get("policy_sets", {}).items()
        }
        return cls(int(raw.get("fail_open_max_amount", 200_000)), sets)

    def describe(self) -> dict:
        return {"fail_open_max_amount": self.fail_open_max_amount,
                "policy_sets": {name: {scope: [vars(r) for r in rules] for scope, rules in scopes.items()}
                                for name, scopes in self.sets.items()}}


@dataclass
class Decision:
    allowed: bool
    limited_by: str = ""
    limit: int = 0
    remaining: int = 0
    retry_after: int = 0
    degraded: bool = False


class Limiter:
    def __init__(self, store: CounterStore, policies: Policies, clock: Callable[[], float] = time.time) -> None:
        self.store, self.policies, self.clock = store, policies, clock
        self._lock = threading.Lock()
        self._previous: dict[str, int] = {}             # closed windows never change again: cache them
        self._leases: dict[str, tuple[int, int, float]] = {}  # key -> (window, permits left, denied until)

    def check(self, policy_set: str, descriptors: list[tuple[str, str]], cost: int = 1,
              amount: int = 0) -> Decision:
        policy_set = policy_set or "payments"
        cost = cost or 1
        rules = self.policies.sets.get(policy_set, {})
        decision = Decision(allowed=True, remaining=10 ** 9)
        try:
            for scope, ident in descriptors:
                if not ident:
                    continue
                for rule in rules.get(scope, []):
                    used = self._consume(policy_set, scope, ident, rule, cost)
                    remaining = max(0, int(rule.limit - used))
                    if used > rule.limit and decision.allowed:
                        decision = Decision(False, scope, rule.limit, 0, self._retry_after(rule))
                    elif decision.allowed and remaining < decision.remaining:
                        decision.remaining, decision.limit = remaining, rule.limit
        except DynaUnavailable:
            allowed = policy_set != "payments" or amount <= self.policies.fail_open_max_amount
            DECISIONS.labels(policy_set, "degraded_allow" if allowed else "degraded_deny", "").inc()
            return Decision(allowed, "" if allowed else "store_unavailable", degraded=True)
        if decision.remaining == 10 ** 9:
            decision.remaining = 0 if not decision.allowed else -1   # -1: no rule applied
        DECISIONS.labels(policy_set, "allowed" if decision.allowed else "limited", decision.limited_by).inc()
        return decision

    # ------------------------------------------------------------------ internals

    def _retry_after(self, rule: Rule) -> int:
        return max(1, math.ceil(rule.window - self.clock() % rule.window))

    def _key(self, policy_set: str, scope: str, ident: str, rule: Rule, index: int) -> str:
        return f"rl:{policy_set}:{scope}:{ident}:{rule.window}:{index}"

    def _consume(self, policy_set: str, scope: str, ident: str, rule: Rule, cost: int) -> float:
        """Take `cost` permits and return the estimated usage including this request.
        A result above rule.limit means the request is rejected (and nothing was taken)."""
        now = self.clock()
        index, fraction = int(now // rule.window), (now % rule.window) / rule.window
        key = self._key(policy_set, scope, ident, rule, index)
        over_limit = rule.limit + cost

        if rule.lease:
            with self._lock:
                lease_index, left, denied_until = self._leases.get(key, (index, 0, 0.0))
                if lease_index == index and left >= cost:
                    self._leases[key] = (index, left - cost, 0.0)
                    return 0                       # served from permits this pod already holds
                if denied_until > now:
                    return over_limit              # recently found exhausted: don't ask the store again yet
        take = max(rule.lease, cost)

        current = self.store.incr(key, take, ttl=rule.window * 2, w=1)
        STORE_CALLS.labels("incr").inc()
        carried = self._previous_count(policy_set, scope, ident, rule, index) * (1 - fraction)
        used = carried + current
        excess = min(take, max(0, math.ceil(used - rule.limit)))
        if excess:
            # Took more than the limit allows: hand the surplus straight back.
            self.store.incr(key, -excess, ttl=rule.window * 2, w=1)
            STORE_CALLS.labels("incr").inc()
        granted = take - excess
        if rule.lease:
            with self._lock:
                self._leases[key] = (index, max(0, granted - cost), 0.0 if granted >= cost else now + 1.0)
                if len(self._leases) > 50_000:
                    self._leases = {k: v for k, v in self._leases.items() if v[0] >= index}
        if granted < cost:
            return over_limit
        return used - excess - (granted - cost)

    def _previous_count(self, policy_set: str, scope: str, ident: str, rule: Rule, index: int) -> int:
        key = self._key(policy_set, scope, ident, rule, index - 1)
        with self._lock:
            if key in self._previous:
                return self._previous[key]
        value = self.store.counter(key, r=1)
        STORE_CALLS.labels("get").inc()
        with self._lock:
            if len(self._previous) > 50_000:
                self._previous.clear()
            self._previous[key] = value
        return value
