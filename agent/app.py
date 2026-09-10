"""The agent chatbot.

One turn:

    input_guard → supervisor → worker (tool_guard per call) → synthesizer → output_guard

The steps are shown on screen as they happen, because the interesting part of an
agent is not the answer, it is which worker was chosen and what the guard did
about the tools it tried to use.
"""

import chainlit as cl

from agentbot.config import (
    GUARD_VALIDATOR,
    MODEL_NAME,
    PHOENIX_PUBLIC_URL,
    TRACING_ENABLED,
)
from agentbot.graph import run
from agentbot.guards import TOOL_ALLOWLIST, GuardRejected, check_input, check_output
from agentbot.guards.input_guard import backend_name
from agentbot.observability import (
    current_trace_id,
    guard_span,
    record_feedback,
    setup_tracing,
    tracer,
)
from agentbot.retrieval import collection_status


@cl.on_chat_start
async def on_chat_start() -> None:
    setup_tracing()

    lines = ["**IT support assistant, multi-agent**", ""]
    for agent, tools in TOOL_ALLOWLIST.items():
        lines.append(f"- `{agent}` → {', '.join(tools) if tools else '**no tools**'}")
    lines.append("")

    status = collection_status()
    if status["ok"]:
        lines.append(f"- knowledge base: **{status['points']} chunks**")
    else:
        lines.append(f"- ⚠️ Qdrant unreachable: `{status['error']}`")
        lines.append("  `docker compose run --rm ingest`")

    lines.append(f"- model: `{MODEL_NAME}`")
    lines.append(f"- guards: input + **tool** + output (`{backend_name()}`, {GUARD_VALIDATOR})")
    if TRACING_ENABLED:
        lines.append(f"- traces: {PHOENIX_PUBLIC_URL}")

    await cl.Message(content="\n".join(lines)).send()


@cl.on_message
async def on_message(message: cl.Message) -> None:
    question = message.content

    with tracer().start_as_current_span("chat") as root:
        root.set_attribute("input.value", question)
        root.set_attribute("llm.model_name", MODEL_NAME)
        trace_id = current_trace_id()

        try:
            with guard_span("input_guard", backend_name()):
                check_input(question)
        except GuardRejected as rejection:
            root.set_attribute("output.refused_by", rejection.guard)
            await cl.Message(content=(
                f"🛑 **Refused by the {rejection.guard}.**\n\n{rejection.reason}\n\n"
                f"_No worker ran and no tool was called._"
                + (f"\n\n`trace {trace_id}`" if trace_id else "")
            )).send()
            return

        # The graph is synchronous and can take several seconds across three
        # model calls, so it runs in a thread rather than blocking the event
        # loop and freezing the UI.
        async with cl.Step(name="agents", type="run") as step:
            result = await cl.make_async(run)(question)
            step.output = "\n".join(
                f"{'🛑' if s.refused else '·'} {s.node}: {s.detail}"
                + (f"  ({s.ms:.0f} ms)" if s.ms else "")
                for s in result.steps
            )

        root.set_attribute("agent.route", result.route)
        root.set_attribute("agent.tool_calls", result.tool_calls)
        root.set_attribute("agent.refusals", result.refusals)

        try:
            with guard_span("output_guard", backend_name()):
                check_output(result.answer)
        except GuardRejected as rejection:
            root.set_attribute("output.refused_by", rejection.guard)
            await cl.Message(content=(
                f"🛑 **Answer withheld by the {rejection.guard}.**\n\n{rejection.reason}"
            )).send()
            return

        root.set_attribute("output.value", result.answer)

    reply = cl.Message(content=result.answer)
    reply.actions = _thumbs(trace_id, question, result)
    await reply.send()
    await _show_run(result)


async def _show_run(result) -> None:
    """What the agents did, under every answer.

    Always, not on demand. The routing decision is the thing most likely to be
    wrong, and a multi-agent answer that hides it asks you to trust a choice you
    were never shown.
    """
    rows = "\n".join(
        f"| {'🛑' if s.refused else '✓'} | `{s.node}` | {s.detail[:80]} | "
        f"{f'{s.ms:.0f} ms' if s.ms else ''} |"
        for s in result.steps
    )
    body = [
        f"**Route** → `{result.route}`  ·  {result.tool_calls} tool call(s)"
        + (f"  ·  **{result.refusals} refused**" if result.refusals else ""),
        "",
        "| | step | detail | |",
        "|---|---|---|---|",
        rows,
    ]
    if result.citations:
        body += ["", "**Sources:** " + ", ".join(f"`{c}`" for c in dict.fromkeys(result.citations))]
    if result.refusals:
        body += ["", "_A refused tool call does not end the run: the refusal goes "
                 "back to the agent as the tool's result, and it explains itself._"]
    await cl.Message(content="\n".join(body), author="agents").send()


def _thumbs(trace_id, question, result) -> list[cl.Action]:
    payload = {
        "trace_id": trace_id,
        "question": question,
        # For an agent answer the route belongs with the thumb. A thumbs-down is
        # otherwise ambiguous three ways: wrong worker, wrong tool call, or bad
        # writing from good notes.
        "route": result.route,
        "citations": result.citations,
        "tool_calls": result.tool_calls,
        "refusals": result.refusals,
    }
    return [
        cl.Action(name="helpful", payload={**payload, "helpful": True},
                  label="👍", tooltip="This answer was helpful"),
        cl.Action(name="not_helpful", payload={**payload, "helpful": False},
                  label="👎", tooltip="This answer was not helpful"),
    ]


@cl.action_callback("helpful")
async def on_helpful(action: cl.Action) -> None:
    await _record(action)


@cl.action_callback("not_helpful")
async def on_not_helpful(action: cl.Action) -> None:
    await _record(action)


async def _record(action: cl.Action) -> None:
    payload = action.payload or {}
    helpful = bool(payload.get("helpful"))
    record_feedback(
        trace_id=payload.get("trace_id"),
        helpful=helpful,
        question=payload.get("question", ""),
        extra={
            "route": payload.get("route"),
            "citations": payload.get("citations", []),
            "tool_calls": payload.get("tool_calls"),
            "refusals": payload.get("refusals"),
        },
    )
    await cl.Message(
        content=("Recorded: 👍 helpful" if helpful else
                 f"Recorded: 👎 not helpful — saved with the route "
                 f"(`{payload.get('route')}`) and the sources, so it is "
                 f"replayable as an eval case."),
        author="feedback",
    ).send()
    await action.remove()
