"""Tracing, and the two helpers the app needs from it.

Six lines of setup and every model call becomes a span, with the prompt that was
actually sent attached to it. That last part is the whole point: "why did it say
that?" is a question about one request, and only a trace can answer it.
"""

import contextlib
import json
import time
from datetime import UTC, datetime

from opentelemetry import trace

from bot.config import (
    FEEDBACK_PATH,
    PHOENIX_COLLECTOR_ENDPOINT,
    PHOENIX_PROJECT_NAME,
    PHOENIX_PUBLIC_URL,
    TRACING_ENABLED,
)

_tracer: trace.Tracer | None = None


def setup_tracing() -> trace.Tracer:
    """Point OpenTelemetry at Phoenix and instrument the OpenAI client."""
    global _tracer
    if _tracer is not None:
        return _tracer

    if not TRACING_ENABLED:
        # A no-op provider. Every span call below still works, and records
        # nothing -- so the app runs with tracing off without any `if` in it.
        _tracer = trace.get_tracer(__name__)
        return _tracer

    from openinference.instrumentation.openai import OpenAIInstrumentor
    from phoenix.otel import register

    provider = register(
        endpoint=PHOENIX_COLLECTOR_ENDPOINT,
        project_name=PHOENIX_PROJECT_NAME,
        # batch=False sends each span as it ends, so it appears in the UI
        # immediately. Right for a demo you are watching; wrong for production,
        # where you want them batched.
        batch=False,
        set_global_tracer_provider=True,
    )
    OpenAIInstrumentor().instrument(tracer_provider=provider)

    _tracer = trace.get_tracer(__name__)
    return _tracer


def tracer() -> trace.Tracer:
    return _tracer if _tracer is not None else setup_tracing()


def current_trace_id() -> str | None:
    """The active trace as 32 hex characters, or None outside a span."""
    context = trace.get_current_span().get_span_context()
    if not context.trace_id:
        return None
    return format(context.trace_id, "032x")


def trace_url(trace_id: str | None) -> str:
    """Where a human goes to see this exact run."""
    base = PHOENIX_PUBLIC_URL.rstrip("/")
    if not trace_id:
        return f"{base}/projects"
    # Phoenix's deep-link shape has changed between releases, so the reliable
    # instruction is "open the project and paste the id into the search box".
    # The link gets you to the right place; the id gets you to the right row.
    return f"{base}/projects"


@contextlib.contextmanager
def guard_span(name: str, backend: str = "local"):
    """Run a guard inside its own span, recording the verdict either way.

    Whether it passed, why it did not, and which backend decided -- all as span
    attributes, so a refusal is attached to the request that caused it.
    """
    from bot.guards.base import GuardRejected

    with tracer().start_as_current_span(name) as span:
        span.set_attribute("guardrail.name", name)
        span.set_attribute("guardrail.backend", backend)
        started = time.perf_counter()
        try:
            yield span
        except GuardRejected as rejection:
            span.set_attribute("guardrail.passed", False)
            span.set_attribute("guardrail.reason", rejection.reason)
            span.set_attribute("guardrail.duration_ms", (time.perf_counter() - started) * 1000)
            raise
        else:
            span.set_attribute("guardrail.passed", True)
            span.set_attribute("guardrail.duration_ms", (time.perf_counter() - started) * 1000)


def record_feedback(trace_id: str | None, helpful: bool, question: str = "") -> None:
    """Append a thumb to the feedback file.

    A thumbs-down in its own table is a number. A thumbs-down carrying the trace
    id is a reproducible bug report: you can go and look at exactly what
    happened, then replay that question as an eval case.

    Deliberately a JSONL file rather than a database. It is the smallest thing
    that keeps the link between the complaint and the run, and it is trivially
    readable by whatever scores your evals later.
    """
    row = {
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "trace_id": trace_id,
        "helpful": helpful,
        "question": question,
    }
    with open(FEEDBACK_PATH, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")
