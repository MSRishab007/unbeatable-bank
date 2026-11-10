"""Structured JSON logs on stdout.

Cloud Logging picks up `severity` and `message` automatically from GKE
containers, so every service's logs are searchable in one place.
"""

import json
import logging
import sys
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "time": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "severity": record.levelname,
            "service": self.service,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in getattr(record, "fields", {}).items():
            entry[key] = value
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def setup_logging(service: str, level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # uvicorn installs its own handlers; route them through ours
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers[:] = []
        logger.propagate = True


def log_fields(**fields) -> dict:
    """Usage: log.info("payment declined", extra=log_fields(txn_id=..., code=...))"""
    return {"fields": fields}
