"""Runs before anything reaches the model.

Two backends, same signature, chosen by GUARD_VALIDATOR:

    local   the patterns in patterns.py. No network, no Hub, ~0.1 ms.
    hub     Guardrails AI with DetectPII from the Hub. Finds names and
            addresses a regex cannot, costs 10-100x more, and has to be
            installed separately.

The local backend is the default because it always works. The Hub backend is
worth switching on once you have measured what the local one misses -- and the
honest answer is usually "most of it", because names are not a pattern.
"""

from bot.config import GUARD_VALIDATOR, MAX_INPUT_CHARS
from bot.guards.base import GuardRejected
from bot.guards.patterns import find_injection, find_pii

GUARD_NAME = "input_guard"


def _check_local(message: str) -> None:
    """Length, injection phrases, PII formats. In that order, cheapest first."""
    if len(message) > MAX_INPUT_CHARS:
        raise GuardRejected(
            GUARD_NAME,
            f"input is {len(message)} characters, over the "
            f"{MAX_INPUT_CHARS} character limit",
        )

    phrase = find_injection(message)
    if phrase is not None:
        raise GuardRejected(
            GUARD_NAME, f"input contains the injection phrase {phrase!r}"
        )

    label = find_pii(message)
    if label is not None:
        raise GuardRejected(GUARD_NAME, f"input matched the {label} pattern")


# The Hub guard is built once, lazily, because importing guardrails is slow and
# most runs never need it.
_hub_guard = None


def _build_hub_guard():
    """Guardrails AI with DetectPII. Raises a useful error if it is missing."""
    # Both of these are read at import time by the library, so they have to be
    # set before it is imported. Out of the box Guardrails AI POSTs telemetry
    # to a hardcoded us-east-1 endpoint; if your reason for self-hosting is
    # data residency, that is not a footnote.
    import os

    os.environ.setdefault("GUARDRAILS_DISABLE_TELEMETRY", "true")
    os.environ.setdefault("GUARDRAILS_ENABLE_METRICS", "false")

    try:
        from guardrails import Guard
        from guardrails.hub import DetectPII
    except ImportError as error:
        raise RuntimeError(
            "GUARD_VALIDATOR=hub needs Guardrails AI and the DetectPII "
            "validator:\n"
            "  pip install guardrails-ai\n"
            "  guardrails hub install hub://guardrails/detect_pii"
        ) from error

    return Guard().use(DetectPII(pii_entities="pii", on_fail="exception"))


def _check_hub(message: str) -> None:
    """Length and injection stay local; PII goes to Presidio via the Hub.

    The framework has no opinion about injection phrases or length, so running
    those through it would be ceremony. Only the part it is genuinely better at
    is delegated.
    """
    global _hub_guard

    if len(message) > MAX_INPUT_CHARS:
        raise GuardRejected(
            GUARD_NAME,
            f"input is {len(message)} characters, over the "
            f"{MAX_INPUT_CHARS} character limit",
            backend="hub",
        )

    phrase = find_injection(message)
    if phrase is not None:
        raise GuardRejected(
            GUARD_NAME, f"input contains the injection phrase {phrase!r}", backend="hub"
        )

    if _hub_guard is None:
        _hub_guard = _build_hub_guard()

    try:
        _hub_guard.validate(message)
    except Exception as error:  # the library raises its own exception types
        # The reason is only available as the stringified exception, which is
        # why the hub path costs more on the reject branch than on the pass one.
        reason = str(error).strip().splitlines()[0][:200]
        raise GuardRejected(GUARD_NAME, f"DetectPII refused: {reason}", "hub") from error


def check_input(message: str) -> str:
    """Return the message unchanged, or raise GuardRejected saying why."""
    if GUARD_VALIDATOR == "hub":
        _check_hub(message)
    else:
        _check_local(message)
    return message


def backend_name() -> str:
    """Which backend is live, for the span attribute."""
    return "guardrails-ai/hub" if GUARD_VALIDATOR == "hub" else "local"
