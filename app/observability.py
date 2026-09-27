"""
Structured logging: one JSON object per line, tagged with a request id.

Deliberately small. It uses the standard library and nothing else: no
metrics, no tracing, no log shipper. The goal is that a line like

    {"event": "transaction.posted", "request_id": "...", "transaction_id": "...",
     "idempotency_key": "..."}

can be grepped out of container logs and joined to the ledger row and the
HTTP request it came from.

Every request gets an id, from the client's `X-Request-ID` header if it
sent a sane one, otherwise freshly generated. The id goes into a context
variable, so any log call made while serving that request carries it
without being passed around, and it is echoed back in the response's
`X-Request-ID` header so a client can quote it.

Fields are attached with the standard `extra=` argument:

    log.info("transaction.posted", extra={"transaction_id": str(txn_id)})
"""

import json
import logging
import re
import time
import uuid
from contextvars import ContextVar

from fastapi import Request

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

# A client-supplied id goes straight into log lines, so it is only accepted
# if it cannot break them: short, and no whitespace, quotes or control
# characters.
_SAFE_REQUEST_ID = re.compile(r"[A-Za-z0-9._\-]{1,128}")

# Every attribute a bare LogRecord has. Anything else on a record was put
# there by `extra=` and belongs in the JSON output.
_RECORD_ATTRS = set(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {
    "message",
    "asctime",
    "taskName",
}

log = logging.getLogger("keel")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        request_id = request_id_var.get()
        if request_id:
            entry["request_id"] = request_id
        entry.update({k: v for k, v in vars(record).items() if k not in _RECORD_ATTRS})
        if record.exc_info:
            entry["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def configure_logging(level: str = "INFO") -> None:
    """
    Give the `keel` logger a JSON handler on stderr. Idempotent, so an app
    re-imported by a reloader or a test does not stack handlers.

    `propagate = False` keeps these lines out of uvicorn's own
    (non-JSON) handlers on the root logger.
    """
    if not any(isinstance(h.formatter, JsonFormatter) for h in log.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        log.addHandler(handler)
    log.setLevel(level.upper())
    log.propagate = False


async def request_context_middleware(request: Request, call_next):
    """Assign the request id, log one line per request, echo the id back."""
    supplied = request.headers.get("x-request-id", "")
    request_id = supplied if _SAFE_REQUEST_ID.fullmatch(supplied) else uuid.uuid4().hex
    token = request_id_var.set(request_id)
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        log.exception(
            "request.failed",
            extra={
                "method": request.method,
                "path": request.url.path,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            },
        )
        raise
    else:
        log.info(
            "request.completed",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            },
        )
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        request_id_var.reset(token)
