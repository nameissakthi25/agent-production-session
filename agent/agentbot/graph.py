"""Supervisor and three workers.

    supervisor  decides who answers. Calls no tools itself.
       retriever      search_kb
       tool_agent     lookup_ticket, check_service_status
    synthesizer  writes the final answer. NO tools, on purpose.

Written as an explicit loop rather than with a graph framework. The routing is
about forty lines, and forty lines you can read beat a DSL you have to trust —
this folder exists to show what the supervisor decided and what the guard
refused, and both are easier to see without a framework's spans in the way.

Every node opens its own span, so a trace is the shape of the run.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from openai import OpenAI

from agentbot.config import (
    MAX_TOKENS,
    MODEL_NAME,
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    REQUEST_TIMEOUT,
    TEMPERATURE,
)
from agentbot.guards.base import GuardRejected
from agentbot.guards.tool_guard import TOOL_ALLOWLIST, check_tool_call
from agentbot.observability import tracer
from agentbot.tools import REGISTRY, schemas_for

_client = OpenAI(base_url=OPENAI_BASE_URL, api_key=OPENAI_API_KEY, timeout=REQUEST_TIMEOUT)

# Prose budgets are handled by _complete_text() rather than by a large
# default, so a short answer stays fast.

# How many tool calls one worker may make before we stop it. An agent that
# loops is the normal failure, not an exotic one, and a budget is the only
# thing that reliably ends it.
MAX_TOOL_CALLS = 4

# The router answers in one word but thinks first, so it needs room for the
# thinking it does not show. See route().
ROUTER_MAX_TOKENS = 256

SUPERVISOR_PROMPT = """You route an IT support question to exactly one worker.

  retriever    for policies, how-to questions, "how do I", "what is the rule"
  tool_agent   for a specific ticket id, or the live status of a named service
  synthesizer  when the question needs neither -- a greeting, or something no
               worker could help with

Reply with ONLY the worker name. No explanation, no punctuation."""

WORKER_PROMPT = """You are the {name} worker for an IT support assistant.

Use your tools to gather what is needed, then stop. Do not write the final
answer for the user -- another worker does that. Report what you found, plainly,
including when you found nothing.

If a tool call is refused, do not retry the same call unchanged. Read the
reason, and either fix the arguments or report that you could not get it."""

SYNTHESIZER_PROMPT = """You write the final answer for an internal IT support
assistant, from the notes another worker gathered.

Rules:
1. Answer only from the notes. If they do not cover the question, say so plainly.
2. Cite sources as [1], [2] where the notes name them.
3. If a tool call was refused, say what could not be checked and why -- do not
   pretend the information is unavailable for some other reason.
4. Never invent a URL, a phone number, an email address or a person's name."""


@dataclass
class Step:
    """One thing that happened, for the UI and for the trace."""

    node: str
    detail: str
    ms: float = 0.0
    refused: bool = False


@dataclass
class Result:
    answer: str
    route: str = ""
    steps: list[Step] = field(default_factory=list)
    notes: str = ""
    citations: list[str] = field(default_factory=list)
    tool_calls: int = 0
    refusals: int = 0


def _chat(messages: list[dict], tools: list[dict] | None = None, **kw):
    return _client.chat.completions.create(
        model=MODEL_NAME,
        messages=messages,
        temperature=kw.pop("temperature", TEMPERATURE),
        max_tokens=kw.pop("max_tokens", MAX_TOKENS),
        **({"tools": tools, "tool_choice": "auto"} if tools else {}),
        **kw,
    )


def _complete_text(messages: list[dict], span=None, **kw) -> str:
    """Ask for prose, and cope with a reasoning model spending the budget.

    This model thinks before it writes, and the thinking is charged to
    max_tokens. MEASURED on a real answer: 1,984 completion tokens to produce
    704 characters of text -- so a 700-token budget returned
    finish_reason="length" with content EMPTY. Not a refusal, not a filter, not
    an error: just an answer that never got to the writing part.

    So: if the content is empty because we ran out of room, ask once more with a
    much larger budget. Raising the default instead would make every short
    answer slow, and would still break on the next long one.
    """
    reply = _chat(messages, **kw)
    choice = reply.choices[0]
    text = (choice.message.content or "").strip()

    if text or choice.finish_reason != "length":
        return text

    if span is not None:
        span.set_attribute("retried_for_token_budget", True)
        span.set_attribute(
            "first_attempt.completion_tokens", reply.usage.completion_tokens if reply.usage else -1
        )

    kw["max_tokens"] = max(kw.get("max_tokens", MAX_TOKENS), MAX_TOKENS) * 3
    retry = _chat(messages, **kw)
    text = (retry.choices[0].message.content or "").strip()
    return text or (
        "I could not produce an answer within the token budget. The notes were "
        "gathered but the write-up did not fit."
    )


def route(question: str) -> str:
    """Pick one worker. Falls back to the retriever rather than failing.

    temperature=0 because this is a classification, not a piece of writing, and
    a router that answers differently on a re-run is a router you cannot debug.
    """
    with tracer().start_as_current_span("supervisor") as span:
        started = time.perf_counter()
        reply = _chat(
            [
                {"role": "system", "content": SUPERVISOR_PROMPT},
                {"role": "user", "content": question},
            ],
            temperature=0.0,
            # NOT 8, however tempting for a one-word answer. This model emits
            # reasoning tokens before its content, so a small budget is spent
            # entirely on thinking and the reply comes back
            # content=None, finish_reason="length" -- every single time.
            # Measured: at 8 the router NEVER answered and every question fell
            # back to the retriever, which looked like a bad router rather than
            # no router at all.
            max_tokens=ROUTER_MAX_TOKENS,
        )
        choice = reply.choices[0]
        raw = (choice.message.content or "").strip().lower()
        chosen = next((w for w in TOOL_ALLOWLIST if w in raw), None)
        fell_back = chosen is None
        if fell_back:
            # The retriever is the least-worst default, but record LOUDLY that
            # nothing was decided. A silent fallback is how a broken router
            # goes unnoticed.
            chosen = "retriever"
        span.set_attribute("route", chosen)
        span.set_attribute("route.raw", raw[:60] or "(empty)")
        span.set_attribute("route.finish_reason", choice.finish_reason or "")
        span.set_attribute("route.fell_back", fell_back)
        span.set_attribute("duration_ms", (time.perf_counter() - started) * 1000)
        return chosen


def run_worker(name: str, question: str, steps: list[Step]) -> tuple[str, list[str]]:
    """Let one worker use its tools. Returns its notes and any citations."""
    allowed = TOOL_ALLOWLIST[name]
    tools = schemas_for(allowed)
    citations: list[str] = []

    with tracer().start_as_current_span(name) as span:
        span.set_attribute("agent.name", name)
        span.set_attribute("agent.allowed_tools", ", ".join(allowed) or "none")

        messages = [
            {"role": "system", "content": WORKER_PROMPT.format(name=name)},
            {"role": "user", "content": question},
        ]

        used = 0
        while used < MAX_TOOL_CALLS:
            reply = _chat(messages, tools=tools or None)
            message = reply.choices[0].message
            calls = message.tool_calls or []

            if not calls:
                notes = message.content or ""
                span.set_attribute("agent.tool_calls", used)
                return notes, citations

            # A model can request several tools in ONE reply, so the budget has
            # to count calls rather than turns. Counting turns let a looping
            # retriever make seven searches under a limit of four.
            calls = calls[: MAX_TOOL_CALLS - used]
            used += len(calls)

            messages.append(
                {
                    "role": "assistant",
                    "content": message.content or "",
                    "tool_calls": [
                        {
                            "id": c.id,
                            "type": "function",
                            "function": {
                                "name": c.function.name,
                                "arguments": c.function.arguments,
                            },
                        }
                        for c in calls
                    ],
                }
            )

            for call in calls:
                messages.append(_execute(name, call, steps, citations))

        # Out of budget. Ask once more, WITHOUT tools, so the worker reports
        # what it already found instead of handing the synthesizer nothing but
        # the news that it gave up.
        span.set_attribute("agent.hit_call_budget", True)
        steps.append(
            Step(name, f"tool budget spent ({MAX_TOOL_CALLS} calls) -- summarising", refused=True)
        )
        messages.append(
            {
                "role": "user",
                "content": "You have used your tool budget. Report what you found "
                "so far, and say plainly what you could not establish.",
            }
        )
        return _complete_text(messages, span=span), citations


def _execute(agent: str, call, steps: list[Step], citations: list[str]) -> dict:
    """Guard, then run, one tool call. Always returns a tool message.

    Even a refusal comes back as a tool result. The model needs to see what
    happened in the place it expects to see it, or the next turn is blind.
    """
    tool = call.function.name
    started = time.perf_counter()

    with tracer().start_as_current_span("tool_guard") as span:
        span.set_attribute("guardrail.name", "tool_guard")
        span.set_attribute("guardrail.agent", agent)
        span.set_attribute("guardrail.tool", tool)
        try:
            arguments = check_tool_call(agent, tool, call.function.arguments)
            span.set_attribute("guardrail.passed", True)
        except GuardRejected as rejection:
            span.set_attribute("guardrail.passed", False)
            span.set_attribute("guardrail.reason", rejection.reason)
            span.set_attribute("guardrail.backend", rejection.backend)
            ms = (time.perf_counter() - started) * 1000
            steps.append(Step(f"tool_guard → {tool}", rejection.reason, ms, refused=True))
            return {
                "role": "tool",
                "tool_call_id": call.id,
                "content": f"REFUSED by the tool guard: {rejection.reason}",
            }

    with tracer().start_as_current_span(f"tool:{tool}") as span:
        function = REGISTRY[tool][0]
        output = function(**arguments)
        span.set_attribute("tool.name", tool)
        span.set_attribute("tool.arguments", json.dumps(arguments)[:200])
        span.set_attribute("tool.output_chars", len(output))

    ms = (time.perf_counter() - started) * 1000
    steps.append(Step(f"{tool}", json.dumps(arguments), ms))

    if tool == "search_kb":
        for line in output.splitlines():
            if line.startswith("[") and "source: " in line:
                citations.append(line.split("source: ", 1)[1].strip())

    return {"role": "tool", "tool_call_id": call.id, "content": output}


def synthesize(question: str, notes: str) -> str:
    """Write the answer. This worker has no tools, and the guard enforces that."""
    with tracer().start_as_current_span("synthesizer") as span:
        started = time.perf_counter()
        answer = _complete_text(
            [
                {"role": "system", "content": SYNTHESIZER_PROMPT},
                {
                    "role": "user",
                    "content": f"Question: {question}\n\nNotes:\n{notes or '(none)'}",
                },
            ],
            span=span,
        )
        span.set_attribute("duration_ms", (time.perf_counter() - started) * 1000)
        span.set_attribute("answer_chars", len(answer))
        return answer


def run(question: str) -> Result:
    """One whole turn: route, work, synthesize."""
    steps: list[Step] = []

    chosen = route(question)
    steps.append(Step("supervisor", f"routed to {chosen}"))

    if chosen == "synthesizer":
        notes, citations = "", []
    else:
        notes, citations = run_worker(chosen, question, steps)
        steps.append(Step(chosen, f"gathered {len(notes)} characters of notes"))

    answer = synthesize(question, notes)
    steps.append(Step("synthesizer", "wrote the answer"))

    return Result(
        answer=answer,
        route=chosen,
        steps=steps,
        notes=notes,
        citations=citations,
        tool_calls=sum(1 for s in steps if not s.refused and s.node in REGISTRY),
        refusals=sum(1 for s in steps if s.refused),
    )
