"""Every knob, read from the environment. Imports nothing that talks to a network."""

import os
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent

# --- the corpus ------------------------------------------------------------
CORPUS_DIR = os.environ.get("CORPUS_DIR", str(HERE / "corpus"))
# Committed on purpose: it is the record of which documents produced which
# answers, and a diff on it is a diff on the assistant's knowledge.
MANIFEST_PATH = os.environ.get("MANIFEST_PATH", str(HERE / "corpus.manifest.json"))

# --- retrieval -------------------------------------------------------------
QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6335")
COLLECTION = os.environ.get("QDRANT_COLLECTION", "it-support-agent")

# bge-small-en-v1.5: 384 dimensions, ~130MB, runs on CPU through ONNX Runtime.
# The GPU is for generation. Spending VRAM on a 384-dim embedder would take it
# from the model that actually needs it.
EMBED_MODEL = os.environ.get("EMBED_MODEL", "BAAI/bge-small-en-v1.5")
EMBED_DIM = int(os.environ.get("EMBED_DIM", "384"))

# How many chunks to retrieve, and the score below which a hit is not worth
# putting in the prompt. TOP_K is a prompt-budget decision, not a quality one:
# more context is not more accuracy, and every chunk costs tokens on every turn.
TOP_K = int(os.environ.get("TOP_K", "4"))

# The score below which a hit is not worth putting in the prompt -- and so the
# line between answering and saying "that is not in our documentation".
#
# MEASURED, and 0.30 was wrong: at that floor every out-of-corpus question still
# retrieved four chunks scoring ~0.60, so the "I do not know" branch could never
# fire. On a labelled set of 12 in-corpus and 10 out-of-corpus questions
# (ingest/measure_floor.py, re-runnable):
#
#     floor 0.30   answers 12/12 in-corpus, wrongly answers 10/10 out-of-corpus
#     floor 0.60   answers 12/12,           wrongly answers  4/10
#     floor 0.65   answers 11/12,           wrongly answers  0/10   <- chosen
#     floor 0.70   answers 10/12,           wrongly answers  0/10
#
# 0.65 trades one real question ("my mailbox is full", 0.645) for ten it should
# never have answered. The honest caveat: lowest in-corpus was 0.645 and highest
# out-of-corpus 0.638, a gap of 0.007. A margin that thin will not survive a new
# document, which is the argument for a reranker rather than another decimal.
SCORE_FLOOR = float(os.environ.get("SCORE_FLOOR", "0.65"))

# --- the model -------------------------------------------------------------
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "http://localhost:8000/v1")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "not-used")
MODEL_NAME = os.environ.get("MODEL_NAME", "qwen3")
TEMPERATURE = float(os.environ.get("TEMPERATURE", "0.2"))
MAX_TOKENS = int(os.environ.get("MAX_TOKENS", "700"))
REQUEST_TIMEOUT = float(os.environ.get("REQUEST_TIMEOUT", "300"))

# --- tracing ---------------------------------------------------------------
PHOENIX_COLLECTOR_ENDPOINT = os.environ.get(
    "PHOENIX_COLLECTOR_ENDPOINT", "http://localhost:6008/v1/traces"
)
PHOENIX_PROJECT_NAME = os.environ.get("PHOENIX_PROJECT_NAME", "agent-support-bot")
PHOENIX_PUBLIC_URL = os.environ.get("PHOENIX_PUBLIC_URL", "http://localhost:6008")
TRACING_ENABLED = os.environ.get("TRACING_ENABLED", "true").lower() != "false"

# --- guards ----------------------------------------------------------------
GUARD_VALIDATOR = os.environ.get("GUARD_VALIDATOR", "guardrails")
MAX_INPUT_CHARS = int(os.environ.get("MAX_INPUT_CHARS", "4000"))

FEEDBACK_PATH = os.environ.get("FEEDBACK_PATH", str(HERE / "feedback.jsonl"))
