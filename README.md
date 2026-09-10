# Agent Production Session

Running an open model yourself, and putting the parts around it that turn a demo
into something you could defend: **guardrails**, **tracing**, and a **UI**.

```
agent-production-session/
├── serving-from-scratch.ipynb   ← start here: empty machine → traced, guarded endpoint
└── chatbot/                     ← the same ideas as a running app
```

---

## The notebook

`serving-from-scratch.ipynb` is the ground-up version. Nothing is pre-installed
and nothing hides behind a Makefile — every step is a command you run in order.

| § | |
|---|---|
| 1 | `pip install huggingface_hub`, then list what Qwen actually publishes and work out which checkpoints fit your card |
| 2 | `pip install vllm` and serve one, with the failure mode of every flag |
| 3 | Inference, GPU memory, and latency — measured, including the three-way split between weights, activations and KV cache |
| 4 | Tracing with Phoenix, and how to find one specific run |
| 5 | Guardrails AI — PII and NSFW validators, and what they cost per request |

**It wants its own environment.** vLLM pins `torch`, `transformers` and `openai`
hard, and will move them under anything else you have installed:

```bash
python3 -m venv ~/serving-venv && source ~/serving-venv/bin/activate
pip install ipykernel && python -m ipykernel install --user --name serving
```

Then pick the **serving** kernel. You need an NVIDIA GPU with ~40GB free for the
default model; the notebook names a small alternative for 24GB cards.

---

## The chatbot

A Chainlit app that talks to any OpenAI-compatible endpoint, guards both sides
of the conversation, and traces every turn.

```
input_guard  →  model (streamed)  →  output_guard  →  answer + 👍/👎
```

Each of those is a span under one root, so a refusal is attached to the request
that caused it instead of sitting in a log file you have to correlate by
timestamp.

### Run it

```bash
make install                      # venv + dependencies
cp chatbot/.env.example chatbot/.env
make phoenix                      # trace viewer on :6006  (Docker)
make run                          # the bot on :8001
```

You also need a model. Point `OPENAI_BASE_URL` at whatever you have — a vLLM
server from the notebook, or any other OpenAI-compatible endpoint.

```bash
make test     # 20 tests, no model, no GPU, ~0.01s
make lint
```

### What is where

| Path | |
|---|---|
| `chatbot/app.py` | Chainlit handlers. One turn, start to finish |
| `chatbot/bot/llm.py` | the model call, streamed. No state |
| `chatbot/bot/guards/` | the guards, and the patterns they share |
| `chatbot/bot/observability.py` | tracing setup, guard spans, feedback |
| `chatbot/bot/config.py` | every knob, read from the environment |

---

## Guardrails: two backends

Set `GUARD_VALIDATOR` in `chatbot/.env`.

**`local` (default)** — the regex and phrase rules in `bot/guards/patterns.py`.
No network, no downloads, microseconds. It always works, which is why it is the
default.

**`hub`** — Guardrails AI with `DetectPII` and `NSFWText` from the Hub:

```bash
make hub          # two separate downloads, a few hundred MB
```

Then set `GUARD_VALIDATOR=hub`.

### Which one to use

The honest comparison is not "framework good, regex bad". A regex is near
perfect on a fixed format — an email, a card number — and structurally blind to
anything without a format. *"I am Jane Doe from the Manchester office"* has no
pattern in it at all, and only named-entity recognition will catch it. **That**
is what the Hub validator buys, and it is worth measuring on your own data
before deciding it is worth the dependency.

Three things to know before you switch it on:

- **Guardrails AI ships zero validators.** All of them live in a Hub fetched over
  the network, one install at a time. Air-gapped, you get an empty framework.
- **It phones home by default**, posting telemetry to a hardcoded us-east-1
  endpoint. If your reason for self-hosting is data residency, that is not a
  footnote — `bot/guards/input_guard.py` disables it *before* the import, since
  both switches are read at import time.
- **It declares `openai<3.0.0`.** This repo pins `openai==2.54.0` so a single
  requirements file resolves cleanly. Pinning openai 3.x instead means pip
  refuses the pair, or a loose venv silently downgrades one of them.

Neither backend is a security boundary. Published defences against prompt
injection have been broken more than 90% of the time once attackers adapted.
Treat guards as a filter that removes the obvious.

---

## Tracing

Phoenix runs as a **service**, not a library:

```bash
make phoenix          # docker compose up -d phoenix
```

Every answer prints a `trace_id`. Open http://localhost:6006, choose the
**chainlit-support-bot** project, paste the id into the search box.

Open the model-call span and look at the attributes: the prompt **actually
sent**, which is always bigger than people expect once the system prompt and
history are counted, plus token counts and latency. Guard spans carry
`guardrail.passed`, `guardrail.reason` and `guardrail.backend`.

> **Why Phoenix is not in `requirements.txt`.** The Phoenix *server*
> (`arize-phoenix`) needs `mcp>=2.0.0`; Chainlit needs `mcp<2.0.0`. pip cannot
> satisfy both, and the resolver error does not name the real cause. The app only
> needs `arize-phoenix-otel`, the exporter, which has no MCP dependency. Running
> the backend as a container is the better shape anyway.

> **Spans export as they end, and the root span ends last.** Query a trace too
> fast and you get children with no root, which renders as an empty tree and
> looks exactly like broken tracing. Wait a second.

---

## Feedback

👍/👎 under each answer appends a line to `feedback.jsonl` with the **trace id**
of the answer being judged.

That link is the whole value. A thumbs-down in its own table is a satisfaction
metric. A thumbs-down carrying a trace id is a reproducible bug report: you have
the question, the prompt, and the answer, so you can replay it and score it.

---

## What is deliberately not built

Saying so is the difference between a demo and a system you would put in front
of your own users.

| | |
|---|---|
| Authentication | none. Anyone who can reach the port can chat |
| Conversation persistence | a list in the session. Restart the process and it is gone |
| Rate limiting | none |
| Retrieval | none. The model answers from what it knows |
| Eval harness | `feedback.jsonl` is the raw material, not the scorer |

---

## Verified

On 2026-09-10, on macOS with Python 3.11:

- `pip install -r requirements-dev.txt` resolves with no conflicts
- 20/20 tests pass in ~0.01s
- Chainlit serves `text/html` on its port within ~4s of starting
- `cl.Action(name=, payload=, label=, tooltip=)` constructs on chainlit 2.12.0
- guard refusals reach Phoenix as spans carrying `guardrail.passed`,
  `guardrail.reason` and `guardrail.backend`

**Not yet verified:** the model call itself and the streaming path, which need a
running endpoint.
