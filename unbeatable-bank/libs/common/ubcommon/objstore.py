"""Object storage: Cloud Storage in the cloud, a local folder otherwise."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol


class ObjectStore(Protocol):
    def put(self, path: str, data: bytes, content_type: str = "application/octet-stream") -> str: ...

    def get(self, path: str) -> bytes | None: ...


class GcsObjectStore:
    def __init__(self, bucket: str | None = None) -> None:
        from google.cloud import storage

        self._bucket = storage.Client().bucket(bucket or os.environ["GCS_BUCKET"])

    def put(self, path, data, content_type="application/octet-stream"):
        self._bucket.blob(path).upload_from_string(data, content_type=content_type)
        return f"gs://{self._bucket.name}/{path}"

    def get(self, path):
        blob = self._bucket.blob(path)
        return blob.download_as_bytes() if blob.exists() else None


class LocalObjectStore:
    def __init__(self, root: str | None = None) -> None:
        self._root = Path(root or os.getenv("OBJSTORE_DIR", "/tmp/unbeatable-objects"))

    def put(self, path, data, content_type="application/octet-stream"):
        target = self._root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return str(target)

    def get(self, path):
        target = self._root / path
        return target.read_bytes() if target.exists() else None


def get_objstore() -> ObjectStore:
    """OBJSTORE=gcs in GCP; anything else writes to a local folder."""
    if os.getenv("OBJSTORE", "local") == "gcs":
        return GcsObjectStore()
    return LocalObjectStore()
