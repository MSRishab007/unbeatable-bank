"""KYC documents: upload rules, who can see a file, review decisions and the audit trail."""

import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient

from ubcommon.auth import Actor

from app import kyc, service

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200
JPEG = b"\xff\xd8\xff\xe0" + b"\x01" * 300
PDF = b"%PDF-1.7\n" + b"%" * 400
OFFICER = Actor("staff", "kyc1", "KYC")
OTHER_OFFICER = Actor("staff", "admin", "ADMIN")
TELLER = Actor("staff", "teller1", "TELLER")


def customer(user) -> Actor:
    return Actor("customer", str(user["customer_id"]))


def inbox(deps, user):
    return deps.docstore.query("notifications", {"customer_id": str(user["customer_id"])})


def event_types(conn, user):
    return [e["event_type"] for e in kyc.events(conn, user["customer_id"])]


def stored_files(deps):
    return sorted(p.name for p in deps.objstore._root.rglob("*") if p.is_file())


# ------------------------------------------------------------------ uploading


def test_upload_stores_the_file_privately_and_records_it(conn, deps, user):
    doc = kyc.upload(conn, deps, user["customer_id"], "pan", PNG)
    assert (doc["status"], doc["doc_type"], doc["content_type"], doc["size_bytes"]) == ("PENDING", "PAN", "image/png", len(PNG))
    assert "object_path" not in doc and doc["has_file"] is True          # storage location is not exposed
    data, content_type, name = kyc.content(conn, deps, doc["document_id"], customer(user))
    assert data == PNG and content_type == "image/png" and name.endswith(".png")
    assert event_types(conn, user) == ["UPLOADED"]                        # the owner looking is not a staff view


@pytest.mark.parametrize("data, content_type", [(PNG, "image/png"), (JPEG, "image/jpeg"), (PDF, "application/pdf")])
def test_type_comes_from_the_file_itself(conn, deps, user, data, content_type):
    assert kyc.upload(conn, deps, user["customer_id"], "PHOTO", data)["content_type"] == content_type


@pytest.mark.parametrize("data", [
    b"<html><script>alert(1)</script></html>",                # a web page
    b"MZ\x90\x00" + b"\x00" * 100,                           # a Windows program
    b"<svg xmlns='http://www.w3.org/2000/svg' onload='x()'/>",
    b"GIF89a" + b"\x00" * 50,
])
def test_anything_but_jpeg_png_pdf_is_refused_and_not_stored(conn, deps, user, data):
    with pytest.raises(kyc.InvalidDocument):
        kyc.upload(conn, deps, user["customer_id"], "PAN", data)
    assert stored_files(deps) == [] and kyc.my_documents(conn, user["customer_id"])["types"][0]["document"] is None


def test_size_type_and_empty_checks(conn, deps, user):
    with pytest.raises(kyc.TooLarge):
        kyc.upload(conn, deps, user["customer_id"], "PAN", PNG + b"\x00" * kyc.MAX_BYTES)
    with pytest.raises(kyc.InvalidDocument):
        kyc.upload(conn, deps, user["customer_id"], "PAN", b"")
    with pytest.raises(kyc.InvalidDocument):
        kyc.upload(conn, deps, user["customer_id"], "PASSPORT", PNG)
    assert stored_files(deps) == []


def test_a_newer_upload_replaces_the_waiting_one_and_deletes_its_file(conn, deps, user):
    first = kyc.upload(conn, deps, user["customer_id"], "PAN", PNG)
    second = kyc.upload(conn, deps, user["customer_id"], "PAN", JPEG)
    assert len(stored_files(deps)) == 1 and stored_files(deps)[0].endswith(".jpg")
    with pytest.raises(kyc.Gone):
        kyc.content(conn, deps, first["document_id"], customer(user))
    mine = kyc.my_documents(conn, user["customer_id"])
    assert mine["types"][0]["document"]["document_id"] == second["document_id"]
    assert [d["document_id"] for d in kyc.queue(conn) if d["customer_id"] == user["customer_id"]] == [second["document_id"]]
    assert event_types(conn, user) == ["UPLOADED", "REPLACED", "UPLOADED"]


def test_what_is_still_missing(conn, deps, user):
    assert kyc.my_documents(conn, user["customer_id"])["missing"] == ["PAN", "ADDRESS_PROOF"]
    kyc.upload(conn, deps, user["customer_id"], "PAN", PNG)
    status = kyc.my_documents(conn, user["customer_id"])
    assert status["missing"] == ["ADDRESS_PROOF"] and status["all_approved"] is False
    assert [(t["doc_type"], t["required"]) for t in status["types"]] == [
        ("PAN", True), ("ADDRESS_PROOF", True), ("PHOTO", False)]


# ------------------------------------------------------------------ who can see a file


def test_another_customers_document_does_not_exist_for_you(conn, deps, user):
    doc = kyc.upload(conn, deps, user["customer_id"], "PAN", PNG)
    stranger = Actor("customer", str(uuid.uuid4()))
    with pytest.raises(service.NotFound):
        kyc.content(conn, deps, doc["document_id"], stranger)
    with pytest.raises(service.NotFound):
        kyc.content(conn, deps, uuid.uuid4(), customer(user))


def test_only_kyc_staff_open_documents_and_every_view_is_recorded(conn, deps, user):
    doc = kyc.upload(conn, deps, user["customer_id"], "PAN", PNG)
    with pytest.raises(kyc.Forbidden):
        kyc.content(conn, deps, doc["document_id"], TELLER)
    with pytest.raises(kyc.Forbidden):
        kyc.content(conn, deps, doc["document_id"], Actor("system", "intake"))
    assert kyc.content(conn, deps, doc["document_id"], OFFICER)[0] == PNG
    assert kyc.content(conn, deps, doc["document_id"], OTHER_OFFICER)[0] == PNG
    views = [(e["event_type"], e["actor"]) for e in kyc.events(conn, user["customer_id"])]
    assert views == [("UPLOADED", f"customer:{user['customer_id']}"), ("VIEWED", "staff:kyc1:KYC"),
                     ("VIEWED", "staff:admin:ADMIN")]


def test_the_audit_trail_cannot_be_edited(conn, deps, user):
    kyc.upload(conn, deps, user["customer_id"], "PAN", PNG)
    for statement in ("UPDATE identity.kyc_events SET actor = 'nobody'", "DELETE FROM identity.kyc_events"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(statement)


# ------------------------------------------------------------------ review


def test_kyc_is_verified_only_when_every_required_document_is_approved(conn, deps, user):
    pan = kyc.upload(conn, deps, user["customer_id"], "PAN", PNG)
    address = kyc.upload(conn, deps, user["customer_id"], "ADDRESS_PROOF", PDF)
    first = kyc.approve(conn, deps, pan["document_id"], OFFICER)
    assert first["status"] == "APPROVED" and first["kyc_verified"] is False and deps.accounts.verified == []
    second = kyc.approve(conn, deps, address["document_id"], OFFICER)
    assert second["kyc_verified"] is True
    assert deps.accounts.verified == [str(user["customer_id"])] and deps.accounts.verified_by == "staff:kyc1:KYC"
    assert [n["title"] for n in inbox(deps, user)] == ["Your KYC is verified"]
    assert event_types(conn, user) == ["UPLOADED", "UPLOADED", "APPROVED", "APPROVED", "KYC_VERIFIED"]
    assert kyc.my_documents(conn, user["customer_id"])["all_approved"] is True

    kyc.approve(conn, deps, address["document_id"], OFFICER)             # pressing Approve twice changes nothing
    assert len(deps.accounts.verified) == 1 and len(inbox(deps, user)) == 1
    with pytest.raises(service.Conflict):                                 # an accepted document is final
        kyc.upload(conn, deps, user["customer_id"], "PAN", JPEG)
    assert len(stored_files(deps)) == 2


def test_if_the_account_service_is_down_approving_again_finishes_the_job(conn, deps, user):
    pan = kyc.upload(conn, deps, user["customer_id"], "PAN", PNG)
    address = kyc.upload(conn, deps, user["customer_id"], "ADDRESS_PROOF", PDF)
    kyc.approve(conn, deps, pan["document_id"], OFFICER)
    deps.accounts.fail = service.Unavailable("account service unavailable")
    with pytest.raises(service.Unavailable):
        kyc.approve(conn, deps, address["document_id"], OFFICER)
    assert deps.accounts.verified == [] and inbox(deps, user) == []
    deps.accounts.fail = None
    assert kyc.approve(conn, deps, address["document_id"], OFFICER)["kyc_verified"] is True
    assert deps.accounts.verified == [str(user["customer_id"])]


def test_reject_deletes_the_file_tells_the_customer_and_allows_a_new_upload(conn, deps, user):
    doc = kyc.upload(conn, deps, user["customer_id"], "PAN", PNG)
    with pytest.raises(kyc.InvalidDocument):
        kyc.reject(conn, deps, doc["document_id"], OFFICER, "no")         # a reason the customer can act on
    rejected = kyc.reject(conn, deps, doc["document_id"], OFFICER, "The photo is too blurred to read")
    assert rejected["status"] == "REJECTED" and rejected["has_file"] is False
    assert stored_files(deps) == []                                       # we don't keep ID we didn't accept
    with pytest.raises(kyc.Gone):
        kyc.content(conn, deps, doc["document_id"], OFFICER)
    note = inbox(deps, user)[0]
    assert note["title"] == "Please upload your PAN card again" and "too blurred" in note["body"]
    status = kyc.my_documents(conn, user["customer_id"])
    assert status["missing"] == ["PAN", "ADDRESS_PROOF"]
    assert status["types"][0]["document"]["reject_reason"] == "The photo is too blurred to read"
    with pytest.raises(service.Conflict):
        kyc.approve(conn, deps, doc["document_id"], OFFICER)
    again = kyc.upload(conn, deps, user["customer_id"], "PAN", JPEG)
    assert again["status"] == "PENDING" and len(stored_files(deps)) == 1


def test_only_kyc_staff_decide(conn, deps, user):
    doc = kyc.upload(conn, deps, user["customer_id"], "PAN", PNG)
    for actor in (TELLER, customer(user), Actor("system", "unknown")):
        with pytest.raises(kyc.Forbidden):
            kyc.approve(conn, deps, doc["document_id"], actor)
        with pytest.raises(kyc.Forbidden):
            kyc.reject(conn, deps, doc["document_id"], actor, "not allowed to decide")
    assert kyc.my_documents(conn, user["customer_id"])["types"][0]["document"]["status"] == "PENDING"


def test_the_same_file_under_two_customers_is_flagged_to_the_reviewer(conn, deps, user):
    other = service.register(conn, deps, full_name="Second Person", date_of_birth=__import__("datetime").date(1990, 1, 1),
                             phone=f"+919{uuid.uuid4().int % 10**9:09d}", email=f"p{uuid.uuid4().hex[:8]}@example.com",
                             password="correct horse battery")
    unique = PNG + uuid.uuid4().bytes
    kyc.upload(conn, deps, user["customer_id"], "PAN", unique)
    kyc.upload(conn, deps, other["customer_id"], "PAN", unique)
    kyc.upload(conn, deps, other["customer_id"], "PHOTO", JPEG + uuid.uuid4().bytes)
    flags = {d["doc_type"]: d["same_file_other_customers"] for d in kyc.customer_documents(conn, other["customer_id"])}
    assert flags == {"PAN": 1, "PHOTO": 0}


# ------------------------------------------------------------------ over HTTP


@pytest.fixture
def api(db_url, deps, monkeypatch):
    from app import main

    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setitem(main._state, "deps", deps)
    with TestClient(main.app) as client:
        yield client


def test_http_upload_and_download(api, user):
    me = {"X-Actor": f"customer:{user['customer_id']}"}
    r = api.post("/v1/kyc/documents?doc_type=PAN", content=PDF, headers={**me, "Content-Type": "application/pdf"})
    assert r.status_code == 201 and r.json()["content_type"] == "application/pdf"
    document_id = r.json()["document_id"]

    lied = api.post("/v1/kyc/documents?doc_type=PHOTO", content=b"<script>x</script>",
                    headers={**me, "Content-Type": "image/png"})          # the header is not believed
    assert lied.status_code == 422 and lied.json()["error"] == "INVALID_DOCUMENT"
    big = api.post("/v1/kyc/documents?doc_type=PHOTO", content=PNG + b"\x00" * kyc.MAX_BYTES, headers=me)
    assert big.status_code == 413
    assert api.post("/v1/kyc/documents?doc_type=PAN", content=PNG, headers={"X-Actor": "staff:kyc1:KYC"}).status_code == 403

    got = api.get(f"/v1/kyc/documents/{document_id}/content", headers=me)
    assert got.status_code == 200 and got.content == PDF and got.headers["content-type"] == "application/pdf"
    assert got.headers["x-content-type-options"] == "nosniff"
    assert got.headers["content-security-policy"].startswith("sandbox")
    assert got.headers["cache-control"] == "no-store"
    stranger = api.get(f"/v1/kyc/documents/{document_id}/content", headers={"X-Actor": f"customer:{uuid.uuid4()}"})
    assert stranger.status_code == 404

    officer = {"X-Actor": "staff:kyc1:KYC"}
    assert [d["document_id"] for d in api.get("/internal/v1/kyc/queue", headers=officer).json()
            if d["customer_id"] == str(user["customer_id"])] == [document_id]
    rejected = api.post(f"/internal/v1/kyc/documents/{document_id}/reject", headers=officer,
                        json={"reason": "Please send the front of the card"})
    assert rejected.status_code == 200 and rejected.json()["status"] == "REJECTED"
    assert api.get(f"/v1/kyc/documents/{document_id}/content", headers=me).status_code == 410
