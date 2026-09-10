"""The chatbot.

One file of Chainlit handlers. Everything interesting lives in bot/ -- this
layer only turns a chat message into a guarded, traced model call and puts the
result on screen.

The shape of one turn:

    input_guard  ->  model (streamed)  ->  output_guard  ->  answer + thumbs

Each of those is a span under one root, so a refusal is attached to the request
that caused it rather than sitting in a log file somewhere.
"""

import chainlit as cl

from bot.config import MODEL_NAME, PHOENIX_PUBLIC_URL, TRACING_ENABLED
from bot.guards import GuardRejected, check_input, check_output
from bot.guards.input_guard import backend_name
from bot.llm import health, stream_answer
from bot.observability import (
    current_trace_id,
    guard_span,
    record_feedback,
    setup_tracing,
    tracer,
)

# How many prior turns to send back to the model. In-process, per session, and
# gone when this process restarts -- which is honest for a demo and wrong for
# production. A real conversation store is a database, not a variable.
HISTORY_TURNS = 6


@cl.on_chat_start
async def on_chat_start() -> None:
    setup_tracing()
    cl.user_session.set("history", [])

    lines = [
        "**Internal IT support assistant**",
        "",
        f"- model: `{MODEL_NAME}`",
        f"- input + output guards: `{backend_name()}`",
    ]

    try:
        state = await health()
        lines.append(f"- endpoint: `{state['base_url']}` serving `{state['served_model']}`")
    except Exception as error:
        lines.append(f"- ⚠️ endpoint unreachable: `{type(error).__name__}`")
        lines.append("  Start the model server, or set `OPENAI_BASE_URL`.")

    if TRACING_ENABLED:
        lines.append(f"- traces: {PHOENIX_PUBLIC_URL}")

    await cl.Message(content="\n".join(lines)).send()


@cl.on_message
async def on_message(message: cl.Message) -> None:
    question = message.content
    history = cl.user_session.get("history") or []

    with tracer().start_as_current_span("chat") as root:
        root.set_attribute("input.value", question)
        root.set_attribute("llm.model_name", MODEL_NAME)
        trace_id = current_trace_id()

        # --- guard 1: before anything reaches the model --------------------
        try:
            with guard_span("input_guard", backend_name()):
                check_input(question)
        except GuardRejected as rejection:
            root.set_attribute("output.refused_by", rejection.guard)
            await _refuse(rejection, trace_id)
            return

        # --- the model, streamed -------------------------------------------
        reply = cl.Message(content="")
        await reply.send()

        answer_parts: list[str] = []
        try:
            async for piece in stream_answer(question, history[-HISTORY_TURNS * 2 :]):
                answer_parts.append(piece)
                await reply.stream_token(piece)
        except Exception as error:
            root.set_attribute("error.type", type(error).__name__)
            reply.content = (
                f"⚠️ The model call failed: `{type(error).__name__}`. Check that the endpoint is up."
            )
            await reply.update()
            return

        answer = "".join(answer_parts)

        # --- guard 2: before the answer is trusted -------------------------
        #
        # The text is already on screen, because it was streamed. If the output
        # guard refuses, we REPLACE it -- which is the honest trade streaming
        # makes: you show tokens before you have judged them. Buffering the whole
        # answer first would make this cleaner and the app feel slower.
        try:
            with guard_span("output_guard", backend_name()):
                check_output(answer)
        except GuardRejected as rejection:
            root.set_attribute("output.refused_by", rejection.guard)
            reply.content = (
                f"🛑 **Answer withheld by the {rejection.guard}.**\n\n{rejection.reason}"
            )
            await reply.update()
            return

        root.set_attribute("output.value", answer)

        history.extend(
            [{"role": "user", "content": question}, {"role": "assistant", "content": answer}]
        )
        cl.user_session.set("history", history)

    reply.actions = _thumbs(trace_id, question)
    await reply.update()

    if trace_id:
        await cl.Message(
            content=f"`trace {trace_id}` — paste it into Phoenix search",
            author="trace",
        ).send()


async def _refuse(rejection: GuardRejected, trace_id: str | None) -> None:
    """Show the refusal, and say which guard made it.

    Shown rather than hidden: the point is that the system said no, and why.
    """
    body = (
        f"🛑 **Refused by the {rejection.guard}.**\n\n{rejection.reason}\n\n_No model was called._"
    )
    if trace_id:
        body += f"\n\n`trace {trace_id}`"
    await cl.Message(content=body).send()


def _thumbs(trace_id: str | None, question: str) -> list[cl.Action]:
    payload = {"trace_id": trace_id, "question": question}
    return [
        cl.Action(
            name="helpful",
            payload={**payload, "helpful": True},
            label="👍",
            tooltip="This answer was helpful",
        ),
        cl.Action(
            name="not_helpful",
            payload={**payload, "helpful": False},
            label="👎",
            tooltip="This answer was not helpful",
        ),
    ]


@cl.action_callback("helpful")
async def on_helpful(action: cl.Action) -> None:
    await _record(action)


@cl.action_callback("not_helpful")
async def on_not_helpful(action: cl.Action) -> None:
    await _record(action)


async def _record(action: cl.Action) -> None:
    """Write the thumb down next to the trace id that produced it.

    That link is the whole value. Without it you have a satisfaction metric;
    with it you have a question you can replay and score.
    """
    payload = action.payload or {}
    record_feedback(
        trace_id=payload.get("trace_id"),
        helpful=bool(payload.get("helpful")),
        question=payload.get("question", ""),
    )
    verdict = "👍 helpful" if payload.get("helpful") else "👎 not helpful"
    await cl.Message(content=f"Recorded: {verdict}", author="feedback").send()
    await action.remove()
