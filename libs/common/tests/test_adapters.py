"""Contract tests: the same assertions run against every backend of each adapter,
so the local stand-ins and the Google services are known to behave alike.

    pytest libs/common/tests                       # in-memory and local-folder backends
    TEST_ADMIN_URL=postgresql://...  pytest ...    # + the Postgres document store
    FIRESTORE_EMULATOR_HOST=localhost:8085 PUBSUB_EMULATOR_HOST=localhost:8086 \
    STORAGE_EMULATOR_HOST=http://localhost:4443 GCP_PROJECT=test-project pytest ...   # + Google emulators

A backend whose service is not configured is skipped, never silently passed. CI
(job "adapters") runs all of them and fails if any is skipped (REQUIRE_ALL_BACKENDS=1).
"""

import os
import time
import uuid

import pytest

from ubcommon import docstore, objstore, pubsub

PROJECT = os.getenv("GCP_PROJECT", "test-project")


def need(env: str) -> None:
    if not os.getenv(env):
        if os.getenv("REQUIRE_ALL_BACKENDS"):
            pytest.fail(f"{env} is not set but REQUIRE_ALL_BACKENDS is")
        pytest.skip(f"{env} not set")


# ------------------------------------------------------------------ document store


@pytest.fixture(params=["memory", "postgres", "firestore"])
def docs(request):
    if request.param == "memory":
        yield docstore.MemoryDocStore()
    elif request.param == "postgres":
        need("TEST_ADMIN_URL")
        store = docstore.PostgresDocStore(os.environ["TEST_ADMIN_URL"])
        yield store
        store.close()
    else:
        need("FIRESTORE_EMULATOR_HOST")
        yield docstore.FirestoreDocStore(project=PROJECT)


@pytest.fixture
def col():
    return f"t_{uuid.uuid4().hex[:12]}"


def test_set_get_replace_and_delete(docs, col):
    assert docs.get(col, "a") is None
    docs.set(col, "a", {"name": "Riya", "n": 1, "tags": ["x", "y"], "nested": {"k": 1.5}})
    assert docs.get(col, "a") == {"id": "a", "name": "Riya", "n": 1, "tags": ["x", "y"], "nested": {"k": 1.5}}
    docs.set(col, "a", {"name": "Arjun"})                      # a plain set replaces the document
    assert docs.get(col, "a") == {"id": "a", "name": "Arjun"}
    docs.delete(col, "a")
    assert docs.get(col, "a") is None
    docs.delete(col, "a")                                       # deleting twice is not an error


def test_merge_keeps_other_fields(docs, col):
    docs.set(col, "a", {"name": "Riya", "read": False})
    docs.set(col, "a", {"read": True}, merge=True)
    assert docs.get(col, "a") == {"id": "a", "name": "Riya", "read": True}
    docs.set(col, "new", {"read": True}, merge=True)            # merge into nothing creates it
    assert docs.get(col, "new") == {"id": "new", "read": True}


def test_add_makes_a_new_id_each_time(docs, col):
    first, second = docs.add(col, {"n": 1}), docs.add(col, {"n": 2})
    assert first != second
    assert docs.get(col, first) == {"id": first, "n": 1}


def test_query_filters_sorts_and_limits(docs, col):
    for i in range(6):
        docs.set(col, f"d{i}", {"customer": "c1" if i % 2 == 0 else "c2", "at": float(i), "read": i < 2})
    mine = docs.query(col, {"customer": "c1"}, order_by="at", descending=True)
    assert [d["id"] for d in mine] == ["d4", "d2", "d0"]
    assert [d["id"] for d in docs.query(col, {"customer": "c1"}, order_by="at", limit=2)] == ["d0", "d2"]
    assert [d["id"] for d in docs.query(col, {"customer": "c1", "read": True})] == ["d0"]      # two filters
    assert docs.query(col, {"customer": "nobody"}) == []
    newest = docs.query(col, order_by="at", descending=True, limit=3)                            # no filter
    assert [d["id"] for d in newest] == ["d5", "d4", "d3"]


def test_expired_documents_are_gone(docs, col):
    """Sessions, OTPs and signals carry expires_at. Firestore's own TTL clean-up can lag by a
    day, so every backend must treat an expired document as absent the moment it expires."""
    now = time.time()
    docs.set(col, "old", {"customer": "c1", "expires_at": now - 5})
    docs.set(col, "live", {"customer": "c1", "expires_at": now + 600})
    docs.set(col, "forever", {"customer": "c1"})
    assert docs.get(col, "old") is None
    live = docs.get(col, "live")
    assert live is not None and abs(live["expires_at"] - (now + 600)) < 0.01      # still epoch seconds
    assert sorted(d["id"] for d in docs.query(col, {"customer": "c1"})) == ["forever", "live"]


# ------------------------------------------------------------------ object store


@pytest.fixture(params=["local", "gcs"])
def objects(request, tmp_path):
    if request.param == "local":
        yield objstore.LocalObjectStore(str(tmp_path))
        return
    need("STORAGE_EMULATOR_HOST")
    from google.cloud import storage

    bucket = f"test-{uuid.uuid4().hex[:12]}"
    storage.Client(project=PROJECT).create_bucket(bucket)
    yield objstore.GcsObjectStore(bucket)


def test_objects_put_get_overwrite_delete(objects):
    assert objects.get("kyc/c1/doc.pdf") is None
    data = bytes(range(256)) * 40                                # binary, not text
    objects.put("kyc/c1/doc.pdf", data, "application/pdf")
    assert objects.get("kyc/c1/doc.pdf") == data
    objects.put("kyc/c1/doc.pdf", b"v2", "application/pdf")
    assert objects.get("kyc/c1/doc.pdf") == b"v2"
    assert objects.get("kyc/c1") is None                         # a folder is not an object
    objects.delete("kyc/c1/doc.pdf")
    assert objects.get("kyc/c1/doc.pdf") is None
    objects.delete("kyc/c1/doc.pdf")                             # deleting twice is not an error


def test_local_store_refuses_paths_outside_its_folder(tmp_path):
    store = objstore.LocalObjectStore(str(tmp_path / "root"))
    for bad in ("../outside.txt", "a/../../outside.txt", "/etc/passwd"):
        with pytest.raises(ValueError):
            store.put(bad, b"x")
        with pytest.raises(ValueError):
            store.get(bad)


# ------------------------------------------------------------------ pub/sub


def test_local_publisher_keeps_and_forwards_messages():
    seen = []
    publisher = pubsub.LocalPublisher(lambda topic, data, attrs: seen.append((topic, data, attrs)))
    publisher.publish("notifications", {"type": "OTP", "amount": 5}, channel="inbox")
    assert seen == publisher.messages == [("notifications", {"type": "OTP", "amount": 5}, {"channel": "inbox"})]


def test_gcp_publisher_delivers_json_and_attributes():
    need("PUBSUB_EMULATOR_HOST")
    import json

    from google.cloud import pubsub_v1

    topic, sub = f"t-{uuid.uuid4().hex[:10]}", f"s-{uuid.uuid4().hex[:10]}"
    admin, subscriber = pubsub_v1.PublisherClient(), pubsub_v1.SubscriberClient()
    topic_path, sub_path = admin.topic_path(PROJECT, topic), subscriber.subscription_path(PROJECT, sub)
    admin.create_topic(name=topic_path)
    subscriber.create_subscription(name=sub_path, topic=topic_path)

    pubsub.GcpPublisher(project=PROJECT).publish(topic, {"type": "OTP", "customer_id": "c1", "amount": 700000},
                                                 channel="inbox")
    got = subscriber.pull(subscription=sub_path, max_messages=5, timeout=10).received_messages
    assert len(got) == 1
    assert json.loads(got[0].message.data) == {"type": "OTP", "customer_id": "c1", "amount": 700000}
    assert dict(got[0].message.attributes) == {"channel": "inbox"}
