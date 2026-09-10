"""The model call, given retrieved context.

The prompt is the whole difference between this and the chatbot next door: it
says answer from these documents, cite them, and say when they do not cover it.
That last instruction is the one that stops a RAG system from being a
confident-sounding chatbot with extra steps.
"""

from collections.abc import AsyncIterator

from openai import AsyncOpenAI

from ragbot.config import (
    MAX_TOKENS,
    MODEL_NAME,
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    REQUEST_TIMEOUT,
    TEMPERATURE,
)
from ragbot.retrieval import Hit, build_context

SYSTEM_PROMPT = """You are an internal IT support assistant for CorpLabs.

Answer ONLY from the numbered sources provided below. Rules, in order:

1. If the sources answer the question, answer it concretely -- give the actual
   URL, the actual steps, the actual policy limits.
2. Cite the sources you used as [1], [2] and so on, inline, next to the claim
   they support.
3. If the sources do not cover the question, say so plainly and stop. Do not
   fill the gap from general knowledge -- a plausible invented portal is worse
   than "that is not in our documentation", because the user cannot tell.
4. Never invent a URL, a phone number, an email address or a person's name."""

NO_CONTEXT_REPLY = (
    "I could not find anything in the IT documentation about that.\n\n"
    "That is the honest answer rather than a guess: nothing in the corpus "
    "scored above the retrieval threshold, so I have no sources to work from."
)

_client = AsyncOpenAI(
    base_url=OPENAI_BASE_URL, api_key=OPENAI_API_KEY, timeout=REQUEST_TIMEOUT
)


def build_messages(question: str, hits: list[Hit]) -> list[dict]:
    context = build_context(hits)
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"Sources:\n\n{context}\n\n---\n\nQuestion: {question}",
        },
    ]


async def stream_answer(question: str, hits: list[Hit]) -> AsyncIterator[str]:
    stream = await _client.chat.completions.create(
        model=MODEL_NAME,
        messages=build_messages(question, hits),
        temperature=TEMPERATURE,
        max_tokens=MAX_TOKENS,
        stream=True,
    )
    async for chunk in stream:
        if not chunk.choices:
            continue
        piece = chunk.choices[0].delta.content
        if piece:
            yield piece


async def health() -> dict:
    models = await _client.models.list()
    served = models.data[0] if models.data else None
    return {
        "base_url": OPENAI_BASE_URL,
        "configured_model": MODEL_NAME,
        "served_model": getattr(served, "id", None),
    }
