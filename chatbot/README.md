# Chatbot: guards and tracing, no documents

An open model behind a chat UI, with the two things usually left out — a guard on
each side of the conversation, and a trace for every answer.

**It has no corpus.** Ask it how to reset your password and it will invent a
plausible "Forgot Password" flow, confidently, with no citation. That is not a
bug to fix here; it is the reason [`../rag/`](../rag/) exists, and the contrast
between the two answers is the whole argument for retrieval.

One turn:

```
input_guard  →  model (streamed)  →  output_guard  →  answer + 👍/👎
```

Each is a span under one root, so a refusal is attached to the request that
caused it rather than sitting in a log file you have to correlate by timestamp.

---

## Run it with Docker

```bash
cp .env.example .env          # then point OPENAI_BASE_URL at your model
docker compose up -d --build
```

| Service | Port | |
|---|---|---|
| `bot` | 8001 | Chainlit |
| `phoenix` | 6006 | traces → project `chainlit-support-bot` |

| | |
|---|---|
| Chatbot | http://localhost:8001 |
| Traces | http://localhost:6006 |

Measured on a first build: **55s**, a **2.17GB** image, and `200 text/html`
about **3 seconds** after the container starts.

**You still need a model.** `OPENAI_BASE_URL` must point at any
OpenAI-compatible endpoint — the vLLM server from
[`../serving-from-scratch.ipynb`](../serving-from-scratch.ipynb), or anything
else that speaks the protocol. `compose.yaml` defaults to
`http://host.docker.internal:8000/v1`, which is how a container reaches a server
on the host.

> **Two Phoenix URLs, and they are not the same.** Inside the compose network
> Phoenix is `http://phoenix:6006`; from your browser it is
> `http://localhost:6006`. `PHOENIX_COLLECTOR_ENDPOINT` wants the first,
> `PHOENIX_PUBLIC_URL` the second. Setting both to the same value is the most
> common tracing-in-Docker mistake — one half silently stops working.

Afterwards:

```bash
docker compose logs -f bot
docker compose down          # stop
docker compose down -v       # ...and discard the traces
```

## Or run it on the host

```bash
docker compose up -d phoenix     # the bit you do not want to install
uv sync --frozen --group dev
uv run chainlit run app.py --port 8001 -w
```

```bash
uv run pytest tests/ -q     # 26 tests, no model, no GPU
uv run ruff check .
```

> **Running this alongside [`../rag/`](../rag/)?** Both are separate compose
> projects and can run at the same time — this one owns host ports **8001** and
> **6006**, `rag/` owns **8002**, **6007** and **6333**. They were both on 6006
> at first, which fails on the port bind the moment you try to bring up the
> second one.

## What is where

| Path | |
|---|---|
| `app.py` | Chainlit handlers. One turn, start to finish |
| `bot/llm.py` | the model call, streamed. Holds no state |
| `bot/guards/` | the guards, and the patterns they share |
| `bot/observability.py` | tracing setup, guard spans, feedback |
| `bot/config.py` | every knob, read from the environment |

---

## CI — and how to set it up

`.github/workflows/chatbot.yml`. Two jobs, and **no secrets to configure**:

| Job | Time | What it gates |
|---|---|---|
| `test` | ~38s | `uv sync --frozen`, `ruff check`, `ruff format --check`, 26 tests |
| `build` | ~3m | build from `uv.lock` → push to GHCR tagged with the commit SHA **and** `:main` → smoke-test the image |

### Setting it up on your own fork

1. **Copy the workflow file.** That is genuinely the whole setup.
2. Check **Settings → Actions → General → Workflow permissions** allows
   *"Read and write"*. The job asks for `packages: write`, and a job can only
   request up to the repository's maximum.
3. Push something under `chatbot/`.

**There is nothing else.** `gh secret list` on this repo returns empty. The only
credential used is `${{ secrets.GITHUB_TOKEN }}`, which GitHub mints per run,
scopes to this repository, and expires when the job ends — you do not create it
and you cannot leak it from a fork's pull request, because a fork's token is
read-only.

That is worth contrasting with the `deploy` job in the sibling
`agent-deploy-session` repo, which needs a long-lived SSH private key in
`GPU_SSH_KEY` plus a host and a pinned host key. **A pipeline that only builds
needs no standing credentials; a pipeline that deploys needs one that outlives
the run.** That difference is most of the security conversation about CD.

### The path filter is part of the design

```yaml
paths:
  - "chatbot/**"
  - ".github/workflows/chatbot.yml"
```

A change to `rag/`, `agent/` or the notebook runs nothing. `rag/` and `agent/`
share most of this code and all of its guards, so a second and third copy of
this workflow would cost minutes per push and catch almost nothing new. Copy the
file when they diverge enough to be worth gating.

### What is deliberately absent

**No `deploy` job and no `eval` job.** Both need a host on the other end. This
repo has no fixed one — the GPU's IP changes on every resume — and a deploy step
pointed at a stale IP goes red for reasons unrelated to your commit, after which
people learn to ignore the pipeline.

So this is **CI, not CD**. It ends at a tagged image in a registry; putting that
image on a server is a separate decision. `agent-deploy-session` has the four-job
version — `test → build → deploy → eval`, where `deploy` SSHes to the box,
pulls the SHA tag and restarts one service, measured at **3m40s** from push to a
scored deploy.

### Two details that earn their place

**`uv sync --frozen`**, not a plain `uv sync`: fail if `uv.lock` does not already
satisfy `pyproject.toml`, rather than quietly resolving something new. CI
resolving its own dependencies is CI testing a different program.

**The last step smoke-tests the image, not the code.** It imports the guards
*inside* the container and asserts the Presidio backend actually refuses a name.
"It built" is not "it works" — this repo shipped a `compose.yaml` that pinned
`GUARD_VALIDATOR=local` while this README described Presidio, and a build-only
gate would have passed it.

```bash
gh run list --limit 5
gh run watch --exit-status
gh run view <id> --log-failed

# the artefact, without pulling 2GB (and it is linux/amd64 only)
docker buildx imagetools inspect \
  ghcr.io/<you>/agent-production-session/chatbot:main
```

---

## Guardrails

The default is **`GUARD_VALIDATOR=guardrails`** — a real Guardrails AI `Guard`,
built from a `Validator` subclass registered with `@register_validator` and
raising through `on_fail="exception"`. Not a wrapper around string matching: the
detection is **Presidio**, the same NER engine the Hub's `DetectPII` validator
wraps.

| Mode | What runs | Cost |
|---|---|---|
| `local` | regex + phrase list only | ~0.01 ms |
| **`guardrails`** *(default)* | the above **plus** a Guard over Presidio | ~5 ms |
| `hub` | the same, using the Hub's own `DetectPII` | ~5 ms + a download |

### Why both, and not just the framework

| Input | regex | Presidio |
|---|---|---|
| `my email is jane.doe@corplabs.com` | REFUSED | REFUSED |
| `call me on +44 7700 900123` | **REFUSED** | passed ← missed |
| `I am Jane Doe from the Manchester office` | passed ← missed | **REFUSED** |
| `please give Priya Raman admin rights` | passed ← missed | **REFUSED** |

**Neither dominates.** A regex is near perfect on a fixed format and
structurally blind to a name in a sentence; NER is the other way round on a
phone number written with a country code. Replacing the regex *with* the
framework would have been a downgrade on row two — so `guardrails` runs both and
takes the union, for about 5 ms.

Three tests pin this, including one asserting the row the framework **loses**. If
a future Presidio starts catching that phone number, delete the test and update
the table — do not quietly weaken the claim and leave the table lying.

### Why Presidio from PyPI instead of the Hub

**Guardrails AI ships zero validators.** All ~65 live in a Hub fetched from
`hub.api.guardrailsai.com`, one `guardrails hub install` at a time. On a build
agent where that host does not resolve you get a framework with nothing in it —
precisely the network a data-residency argument implies. Taking Presidio
straight from PyPI gives the same engine with no Hub, no token and no egress.

`make hub` and `GUARD_VALIDATOR=hub` remain available if you prefer the Hub's own
validator.

### It phones home, and the documented switches do not stop it

`guardrails/utils/hub_telemetry_utils.py:70` hardcodes an OpenTelemetry endpoint
at `hty0gc1ok3.execute-api.us-east-1.amazonaws.com`. Verified with
guardrails-ai 0.11.0: it still attempts that POST with **all** of these set —

- `GUARDRAILS_DISABLE_TELEMETRY=true`
- `GUARDRAILS_ENABLE_METRICS=false`
- `guardrails.settings.disable_tracing = True`
- `TRACELOOP_BASE_URL`, `TRACELOOP_TELEMETRY=false`

Nothing left the machine only because DNS for that host failed. On an open
network it would have gone. This repo sets those switches anyway, but **the only
control that actually works is egress policy** — treat it as a firewall rule, not
a config flag.

Neither backend is a security boundary. Published defences against prompt
injection have been broken more than 90% of the time once attackers adapted.
Treat guards as a filter that removes the obvious.

---

## Traceability

Phoenix runs as a **service**, not a library. Every answer prints a `trace_id`;
open http://localhost:6006, choose the **`chainlit-support-bot`** project, and
paste the id into the search box.

A refused turn is a complete trace on its own:

```
chat
   input_guard    passed=False  backend=guardrails-ai/presidio
                  reason="personal data detected: PERSON (confidence 0.85)"
```

Note what is **missing** from it: no `ChatCompletion`. The model was never
called, and the trace shows that as an absence.

Guard spans carry `guardrail.name`, `guardrail.passed`, `guardrail.reason`,
`guardrail.backend` and `guardrail.duration_ms`. Model spans carry the prompt
**actually sent** — always bigger than people expect once the system prompt and
history are counted — plus token counts and latency.

> **Why Phoenix is not in `pyproject.toml`.** The Phoenix *server*
> (`arize-phoenix`) needs `mcp>=2.0.0`; Chainlit needs `mcp<2.0.0`. uv cannot
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
metric; a thumbs-down carrying a trace id is a reproducible bug report — you have
the question, the prompt and the answer, so you can replay it and score it.

---

## What is deliberately not built

| | |
|---|---|
| Retrieval | none — that is [`../rag/`](../rag/) |
| Authentication | none. Anyone who can reach the port can chat |
| Conversation persistence | a list in the session. Restart the process and it is gone |
| Rate limiting | none |
| Eval harness | `feedback.jsonl` is the raw material, not the scorer |

## Verified

macOS + Docker, Python 3.12 in the image:

- `uv sync --frozen` resolves with no conflicts; **26/26 tests pass**
- `docker compose up --build` → 55s, 2.17GB, `200 text/html` ~3s after start
- the Presidio guard refuses `I am Jane Doe from the Manchester office`
  **inside the container**
- guard refusals reach Phoenix carrying `guardrail.passed`, `guardrail.reason`
  and `guardrail.backend`
- against a live Qwen3.8-27B-FP8: answered in 7.5s, injection refused in 0.00s,
  `PERSON (confidence 0.85)` refused in 0.03s, feedback returning 204

**Not verified:** the Chainlit websocket path — streaming into the browser and
the 👍/👎 buttons firing — which needs a human clicking.
