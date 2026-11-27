"""One allowlisted metadata record per request; never serialize request objects."""

import json
import logging
import re
import sys
from contextvars import ContextVar
from time import perf_counter
from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger("meme_generator")
logger.setLevel(logging.INFO)
logger.propagate = False
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)

request_id: ContextVar[str] = ContextVar("request_id", default="")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        supplied = request.headers.get("X-Request-ID", "")
        identifier = supplied if re.fullmatch(r"[A-Za-z0-9-]{1,64}", supplied) else uuid4().hex
        token = request_id.set(identifier)
        started = perf_counter()
        metrics: dict[str, object] = {}
        request.state.metrics = metrics
        try:
            try:
                response = await call_next(request)
            except Exception:
                # No exception text or traceback: parsers/libraries may include input.
                response = JSONResponse(
                    {
                        "error": "internal_error",
                        "message": "The request could not be completed.",
                        "request_id": identifier,
                    },
                    status_code=500,
                )
            response.headers["X-Request-ID"] = identifier
            logger.info(
                json.dumps(
                    {
                        "request_id": identifier,
                        "status": response.status_code,
                        **metrics,
                        "total_ms": round((perf_counter() - started) * 1000, 3),
                    }
                )
            )
            return response
        finally:
            request_id.reset(token)
