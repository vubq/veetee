"""Structured logging helpers with conservative secret redaction."""

import json
import logging
import re
from typing import Any

from .app_context import request_id_context

_SECRET_NAME = re.compile(r"(key|token|secret|password|credential|authorization)", re.I)
_BEARER = re.compile(r"Bearer\s+[^\s]+", re.I)
_SECRET_VALUE = re.compile(
    r"\b(api[_-]?key|token|secret|password|credential|authorization)"
    r"(?P<separator>\s*(?::|=|\s)\s*)(?:Bearer\s+)?[^\s,;}\]]+",
    re.I,
)


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if _SECRET_NAME.search(str(key)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        value = _SECRET_VALUE.sub(
            lambda match: f"{match.group(1)}{match.group('separator')}[REDACTED]", value
        )
        return _BEARER.sub("Bearer [REDACTED]", value)
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = request_id_context.get()
        if request_id:
            payload["request_id"] = request_id
        context = getattr(record, "context", None)
        if context:
            payload["context"] = context
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(redact(payload), ensure_ascii=False, separators=(",", ":"))


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
