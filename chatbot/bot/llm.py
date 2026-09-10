"""The model call, and nothing else.

Kept apart from the Chainlit handlers so it can be exercised without a UI, and
so the guards wrap something small and obvious.
"""

from collections.abc import AsyncIterator

from openai import AsyncOpenAI

from bot.config import (
    MAX_TOKENS,
    MODEL_NAME,
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    REQUEST_TIMEOUT,
    SYSTEM_PROMPT,
    TEMPERATURE,
)

# One client for the process. The base URL is the only thing that differs
# between a self-hosted vLLM server and a hosted API -- which is the entire
# portability argument for the OpenAI protocol, and the reason self-hosting is
# not a rewrite.
_client = AsyncOpenAI(
    base_url=OPENAI_BASE_URL,
    api_key=OPENAI_API_KEY,
    timeout=REQUEST_TIMEOUT,
)


def build_messages(question: str, history: list[dict] | None = None) -> list[dict]:
    """System prompt, then prior turns, then the new question.

    History is passed in rather than stored here on purpose: this module holds
    no state, so nothing about it changes when you put a real conversation store
    behind the UI.
    """
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(history or [])
    messages.append({"role": "user", "content": question})
    return messages


async def stream_answer(
    question: str, history: list[dict] | None = None
) -> AsyncIterator[str]:
    """Yield the answer one chunk at a time.

    Streaming is not decoration. On a self-hosted card the first token arrives
    quickly and the rest arrive steadily, so a streamed answer feels immediate
    where the same request rendered at the end feels broken.
    """
    stream = await _client.chat.completions.create(
        model=MODEL_NAME,
        messages=build_messages(question, history),
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
    """What the endpoint says it is serving. Used by the startup check."""
    models = await _client.models.list()
    served = models.data[0] if models.data else None
    return {
        "base_url": OPENAI_BASE_URL,
        "configured_model": MODEL_NAME,
        "served_model": getattr(served, "id", None),
    }
