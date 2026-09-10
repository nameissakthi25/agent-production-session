"""Guards: checks that run around the model, never inside it.

Three run in this app:

    input_guard   before anything reaches the model
    tool_guard    before EVERY tool call -- see tool_guard.py, it is the point
    output_guard  before the answer reaches the user

Position is the design. A check that runs after the model has already acted is
not a guardrail, it is a log entry.

Both raise GuardRejected, both are ordinary functions with ordinary inputs, and
both are therefore testable without a GPU. That is deliberate: most of what gets
called "LLM testing" is really this, and it does not need a model.
"""

from agentbot.guards.base import GuardRejected
from agentbot.guards.input_guard import check_input
from agentbot.guards.output_guard import check_output
from agentbot.guards.tool_guard import TOOL_ALLOWLIST, check_tool_call

__all__ = [
    "GuardRejected",
    "TOOL_ALLOWLIST",
    "check_input",
    "check_output",
    "check_tool_call",
]
