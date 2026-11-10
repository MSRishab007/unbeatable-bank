"""Document store: Firestore in the cloud, in-memory for tests and local runs.

Used for high-volume, semi-structured data: sessions, login events, fraud
alerts, the notification inbox, analytics results.

Queries use equality filters only and sort in the service, so no composite
Firestore indexes need to be managed.

Conventions: timestamps are stored as epoch seconds (floats), so documents look
the same in every backend. The one exception is `expires_at`: in Firestore it
is written as a native timestamp so a TTL policy can delete expired documents,
and converted back to epoch seconds on read.
"""

from __future__ import annotations

import copy
import os
import threading
import uuid
from typing import Any, Protocol


class DocStore(Protocol):
    def set(self, collection: str, doc_id: str, data: dict, merge: bool = False) -> None: ...

    def get(self, collection: str, doc_id: str) -> dict | None: ...

    def add(self, collection: str, data: dict) -> str: ...

    def delete(self, collection: str, doc_id: str) -> None: ...

    def query(self, collection: str, filters: dict[str, Any] | None = None, *,
              order_by: str | None = None, descending: bool = False, limit: int = 50) -> list[dict]: ...


def _sorted(docs: list[dict], order_by: str | None, descending: bool, limit: int) -> list[dict]:
    if order_by:
        docs.sort(key=lambda d: (d.get(order_by) is None, d.get(order_by)), reverse=descending)
    return docs[:limit]


class MemoryDocStore:
    def __init__(self) -> None:
        self._data: dict[str, dict[str, dict]] = {}
        self._lock = threading.Lock()

    def set(self, collection, doc_id, data, merge=False):
        with self._lock:
            docs = self._data.setdefault(collection, {})
            docs[doc_id] = {**docs.get(doc_id, {}), **data} if merge else dict(data)

    def get(self, collection, doc_id):
        with self._lock:
            doc = self._data.get(collection, {}).get(doc_id)
            return {"id": doc_id, **copy.deepcopy(doc)} if doc is not None and not _expired(doc) else None

    def add(self, collection, data):
        doc_id = uuid.uuid4().hex
        self.set(collection, doc_id, data)
        return doc_id

    def delete(self, collection, doc_id):
        with self._lock:
            self._data.get(collection, {}).pop(doc_id, None)

    def query(self, collection, filters=None, *, order_by=None, descending=False, limit=50):
        with self._lock:
            docs = [
                {"id": doc_id, **copy.deepcopy(doc)}
                for doc_id, doc in self._data.get(collection, {}).items()
                if all(doc.get(k) == v for k, v in (filters or {}).items()) and not _expired(doc)
            ]
        return _sorted(docs, order_by, descending, limit)


def _to_firestore(data: dict) -> dict:
    if isinstance(data.get("expires_at"), (int, float)):
        from datetime import datetime, timezone
        return {**data, "expires_at": datetime.fromtimestamp(data["expires_at"], tz=timezone.utc)}
    return data


def _from_firestore(doc_id: str, data: dict) -> dict:
    expires = data.get("expires_at")
    if expires is not None and hasattr(expires, "timestamp"):
        data = {**data, "expires_at": expires.timestamp()}
    return {"id": doc_id, **data}


class PostgresDocStore:
    """Documents as JSONB rows. Used for local development, where several
    services need to see the same documents without a Firestore emulator."""

    def __init__(self, url: str | None = None) -> None:
        from psycopg_pool import ConnectionPool

        self._pool = ConnectionPool(url or os.environ["DOCSTORE_URL"], min_size=1, max_size=4,
                                    kwargs={"autocommit": True}, open=True)
        with self._pool.connection() as conn:
            conn.execute("SELECT pg_advisory_lock(727001)")
            try:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS documents (collection text NOT NULL, id text NOT NULL, "
                    "data jsonb NOT NULL, PRIMARY KEY (collection, id))")
            finally:
                conn.execute("SELECT pg_advisory_unlock(727001)")

    def set(self, collection, doc_id, data, merge=False):
        from psycopg.types.json import Jsonb

        update = "documents.data || excluded.data" if merge else "excluded.data"
        with self._pool.connection() as conn:
            conn.execute(
                f"INSERT INTO documents (collection, id, data) VALUES (%s, %s, %s) "
                f"ON CONFLICT (collection, id) DO UPDATE SET data = {update}",
                (collection, doc_id, Jsonb(data)))

    def get(self, collection, doc_id):
        with self._pool.connection() as conn:
            row = conn.execute("SELECT data FROM documents WHERE collection = %s AND id = %s",
                               (collection, doc_id)).fetchone()
        if row is None or _expired(row[0]):
            return None
        return {"id": doc_id, **row[0]}

    def add(self, collection, data):
        doc_id = uuid.uuid4().hex
        self.set(collection, doc_id, data)
        return doc_id

    def delete(self, collection, doc_id):
        with self._pool.connection() as conn:
            conn.execute("DELETE FROM documents WHERE collection = %s AND id = %s", (collection, doc_id))

    def close(self) -> None:
        self._pool.close()

    def query(self, collection, filters=None, *, order_by=None, descending=False, limit=50):
        from psycopg.types.json import Jsonb

        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT id, data FROM documents WHERE collection = %s AND data @> %s LIMIT 2000",
                (collection, Jsonb(filters or {}))).fetchall()
        docs = [{"id": doc_id, **data} for doc_id, data in rows if not _expired(data)]
        return _sorted(docs, order_by, descending, limit)


def _expired(data: dict) -> bool:
    import time
    expires = data.get("expires_at")
    return isinstance(expires, (int, float)) and expires <= time.time()


class FirestoreDocStore:
    FETCH_CAP = 500  # rows fetched per filtered query before sorting in memory

    def __init__(self, project: str | None = None, database: str | None = None) -> None:
        from google.cloud import firestore

        self._db = firestore.Client(project=project or os.getenv("GCP_PROJECT"),
                                    database=database or os.getenv("FIRESTORE_DATABASE", "(default)"))

    def set(self, collection, doc_id, data, merge=False):
        self._db.collection(collection).document(doc_id).set(_to_firestore(data), merge=merge)

    def get(self, collection, doc_id):
        snap = self._db.collection(collection).document(doc_id).get()
        return _from_firestore(snap.id, snap.to_dict()) if snap.exists else None

    def add(self, collection, data):
        _, ref = self._db.collection(collection).add(_to_firestore(data))
        return ref.id

    def delete(self, collection, doc_id):
        self._db.collection(collection).document(doc_id).delete()

    def query(self, collection, filters=None, *, order_by=None, descending=False, limit=50):
        from google.cloud import firestore
        from google.cloud.firestore_v1.base_query import FieldFilter

        query = self._db.collection(collection)
        if filters:
            for field, value in filters.items():
                query = query.where(filter=FieldFilter(field, "==", value))
            docs = [_from_firestore(s.id, s.to_dict()) for s in query.limit(self.FETCH_CAP).stream()]
            return _sorted(docs, order_by, descending, limit)
        if order_by:
            direction = firestore.Query.DESCENDING if descending else firestore.Query.ASCENDING
            query = query.order_by(order_by, direction=direction)
        return [_from_firestore(s.id, s.to_dict()) for s in query.limit(limit).stream()]


def get_docstore() -> DocStore:
    """DOCSTORE=firestore in GCP, postgres for local stacks, memory (default) for tests."""
    kind = os.getenv("DOCSTORE", "memory")
    if kind == "firestore":
        return FirestoreDocStore()
    if kind == "postgres":
        return PostgresDocStore()
    return MemoryDocStore()
