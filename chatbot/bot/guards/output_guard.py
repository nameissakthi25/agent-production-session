"""Runs before the answer reaches the user.

Cheapest checks first, and the expensive one is optional:

    1. the answer is not empty                       free
    2. it does not leak the system prompt            microseconds
    3. it does not contain PII the model invented    microseconds
    4. NSFWText, if the Hub validator is installed   ~10-100 ms

Step 4 is a small classifier, not a rule, so it costs real time on every single
answer -- including the overwhelming majority it never blocks. Measure it before
you decide it is free.
"""

from bot.config import GUARD_VALIDATOR
from bot.guards.base import GuardRejected
from bot.guards.patterns import find_pii

GUARD_NAME = "output_guard"

# Fragments that only appear if the model is reciting its own instructions
# back. Checked lowercased.
LEAK_MARKERS: tuple[str, ...] = (
    "you are an internal it support assistant",
    "my system prompt",
    "my instructions are",
    "as an ai language model, my instructions",
)

_nsfw_guard = None


def _build_nsfw_guard():
    import os

    os.environ.setdefault("GUARDRAILS_DISABLE_TELEMETRY", "true")
    os.environ.setdefault("GUARDRAILS_ENABLE_METRICS", "false")

    try:
        from guardrails import Guard
        from guardrails.hub import NSFWText
    except ImportError as error:
        raise RuntimeError(
            "GUARD_VALIDATOR=hub needs Guardrails AI and the NSFWText "
            "validator:\n"
            "  pip install guardrails-ai\n"
            "  guardrails hub install hub://guardrails/nsfw_text"
        ) from error

    return Guard().use(NSFWText(threshold=0.8, validation_method="sentence", on_fail="exception"))


def check_output(answer: str) -> str:
    """Return the answer unchanged, or raise GuardRejected saying why."""
    global _nsfw_guard

    backend = "guardrails-ai/hub" if GUARD_VALIDATOR == "hub" else "local"

    if not answer or not answer.strip():
        raise GuardRejected(GUARD_NAME, "the model returned an empty answer", backend)

    lowered = answer.lower()
    for marker in LEAK_MARKERS:
        if marker in lowered:
            raise GuardRejected(
                GUARD_NAME, "the answer appears to quote its own instructions", backend
            )

    # An assistant that invents a plausible-looking email address or phone
    # number is worse than one that says it does not know.
    label = find_pii(answer)
    if label is not None:
        raise GuardRejected(
            GUARD_NAME, f"the answer contains something matching a {label}", backend
        )

    if GUARD_VALIDATOR == "hub":
        if _nsfw_guard is None:
            _nsfw_guard = _build_nsfw_guard()
        try:
            _nsfw_guard.validate(answer)
        except Exception as error:
            reason = str(error).strip().splitlines()[0][:200]
            raise GuardRejected(GUARD_NAME, f"NSFWText refused: {reason}", backend) from error

    return answer
