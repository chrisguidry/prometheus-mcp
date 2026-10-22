"""Turning errors in a tool call into messages a model can act on.

The server masks the details of unexpected errors, so that a stack trace
never leaks internal state to the client. The errors handled here are
different: Prometheus writes its error detail for the caller ("parse error:
unexpected identifier"), and a bad time argument or an unknown server slug is
the model's own mistake. The model needs that detail to correct its next call,
so this middleware passes it through as a ToolError, which the server never
masks.

FastMCP masks a tool's exception before middleware sees it, but it chains the
original exception as the masked error's cause, so that is where this looks.
"""

from __future__ import annotations

import logging

import mcp.types as mt
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools import ToolResult

from prometheus_mcp.config import UnknownServerError
from prometheus_mcp.prometheus import PrometheusError
from prometheus_mcp.time_range import TimeRangeError

__all__ = ["PrometheusErrorMiddleware"]

logger: logging.Logger = logging.getLogger(__name__)

# The messages of these errors hold no credentials: the client names a server
# by its slug and never by its URL.
_REPORTED = (PrometheusError, TimeRangeError, UnknownServerError)


class PrometheusErrorMiddleware(Middleware):
    """Report Prometheus errors and argument errors from any tool as ToolErrors."""

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        try:
            return await call_next(context)
        except ToolError as error:
            cause = error.__cause__
            if not isinstance(cause, _REPORTED):
                raise
            logger.warning("%s in %s", type(cause).__name__, context.message.name)
            raise ToolError(str(cause)) from cause
