"""Guardrails AI, with a validator that actually detects things.

This is the real framework: a `Validator` subclass registered with
`@register_validator`, assembled into a `Guard`, raising on failure through
`on_fail="exception"`. What differs from the usual example is where the
detection comes from.

WHY NOT THE HUB
---------------
Guardrails AI ships zero validators. All ~65 live in a Hub fetched from
hub.api.guardrailsai.com, one `guardrails hub install` at a time. That is fine
on a laptop with open egress and useless on a locked-down build agent, an
air-gapped box, or any network where that hostname does not resolve -- which is
exactly the environment a data-residency argument implies.

So this module wraps **Presidio** directly, from PyPI. Presidio is what the
Hub's DetectPII validator wraps too, so the detection is the same engine; only
the delivery mechanism differs, and this one survives having no Hub.

Set GUARD_VALIDATOR=hub instead if you have run `make hub` and would rather use
the Hub's own DetectPII.
"""

import os

# Read at IMPORT time by the library, so they must be set before the import
# below. Out of the box Guardrails AI POSTs OpenTelemetry spans to a hardcoded
# us-east-1 endpoint -- we have watched it retry against that host with the
# network down. For a system whose reason to exist is that data stays put, that
# is not a footnote.
os.environ.setdefault("GUARDRAILS_DISABLE_TELEMETRY", "true")
os.environ.setdefault("GUARDRAILS_ENABLE_METRICS", "false")

from guardrails import Guard  # noqa: E402
from guardrails.classes.validation.validation_result import (  # noqa: E402
    FailResult,
    PassResult,
    ValidationResult,
)
from guardrails.validator_base import Validator, register_validator  # noqa: E402

# Entities worth refusing on. Presidio knows many more; this is the set that
# actually turns up in IT support text. PERSON and LOCATION are the ones a
# regex can never reach, and the reason this module exists.
DEFAULT_ENTITIES = (
    "PERSON",
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "CREDIT_CARD",
    "IBAN_CODE",
    "US_SSN",
    "UK_NHS",
    "LOCATION",
)

# Presidio returns a confidence score per finding. Below this we let it through:
# PERSON in particular fires on capitalised product names if you trust every
# hit, and a guard with a high false-positive rate gets switched off by whoever
# has to live with it.
DEFAULT_THRESHOLD = 0.5

_analyzer = None


def _get_analyzer():
    """Build the Presidio analyzer once. Loading the NLP model is slow."""
    global _analyzer
    if _analyzer is None:
        from presidio_analyzer import AnalyzerEngine
        from presidio_analyzer.nlp_engine import NlpEngineProvider

        provider = NlpEngineProvider(
            nlp_configuration={
                "nlp_engine_name": "spacy",
                # The small model. The large one scores better on PERSON and is
                # ~560MB; measure on your own text before paying for it.
                "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}],
            }
        )
        _analyzer = AnalyzerEngine(nlp_engine=provider.create_engine())
    return _analyzer


@register_validator(name="corplabs/presidio-pii", data_type="string")
class PresidioPII(Validator):
    """Refuse text containing personal data, using NER rather than patterns."""

    def __init__(self, entities=DEFAULT_ENTITIES, threshold=DEFAULT_THRESHOLD, **kwargs):
        super().__init__(**kwargs)
        self.entities = list(entities)
        self.threshold = threshold

    def _validate(self, value: str, metadata: dict) -> ValidationResult:
        results = _get_analyzer().analyze(
            text=value, entities=self.entities, language="en"
        )
        hits = [r for r in results if r.score >= self.threshold]
        if not hits:
            return PassResult()

        # Report the CATEGORY and the score, never the value. A refusal message
        # that quotes the personal data it just refused has leaked it into
        # whatever logs the refusal.
        worst = max(hits, key=lambda r: r.score)
        others = {r.entity_type for r in hits} - {worst.entity_type}
        detail = f"{worst.entity_type} (confidence {worst.score:.2f})"
        if others:
            detail += " and also " + ", ".join(sorted(others))
        return FailResult(error_message=f"input contains personal data: {detail}")


def build_guard(entities=DEFAULT_ENTITIES, threshold=DEFAULT_THRESHOLD) -> Guard:
    """A Guardrails AI Guard wrapping the Presidio validator.

    on_fail belongs on the validator, not on .use() -- putting it on .use() is
    silently ignored and the Guard then returns a failed outcome instead of
    raising, which looks exactly like a guard that never fires.
    """
    return Guard().use(
        PresidioPII(entities=entities, threshold=threshold, on_fail="exception")
    )


def build_hub_guard() -> Guard:
    """The Hub's own DetectPII, if somebody ran `make hub`."""
    try:
        from guardrails.hub import DetectPII
    except ImportError as error:
        raise RuntimeError(
            "GUARD_VALIDATOR=hub needs the Hub validator:\n"
            "  guardrails hub install hub://guardrails/detect_pii\n"
            "This requires network access to hub.api.guardrailsai.com. If that "
            "host is unavailable, use GUARD_VALIDATOR=guardrails instead -- it "
            "wraps the same Presidio engine from PyPI."
        ) from error

    return Guard().use(DetectPII(pii_entities="pii", on_fail="exception"))
