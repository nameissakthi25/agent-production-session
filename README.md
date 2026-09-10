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

### Run it with Docker

Two containers: the bot, and Phoenix for the traces.

```bash
cd chatbot
cp .env.example .env          # then edit OPENAI_BASE_URL to point at your model
docker compose up -d --build
```

| | |
|---|---|
| Chatbot | http://localhost:8001 |
| Traces | http://localhost:6006 |

The image installs from `uv.lock`, so what runs in the container is the same
resolution that ran on your machine. Measured on a first build: **55s**, **2.17GB**,
and the app answers `200 text/html` about **3 seconds** after the container starts.

**You still need a model.** `OPENAI_BASE_URL` must point at any
OpenAI-compatible endpoint — the vLLM server from the notebook, or anything else
that speaks the protocol. In `compose.yaml` it defaults to
`http://host.docker.internal:8000/v1`, which is how a container reaches a server
running on the host.

> **Two Phoenix URLs, and they are not the same.** Inside the compose network
> Phoenix is `http://phoenix:6006`; from your browser it is
> `http://localhost:6006`. `PHOENIX_COLLECTOR_ENDPOINT` wants the first,
> `PHOENIX_PUBLIC_URL` the second. Setting both to the same value is the most
> common tracing-in-Docker mistake — one half silently stops working.

Useful afterwards:

```bash
docker compose logs -f bot      # what the app is doing
docker compose down             # stop both
docker compose down -v          # ...and discard the traces
```

### Or run it on the host

```bash
make install                  # uv sync --frozen for both projects
cp chatbot/.env.example chatbot/.env
make phoenix                  # trace viewer on :6006 (Docker)
make run                      # the bot on :8001, with hot reload
```

```bash
make test     # 26 tests, no model, no GPU
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

## Guardrails: Guardrails AI, and what it actually buys

The default is **`GUARD_VALIDATOR=guardrails`** — a real Guardrails AI `Guard`,
built from a `Validator` subclass registered with `@register_validator` and
raising through `on_fail="exception"`. Not a wrapper around string matching:
the detection is **Presidio**, the same NER engine the Hub's `DetectPII`
validator wraps.

| Mode | What runs | Cost |
|---|---|---|
| `local` | regex + phrase list only | ~0.01 ms |
| **`guardrails`** *(default)* | the above **plus** a Guardrails AI Guard over Presidio | ~5 ms |
| `hub` | the same, using the Hub's own `DetectPII` | ~5 ms + a download |

### Why both, and not just the framework

Measured on the seven-case set in the tests:

| Input | regex | Presidio |
|---|---|---|
| `my email is jane.doe@corplabs.com` | REFUSED | REFUSED |
| `call me on +44 7700 900123` | **REFUSED** | passed ← missed |
| `I am Jane Doe from the Manchester office` | passed ← missed | **REFUSED** |
| `please give Priya Raman admin rights` | passed ← missed | **REFUSED** |

**Neither dominates.** A regex is near perfect on a fixed format and
structurally blind to a name in a sentence. NER is the other way round on a
phone number written with a country code. Replacing the regex *with* the
framework would have been a downgrade on row two — so `guardrails` runs both and
takes the union, for about 5 ms.

Three tests pin this, including one asserting the row the framework loses. If a
future Presidio starts catching that phone number, delete the test and update
the table — do not quietly weaken the claim and leave the table lying.

### Why Presidio from PyPI instead of the Hub

**Guardrails AI ships zero validators.** All ~65 live in a Hub fetched from
`hub.api.guardrailsai.com`, one `guardrails hub install` at a time. On a build
agent where that host does not resolve, you get a framework with nothing in it —
which is precisely the network a data-residency argument implies.

Taking Presidio straight from PyPI gives the same engine with no Hub, no token
and no egress. `make hub` and `GUARD_VALIDATOR=hub` remain available if you
prefer the Hub's own validator.

### It phones home, and the documented switches do not stop it

`guardrails/utils/hub_telemetry_utils.py:70` hardcodes an OpenTelemetry
endpoint at `hty0gc1ok3.execute-api.us-east-1.amazonaws.com`. Verified on
2026-09-10 with guardrails-ai 0.11.0: it still attempts that POST with **all**
of these set —

- `GUARDRAILS_DISABLE_TELEMETRY=true`
- `GUARDRAILS_ENABLE_METRICS=false`
- `guardrails.settings.disable_tracing = True`
- `TRACELOOP_BASE_URL`, `TRACELOOP_TELEMETRY=false`

Nothing left the machine here only because DNS for that host failed. On an open
network it would have gone. This repo sets those switches anyway, but **the only
control that actually works is egress policy** — so if the reason you self-host
is that data stays put, treat this as a firewall rule, not a config flag.

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
- 26/26 tests pass from the lockfile (the spaCy model load is most of the time)
- `docker compose up --build` builds in 55s to a 2.17GB image, and the app
  answers `200 text/html` ~3s after the container starts
- the Presidio guard refuses `I am Jane Doe from the Manchester office` **inside
  the container**, not just on a laptop
- the Guardrails AI + Presidio guard refuses two names the regex misses, and
  lets through one phone number the regex catches
- Chainlit serves `text/html` on its port within ~4s of starting
- `cl.Action(name=, payload=, label=, tooltip=)` constructs on chainlit 2.12.0
- guard refusals reach Phoenix as spans carrying `guardrail.passed`,
  `guardrail.reason` and `guardrail.backend`

**Not yet verified:** the model call itself and the streaming path, which need a
running endpoint.
