"""Runs before anything reaches the model.

Three backends, one signature, chosen by GUARD_VALIDATOR:

    local        the patterns in patterns.py. ~0.01 ms, no network.
    guardrails   a Guardrails AI Guard wrapping Presidio, PLUS the patterns.
                 ~5 ms. The default.
    hub          the same, but with the Hub's own DetectPII validator.

MEASURED, and the reason "guardrails" runs both rather than replacing one with
the other:

    input                                     regex     presidio
    my email is jane.doe@corplabs.com         REFUSED   REFUSED
    call me on +44 7700 900123                REFUSED   pass      <-- missed
    I am Jane Doe from the Manchester office  pass      REFUSED   <-- missed
    please give Priya Raman admin rights      pass      REFUSED   <-- missed

Neither dominates. A regex is near perfect on a fixed format and structurally
blind to a name; NER is the other way round on a phone number written with a
country code. Running both costs about 5 ms and catches the union -- and
"we replaced our regex with a framework" would have been a downgrade on row two.
"""

from agentbot.config import GUARD_VALIDATOR, MAX_INPUT_CHARS
from agentbot.guards.base import GuardRejected
from agentbot.guards.patterns import find_injection, find_pii

GUARD_NAME = "input_guard"

# Built once, lazily. Loading the NLP model takes a second or two, and a run
# that never uses this backend should not pay for it.
_framework_guard = None


def _check_cheap(message: str, backend: str) -> None:
    """Length, injection phrases, PII formats. Cheapest first, always run.

    The framework has no opinion about length or injection phrases, so routing
    those through it would be ceremony. These stay here whichever backend is
    selected.
    """
    if len(message) > MAX_INPUT_CHARS:
        raise GuardRejected(
            GUARD_NAME,
            f"input is {len(message)} characters, over the {MAX_INPUT_CHARS} character limit",
            backend,
        )

    phrase = find_injection(message)
    if phrase is not None:
        raise GuardRejected(GUARD_NAME, f"input contains the injection phrase {phrase!r}", backend)

    label = find_pii(message)
    if label is not None:
        raise GuardRejected(GUARD_NAME, f"input matched the {label} pattern", backend)


def _check_framework(message: str, backend: str) -> None:
    """Then the Guard, for the categories a pattern cannot express."""
    global _framework_guard

    if _framework_guard is None:
        from agentbot.guards import framework

        _framework_guard = (
            framework.build_hub_guard() if GUARD_VALIDATOR == "hub" else framework.build_guard()
        )

    try:
        _framework_guard.validate(message)
    except Exception as error:
        # The reason is only recoverable as the stringified exception, which is
        # why the reject path costs more than the pass path in this library.
        reason = str(error).strip().splitlines()[-1][:200]
        raise GuardRejected(GUARD_NAME, reason, backend) from error


def check_input(message: str) -> str:
    """Return the message unchanged, or raise GuardRejected saying why."""
    backend = backend_name()
    _check_cheap(message, backend)
    if GUARD_VALIDATOR in {"guardrails", "hub"}:
        _check_framework(message, backend)
    return message


def backend_name() -> str:
    """Which backend is live, for the span attribute."""
    return {
        "guardrails": "guardrails-ai/presidio",
        "hub": "guardrails-ai/hub",
    }.get(GUARD_VALIDATOR, "local")
