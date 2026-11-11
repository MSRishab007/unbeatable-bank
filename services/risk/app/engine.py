"""Real-time risk scoring.

For each payment the engine reads a handful of features from DynaStore
(velocity counters, the customer's usual amount, when a device or payee was
first seen, signals raised by other services), evaluates the rules in
config/rules.yaml and returns a score, a decision and the reasons.

DynaStore suits this job: the features are counters and "first seen" markers
that are written on every payment and read with low latency, a slightly stale
value is harmless, and the store keeps working when a node is down.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable

import yaml
from prometheus_client import Counter, Histogram

from ubcommon.dynaclient import DynaClient, DynaUnavailable

log = logging.getLogger("risk")

DECISIONS = Counter("risk_decisions_total", "Risk decisions", ["decision"])
RULE_HITS = Counter("risk_rule_hits_total", "Rules that fired", ["rule", "shadow"])
SCORE = Histogram("risk_score", "Distribution of risk scores", buckets=(0, 10, 20, 30, 45, 60, 75, 85, 100))
DEGRADED = Counter("risk_degraded_total", "Evaluations made without the feature store")

IST = timezone(timedelta(hours=5, minutes=30))
DAY = 86400


@dataclass
class Hit:
    rule: str
    weight: int
    shadow: bool
    detail: str = ""


@dataclass
class Result:
    score: int
    decision: str
    reasons: list[str]
    hits: list[Hit]
    degraded: bool = False


@dataclass
class Payment:
    txn_id: str
    customer_id: str
    from_account_id: str
    to_account_id: str
    amount: int
    channel: str = "TRANSFER"
    device_id: str = ""
    ip: str = ""
    payee_trusted: bool = False
    account_idle_days: int = 0
    event_time_ms: int = 0


class Rules:
    """rules.yaml, reloaded when the file changes."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._mtime = 0.0
        self._checked = 0.0
        self._lock = threading.Lock()
        self.config: dict = {}
        self.reload()

    def reload(self) -> None:
        with open(self.path) as f:
            config = yaml.safe_load(f)
        for name, rule in config["rules"].items():
            rule.setdefault("enabled", True)
            rule.setdefault("shadow", False)
            if not isinstance(rule.get("weight"), int):
                raise ValueError(f"rule {name} needs an integer weight")
        bands = config["bands"]
        if not 0 < bands["challenge"] < bands["hold"] < bands["block"] <= 100:
            raise ValueError("bands must satisfy 0 < challenge < hold < block <= 100")
        self.config, self._mtime = config, os.path.getmtime(self.path)
        log.info("loaded %d rules from %s", len(config["rules"]), self.path)

    def current(self) -> dict:
        now = time.time()
        with self._lock:
            if now - self._checked > 10:
                self._checked = now
                try:
                    if os.path.getmtime(self.path) != self._mtime:
                        self.reload()
                except Exception:
                    log.exception("could not reload rules; keeping the previous version")
        return self.config


@dataclass
class Features:
    velocity_1m: float = 0
    velocity_1h: float = 0
    small_recent: int = 0
    structuring_count: int = 0
    usual_amount: float = 0
    history: int = 0
    device_age_hours: float | None = None      # None = no device id supplied
    payee_age_hours: float = 0
    signals: set[str] = field(default_factory=set)
    denied: bool = False


class Engine:
    def __init__(self, store: DynaClient, rules: Rules, clock: Callable[[], float] = time.time) -> None:
        self.store, self.rules, self.clock = store, rules, clock
        self._pool = ThreadPoolExecutor(max_workers=16, thread_name_prefix="risk-features")

    # ------------------------------------------------------------------ public

    def evaluate(self, p: Payment) -> Result:
        config = self.rules.current()
        rules = config["rules"]
        now = p.event_time_ms / 1000 if p.event_time_ms else self.clock()
        degraded = False
        try:
            f = self._features(p, rules, now)
        except DynaUnavailable:
            f, degraded = Features(), True
            DEGRADED.inc()

        hits: list[Hit] = []

        def fire(name: str, detail: str = "") -> None:
            rule = rules.get(name)
            if rule and rule["enabled"]:
                hits.append(Hit(name, rule["weight"], rule["shadow"], detail))

        def on(name: str) -> dict:
            return rules.get(name) or {"enabled": False}

        if f.denied:
            fire("DENY_LIST", "account, customer or device is on the deny list")
        r = on("VELOCITY_1M")
        if f.velocity_1m > r.get("threshold", 1e9):
            fire("VELOCITY_1M", f"{f.velocity_1m:.0f} payments in the last minute")
        r = on("VELOCITY_1H")
        if f.velocity_1h > r.get("threshold", 1e9):
            fire("VELOCITY_1H", f"{f.velocity_1h:.0f} payments in the last hour")
        r = on("AMOUNT_ANOMALY")
        if (f.history >= r.get("min_history", 1e9) and p.amount >= r.get("min_amount", 0)
                and p.amount > r.get("multiple", 1e9) * f.usual_amount):
            fire("AMOUNT_ANOMALY", f"{p.amount / max(f.usual_amount, 1):.0f}x the customer's usual amount")
        r = on("HIGH_AMOUNT")
        if p.amount >= r.get("min_amount", 1e18):
            fire("HIGH_AMOUNT")
        r = on("NEW_DEVICE")
        new_device = f.device_age_hours is not None and f.device_age_hours < r.get("hours", 24)
        if new_device:
            fire("NEW_DEVICE", "device first seen less than a day ago")
        r = on("NEW_PAYEE")
        new_payee = not p.payee_trusted and f.payee_age_hours < r.get("hours", 24)
        if new_payee and p.amount >= r.get("min_amount", 0):
            fire("NEW_PAYEE", "first payment to this payee")
        r = on("ATO_PATTERN")
        if new_device and new_payee and p.amount >= r.get("min_amount", 1e18):
            fire("ATO_PATTERN", "new device, new payee and a large amount together")
        r = on("MICRO_PROBING")
        if f.small_recent >= r.get("small_count", 1e9) and p.amount >= r.get("min_amount", 1e18):
            fire("MICRO_PROBING", f"{f.small_recent} tiny payments, then a large one")
        r = on("DORMANT_ACCOUNT")
        if p.account_idle_days >= r.get("idle_days", 1e9) and p.amount >= r.get("min_amount", 0):
            fire("DORMANT_ACCOUNT", f"account unused for {p.account_idle_days} days")
        r = on("ODD_HOURS")
        hour = datetime.fromtimestamp(now, IST).hour
        if r.get("from_hour", 0) <= hour < r.get("to_hour", 0):
            fire("ODD_HOURS", f"{hour}:00 India time")
        r = on("STRUCTURING")
        if f.structuring_count >= r.get("count", 1e9):
            fire("STRUCTURING", f"{f.structuring_count} payments just under the threshold today")
        for signal, rule_name in (("new_device_login", "SESSION_NEW_DEVICE_LOGIN"),
                                  ("impossible_travel", "IMPOSSIBLE_TRAVEL"),
                                  ("pattern_sender", "PATTERN_SIGNAL_SENDER"),
                                  ("mule_payee", "MULE_PAYEE")):
            if signal in f.signals:
                fire(rule_name)
        if "fd_break" in f.signals and not p.payee_trusted:
            fire("FD_BREAK_AND_FLEE", "deposit closed early, then money sent to an untrusted payee")
        # Account-security events from Identity. A changed email/phone or a reset password is
        # usually genuine, so it never blocks on its own: it only adds weight when the money is
        # going somewhere the customer hasn't paid before.
        if "contact_changed" in f.signals and not p.payee_trusted:
            fire("CONTACT_CHANGED", "email or phone changed in the last 3 days, paying a new payee")
        if "credential_reset" in f.signals and not p.payee_trusted:
            fire("CREDENTIAL_RESET", "password or PIN reset in the last day, paying a new payee")
        if "pin_locked" in f.signals:
            fire("PIN_LOCKED", "a PIN was locked after wrong guesses in the last day")
        if "token_reuse" in f.signals:
            fire("TOKEN_REUSE", "a copied sign-in token was used in the last day")

        score = min(100, sum(h.weight for h in hits if not h.shadow))
        bands = config["bands"]
        decision = ("BLOCK" if score >= bands["block"] else "HOLD" if score >= bands["hold"]
                    else "CHALLENGE" if score >= bands["challenge"] else "ALLOW")
        if degraded and p.amount >= config.get("degraded_hold_amount", 1e18) and decision in ("ALLOW", "CHALLENGE"):
            decision = "HOLD"
            hits.append(Hit("DEGRADED_MODE", 0, False, "feature store unreachable; large payment held"))

        if decision == "ALLOW" and not degraded:
            self._learn(p, f, now)

        for h in hits:
            RULE_HITS.labels(h.rule, str(h.shadow).lower()).inc()
        DECISIONS.labels(decision).inc()
        SCORE.observe(score)
        reasons = [h.rule for h in hits if not h.shadow]
        log.info("risk %s score=%d reasons=%s txn=%s", decision, score, reasons, p.txn_id)
        return Result(score, decision, reasons, hits, degraded)

    # ------------------------------------------------------------------ features

    def _window(self, base: str, window: int, now: float, delta: int) -> float:
        """Sliding-window count: adds `delta` to the current window and blends in the previous one."""
        index, fraction = int(now // window), (now % window) / window
        current = (self.store.incr(f"{base}:{window}:{index}", delta, ttl=window * 2, w=1) if delta
                   else self.store.counter(f"{base}:{window}:{index}", r=1))
        previous = self.store.counter(f"{base}:{window}:{index - 1}", r=1)
        return current + previous * (1 - fraction)

    def _first_seen_hours(self, key: str, now: float) -> float:
        """Hours since this key was first recorded; records it now if it is new."""
        _, value = self.store.put_if_absent(key, repr(now), ttl=365 * DAY, r=1, w=1)
        try:
            return max(0.0, (now - float(value)) / 3600)
        except ValueError:
            return 0.0

    def _features(self, p: Payment, rules: dict, now: float) -> Features:
        acct, cust = p.from_account_id, p.customer_id
        probe, struct = rules.get("MICRO_PROBING", {}), rules.get("STRUCTURING", {})
        is_small = p.amount <= probe.get("small_amount", 0)
        threshold = struct.get("threshold_amount", 0)
        near_threshold = threshold * (1 - struct.get("band_pct", 0) / 100) <= p.amount < threshold

        jobs = {
            "velocity_1m": lambda: self._window(f"feat:cnt:{acct}", 60, now, 1),
            "velocity_1h": lambda: self._window(f"feat:cnt:{acct}", 3600, now, 1),
            "small_recent": lambda: self._window(f"feat:small:{acct}", 600, now, 1 if is_small else 0),
            "structuring": lambda: self._window(f"feat:struct:{acct}", DAY, now, 1 if near_threshold else 0),
            "baseline": lambda: self.store.get(f"feat:avg:{cust}", r=1).json({}),
            "payee": lambda: self._first_seen_hours(f"seen:payee:{cust}:{p.to_account_id}", now),
            "device": lambda: self._first_seen_hours(f"seen:dev:{cust}:{p.device_id}", now) if p.device_id else None,
        }
        flags = {
            "new_device_login": f"sig:cust:{cust}:new_device_login",
            "impossible_travel": f"sig:cust:{cust}:impossible_travel",
            "fd_break": f"sig:cust:{cust}:fd_break",
            "contact_changed": f"sig:cust:{cust}:contact_changed",
            "credential_reset": f"sig:cust:{cust}:credential_reset",
            "pin_locked": f"sig:cust:{cust}:pin_locked",
            "token_reuse": f"sig:cust:{cust}:token_reuse",
            "pattern_sender": f"sig:acct:{acct}",
            "mule_payee": f"sig:mule:{p.to_account_id}",
            "deny_acct": f"deny:acct:{acct}",
            "deny_cust": f"deny:cust:{cust}",
            "deny_payee": f"deny:acct:{p.to_account_id}",
        }
        if p.device_id:
            flags["deny_dev"] = f"deny:dev:{p.device_id}"
        for name, key in flags.items():
            jobs[f"flag:{name}"] = lambda key=key: self.store.get(key, r=1).found

        futures = {name: self._pool.submit(job) for name, job in jobs.items()}
        got = {name: future.result() for name, future in futures.items()}   # raises DynaUnavailable

        baseline = got["baseline"] or {}
        present = {name[5:] for name, value in got.items() if name.startswith("flag:") and value}
        return Features(
            velocity_1m=got["velocity_1m"], velocity_1h=got["velocity_1h"],
            small_recent=int(got["small_recent"]) - (1 if is_small else 0),   # tiny payments before this one
            structuring_count=int(got["structuring"]),
            usual_amount=float(baseline.get("mean", 0)), history=int(baseline.get("n", 0)),
            device_age_hours=got["device"], payee_age_hours=got["payee"],
            signals={s for s in present if not s.startswith("deny_")},
            denied=any(s.startswith("deny_") for s in present),
        )

    def _learn(self, p: Payment, f: Features, now: float) -> None:
        """Update the customer's usual amount (exponential moving average) after an allowed payment."""
        try:
            n = f.history + 1
            mean = p.amount if f.history == 0 else f.usual_amount + 0.2 * (p.amount - f.usual_amount)
            self.store.put(f"feat:avg:{p.customer_id}", {"n": n, "mean": round(mean, 2)}, ttl=90 * DAY, w=1)
        except DynaUnavailable:
            pass

    # ------------------------------------------------------------------ signals (used by tests and other services)

    def raise_signal(self, key: str, ttl: int = 1800) -> None:
        self.store.put(key, repr(self.clock()), ttl=ttl, w=1)
