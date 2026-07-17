"""OpenTelemetry distributed tracing setup.

Provides lightweight OTel spans around LLM calls, MCP tool invocations,
and DB writes.  Designed to degrade gracefully when the ``opentelemetry``
package is not installed — all public helpers return no-op context managers
in that case.

Wire-up:
    Call ``setup_telemetry()`` once from ``app/main.py`` on startup.
    Then use ``agent_span()``, ``tool_span()``, and ``db_span()`` as async
    context managers wherever you need a span.

trace_id storage:
    ``get_current_trace_id()`` returns the hex trace-id of the active span
    so callers can persist it on an execution or investigation row.

"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager, contextmanager
from typing import Any, AsyncGenerator, Generator, Optional

logger = logging.getLogger(__name__)

# Lazily imported so the app starts even without the package installed
_tracer: Any = None
_otel_available: bool = False


def setup_telemetry(service_name: str = "kyc-protect-oncall-agent") -> None:
    """Initialise OTel SDK and configure exporters.

    Reads configuration from environment variables:
    - ``OTEL_ENABLED``          : set to ``"true"`` to activate (default: off in dev)
    - ``OTEL_EXPORTER_OTLP_ENDPOINT``: OTLP gRPC endpoint (e.g. ``http://localhost:4317``)
    - ``OTEL_TRACES_SAMPLER``   : e.g. ``"always_on"`` or ``"traceidratio"``

    Silently skips setup when ``opentelemetry`` is not installed.
    """
    global _tracer, _otel_available

    if os.getenv("OTEL_ENABLED", "").lower() != "true":
        logger.debug("OTel tracing disabled (set OTEL_ENABLED=true to activate)")
        return

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
        from opentelemetry.sdk.resources import Resource

        resource = Resource(attributes={"service.name": service_name})
        provider = TracerProvider(resource=resource)

        # Wire the OTLP exporter when an endpoint is configured
        otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
        if otlp_endpoint:
            try:
                from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
                provider.add_span_processor(
                    BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint))
                )
                logger.info("OTel: OTLP exporter wired to %s", otlp_endpoint)
            except ImportError:
                logger.warning(
                    "OTel: opentelemetry-exporter-otlp-proto-grpc not installed; "
                    "falling back to console exporter"
                )
                provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
        else:
            # Dev default — write spans to stdout
            provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))

        trace.set_tracer_provider(provider)
        _tracer = trace.get_tracer(service_name)
        _otel_available = True
        logger.info("OTel tracing initialised for service '%s'", service_name)

    except ImportError:
        logger.warning(
            "opentelemetry-sdk not installed — tracing disabled. "
            "Install opentelemetry-sdk and opentelemetry-api to enable."
        )


def get_current_trace_id() -> Optional[str]:
    """Return the hex trace-id of the currently active span, or None."""
    if not _otel_available:
        return None
    try:
        from opentelemetry import trace
        ctx = trace.get_current_span().get_span_context()
        if ctx and ctx.is_valid:
            return format(ctx.trace_id, "032x")
    except Exception:
        pass
    return None


@asynccontextmanager
async def agent_span(
    agent_name: str,
    execution_id: Optional[str] = None,
    model: Optional[str] = None,
) -> AsyncGenerator[Any, None]:
    """Async context manager: OTel span around an LLM / agent call.

    Usage::

        async with agent_span("react_agent", execution_id=eid, model="claude-3-5-sonnet"):
            result = await agent.ainvoke(input_state)
    """
    if not _otel_available or _tracer is None:
        yield None
        return

    with _tracer.start_as_current_span(f"agent.{agent_name}") as span:
        if execution_id:
            span.set_attribute("execution.id", execution_id)
        if model:
            span.set_attribute("llm.model", model)
        yield span


@asynccontextmanager
async def tool_span(
    tool_name: str,
    execution_id: Optional[str] = None,
) -> AsyncGenerator[Any, None]:
    """Async context manager: OTel span around an MCP tool invocation."""
    if not _otel_available or _tracer is None:
        yield None
        return

    with _tracer.start_as_current_span(f"tool.{tool_name}") as span:
        if execution_id:
            span.set_attribute("execution.id", execution_id)
        span.set_attribute("tool.name", tool_name)
        yield span


@asynccontextmanager
async def db_span(
    operation: str,
    table: Optional[str] = None,
) -> AsyncGenerator[Any, None]:
    """Async context manager: OTel span around a database write."""
    if not _otel_available or _tracer is None:
        yield None
        return

    with _tracer.start_as_current_span(f"db.{operation}") as span:
        if table:
            span.set_attribute("db.table", table)
        span.set_attribute("db.operation", operation)
        yield span
