"""KYC documents: customers upload proof of identity, staff review it.

    customer uploads a file  ->  PENDING  ->  staff APPROVE  ->  when every required
                                          \\                     type is approved, KYC is
                                           \\                    verified and accounts activate
                                            ->  staff REJECT (with a reason): the file is
                                                deleted, the customer is told and can upload again

Rules:
  - JPEG, PNG or PDF only, judged from the file's own bytes (not its name or the
    Content-Type the browser sent), at most MAX_BYTES.
  - Files live in a private object-store bucket. Nobody gets a link to the bucket:
    the bytes are served through this service, which checks who is asking and
    records every staff view.
  - A rejected or replaced file is deleted at once. The bank does not keep identity
    documents it did not accept; the record of what happened (with the file's
    SHA-256) stays.
  - The same file turning up under two customers is a fraud signal, shown to the reviewer.
"""

from __future__ import annotations

import hashlib
import logging
import os
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from ubcommon.auth import Actor

from .service import Conflict, Deps, IdentityError, NotFound

log = logging.getLogger("identity.kyc")

MAX_BYTES = 5 * 1024 * 1024
DOC_TYPES = {"PAN": "PAN card", "ADDRESS_PROOF": "address proof", "PHOTO": "photograph"}
REVIEWER_ROLES = {"KYC", "ADMIN"}
_SIGNATURES = (
    (b"\xff\xd8\xff", "image/jpeg", "jpg"),
    (b"\x89PNG\r\n\x1a\n", "image/png", "png"),
    (b"%PDF-", "application/pdf", "pdf"),
)


def required_types() -> tuple[str, ...]:
    """Document types that must all be approved before KYC is verified (KYC_REQUIRED_DOCS)."""
    raw = os.getenv("KYC_REQUIRED_DOCS", "PAN,ADDRESS_PROOF")
    return tuple(t for t in (x.strip().upper() for x in raw.split(",")) if t in DOC_TYPES)


class InvalidDocument(IdentityError):
    code = "INVALID_DOCUMENT"
    http_status = 422


class TooLarge(IdentityError):
    code = "DOCUMENT_TOO_LARGE"
    http_status = 413


class Forbidden(IdentityError):
    code = "FORBIDDEN"
    http_status = 403


class Gone(IdentityError):
    code = "DOCUMENT_REMOVED"
    http_status = 410


def sniff(data: bytes) -> tuple[str, str]:
    """(content type, extension) from the file's first bytes."""
    for signature, content_type, extension in _SIGNATURES:
        if data.startswith(signature):
            return content_type, extension
    raise InvalidDocument("Upload a JPEG or PNG picture, or a PDF.")


def _cur(conn: psycopg.Connection) -> psycopg.Cursor:
    return conn.cursor(row_factory=dict_row)


def _log_event(cur, customer_id, document_id, event_type: str, actor: str, **detail) -> None:
    cur.execute(
        "INSERT INTO identity.kyc_events (customer_id, document_id, event_type, actor, detail) "
        "VALUES (%s, %s, %s, %s, %s)", (customer_id, document_id, event_type, actor, Jsonb(detail)))


def _public(row: dict) -> dict:
    row = dict(row)
    row.pop("object_path", None)
    row["has_file"] = row["status"] in ("PENDING", "APPROVED")
    return row


def _remove_file(deps: Deps, path: str) -> None:
    try:
        deps.objstore.delete(path)
    except Exception:  # noqa: BLE001 - the decision stands; an orphaned file is logged for clean-up
        log.exception("could not delete KYC file %s", path)


# ------------------------------------------------------------------ customer


def upload(conn: psycopg.Connection, deps: Deps, customer_id: UUID, doc_type: str, data: bytes) -> dict:
    doc_type = (doc_type or "").upper()
    if doc_type not in DOC_TYPES:
        raise InvalidDocument(f"Unknown document type. Use one of: {', '.join(DOC_TYPES)}.")
    if not data:
        raise InvalidDocument("The file is empty.")
    if len(data) > MAX_BYTES:
        raise TooLarge(f"The file is larger than {MAX_BYTES // (1024 * 1024)} MB.")
    content_type, extension = sniff(data)

    document_id = uuid4()
    path = f"kyc/{customer_id}/{document_id}.{extension}"
    digest = hashlib.sha256(data).hexdigest()
    actor = f"customer:{customer_id}"
    deps.objstore.put(path, data, content_type)          # file first: a row never points at nothing
    replaced: list[str] = []
    try:
        with conn.transaction(), _cur(conn) as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"kyc:{customer_id}:{doc_type}",))
            cur.execute(
                "SELECT document_id, status, object_path FROM identity.kyc_documents "
                "WHERE customer_id = %s AND doc_type = %s AND status IN ('PENDING', 'APPROVED')",
                (customer_id, doc_type))
            for old in cur.fetchall():
                if old["status"] == "APPROVED":
                    raise Conflict(f"Your {DOC_TYPES[doc_type]} is already verified.")
                cur.execute("UPDATE identity.kyc_documents SET status = 'REPLACED', decided_at = now() "
                            "WHERE document_id = %s", (old["document_id"],))
                _log_event(cur, customer_id, old["document_id"], "REPLACED", actor, by=str(document_id))
                replaced.append(old["object_path"])
            cur.execute(
                "INSERT INTO identity.kyc_documents (document_id, customer_id, doc_type, content_type, "
                "size_bytes, sha256, object_path) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *",
                (document_id, customer_id, doc_type, content_type, len(data), digest, path))
            row = cur.fetchone()
            _log_event(cur, customer_id, document_id, "UPLOADED", actor, doc_type=doc_type,
                       size_bytes=len(data), sha256=digest)
    except Exception:
        _remove_file(deps, path)
        raise
    for old_path in replaced:
        _remove_file(deps, old_path)
    return _public(row)


def my_documents(conn: psycopg.Connection, customer_id: UUID) -> dict:
    """The newest document of each type, and what is still needed."""
    with _cur(conn) as cur:
        cur.execute(
            "SELECT DISTINCT ON (doc_type) * FROM identity.kyc_documents WHERE customer_id = %s "
            "AND status <> 'REPLACED' ORDER BY doc_type, uploaded_at DESC", (customer_id,))
        latest = {r["doc_type"]: _public(r) for r in cur.fetchall()}
    required = required_types()
    return {
        "max_bytes": MAX_BYTES,
        "types": [{"doc_type": t, "label": label, "required": t in required, "document": latest.get(t)}
                  for t, label in DOC_TYPES.items()],
        "missing": [t for t in required if latest.get(t, {}).get("status") not in ("PENDING", "APPROVED")],
        "all_approved": all(latest.get(t, {}).get("status") == "APPROVED" for t in required),
    }


def content(conn: psycopg.Connection, deps: Deps, document_id: UUID, actor: Actor) -> tuple[bytes, str, str]:
    """(bytes, content type, file name). Customers see their own; KYC staff see any, and it is recorded."""
    with conn.transaction(), _cur(conn) as cur:
        cur.execute("SELECT * FROM identity.kyc_documents WHERE document_id = %s", (document_id,))
        row = cur.fetchone()
        if row is None or (actor.kind == "customer" and str(row["customer_id"]) != actor.id):
            raise NotFound("document not found")          # someone else's looks like it doesn't exist
        if actor.kind == "staff" and actor.role not in REVIEWER_ROLES:
            raise Forbidden("only KYC officers can open identity documents")
        if actor.kind not in ("customer", "staff"):
            raise Forbidden("not allowed")
        if row["status"] not in ("PENDING", "APPROVED"):
            raise Gone("This file was removed when the document was rejected or replaced.")
        if actor.kind == "staff":
            _log_event(cur, row["customer_id"], document_id, "VIEWED", actor.header())
    data = deps.objstore.get(row["object_path"])
    if data is None:
        raise Gone("The file is missing from storage.")
    extension = row["object_path"].rsplit(".", 1)[-1]
    return data, row["content_type"], f"{row['doc_type'].lower()}-{str(document_id)[:8]}.{extension}"


# ------------------------------------------------------------------ staff


_WITH_DUPLICATES = """
    SELECT d.*, (SELECT count(DISTINCT o.customer_id) FROM identity.kyc_documents o
                 WHERE o.sha256 = d.sha256 AND o.customer_id <> d.customer_id) AS same_file_other_customers
    FROM identity.kyc_documents d
"""


def customer_documents(conn: psycopg.Connection, customer_id: UUID) -> list[dict]:
    with _cur(conn) as cur:
        cur.execute(_WITH_DUPLICATES + "WHERE d.customer_id = %s AND d.status <> 'REPLACED' "
                    "ORDER BY d.uploaded_at DESC", (customer_id,))
        return [_public(r) for r in cur.fetchall()]


def queue(conn: psycopg.Connection, limit: int = 50) -> list[dict]:
    """Documents waiting for review, oldest first."""
    with _cur(conn) as cur:
        cur.execute(_WITH_DUPLICATES + "WHERE d.status = 'PENDING' ORDER BY d.uploaded_at LIMIT %s",
                    (max(1, min(limit, 200)),))
        return [_public(r) for r in cur.fetchall()]


def events(conn: psycopg.Connection, customer_id: UUID) -> list[dict]:
    with _cur(conn) as cur:
        cur.execute("SELECT * FROM identity.kyc_events WHERE customer_id = %s ORDER BY event_id", (customer_id,))
        return cur.fetchall()


def _reviewer(actor: Actor) -> str:
    if actor.kind != "staff" or actor.role not in REVIEWER_ROLES:
        raise Forbidden("only KYC officers can review identity documents")
    return actor.header()


def approve(conn: psycopg.Connection, deps: Deps, document_id: UUID, actor: Actor) -> dict:
    """Accept one document. When every required type is accepted, the customer's KYC is verified.
    Safe to repeat: approving again re-runs the "is everything approved now?" step."""
    who = _reviewer(actor)
    with conn.transaction(), _cur(conn) as cur:
        cur.execute("SELECT * FROM identity.kyc_documents WHERE document_id = %s FOR UPDATE", (document_id,))
        row = cur.fetchone()
        if row is None:
            raise NotFound("document not found")
        if row["status"] == "PENDING":
            cur.execute("UPDATE identity.kyc_documents SET status = 'APPROVED', decided_at = now(), "
                        "decided_by = %s WHERE document_id = %s RETURNING *", (who, document_id))
            row = cur.fetchone()
            _log_event(cur, row["customer_id"], document_id, "APPROVED", who, doc_type=row["doc_type"])
        elif row["status"] != "APPROVED":
            raise Conflict(f"This document was {row['status'].lower()}; it can't be approved.")
        cur.execute("SELECT doc_type FROM identity.kyc_documents WHERE customer_id = %s AND status = 'APPROVED'",
                    (row["customer_id"],))
        approved = {r["doc_type"] for r in cur.fetchall()}
        cur.execute("SELECT 1 FROM identity.kyc_events WHERE customer_id = %s AND event_type = 'KYC_VERIFIED' "
                    "LIMIT 1", (row["customer_id"],))
        already = cur.fetchone() is not None
    complete = all(t in approved for t in required_types())
    if complete and not already:
        deps.accounts.verify_kyc(row["customer_id"], actor=who)      # activates the customer's accounts
        with conn.transaction(), _cur(conn) as cur:
            _log_event(cur, row["customer_id"], None, "KYC_VERIFIED", who)
        deps.notifier.send(row["customer_id"], "KYC", "Your KYC is verified",
                           "Your documents were accepted. Your accounts are now active.",
                           message_id=f"kyc-verified-{row['customer_id']}")
    return {**_public(row), "kyc_verified": complete}


def reject(conn: psycopg.Connection, deps: Deps, document_id: UUID, actor: Actor, reason: str) -> dict:
    who = _reviewer(actor)
    reason = (reason or "").strip()
    if len(reason) < 5:
        raise InvalidDocument("Give the customer a reason they can act on.")
    with conn.transaction(), _cur(conn) as cur:
        cur.execute("SELECT * FROM identity.kyc_documents WHERE document_id = %s FOR UPDATE", (document_id,))
        row = cur.fetchone()
        if row is None:
            raise NotFound("document not found")
        if row["status"] != "PENDING":
            raise Conflict(f"This document is already {row['status'].lower()}.")
        cur.execute("UPDATE identity.kyc_documents SET status = 'REJECTED', reject_reason = %s, "
                    "decided_at = now(), decided_by = %s WHERE document_id = %s RETURNING *",
                    (reason, who, document_id))
        updated = cur.fetchone()
        _log_event(cur, row["customer_id"], document_id, "REJECTED", who, doc_type=row["doc_type"], reason=reason)
    _remove_file(deps, row["object_path"])
    deps.notifier.send(row["customer_id"], "KYC", f"Please upload your {DOC_TYPES[row['doc_type']]} again",
                       f"We couldn't accept it: {reason}", data={"doc_type": row["doc_type"]},
                       message_id=f"kyc-rejected-{document_id}")
    return _public(updated)
