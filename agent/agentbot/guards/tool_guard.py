"""Runs before every single tool call. This is the centrepiece of this folder.

The other two guards check text. This one checks an *action*, and that is a
different kind of thing: text you got wrong is an embarrassment, a tool call you
got wrong is a write to a system that trusted you.

Two checks, in this order:

1. **Is this agent allowed to call this tool at all?**  An allowlist. Not a
   model, not a prompt instruction, not a judgment call -- a dictionary.
2. **Are the arguments valid?**  The Pydantic model for that tool. The arguments
   were written by a language model, so "the format is documented" is not a
   constraint.

A refusal does NOT end the run. The tool is not called, the refusal is returned
to the agent as the tool's result, and the agent carries on and explains itself.
That is deliberate: a system that crashes on a malformed tool call is worse at
its job than one that says "I could not look that up, and here is why".
"""

from __future__ import annotations

import json

from pydantic import ValidationError

from agentbot.guards.base import GuardRejected
from agentbot.tools import REGISTRY

GUARD_NAME = "tool_guard"

# Which agent may call which tool. Three lines, and the most copyable thing in
# this repo.
#
# `synthesizer` has NO TOOLS. It writes prose from what the other workers found,
# so any tool call it attempts is by definition a bug or an attack -- and either
# way the answer is the same: refuse it. An empty list is a decision, not an
# oversight.
TOOL_ALLOWLIST: dict[str, list[str]] = {
    "retriever": ["search_kb"],
    "tool_agent": ["lookup_ticket", "check_service_status"],
    "synthesizer": [],
}


def check_tool_call(agent: str, tool: str, raw_arguments: str | dict) -> dict:
    """Return validated arguments, or raise GuardRejected saying why.

    `agent` is which worker is asking. Passing it in rather than inferring it is
    the whole mechanism: the guard cannot be talked into forgetting who called.
    """
    allowed = TOOL_ALLOWLIST.get(agent)
    if allowed is None:
        raise GuardRejected(
            GUARD_NAME, f"unknown agent {agent!r} -- not in the allowlist", "allowlist"
        )

    if tool not in allowed:
        # Say what it may call. A refusal the caller cannot act on is just a
        # dead end, and the agent's next turn is better for knowing.
        permitted = ", ".join(allowed) if allowed else "no tools at all"
        raise GuardRejected(
            GUARD_NAME,
            f"agent {agent!r} may not call {tool!r}; it is allowed {permitted}",
            "allowlist",
        )

    if tool not in REGISTRY:
        raise GuardRejected(GUARD_NAME, f"tool {tool!r} does not exist", "registry")

    _, model, _ = REGISTRY[tool]

    if isinstance(raw_arguments, str):
        try:
            parsed = json.loads(raw_arguments or "{}")
        except json.JSONDecodeError as error:
            raise GuardRejected(
                GUARD_NAME,
                f"arguments for {tool!r} are not valid JSON: {error}",
                "schema",
            ) from error
    else:
        parsed = raw_arguments

    try:
        return model(**parsed).model_dump()
    except ValidationError as error:
        # One line, naming the field. The full Pydantic report is accurate and
        # unreadable, and this string goes back to the model as a tool result --
        # it has to be something the next turn can act on.
        first = error.errors()[0]
        field = ".".join(str(p) for p in first["loc"]) or "arguments"
        raise GuardRejected(
            GUARD_NAME,
            f"argument {field} is invalid: {first['msg']}",
            "schema",
        ) from error
