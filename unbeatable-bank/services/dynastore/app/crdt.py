"""Stored values ("envelopes") and how replicas reconcile them.

Two kinds of value, both JSON:

  counter   {"t": "c", "p": {actor: n}, "n": {actor: n}, "exp": ts}
            A PN-counter CRDT. Each node only ever increases its own slot, and
            merging takes the per-slot maximum, so concurrent increments on
            different nodes are never lost and never double-counted.

  register  {"t": "r", "s": [{"v": b64, "c": {actor: n}, "ts": t}], "exp": ts}
            A value with a vector clock. A write that has seen an older version
            replaces it; two writes that have not seen each other are both kept
            as siblings, and readers get a deterministic winner.

`exp` is an absolute expiry time (0 = never). Expired values behave as absent.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time

MAX_SIBLINGS = 10


def expired(env: dict | None, at: float | None = None) -> bool:
    return env is not None and bool(env.get("exp")) and env["exp"] <= (at if at is not None else time.time())


def live(env: dict | None, at: float | None = None) -> dict | None:
    return None if env is None or expired(env, at) else env


def _merge_exp(a: float, b: float) -> float:
    return 0 if (not a or not b) else max(a, b)


# ------------------------------------------------------------------ counters


def new_counter(exp: float = 0) -> dict:
    return {"t": "c", "p": {}, "n": {}, "exp": exp}


def counter_add(env: dict | None, actor: str, delta: int, exp: float = 0) -> dict:
    env = json.loads(json.dumps(env)) if env and env.get("t") == "c" else new_counter(exp)
    slot = "p" if delta >= 0 else "n"
    env[slot][actor] = env[slot].get(actor, 0) + abs(delta)
    if exp:
        env["exp"] = max(env.get("exp") or 0, exp)
    return env


def counter_value(env: dict | None) -> int:
    if not env or env.get("t") != "c":
        return 0
    return sum(env["p"].values()) - sum(env["n"].values())


# ------------------------------------------------------------------ registers


def descends(a: dict, b: dict) -> bool:
    """True if clock a has seen everything clock b has."""
    return all(a.get(actor, 0) >= n for actor, n in b.items())


def merged_clock(env: dict | None) -> dict:
    clock: dict = {}
    for s in (env or {}).get("s", []):
        for actor, n in s["c"].items():
            clock[actor] = max(clock.get(actor, 0), n)
    return clock


def register_write(env: dict | None, actor: str, value: bytes, context: dict | None, exp: float = 0,
                   first_writer_wins: bool = False, ts: float | None = None) -> dict:
    """Add a new version. `context` is the clock the writer last read; with no
    context the write supersedes everything this node currently holds."""
    current = env if env and env.get("t") == "r" else None
    base = dict(context) if context else merged_clock(current)
    seen = merged_clock(current)
    base[actor] = max(base.get(actor, 0), seen.get(actor, 0)) + 1
    sibling = {"v": base64.b64encode(value).decode(), "c": base, "ts": ts if ts is not None else time.time()}
    survivors = [s for s in (current or {}).get("s", []) if not descends(base, s["c"])]
    out = {"t": "r", "s": _cap(survivors + [sibling]), "exp": exp}
    if first_writer_wins or (current or {}).get("fww"):
        out["fww"] = True
    return out


def _cap(siblings: list[dict]) -> list[dict]:
    return sorted(siblings, key=lambda s: s["ts"])[-MAX_SIBLINGS:]


def winner(env: dict | None) -> bytes | None:
    """Deterministic choice among siblings: latest write, or earliest for
    first-writer-wins keys (PutIfAbsent). Ties break on the value itself."""
    if not env or env.get("t") != "r" or not env["s"]:
        return None
    pick = min if env.get("fww") else max
    return base64.b64decode(pick(env["s"], key=lambda s: (s["ts"], s["v"]))["v"])


def sibling_values(env: dict | None) -> list[bytes]:
    if not env or env.get("t") != "r":
        return []
    return [base64.b64decode(s["v"]) for s in sorted(env["s"], key=lambda s: (s["ts"], s["v"]))]


# ------------------------------------------------------------------ merge


def merge(a: dict | None, b: dict | None, at: float | None = None) -> dict | None:
    """Reconcile two replicas' versions of the same key. Commutative, associative, idempotent."""
    a, b = live(a, at), live(b, at)
    if a is None:
        return b
    if b is None:
        return a
    if a.get("t") != b.get("t"):
        # A key changed type (shouldn't happen). Keep the one that expires later, deterministically.
        return max(a, b, key=lambda e: (e.get("exp") or float("inf"), e["t"]))
    if a["t"] == "c":
        return {
            "t": "c",
            "p": {k: max(a["p"].get(k, 0), b["p"].get(k, 0)) for k in a["p"].keys() | b["p"].keys()},
            "n": {k: max(a["n"].get(k, 0), b["n"].get(k, 0)) for k in a["n"].keys() | b["n"].keys()},
            "exp": _merge_exp(a.get("exp") or 0, b.get("exp") or 0),
        }
    unique: dict[str, dict] = {}
    for s in a["s"] + b["s"]:
        unique[json.dumps(s["c"], sort_keys=True)] = s
    siblings = list(unique.values())
    kept = [s for s in siblings
            if not any(o is not s and o["c"] != s["c"] and descends(o["c"], s["c"]) for o in siblings)]
    out = {"t": "r", "s": _cap(kept), "exp": _merge_exp(a.get("exp") or 0, b.get("exp") or 0)}
    if a.get("fww") or b.get("fww"):
        out["fww"] = True
    return out


def canonical(env: dict) -> str:
    if env.get("t") == "r":
        env = {**env, "s": sorted(env["s"], key=lambda s: json.dumps(s["c"], sort_keys=True))}
    return json.dumps(env, sort_keys=True, separators=(",", ":"))


def digest(env: dict | None) -> str:
    return hashlib.sha1(canonical(env).encode()).hexdigest()[:16] if env else ""
