"""Object storage: Cloud Storage in the cloud, a local folder otherwise.

Paths are plain keys such as "kyc/<customer>/<document>" or
"statements/<account>/2026-11.pdf". Nothing here is public: services read the
bytes and decide who may see them.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol


class ObjectStore(Protocol):
    def put(self, path: str, data: bytes, content_type: str = "application/octet-stream") -> str: ...

    def get(self, path: str) -> bytes | None: ...

    def delete(self, path: str) -> None: ...


class GcsObjectStore:
    def __init__(self, bucket: str | None = None) -> None:
        from google.cloud import storage

        self._bucket = storage.Client().bucket(bucket or os.environ["GCS_BUCKET"])

    def put(self, path, data, content_type="application/octet-stream"):
        self._bucket.blob(path).upload_from_string(data, content_type=content_type)
        return f"gs://{self._bucket.name}/{path}"

    def get(self, path):
        from google.api_core.exceptions import NotFound

        try:
            return self._bucket.blob(path).download_as_bytes()
        except NotFound:
            return None

    def delete(self, path):
        from google.api_core.exceptions import NotFound

        try:
            self._bucket.blob(path).delete()
        except NotFound:
            pass


class LocalObjectStore:
    def __init__(self, root: str | None = None) -> None:
        self._root = Path(root or os.getenv("OBJSTORE_DIR", "/tmp/unbeatable-objects")).resolve()

    def _target(self, path: str) -> Path:
        target = (self._root / path).resolve()
        if not target.is_relative_to(self._root):
            raise ValueError(f"object path escapes the store: {path!r}")
        return target

    def put(self, path, data, content_type="application/octet-stream"):
        target = self._target(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return str(target)

    def get(self, path):
        target = self._target(path)
        return target.read_bytes() if target.is_file() else None

    def delete(self, path):
        self._target(path).unlink(missing_ok=True)


def get_objstore() -> ObjectStore:
    """OBJSTORE=gcs in GCP; anything else writes to a local folder."""
    if os.getenv("OBJSTORE", "local") == "gcs":
        return GcsObjectStore()
    return LocalObjectStore()
