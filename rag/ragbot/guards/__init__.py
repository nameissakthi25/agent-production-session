"""Guards: checks that run around the model, never inside it.

Two run in this app:

    input_guard   before anything reaches the model
    output_guard  before the answer reaches the user

Position is the design. A check that runs after the model has already acted is
not a guardrail, it is a log entry.

Both raise GuardRejected, both are ordinary functions with ordinary inputs, and
both are therefore testable without a GPU. That is deliberate: most of what gets
called "LLM testing" is really this, and it does not need a model.
"""

from ragbot.guards.base import GuardRejected
from ragbot.guards.input_guard import check_input
from ragbot.guards.output_guard import check_output

__all__ = ["GuardRejected", "check_input", "check_output"]
