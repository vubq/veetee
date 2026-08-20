"""Request-scoped context shared by middleware and logging."""

from contextvars import ContextVar

request_id_context: ContextVar[str | None] = ContextVar("veetee_request_id", default=None)
