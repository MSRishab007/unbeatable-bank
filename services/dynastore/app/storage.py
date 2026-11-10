"""One node's local storage: SQLite on the pod's disk.

Holds the key-value data, the hints kept for unreachable replicas, and the
node's actor id. All access goes through one lock, so a read-modify-write
(`update`) is atomic within the node.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Callable

from . import crdt


class Storage:
    def __init__(self, path: str) -> None:
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._lock = threading.RLock()
        self._db.executescript(
            """
            PRAGMA journal_mode = WAL;
            PRAGMA synchronous = NORMAL;
            CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, env TEXT NOT NULL, digest TEXT NOT NULL,
                                           exp REAL NOT NULL DEFAULT 0);
            CREATE INDEX IF NOT EXISTS kv_exp ON kv (exp) WHERE exp > 0;
            CREATE TABLE IF NOT EXISTS hints (target TEXT NOT NULL, key TEXT NOT NULL, created REAL NOT NULL,
                                              PRIMARY KEY (target, key));
            CREATE TABLE IF NOT EXISTS meta (name TEXT PRIMARY KEY, value TEXT NOT NULL);
            """
        )

    # ------------------------------------------------------------ identity

    def epoch(self) -> str:
        """A random id created with the data directory. If a node loses its disk
        it comes back with a new epoch, and therefore fresh counter slots, so it
        can never re-use (and under-count) a slot other replicas remember."""
        with self._lock:
            row = self._db.execute("SELECT value FROM meta WHERE name = 'epoch'").fetchone()
            if row:
                return row[0]
            value = uuid.uuid4().hex[:8]
            self._db.execute("INSERT INTO meta (name, value) VALUES ('epoch', ?)", (value,))
            return value

    # ------------------------------------------------------------ key-value

    def get(self, key: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT env FROM kv WHERE key = ?", (key,)).fetchone()
        return crdt.live(json.loads(row[0])) if row else None

    def _put(self, key: str, env: dict) -> None:
        self._db.execute(
            "INSERT INTO kv (key, env, digest, exp) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (key) DO UPDATE SET env = excluded.env, digest = excluded.digest, exp = excluded.exp",
            (key, json.dumps(env, separators=(",", ":")), crdt.digest(env), env.get("exp") or 0),
        )

    def merge(self, key: str, incoming: dict | None) -> tuple[dict | None, bool]:
        """Merge a replica's version into ours. Returns (result, changed)."""
        with self._lock:
            current = self.get(key)
            merged = crdt.merge(current, incoming)
            changed = merged is not None and crdt.digest(merged) != crdt.digest(current)
            if changed:
                self._put(key, merged)
            return merged, changed

    def update(self, key: str, fn: Callable[[dict | None], dict]) -> dict:
        """Atomic read-modify-write on this node."""
        with self._lock:
            env = fn(self.get(key))
            self._put(key, env)
            return env

    def digests(self, after: str = "", limit: int = 500) -> list[tuple[str, str]]:
        with self._lock:
            return self._db.execute(
                "SELECT key, digest FROM kv WHERE key > ? AND (exp = 0 OR exp > ?) ORDER BY key LIMIT ?",
                (after, time.time(), limit),
            ).fetchall()

    def count(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM kv WHERE exp = 0 OR exp > ?", (time.time(),)).fetchone()[0]

    def sweep(self) -> int:
        with self._lock:
            cur = self._db.execute("DELETE FROM kv WHERE exp > 0 AND exp <= ?", (time.time(),))
            self._db.execute("DELETE FROM hints WHERE key NOT IN (SELECT key FROM kv)")
            return cur.rowcount

    # ------------------------------------------------------------ hints

    def add_hint(self, target: str, key: str) -> None:
        with self._lock:
            self._db.execute("INSERT OR IGNORE INTO hints (target, key, created) VALUES (?, ?, ?)",
                             (target, key, time.time()))

    def hints(self, target: str, limit: int = 200) -> list[str]:
        with self._lock:
            return [r[0] for r in self._db.execute(
                "SELECT key FROM hints WHERE target = ? ORDER BY created LIMIT ?", (target, limit))]

    def hint_targets(self) -> list[str]:
        with self._lock:
            return [r[0] for r in self._db.execute("SELECT DISTINCT target FROM hints")]

    def remove_hint(self, target: str, key: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM hints WHERE target = ? AND key = ?", (target, key))

    def hint_count(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM hints").fetchone()[0]

    def close(self) -> None:
        with self._lock:
            self._db.close()
