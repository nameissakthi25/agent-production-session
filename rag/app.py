"""The RAG chatbot.

One turn:

    input_guard  ->  retrieve  ->  model (streamed)  ->  output_guard  ->  answer

Each is a span under one root. The retrieval span carries what came back and
what it scored, which is the difference between "the answer was wrong" and "the
answer was wrong because the right document scored 0.28 and the floor is 0.30".
"""

import chainlit as cl

from ragbot.config import (
    GUARD_VALIDATOR,
    MODEL_NAME,
    PHOENIX_PUBLIC_URL,
    SCORE_FLOOR,
    TOP_K,
    TRACING_ENABLED,
)
from ragbot.guards import GuardRejected, check_input, check_output
from ragbot.guards.input_guard import backend_name
from ragbot.llm import NO_CONTEXT_REPLY, health, stream_answer
from ragbot.observability import (
    current_trace_id,
    guard_span,
    record_feedback,
    setup_tracing,
    tracer,
)
from ragbot.retrieval import collection_status, search


@cl.on_chat_start
async def on_chat_start() -> None:
    setup_tracing()

    status = collection_status()
    lines = ["**IT support assistant, with retrieval**", ""]

    if status["ok"]:
        lines.append(f"- corpus: **{status['points']} chunks** in "
                     f"`{status['collection']}`")
    else:
        lines.append(f"- ⚠️ Qdrant unreachable: `{status['error']}`")
        lines.append("  Run `docker compose up -d qdrant` then "
                     "`uv run python -m ingest.build_index`.")

    lines.append(f"- retrieval: top {TOP_K}, score floor {SCORE_FLOOR}")
    lines.append(f"- guards: `{backend_name()}` ({GUARD_VALIDATOR})")

    try:
        state = await health()
        lines.append(f"- model: `{state['served_model']}` at `{state['base_url']}`")
    except Exception as error:
        lines.append(f"- ⚠️ model endpoint unreachable: `{type(error).__name__}`")

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

        # --- guard 1 -------------------------------------------------------
        try:
            with guard_span("input_guard", backend_name()):
                check_input(question)
        except GuardRejected as rejection:
            root.set_attribute("output.refused_by", rejection.guard)
            await cl.Message(content=(
                f"🛑 **Refused by the {rejection.guard}.**\n\n{rejection.reason}\n\n"
                f"_Nothing was retrieved and no model was called._"
                + (f"\n\n`trace {trace_id}`" if trace_id else "")
            )).send()
            return

        # --- retrieve ------------------------------------------------------
        with tracer().start_as_current_span("retrieve") as span:
            hits, stats = search(question)
            for key, value in stats.items():
                span.set_attribute(key, value)
            span.set_attribute("retrieval.citations",
                               ", ".join(h.citation for h in hits) or "none")

        if not hits:
            # Nothing cleared the floor. Say so instead of asking the model to
            # improvise -- this is the branch that separates RAG from a chatbot.
            root.set_attribute("output.value", "no_context")
            await cl.Message(content=NO_CONTEXT_REPLY).send()
            await _show_sources([], stats)
            return

        # --- the model, streamed -------------------------------------------
        reply = cl.Message(content="")
        await reply.send()

        parts: list[str] = []
        try:
            async for piece in stream_answer(question, hits):
                parts.append(piece)
                await reply.stream_token(piece)
        except Exception as error:
            root.set_attribute("error.type", type(error).__name__)
            reply.content = (f"⚠️ The model call failed: `{type(error).__name__}`. "
                             "Check the endpoint is up.")
            await reply.update()
            return

        answer = "".join(parts)

        # --- guard 2 -------------------------------------------------------
        #
        # The text is already on screen because it was streamed, so a refusal
        # here REPLACES it. That is the honest cost of streaming: you show
        # tokens before you have judged them.
        try:
            with guard_span("output_guard", backend_name()):
                check_output(answer)
        except GuardRejected as rejection:
            root.set_attribute("output.refused_by", rejection.guard)
            reply.content = (f"🛑 **Answer withheld by the {rejection.guard}.**\n\n"
                             f"{rejection.reason}")
            await reply.update()
            return

        root.set_attribute("output.value", answer)

    reply.actions = _thumbs(trace_id, question, hits)
    await reply.update()
    await _show_sources(hits, stats)


async def _show_sources(hits, stats) -> None:
    """What was retrieved, with scores, under every answer.

    Shown always, not on demand. A RAG answer whose sources are hidden asks the
    reader to trust the retrieval, and the retrieval is the part most likely to
    be wrong.
    """
    if hits:
        rows = "\n".join(
            f"| {n} | `{h.citation}` | {h.score:.3f} |"
            for n, h in enumerate(hits, 1)
        )
        body = (
            f"**Sources** — corpus `{stats['retrieval.corpus_version']}`, "
            f"{stats['retrieval.candidates']} candidates, "
            f"{stats['retrieval.kept']} above the {stats['retrieval.floor']} floor\n\n"
            f"| # | source | score |\n|---|---|---|\n{rows}\n\n"
            f"_embed {stats['retrieval.embed_ms']} ms · "
            f"search {stats['retrieval.search_ms']} ms_"
        )
    else:
        body = (
            f"**No sources** — {stats['retrieval.candidates']} candidates, "
            f"best score {stats['retrieval.top_score']}, "
            f"floor {stats['retrieval.floor']}.\n\n"
            f"_The nearest chunk was not near enough. Lowering the floor would "
            f"produce an answer; it would not produce a better one._"
        )
    await cl.Message(content=body, author="retrieval").send()


def _thumbs(trace_id, question, hits) -> list[cl.Action]:
    payload = {
        "trace_id": trace_id,
        "question": question,
        # The citations go with the thumb. A thumbs-down on a RAG answer is
        # ambiguous on its own: bad retrieval, or bad generation from good
        # retrieval? Recording what was retrieved is what makes it answerable.
        "citations": [h.citation for h in hits],
        "top_score": round(hits[0].score, 4) if hits else 0.0,
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
            "citations": payload.get("citations", []),
            "top_score": payload.get("top_score"),
        },
    )
    await cl.Message(
        content=("Recorded: 👍 helpful" if helpful else
                 "Recorded: 👎 not helpful — the retrieved sources were saved "
                 "with it, so this is replayable as an eval case."),
        author="feedback",
    ).send()
    await action.remove()
