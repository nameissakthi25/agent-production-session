"""Every knob in one place, read from the environment.

Nothing here talks to a network. Importing this module is safe in a test.
"""

import os

# --- the model -------------------------------------------------------------
#
# Any OpenAI-compatible endpoint. A self-hosted vLLM server and api.openai.com
# differ by this one string, which is the entire portability argument for the
# OpenAI protocol.
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "http://localhost:8000/v1")
# vLLM ignores the key but the client insists on one being present.
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "not-used")
MODEL_NAME = os.environ.get("MODEL_NAME", "Qwen/Qwen3.8-27B-FP8")

TEMPERATURE = float(os.environ.get("TEMPERATURE", "0.2"))
MAX_TOKENS = int(os.environ.get("MAX_TOKENS", "600"))
REQUEST_TIMEOUT = float(os.environ.get("REQUEST_TIMEOUT", "300"))

SYSTEM_PROMPT = os.environ.get(
    "SYSTEM_PROMPT",
    "You are an internal IT support assistant. Answer from what you know about "
    "corporate IT: accounts, VPN, devices, access requests and common "
    "incidents. Be concrete and brief. If you do not know, say so plainly "
    "rather than inventing a portal, a URL or a policy.",
)

# --- tracing ---------------------------------------------------------------
PHOENIX_COLLECTOR_ENDPOINT = os.environ.get(
    "PHOENIX_COLLECTOR_ENDPOINT", "http://localhost:6006/v1/traces"
)
PHOENIX_PROJECT_NAME = os.environ.get("PHOENIX_PROJECT_NAME", "chainlit-support-bot")
# Phoenix as the viewer's BROWSER reaches it, for the clickable link under each
# answer -- not as this process reaches it. In Docker those differ.
PHOENIX_PUBLIC_URL = os.environ.get("PHOENIX_PUBLIC_URL", "http://localhost:6006")
TRACING_ENABLED = os.environ.get("TRACING_ENABLED", "true").lower() != "false"

# --- guards ----------------------------------------------------------------
#
# "local"       regex and a phrase list, defined in this repo. Microseconds,
#               no network, always works. Blind to anything without a format.
# "guardrails"  Guardrails AI Guard wrapping Presidio (NER). Catches names and
#               places a regex cannot. Installs from PyPI, needs no Hub.
# "hub"         Guardrails AI with the Hub's own DetectPII. Same engine as
#               "guardrails", but fetched from hub.api.guardrailsai.com.
#
# They are COMPLEMENTARY, not a ladder -- measured, the regex catches phone
# numbers Presidio misses, and Presidio catches names the regex cannot see.
# "guardrails" runs both, which is why it is the default.
GUARD_VALIDATOR = os.environ.get("GUARD_VALIDATOR", "guardrails")
MAX_INPUT_CHARS = int(os.environ.get("MAX_INPUT_CHARS", "4000"))

# Where thumbs go. One JSON object per line, appended.
FEEDBACK_PATH = os.environ.get("FEEDBACK_PATH", "feedback.jsonl")
